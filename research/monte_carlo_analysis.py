from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtesting.engine import (
    build_exit_model,
    run_backtest,
)
from research.universes import TOP10_SYMBOLS
from strategy.hybrid_trend_donchian_entry import (
    HybridTrendDonchianEntryModel,
)


DEFAULT_SIMULATION_OUTPUT = (
    "research_results/"
    "monte_carlo_simulations.csv"
)

DEFAULT_SUMMARY_OUTPUT = (
    "research_results/"
    "monte_carlo_summary.csv"
)

DEFAULT_PERCENTILE_OUTPUT = (
    "research_results/"
    "monte_carlo_percentiles.csv"
)

DEFAULT_TRADE_OUTPUT = (
    "research_results/"
    "monte_carlo_source_trades.csv"
)


def trade_to_row(
    trade: Any,
) -> dict[str, Any]:
    return {
        "symbol": getattr(
            trade,
            "symbol",
            None,
        ),
        "entry_date": getattr(
            trade,
            "entry_date",
            None,
        ),
        "exit_date": getattr(
            trade,
            "exit_date",
            None,
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
        "net_pnl": float(
            getattr(
                trade,
                "net_pnl",
                getattr(
                    trade,
                    "pnl",
                    0.0,
                ),
            )
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
                False,
            )
        ),
        "exit_reason": str(
            getattr(
                trade,
                "exit_reason",
                "",
            )
        ),
    }


def calculate_max_drawdown_pct(
    equity_curve: np.ndarray,
) -> float:
    if equity_curve.size == 0:
        return 0.0

    peaks = np.maximum.accumulate(
        equity_curve
    )

    drawdowns = np.divide(
        equity_curve - peaks,
        peaks,
        out=np.zeros_like(
            equity_curve,
            dtype=float,
        ),
        where=peaks != 0,
    )

    return float(
        drawdowns.min()
        * 100.0
    )


def calculate_sharpe(
    returns: np.ndarray,
) -> float:
    if returns.size < 2:
        return 0.0

    standard_deviation = float(
        np.std(
            returns,
            ddof=1,
        )
    )

    if standard_deviation <= 0:
        return 0.0

    return float(
        np.mean(returns)
        / standard_deviation
        * math.sqrt(
            returns.size
        )
    )


def calculate_profit_factor(
    pnl_values: np.ndarray,
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


def simulate_trade_sequence(
    *,
    sampled_trade_returns_pct: np.ndarray,
    initial_capital: float,
    position_fraction: float,
    years: float,
) -> dict[str, float]:
    equity = float(
        initial_capital
    )

    equity_values = [
        equity
    ]

    portfolio_returns: list[
        float
    ] = []

    pnl_values: list[
        float
    ] = []

    for trade_return_pct in (
        sampled_trade_returns_pct
    ):
        allocated_capital = (
            equity
            * position_fraction
        )

        trade_return_fraction = (
            float(
                trade_return_pct
            )
            / 100.0
        )

        trade_pnl = (
            allocated_capital
            * trade_return_fraction
        )

        previous_equity = equity

        equity = max(
            0.0,
            equity + trade_pnl,
        )

        if previous_equity > 0:
            portfolio_return = (
                equity
                / previous_equity
                - 1.0
            )
        else:
            portfolio_return = 0.0

        portfolio_returns.append(
            portfolio_return
        )

        pnl_values.append(
            trade_pnl
        )

        equity_values.append(
            equity
        )

        if equity <= 0:
            break

    equity_array = np.asarray(
        equity_values,
        dtype=float,
    )

    return_array = np.asarray(
        portfolio_returns,
        dtype=float,
    )

    pnl_array = np.asarray(
        pnl_values,
        dtype=float,
    )

    final_equity = float(
        equity_array[-1]
    )

    total_return_pct = (
        (
            final_equity
            / initial_capital
            - 1.0
        )
        * 100.0
        if initial_capital > 0
        else 0.0
    )

    if (
        final_equity > 0
        and initial_capital > 0
        and years > 0
    ):
        cagr_pct = (
            (
                final_equity
                / initial_capital
            )
            ** (
                1.0 / years
            )
            - 1.0
        ) * 100.0
    else:
        cagr_pct = -100.0

    max_drawdown_pct = (
        calculate_max_drawdown_pct(
            equity_array
        )
    )

    sharpe_ratio = (
        calculate_sharpe(
            return_array
        )
    )

    profit_factor = (
        calculate_profit_factor(
            pnl_array
        )
    )

    wins = int(
        (
            pnl_array > 0
        ).sum()
    )

    completed_trades = int(
        pnl_array.size
    )

    win_rate_pct = (
        wins
        / completed_trades
        * 100.0
        if completed_trades > 0
        else 0.0
    )

    return {
        "final_equity": (
            final_equity
        ),
        "total_return_pct": (
            total_return_pct
        ),
        "cagr_pct": cagr_pct,
        "max_drawdown_pct": (
            max_drawdown_pct
        ),
        "sharpe_ratio": (
            sharpe_ratio
        ),
        "profit_factor": (
            profit_factor
        ),
        "win_rate_pct": (
            win_rate_pct
        ),
        "completed_trades": (
            completed_trades
        ),
        "ruined": float(
            final_equity <= 0
        ),
    }


def run_monte_carlo(
    *,
    trade_returns_pct: np.ndarray,
    simulations: int,
    initial_capital: float,
    position_fraction: float,
    years: float,
    random_seed: int,
    sampling_method: str,
) -> pd.DataFrame:
    if simulations <= 0:
        raise ValueError(
            "simulations phải lớn hơn 0."
        )

    if trade_returns_pct.size == 0:
        raise ValueError(
            "Không có trade return "
            "để mô phỏng."
        )

    if not (
        0 < position_fraction <= 1
    ):
        raise ValueError(
            "position_fraction phải "
            "nằm trong khoảng (0, 1]."
        )

    supported_methods = {
        "bootstrap",
        "shuffle",
    }

    if (
        sampling_method
        not in supported_methods
    ):
        raise ValueError(
            "sampling_method phải là "
            "'bootstrap' hoặc 'shuffle'."
        )

    random_generator = (
        np.random.default_rng(
            random_seed
        )
    )

    rows: list[
        dict[str, Any]
    ] = []

    number_of_trades = int(
        trade_returns_pct.size
    )

    for simulation_number in range(
        1,
        simulations + 1,
    ):
        if (
            sampling_method
            == "bootstrap"
        ):
            sampled_returns = (
                random_generator.choice(
                    trade_returns_pct,
                    size=number_of_trades,
                    replace=True,
                )
            )

        else:
            sampled_returns = (
                random_generator.permutation(
                    trade_returns_pct
                )
            )

        result = (
            simulate_trade_sequence(
                sampled_trade_returns_pct=(
                    sampled_returns
                ),
                initial_capital=(
                    initial_capital
                ),
                position_fraction=(
                    position_fraction
                ),
                years=years,
            )
        )

        rows.append(
            {
                "simulation": (
                    simulation_number
                ),
                "sampling_method": (
                    sampling_method
                ),
                **result,
            }
        )

        if (
            simulation_number % 500
            == 0
            or simulation_number
            == simulations
        ):
            print(
                f"Monte Carlo: "
                f"{simulation_number}/"
                f"{simulations}"
            )

    return pd.DataFrame(
        rows
    )


def finite_numeric_series(
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


def probability_pct(
    condition: pd.Series,
) -> float:
    if condition.empty:
        return 0.0

    return float(
        condition.mean()
        * 100.0
    )


def build_summary(
    simulations_df: pd.DataFrame,
) -> pd.DataFrame:
    if simulations_df.empty:
        return pd.DataFrame()

    returns = finite_numeric_series(
        simulations_df,
        "total_return_pct",
    )

    cagrs = finite_numeric_series(
        simulations_df,
        "cagr_pct",
    )

    drawdowns = finite_numeric_series(
        simulations_df,
        "max_drawdown_pct",
    )

    sharpes = finite_numeric_series(
        simulations_df,
        "sharpe_ratio",
    )

    profit_factors = (
        finite_numeric_series(
            simulations_df,
            "profit_factor",
        )
    )

    final_equities = (
        finite_numeric_series(
            simulations_df,
            "final_equity",
        )
    )

    ruined = (
        pd.to_numeric(
            simulations_df[
                "ruined"
            ],
            errors="coerce",
        )
        .fillna(0.0)
        > 0
    )

    summary = {
        "simulations": int(
            len(simulations_df)
        ),
        "probability_profit_pct": (
            probability_pct(
                returns > 0
            )
        ),
        "probability_loss_pct": (
            probability_pct(
                returns < 0
            )
        ),
        "probability_cagr_above_5_pct": (
            probability_pct(
                cagrs >= 5
            )
        ),
        "probability_cagr_above_10_pct": (
            probability_pct(
                cagrs >= 10
            )
        ),
        "probability_drawdown_over_10_pct": (
            probability_pct(
                drawdowns <= -10
            )
        ),
        "probability_drawdown_over_20_pct": (
            probability_pct(
                drawdowns <= -20
            )
        ),
        "probability_drawdown_over_30_pct": (
            probability_pct(
                drawdowns <= -30
            )
        ),
        "risk_of_ruin_pct": (
            probability_pct(
                ruined
            )
        ),
        "mean_return_pct": float(
            returns.mean()
        ),
        "median_return_pct": float(
            returns.median()
        ),
        "return_5th_percentile_pct": (
            float(
                returns.quantile(
                    0.05
                )
            )
        ),
        "return_95th_percentile_pct": (
            float(
                returns.quantile(
                    0.95
                )
            )
        ),
        "mean_cagr_pct": float(
            cagrs.mean()
        ),
        "median_cagr_pct": float(
            cagrs.median()
        ),
        "median_drawdown_pct": float(
            drawdowns.median()
        ),
        "drawdown_5th_percentile_pct": (
            float(
                drawdowns.quantile(
                    0.05
                )
            )
        ),
        "worst_drawdown_pct": float(
            drawdowns.min()
        ),
        "median_sharpe": float(
            sharpes.median()
        ),
        "sharpe_5th_percentile": (
            float(
                sharpes.quantile(
                    0.05
                )
            )
        ),
        "median_profit_factor": (
            float(
                profit_factors.median()
            )
        ),
        "median_final_equity": (
            float(
                final_equities.median()
            )
        ),
        "final_equity_5th_percentile": (
            float(
                final_equities.quantile(
                    0.05
                )
            )
        ),
    }

    return pd.DataFrame(
        [summary]
    )


def build_percentile_table(
    simulations_df: pd.DataFrame,
) -> pd.DataFrame:
    percentiles = [
        0.01,
        0.05,
        0.10,
        0.25,
        0.50,
        0.75,
        0.90,
        0.95,
        0.99,
    ]

    columns = [
        "final_equity",
        "total_return_pct",
        "cagr_pct",
        "max_drawdown_pct",
        "sharpe_ratio",
        "profit_factor",
    ]

    rows: list[
        dict[str, Any]
    ] = []

    for percentile in percentiles:
        row: dict[str, Any] = {
            "percentile": (
                percentile * 100
            )
        }

        for column in columns:
            values = (
                finite_numeric_series(
                    simulations_df,
                    column,
                )
            )

            row[column] = float(
                values.quantile(
                    percentile
                )
            )

        rows.append(row)

    return pd.DataFrame(
        rows
    )


def run_analysis(
    *,
    symbols: list[str],
    start_date: str,
    end_date: str,
    initial_capital: float,
    max_positions: int,
    simulations: int,
    random_seed: int,
    sampling_method: str,
    max_holding_days: int,
    atr_stop_multiplier: float,
    atr_target_multiplier: float,
    stop_loss_pct: float,
    take_profit_pct: float,
    min_adx: float,
    simulation_output_path: str,
    summary_output_path: str,
    percentile_output_path: str,
    trade_output_path: str,
) -> tuple[
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

    position_fraction = (
        position_size_pct
        / 100.0
    )

    entry_model = (
        HybridTrendDonchianEntryModel(
            mode="trend_context",
        )
    )

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

    print(
        "Đang chạy source backtest..."
    )

    (
        trades,
        source_metrics,
        equity_df,
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

    if not trades:
        raise ValueError(
            "Source backtest không "
            "tạo được giao dịch."
        )

    trades_df = pd.DataFrame(
        [
            trade_to_row(
                trade
            )
            for trade in trades
        ]
    )

    trade_returns_pct = (
        pd.to_numeric(
            trades_df[
                "net_return_pct"
            ],
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
        .to_numpy(
            dtype=float
        )
    )

    start_timestamp = (
        pd.to_datetime(
            start_date
        )
    )

    end_timestamp = (
        pd.to_datetime(
            end_date
        )
    )

    years = max(
        (
            end_timestamp
            - start_timestamp
        ).days
        / 365.25,
        1.0 / 365.25,
    )

    print(
        f"Source trades: "
        f"{len(trade_returns_pct)}"
    )

    print(
        "Source Return: "
        f"{source_metrics.get('total_return_pct', 0.0):+.2f}%"
    )

    print(
        "Source Sharpe: "
        f"{source_metrics.get('sharpe_ratio', 0.0):.2f}"
    )

    print(
        "Source Drawdown: "
        f"{source_metrics.get('max_drawdown_pct', 0.0):.2f}%"
    )

    print(
        f"Sampling method: "
        f"{sampling_method}"
    )

    simulations_df = run_monte_carlo(
        trade_returns_pct=(
            trade_returns_pct
        ),
        simulations=simulations,
        initial_capital=(
            initial_capital
        ),
        position_fraction=(
            position_fraction
        ),
        years=years,
        random_seed=random_seed,
        sampling_method=(
            sampling_method
        ),
    )

    simulations_df.insert(
        1,
        "source_trades",
        len(trade_returns_pct),
    )

    simulations_df.insert(
        2,
        "position_fraction",
        position_fraction,
    )

    summary_df = build_summary(
        simulations_df
    )

    summary_df.insert(
        0,
        "entry_model",
        entry_model.name,
    )

    summary_df.insert(
        1,
        "start_date",
        start_date,
    )

    summary_df.insert(
        2,
        "end_date",
        end_date,
    )

    summary_df.insert(
        3,
        "source_total_trades",
        len(trade_returns_pct),
    )

    summary_df.insert(
        4,
        "source_total_return_pct",
        float(
            source_metrics.get(
                "total_return_pct",
                0.0,
            )
        ),
    )

    summary_df.insert(
        5,
        "source_sharpe_ratio",
        float(
            source_metrics.get(
                "sharpe_ratio",
                0.0,
            )
        ),
    )

    summary_df.insert(
        6,
        "source_max_drawdown_pct",
        float(
            source_metrics.get(
                "max_drawdown_pct",
                0.0,
            )
        ),
    )

    summary_df.insert(
        7,
        "sampling_method",
        sampling_method,
    )

    percentile_df = (
        build_percentile_table(
            simulations_df
        )
    )

    save_dataframe(
        simulations_df,
        simulation_output_path,
    )

    save_dataframe(
        summary_df,
        summary_output_path,
    )

    save_dataframe(
        percentile_df,
        percentile_output_path,
    )

    save_dataframe(
        trades_df,
        trade_output_path,
    )

    print_results(
        summary_df=summary_df,
        percentile_df=(
            percentile_df
        ),
    )

    return (
        simulations_df,
        summary_df,
        percentile_df,
        trades_df,
    )


def save_dataframe(
    dataframe: pd.DataFrame,
    output_path: str,
) -> None:
    output = Path(
        output_path
    )

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        dataframe.to_csv(
            output,
            index=False,
            encoding="utf-8-sig",
        )

    except PermissionError as exc:
        raise PermissionError(
            f"Không thể ghi file "
            f"{output}. "
            "Hãy đóng file trong Excel."
        ) from exc

    print(
        f"Đã xuất: {output}"
    )


def print_results(
    *,
    summary_df: pd.DataFrame,
    percentile_df: pd.DataFrame,
) -> None:
    print()
    print("=" * 160)
    print(
        "MONTE CARLO SUMMARY"
    )
    print("=" * 160)

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
    print("=" * 120)
    print(
        "MONTE CARLO PERCENTILES"
    )
    print("=" * 120)

    print(
        percentile_df.to_string(
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
            "Monte Carlo bootstrap "
            "cho Hybrid Trend Context."
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
        "--simulations",
        type=int,
        default=10_000,
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
    )

    parser.add_argument(
        "--sampling-method",
        choices=[
            "bootstrap",
            "shuffle",
        ],
        default="bootstrap",
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
        "--simulation-output",
        default=(
            DEFAULT_SIMULATION_OUTPUT
        ),
    )

    parser.add_argument(
        "--summary-output",
        default=(
            DEFAULT_SUMMARY_OUTPUT
        ),
    )

    parser.add_argument(
        "--percentile-output",
        default=(
            DEFAULT_PERCENTILE_OUTPUT
        ),
    )

    parser.add_argument(
        "--trade-output",
        default=(
            DEFAULT_TRADE_OUTPUT
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

    run_analysis(
        symbols=symbols,
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.capital,
        max_positions=(
            args.max_positions
        ),
        simulations=args.simulations,
        random_seed=args.seed,
        sampling_method=(
            args.sampling_method
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
        simulation_output_path=(
            args.simulation_output
        ),
        summary_output_path=(
            args.summary_output
        ),
        percentile_output_path=(
            args.percentile_output
        ),
        trade_output_path=(
            args.trade_output
        ),
    )


if __name__ == "__main__":
    main()