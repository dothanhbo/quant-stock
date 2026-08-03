from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Iterable

from backtesting.ranking import (
    RankingMethod,
    composite_score,
    parse_ranking_method,
)
from backtesting.trade import Trade


class DecisionAction(str, Enum):
    OPEN = "open"
    REJECT = "reject"
    WOULD_REPLACE = "would_replace"


@dataclass(slots=True, frozen=True)
class CandidateDecision:
    action: DecisionAction
    reason: str
    candidate: Trade
    candidate_quality: float | None = None
    weakest_trade: Trade | None = None
    weakest_quality: float | None = None
    quality_gap: float | None = None
    replacement_threshold: float = 0.0


def _safe_float(
    value: object,
    *,
    default: float = float("-inf"),
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default

    if not isfinite(result):
        return default

    return result


def trade_quality(
    trade: Trade,
    method: RankingMethod | str,
) -> float:
    ranking_method = parse_ranking_method(method)

    if ranking_method == RankingMethod.FIRST_COME:
        return float("-inf")

    if ranking_method == RankingMethod.SIGNAL_SCORE:
        return _safe_float(
            getattr(trade, "signal_score", None)
        )

    if ranking_method == RankingMethod.RELATIVE_STRENGTH:
        return _safe_float(
            getattr(trade, "relative_strength", None)
        )

    if ranking_method == RankingMethod.ADX:
        return _safe_float(
            getattr(trade, "adx", None)
        )

    if ranking_method == RankingMethod.VOLUME_RATIO:
        return _safe_float(
            getattr(trade, "volume_ratio", None)
        )

    if ranking_method == RankingMethod.COMPOSITE:
        try:
            return _safe_float(composite_score(trade))
        except (TypeError, ValueError, AttributeError):
            return float("-inf")

    return float("-inf")


def find_weakest_trade(
    open_positions: Iterable[Trade],
    *,
    ranking_method: RankingMethod | str,
) -> tuple[Trade | None, float | None]:
    positions = list(open_positions)

    if not positions:
        return None, None

    scored_positions = [
        (
            trade,
            trade_quality(
                trade,
                ranking_method,
            ),
        )
        for trade in positions
    ]

    weakest_trade, weakest_quality = min(
        scored_positions,
        key=lambda item: (
            item[1],
            str(item[0].symbol),
        ),
    )

    return weakest_trade, weakest_quality


def decide_candidate(
    *,
    candidate: Trade,
    open_positions: Iterable[Trade],
    max_positions: int,
    ranking_method: RankingMethod | str,
    replacement_threshold: float = 0.0,
    allow_duplicate_symbols: bool = False,
) -> CandidateDecision:
    if max_positions < 1:
        raise ValueError(
            "max_positions phải từ 1 trở lên."
        )

    if replacement_threshold < 0:
        raise ValueError(
            "replacement_threshold không được âm."
        )

    ranking_method = parse_ranking_method(
        ranking_method
    )
    positions = list(open_positions)

    if (
        not allow_duplicate_symbols
        and any(
            str(trade.symbol).upper()
            == str(candidate.symbol).upper()
            for trade in positions
        )
    ):
        return CandidateDecision(
            action=DecisionAction.REJECT,
            reason="duplicate_symbol",
            candidate=candidate,
            replacement_threshold=replacement_threshold,
        )

    candidate_quality = trade_quality(
        candidate,
        ranking_method,
    )

    if len(positions) < max_positions:
        return CandidateDecision(
            action=DecisionAction.OPEN,
            reason="slot_available",
            candidate=candidate,
            candidate_quality=(
                None
                if candidate_quality == float("-inf")
                else candidate_quality
            ),
            replacement_threshold=replacement_threshold,
        )

    if ranking_method == RankingMethod.FIRST_COME:
        return CandidateDecision(
            action=DecisionAction.REJECT,
            reason="portfolio_full_no_quality_method",
            candidate=candidate,
            replacement_threshold=replacement_threshold,
        )

    weakest_trade, weakest_quality = find_weakest_trade(
        positions,
        ranking_method=ranking_method,
    )

    if weakest_trade is None or weakest_quality is None:
        return CandidateDecision(
            action=DecisionAction.REJECT,
            reason="portfolio_full_no_weakest_trade",
            candidate=candidate,
            candidate_quality=(
                None
                if candidate_quality == float("-inf")
                else candidate_quality
            ),
            replacement_threshold=replacement_threshold,
        )

    if (
        candidate_quality == float("-inf")
        or weakest_quality == float("-inf")
    ):
        return CandidateDecision(
            action=DecisionAction.REJECT,
            reason="missing_quality_data",
            candidate=candidate,
            candidate_quality=(
                None
                if candidate_quality == float("-inf")
                else candidate_quality
            ),
            weakest_trade=weakest_trade,
            weakest_quality=(
                None
                if weakest_quality == float("-inf")
                else weakest_quality
            ),
            replacement_threshold=replacement_threshold,
        )

    quality_gap = candidate_quality - weakest_quality

    if quality_gap >= replacement_threshold:
        return CandidateDecision(
            action=DecisionAction.WOULD_REPLACE,
            reason="candidate_quality_above_threshold",
            candidate=candidate,
            candidate_quality=candidate_quality,
            weakest_trade=weakest_trade,
            weakest_quality=weakest_quality,
            quality_gap=quality_gap,
            replacement_threshold=replacement_threshold,
        )

    return CandidateDecision(
        action=DecisionAction.REJECT,
        reason="candidate_quality_below_threshold",
        candidate=candidate,
        candidate_quality=candidate_quality,
        weakest_trade=weakest_trade,
        weakest_quality=weakest_quality,
        quality_gap=quality_gap,
        replacement_threshold=replacement_threshold,
    )
