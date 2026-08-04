from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Any, Callable, Iterable

import pandas as pd

from backtesting.walk_forward import (
    WalkForwardConfig,
    WalkForwardFold,
    build_walk_forward_folds,
)


@dataclass(slots=True, frozen=True)
class WalkForwardParameterGrid:
    atr_stop_multipliers: tuple[float, ...] = (
        1.5,
        2.0,
        2.5,
    )
    atr_target_multipliers: tuple[float, ...] = (
        3.0,
        4.0,
        5.0,
    )
    holding_days: tuple[int, ...] = (
        20,
        30,
        40,
    )
    min_adx_values: tuple[float, ...] = (
        15.0,
        20.0,
        25.0,
    )

    def validate(self) -> None:
        if not self.atr_stop_multipliers:
            raise ValueError(
                "atr_stop_multipliers không được rỗng."
            )

        if not self.atr_target_multipliers:
            raise ValueError(
                "atr_target_multipliers không được rỗng."
            )

        if not self.holding_days:
            raise ValueError(
                "holding_days không được rỗng."
            )

        if not self.min_adx_values:
            raise ValueError(
                "min_adx_values không được rỗng."
            )

        if any(
            value <= 0
            for value in self.atr_stop_multipliers
        ):
            raise ValueError(
                "ATR stop multiplier phải lớn hơn 0."
            )

        if any(
            value <= 0
            for value in self.atr_target_multipliers
        ):
            raise ValueError(
                "ATR target multiplier phải lớn hơn 0."
            )

        if any(
            value < 1
            for value in self.holding_days
        ):
            raise ValueError(
                "holding_days phải từ 1 trở lên."
            )

        if any(
            value < 0
            for value in self.min_adx_values
        ):
            raise ValueError(
                "min_adx không được âm."
            )

    def combinations(
        self,
    ) -> Iterable[dict[str, float | int]]:
        self.validate()

        for (
            stop_multiplier,
            target_multiplier,
            holding_days,
            min_adx,
        ) in itertools.product(
            self.atr_stop_multipliers,
            self.atr_target_multipliers,
            self.holding_days,
            self.min_adx_values,
        ):
            yield {
                "atr_stop_multiplier": float(
                    stop_multiplier
                ),
                "atr_target_multiplier": float(
                    target_multiplier
                ),
                "max_holding_days": int(
                    holding_days
                ),
                "min_adx": float(
                    min_adx
                ),
            }


@dataclass(slots=True, frozen=True)
class OptimizationConfig:
    objective: str = "sharpe_ratio"
    minimum_train_trades: int = 10
    drawdown_penalty: float = 0.05

    def validate(self) -> None:
        supported = {
            "sharpe_ratio",
            "total_return_pct",
            "profit_factor",
            "composite",
        }

        if self.objective not in supported:
            raise ValueError(
                "objective không hợp lệ: "
                f"{self.objective}"
            )

        if self.minimum_train_trades < 1:
            raise ValueError(
                "minimum_train_trades phải từ 1."
            )

        if self.drawdown_penalty < 0:
            raise ValueError(
                "drawdown_penalty không được âm."
            )


@dataclass(slots=True, frozen=True)
class WalkForwardOptimizationResult:
    folds: pd.DataFrame
    train_search: pd.DataFrame
    summary: dict[str, Any]

    def save(
        self,
        *,
        folds_path: str,
        train_search_path: str,
        summary_path: str,
    ) -> None:
        self.folds.to_csv(
            folds_path,
            index=False,
            encoding="utf-8-sig",
        )

        self.train_search.to_csv(
            train_search_path,
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


def _objective_score(
    metrics: dict[str, Any],
    *,
    config: OptimizationConfig,
) -> float:
    trades = int(
        metrics.get(
            "total_trades",
            0,
        )
    )

    if trades < config.minimum_train_trades:
        return -math.inf

    sharpe = _safe_float(
        metrics.get(
            "sharpe_ratio"
        )
    )

    total_return = _safe_float(
        metrics.get(
            "total_return_pct"
        )
    )

    profit_factor = _safe_float(
        metrics.get(
            "profit_factor"
        )
    )

    max_drawdown = abs(
        _safe_float(
            metrics.get(
                "max_drawdown_pct"
            )
        )
    )

    if config.objective == "sharpe_ratio":
        return sharpe

    if config.objective == "total_return_pct":
        return total_return

    if config.objective == "profit_factor":
        return profit_factor

    # Composite:
    # Sharpe là thành phần chính.
    # Return và profit factor bổ trợ.
    # Drawdown bị phạt.
    return (
        sharpe
        + total_return / 100
        + min(profit_factor, 5.0) * 0.10
        - max_drawdown
        * config.drawdown_penalty
    )


def _select_best_candidate(
    search_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    valid_rows = [
        row
        for row in search_rows
        if math.isfinite(
            float(
                row["objective_score"]
            )
        )
    ]

    if not valid_rows:
        raise ValueError(
            "Không có bộ tham số train hợp lệ. "
            "Hãy giảm minimum_train_trades "
            "hoặc mở rộng train window."
        )

    return max(
        valid_rows,
        key=lambda row: (
            float(
                row["objective_score"]
            ),
            float(
                row["train_total_return_pct"]
            ),
            float(
                row["train_sharpe_ratio"]
            ),
            -abs(
                float(
                    row["train_max_drawdown_pct"]
                )
            ),
        ),
    )


def run_walk_forward_optimization(
    *,
    walk_forward_config: WalkForwardConfig,
    parameter_grid: WalkForwardParameterGrid,
    optimization_config: OptimizationConfig,
    initial_capital: float,
    run_backtest_fn: Callable[..., tuple],
    build_exit_model_fn: Callable[..., Any],
    base_backtest_kwargs: dict[str, Any],
) -> WalkForwardOptimizationResult:
    walk_forward_config.validate()
    parameter_grid.validate()
    optimization_config.validate()

    if initial_capital <= 0:
        raise ValueError(
            "initial_capital phải lớn hơn 0."
        )

    folds: list[WalkForwardFold] = (
        build_walk_forward_folds(
            walk_forward_config
        )
    )

    fold_rows: list[dict[str, Any]] = []
    search_rows_all: list[dict[str, Any]] = []

    current_capital = float(
        initial_capital
    )

    for fold in folds:
        print()
        print("=" * 100)
        print(
            f"WALK-FORWARD OPTIMIZATION "
            f"FOLD {fold.fold}"
        )
        print("=" * 100)

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

        fold_search_rows: list[
            dict[str, Any]
        ] = []

        combinations = list(
            parameter_grid.combinations()
        )

        print(
            f"Parameter combinations: "
            f"{len(combinations)}"
        )

        for combination_index, params in enumerate(
            combinations,
            start=1,
        ):
            exit_model = build_exit_model_fn(
                name="atr",
                stop_atr_multiplier=params[
                    "atr_stop_multiplier"
                ],
                target_atr_multiplier=params[
                    "atr_target_multiplier"
                ],
                break_even_trigger=5.0,
                trailing_atr_multiplier=2.0,
            )

            _, train_metrics, _ = (
                run_backtest_fn(
                    **base_backtest_kwargs,
                    start_date=str(
                        fold.train_start.date()
                    ),
                    end_date=str(
                        fold.train_end.date()
                    ),
                    initial_capital=(
                        initial_capital
                    ),
                    max_holding_days=params[
                        "max_holding_days"
                    ],
                    min_adx=params[
                        "min_adx"
                    ],
                    exit_model=exit_model,
                    verbose=False,
                )
            )

            objective_score = (
                _objective_score(
                    train_metrics,
                    config=(
                        optimization_config
                    ),
                )
            )

            row = {
                "fold": fold.fold,
                "combination": (
                    combination_index
                ),
                **params,
                "objective": (
                    optimization_config.objective
                ),
                "objective_score": (
                    objective_score
                ),
                "train_trades": int(
                    train_metrics.get(
                        "total_trades",
                        0,
                    )
                ),
                "train_total_return_pct": (
                    _safe_float(
                        train_metrics.get(
                            "total_return_pct"
                        )
                    )
                ),
                "train_sharpe_ratio": (
                    _safe_float(
                        train_metrics.get(
                            "sharpe_ratio"
                        )
                    )
                ),
                "train_sortino_ratio": (
                    _safe_float(
                        train_metrics.get(
                            "sortino_ratio"
                        )
                    )
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
                "train_win_rate_pct": (
                    _safe_float(
                        train_metrics.get(
                            "win_rate_pct"
                        )
                    )
                ),
            }

            fold_search_rows.append(
                row
            )

            search_rows_all.append(
                row
            )

        best = _select_best_candidate(
            fold_search_rows
        )

        print()
        print("BEST TRAIN PARAMETERS")
        print("-" * 100)
        print(
            f"ATR Stop     : "
            f"{best['atr_stop_multiplier']}"
        )
        print(
            f"ATR Target   : "
            f"{best['atr_target_multiplier']}"
        )
        print(
            f"Holding Days : "
            f"{best['max_holding_days']}"
        )
        print(
            f"Min ADX      : "
            f"{best['min_adx']}"
        )
        print(
            f"Train Trades : "
            f"{best['train_trades']}"
        )
        print(
            f"Train Return : "
            f"{best['train_total_return_pct']:+.2f}%"
        )
        print(
            f"Train Sharpe : "
            f"{best['train_sharpe_ratio']:.2f}"
        )

        best_exit_model = (
            build_exit_model_fn(
                name="atr",
                stop_atr_multiplier=float(
                    best[
                        "atr_stop_multiplier"
                    ]
                ),
                target_atr_multiplier=float(
                    best[
                        "atr_target_multiplier"
                    ]
                ),
                break_even_trigger=5.0,
                trailing_atr_multiplier=2.0,
            )
        )

        fold_initial_capital = (
            current_capital
        )

        test_trades, test_metrics, _ = (
            run_backtest_fn(
                **base_backtest_kwargs,
                start_date=str(
                    fold.test_start.date()
                ),
                end_date=str(
                    fold.test_end.date()
                ),
                initial_capital=(
                    fold_initial_capital
                ),
                max_holding_days=int(
                    best[
                        "max_holding_days"
                    ]
                ),
                min_adx=float(
                    best["min_adx"]
                ),
                exit_model=best_exit_model,
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

        test_return = _safe_float(
            test_metrics.get(
                "total_return_pct"
            )
        )

        fold_rows.append(
            {
                "fold": fold.fold,
                "train_start": (
                    fold.train_start.date()
                ),
                "train_end": (
                    fold.train_end.date()
                ),
                "test_start": (
                    fold.test_start.date()
                ),
                "test_end": (
                    fold.test_end.date()
                ),

                "selected_atr_stop_multiplier": (
                    best[
                        "atr_stop_multiplier"
                    ]
                ),
                "selected_atr_target_multiplier": (
                    best[
                        "atr_target_multiplier"
                    ]
                ),
                "selected_max_holding_days": (
                    best[
                        "max_holding_days"
                    ]
                ),
                "selected_min_adx": (
                    best["min_adx"]
                ),

                "train_objective_score": (
                    best[
                        "objective_score"
                    ]
                ),
                "train_trades": (
                    best["train_trades"]
                ),
                "train_return_pct": (
                    best[
                        "train_total_return_pct"
                    ]
                ),
                "train_sharpe_ratio": (
                    best[
                        "train_sharpe_ratio"
                    ]
                ),
                "train_max_drawdown_pct": (
                    best[
                        "train_max_drawdown_pct"
                    ]
                ),
                "train_profit_factor": (
                    best[
                        "train_profit_factor"
                    ]
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
                    _safe_float(
                        test_metrics.get(
                            "sharpe_ratio"
                        )
                    )
                ),
                "test_sortino_ratio": (
                    _safe_float(
                        test_metrics.get(
                            "sortino_ratio"
                        )
                    )
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
                "test_profitable": (
                    test_return > 0
                ),
                "return_degradation_pct": (
                    test_return
                    - float(
                        best[
                            "train_total_return_pct"
                        ]
                    )
                ),
                "sharpe_degradation": (
                    _safe_float(
                        test_metrics.get(
                            "sharpe_ratio"
                        )
                    )
                    - float(
                        best[
                            "train_sharpe_ratio"
                        ]
                    )
                ),
            }
        )

        print()
        print("OUT-OF-SAMPLE RESULT")
        print("-" * 100)
        print(
            f"Test Trades   : "
            f"{len(test_trades)}"
        )
        print(
            f"Test Return   : "
            f"{test_return:+.2f}%"
        )
        print(
            f"Test Equity   : "
            f"{test_final_equity:,.0f}"
        )

    folds_df = pd.DataFrame(
        fold_rows
    )

    train_search_df = pd.DataFrame(
        search_rows_all
    )

    profitable_folds = int(
        folds_df[
            "test_profitable"
        ].sum()
    )

    total_folds = len(
        folds_df
    )

    summary: dict[str, Any] = {
        "folds": total_folds,
        "objective": (
            optimization_config.objective
        ),
        "minimum_train_trades": (
            optimization_config
            .minimum_train_trades
        ),
        "parameter_combinations": len(
            list(
                parameter_grid.combinations()
            )
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

    return WalkForwardOptimizationResult(
        folds=folds_df,
        train_search=train_search_df,
        summary=summary,
    )


def print_walk_forward_optimization_report(
    result: WalkForwardOptimizationResult,
) -> None:
    summary = result.summary

    print()
    print("=" * 90)
    print("WALK-FORWARD OPTIMIZATION")
    print("=" * 90)

    print(
        f"Objective           : "
        f"{summary['objective']}"
    )
    print(
        f"Folds               : "
        f"{summary['folds']}"
    )
    print(
        f"Combinations/Fold   : "
        f"{summary['parameter_combinations']}"
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
        f"Worst Drawdown      : "
        f"{summary['worst_test_drawdown_pct']:.2f}%"
    )
    print("=" * 90)