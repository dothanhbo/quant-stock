from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv() -> bool:
        return False

from backtesting.engine import load_price_data, run_backtest
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.walk_forward import WalkForwardConfig, build_walk_forward_folds
from execution.signal_executor import PaperExecutionConfig
from research.run_volume_candidate_matrix import (
    build_case_kwargs,
    build_cases,
)
from research.universes import HOLDOUT20_SYMBOLS


DEFAULT_OUTPUT_DIR = Path(
    "research_results/trade_level_diagnostics_holdout20"
)
DIAGNOSTIC_CASE_IDS = (
    "volume_only__current__fixed20",
    "volume_only__frozen__fixed20",
    "volume_only__current__atr_risk",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Out-of-sample trade-level diagnostics for entry, exit, "
            "regime, score and position-sizing behavior."
        )
    )
    parser.add_argument("--db", default="market.db")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--start", default="2018-08-07")
    parser.add_argument("--end", default="2026-08-21")
    parser.add_argument("--train-months", type=int, default=24)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=6)
    parser.add_argument("--hold", type=int, default=30)
    parser.add_argument("--sell-tax-rate", type=float, default=0.001)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument(
        "--only",
        nargs="*",
        default=None,
        help=(
            "Optional diagnostic case_id list. Defaults to the three "
            "representative cases."
        ),
    )
    return parser


def calculate_excursions(
    *,
    entry_price: float,
    bars: pd.DataFrame,
) -> tuple[float, float]:
    if entry_price <= 0 or bars.empty:
        return 0.0, 0.0
    highs = pd.to_numeric(bars["high"], errors="coerce").dropna()
    lows = pd.to_numeric(bars["low"], errors="coerce").dropna()
    mfe_pct = (
        float((highs.max() / entry_price - 1.0) * 100.0)
        if not highs.empty
        else 0.0
    )
    mae_pct = (
        float((lows.min() / entry_price - 1.0) * 100.0)
        if not lows.empty
        else 0.0
    )
    return mfe_pct, mae_pct


def classify_trade_path(
    *,
    return_pct: float,
    mfe_pct: float,
) -> str:
    if return_pct < 0 and mfe_pct < 2.0:
        return "LOSS_NEVER_WORKED"
    if return_pct < 0 and mfe_pct >= 2.0:
        return "LOSS_GAVE_BACK_PROFIT"
    if return_pct >= 0 and mfe_pct - return_pct >= 3.0:
        return "WIN_GAVE_BACK_3PCT_PLUS"
    return "WIN_CAPTURED"


def holding_bucket(days: int) -> str:
    if days <= 5:
        return "00-05d"
    if days <= 10:
        return "06-10d"
    if days <= 20:
        return "11-20d"
    return "21d+"


def score_bucket(score: float | None) -> str:
    if score is None or not np.isfinite(score):
        return "UNKNOWN"
    if score < 65:
        return "<65"
    if score < 70:
        return "65-69"
    if score < 75:
        return "70-74"
    if score < 80:
        return "75-79"
    if score < 85:
        return "80-84"
    return "85+"


def load_price_cache(
    *,
    symbols: list[str],
    db_path: str,
) -> dict[str, pd.DataFrame]:
    cache: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        df = load_price_data(symbol, db_path)
        if not df.empty:
            df = df.copy()
            df["time"] = pd.to_datetime(df["time"]).dt.normalize()
        cache[symbol] = df
    return cache


def trade_to_row(
    *,
    trade,
    case_id: str,
    fold: int,
    price_cache: dict[str, pd.DataFrame],
) -> dict:
    row = trade.to_dict()
    entry_date = pd.Timestamp(trade.entry_date).normalize()
    exit_date = pd.Timestamp(trade.exit_date).normalize()
    prices = price_cache.get(trade.symbol, pd.DataFrame())
    bars = (
        prices[
            (prices["time"] >= entry_date)
            & (prices["time"] <= exit_date)
        ]
        if not prices.empty
        else pd.DataFrame()
    )
    mfe_pct, mae_pct = calculate_excursions(
        entry_price=float(trade.entry_price),
        bars=bars,
    )
    net_return_pct = float(trade.net_return_pct)
    signal_score = (
        float(trade.signal_score)
        if trade.signal_score is not None
        else np.nan
    )
    row.update({
        "case_id": case_id,
        "fold": fold,
        "mfe_pct": mfe_pct,
        "mae_pct": mae_pct,
        "giveback_from_mfe_pct": mfe_pct - net_return_pct,
        "mfe_capture_ratio": (
            net_return_pct / mfe_pct
            if mfe_pct > 0
            else np.nan
        ),
        "trade_path_class": classify_trade_path(
            return_pct=net_return_pct,
            mfe_pct=mfe_pct,
        ),
        "holding_bucket": holding_bucket(int(trade.holding_days)),
        "score_bucket": score_bucket(signal_score),
        "position_value": float(trade.entry_price * trade.quantity),
    })
    return row


def add_score_quantiles(trades: pd.DataFrame) -> pd.DataFrame:
    result = trades.copy()
    result["score_quantile"] = "UNKNOWN"
    for case_id, index in result.groupby("case_id").groups.items():
        scores = pd.to_numeric(
            result.loc[index, "signal_score"],
            errors="coerce",
        )
        valid = scores.dropna()
        if valid.empty:
            continue
        ranks = valid.rank(method="first")
        bins = min(5, len(valid))
        labels = [f"Q{i}" for i in range(1, bins + 1)]
        result.loc[valid.index, "score_quantile"] = pd.qcut(
            ranks,
            q=bins,
            labels=labels,
        ).astype(str)
    return result


def aggregate_dimension(
    trades: pd.DataFrame,
    *,
    dimension: str,
) -> pd.DataFrame:
    rows: list[dict] = []
    for (case_id, value), group in trades.groupby(
        ["case_id", dimension],
        dropna=False,
    ):
        returns = pd.to_numeric(group["net_return_pct"], errors="coerce")
        pnl = pd.to_numeric(group["net_pnl"], errors="coerce")
        rows.append({
            "case_id": case_id,
            "dimension": dimension,
            "value": str(value),
            "trades": int(len(group)),
            "wins": int((pnl > 0).sum()),
            "win_rate_pct": float((pnl > 0).mean() * 100.0),
            "average_return_pct": float(returns.mean()),
            "median_return_pct": float(returns.median()),
            "total_net_pnl": float(pnl.sum()),
            "average_mfe_pct": float(group["mfe_pct"].mean()),
            "average_mae_pct": float(group["mae_pct"].mean()),
            "average_giveback_pct": float(
                group["giveback_from_mfe_pct"].mean()
            ),
            "average_position_value": float(
                group["position_value"].mean()
            ),
            "average_risk_pct": float(
                pd.to_numeric(group["risk_pct"], errors="coerce").mean()
            ),
        })
    return pd.DataFrame(rows)


def build_case_summary(trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for case_id, group in trades.groupby("case_id"):
        pnl = pd.to_numeric(group["net_pnl"], errors="coerce")
        returns = pd.to_numeric(group["net_return_pct"], errors="coerce")
        path = group["trade_path_class"]
        rows.append({
            "case_id": case_id,
            "trades": int(len(group)),
            "wins": int((pnl > 0).sum()),
            "win_rate_pct": float((pnl > 0).mean() * 100.0),
            "average_return_pct": float(returns.mean()),
            "median_return_pct": float(returns.median()),
            "total_net_pnl": float(pnl.sum()),
            "average_mfe_pct": float(group["mfe_pct"].mean()),
            "average_mae_pct": float(group["mae_pct"].mean()),
            "average_giveback_pct": float(
                group["giveback_from_mfe_pct"].mean()
            ),
            "loss_never_worked_pct": float(
                (path == "LOSS_NEVER_WORKED").mean() * 100.0
            ),
            "loss_gave_back_profit_pct": float(
                (path == "LOSS_GAVE_BACK_PROFIT").mean() * 100.0
            ),
            "win_gave_back_3pct_plus_pct": float(
                (path == "WIN_GAVE_BACK_3PCT_PLUS").mean() * 100.0
            ),
            "average_position_value": float(
                group["position_value"].mean()
            ),
            "average_risk_amount": float(
                pd.to_numeric(group["risk_amount"], errors="coerce").mean()
            ),
        })
    return pd.DataFrame(rows)


def main() -> None:
    load_dotenv()
    args = build_parser().parse_args()
    paper = PaperExecutionConfig.from_env()
    parity = BacktestPaperParityConfig.from_paper_config(
        paper,
        sell_tax_rate=args.sell_tax_rate,
    )
    symbols = (
        list(HOLDOUT20_SYMBOLS)
        if args.symbols is None
        else [
            value.strip().upper()
            for value in args.symbols
            if value.strip()
        ]
    )
    config = WalkForwardConfig(
        start_date=args.start,
        end_date=args.end,
        train_months=args.train_months,
        test_months=args.test_months,
        step_months=args.step_months,
    )
    folds = build_walk_forward_folds(config)
    case_map = {case.case_id: case for case in build_cases()}
    selected_ids = list(args.only or DIAGNOSTIC_CASE_IDS)
    unknown = sorted(set(selected_ids) - set(case_map))
    if unknown:
        raise ValueError("Unknown case_id: " + ", ".join(unknown))

    price_cache = load_price_cache(symbols=symbols, db_path=args.db)
    trade_rows: list[dict] = []

    for case_index, case_id in enumerate(selected_ids, start=1):
        case = case_map[case_id]
        current_capital = float(parity.initial_cash)
        kwargs = build_case_kwargs(
            case=case,
            paper=paper,
            parity=parity,
            symbols=symbols,
            hold=args.hold,
        )
        kwargs["db_path"] = args.db

        print("\n" + "#" * 96)
        print(f"DIAGNOSTIC CASE {case_index}/{len(selected_ids)}: {case_id}")
        print("#" * 96)

        for fold in folds:
            trades, metrics, _ = run_backtest(
                **kwargs,
                start_date=str(fold.test_start.date()),
                end_date=str(fold.test_end.date()),
                initial_capital=current_capital,
                verbose=False,
            )
            current_capital = float(
                metrics.get("final_equity", current_capital)
            )
            print(
                f"Fold {fold.fold:02d}: {len(trades):3d} trades | "
                f"return {float(metrics.get('total_return_pct', 0.0)):+.2f}%"
            )
            trade_rows.extend(
                trade_to_row(
                    trade=trade,
                    case_id=case_id,
                    fold=fold.fold,
                    price_cache=price_cache,
                )
                for trade in trades
            )

    if not trade_rows:
        raise RuntimeError("Không có executed trade để phân tích.")

    trades_df = add_score_quantiles(pd.DataFrame(trade_rows))
    dimensions = [
        "market_regime",
        "symbol",
        "score_bucket",
        "score_quantile",
        "exit_reason",
        "holding_bucket",
        "trade_path_class",
        "fold",
    ]
    groups_df = pd.concat(
        [
            aggregate_dimension(trades_df, dimension=dimension)
            for dimension in dimensions
        ],
        ignore_index=True,
    )
    summary_df = build_case_summary(trades_df)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    trades_path = output_dir / "trade_diagnostics_trades.csv"
    groups_path = output_dir / "trade_diagnostics_groups.csv"
    summary_path = output_dir / "trade_diagnostics_summary.csv"
    trades_df.to_csv(trades_path, index=False, encoding="utf-8-sig")
    groups_df.to_csv(groups_path, index=False, encoding="utf-8-sig")
    summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 140)
    print("TRADE-LEVEL DIAGNOSTIC SUMMARY")
    print("=" * 140)
    print(summary_df.to_string(index=False))
    print(f"\nSaved: {trades_path}")
    print(f"Saved: {groups_path}")
    print(f"Saved: {summary_path}")


if __name__ == "__main__":
    main()
