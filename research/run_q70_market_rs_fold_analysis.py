from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtesting.engine import BacktestConfig, run_backtest
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.walk_forward import WalkForwardConfig, build_walk_forward_folds
from config.trading_policy import TradingPolicy
from execution.signal_executor import PaperExecutionConfig
from research.universes import HOLDOUT20_SYMBOLS

from research.audit_q70_market_rs_ablation_wfo import (
    VARIANTS,
    VariantQualityGate,
    build_kwargs,
    generate_candidates_exact,
    load_market_health,
    FrozenVariantQuality,
    Q70_FEATURES_WITH_RS,
    Q70_FEATURES_NO_RS,
)


def trade_to_row(trade: Any, variant: str) -> dict[str, Any]:
    def enum_name(value: Any) -> str:
        text = str(value)
        return text.split(".")[-1] if "." in text else text

    return {
        "variant": variant,
        "symbol": getattr(trade, "symbol", None),
        "signal_date": getattr(trade, "signal_date", None),
        "entry_date": getattr(trade, "entry_date", None),
        "entry_price": float(getattr(trade, "entry_price", 0.0)),
        "exit_date": getattr(trade, "exit_date", None),
        "exit_price": float(getattr(trade, "exit_price", 0.0)),
        "quantity": int(getattr(trade, "quantity", 0)),
        "cost": float(getattr(trade, "cost", 0.0)),
        "signal_score": getattr(trade, "signal_score", None),
        "relative_strength": getattr(trade, "relative_strength", None),
        "adx": getattr(trade, "adx", None),
        "volume_ratio": getattr(trade, "volume_ratio", None),
        "market_regime": getattr(trade, "market_regime", None),
        "quality_composite": getattr(trade, "quality_composite", None),
        "market_state": getattr(trade, "market_state", None),
        "net_pnl": float(getattr(trade, "net_pnl", getattr(trade, "pnl", 0.0))),
        "net_return_pct": float(
            getattr(trade, "net_return_pct", getattr(trade, "return_pct", 0.0))
        ),
        "holding_days": int(getattr(trade, "holding_days", 0)),
        "transaction_cost": float(getattr(trade, "total_transaction_cost", 0.0)),
        "is_win": bool(getattr(trade, "is_win", False)),
        "exit_reason": enum_name(getattr(trade, "exit_reason", "")),
        "execution": enum_name(getattr(trade, "execution", "")),
    }


def _trade_key(row: pd.Series) -> tuple[str, str, str]:
    return (
        str(row["symbol"]),
        str(pd.to_datetime(row["signal_date"], errors="coerce")),
        str(pd.to_datetime(row["entry_date"], errors="coerce")),
    )


def _find_trade(df: pd.DataFrame, key):
    matches = df[df["trade_key"] == key]
    if matches.empty:
        return None
    return matches.iloc[0]


def build_paired_analysis(trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    required = {"variant", "symbol", "signal_date", "entry_date", "net_return_pct", "net_pnl"}
    missing = required - set(trades.columns)
    if missing:
        raise ValueError(f"Missing trade columns: {sorted(missing)}")

    ab = trades[trades["variant"].isin(["A_current", "B_no_q70_rs"])].copy()
    if ab.empty:
        raise ValueError("No A_current/B_no_q70_rs trades found.")

    a = ab[ab["variant"] == "A_current"].copy()
    b = ab[ab["variant"] == "B_no_q70_rs"].copy()

    a["trade_key"] = a.apply(_trade_key, axis=1)
    b["trade_key"] = b.apply(_trade_key, axis=1)

    rows = []

    keys = sorted(set(a["trade_key"]) | set(b["trade_key"]))

    for key in keys:
        ar = _find_trade(a, key)
        br = _find_trade(b, key)

        # Defensive handling if duplicate trade keys ever appear.
        if isinstance(ar, pd.DataFrame):
            ar = ar.iloc[0]
        if isinstance(br, pd.DataFrame):
            br = br.iloc[0]

        rows.append({
            "trade_key": key,
            "symbol": key[0],
            "signal_date": key[1],
            "entry_date": key[2],
            "in_A": ar is not None,
            "in_B": br is not None,
            "A_net_return_pct": float(ar["net_return_pct"]) if ar is not None else np.nan,
            "B_net_return_pct": float(br["net_return_pct"]) if br is not None else np.nan,
            "A_net_pnl": float(ar["net_pnl"]) if ar is not None else np.nan,
            "B_net_pnl": float(br["net_pnl"]) if br is not None else np.nan,
            "A_signal_score": float(ar["signal_score"]) if ar is not None and pd.notna(ar["signal_score"]) else np.nan,
            "B_signal_score": float(br["signal_score"]) if br is not None and pd.notna(br["signal_score"]) else np.nan,
            "A_relative_strength": float(ar["relative_strength"]) if ar is not None and pd.notna(ar["relative_strength"]) else np.nan,
            "B_relative_strength": float(br["relative_strength"]) if br is not None and pd.notna(br["relative_strength"]) else np.nan,
            "A_quality": float(ar["quality_composite"]) if ar is not None and pd.notna(ar["quality_composite"]) else np.nan,
            "B_quality": float(br["quality_composite"]) if br is not None and pd.notna(br["quality_composite"]) else np.nan,
            "A_exit_reason": ar["exit_reason"] if ar is not None else None,
            "B_exit_reason": br["exit_reason"] if br is not None else None,
        })

    paired = pd.DataFrame(rows)
    paired["selection"] = np.select(
        [
            paired["in_A"] & paired["in_B"],
            paired["in_A"] & ~paired["in_B"],
            ~paired["in_A"] & paired["in_B"],
        ],
        ["BOTH", "A_ONLY", "B_ONLY"],
        default="NONE",
    )
    paired["A_minus_B_return_pp"] = paired["A_net_return_pct"] - paired["B_net_return_pct"]

    # Focused diagnostic: what happened to trades that Q70 RS removed from A
    removed = paired[paired["selection"] == "B_ONLY"].copy()
    return paired, removed



def build_removed_feature_diagnostic(
    trades: pd.DataFrame,
    *,
    fold,
    policy,
    paper,
    parity,
    symbols,
    db_path: str,
    market_health: pd.DataFrame,
    threshold: float = 0.70,
) -> pd.DataFrame:
    """Reconstruct Q70-with-RS feature percentiles for B-only trades.

    This is a research diagnostic only. It does not modify production policy.
    """
    ab = trades[trades["variant"].isin(["A_current", "B_no_q70_rs"])].copy()
    if ab.empty:
        return pd.DataFrame()

    a = ab[ab["variant"] == "A_current"].copy()
    b = ab[ab["variant"] == "B_no_q70_rs"].copy()
    a["trade_key"] = a.apply(_trade_key, axis=1)
    b["trade_key"] = b.apply(_trade_key, axis=1)

    keys = sorted(set(b["trade_key"]) - set(a["trade_key"]))
    if not keys:
        return pd.DataFrame()

    # Rebuild the exact A/current Q70 model from Fold 6 training data.
    module = __import__(
        "research.audit_q70_market_rs_ablation_wfo",
        fromlist=["VariantEntryModel"],
    )
    train_model = module.VariantEntryModel(policy.build_entry_model(), True)
    train_kwargs = build_kwargs(
        paper, policy, parity, symbols, train_model, db_path
    )
    train_candidates = generate_candidates_exact(
        train_kwargs,
        symbols,
        str(fold.train_start.date()),
        str(fold.train_end.date()),
    )
    quality_model = FrozenVariantQuality.fit(train_candidates, Q70_FEATURES_WITH_RS)

    rows = []
    for key in keys:
        br = _find_trade(b, key)
        if br is None:
            continue

        values = {}
        for feature in Q70_FEATURES_WITH_RS:
            value = pd.to_numeric(pd.Series([br.get(feature)]), errors="coerce").iloc[0]
            values[feature] = float(value) if pd.notna(value) else np.nan

        percentiles = {}
        for feature in Q70_FEATURES_WITH_RS:
            value = values[feature]
            if pd.isna(value):
                percentiles[f"{feature}_percentile"] = np.nan
                continue
            reference_store = (
                getattr(quality_model, "reference_values", None)
                or getattr(quality_model, "feature_values", None)
                or getattr(quality_model, "_reference_values", None)
                or getattr(quality_model, "_feature_values", None)
            )
            if reference_store is None:
                # FrozenVariantQuality stores its fitted state on the instance;
                # discover the mapping without assuming a non-existent public API.
                for attr_name, attr_value in vars(quality_model).items():
                    if isinstance(attr_value, dict) and feature in attr_value:
                        reference_store = attr_value
                        break

            ref_values = [] if reference_store is None else reference_store.get(feature, [])
            ref = np.asarray(ref_values, dtype=float)
            ref = ref[np.isfinite(ref)]
            if len(ref) == 0:
                raise ValueError(
                    f"Could not recover fitted Q70 reference values for feature={feature!r}. "
                    f"FrozenVariantQuality attrs={list(vars(quality_model).keys())}"
                )
            if len(ref) == 0:
                percentiles[f"{feature}_percentile"] = np.nan
            else:
                percentiles[f"{feature}_percentile"] = float(np.mean(ref <= value))

        valid = [
            percentiles[f"{feature}_percentile"]
            for feature in Q70_FEATURES_WITH_RS
            if pd.notna(percentiles[f"{feature}_percentile"])
        ]
        composite = float(np.mean(valid)) if valid else np.nan

        rows.append({
            "symbol": key[0],
            "signal_date": key[1],
            "entry_date": key[2],
            "B_net_return_pct": float(br["net_return_pct"]),
            "B_net_pnl": float(br["net_pnl"]),
            "signal_score": values["signal_score"],
            "relative_strength_20d": values["relative_strength"],
            "adx": values["adx"],
            "volume_ratio": values["volume_ratio"],
            **percentiles,
            "q70_composite_with_rs": composite,
            "q70_threshold": threshold,
            "passes_q70_with_rs": bool(pd.notna(composite) and composite >= threshold),
            "rs_rank_contribution": percentiles.get("relative_strength_percentile", np.nan),
        })

    return pd.DataFrame(rows)


def summarize_removed(removed: pd.DataFrame) -> pd.DataFrame:
    if removed.empty:
        return pd.DataFrame([{
            "removed_trades": 0,
            "removed_mean_return_pct": np.nan,
            "removed_median_return_pct": np.nan,
            "removed_total_net_pnl": 0.0,
            "removed_winners": 0,
            "removed_losers": 0,
            "removed_winner_rate": np.nan,
        }])

    returns = pd.to_numeric(removed["B_net_return_pct"], errors="coerce")
    pnl = pd.to_numeric(removed["B_net_pnl"], errors="coerce")
    return pd.DataFrame([{
        "removed_trades": int(len(removed)),
        "removed_mean_return_pct": float(returns.mean()),
        "removed_median_return_pct": float(returns.median()),
        "removed_total_net_pnl": float(pnl.sum()),
        "removed_winners": int((returns > 0).sum()),
        "removed_losers": int((returns < 0).sum()),
        "removed_winner_rate": float((returns > 0).mean()),
    }])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fold 6 trade-level diagnostic for Q70 Market RS ablation."
    )
    parser.add_argument("--market-health", required=True)
    parser.add_argument("--db-path", default="data/market.db")
    parser.add_argument("--output", default="research_results/q70_market_rs_fold6_analysis")
    args = parser.parse_args()

    paper = PaperExecutionConfig.from_env()
    policy = TradingPolicy.from_env()
    parity = BacktestPaperParityConfig.from_paper_config(paper)
    symbols = list(HOLDOUT20_SYMBOLS)
    health = load_market_health(args.market_health)

    wf = WalkForwardConfig(
        start_date="2018-08-07",
        end_date="2026-08-21",
        train_months=24,
        test_months=6,
        step_months=6,
    )
    folds = build_walk_forward_folds(wf)
    fold = next((f for f in folds if f.fold == 6), None)
    if fold is None:
        raise ValueError("Fold 6 not found.")

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    trade_rows: list[dict[str, Any]] = []
    fold_capital = float(parity.initial_cash)

    # Reconstruct the same chained capital entering Fold 6 for A/B.
    # This is needed because run_backtest sizing can depend on starting equity.
    for prior_fold in folds:
        if prior_fold.fold >= 6:
            break
        for variant in ["A_current", "B_no_q70_rs"]:
            use_base_rs = variant == "A_current"
            use_q70_rs = variant == "A_current"
            features = Q70_FEATURES_WITH_RS if use_q70_rs else Q70_FEATURES_NO_RS

            train_model = __import__(
                "research.audit_q70_market_rs_ablation_wfo",
                fromlist=["VariantEntryModel"],
            ).VariantEntryModel(policy.build_entry_model(), use_base_rs)
            train_kwargs = build_kwargs(
                paper, policy, parity, symbols, train_model, args.db_path
            )
            train_candidates = generate_candidates_exact(
                train_kwargs, symbols,
                str(prior_fold.train_start.date()),
                str(prior_fold.train_end.date()),
            )
            quality_model = FrozenVariantQuality.fit(train_candidates, features)
            gate = VariantQualityGate(
                base_model=policy.build_entry_model(),
                use_base_rs=use_base_rs,
                quality_model=quality_model,
                market_health=health,
                threshold=0.70,
                use_q70_rs=use_q70_rs,
            )
            test_kwargs = build_kwargs(
                paper, policy, parity, symbols, gate, args.db_path
            )
            _, metrics, _ = run_backtest(
                **test_kwargs,
                start_date=str(prior_fold.test_start.date()),
                end_date=str(prior_fold.test_end.date()),
                initial_capital=fold_capital,
                verbose=False,
            )
            # A and B are independent chained-capital paths in the original WFO.
            # For Fold 6 we only need their respective starting capital.
            if variant == "A_current":
                a_capital = float(metrics.get("final_equity", fold_capital))
            else:
                b_capital = float(metrics.get("final_equity", fold_capital))

        # Stop after obtaining Fold 5 capital for both paths.
        fold_capital = float(parity.initial_cash)

    # The loop above is intentionally not used for capital reconstruction because
    # the two paths must be carried independently. Re-run cleanly with separate capitals.
    capitals = {"A_current": float(parity.initial_cash), "B_no_q70_rs": float(parity.initial_cash)}

    for prior_fold in folds:
        if prior_fold.fold >= 6:
            break
        for variant in ["A_current", "B_no_q70_rs"]:
            use_base_rs = variant == "A_current"
            use_q70_rs = variant == "A_current"
            features = Q70_FEATURES_WITH_RS if use_q70_rs else Q70_FEATURES_NO_RS

            module = __import__(
                "research.audit_q70_market_rs_ablation_wfo",
                fromlist=["VariantEntryModel"],
            )
            train_model = module.VariantEntryModel(policy.build_entry_model(), use_base_rs)
            train_kwargs = build_kwargs(
                paper, policy, parity, symbols, train_model, args.db_path
            )
            train_candidates = generate_candidates_exact(
                train_kwargs, symbols,
                str(prior_fold.train_start.date()),
                str(prior_fold.train_end.date()),
            )
            quality_model = FrozenVariantQuality.fit(train_candidates, features)
            gate = VariantQualityGate(
                base_model=policy.build_entry_model(),
                use_base_rs=use_base_rs,
                quality_model=quality_model,
                market_health=health,
                threshold=0.70,
                use_q70_rs=use_q70_rs,
            )
            test_kwargs = build_kwargs(
                paper, policy, parity, symbols, gate, args.db_path
            )
            _, metrics, _ = run_backtest(
                **test_kwargs,
                start_date=str(prior_fold.test_start.date()),
                end_date=str(prior_fold.test_end.date()),
                initial_capital=capitals[variant],
                verbose=False,
            )
            capitals[variant] = float(metrics.get("final_equity", capitals[variant]))

    fold_rows = []
    for variant in ["A_current", "B_no_q70_rs"]:
        use_base_rs = variant == "A_current"
        use_q70_rs = variant == "A_current"
        features = Q70_FEATURES_WITH_RS if use_q70_rs else Q70_FEATURES_NO_RS

        module = __import__(
            "research.audit_q70_market_rs_ablation_wfo",
            fromlist=["VariantEntryModel"],
        )
        train_model = module.VariantEntryModel(policy.build_entry_model(), use_base_rs)
        train_kwargs = build_kwargs(
            paper, policy, parity, symbols, train_model, args.db_path
        )
        train_candidates = generate_candidates_exact(
            train_kwargs, symbols,
            str(fold.train_start.date()),
            str(fold.train_end.date()),
        )
        quality_model = FrozenVariantQuality.fit(train_candidates, features)

        gate = VariantQualityGate(
            base_model=policy.build_entry_model(),
            use_base_rs=use_base_rs,
            quality_model=quality_model,
            market_health=health,
            threshold=0.70,
            use_q70_rs=use_q70_rs,
        )
        test_kwargs = build_kwargs(
            paper, policy, parity, symbols, gate, args.db_path
        )
        trades, metrics, _ = run_backtest(
            **test_kwargs,
            start_date=str(fold.test_start.date()),
            end_date=str(fold.test_end.date()),
            initial_capital=capitals[variant],
            verbose=False,
        )

        for trade in trades:
            trade_rows.append(trade_to_row(trade, variant))

        fold_rows.append({
            "fold": 6,
            "variant": variant,
            "test_start": fold.test_start.date(),
            "test_end": fold.test_end.date(),
            "starting_capital": capitals[variant],
            "final_equity": float(metrics.get("final_equity", capitals[variant])),
            "return_pct": float(metrics.get("total_return_pct", 0.0)),
            "trades": len(trades),
        })

    trades_df = pd.DataFrame(trade_rows)
    paired, removed = build_paired_analysis(trades_df)
    removed_summary = summarize_removed(removed)

    removed_feature_diagnostic = build_removed_feature_diagnostic(
        trades_df,
        fold=fold,
        policy=policy,
        paper=paper,
        parity=parity,
        symbols=symbols,
        db_path=args.db_path,
        market_health=health,
        threshold=0.70,
    )

    trades_df.to_csv(output / "trade_level.csv", index=False, encoding="utf-8-sig")
    paired.to_csv(output / "paired_trade_analysis.csv", index=False, encoding="utf-8-sig")
    removed.to_csv(output / "b_only_removed_by_q70_rs.csv", index=False, encoding="utf-8-sig")
    removed_feature_diagnostic.to_csv(
        output / "b_only_q70_feature_diagnostic.csv",
        index=False,
        encoding="utf-8-sig",
    )
    removed_summary.to_csv(output / "removed_summary.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(fold_rows).to_csv(output / "fold_summary.csv", index=False, encoding="utf-8-sig")

    print("=== Q70 MARKET RS FOLD 6 TRADE-LEVEL ANALYSIS ===")
    print(f"Fold: 6 | {fold.test_start.date()} -> {fold.test_end.date()}")
    print(f"A starting capital: {capitals['A_current']:,.2f}")
    print(f"B starting capital: {capitals['B_no_q70_rs']:,.2f}")
    print()
    print("=== FOLD SUMMARY ===")
    print(pd.DataFrame(fold_rows).to_string(index=False))
    print()
    print("=== SELECTION OVERLAP ===")
    print(paired["selection"].value_counts().rename_axis("selection").to_string())
    print()
    print("=== B-ONLY TRADES (REMOVED BY Q70 RS) ===")
    if removed.empty:
        print("None.")
    else:
        cols = [
            "symbol", "signal_date", "entry_date",
            "B_net_return_pct", "B_net_pnl",
            "B_signal_score", "B_relative_strength", "B_quality",
            "B_exit_reason",
        ]
        print(removed.sort_values("B_net_return_pct", ascending=False)[cols].to_string(index=False))
    print()
    print("=== REMOVED TRADE SUMMARY ===")
    print(removed_summary.to_string(index=False))
    print()

    print("=== B-ONLY Q70 FEATURE DIAGNOSTIC ===")
    if removed_feature_diagnostic.empty:
        print("No B-only trades available for feature diagnostic.")
    else:
        print(removed_feature_diagnostic.to_string(index=False))
    print()
    print(f"Output: {output.resolve()}")


if __name__ == "__main__":
    main()
