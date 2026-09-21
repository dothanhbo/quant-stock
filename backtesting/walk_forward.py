from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Literal

import pandas as pd

from core.database_coverage import (
    CoverageUniverseIndex,
    build_database_coverage_index,
)


@dataclass(slots=True, frozen=True)
class WalkForwardConfig:
    start_date: str
    end_date: str
    train_months: int = 24
    test_months: int = 6
    step_months: int = 6

    def validate(self) -> None:
        start = pd.Timestamp(self.start_date)
        end = pd.Timestamp(self.end_date)

        if start >= end:
            raise ValueError(
                "start_date phải nhỏ hơn end_date."
            )

        if self.train_months < 1:
            raise ValueError(
                "train_months phải từ 1 trở lên."
            )

        if self.test_months < 1:
            raise ValueError(
                "test_months phải từ 1 trở lên."
            )

        if self.step_months < 1:
            raise ValueError(
                "step_months phải từ 1 trở lên."
            )


@dataclass(slots=True, frozen=True)
class WalkForwardFold:
    fold: int

    train_start: pd.Timestamp
    train_end: pd.Timestamp

    test_start: pd.Timestamp
    test_end: pd.Timestamp

    def to_dict(self) -> dict[str, Any]:
        return {
            "fold": self.fold,
            "train_start": self.train_start.date(),
            "train_end": self.train_end.date(),
            "test_start": self.test_start.date(),
            "test_end": self.test_end.date(),
        }


@dataclass(slots=True, frozen=True)
class WalkForwardResult:
    folds: pd.DataFrame
    summary: dict[str, Any]
    test_trades: pd.DataFrame

    def save(
        self,
        *,
        folds_path: str,
        summary_path: str,
        test_trades_path: str | None = None,
    ) -> None:
        self.folds.to_csv(
            folds_path,
            index=False,
            encoding="utf-8-sig",
        )

        pd.DataFrame(
            [self.summary]
        ).to_csv(
            summary_path,
            index=False,
            encoding="utf-8-sig",
        )

        if test_trades_path is not None:
            self.test_trades.to_csv(
                test_trades_path,
                index=False,
                encoding="utf-8-sig",
            )


def _safe_float(
    value: Any,
    default: float = 0.0,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default

    if not math.isfinite(result):
        return default

    return result


def calculate_chained_drawdown_pct(
    equity_curves: list[pd.DataFrame],
) -> float:
    """Calculate drawdown across the complete chained OOS equity path."""
    values: list[pd.Series] = []
    for curve in equity_curves:
        if curve is None or curve.empty or "equity" not in curve.columns:
            continue
        equity = pd.to_numeric(
            curve["equity"],
            errors="coerce",
        ).dropna()
        if not equity.empty:
            values.append(equity)
    if not values:
        return 0.0

    chained = pd.concat(values, ignore_index=True)
    chained = chained[chained > 0]
    if chained.empty:
        return 0.0
    running_peak = chained.cummax()
    return float(((chained / running_peak) - 1.0).min() * 100.0)


def build_walk_forward_folds(
    config: WalkForwardConfig,
) -> list[WalkForwardFold]:
    config.validate()

    global_start = pd.Timestamp(
        config.start_date
    ).normalize()

    global_end = pd.Timestamp(
        config.end_date
    ).normalize()

    folds: list[WalkForwardFold] = []

    train_start = global_start
    fold_number = 1

    while True:
        train_end = (
            train_start
            + pd.DateOffset(
                months=config.train_months
            )
            - pd.Timedelta(days=1)
        )

        test_start = (
            train_end
            + pd.Timedelta(days=1)
        )

        test_end = (
            test_start
            + pd.DateOffset(
                months=config.test_months
            )
            - pd.Timedelta(days=1)
        )

        if test_start > global_end:
            break

        test_end = min(
            test_end,
            global_end,
        )

        # Không tạo fold test quá ngắn.
        minimum_test_end = (
            test_start
            + pd.DateOffset(
                months=1
            )
            - pd.Timedelta(days=1)
        )

        if test_end < minimum_test_end:
            break

        folds.append(
            WalkForwardFold(
                fold=fold_number,
                train_start=train_start,
                train_end=min(
                    train_end,
                    global_end,
                ),
                test_start=test_start,
                test_end=test_end,
            )
        )

        fold_number += 1

        train_start = (
            train_start
            + pd.DateOffset(
                months=config.step_months
            )
        )

    if not folds:
        raise ValueError(
            "Không tạo được walk-forward fold. "
            "Hãy kiểm tra thời gian train/test."
        )

    return folds


def _validate_coverage_index(
    coverage_index: CoverageUniverseIndex,
    *,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    minimum_history_sessions: int,
    maximum_staleness_sessions: int,
) -> None:
    requested_start_date = start_date.date().isoformat()
    requested_end_date = end_date.date().isoformat()
    if (
        coverage_index.start_date > requested_start_date
        or coverage_index.end_date < requested_end_date
    ):
        raise ValueError("coverage_index does not cover every walk-forward fold")
    if coverage_index.minimum_history_sessions != minimum_history_sessions:
        raise ValueError("coverage_index minimum_history_sessions does not match config")
    if coverage_index.maximum_staleness_sessions != maximum_staleness_sessions:
        raise ValueError("coverage_index maximum_staleness_sessions does not match config")


def _eligible_count_summary(
    coverage_index: CoverageUniverseIndex,
    *,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
) -> tuple[int, int, float]:
    start = start_date.date().isoformat()
    end = end_date.date().isoformat()
    counts = tuple(
        coverage_index.eligible_count_by_session[session_date]
        for session_date in coverage_index.session_dates
        if start <= session_date <= end
    )
    if not counts:
        return 0, 0, 0.0
    return min(counts), max(counts), float(sum(counts) / len(counts))


def run_walk_forward(
    *,
    config: WalkForwardConfig,
    initial_capital: float,
    run_backtest_fn: Callable[..., tuple],
    backtest_kwargs: dict[str, Any],
    universe_mode: Literal["current_vn100", "database_coverage"] = "current_vn100",
    minimum_history_sessions: int = 50,
    maximum_staleness_sessions: int = 5,
    coverage_index: CoverageUniverseIndex | None = None,
) -> WalkForwardResult:
    """Rolling fixed-policy validation; this function does not optimize train parameters."""
    if initial_capital <= 0:
        raise ValueError(
            "initial_capital phải lớn hơn 0."
        )

    folds = build_walk_forward_folds(
        config
    )

    if universe_mode not in {"current_vn100", "database_coverage"}:
        raise ValueError("universe_mode is not supported")
    if minimum_history_sessions < 0:
        raise ValueError("minimum_history_sessions must be non-negative")
    if maximum_staleness_sessions < 0:
        raise ValueError("maximum_staleness_sessions must be non-negative")

    explicit_symbols_supplied = backtest_kwargs.get("symbols") is not None
    effective_universe_mode = (
        "explicit_symbols"
        if explicit_symbols_supplied
        else universe_mode
    )
    shared_coverage_index: CoverageUniverseIndex | None = None
    coverage_backtest_kwargs: dict[str, Any] = {}

    if universe_mode == "database_coverage":
        coverage_backtest_kwargs = {
            "universe_mode": universe_mode,
            "minimum_history_sessions": minimum_history_sessions,
            "maximum_staleness_sessions": maximum_staleness_sessions,
        }

    if universe_mode == "database_coverage" and not explicit_symbols_supplied:
        overall_start = min(fold.train_start for fold in folds)
        overall_end = max(fold.test_end for fold in folds)
        if coverage_index is None:
            shared_coverage_index = build_database_coverage_index(
                overall_start.date().isoformat(),
                overall_end.date().isoformat(),
                minimum_history_sessions=minimum_history_sessions,
                maximum_staleness_sessions=maximum_staleness_sessions,
                database_path=backtest_kwargs.get("db_path"),
            )
        else:
            _validate_coverage_index(
                coverage_index,
                start_date=overall_start,
                end_date=overall_end,
                minimum_history_sessions=minimum_history_sessions,
                maximum_staleness_sessions=maximum_staleness_sessions,
            )
            shared_coverage_index = coverage_index

        coverage_backtest_kwargs["coverage_index"] = shared_coverage_index

    call_backtest_kwargs = {
        **backtest_kwargs,
        **coverage_backtest_kwargs,
    }

    rows: list[dict[str, Any]] = []
    test_equity_curves: list[pd.DataFrame] = []
    all_test_trades: list[dict[str, Any]] = []

    current_capital = float(
        initial_capital
    )

    for fold_number, fold in enumerate(folds, start=1):
        print()
        print("=" * 90)
        print(
            f"WALK-FORWARD FOLD {fold.fold}"
        )
        print("=" * 90)
        print(
            f"Train: "
            f"{fold.train_start.date()} "
            f"-> {fold.train_end.date()}"
        )
        print(
            f"Test : "
            f"{fold.test_start.date()} "
            f"-> {fold.test_end.date()}"
        )

        # V1: chạy train để đo in-sample.
        _, train_metrics, _ = (
            run_backtest_fn(
                **call_backtest_kwargs,
                start_date=str(
                    fold.train_start.date()
                ),
                end_date=str(
                    fold.train_end.date()
                ),
                initial_capital=(
                    initial_capital
                ),
                verbose=False,
            )
        )

        fold_initial_capital = (
            current_capital
        )

        test_trades, test_metrics, test_equity = (
            run_backtest_fn(
                **call_backtest_kwargs,
                start_date=str(
                    fold.test_start.date()
                ),
                end_date=str(
                    fold.test_end.date()
                ),
                initial_capital=(
                    fold_initial_capital
                ),
                verbose=False,
            )
        )

        for trade in test_trades:
            trade_row = trade.to_dict()
            trade_row["fold"] = fold_number
            all_test_trades.append(trade_row)

        if isinstance(test_equity, pd.DataFrame):
            test_equity_curves.append(test_equity.copy())

        test_final_equity = _safe_float(
            test_metrics.get(
                "final_equity"
            ),
            default=fold_initial_capital,
        )

        current_capital = (
            test_final_equity
        )

        train_return = _safe_float(
            train_metrics.get(
                "total_return_pct"
            )
        )

        test_return = _safe_float(
            test_metrics.get(
                "total_return_pct"
            )
        )

        train_sharpe = _safe_float(
            train_metrics.get(
                "sharpe_ratio"
            )
        )

        test_sharpe = _safe_float(
            test_metrics.get(
                "sharpe_ratio"
            )
        )

        if shared_coverage_index is None:
            train_eligible_counts = (
                train_metrics.get("eligible_symbol_count_min"),
                train_metrics.get("eligible_symbol_count_max"),
                train_metrics.get("eligible_symbol_count_mean"),
            )
            test_eligible_counts = (
                test_metrics.get("eligible_symbol_count_min"),
                test_metrics.get("eligible_symbol_count_max"),
                test_metrics.get("eligible_symbol_count_mean"),
            )
        else:
            train_eligible_counts = _eligible_count_summary(
                shared_coverage_index,
                start_date=fold.train_start,
                end_date=fold.train_end,
            )
            test_eligible_counts = _eligible_count_summary(
                shared_coverage_index,
                start_date=fold.test_start,
                end_date=fold.test_end,
            )

        rows.append(
            {
                **fold.to_dict(),

                "requested_universe_mode": universe_mode,
                "effective_universe_mode": (
                    train_metrics.get(
                        "effective_universe_mode",
                        effective_universe_mode,
                    )
                ),
                "minimum_history_sessions": minimum_history_sessions,
                "maximum_staleness_sessions": maximum_staleness_sessions,
                "train_eligible_symbol_count_min": train_eligible_counts[0],
                "train_eligible_symbol_count_max": train_eligible_counts[1],
                "train_eligible_symbol_count_mean": train_eligible_counts[2],
                "test_eligible_symbol_count_min": test_eligible_counts[0],
                "test_eligible_symbol_count_max": test_eligible_counts[1],
                "test_eligible_symbol_count_mean": test_eligible_counts[2],

                "train_trades": int(
                    train_metrics.get(
                        "total_trades",
                        0,
                    )
                ),
                "train_return_pct": (
                    train_return
                ),
                "train_sharpe_ratio": (
                    train_sharpe
                ),
                "train_max_drawdown_pct": (
                    _safe_float(
                        train_metrics.get(
                            "max_drawdown_pct"
                        )
                    )
                ),
                "train_profit_factor": (
                    _safe_float(
                        train_metrics.get(
                            "profit_factor"
                        )
                    )
                ),

                "test_initial_capital": (
                    fold_initial_capital
                ),
                "test_final_equity": (
                    test_final_equity
                ),
                "test_trades": len(
                    test_trades
                ),
                "test_return_pct": (
                    test_return
                ),
                "test_sharpe_ratio": (
                    test_sharpe
                ),
                "test_max_drawdown_pct": (
                    _safe_float(
                        test_metrics.get(
                            "max_drawdown_pct"
                        )
                    )
                ),
                "test_profit_factor": (
                    _safe_float(
                        test_metrics.get(
                            "profit_factor"
                        )
                    )
                ),
                "test_win_rate_pct": (
                    _safe_float(
                        test_metrics.get(
                            "win_rate_pct"
                        )
                    )
                ),

                "return_degradation_pct": (
                    test_return
                    - train_return
                ),
                "sharpe_degradation": (
                    test_sharpe
                    - train_sharpe
                ),
                "test_profitable": (
                    test_return > 0
                ),
            }
        )

        print(
            f"Train return : "
            f"{train_return:+.2f}%"
        )
        print(
            f"Test return  : "
            f"{test_return:+.2f}%"
        )
        print(
            f"Test trades  : "
            f"{len(test_trades)}"
        )
        print(
            f"Test equity  : "
            f"{test_final_equity:,.0f}"
        )

    folds_df = pd.DataFrame(
        rows
    )

    profitable_folds = int(
        folds_df["test_profitable"].sum()
    )

    total_folds = len(
        folds_df
    )

    summary: dict[str, Any] = {
        "optimization_performed": False,
        "folds": total_folds,
        "train_months": (
            config.train_months
        ),
        "test_months": (
            config.test_months
        ),
        "step_months": (
            config.step_months
        ),
        "initial_capital": float(
            initial_capital
        ),
        "final_equity": float(
            current_capital
        ),
        "walk_forward_return_pct": float(
            (
                current_capital
                / initial_capital
                - 1
            )
            * 100
        ),
        "profitable_folds": (
            profitable_folds
        ),
        "losing_folds": (
            total_folds
            - profitable_folds
        ),
        "profitable_fold_pct": float(
            profitable_folds
            / total_folds
            * 100
        ),
        "total_test_trades": int(
            folds_df[
                "test_trades"
            ].sum()
        ),
        "average_test_return_pct": float(
            folds_df[
                "test_return_pct"
            ].mean()
        ),
        "median_test_return_pct": float(
            folds_df[
                "test_return_pct"
            ].median()
        ),
        "worst_test_return_pct": float(
            folds_df[
                "test_return_pct"
            ].min()
        ),
        "best_test_return_pct": float(
            folds_df[
                "test_return_pct"
            ].max()
        ),
        "average_test_sharpe": float(
            folds_df[
                "test_sharpe_ratio"
            ].mean()
        ),
        "average_test_drawdown_pct": float(
            folds_df[
                "test_max_drawdown_pct"
            ].mean()
        ),
        "worst_test_drawdown_pct": float(
            folds_df[
                "test_max_drawdown_pct"
            ].min()
        ),
        "chained_max_drawdown_pct": (
            calculate_chained_drawdown_pct(test_equity_curves)
        ),
        "average_return_degradation_pct": float(
            folds_df[
                "return_degradation_pct"
            ].mean()
        ),
        "average_sharpe_degradation": float(
            folds_df[
                "sharpe_degradation"
            ].mean()
        ),
    }

    test_trades_df = pd.DataFrame(all_test_trades)

    return WalkForwardResult(
        folds=folds_df,
        summary=summary,
        test_trades=test_trades_df,
    )


def print_walk_forward_report(
    result: WalkForwardResult,
) -> None:
    summary = result.summary

    print()
    print("=" * 80)
    print("WALK-FORWARD ANALYSIS")
    print("=" * 80)

    print(
        f"Folds               : "
        f"{summary['folds']}"
    )
    print(
        f"Train/Test          : "
        f"{summary['train_months']}m / "
        f"{summary['test_months']}m"
    )
    print(
        f"Initial Capital     : "
        f"{summary['initial_capital']:,.0f}"
    )
    print(
        f"Final Equity        : "
        f"{summary['final_equity']:,.0f}"
    )
    print(
        f"Walk-Forward Return : "
        f"{summary['walk_forward_return_pct']:+.2f}%"
    )

    print()
    print("OUT-OF-SAMPLE STABILITY")
    print("-" * 80)

    print(
        f"Profitable Folds    : "
        f"{summary['profitable_folds']} / "
        f"{summary['folds']} "
        f"({summary['profitable_fold_pct']:.2f}%)"
    )
    print(
        f"Total Test Trades   : "
        f"{summary['total_test_trades']}"
    )
    print(
        f"Average Test Return : "
        f"{summary['average_test_return_pct']:+.2f}%"
    )
    print(
        f"Median Test Return  : "
        f"{summary['median_test_return_pct']:+.2f}%"
    )
    print(
        f"Worst Test Return   : "
        f"{summary['worst_test_return_pct']:+.2f}%"
    )
    print(
        f"Best Test Return    : "
        f"{summary['best_test_return_pct']:+.2f}%"
    )
    print(
        f"Average Test Sharpe : "
        f"{summary['average_test_sharpe']:.2f}"
    )
    print(
        f"Worst Test Drawdown : "
        f"{summary['worst_test_drawdown_pct']:.2f}%"
    )

    print()
    print("DEGRADATION")
    print("-" * 80)

    print(
        f"Average Return Gap  : "
        f"{summary['average_return_degradation_pct']:+.2f}%"
    )
    print(
        f"Average Sharpe Gap  : "
        f"{summary['average_sharpe_degradation']:+.2f}"
    )

    print("=" * 80)
