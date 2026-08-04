from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Iterable

import pandas as pd

from backtesting.trade import Trade


@dataclass(slots=True, frozen=True)
class MonteCarloConfig:
    simulations: int = 5_000
    confidence_level: float = 0.95
    random_seed: int | None = 42

    def validate(self) -> None:
        if self.simulations < 100:
            raise ValueError(
                "simulations phải từ 100 trở lên."
            )

        if not 0 < self.confidence_level < 1:
            raise ValueError(
                "confidence_level phải nằm trong khoảng (0, 1)."
            )


@dataclass(slots=True, frozen=True)
class MonteCarloResult:
    simulations: pd.DataFrame
    summary: dict[str, float | int]

    def save(
        self,
        *,
        simulation_path: str,
        summary_path: str,
    ) -> None:
        self.simulations.to_csv(
            simulation_path,
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
    value: object,
    default: float = 0.0,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default

    if not math.isfinite(result):
        return default

    return result


def _extract_trade_returns(
    trades: Iterable[Trade],
) -> list[float]:
    returns: list[float] = []

    for trade in trades:
        if not trade.is_closed:
            continue

        value = _safe_float(
            trade.net_return_pct,
            default=math.nan,
        )

        if math.isfinite(value):
            returns.append(value)

    return returns


def _calculate_max_drawdown(
    equity_values: list[float],
) -> float:
    if not equity_values:
        return 0.0

    peak = equity_values[0]
    max_drawdown = 0.0

    for equity in equity_values:
        peak = max(peak, equity)

        if peak <= 0:
            continue

        drawdown = (
            equity / peak - 1
        ) * 100

        max_drawdown = min(
            max_drawdown,
            drawdown,
        )

    return max_drawdown


def _simulate_single_path(
    *,
    trade_returns: list[float],
    initial_capital: float,
    position_size_pct: float,
    rng: random.Random,
) -> dict[str, float]:
    sampled_returns = [
        rng.choice(trade_returns)
        for _ in range(len(trade_returns))
    ]

    equity = float(initial_capital)
    equity_curve = [equity]

    wins = 0
    losses = 0

    position_fraction = (
        position_size_pct / 100
    )

    for return_pct in sampled_returns:
        portfolio_return_pct = (
            return_pct
            * position_fraction
        )

        equity *= (
            1 + portfolio_return_pct / 100
        )

        equity_curve.append(equity)

        if portfolio_return_pct > 0:
            wins += 1
        elif portfolio_return_pct < 0:
            losses += 1

        equity_curve.append(equity)

        if return_pct > 0:
            wins += 1
        elif return_pct < 0:
            losses += 1

    total_return_pct = (
        equity / initial_capital - 1
    ) * 100

    max_drawdown_pct = (
        _calculate_max_drawdown(
            equity_curve
        )
    )

    return {
        "final_equity": equity,
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "wins": float(wins),
        "losses": float(losses),
    }


def run_monte_carlo(
    trades: Iterable[Trade],
    *,
    initial_capital: float,
    position_size_pct: float = 20.0,
    simulations: int = 5_000,
    confidence_level: float = 0.95,
    random_seed: int | None = 42,
) -> MonteCarloResult:
    config = MonteCarloConfig(
        simulations=simulations,
        confidence_level=confidence_level,
        random_seed=random_seed,
    )
    config.validate()

    if initial_capital <= 0:
        raise ValueError(
            "initial_capital phải lớn hơn 0."
        )

    if not 0 < position_size_pct <= 100:
        raise ValueError(
            "position_size_pct phải nằm trong khoảng (0, 100]."
        )

    trade_returns = _extract_trade_returns(
        trades
    )

    if not trade_returns:
        raise ValueError(
            "Không có closed trade hợp lệ để chạy Monte Carlo."
        )

    rng = random.Random(
        config.random_seed
    )

    rows: list[dict[str, float | int]] = []

    for simulation_id in range(
        1,
        config.simulations + 1,
    ):
        result = _simulate_single_path(
            trade_returns=trade_returns,
            initial_capital=initial_capital,
            position_size_pct=position_size_pct,
            rng=rng,
        )

        rows.append(
            {
                "simulation": simulation_id,
                **result,
            }
        )

    simulations_df = pd.DataFrame(
        rows
    )

    alpha = (
        1 - config.confidence_level
    )

    lower_quantile = alpha / 2
    upper_quantile = 1 - alpha / 2

    return_series = simulations_df[
        "total_return_pct"
    ]

    drawdown_series = simulations_df[
        "max_drawdown_pct"
    ]

    final_equity_series = simulations_df[
        "final_equity"
    ]

    summary: dict[str, float | int] = {
        "simulations": config.simulations,
        "trade_count": len(trade_returns),
        "confidence_level": (
            config.confidence_level
        ),
        "initial_capital": (
            float(initial_capital)
        ),
        "position_size_pct": float(
            position_size_pct
        ),
        "median_final_equity": float(
            final_equity_series.median()
        ),
        "mean_final_equity": float(
            final_equity_series.mean()
        ),
        "worst_final_equity": float(
            final_equity_series.min()
        ),
        "best_final_equity": float(
            final_equity_series.max()
        ),

        "median_return_pct": float(
            return_series.median()
        ),
        "mean_return_pct": float(
            return_series.mean()
        ),
        "worst_return_pct": float(
            return_series.min()
        ),
        "best_return_pct": float(
            return_series.max()
        ),

        "return_lower_bound_pct": float(
            return_series.quantile(
                lower_quantile
            )
        ),
        "return_upper_bound_pct": float(
            return_series.quantile(
                upper_quantile
            )
        ),

        "median_max_drawdown_pct": float(
            drawdown_series.median()
        ),
        "worst_max_drawdown_pct": float(
            drawdown_series.min()
        ),

        "drawdown_confidence_bound_pct": float(
            drawdown_series.quantile(
                alpha
            )
        ),

        "probability_of_loss_pct": float(
            (
                return_series < 0
            ).mean()
            * 100
        ),

        "probability_of_profit_pct": float(
            (
                return_series > 0
            ).mean()
            * 100
        ),

        "probability_of_20pct_drawdown_pct": float(
            (
                drawdown_series <= -20
            ).mean()
            * 100
        ),

        "probability_of_30pct_drawdown_pct": float(
            (
                drawdown_series <= -30
            ).mean()
            * 100
        ),
    }

    return MonteCarloResult(
        simulations=simulations_df,
        summary=summary,
    )


def print_monte_carlo_report(
    result: MonteCarloResult,
) -> None:
    summary = result.summary

    print()
    print("=" * 70)
    print("MONTE CARLO SIMULATION")
    print("=" * 70)

    print(
        f"Simulations        : "
        f"{summary['simulations']:,}"
    )
    print(
        f"Trades per path    : "
        f"{summary['trade_count']:,}"
    )
    print(
        f"Confidence level   : "
        f"{summary['confidence_level']:.0%}"
    )

    print()
    print("RETURN DISTRIBUTION")
    print("-" * 70)

    print(
        f"Median Return      : "
        f"{summary['median_return_pct']:+.2f}%"
    )
    print(
        f"Mean Return        : "
        f"{summary['mean_return_pct']:+.2f}%"
    )
    print(
        f"Worst Return       : "
        f"{summary['worst_return_pct']:+.2f}%"
    )
    print(
        f"Best Return        : "
        f"{summary['best_return_pct']:+.2f}%"
    )

    print(
        f"Confidence Range   : "
        f"{summary['return_lower_bound_pct']:+.2f}% "
        f"to "
        f"{summary['return_upper_bound_pct']:+.2f}%"
    )

    print()
    print("RISK DISTRIBUTION")
    print("-" * 70)

    print(
        f"Median Drawdown    : "
        f"{summary['median_max_drawdown_pct']:.2f}%"
    )
    print(
        f"Worst Drawdown     : "
        f"{summary['worst_max_drawdown_pct']:.2f}%"
    )
    print(
        f"Confidence DD      : "
        f"{summary['drawdown_confidence_bound_pct']:.2f}%"
    )

    print(
        f"Probability Loss   : "
        f"{summary['probability_of_loss_pct']:.2f}%"
    )
    print(
        f"Probability Profit : "
        f"{summary['probability_of_profit_pct']:.2f}%"
    )
    print(
        f"P(Drawdown <= -20%): "
        f"{summary['probability_of_20pct_drawdown_pct']:.2f}%"
    )
    print(
        f"P(Drawdown <= -30%): "
        f"{summary['probability_of_30pct_drawdown_pct']:.2f}%"
    )

    print("=" * 70)