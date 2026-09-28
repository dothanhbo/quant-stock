from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtesting.engine import (
    build_exit_model,
    run_backtest,
)
from core.database import engine
from research.universes import TOP10_SYMBOLS
from strategy.donchian_breakout_entry import (
    DonchianBreakoutEntryModel,
)
from strategy.hybrid_trend_donchian_entry import (
    HybridTrendDonchianEntryModel,
)
from strategy.market_regime import (
    prepare_market_regime_history,
)
from strategy.trend_strategy_v1 import (
    TrendStrategyV1,
)


DEFAULT_TRADES_OUTPUT = (
    "research_results/"
    "market_regime_trades.csv"
)

DEFAULT_SUMMARY_OUTPUT = (
    "research_results/"
    "market_regime_summary.csv"
)

DEFAULT_SYMBOL_OUTPUT = (
    "research_results/"
    "market_regime_by_symbol.csv"
)

DEFAULT_MODEL_OUTPUT = (
    "research_results/"
    "market_regime_by_model.csv"
)

DEFAULT_YEAR_OUTPUT = (
    "research_results/"
    "market_regime_by_year.csv"
)

DEFAULT_COVERAGE_OUTPUT = (
    "research_results/"
    "market_regime_coverage.csv"
)


def normalize_enum_text(
    value: Any,
) -> str:
    text = str(value)

    if "." in text:
        return text.split(
            "."
        )[-1]

    return text


def load_regime_history(
    *,
    start_date: str | None,
    end_date: str | None,
) -> pd.DataFrame:
    benchmark_df = pd.read_sql(
        """
        SELECT time, close
        FROM prices
        WHERE symbol = 'VNINDEX'
        ORDER BY time
        """,
        engine,
    )

    regime_df = (
        prepare_market_regime_history(
            benchmark_df
        )
    )

    if regime_df.empty:
        raise ValueError(
            "Không tạo được lịch sử "
            "Market Regime từ VNINDEX."
        )

    regime_df[
        "time"
    ] = pd.to_datetime(
        regime_df[
            "time"
        ],
        errors="coerce",
    )

    regime_df = (
        regime_df
        .dropna(
            subset=[
                "time",
                "Market_Regime",
            ]
        )
        .sort_values(
            "time"
        )
        .reset_index(
            drop=True
        )
    )

    if start_date is not None:
        start = pd.to_datetime(
            start_date,
            errors="coerce",
        )

        if pd.isna(start):
            raise ValueError(
                f"start_date không hợp lệ: "
                f"{start_date}"
            )

        regime_df = regime_df[
            regime_df["time"]
            >= start
        ]

    if end_date is not None:
        end = pd.to_datetime(
            end_date,
            errors="coerce",
        )

        if pd.isna(end):
            raise ValueError(
                f"end_date không hợp lệ: "
                f"{end_date}"
            )

        regime_df = regime_df[
            regime_df["time"]
            <= end
        ]

    if regime_df.empty:
        raise ValueError(
            "Không còn dữ liệu regime "
            "trong khoảng thời gian yêu cầu."
        )

    return regime_df


def build_model_registry(
) -> dict[str, Any]:
    models = [
        TrendStrategyV1(),

        DonchianBreakoutEntryModel(),

        HybridTrendDonchianEntryModel(
            mode="trend_context",
        ),

        HybridTrendDonchianEntryModel(
            mode="strict",
        ),
    ]

    registry: dict[
        str,
        Any,
    ] = {}

    for model in models:
        model_name = str(
            getattr(
                model,
                "name",
                model.__class__.__name__,
            )
        )

        if model_name in registry:
            raise ValueError(
                "Entry model bị trùng tên: "
                f"{model_name}"
            )

        registry[
            model_name
        ] = model

    return registry


def trade_to_row(
    *,
    trade: Any,
    entry_model_name: str,
) -> dict[str, Any]:
    entry_date = pd.to_datetime(
        getattr(
            trade,
            "entry_date",
            None,
        ),
        errors="coerce",
    )

    exit_date = pd.to_datetime(
        getattr(
            trade,
            "exit_date",
            None,
        ),
        errors="coerce",
    )

    net_pnl = float(
        getattr(
            trade,
            "net_pnl",
            getattr(
                trade,
                "pnl",
                0.0,
            ),
        )
    )

    return {
        "entry_model": (
            entry_model_name
        ),
        "symbol": getattr(
            trade,
            "symbol",
            None,
        ),
        "entry_date": (
            entry_date
        ),
        "exit_date": (
            exit_date
        ),
        "entry_year": (
            int(
                entry_date.year
            )
            if not pd.isna(
                entry_date
            )
            else None
        ),
        "exit_year": (
            int(
                exit_date.year
            )
            if not pd.isna(
                exit_date
            )
            else None
        ),
        "entry_price": float(
            getattr(
                trade,
                "entry_price",
                0.0,
            )
        ),
        "exit_price": float(
            getattr(
                trade,
                "exit_price",
                0.0,
            )
        ),
        "quantity": int(
            getattr(
                trade,
                "quantity",
                0,
            )
        ),
        "cost": float(
            getattr(
                trade,
                "cost",
                0.0,
            )
        ),
        "net_pnl": (
            net_pnl
        ),
        "net_return_pct": float(
            getattr(
                trade,
                "net_return_pct",
                getattr(
                    trade,
                    "return_pct",
                    0.0,
                ),
            )
        ),
        "holding_days": int(
            getattr(
                trade,
                "holding_days",
                0,
            )
        ),
        "transaction_cost": float(
            getattr(
                trade,
                "total_transaction_cost",
                0.0,
            )
        ),
        "is_win": bool(
            getattr(
                trade,
                "is_win",
                net_pnl > 0,
            )
        ),
        "exit_reason": (
            normalize_enum_text(
                getattr(
                    trade,
                    "exit_reason",
                    "",
                )
            )
        ),
        "execution": (
            normalize_enum_text(
                getattr(
                    trade,
                    "execution",
                    "",
                )
            )
        ),
    }


def attach_entry_regime(
    *,
    trades_df: pd.DataFrame,
    regime_df: pd.DataFrame,
) -> pd.DataFrame:
    if trades_df.empty:
        return trades_df.copy()

    trades = (
        trades_df.copy()
        .sort_values(
            "entry_date"
        )
        .reset_index(
            drop=True
        )
    )

    regime_columns = [
        column
        for column in [
            "time",
            "Market_Regime",
            "close",
            "EMA50",
            "EMA200",
            "EMA50_Slope_10D",
            "Return_20D",
            "Distance_EMA200",
        ]
        if column
        in regime_df.columns
    ]

    regimes = (
        regime_df[
            regime_columns
        ]
        .copy()
        .sort_values(
            "time"
        )
        .reset_index(
            drop=True
        )
    )

    attached = pd.merge_asof(
        trades,
        regimes,
        left_on="entry_date",
        right_on="time",
        direction="backward",
        allow_exact_matches=True,
    )

    attached[
        "Market_Regime"
    ] = (
        attached[
            "Market_Regime"
        ]
        .fillna(
            "UNKNOWN"
        )
    )

    attached[
        "regime_date"
    ] = attached[
        "time"
    ]

    attached = attached.drop(
        columns=[
            "time",
        ],
        errors="ignore",
    )

    return attached


def numeric_series(
    dataframe: pd.DataFrame,
    column: str,
) -> pd.Series:
    return (
        pd.to_numeric(
            dataframe[column],
            errors="coerce",
        )
        .replace(
            [
                np.inf,
                -np.inf,
            ],
            np.nan,
        )
        .dropna()
    )


def calculate_profit_factor(
    pnl_values: pd.Series,
) -> float:
    gross_profit = float(
        pnl_values[
            pnl_values > 0
        ].sum()
    )

    gross_loss = abs(
        float(
            pnl_values[
                pnl_values < 0
            ].sum()
        )
    )

    if gross_loss <= 0:
        return (
            float("inf")
            if gross_profit > 0
            else 0.0
        )

    return (
        gross_profit
        / gross_loss
    )


def calculate_group_metrics(
    group: pd.DataFrame,
) -> dict[str, Any]:
    returns = numeric_series(
        group,
        "net_return_pct",
    )

    pnl_values = numeric_series(
        group,
        "net_pnl",
    )

    holding_days = numeric_series(
        group,
        "holding_days",
    )

    costs = numeric_series(
        group,
        "transaction_cost",
    )

    wins = (
        group[
            "is_win"
        ]
        .fillna(
            False
        )
        .astype(
            bool
        )
    )

    total_trades = int(
        len(
            group
        )
    )

    winning_trades = int(
        wins.sum()
    )

    losing_trades = (
        total_trades
        - winning_trades
    )

    winners = returns[
        returns > 0
    ]

    losers = returns[
        returns < 0
    ]

    average_winner = (
        float(
            winners.mean()
        )
        if not winners.empty
        else 0.0
    )

    average_loser = (
        float(
            losers.mean()
        )
        if not losers.empty
        else 0.0
    )

    payoff_ratio = (
        average_winner
        / abs(
            average_loser
        )
        if average_loser < 0
        else 0.0
    )

    return {
        "total_trades": (
            total_trades
        ),
        "winning_trades": (
            winning_trades
        ),
        "losing_trades": (
            losing_trades
        ),
        "win_rate_pct": (
            winning_trades
            / total_trades
            * 100.0
            if total_trades > 0
            else 0.0
        ),
        "total_net_pnl": float(
            pnl_values.sum()
        ),
        "total_transaction_cost": float(
            costs.sum()
        ),
        "average_return_pct": float(
            returns.mean()
        ),
        "median_return_pct": float(
            returns.median()
        ),
        "expectancy_pct": float(
            returns.mean()
        ),
        "average_winner_pct": (
            average_winner
        ),
        "average_loser_pct": (
            average_loser
        ),
        "payoff_ratio": (
            payoff_ratio
        ),
        "profit_factor": (
            calculate_profit_factor(
                pnl_values
            )
        ),
        "average_holding_days": float(
            holding_days.mean()
        ),
        "median_holding_days": float(
            holding_days.median()
        ),
        "best_trade_pct": float(
            returns.max()
        ),
        "worst_trade_pct": float(
            returns.min()
        ),
    }


def build_group_table(
    *,
    dataframe: pd.DataFrame,
    group_columns: list[str],
) -> pd.DataFrame:
    if dataframe.empty:
        return pd.DataFrame()

    rows: list[
        dict[str, Any]
    ] = []

    group_key: str | list[str]

    if len(
        group_columns
    ) == 1:
        group_key = (
            group_columns[0]
        )
    else:
        group_key = (
            group_columns
        )

    for (
        group_value,
        group,
    ) in dataframe.groupby(
        group_key,
        dropna=False,
        sort=True,
        observed=False,
    ):
        if len(
            group_columns
        ) == 1:
            values = [
                group_value
            ]
        else:
            values = list(
                group_value
            )

        row = {
            column: value
            for (
                column,
                value,
            ) in zip(
                group_columns,
                values,
            )
        }

        row.update(
            calculate_group_metrics(
                group
            )
        )

        rows.append(
            row
        )

    result = pd.DataFrame(
        rows
    )

    if result.empty:
        return result

    return (
        result
        .sort_values(
            by=[
                *group_columns,
            ],
        )
        .reset_index(
            drop=True
        )
    )


def build_regime_coverage(
    regime_df: pd.DataFrame,
) -> pd.DataFrame:
    if regime_df.empty:
        return pd.DataFrame()

    coverage = (
        regime_df[
            "Market_Regime"
        ]
        .value_counts(
            dropna=False
        )
        .rename_axis(
            "Market_Regime"
        )
        .reset_index(
            name="market_days"
        )
    )

    total_days = int(
        coverage[
            "market_days"
        ].sum()
    )

    coverage[
        "market_day_pct"
    ] = (
        coverage[
            "market_days"
        ]
        / total_days
        * 100.0
        if total_days > 0
        else 0.0
    )

    return (
        coverage
        .sort_values(
            by=[
                "Market_Regime",
            ]
        )
        .reset_index(
            drop=True
        )
    )


def run_market_regime_benchmark(
    *,
    symbols: list[str],
    start_date: str,
    end_date: str,
    initial_capital: float,
    max_positions: int,
    max_holding_days: int,
    atr_stop_multiplier: float,
    atr_target_multiplier: float,
    stop_loss_pct: float,
    take_profit_pct: float,
    min_adx: float,
    include_all_models: bool,
    trades_output_path: str,
    summary_output_path: str,
    symbol_output_path: str,
    model_output_path: str,
    year_output_path: str,
    coverage_output_path: str,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    if max_positions <= 0:
        raise ValueError(
            "max_positions phải lớn hơn 0."
        )

    position_size_pct = (
        100.0
        / max_positions
    )

    regime_df = load_regime_history(
        start_date=start_date,
        end_date=end_date,
    )

    registry = (
        build_model_registry()
        if include_all_models
        else {
            (
                "hybrid_trend_donchian_v1"
                "__trend_context"
            ): HybridTrendDonchianEntryModel(
                mode="trend_context",
            )
        }
    )

    trade_rows: list[
        dict[str, Any]
    ] = []

    for (
        entry_model_name,
        entry_model,
    ) in registry.items():
        print()
        print("=" * 100)
        print(
            "REGIME SOURCE BACKTEST: "
            f"{entry_model_name}"
        )
        print("=" * 100)

        exit_model = build_exit_model(
            name="atr",
            stop_atr_multiplier=(
                atr_stop_multiplier
            ),
            target_atr_multiplier=(
                atr_target_multiplier
            ),
            break_even_trigger=5.0,
            trailing_atr_multiplier=2.0,
        )

        (
            trades,
            metrics,
            _,
        ) = run_backtest(
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            initial_capital=(
                initial_capital
            ),
            position_size_pct=(
                position_size_pct
            ),
            stop_loss_pct=(
                stop_loss_pct
            ),
            take_profit_pct=(
                take_profit_pct
            ),
            max_holding_days=(
                max_holding_days
            ),
            min_adx=min_adx,
            entry_model=entry_model,
            exit_model=exit_model,
            verbose=False,
        )

        print(
            f"Trades: "
            f"{metrics.get('total_trades', len(trades))}"
        )

        print(
            f"Return: "
            f"{metrics.get('total_return_pct', 0.0):+.2f}%"
        )

        print(
            f"Sharpe: "
            f"{metrics.get('sharpe_ratio', 0.0):.2f}"
        )

        for trade in trades:
            trade_rows.append(
                trade_to_row(
                    trade=trade,
                    entry_model_name=(
                        entry_model_name
                    ),
                )
            )

    raw_trades_df = pd.DataFrame(
        trade_rows
    )

    if raw_trades_df.empty:
        raise ValueError(
            "Không có trade để benchmark "
            "theo Market Regime."
        )

    trades_df = attach_entry_regime(
        trades_df=raw_trades_df,
        regime_df=regime_df,
    )

    summary_df = build_group_table(
        dataframe=trades_df,
        group_columns=[
            "Market_Regime",
        ],
    )

    by_symbol_df = build_group_table(
        dataframe=trades_df,
        group_columns=[
            "symbol",
            "Market_Regime",
        ],
    )

    by_model_df = build_group_table(
        dataframe=trades_df,
        group_columns=[
            "entry_model",
            "Market_Regime",
        ],
    )

    by_year_df = build_group_table(
        dataframe=trades_df,
        group_columns=[
            "entry_year",
            "Market_Regime",
        ],
    )

    coverage_df = build_regime_coverage(
        regime_df
    )

    save_dataframe(
        trades_df,
        trades_output_path,
    )

    save_dataframe(
        summary_df,
        summary_output_path,
    )

    save_dataframe(
        by_symbol_df,
        symbol_output_path,
    )

    save_dataframe(
        by_model_df,
        model_output_path,
    )

    save_dataframe(
        by_year_df,
        year_output_path,
    )

    save_dataframe(
        coverage_df,
        coverage_output_path,
    )

    print_results(
        summary_df=summary_df,
        by_model_df=by_model_df,
        coverage_df=coverage_df,
    )

    return (
        trades_df,
        summary_df,
        by_symbol_df,
        by_model_df,
        by_year_df,
        coverage_df,
    )


def save_dataframe(
    dataframe: pd.DataFrame,
    output_path: str,
) -> None:
    path = Path(
        output_path
    )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        dataframe.to_csv(
            path,
            index=False,
            encoding="utf-8-sig",
        )

    except PermissionError as exc:
        raise PermissionError(
            f"Không thể ghi {path}. "
            "Hãy đóng file trong Excel."
        ) from exc

    print(
        f"Đã xuất: {path}"
    )


def print_results(
    *,
    summary_df: pd.DataFrame,
    by_model_df: pd.DataFrame,
    coverage_df: pd.DataFrame,
) -> None:
    print()
    print("=" * 180)
    print(
        "MARKET REGIME SUMMARY"
    )
    print("=" * 180)

    print(
        summary_df.to_string(
            index=False,
            float_format=(
                lambda value:
                f"{value:.2f}"
            ),
        )
    )

    print()
    print("=" * 190)
    print(
        "ENTRY MODEL × MARKET REGIME"
    )
    print("=" * 190)

    print(
        by_model_df.to_string(
            index=False,
            float_format=(
                lambda value:
                f"{value:.2f}"
            ),
        )
    )

    print()
    print("=" * 100)
    print(
        "MARKET REGIME COVERAGE"
    )
    print("=" * 100)

    print(
        coverage_df.to_string(
            index=False,
            float_format=(
                lambda value:
                f"{value:.2f}"
            ),
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark trade performance "
            "theo Market Regime tại entry."
        )
    )

    parser.add_argument(
        "--symbol",
        nargs="+",
        default=None,
    )

    parser.add_argument(
        "--start",
        default="2018-08-04",
    )

    parser.add_argument(
        "--end",
        default="2026-07-31",
    )

    parser.add_argument(
        "--capital",
        type=float,
        default=100_000_000,
    )

    parser.add_argument(
        "--max-positions",
        type=int,
        default=5,
    )

    parser.add_argument(
        "--hold",
        type=int,
        default=30,
    )

    parser.add_argument(
        "--atr-stop-multiplier",
        type=float,
        default=2.0,
    )

    parser.add_argument(
        "--atr-target-multiplier",
        type=float,
        default=5.0,
    )

    parser.add_argument(
        "--sl",
        type=float,
        default=3.0,
    )

    parser.add_argument(
        "--tp",
        type=float,
        default=8.0,
    )

    parser.add_argument(
        "--min-adx",
        type=float,
        default=20.0,
    )

    parser.add_argument(
        "--all-models",
        action="store_true",
    )

    parser.add_argument(
        "--trades-output",
        default=(
            DEFAULT_TRADES_OUTPUT
        ),
    )

    parser.add_argument(
        "--summary-output",
        default=(
            DEFAULT_SUMMARY_OUTPUT
        ),
    )

    parser.add_argument(
        "--symbol-output",
        default=(
            DEFAULT_SYMBOL_OUTPUT
        ),
    )

    parser.add_argument(
        "--model-output",
        default=(
            DEFAULT_MODEL_OUTPUT
        ),
    )

    parser.add_argument(
        "--year-output",
        default=(
            DEFAULT_YEAR_OUTPUT
        ),
    )

    parser.add_argument(
        "--coverage-output",
        default=(
            DEFAULT_COVERAGE_OUTPUT
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    symbols = (
        TOP10_SYMBOLS
        if args.symbol is None
        else [
            symbol.upper().strip()
            for symbol in args.symbol
        ]
    )

    run_market_regime_benchmark(
        symbols=symbols,
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.capital,
        max_positions=(
            args.max_positions
        ),
        max_holding_days=args.hold,
        atr_stop_multiplier=(
            args.atr_stop_multiplier
        ),
        atr_target_multiplier=(
            args.atr_target_multiplier
        ),
        stop_loss_pct=args.sl,
        take_profit_pct=args.tp,
        min_adx=args.min_adx,
        include_all_models=(
            args.all_models
        ),
        trades_output_path=(
            args.trades_output
        ),
        summary_output_path=(
            args.summary_output
        ),
        symbol_output_path=(
            args.symbol_output
        ),
        model_output_path=(
            args.model_output
        ),
        year_output_path=(
            args.year_output
        ),
        coverage_output_path=(
            args.coverage_output
        ),
    )


if __name__ == "__main__":
    main()
