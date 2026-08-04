from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

import pandas as pd

from backtesting.engine import (
    build_exit_model,
    run_backtest,
)
from backtesting.position_sizers import (
    FixedFractionSizer,
)
from research.benchmark_portfolio_models import (
    build_portfolio_model_registry,
)
from research.universes import TOP10_SYMBOLS
from research.walk_forward import (
    WalkForwardConfig,
    build_walk_forward_windows,
)


DEFAULT_OUTPUT_DIR = Path(
    "research_results/"
    "walk_forward_portfolio_heat"
)

DEFAULT_HEAT_LEVELS: list[
    float | None
] = [
    None,
    3.0,
    4.0,
    5.0,
]


@dataclass(frozen=True)
class HeatWalkForwardConfig:
    start_date: str = "2020-01-01"
    end_date: str = "2026-07-31"

    train_years: int = 3
    test_months: int = 12
    step_months: int = 12
    anchored: bool = False

    initial_capital: float = (
        100_000_000.0
    )
    position_size_pct: float = 20.0

    max_holding_days: int = 30
    min_adx: float = 20.0

    entry_model_name: str = (
        "hybrid_trend_donchian_v1"
        "__trend_context"
    )
    ranking_method: str = (
        "relative_strength"
    )

    atr_stop_multiplier: float = 2.0
    atr_target_multiplier: float = 5.0

    min_trades_per_window: int = 5

    def validate(self) -> None:
        if (
            pd.Timestamp(self.start_date)
            >= pd.Timestamp(self.end_date)
        ):
            raise ValueError(
                "start_date phải nhỏ hơn "
                "end_date."
            )

        if self.train_years < 1:
            raise ValueError(
                "train_years phải từ 1."
            )

        if self.test_months < 1:
            raise ValueError(
                "test_months phải từ 1."
            )

        if self.step_months < 1:
            raise ValueError(
                "step_months phải từ 1."
            )

        if self.initial_capital <= 0:
            raise ValueError(
                "initial_capital phải lớn hơn 0."
            )

        if not (
            0
            < self.position_size_pct
            <= 100
        ):
            raise ValueError(
                "position_size_pct không hợp lệ."
            )

        if self.min_trades_per_window < 0:
            raise ValueError(
                "min_trades_per_window "
                "không được âm."
            )


def heat_label(
    heat_pct: float | None,
) -> str:
    if heat_pct is None:
        return "unlimited"

    return (
        "heat_"
        + str(heat_pct)
        .replace(".", "_")
    )


def safe_float(
    value: Any,
    default: float = 0.0,
) -> float:
    try:
        result = float(value)

    except (
        TypeError,
        ValueError,
    ):
        return default

    if pd.isna(result):
        return default

    return result


def parse_heat_levels(
    value: str,
) -> list[float | None]:
    levels: list[
        float | None
    ] = []

    for raw_item in value.split(","):
        item = (
            raw_item
            .strip()
            .lower()
        )

        if not item:
            continue

        if item in {
            "none",
            "off",
            "unlimited",
        }:
            level = None
        else:
            level = float(item)

            if level <= 0:
                raise ValueError(
                    "Heat level phải lớn hơn 0."
                )

        if level not in levels:
            levels.append(level)

    if not levels:
        raise ValueError(
            "Không có heat level hợp lệ."
        )

    return levels


def summarize_by_heat(
    window_df: pd.DataFrame,
) -> pd.DataFrame:
    if window_df.empty:
        return pd.DataFrame()

    rows: list[
        dict[str, Any]
    ] = []

    for (
        label,
        group,
    ) in window_df.groupby(
        "heat_label",
        sort=False,
    ):
        valid = group[
            group["enough_trades"]
        ].copy()

        source = (
            valid
            if not valid.empty
            else group
        )

        windows_used = len(source)

        positive_windows = int(
            (
                source[
                    "total_return_pct"
                ]
                > 0
            ).sum()
        )

        positive_rate = (
            positive_windows
            / windows_used
            * 100
            if windows_used
            else 0.0
        )

        median_sharpe = safe_float(
            source[
                "sharpe_ratio"
            ].median()
        )

        robust = bool(
            windows_used >= 3
            and positive_rate >= 60.0
            and median_sharpe > 0
        )

        rows.append(
            {
                "heat_label": label,
                "max_portfolio_heat_pct": (
                    group[
                        "max_portfolio_heat_pct"
                    ].iloc[0]
                ),
                "windows_total": int(
                    len(group)
                ),
                "windows_used": int(
                    windows_used
                ),
                "positive_windows": (
                    positive_windows
                ),
                "positive_window_rate_pct": (
                    positive_rate
                ),
                "total_oos_trades": int(
                    group[
                        "total_trades"
                    ].sum()
                ),
                "total_heat_rejections": int(
                    group[
                        "rejected_by_heat"
                    ].sum()
                ),
                "mean_oos_return_pct": (
                    safe_float(
                        source[
                            "total_return_pct"
                        ].mean()
                    )
                ),
                "median_oos_return_pct": (
                    safe_float(
                        source[
                            "total_return_pct"
                        ].median()
                    )
                ),
                "worst_oos_return_pct": (
                    safe_float(
                        source[
                            "total_return_pct"
                        ].min()
                    )
                ),
                "best_oos_return_pct": (
                    safe_float(
                        source[
                            "total_return_pct"
                        ].max()
                    )
                ),
                "mean_sharpe_ratio": (
                    safe_float(
                        source[
                            "sharpe_ratio"
                        ].mean()
                    )
                ),
                "median_sharpe_ratio": (
                    median_sharpe
                ),
                "mean_max_drawdown_pct": (
                    safe_float(
                        source[
                            "max_drawdown_pct"
                        ].mean()
                    )
                ),
                "worst_max_drawdown_pct": (
                    safe_float(
                        source[
                            "max_drawdown_pct"
                        ].min()
                    )
                ),
                "mean_profit_factor": (
                    safe_float(
                        source[
                            "profit_factor"
                        ].mean()
                    )
                ),
                "mean_peak_heat_pct": (
                    safe_float(
                        source[
                            "peak_portfolio_heat_pct"
                        ].mean()
                    )
                ),
                "robust": robust,
            }
        )

    summary_df = pd.DataFrame(
        rows
    )

    summary_df = (
        summary_df
        .sort_values(
            by=[
                "robust",
                "median_sharpe_ratio",
                "mean_oos_return_pct",
                "worst_oos_return_pct",
                "mean_max_drawdown_pct",
            ],
            ascending=[
                False,
                False,
                False,
                False,
                False,
            ],
        )
        .reset_index(
            drop=True
        )
    )

    summary_df.insert(
        0,
        "rank",
        range(
            1,
            len(summary_df) + 1,
        ),
    )

    return summary_df


def run_heat_walk_forward(
    *,
    config: HeatWalkForwardConfig,
    heat_levels: list[
        float | None
    ],
    symbols: list[str] | None = None,
    output_dir: Path = (
        DEFAULT_OUTPUT_DIR
    ),
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
]:
    config.validate()

    selected_symbols = (
        list(TOP10_SYMBOLS)
        if symbols is None
        else sorted(
            {
                symbol
                .upper()
                .strip()
                for symbol
                in symbols
                if symbol.strip()
            }
        )
    )

    if not selected_symbols:
        raise ValueError(
            "Không có symbol hợp lệ."
        )

    registry = (
        build_portfolio_model_registry()
    )

    if (
        config.entry_model_name
        not in registry
    ):
        raise ValueError(
            "Entry model không tồn tại: "
            f"{config.entry_model_name}"
        )

    entry_model = registry[
        config.entry_model_name
    ]

    windows = (
        build_walk_forward_windows(
            WalkForwardConfig(
                start_date=(
                    config.start_date
                ),
                end_date=(
                    config.end_date
                ),
                train_years=(
                    config.train_years
                ),
                test_months=(
                    config.test_months
                ),
                step_months=(
                    config.step_months
                ),
                anchored=(
                    config.anchored
                ),
            )
        )
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    rows: list[
        dict[str, Any]
    ] = []

    print("=" * 110)
    print(
        "PORTFOLIO HEAT "
        "WALK-FORWARD OOS"
    )
    print("=" * 110)
    print(
        f"Symbols      : "
        f"{len(selected_symbols)}"
    )
    print(
        f"Windows      : "
        f"{len(windows)}"
    )
    print(
        f"Heat levels  : "
        f"{len(heat_levels)}"
    )
    print(
        f"Entry        : "
        f"{config.entry_model_name}"
    )
    print(
        f"Exit         : ATR "
        f"{config.atr_stop_multiplier:g}/"
        f"{config.atr_target_multiplier:g}"
    )
    print(
        f"Ranking      : "
        f"{config.ranking_method}"
    )
    print("=" * 110)

    for heat_level in heat_levels:
        label = heat_label(
            heat_level
        )

        print()
        print("#" * 110)
        print(
            f"HEAT CONFIG: {label}"
        )
        print("#" * 110)

        for window in windows:
            started_at = perf_counter()

            exit_model = build_exit_model(
                name="atr",
                stop_atr_multiplier=(
                    config
                    .atr_stop_multiplier
                ),
                target_atr_multiplier=(
                    config
                    .atr_target_multiplier
                ),
                break_even_trigger=5.0,
                trailing_atr_multiplier=2.0,
            )

            position_sizer = (
                FixedFractionSizer(
                    position_size_pct=(
                        config
                        .position_size_pct
                    )
                )
            )

            (
                trades,
                metrics,
                equity_df,
            ) = run_backtest(
                symbols=selected_symbols,
                start_date=(
                    window.test_start
                ),
                end_date=(
                    window.test_end
                ),
                initial_capital=(
                    config
                    .initial_capital
                ),
                position_size_pct=(
                    config
                    .position_size_pct
                ),
                max_holding_days=(
                    config
                    .max_holding_days
                ),
                min_adx=config.min_adx,
                entry_model=entry_model,
                exit_model=exit_model,
                ranking_method=(
                    config.ranking_method
                ),
                position_sizer=(
                    position_sizer
                ),
                max_portfolio_heat_pct=(
                    heat_level
                ),
                verbose=False,
            )

            elapsed = (
                perf_counter()
                - started_at
            )

            rejected_reasons = (
                metrics.get(
                    "rejected_trade_reasons",
                    {},
                )
                or {}
            )

            heat_series = pd.Series(
                dtype=float
            )

            if (
                not equity_df.empty
                and "portfolio_heat_pct"
                in equity_df.columns
            ):
                heat_series = (
                    pd.to_numeric(
                        equity_df[
                            "portfolio_heat_pct"
                        ],
                        errors="coerce",
                    )
                    .dropna()
                )

            total_trades = int(
                metrics.get(
                    "total_trades",
                    len(trades),
                )
            )

            row = {
                "heat_label": label,
                "max_portfolio_heat_pct": (
                    heat_level
                ),
                "window_id": (
                    window.window_id
                ),
                "train_start": (
                    window.train_start
                ),
                "train_end": (
                    window.train_end
                ),
                "test_start": (
                    window.test_start
                ),
                "test_end": (
                    window.test_end
                ),
                "total_trades": (
                    total_trades
                ),
                "enough_trades": (
                    total_trades
                    >= config
                    .min_trades_per_window
                ),
                "rejected_trades": int(
                    metrics.get(
                        "rejected_trades",
                        0,
                    )
                ),
                "rejected_by_heat": int(
                    rejected_reasons.get(
                        "portfolio_heat_limit",
                        0,
                    )
                ),
                "missing_stop_rejections": int(
                    rejected_reasons.get(
                        "missing_stop_price",
                        0,
                    )
                ),
                "total_return_pct": (
                    safe_float(
                        metrics.get(
                            "total_return_pct"
                        )
                    )
                ),
                "cagr_pct": (
                    safe_float(
                        metrics.get(
                            "cagr_pct"
                        )
                    )
                ),
                "sharpe_ratio": (
                    safe_float(
                        metrics.get(
                            "sharpe_ratio"
                        )
                    )
                ),
                "sortino_ratio": (
                    safe_float(
                        metrics.get(
                            "sortino_ratio"
                        )
                    )
                ),
                "max_drawdown_pct": (
                    safe_float(
                        metrics.get(
                            "max_drawdown_pct"
                        )
                    )
                ),
                "profit_factor": (
                    safe_float(
                        metrics.get(
                            "profit_factor"
                        )
                    )
                ),
                "win_rate_pct": (
                    safe_float(
                        metrics.get(
                            "win_rate_pct"
                        )
                    )
                ),
                "average_portfolio_heat_pct": (
                    safe_float(
                        heat_series.mean()
                    )
                ),
                "peak_portfolio_heat_pct": (
                    safe_float(
                        heat_series.max()
                    )
                ),
                "elapsed_seconds": round(
                    elapsed,
                    2,
                ),
            }

            rows.append(row)

            print(
                f"[{window.window_id:02d}/"
                f"{len(windows):02d}] "
                f"OOS {window.test_start} "
                f"-> {window.test_end} | "
                f"Trades={total_trades} | "
                f"Reject={row['rejected_by_heat']} | "
                f"Return="
                f"{row['total_return_pct']:+.2f}% | "
                f"Sharpe="
                f"{row['sharpe_ratio']:.2f} | "
                f"MDD="
                f"{row['max_drawdown_pct']:.2f}% | "
                f"{elapsed:.1f}s"
            )

    window_df = pd.DataFrame(
        rows
    )

    summary_df = summarize_by_heat(
        window_df
    )

    window_path = (
        output_dir
        / "windows.csv"
    )

    summary_path = (
        output_dir
        / "summary.csv"
    )

    config_path = (
        output_dir
        / "config.json"
    )

    window_df.to_csv(
        window_path,
        index=False,
        encoding="utf-8-sig",
    )

    summary_df.to_csv(
        summary_path,
        index=False,
        encoding="utf-8-sig",
    )

    config_path.write_text(
        json.dumps(
            {
                **asdict(config),
                "symbols": (
                    selected_symbols
                ),
                "heat_levels": (
                    heat_levels
                ),
                "note": (
                    "Train windows remain metadata "
                    "only; this compares fixed heat "
                    "configurations on OOS windows."
                ),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print()
    print(
        f"Đã xuất: {window_path}"
    )
    print(
        f"Đã xuất: {summary_path}"
    )
    print(
        f"Đã xuất: {config_path}"
    )

    print()
    print("=" * 200)
    print(
        "PORTFOLIO HEAT "
        "WALK-FORWARD SUMMARY"
    )
    print("=" * 200)

    print(
        summary_df.to_string(
            index=False,
            float_format=(
                lambda value:
                f"{value:.2f}"
            ),
        )
    )

    return (
        window_df,
        summary_df,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rolling OOS comparison of "
            "portfolio heat limits."
        )
    )

    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
    )

    parser.add_argument(
        "--heat-levels",
        type=parse_heat_levels,
        default=(
            DEFAULT_HEAT_LEVELS
        ),
        help=(
            "Ví dụ unlimited,3,4,5"
        ),
    )

    parser.add_argument(
        "--start",
        default="2020-01-01",
    )

    parser.add_argument(
        "--end",
        default="2026-07-31",
    )

    parser.add_argument(
        "--train-years",
        type=int,
        default=3,
    )

    parser.add_argument(
        "--test-months",
        type=int,
        default=12,
    )

    parser.add_argument(
        "--step-months",
        type=int,
        default=12,
    )

    parser.add_argument(
        "--anchored",
        action="store_true",
    )

    parser.add_argument(
        "--capital",
        type=float,
        default=100_000_000,
    )

    parser.add_argument(
        "--position-size",
        type=float,
        default=20.0,
    )

    parser.add_argument(
        "--hold",
        type=int,
        default=30,
    )

    parser.add_argument(
        "--min-adx",
        type=float,
        default=20.0,
    )

    parser.add_argument(
        "--min-trades",
        type=int,
        default=5,
    )

    parser.add_argument(
        "--ranking",
        default="relative_strength",
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
        "--output",
        default=str(
            DEFAULT_OUTPUT_DIR
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    config = HeatWalkForwardConfig(
        start_date=args.start,
        end_date=args.end,
        train_years=(
            args.train_years
        ),
        test_months=(
            args.test_months
        ),
        step_months=(
            args.step_months
        ),
        anchored=args.anchored,
        initial_capital=(
            args.capital
        ),
        position_size_pct=(
            args.position_size
        ),
        max_holding_days=(
            args.hold
        ),
        min_adx=args.min_adx,
        ranking_method=(
            args.ranking
        ),
        atr_stop_multiplier=(
            args.atr_stop_multiplier
        ),
        atr_target_multiplier=(
            args.atr_target_multiplier
        ),
        min_trades_per_window=(
            args.min_trades
        ),
    )

    run_heat_walk_forward(
        config=config,
        heat_levels=(
            args.heat_levels
        ),
        symbols=args.symbols,
        output_dir=Path(
            args.output
        ),
    )


if __name__ == "__main__":
    main()
