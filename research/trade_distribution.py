from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtesting.engine import build_exit_model, run_backtest
from research.universes import TOP10_SYMBOLS
from strategy.hybrid_trend_donchian_entry import HybridTrendDonchianEntryModel


def enum_name(value: Any) -> str:
    text = str(value)
    return text.split(".")[-1] if "." in text else text


def trade_to_row(trade: Any) -> dict[str, Any]:
    entry_date = pd.to_datetime(getattr(trade, "entry_date", None), errors="coerce")
    exit_date = pd.to_datetime(getattr(trade, "exit_date", None), errors="coerce")
    net_pnl = float(getattr(trade, "net_pnl", getattr(trade, "pnl", 0.0)))
    return {
        "symbol": getattr(trade, "symbol", None),
        "entry_date": entry_date,
        "exit_date": exit_date,
        "exit_year": int(exit_date.year) if not pd.isna(exit_date) else None,
        "entry_price": float(getattr(trade, "entry_price", 0.0)),
        "exit_price": float(getattr(trade, "exit_price", 0.0)),
        "quantity": int(getattr(trade, "quantity", 0)),
        "net_pnl": net_pnl,
        "net_return_pct": float(
            getattr(trade, "net_return_pct", getattr(trade, "return_pct", 0.0))
        ),
        "holding_days": int(getattr(trade, "holding_days", 0)),
        "transaction_cost": float(getattr(trade, "total_transaction_cost", 0.0)),
        "is_win": bool(getattr(trade, "is_win", net_pnl > 0)),
        "exit_reason": enum_name(getattr(trade, "exit_reason", "")),
        "execution": enum_name(getattr(trade, "execution", "")),
    }


def numeric(df: pd.DataFrame, column: str) -> pd.Series:
    return (
        pd.to_numeric(df[column], errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
    )


def profit_factor(pnl: pd.Series) -> float:
    gross_profit = float(pnl[pnl > 0].sum())
    gross_loss = abs(float(pnl[pnl < 0].sum()))
    if gross_loss <= 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def longest_streak(values: pd.Series, target: bool) -> int:
    longest = current = 0
    for value in values.fillna(False).astype(bool):
        if value == target:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def metrics(group: pd.DataFrame) -> dict[str, Any]:
    returns = numeric(group, "net_return_pct")
    pnl = numeric(group, "net_pnl")
    holds = numeric(group, "holding_days")
    costs = numeric(group, "transaction_cost")
    total = len(group)
    wins = int(group["is_win"].fillna(False).astype(bool).sum())
    winners = returns[returns > 0]
    losers = returns[returns < 0]
    avg_winner = float(winners.mean()) if not winners.empty else 0.0
    avg_loser = float(losers.mean()) if not losers.empty else 0.0

    return {
        "total_trades": total,
        "winning_trades": wins,
        "losing_trades": total - wins,
        "win_rate_pct": wins / total * 100 if total else 0.0,
        "total_net_pnl": float(pnl.sum()),
        "total_transaction_cost": float(costs.sum()),
        "average_return_pct": float(returns.mean()),
        "median_return_pct": float(returns.median()),
        "best_trade_pct": float(returns.max()),
        "worst_trade_pct": float(returns.min()),
        "average_winner_pct": avg_winner,
        "average_loser_pct": avg_loser,
        "payoff_ratio": avg_winner / abs(avg_loser) if avg_loser < 0 else 0.0,
        "expectancy_pct": float(returns.mean()),
        "profit_factor": profit_factor(pnl),
        "average_holding_days": float(holds.mean()),
        "median_holding_days": float(holds.median()),
    }


def grouped_table(df: pd.DataFrame, column: str) -> pd.DataFrame:
    rows = [{column: key, **metrics(group)} for key, group in df.groupby(column, dropna=False)]
    if not rows:
        return pd.DataFrame()
    return (
        pd.DataFrame(rows)
        .sort_values(
            ["expectancy_pct", "profit_factor", "total_trades"],
            ascending=[False, False, False],
        )
        .reset_index(drop=True)
    )


def save(df: pd.DataFrame, path_text: str) -> None:
    path = Path(path_text)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_csv(path, index=False, encoding="utf-8-sig")
    except PermissionError as exc:
        raise PermissionError(f"Không thể ghi {path}. Hãy đóng file trong Excel.") from exc
    print(f"Đã xuất: {path}")


def run_analysis(args: argparse.Namespace) -> None:
    symbols = TOP10_SYMBOLS if args.symbol is None else [
        value.upper().strip() for value in args.symbol
    ]

    if args.max_positions <= 0:
        raise ValueError("max_positions phải lớn hơn 0.")

    entry_model = HybridTrendDonchianEntryModel(mode="trend_context")
    exit_model = build_exit_model(
        name="atr",
        stop_atr_multiplier=args.atr_stop_multiplier,
        target_atr_multiplier=args.atr_target_multiplier,
        break_even_trigger=5.0,
        trailing_atr_multiplier=2.0,
    )

    trades, source_metrics, _ = run_backtest(
        symbols=symbols,
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.capital,
        position_size_pct=100.0 / args.max_positions,
        stop_loss_pct=args.sl,
        take_profit_pct=args.tp,
        max_holding_days=args.hold,
        min_adx=args.min_adx,
        entry_model=entry_model,
        exit_model=exit_model,
        verbose=False,
    )

    if not trades:
        raise ValueError("Source backtest không tạo được giao dịch.")

    trades_df = pd.DataFrame([trade_to_row(trade) for trade in trades])
    trades_df["holding_bucket"] = pd.cut(
        pd.to_numeric(trades_df["holding_days"], errors="coerce"),
        bins=[-1, 3, 5, 10, 15, 20, 30, np.inf],
        labels=["0-3", "4-5", "6-10", "11-15", "16-20", "21-30", "31+"],
        include_lowest=True,
    )

    ordered = trades_df.sort_values(["exit_date", "entry_date", "symbol"]).reset_index(drop=True)
    summary = metrics(ordered)
    summary.update({
        "entry_model": entry_model.name,
        "start_date": args.start,
        "end_date": args.end,
        "source_total_return_pct": float(source_metrics.get("total_return_pct", 0.0)),
        "source_sharpe_ratio": float(source_metrics.get("sharpe_ratio", 0.0)),
        "source_max_drawdown_pct": float(source_metrics.get("max_drawdown_pct", 0.0)),
        "longest_win_streak": longest_streak(ordered["is_win"], True),
        "longest_loss_streak": longest_streak(ordered["is_win"], False),
    })

    summary_df = pd.DataFrame([summary])
    symbol_df = grouped_table(trades_df, "symbol")
    year_df = grouped_table(trades_df, "exit_year")
    exit_df = grouped_table(trades_df, "exit_reason")
    holding_df = grouped_table(trades_df, "holding_bucket")

    save(trades_df, args.trades_output)
    save(summary_df, args.summary_output)
    save(symbol_df, args.symbol_output)
    save(year_df, args.year_output)
    save(exit_df, args.exit_reason_output)
    save(holding_df, args.holding_output)

    print("\n" + "=" * 150)
    print("TRADE DISTRIBUTION SUMMARY")
    print("=" * 150)
    print(summary_df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))

    for title, table in [
        ("BY SYMBOL", symbol_df),
        ("BY YEAR", year_df),
        ("BY EXIT REASON", exit_df),
        ("BY HOLDING BUCKET", holding_df),
    ]:
        print("\n" + "=" * 120)
        print(f"TRADE DISTRIBUTION {title}")
        print("=" * 120)
        print(table.to_string(index=False, float_format=lambda x: f"{x:.2f}"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Trade distribution analysis cho Hybrid Trend Context."
    )
    parser.add_argument("--symbol", nargs="+", default=None)
    parser.add_argument("--start", default="2018-08-04")
    parser.add_argument("--end", default="2026-07-31")
    parser.add_argument("--capital", type=float, default=100_000_000)
    parser.add_argument("--max-positions", type=int, default=5)
    parser.add_argument("--hold", type=int, default=30)
    parser.add_argument("--atr-stop-multiplier", type=float, default=2.0)
    parser.add_argument("--atr-target-multiplier", type=float, default=5.0)
    parser.add_argument("--sl", type=float, default=3.0)
    parser.add_argument("--tp", type=float, default=8.0)
    parser.add_argument("--min-adx", type=float, default=20.0)
    parser.add_argument(
        "--trades-output",
        default="research_results/trade_distribution_trades.csv",
    )
    parser.add_argument(
        "--summary-output",
        default="research_results/trade_distribution_summary.csv",
    )
    parser.add_argument(
        "--symbol-output",
        default="research_results/trade_distribution_by_symbol.csv",
    )
    parser.add_argument(
        "--year-output",
        default="research_results/trade_distribution_by_year.csv",
    )
    parser.add_argument(
        "--exit-reason-output",
        default="research_results/trade_distribution_by_exit_reason.csv",
    )
    parser.add_argument(
        "--holding-output",
        default="research_results/trade_distribution_by_holding_bucket.csv",
    )
    return parser.parse_args()


def main() -> None:
    run_analysis(parse_args())


if __name__ == "__main__":
    main()
