from __future__ import annotations

from enum import Enum


class RankingMethod(str, Enum):

    FIRST_COME = "first_come"

    SIGNAL_SCORE = "signal_score"

    RELATIVE_STRENGTH = "relative_strength"

    ADX = "adx"

    VOLUME_RATIO = "volume_ratio"

    COMPOSITE = "composite"

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

    return candidates

