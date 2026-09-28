from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


DEFAULT_TRADES_INPUT = (
    "research_results/"
    "trade_distribution_trades.csv"
)

DEFAULT_SUMMARY_OUTPUT = (
    "research_results/"
    "risk_of_ruin_summary.csv"
)

DEFAULT_HORIZON_OUTPUT = (
    "research_results/"
    "risk_of_ruin_by_horizon.csv"
)

DEFAULT_SIZING_OUTPUT = (
    "research_results/"
    "risk_of_ruin_by_position_size.csv"
)

DEFAULT_SIMULATION_OUTPUT = (
    "research_results/"
    "risk_of_ruin_simulations.csv"
)


CAPITAL_LEVELS = [
    100_000_000,
    500_000_000,
    1_000_000_000,
]

HORIZON_YEARS = [
    1,
    3,
    5,
]

POSITION_FRACTIONS = [
    0.10,
    0.15,
    0.20,
    0.25,
    0.30,
]

DRAWDOWN_THRESHOLDS = [
    10,
    20,
    30,
    40,
    50,
]

CAPITAL_FLOOR_RATIOS = [
    0.90,
    0.80,
    0.70,
    0.50,
]


def load_trade_returns(
    input_path: str,
) -> pd.DataFrame:
    path = Path(
        input_path
    )

    if not path.exists():
        raise FileNotFoundError(
            f"Không tìm thấy file: {path}"
        )

    dataframe = pd.read_csv(
        path
    )

    required_columns = {
        "net_return_pct",
    }

    missing_columns = (
        required_columns
        - set(
            dataframe.columns
        )
    )

    if missing_columns:
        raise ValueError(
            "File trades thiếu cột: "
            + ", ".join(
                sorted(
                    missing_columns
                )
            )
        )

    dataframe[
        "net_return_pct"
    ] = (
        pd.to_numeric(
            dataframe[
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
    )

    dataframe = (
        dataframe
        .dropna(
            subset=[
                "net_return_pct"
            ]
        )
        .reset_index(
            drop=True
        )
    )

    if dataframe.empty:
        raise ValueError(
            "Không có trade return hợp lệ."
        )

    return dataframe


def infer_backtest_years(
    trades_df: pd.DataFrame,
    start_date: str | None,
    end_date: str | None,
) -> float:
    if (
        start_date is not None
        and end_date is not None
    ):
        start = pd.to_datetime(
            start_date,
            errors="coerce",
        )

        end = pd.to_datetime(
            end_date,
            errors="coerce",
        )

    elif {
        "entry_date",
        "exit_date",
    }.issubset(
        trades_df.columns
    ):
        start = pd.to_datetime(
            trades_df[
                "entry_date"
            ],
            errors="coerce",
        ).min()

        end = pd.to_datetime(
            trades_df[
                "exit_date"
            ],
            errors="coerce",
        ).max()

    else:
        return 1.0

    if (
        pd.isna(start)
        or pd.isna(end)
        or end <= start
    ):
        return 1.0

    return max(
        (
            end - start
        ).days
        / 365.25,
        1.0,
    )


def calculate_kelly_fraction(
    returns_pct: np.ndarray,
) -> dict[str, float]:
    wins = returns_pct[
        returns_pct > 0
    ]

    losses = returns_pct[
        returns_pct < 0
    ]

    win_probability = (
        float(
            wins.size
            / returns_pct.size
        )
        if returns_pct.size > 0
        else 0.0
    )

    loss_probability = (
        1.0
        - win_probability
    )

    average_win = (
        float(
            wins.mean()
        )
        if wins.size > 0
        else 0.0
    )

    average_loss = (
        abs(
            float(
                losses.mean()
            )
        )
        if losses.size > 0
        else 0.0
    )

    payoff_ratio = (
        average_win
        / average_loss
        if average_loss > 0
        else 0.0
    )

    full_kelly = (
        win_probability
        - (
            loss_probability
            / payoff_ratio
        )
        if payoff_ratio > 0
        else 0.0
    )

    full_kelly = max(
        0.0,
        full_kelly,
    )

    return {
        "win_probability": (
            win_probability
        ),
        "average_win_pct": (
            average_win
        ),
        "average_loss_pct": (
            average_loss
        ),
        "payoff_ratio": (
            payoff_ratio
        ),
        "full_kelly_fraction": (
            full_kelly
        ),
        "half_kelly_fraction": (
            full_kelly
            * 0.5
        ),
        "quarter_kelly_fraction": (
            full_kelly
            * 0.25
        ),
    }


def simulate_path(
    *,
    sampled_returns_pct: np.ndarray,
    initial_capital: float,
    position_fraction: float,
) -> dict[str, float]:
    equity = float(
        initial_capital
    )

    peak = equity
    max_drawdown_pct = 0.0
    minimum_equity = equity

    for trade_return_pct in (
        sampled_returns_pct
    ):
        allocated_capital = (
            equity
            * position_fraction
        )

        pnl = (
            allocated_capital
            * float(
                trade_return_pct
            )
            / 100.0
        )

        equity = max(
            0.0,
            equity + pnl,
        )

        peak = max(
            peak,
            equity,
        )

        minimum_equity = min(
            minimum_equity,
            equity,
        )

        if peak > 0:
            drawdown_pct = (
                equity
                / peak
                - 1.0
            ) * 100.0

            max_drawdown_pct = min(
                max_drawdown_pct,
                drawdown_pct,
            )

        if equity <= 0:
            break

    return {
        "final_equity": (
            equity
        ),
        "minimum_equity": (
            minimum_equity
        ),
        "total_return_pct": (
            (
                equity
                / initial_capital
                - 1.0
            )
            * 100.0
            if initial_capital > 0
            else 0.0
        ),
        "max_drawdown_pct": (
            max_drawdown_pct
        ),
        "ruined": float(
            equity <= 0
        ),
    }


def run_simulations(
    *,
    trade_returns_pct: np.ndarray,
    simulations: int,
    trades_per_simulation: int,
    initial_capital: float,
    position_fraction: float,
    seed: int,
) -> pd.DataFrame:
    if simulations <= 0:
        raise ValueError(
            "simulations phải lớn hơn 0."
        )

    if trades_per_simulation <= 0:
        raise ValueError(
            "trades_per_simulation "
            "phải lớn hơn 0."
        )

    generator = (
        np.random.default_rng(
            seed
        )
    )

    rows: list[
        dict[str, Any]
    ] = []

    for simulation in range(
        1,
        simulations + 1,
    ):
        sampled_returns = (
            generator.choice(
                trade_returns_pct,
                size=(
                    trades_per_simulation
                ),
                replace=True,
            )
        )

        result = simulate_path(
            sampled_returns_pct=(
                sampled_returns
            ),
            initial_capital=(
                initial_capital
            ),
            position_fraction=(
                position_fraction
            ),
        )

        rows.append(
            {
                "simulation": (
                    simulation
                ),
                "initial_capital": (
                    initial_capital
                ),
                "position_fraction": (
                    position_fraction
                ),
                "trades_per_simulation": (
                    trades_per_simulation
                ),
                **result,
            }
        )

    return pd.DataFrame(
        rows
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


def build_risk_metrics(
    simulations_df: pd.DataFrame,
    *,
    initial_capital: float,
) -> dict[str, float]:
    returns = pd.to_numeric(
        simulations_df[
            "total_return_pct"
        ],
        errors="coerce",
    ).fillna(
        0.0
    )

    drawdowns = pd.to_numeric(
        simulations_df[
            "max_drawdown_pct"
        ],
        errors="coerce",
    ).fillna(
        0.0
    )

    final_equity = pd.to_numeric(
        simulations_df[
            "final_equity"
        ],
        errors="coerce",
    ).fillna(
        0.0
    )

    minimum_equity = pd.to_numeric(
        simulations_df[
            "minimum_equity"
        ],
        errors="coerce",
    ).fillna(
        0.0
    )

    ruined = (
        pd.to_numeric(
            simulations_df[
                "ruined"
            ],
            errors="coerce",
        )
        .fillna(
            0.0
        )
        > 0
    )

    result: dict[
        str,
        float,
    ] = {
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
        "risk_of_ruin_pct": (
            probability_pct(
                ruined
            )
        ),
        "median_return_pct": float(
            returns.median()
        ),
        "return_5th_percentile_pct": float(
            returns.quantile(
                0.05
            )
        ),
        "median_final_equity": float(
            final_equity.median()
        ),
        "final_equity_5th_percentile": float(
            final_equity.quantile(
                0.05
            )
        ),
        "median_max_drawdown_pct": float(
            drawdowns.median()
        ),
        "drawdown_5th_percentile_pct": float(
            drawdowns.quantile(
                0.05
            )
        ),
        "worst_drawdown_pct": float(
            drawdowns.min()
        ),
    }

    for threshold in (
        DRAWDOWN_THRESHOLDS
    ):
        result[
            f"probability_dd_over_{threshold}_pct"
        ] = probability_pct(
            drawdowns
            <= -float(
                threshold
            )
        )

    for ratio in (
        CAPITAL_FLOOR_RATIOS
    ):
        label = int(
            ratio * 100
        )

        result[
            f"probability_equity_below_{label}_pct"
        ] = probability_pct(
            minimum_equity
            <= (
                initial_capital
                * ratio
            )
        )

    return result


def build_horizon_table(
    *,
    trade_returns_pct: np.ndarray,
    simulations: int,
    annual_trade_rate: float,
    initial_capital: float,
    position_fraction: float,
    seed: int,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
]:
    summary_rows: list[
        dict[str, Any]
    ] = []

    simulation_frames: list[
        pd.DataFrame
    ] = []

    for index, years in enumerate(
        HORIZON_YEARS,
        start=1,
    ):
        trades_per_simulation = max(
            1,
            round(
                annual_trade_rate
                * years
            ),
        )

        simulations_df = (
            run_simulations(
                trade_returns_pct=(
                    trade_returns_pct
                ),
                simulations=(
                    simulations
                ),
                trades_per_simulation=(
                    trades_per_simulation
                ),
                initial_capital=(
                    initial_capital
                ),
                position_fraction=(
                    position_fraction
                ),
                seed=(
                    seed
                    + index
                ),
            )
        )

        simulations_df.insert(
            0,
            "horizon_years",
            years,
        )

        simulation_frames.append(
            simulations_df
        )

        summary_rows.append(
            {
                "horizon_years": (
                    years
                ),
                "annual_trade_rate": (
                    annual_trade_rate
                ),
                "trades_per_simulation": (
                    trades_per_simulation
                ),
                "position_fraction": (
                    position_fraction
                ),
                "initial_capital": (
                    initial_capital
                ),
                **build_risk_metrics(
                    simulations_df,
                    initial_capital=(
                        initial_capital
                    ),
                ),
            }
        )

    return (
        pd.DataFrame(
            summary_rows
        ),
        pd.concat(
            simulation_frames,
            ignore_index=True,
        ),
    )


def build_sizing_table(
    *,
    trade_returns_pct: np.ndarray,
    simulations: int,
    annual_trade_rate: float,
    horizon_years: int,
    initial_capital: float,
    seed: int,
) -> pd.DataFrame:
    rows: list[
        dict[str, Any]
    ] = []

    trades_per_simulation = max(
        1,
        round(
            annual_trade_rate
            * horizon_years
        ),
    )

    for index, position_fraction in (
        enumerate(
            POSITION_FRACTIONS,
            start=1,
        )
    ):
        simulations_df = (
            run_simulations(
                trade_returns_pct=(
                    trade_returns_pct
                ),
                simulations=(
                    simulations
                ),
                trades_per_simulation=(
                    trades_per_simulation
                ),
                initial_capital=(
                    initial_capital
                ),
                position_fraction=(
                    position_fraction
                ),
                seed=(
                    seed
                    + 100
                    + index
                ),
            )
        )

        rows.append(
            {
                "horizon_years": (
                    horizon_years
                ),
                "position_fraction": (
                    position_fraction
                ),
                "position_size_pct": (
                    position_fraction
                    * 100.0
                ),
                "trades_per_simulation": (
                    trades_per_simulation
                ),
                **build_risk_metrics(
                    simulations_df,
                    initial_capital=(
                        initial_capital
                    ),
                ),
            }
        )

    return pd.DataFrame(
        rows
    )


def build_capital_table(
    horizon_df: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[
        dict[str, Any]
    ] = []

    for capital in CAPITAL_LEVELS:
        for _, row in (
            horizon_df.iterrows()
        ):
            ratio = (
                capital
                / float(
                    row[
                        "initial_capital"
                    ]
                )
            )

            rows.append(
                {
                    "initial_capital": (
                        capital
                    ),
                    "horizon_years": int(
                        row[
                            "horizon_years"
                        ]
                    ),
                    "position_fraction": (
                        row[
                            "position_fraction"
                        ]
                    ),
                    "probability_profit_pct": (
                        row[
                            "probability_profit_pct"
                        ]
                    ),
                    "probability_loss_pct": (
                        row[
                            "probability_loss_pct"
                        ]
                    ),
                    "risk_of_ruin_pct": (
                        row[
                            "risk_of_ruin_pct"
                        ]
                    ),
                    "median_final_equity": (
                        row[
                            "median_final_equity"
                        ]
                        * ratio
                    ),
                    "final_equity_5th_percentile": (
                        row[
                            "final_equity_5th_percentile"
                        ]
                        * ratio
                    ),
                    "median_max_drawdown_pct": (
                        row[
                            "median_max_drawdown_pct"
                        ]
                    ),
                    "probability_dd_over_20_pct": (
                        row[
                            "probability_dd_over_20_pct"
                        ]
                    ),
                    "probability_dd_over_30_pct": (
                        row[
                            "probability_dd_over_30_pct"
                        ]
                    ),
                }
            )

    return pd.DataFrame(
        rows
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


def run_analysis(
    args: argparse.Namespace,
) -> None:
    trades_df = load_trade_returns(
        args.trades_input
    )

    trade_returns_pct = (
        trades_df[
            "net_return_pct"
        ]
        .to_numpy(
            dtype=float
        )
    )

    backtest_years = (
        infer_backtest_years(
            trades_df,
            args.start,
            args.end,
        )
    )

    annual_trade_rate = (
        len(
            trade_returns_pct
        )
        / backtest_years
    )

    position_fraction = (
        args.position_size_pct
        / 100.0
    )

    if not (
        0
        < position_fraction
        <= 1
    ):
        raise ValueError(
            "position-size-pct phải "
            "nằm trong khoảng (0, 100]."
        )

    kelly = calculate_kelly_fraction(
        trade_returns_pct
    )

    (
        horizon_df,
        simulations_df,
    ) = build_horizon_table(
        trade_returns_pct=(
            trade_returns_pct
        ),
        simulations=(
            args.simulations
        ),
        annual_trade_rate=(
            annual_trade_rate
        ),
        initial_capital=(
            args.capital
        ),
        position_fraction=(
            position_fraction
        ),
        seed=args.seed,
    )

    sizing_df = build_sizing_table(
        trade_returns_pct=(
            trade_returns_pct
        ),
        simulations=(
            args.simulations
        ),
        annual_trade_rate=(
            annual_trade_rate
        ),
        horizon_years=(
            args.sizing_horizon
        ),
        initial_capital=(
            args.capital
        ),
        seed=args.seed,
    )

    capital_df = build_capital_table(
        horizon_df
    )

    summary_df = pd.DataFrame(
        [
            {
                "source_trades": len(
                    trade_returns_pct
                ),
                "backtest_years": (
                    backtest_years
                ),
                "annual_trade_rate": (
                    annual_trade_rate
                ),
                "simulations": (
                    args.simulations
                ),
                "position_size_pct": (
                    args.position_size_pct
                ),
                **kelly,
                "recommended_quarter_kelly_pct": (
                    min(
                        kelly[
                            "quarter_kelly_fraction"
                        ],
                        1.0,
                    )
                    * 100.0
                ),
                "current_position_vs_full_kelly_pct": (
                    (
                        position_fraction
                        / kelly[
                            "full_kelly_fraction"
                        ]
                        * 100.0
                    )
                    if kelly[
                        "full_kelly_fraction"
                    ] > 0
                    else 0.0
                ),
            }
        ]
    )

    save_dataframe(
        summary_df,
        args.summary_output,
    )

    save_dataframe(
        horizon_df,
        args.horizon_output,
    )

    save_dataframe(
        sizing_df,
        args.sizing_output,
    )

    save_dataframe(
        simulations_df,
        args.simulation_output,
    )

    capital_output = Path(
        args.horizon_output
    ).with_name(
        "risk_of_ruin_by_capital.csv"
    )

    save_dataframe(
        capital_df,
        str(
            capital_output
        ),
    )

    print()
    print("=" * 170)
    print(
        "RISK OF RUIN SUMMARY"
    )
    print("=" * 170)

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
    print("=" * 200)
    print(
        "RISK BY HORIZON"
    )
    print("=" * 200)

    print(
        horizon_df.to_string(
            index=False,
            float_format=(
                lambda value:
                f"{value:.2f}"
            ),
        )
    )

    print()
    print("=" * 200)
    print(
        "RISK BY POSITION SIZE"
    )
    print("=" * 200)

    print(
        sizing_df.to_string(
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
            "Bootstrap Risk of Ruin "
            "từ trade distribution."
        )
    )

    parser.add_argument(
        "--trades-input",
        default=(
            DEFAULT_TRADES_INPUT
        ),
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
        "--position-size-pct",
        type=float,
        default=20.0,
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
        "--sizing-horizon",
        type=int,
        default=5,
    )

    parser.add_argument(
        "--summary-output",
        default=(
            DEFAULT_SUMMARY_OUTPUT
        ),
    )

    parser.add_argument(
        "--horizon-output",
        default=(
            DEFAULT_HORIZON_OUTPUT
        ),
    )

    parser.add_argument(
        "--sizing-output",
        default=(
            DEFAULT_SIZING_OUTPUT
        ),
    )

    parser.add_argument(
        "--simulation-output",
        default=(
            DEFAULT_SIMULATION_OUTPUT
        ),
    )

    return parser.parse_args()


def main() -> None:
    run_analysis(
        parse_args()
    )


if __name__ == "__main__":
    main()
