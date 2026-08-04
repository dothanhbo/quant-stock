from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

import pandas as pd

from backtesting.trade import Trade


BootstrapMethod = Literal[
    "trade",
    "block",
    "regime",
]


@dataclass(slots=True, frozen=True)
class MonteCarloV2Config:
    simulations: int = 10_000
    confidence_level: float = 0.95
    position_size_pct: float = 20.0

    bootstrap_method: BootstrapMethod = "block"
    block_size: int = 10

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

        if not 0 < self.position_size_pct <= 100:
            raise ValueError(
                "position_size_pct phải nằm trong khoảng (0, 100]."
            )

        if self.bootstrap_method not in {
            "trade",
            "block",
            "regime",
        }:
            raise ValueError(
                f"bootstrap_method không hợp lệ: "
                f"{self.bootstrap_method}"
            )

        if self.block_size < 1:
            raise ValueError(
                "block_size phải từ 1 trở lên."
            )


@dataclass(slots=True, frozen=True)
class TradeObservation:
    sequence: int
    symbol: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    return_pct: float
    market_regime: str


@dataclass(slots=True, frozen=True)
class MonteCarloV2Result:
    simulations: pd.DataFrame
    summary: dict[str, float | int | str]

    def save(
        self,
        *,
        output_dir: str | Path,
    ) -> None:
        output_path = Path(output_dir)

        output_path.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.simulations.to_csv(
            output_path / "simulations_v2.csv",
            index=False,
            encoding="utf-8-sig",
        )

        pd.DataFrame(
            [self.summary]
        ).to_csv(
            output_path / "summary_v2.csv",
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


def _normalize_regime(
    value: object,
) -> str:
    if value is None:
        return "UNKNOWN"

    normalized = (
        str(value)
        .strip()
        .upper()
    )

    if normalized in {
        "BULL",
        "SIDEWAY",
        "BEAR",
    }:
        return normalized

    return "UNKNOWN"


def extract_trade_observations(
    trades: Iterable[Trade],
) -> list[TradeObservation]:
    closed_trades = [
        trade
        for trade in trades
        if trade.is_closed
    ]

    closed_trades.sort(
        key=lambda trade: (
            trade.entry_date,
            trade.symbol,
        )
    )

    observations: list[
        TradeObservation
    ] = []

    for sequence, trade in enumerate(
        closed_trades,
        start=1,
    ):
        return_pct = _safe_float(
            trade.net_return_pct,
            default=math.nan,
        )

        if not math.isfinite(return_pct):
            continue

        observations.append(
            TradeObservation(
                sequence=sequence,
                symbol=trade.symbol,
                entry_date=pd.Timestamp(
                    trade.entry_date
                ),
                exit_date=pd.Timestamp(
                    trade.exit_date
                ),
                return_pct=return_pct,
                market_regime=(
                    _normalize_regime(
                        trade.market_regime
                    )
                ),
            )
        )

    if not observations:
        raise ValueError(
            "Không có closed trade hợp lệ."
        )

    return observations


def _trade_bootstrap(
    observations: list[TradeObservation],
    *,
    rng: random.Random,
) -> list[TradeObservation]:
    return [
        rng.choice(
            observations
        )
        for _ in range(
            len(observations)
        )
    ]


def _build_overlapping_blocks(
    observations: list[TradeObservation],
    *,
    block_size: int,
) -> list[list[TradeObservation]]:
    if block_size >= len(observations):
        return [
            observations.copy()
        ]

    return [
        observations[
            start_index:
            start_index + block_size
        ]
        for start_index in range(
            0,
            len(observations)
            - block_size
            + 1,
        )
    ]


def _block_bootstrap(
    observations: list[TradeObservation],
    *,
    block_size: int,
    rng: random.Random,
) -> list[TradeObservation]:
    blocks = _build_overlapping_blocks(
        observations,
        block_size=block_size,
    )

    sampled: list[
        TradeObservation
    ] = []

    while len(sampled) < len(
        observations
    ):
        sampled.extend(
            rng.choice(
                blocks
            )
        )

    return sampled[
        :len(observations)
    ]


def _build_regime_blocks(
    observations: list[TradeObservation],
) -> list[list[TradeObservation]]:
    if not observations:
        return []

    blocks: list[
        list[TradeObservation]
    ] = []

    current_block = [
        observations[0]
    ]

    current_regime = (
        observations[0]
        .market_regime
    )

    for observation in observations[1:]:
        if (
            observation.market_regime
            == current_regime
        ):
            current_block.append(
                observation
            )
            continue

        blocks.append(
            current_block
        )

        current_block = [
            observation
        ]

        current_regime = (
            observation.market_regime
        )

    blocks.append(
        current_block
    )

    return blocks


def _regime_bootstrap(
    observations: list[TradeObservation],
    *,
    rng: random.Random,
) -> list[TradeObservation]:
    regime_blocks = (
        _build_regime_blocks(
            observations
        )
    )

    sampled: list[
        TradeObservation
    ] = []

    while len(sampled) < len(
        observations
    ):
        sampled.extend(
            rng.choice(
                regime_blocks
            )
        )

    return sampled[
        :len(observations)
    ]


def _sample_path(
    observations: list[TradeObservation],
    *,
    config: MonteCarloV2Config,
    rng: random.Random,
) -> list[TradeObservation]:
    if config.bootstrap_method == "trade":
        return _trade_bootstrap(
            observations,
            rng=rng,
        )

    if config.bootstrap_method == "block":
        return _block_bootstrap(
            observations,
            block_size=config.block_size,
            rng=rng,
        )

    return _regime_bootstrap(
        observations,
        rng=rng,
    )


def _calculate_max_drawdown(
    equity_curve: list[float],
) -> float:
    if not equity_curve:
        return 0.0

    peak = equity_curve[0]
    worst_drawdown = 0.0

    for equity in equity_curve:
        peak = max(
            peak,
            equity,
        )

        if peak <= 0:
            continue

        drawdown_pct = (
            equity / peak
            - 1
        ) * 100

        worst_drawdown = min(
            worst_drawdown,
            drawdown_pct,
        )

    return worst_drawdown


def _simulate_path(
    sampled_path: list[TradeObservation],
    *,
    initial_capital: float,
    position_size_pct: float,
) -> dict[str, float]:
    position_fraction = (
        position_size_pct
        / 100
    )

    equity = float(
        initial_capital
    )

    equity_curve = [
        equity
    ]

    wins = 0
    losses = 0

    bull_trades = 0
    sideway_trades = 0
    bear_trades = 0
    unknown_trades = 0

    for observation in sampled_path:
        portfolio_return_pct = (
            observation.return_pct
            * position_fraction
        )

        equity *= (
            1
            + portfolio_return_pct
            / 100
        )

        equity_curve.append(
            equity
        )

        if portfolio_return_pct > 0:
            wins += 1
        elif portfolio_return_pct < 0:
            losses += 1

        if observation.market_regime == "BULL":
            bull_trades += 1
        elif observation.market_regime == "SIDEWAY":
            sideway_trades += 1
        elif observation.market_regime == "BEAR":
            bear_trades += 1
        else:
            unknown_trades += 1

    total_return_pct = (
        equity / initial_capital
        - 1
    ) * 100

    max_drawdown_pct = (
        _calculate_max_drawdown(
            equity_curve
        )
    )

    return {
        "final_equity": equity,
        "total_return_pct": (
            total_return_pct
        ),
        "max_drawdown_pct": (
            max_drawdown_pct
        ),
        "wins": float(wins),
        "losses": float(losses),
        "bull_trades": float(
            bull_trades
        ),
        "sideway_trades": float(
            sideway_trades
        ),
        "bear_trades": float(
            bear_trades
        ),
        "unknown_trades": float(
            unknown_trades
        ),
    }


def run_monte_carlo_v2(
    trades: Iterable[Trade],
    *,
    initial_capital: float,
    simulations: int = 10_000,
    confidence_level: float = 0.95,
    position_size_pct: float = 20.0,
    bootstrap_method: BootstrapMethod = "block",
    block_size: int = 10,
    random_seed: int | None = 42,
) -> MonteCarloV2Result:
    config = MonteCarloV2Config(
        simulations=simulations,
        confidence_level=(
            confidence_level
        ),
        position_size_pct=(
            position_size_pct
        ),
        bootstrap_method=(
            bootstrap_method
        ),
        block_size=block_size,
        random_seed=random_seed,
    )

    config.validate()

    if initial_capital <= 0:
        raise ValueError(
            "initial_capital phải lớn hơn 0."
        )

    observations = (
        extract_trade_observations(
            trades
        )
    )

    rng = random.Random(
        config.random_seed
    )

    rows: list[
        dict[str, float | int]
    ] = []

    for simulation_id in range(
        1,
        config.simulations + 1,
    ):
        sampled_path = (
            _sample_path(
                observations,
                config=config,
                rng=rng,
            )
        )

        path_result = (
            _simulate_path(
                sampled_path,
                initial_capital=(
                    initial_capital
                ),
                position_size_pct=(
                    config.position_size_pct
                ),
            )
        )

        rows.append(
            {
                "simulation": (
                    simulation_id
                ),
                **path_result,
            }
        )

    simulations_df = pd.DataFrame(
        rows
    )

    alpha = (
        1
        - config.confidence_level
    )

    lower_quantile = (
        alpha / 2
    )

    upper_quantile = (
        1
        - alpha / 2
    )

    returns = simulations_df[
        "total_return_pct"
    ]

    drawdowns = simulations_df[
        "max_drawdown_pct"
    ]

    final_equity = simulations_df[
        "final_equity"
    ]

    summary: dict[
        str,
        float | int | str
    ] = {
        "bootstrap_method": (
            config.bootstrap_method
        ),
        "block_size": (
            config.block_size
        ),
        "simulations": (
            config.simulations
        ),
        "trade_count": len(
            observations
        ),
        "position_size_pct": (
            config.position_size_pct
        ),
        "confidence_level": (
            config.confidence_level
        ),
        "initial_capital": (
            float(initial_capital)
        ),
        "median_final_equity": float(
            final_equity.median()
        ),
        "mean_final_equity": float(
            final_equity.mean()
        ),
        "worst_final_equity": float(
            final_equity.min()
        ),
        "best_final_equity": float(
            final_equity.max()
        ),
        "median_return_pct": float(
            returns.median()
        ),
        "mean_return_pct": float(
            returns.mean()
        ),
        "worst_return_pct": float(
            returns.min()
        ),
        "best_return_pct": float(
            returns.max()
        ),
        "return_lower_bound_pct": float(
            returns.quantile(
                lower_quantile
            )
        ),
        "return_upper_bound_pct": float(
            returns.quantile(
                upper_quantile
            )
        ),
        "median_drawdown_pct": float(
            drawdowns.median()
        ),
        "worst_drawdown_pct": float(
            drawdowns.min()
        ),
        "drawdown_95pct_bound": float(
            drawdowns.quantile(
                alpha
            )
        ),
        "probability_of_loss_pct": float(
            (
                returns < 0
            ).mean()
            * 100
        ),
        "probability_drawdown_10_pct": float(
            (
                drawdowns <= -10
            ).mean()
            * 100
        ),
        "probability_drawdown_20_pct": float(
            (
                drawdowns <= -20
            ).mean()
            * 100
        ),
        "probability_drawdown_30_pct": float(
            (
                drawdowns <= -30
            ).mean()
            * 100
        ),
    }

    return MonteCarloV2Result(
        simulations=(
            simulations_df
        ),
        summary=summary,
    )


def print_monte_carlo_v2_report(
    result: MonteCarloV2Result,
) -> None:
    summary = result.summary

    print()
    print("=" * 80)
    print("MONTE CARLO V2")
    print("=" * 80)

    print(
        f"Bootstrap Method   : "
        f"{summary['bootstrap_method']}"
    )
    print(
        f"Block Size         : "
        f"{summary['block_size']}"
    )
    print(
        f"Simulations        : "
        f"{summary['simulations']:,}"
    )
    print(
        f"Trades per Path    : "
        f"{summary['trade_count']}"
    )
    print(
        f"Position Size      : "
        f"{summary['position_size_pct']:.2f}%"
    )

    print()
    print("RETURN DISTRIBUTION")
    print("-" * 80)

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
    print("-" * 80)

    print(
        f"Median Drawdown    : "
        f"{summary['median_drawdown_pct']:.2f}%"
    )
    print(
        f"Worst Drawdown     : "
        f"{summary['worst_drawdown_pct']:.2f}%"
    )
    print(
        f"95% Drawdown Bound : "
        f"{summary['drawdown_95pct_bound']:.2f}%"
    )
    print(
        f"Probability Loss   : "
        f"{summary['probability_of_loss_pct']:.2f}%"
    )
    print(
        f"P(DD <= -10%)      : "
        f"{summary['probability_drawdown_10_pct']:.2f}%"
    )
    print(
        f"P(DD <= -20%)      : "
        f"{summary['probability_drawdown_20_pct']:.2f}%"
    )
    print(
        f"P(DD <= -30%)      : "
        f"{summary['probability_drawdown_30_pct']:.2f}%"
    )

    print("=" * 80)