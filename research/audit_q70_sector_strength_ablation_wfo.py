from __future__ import annotations

"""Research-only chained-OOS ablation: V2 Q70 vs V2 Q70 + sector-strength soft ranking.

Important:
- This file does NOT modify production strategy/policy code.
- Q70 is computed from the original candidate features.
- Sector strength only changes the ranking order AFTER Q70.
- PortfolioSimulator normally re-ranks candidates internally, so the sector variant
  temporarily replaces its module-local rank_candidates function during simulation.
- The monkey-patch is restored in a finally block after every simulation.
"""

import argparse
import copy
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtesting.engine import BacktestConfig, build_exit_model, generate_candidate_trades
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.portfolio_metrics import calculate_portfolio_metrics
import backtesting.portfolio_simulator as portfolio_simulator_module
from backtesting.portfolio_simulator import PortfolioSimulator
from backtesting.ranking import RankingMethod
from backtesting.transaction_cost import TransactionCostConfig
from backtesting.walk_forward import WalkForwardConfig, build_walk_forward_folds
from config.trading_policy import TradingPolicy, apply_strategy_config
from core.database import load_price_data
from core.sector import fetch_sector_mapping
from core.universe import get_vn100_symbols
from execution.signal_executor import PaperExecutionConfig
from research.universes import HOLDOUT20_SYMBOLS

QUALITY_FEATURES = ("signal_score", "relative_strength", "adx", "volume_ratio")
SECTOR_MOMENTUM_PERIOD = 20
DEFAULT_SECTOR_SCORE_ADJUSTMENT = 10.0


def _num(value: Any, default: float = np.nan) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if np.isfinite(x) else default


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["time", "close"])

    out = df.copy()
    out["time"] = pd.to_datetime(out["time"], errors="coerce").dt.normalize()
    out["close"] = pd.to_numeric(out["close"], errors="coerce")

    return (
        out.dropna(subset=["time", "close"])
        .drop_duplicates("time", keep="last")
        .sort_values("time")
        .reset_index(drop=True)
    )


def build_sector_strength_daily(
    universe: list[str],
    mapping: dict[str, str],
    db_path: str,
) -> pd.DataFrame:
    """Build point-in-time equal-weight 20D sector momentum and daily percentile."""
    # db_path is retained in the function signature for CLI/API compatibility.
    del db_path

    rows = []

    for symbol in universe:
        sector = mapping.get(symbol)
        if not sector:
            continue

        prices = _prepare(load_price_data(symbol))
        if len(prices) < SECTOR_MOMENTUM_PERIOD + 1:
            continue

        prices["daily_return"] = prices["close"].pct_change()
        prices["sector"] = sector
        rows.append(prices[["time", "sector", "daily_return"]])

    if not rows:
        return pd.DataFrame(
            columns=[
                "time",
                "sector",
                "sector_return_20d",
                "sector_percentile_20d",
            ]
        )

    daily = (
        pd.concat(rows, ignore_index=True)
        .dropna(subset=["daily_return"])
        .groupby(["time", "sector"], as_index=False)["daily_return"]
        .mean()
        .rename(columns={"daily_return": "sector_daily_return"})
        .sort_values(["sector", "time"])
        .reset_index(drop=True)
    )

    daily["sector_return_20d"] = (
        daily.groupby("sector")["sector_daily_return"]
        .transform(
            lambda s: (1.0 + s)
            .rolling(SECTOR_MOMENTUM_PERIOD)
            .apply(np.prod, raw=True)
            - 1.0
        )
    )

    daily["sector_percentile_20d"] = daily.groupby("time")[
        "sector_return_20d"
    ].rank(method="average", pct=True)

    return daily


def frozen_percentile(value: Any, ref: np.ndarray) -> float:
    x = _num(value)
    if not np.isfinite(x) or ref.size == 0:
        return 0.5
    return float(np.searchsorted(ref, x, side="right") / ref.size)


def fit_quality(candidates: list[Any]) -> dict[str, np.ndarray]:
    refs: dict[str, np.ndarray] = {}

    for feature in QUALITY_FEATURES:
        vals = np.asarray(
            [_num(getattr(c, feature, np.nan)) for c in candidates],
            dtype=float,
        )
        refs[feature] = np.sort(vals[np.isfinite(vals)])

    return refs


def quality_score(candidate: Any, refs: dict[str, np.ndarray]) -> float:
    return float(
        np.mean(
            [
                frozen_percentile(
                    getattr(candidate, feature, np.nan),
                    refs[feature],
                )
                for feature in QUALITY_FEATURES
            ]
        )
    )


def candidate_sector_percentile(
    candidate: Any,
    sector_strength: pd.DataFrame,
    mapping: dict[str, str],
) -> float:
    symbol = str(getattr(candidate, "symbol", "")).strip().upper()
    sector = mapping.get(symbol)

    if not sector:
        return np.nan

    date = getattr(candidate, "signal_date", None)
    if date is None:
        date = getattr(candidate, "entry_date", None)
    if date is None:
        return np.nan

    date = pd.Timestamp(date).normalize()

    rows = sector_strength[
        (sector_strength["time"] == date)
        & (sector_strength["sector"] == sector)
    ]

    if rows.empty:
        return np.nan

    return _num(rows.iloc[0]["sector_percentile_20d"])


def sector_adjusted_score(
    candidate: Any,
    sector_strength: pd.DataFrame,
    mapping: dict[str, str],
    adjustment: float,
) -> float:
    """Return the research-only ranking score for one candidate."""
    pct = candidate_sector_percentile(candidate, sector_strength, mapping)
    base_score = _num(getattr(candidate, "signal_score", np.nan))

    if np.isfinite(pct) and np.isfinite(base_score):
        return float(base_score + adjustment * (pct - 0.5))

    return base_score


def research_rank_candidates(
    candidates: list[Any],
    method: RankingMethod | str,
    sector_strength: pd.DataFrame,
    mapping: dict[str, str],
    adjustment: float,
) -> list[Any]:
    """Rank candidates for the research-only sector-strength variant.

    Only SIGNAL_SCORE ranking is modified. Any other ranking method is delegated
    to the production ranking implementation.
    """
    if method != RankingMethod.SIGNAL_SCORE and str(method) != str(
        RankingMethod.SIGNAL_SCORE
    ):
        return portfolio_simulator_module.rank_candidates(
            candidates,
            method=method,
        )

    scored = [
        (
            candidate,
            sector_adjusted_score(
                candidate,
                sector_strength,
                mapping,
                adjustment,
            ),
        )
        for candidate in candidates
    ]

    scored.sort(
        key=lambda item: (
            _num(item[1], default=-np.inf),
            _num(getattr(item[0], "signal_score", np.nan), default=-np.inf),
        ),
        reverse=True,
    )

    return [candidate for candidate, _ in scored]


def ranking_diagnostics(
    candidates: list[Any],
    sector_strength: pd.DataFrame,
    mapping: dict[str, str],
    adjustment: float,
    top_n: int = 5,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Measure whether sector strength actually changes daily execution ranking.

    PortfolioSimulator ranks entry candidates date-by-date. This diagnostic
    mirrors that granularity instead of comparing one global candidate list.
    It is research-only and does not mutate production ranking.
    """
    rows: list[dict[str, Any]] = []
    changed_days = 0
    changed_positions = 0
    top_n_changed_days = 0
    total_candidates = 0
    available_candidates = 0
    score_adjustments: list[float] = []

    grouped: dict[str, list[Any]] = {}
    for candidate in candidates:
        raw_date = getattr(candidate, "entry_date", None)
        if raw_date is None:
            raw_date = getattr(candidate, "signal_date", None)
        if raw_date is None:
            key = ""
        else:
            try:
                key = pd.Timestamp(raw_date).date().isoformat()
            except Exception:
                key = str(raw_date)
        grouped.setdefault(key, []).append(candidate)

    for date_key in sorted(grouped):
        day_candidates = grouped[date_key]
        baseline_ranked = portfolio_simulator_module.rank_candidates(
            day_candidates,
            method=RankingMethod.SIGNAL_SCORE,
        )
        sector_ranked = research_rank_candidates(
            day_candidates,
            RankingMethod.SIGNAL_SCORE,
            sector_strength,
            mapping,
            adjustment,
        )

        baseline_symbols = [str(getattr(c, "symbol", "")) for c in baseline_ranked]
        sector_symbols = [str(getattr(c, "symbol", "")) for c in sector_ranked]

        total_candidates += len(day_candidates)
        changed = baseline_symbols != sector_symbols
        if changed:
            changed_days += 1

        for i, (before, after) in enumerate(zip(baseline_symbols, sector_symbols)):
            if before != after:
                changed_positions += 1

        if baseline_symbols[:top_n] != sector_symbols[:top_n]:
            top_n_changed_days += 1

        day_adjustments: list[float] = []
        for candidate in day_candidates:
            pct = candidate_sector_percentile(candidate, sector_strength, mapping)
            base_score = _num(getattr(candidate, "signal_score", np.nan))
            if np.isfinite(pct):
                available_candidates += 1
            if np.isfinite(pct) and np.isfinite(base_score):
                delta = float(adjustment * (pct - 0.5))
                day_adjustments.append(delta)
                score_adjustments.append(delta)

        rows.append(
            {
                "date": date_key,
                "candidate_count": len(day_candidates),
                "ranking_changed": int(changed),
                "top_n_changed": int(baseline_symbols[:top_n] != sector_symbols[:top_n]),
                "baseline_top_symbols": "|".join(baseline_symbols[:top_n]),
                "sector_top_symbols": "|".join(sector_symbols[:top_n]),
                "mean_score_adjustment": float(np.mean(day_adjustments)) if day_adjustments else np.nan,
                "min_score_adjustment": float(np.min(day_adjustments)) if day_adjustments else np.nan,
                "max_score_adjustment": float(np.max(day_adjustments)) if day_adjustments else np.nan,
            }
        )

    summary = {
        "diagnostic_days": len(grouped),
        "diagnostic_candidates": total_candidates,
        "sector_data_available_candidates": available_candidates,
        "ranking_changed_days": changed_days,
        "ranking_changed_day_pct": (changed_days / len(grouped) * 100.0) if grouped else 0.0,
        "changed_rank_positions": changed_positions,
        "top_n": top_n,
        "top_n_changed_days": top_n_changed_days,
        "top_n_changed_day_pct": (top_n_changed_days / len(grouped) * 100.0) if grouped else 0.0,
        "mean_abs_score_adjustment": float(np.mean(np.abs(score_adjustments))) if score_adjustments else 0.0,
        "max_abs_score_adjustment": float(np.max(np.abs(score_adjustments))) if score_adjustments else 0.0,
    }
    return summary, rows


def build_candidates(
    symbols: list[str],
    db_path: str,
    policy: TradingPolicy,
    paper: PaperExecutionConfig,
    start_date: str,
    end_date: str,
) -> list[Any]:
    config = BacktestConfig(
        max_holding_days=policy.maximum_holding_days,
        initial_capital=paper.initial_cash,
        buy_commission_pct=paper.commission_rate * 100,
        sell_commission_pct=paper.commission_rate * 100,
        sell_tax_pct=paper.sell_tax_rate * 100,
        buy_slippage_pct=paper.slippage_bps / 100,
        sell_slippage_pct=paper.slippage_bps / 100,
        ranking_method="signal_score",
    )

    entry_model = policy.build_entry_model()

    exit_model = build_exit_model(
        name="atr",
        stop_atr_multiplier=policy.stop_atr_multiplier,
        target_atr_multiplier=policy.target_atr_multiplier,
        break_even_trigger=policy.target_atr_multiplier,
        trailing_atr_multiplier=policy.trailing_atr_multiplier,
    )

    out: list[Any] = []

    for symbol in symbols:
        out.extend(
            generate_candidate_trades(
                symbol=symbol,
                config=config,
                db_path=db_path,
                warmup_bars=60,
                verbose=False,
                start_date=start_date,
                end_date=end_date,
                entry_model=entry_model,
                exit_model=exit_model,
            )
        )

    return out


def simulate(
    candidates: list[Any],
    paper: PaperExecutionConfig,
    parity: BacktestPaperParityConfig,
    initial_capital: float,
    sector_strength: pd.DataFrame | None = None,
    mapping: dict[str, str] | None = None,
    sector_adjustment: float = 0.0,
):
    costs = TransactionCostConfig(
        buy_commission_pct=parity.commission_pct,
        sell_commission_pct=parity.commission_pct,
        sell_tax_pct=parity.sell_tax_pct,
        buy_slippage_pct=parity.slippage_pct,
        sell_slippage_pct=parity.slippage_pct,
    )

    sim = PortfolioSimulator(
        initial_cash=initial_capital,
        position_size_pct=paper.fixed_fraction_pct,
        position_sizer=parity.build_position_sizer(),
        ranking_method="signal_score",
        transaction_cost_config=costs,
        max_positions=paper.maximum_open_positions,
        lot_size=paper.lot_size,
        max_new_positions_per_day=paper.maximum_orders_per_scan,
        maximum_gross_exposure_pct=paper.maximum_gross_exposure_pct,
        minimum_cash_buffer_pct=paper.minimum_cash_buffer_pct,
    )

    original_rank_candidates = portfolio_simulator_module.rank_candidates

    try:
        if sector_strength is not None and mapping is not None:
            def _research_rank(candidates_to_rank, method):
                return research_rank_candidates(
                    candidates_to_rank,
                    method,
                    sector_strength,
                    mapping,
                    sector_adjustment,
                )

            portfolio_simulator_module.rank_candidates = _research_rank

        result = sim.simulate(copy.deepcopy(candidates))
    finally:
        portfolio_simulator_module.rank_candidates = original_rank_candidates

    metrics = calculate_portfolio_metrics(
        result.equity_curve,
        final_equity=float(result.final_equity),
    )

    return result.executed_trades, metrics


def run_fold(
    train: list[Any],
    test: list[Any],
    quality_threshold: float,
):
    refs = fit_quality(train)

    train_q70 = [
        c for c in train
        if quality_score(c, refs) >= quality_threshold
    ]

    test_q70 = [
        c for c in test
        if quality_score(c, refs) >= quality_threshold
    ]

    # Both variants receive exactly the same Q70 candidate set.
    # Sector strength is applied only inside PortfolioSimulator's ranking step.
    return test_q70, test_q70, len(train_q70), len(test_q70)


def main() -> None:
    from config.strategy_config import V2_Q70_ATR45_9
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default="data/market.db")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--start", default="2018-08-07")
    parser.add_argument("--end", default="2026-08-21")
    parser.add_argument("--train-months", type=int, default=24)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=6)
    parser.add_argument("--quality-threshold", type=float, default=0.70)
    parser.add_argument(
        "--sector-adjustment",
        type=float,
        default=DEFAULT_SECTOR_SCORE_ADJUSTMENT,
        help="Adjustment coefficient; sector effect is adjustment * (percentile - 0.5).",
    )
    parser.add_argument(
        "--output",
        default="research_results/q70_sector_strength_ablation_wfo",
    )
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="Run sector-adjustment sweep: 0, 5, 10, 20, 50, 100.",
    )
    args = parser.parse_args()

    if args.sweep:
        adjustments = (0.0, 5.0, 10.0, 20.0, 50.0, 100.0)
        print("=== SECTOR STRENGTH ADJUSTMENT SWEEP ===")
        print(
            "Adjustments:",
            ", ".join(str(int(x)) for x in adjustments),
        )

        import subprocess
        import sys

        project_root = Path(__file__).resolve().parents[1]
        module_name = "research.audit_q70_sector_strength_ablation_wfo"

        base_args = [
            sys.executable,
            "-m",
            module_name,
            "--db-path", args.db_path,
            "--start", args.start,
            "--end", args.end,
            "--train-months", str(args.train_months),
            "--test-months", str(args.test_months),
            "--step-months", str(args.step_months),
            "--quality-threshold", str(args.quality_threshold),
        ]
        if args.symbols is not None:
            base_args.append("--symbols")
            base_args.extend(args.symbols)

        for adjustment in adjustments:
            print(f"\n--- adjustment={adjustment:g} ---")
            cmd = base_args + [
                "--sector-adjustment", str(adjustment),
                "--output",
                str(Path(args.output) / f"adjustment_{adjustment:g}"),
            ]
            completed = subprocess.run(cmd, check=False)
            if completed.returncode != 0:
                raise SystemExit(completed.returncode)

        print("\nSweep complete.")
        raise SystemExit(0)

    apply_strategy_config(V2_Q70_ATR45_9)

    paper = PaperExecutionConfig.from_env()
    policy = TradingPolicy.from_env()
    parity = BacktestPaperParityConfig.from_paper_config(
        paper,
        sell_tax_rate=paper.sell_tax_rate,
    )

    symbols = (
        list(HOLDOUT20_SYMBOLS)
        if args.symbols is None
        else sorted(
            {
                s.strip().upper()
                for s in args.symbols
                if s.strip()
            }
        )
    )

    folds = build_walk_forward_folds(
        WalkForwardConfig(
            start_date=args.start,
            end_date=args.end,
            train_months=args.train_months,
            test_months=args.test_months,
            step_months=args.step_months,
        )
    )

    universe = [
        str(s).strip().upper()
        for s in get_vn100_symbols()
    ]

    mapping_all = fetch_sector_mapping()
    mapping = {
        s: mapping_all[s]
        for s in universe
        if s in mapping_all
    }

    sector_strength = build_sector_strength_daily(
        universe,
        mapping,
        args.db_path,
    )

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    baseline_cap = float(parity.initial_cash)
    sector_cap = float(parity.initial_cash)

    fold_rows = []
    trade_rows = []
    ranking_diag_rows = []
    ranking_diag_summary_rows = []

    print(
        f"V2={V2_Q70_ATR45_9.name} | "
        f"symbols={len(symbols)} | folds={len(folds)}"
    )
    print(
        f"Sector strength={SECTOR_MOMENTUM_PERIOD}D | "
        f"soft adjustment=+/-{args.sector_adjustment:.1f} score | "
        f"Q70={args.quality_threshold:.0%}"
    )
    print(
        "Q70 is computed on the ORIGINAL candidate score; "
        "sector strength only changes ranking after Q70."
    )
    print(
        "Research implementation: PortfolioSimulator ranking is "
        "temporarily patched only during the sector simulation."
    )

    for fold in folds:
        train = build_candidates(
            symbols,
            args.db_path,
            policy,
            paper,
            str(fold.train_start.date()),
            str(fold.train_end.date()),
        )

        test = build_candidates(
            symbols,
            args.db_path,
            policy,
            paper,
            str(fold.test_start.date()),
            str(fold.test_end.date()),
        )

        base, sector, train_q70_n, test_q70_n = run_fold(
            train,
            test,
            args.quality_threshold,
        )

        diag_summary, diag_rows = ranking_diagnostics(
            sector,
            sector_strength,
            mapping,
            args.sector_adjustment,
            top_n=max(
                int(paper.maximum_orders_per_scan),
                int(paper.maximum_open_positions),
                5,
            ),
        )
        ranking_diag_summary_rows.append(
            {
                "fold": fold.fold,
                **diag_summary,
            }
        )
        for row in diag_rows:
            ranking_diag_rows.append(
                {
                    "fold": fold.fold,
                    **row,
                }
            )

        bt, bm = simulate(
            base,
            paper,
            parity,
            baseline_cap,
        )

        st, sm = simulate(
            sector,
            paper,
            parity,
            sector_cap,
            sector_strength=sector_strength,
            mapping=mapping,
            sector_adjustment=args.sector_adjustment,
        )

        bf = float(bm.get("final_equity", baseline_cap))
        sf = float(sm.get("final_equity", sector_cap))

        br = (bf / baseline_cap - 1.0) * 100.0
        sr = (sf / sector_cap - 1.0) * 100.0

        sector_available = sum(
            np.isfinite(
                candidate_sector_percentile(
                    c,
                    sector_strength,
                    mapping,
                )
            )
            for c in sector
        )

        fold_rows.append(
            {
                "fold": fold.fold,
                "train_start": fold.train_start.date(),
                "train_end": fold.train_end.date(),
                "test_start": fold.test_start.date(),
                "test_end": fold.test_end.date(),
                "train_candidates": len(train),
                "test_candidates": len(test),
                "baseline_q70_candidates": len(base),
                "sector_strength_candidates": len(sector),
                "sector_strength_available": int(sector_available),
                "ranking_changed_days": int(diag_summary["ranking_changed_days"]),
                "ranking_changed_day_pct": float(diag_summary["ranking_changed_day_pct"]),
                "top_n_changed_days": int(diag_summary["top_n_changed_days"]),
                "top_n_changed_day_pct": float(diag_summary["top_n_changed_day_pct"]),
                "changed_rank_positions": int(diag_summary["changed_rank_positions"]),
                "mean_abs_score_adjustment": float(diag_summary["mean_abs_score_adjustment"]),
                "max_abs_score_adjustment": float(diag_summary["max_abs_score_adjustment"]),
                "baseline_return_pct": br,
                "sector_strength_return_pct": sr,
                "delta_pp": sr - br,
                "baseline_trades": len(bt),
                "sector_strength_trades": len(st),
                "baseline_sharpe": float(
                    bm.get("sharpe_ratio", 0.0)
                ),
                "sector_strength_sharpe": float(
                    sm.get("sharpe_ratio", 0.0)
                ),
                "baseline_max_drawdown_pct": float(
                    bm.get("max_drawdown_pct", 0.0)
                ),
                "sector_strength_max_drawdown_pct": float(
                    sm.get("max_drawdown_pct", 0.0)
                ),
            }
        )

        for name, trades in (
            ("baseline_q70", bt),
            ("q70_sector_strength", st),
        ):
            for trade in trades:
                trade_rows.append(
                    {
                        "policy": name,
                        "fold": fold.fold,
                        "symbol": getattr(trade, "symbol", ""),
                        "signal_date": getattr(
                            trade,
                            "signal_date",
                            "",
                        ),
                        "entry_date": getattr(
                            trade,
                            "entry_date",
                            "",
                        ),
                        "net_return_pct": getattr(
                            trade,
                            "net_return_pct",
                            np.nan,
                        ),
                    }
                )

        baseline_cap = bf
        sector_cap = sf

        print(
            f"FOLD {fold.fold}: "
            f"baseline={br:+.2f}% | "
            f"sector_strength={sr:+.2f}% | "
            f"delta={sr - br:+.2f}pp | "
            f"Q70 candidates={len(base)} | "
            f"sector-data={sector_available}"
        )

    fd = pd.DataFrame(fold_rows)
    td = pd.DataFrame(trade_rows)

    fd.to_csv(
        output / "fold_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    td.to_csv(
        output / "trade_level.csv",
        index=False,
        encoding="utf-8-sig",
    )

    rd = pd.DataFrame(ranking_diag_rows)
    rds = pd.DataFrame(ranking_diag_summary_rows)
    rd.to_csv(
        output / "ranking_diagnostics_daily.csv",
        index=False,
        encoding="utf-8-sig",
    )
    rds.to_csv(
        output / "ranking_diagnostics_fold.csv",
        index=False,
        encoding="utf-8-sig",
    )

    def summary(ret_col: str, trade_col: str) -> dict[str, Any]:
        r = fd[ret_col].to_numpy(float)

        return {
            "folds": len(r),
            "profitable_folds": int((r > 0).sum()),
            "mean_fold_return_pct": float(r.mean()),
            "median_fold_return_pct": float(np.median(r)),
            "compounded_oos_return_pct": float(
                (np.prod(1.0 + r / 100.0) - 1.0) * 100.0
            ),
            "total_trades": int(fd[trade_col].sum()),
            "worst_fold_pct": float(r.min()),
            "best_fold_pct": float(r.max()),
        }

    summary_df = pd.DataFrame(
        [
            {
                "policy": "baseline_q70",
                **summary(
                    "baseline_return_pct",
                    "baseline_trades",
                ),
            },
            {
                "policy": "q70_sector_strength",
                **summary(
                    "sector_strength_return_pct",
                    "sector_strength_trades",
                ),
            },
        ]
    )

    summary_df.to_csv(
        output / "summary.csv",
        index=False,
        encoding="utf-8-sig",
    )

    print("\n=== V2 Q70 SECTOR STRENGTH ABLATION WFO ===")
    print(summary_df.to_string(index=False))

    if not rds.empty:
        print("\n=== RANKING DIAGNOSTICS ===")
        print(
            rds[[
                "fold",
                "diagnostic_days",
                "diagnostic_candidates",
                "sector_data_available_candidates",
                "ranking_changed_days",
                "ranking_changed_day_pct",
                "top_n_changed_days",
                "top_n_changed_day_pct",
                "changed_rank_positions",
                "mean_abs_score_adjustment",
                "max_abs_score_adjustment",
            ]].to_string(index=False)
        )
        print(
            "\nInterpretation: if ranking_changed_days/top_n_changed_days are near 0, "
            "the sector feature is not materially changing the execution order. "
            "If they are high but trades remain unchanged, the bottleneck is "
            "likely downstream execution constraints (slots, orders/day, sizing, "
            "regime, exposure, or candidate decisions)."
        )

    print(f"\nOutput: {output.resolve()}")


if __name__ == "__main__":
    main()
