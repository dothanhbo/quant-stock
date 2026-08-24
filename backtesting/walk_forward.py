from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd


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

    def save(
        self,
        *,
        folds_path: str,
        summary_path: str,
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


def run_walk_forward(
    *,
    config: WalkForwardConfig,
    initial_capital: float,
    run_backtest_fn: Callable[..., tuple],
    backtest_kwargs: dict[str, Any],
) -> WalkForwardResult:
    """Rolling fixed-policy validation; this function does not optimize train parameters."""
    if initial_capital <= 0:
        raise ValueError(
            "initial_capital phải lớn hơn 0."
        )

    folds = build_walk_forward_folds(
        config
    )

    rows: list[dict[str, Any]] = []

    current_capital = float(
        initial_capital
    )

    for fold in folds:
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
                **backtest_kwargs,
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

        test_trades, test_metrics, _ = (
            run_backtest_fn(
                **backtest_kwargs,
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

        rows.append(
            {
                **fold.to_dict(),

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

    return WalkForwardResult(
        folds=folds_df,
        summary=summary,
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
