from __future__ import annotations

from enum import Enum


class RankingMethod(str, Enum):

    FIRST_COME = "first_come"

    SIGNAL_SCORE = "signal_score"

    RELATIVE_STRENGTH = "relative_strength"

    ADX = "adx"

    VOLUME_RATIO = "volume_ratio"

    COMPOSITE = "composite"

    CROSS_SECTIONAL_LEADERSHIP = (
        "cross_sectional_leadership"
    )

def parse_ranking_method(
    value: RankingMethod | str,
) -> RankingMethod:
    if isinstance(
        value,
        RankingMethod,
    ):
        return value

    try:
        return RankingMethod(
            str(
                value
            )
            .strip()
            .lower()
        )

    except ValueError as exc:
        supported = ", ".join(
            method.value
            for method
            in RankingMethod
        )

        raise ValueError(
            f"ranking_method không hợp lệ: "
            f"{value}. "
            f"Hỗ trợ: {supported}"
        ) from exc


def _value(value):

    if value is None:
        return float("-inf")

    return float(value)


def composite_score(trade):

    return (

        0.40 * _value(trade.signal_score)

        + 0.30 * _value(trade.relative_strength)

        + 0.15 * _value(trade.adx)

        + 0.15 * _value(trade.volume_ratio)

    )


def _percentile_scores(candidates, attribute):
    """Return 0..1 cross-sectional ranks without mixing raw scales."""
    values = [
        _value(getattr(candidate, attribute, None))
        for candidate in candidates
    ]
    ordered = sorted(range(len(values)), key=values.__getitem__)
    denominator = max(len(ordered) - 1, 1)
    result = [0.0] * len(ordered)
    position = 0
    while position < len(ordered):
        end = position + 1
        while (
            end < len(ordered)
            and values[ordered[end]] == values[ordered[position]]
        ):
            end += 1
        average_rank = (position + end - 1) / 2.0
        for ordered_position in range(position, end):
            result[ordered[ordered_position]] = average_rank / denominator
        position = end
    return result


def cross_sectional_leadership_scores(candidates):
    """Balanced momentum/quality score for candidates on the same day."""
    candidates = list(candidates)
    if not candidates:
        return []

    weights = {
        "signal_score": 0.35,
        "relative_strength": 0.40,
        "adx": 0.15,
        "volume_ratio": 0.10,
    }
    scores = [0.0] * len(candidates)
    for attribute, weight in weights.items():
        percentiles = _percentile_scores(
            candidates,
            attribute,
        )
        for index, percentile in enumerate(percentiles):
            scores[index] += weight * percentile
    return scores


def rank_candidates(

    candidates,

    method: RankingMethod,

):

    candidates = list(candidates)

    if method == RankingMethod.FIRST_COME:
        return candidates

    if method == RankingMethod.SIGNAL_SCORE:
        return sorted(
            candidates,
            key=lambda x: _value(
                x.signal_score,
            ),
            reverse=True,
        )

    if method == RankingMethod.RELATIVE_STRENGTH:
        return sorted(
            candidates,
            key=lambda x: _value(
                x.relative_strength,
            ),
            reverse=True,
        )

    if method == RankingMethod.ADX:
        return sorted(
            candidates,
            key=lambda x: _value(
                x.adx,
            ),
            reverse=True,
        )

    if method == RankingMethod.VOLUME_RATIO:
        return sorted(
            candidates,
            key=lambda x: _value(
                x.volume_ratio,
            ),
            reverse=True,
        )

    if method == RankingMethod.COMPOSITE:
        return sorted(
            candidates,
            key=composite_score,
            reverse=True,
        )

    if method == RankingMethod.CROSS_SECTIONAL_LEADERSHIP:
        scores = cross_sectional_leadership_scores(candidates)
        return [
            candidate
            for _, candidate in sorted(
                zip(scores, candidates),
                key=lambda item: item[0],
                reverse=True,
            )
        ]

    return candidates

