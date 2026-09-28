from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtesting.engine import build_exit_model, run_backtest
from research.universes import TOP10_SYMBOLS
from strategy.donchian_breakout_entry import DonchianBreakoutEntryModel
from strategy.hybrid_trend_donchian_entry import HybridTrendDonchianEntryModel
from strategy.trend_strategy_v1 import TrendStrategyV1


def normalize_enum_text(value: Any) -> str:
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
        "entry_year": int(entry_date.year) if not pd.isna(entry_date) else None,
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
        "exit_reason": normalize_enum_text(getattr(trade, "exit_reason", "")),
        "signal_score": getattr(trade, "signal_score", None),
        "relative_strength": getattr(trade, "relative_strength", None),
        "adx": getattr(trade, "adx", None),
        "volume_ratio": getattr(trade, "volume_ratio", None),
        "market_regime": getattr(trade, "market_regime", None),
        "entry_model": getattr(trade, "entry_model", None),
    }


def build_model_registry(include_all_models: bool) -> dict[str, Any]:
    if not include_all_models:
        model = HybridTrendDonchianEntryModel(mode="trend_context")
        return {model.name: model}

    models = [
        TrendStrategyV1(),
        DonchianBreakoutEntryModel(),
        HybridTrendDonchianEntryModel(mode="trend_context"),
        HybridTrendDonchianEntryModel(mode="strict"),
        HybridTrendDonchianEntryModel(mode="score_blend"),
    ]

    registry: dict[str, Any] = {}
    for model in models:
        name = str(getattr(model, "name", model.__class__.__name__))
        if name in registry:
            raise ValueError(f"Entry model bị trùng tên: {name}")
        registry[name] = model
    return registry


def numeric_series(dataframe: pd.DataFrame, column: str) -> pd.Series:
    return (
        pd.to_numeric(dataframe[column], errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
    )


def calculate_profit_factor(pnl_values: pd.Series) -> float:
    gross_profit = float(pnl_values[pnl_values > 0].sum())
    gross_loss = abs(float(pnl_values[pnl_values < 0].sum()))
    if gross_loss <= 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def calculate_sharpe(returns_pct: pd.Series) -> float:
    if len(returns_pct) < 2:
        return 0.0
    standard_deviation = float(returns_pct.std(ddof=1))
    if standard_deviation <= 0:
        return 0.0
    return float(returns_pct.mean() / standard_deviation * np.sqrt(len(returns_pct)))


def calculate_group_metrics(group: pd.DataFrame) -> dict[str, Any]:
    returns = numeric_series(group, "net_return_pct")
    pnl_values = numeric_series(group, "net_pnl")
    holding_days = numeric_series(group, "holding_days")
    transaction_costs = numeric_series(group, "transaction_cost")
    wins = group["is_win"].fillna(False).astype(bool)
    total_trades = int(len(group))
    winning_trades = int(wins.sum())
    winners = returns[returns > 0]
    losers = returns[returns < 0]
    average_winner = float(winners.mean()) if not winners.empty else 0.0
    average_loser = float(losers.mean()) if not losers.empty else 0.0

    return {
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": total_trades - winning_trades,
        "win_rate_pct": winning_trades / total_trades * 100.0 if total_trades else 0.0,
        "total_net_pnl": float(pnl_values.sum()),
        "total_transaction_cost": float(transaction_costs.sum()),
        "average_return_pct": float(returns.mean()),
        "median_return_pct": float(returns.median()),
        "expectancy_pct": float(returns.mean()),
        "sharpe_ratio": calculate_sharpe(returns),
        "profit_factor": calculate_profit_factor(pnl_values),
        "average_winner_pct": average_winner,
        "average_loser_pct": average_loser,
        "payoff_ratio": average_winner / abs(average_loser) if average_loser < 0 else 0.0,
        "average_holding_days": float(holding_days.mean()),
        "median_holding_days": float(holding_days.median()),
        "best_trade_pct": float(returns.max()),
        "worst_trade_pct": float(returns.min()),
    }


def build_group_table(dataframe: pd.DataFrame, group_columns: list[str]) -> pd.DataFrame:
    if dataframe.empty:
        return pd.DataFrame()

    group_key: str | list[str] = group_columns[0] if len(group_columns) == 1 else group_columns
    rows: list[dict[str, Any]] = []

    for group_value, group in dataframe.groupby(
        group_key,
        dropna=False,
        observed=False,
        sort=True,
    ):
        values = [group_value] if len(group_columns) == 1 else list(group_value)
        row = {column: value for column, value in zip(group_columns, values)}
        row.update(calculate_group_metrics(group))
        rows.append(row)

    return pd.DataFrame(rows).sort_values(group_columns).reset_index(drop=True)


def add_bucket_columns(trades_df: pd.DataFrame) -> pd.DataFrame:
    dataframe = trades_df.copy()

    dataframe["score_bucket"] = pd.cut(
        pd.to_numeric(dataframe["signal_score"], errors="coerce"),
        bins=[-np.inf, 69.9999, 79.9999, 89.9999, np.inf],
        labels=["<70", "70-79", "80-89", "90+"],
        include_lowest=True,
    )
    dataframe["adx_bucket"] = pd.cut(
        pd.to_numeric(dataframe["adx"], errors="coerce"),
        bins=[-np.inf, 19.9999, 24.9999, 29.9999, 34.9999, np.inf],
        labels=["<20", "20-24", "25-29", "30-34", "35+"],
        include_lowest=True,
    )
    dataframe["rs_bucket"] = pd.cut(
        pd.to_numeric(dataframe["relative_strength"], errors="coerce"),
        bins=[-np.inf, -0.0001, 1.9999, 3.9999, 5.9999, np.inf],
        labels=["<0", "0-1.99", "2-3.99", "4-5.99", "6+"],
        include_lowest=True,
    )
    dataframe["volume_bucket"] = pd.cut(
        pd.to_numeric(dataframe["volume_ratio"], errors="coerce"),
        bins=[-np.inf, 0.9999, 1.4999, 1.9999, 2.9999, np.inf],
        labels=["<1.0", "1.0-1.49", "1.5-1.99", "2.0-2.99", "3.0+"],
        include_lowest=True,
    )

    dataframe["score_80_plus"] = pd.to_numeric(
        dataframe["signal_score"], errors="coerce"
    ) >= 80
    dataframe["adx_25_plus"] = pd.to_numeric(
        dataframe["adx"], errors="coerce"
    ) >= 25
    dataframe["rs_positive"] = pd.to_numeric(
        dataframe["relative_strength"], errors="coerce"
    ) >= 0
    dataframe["volume_15_plus"] = pd.to_numeric(
        dataframe["volume_ratio"], errors="coerce"
    ) >= 1.5

    return dataframe


def build_cross_analysis(trades_df: pd.DataFrame) -> pd.DataFrame:
    filters = {
        "score>=80": trades_df["score_80_plus"],
        "adx>=25": trades_df["adx_25_plus"],
        "rs>=0": trades_df["rs_positive"],
        "volume>=1.5": trades_df["volume_15_plus"],
        "score>=80_and_adx>=25": trades_df["score_80_plus"] & trades_df["adx_25_plus"],
        "score>=80_and_rs>=0": trades_df["score_80_plus"] & trades_df["rs_positive"],
        "score>=80_and_volume>=1.5": trades_df["score_80_plus"] & trades_df["volume_15_plus"],
        "score>=80_adx>=25_rs>=0": (
            trades_df["score_80_plus"]
            & trades_df["adx_25_plus"]
            & trades_df["rs_positive"]
        ),
        "all_quality_filters": (
            trades_df["score_80_plus"]
            & trades_df["adx_25_plus"]
            & trades_df["rs_positive"]
            & trades_df["volume_15_plus"]
        ),
    }

    rows: list[dict[str, Any]] = []
    for regime in ["ALL", "BULL", "SIDEWAY", "BEAR"]:
        regime_mask = (
            pd.Series(True, index=trades_df.index)
            if regime == "ALL"
            else trades_df["market_regime"] == regime
        )
        for filter_name, filter_mask in filters.items():
            subset = trades_df[regime_mask & filter_mask]
            if subset.empty:
                continue
            rows.append(
                {
                    "market_regime": regime,
                    "filter_name": filter_name,
                    **calculate_group_metrics(subset),
                }
            )

    if not rows:
        return pd.DataFrame()

    return (
        pd.DataFrame(rows)
        .sort_values(
            ["market_regime", "expectancy_pct", "profit_factor", "total_trades"],
            ascending=[True, False, False, False],
        )
        .reset_index(drop=True)
    )


def save_dataframe(dataframe: pd.DataFrame, output_path: str) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        dataframe.to_csv(path, index=False, encoding="utf-8-sig")
    except PermissionError as exc:
        raise PermissionError(f"Không thể ghi {path}. Hãy đóng file trong Excel.") from exc
    print(f"Đã xuất: {path}")


def print_table(title: str, dataframe: pd.DataFrame) -> None:
    print()
    print("=" * 170)
    print(title)
    print("=" * 170)
    print(dataframe.to_string(index=False, float_format=lambda value: f"{value:.2f}"))


def run_analysis(args: argparse.Namespace) -> None:
    symbols = TOP10_SYMBOLS if args.symbol is None else [
        symbol.upper().strip() for symbol in args.symbol
    ]

    if args.max_positions <= 0:
        raise ValueError("max_positions phải lớn hơn 0.")

    registry = build_model_registry(args.all_models)
    trade_rows: list[dict[str, Any]] = []

    for entry_model_name, entry_model in registry.items():
        print()
        print("=" * 100)
        print(f"TRADE RANKING SOURCE: {entry_model_name}")
        print("=" * 100)

        exit_model = build_exit_model(
            name="atr",
            stop_atr_multiplier=args.atr_stop_multiplier,
            target_atr_multiplier=args.atr_target_multiplier,
            break_even_trigger=5.0,
            trailing_atr_multiplier=2.0,
        )

        trades, metrics, _ = run_backtest(
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

        print(f"Trades: {metrics.get('total_trades', len(trades))}")
        print(f"Return: {metrics.get('total_return_pct', 0.0):+.2f}%")
        print(f"Sharpe: {metrics.get('sharpe_ratio', 0.0):.2f}")

        for trade in trades:
            row = trade_to_row(trade)
            if not row["entry_model"]:
                row["entry_model"] = entry_model_name
            trade_rows.append(row)

    trades_df = pd.DataFrame(trade_rows)
    if trades_df.empty:
        raise ValueError("Không có trade để phân tích.")

    trades_df = add_bucket_columns(trades_df)
    score_df = build_group_table(trades_df, ["score_bucket"])
    adx_df = build_group_table(trades_df, ["adx_bucket"])
    rs_df = build_group_table(trades_df, ["rs_bucket"])
    volume_df = build_group_table(trades_df, ["volume_bucket"])
    model_df = build_group_table(trades_df, ["entry_model"])
    regime_df = build_group_table(trades_df, ["market_regime"])
    cross_df = build_cross_analysis(trades_df)

    outputs = [
        (trades_df, args.trades_output),
        (score_df, args.score_output),
        (adx_df, args.adx_output),
        (rs_df, args.rs_output),
        (volume_df, args.volume_output),
        (model_df, args.model_output),
        (regime_df, args.regime_output),
        (cross_df, args.cross_output),
    ]
    for dataframe, output_path in outputs:
        save_dataframe(dataframe, output_path)

    print_table("TRADE SCORE BUCKET", score_df)
    print_table("TRADE ADX BUCKET", adx_df)
    print_table("TRADE RELATIVE STRENGTH BUCKET", rs_df)
    print_table("TRADE VOLUME RATIO BUCKET", volume_df)
    print_table("TRADE ENTRY MODEL", model_df)
    print_table("TRADE MARKET REGIME", regime_df)
    print_table("TRADE CROSS ANALYSIS", cross_df)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phân tích chất lượng trade theo Score, ADX, RS và Volume."
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
    parser.add_argument("--all-models", action="store_true")
    parser.add_argument(
        "--trades-output",
        default="research_results/trade_ranking_trades.csv",
    )
    parser.add_argument(
        "--score-output",
        default="research_results/trade_score_bucket.csv",
    )
    parser.add_argument(
        "--adx-output",
        default="research_results/trade_adx_bucket.csv",
    )
    parser.add_argument(
        "--rs-output",
        default="research_results/trade_rs_bucket.csv",
    )
    parser.add_argument(
        "--volume-output",
        default="research_results/trade_volume_bucket.csv",
    )
    parser.add_argument(
        "--model-output",
        default="research_results/trade_entry_model.csv",
    )
    parser.add_argument(
        "--regime-output",
        default="research_results/trade_market_regime.csv",
    )
    parser.add_argument(
        "--cross-output",
        default="research_results/trade_cross_analysis.csv",
    )
    return parser.parse_args()


def main() -> None:
    run_analysis(parse_args())


if __name__ == "__main__":
    main()
