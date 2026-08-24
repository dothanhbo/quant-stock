from types import SimpleNamespace

from backtesting.ranking import (
    RankingMethod,
    parse_ranking_method,
    rank_candidates,
)


def candidate(symbol, score, rs, adx, volume):
    return SimpleNamespace(
        symbol=symbol,
        signal_score=score,
        relative_strength=rs,
        adx=adx,
        volume_ratio=volume,
    )


def test_cross_sectional_leadership_uses_balanced_percentile_ranks() -> None:
    candidates = [
        candidate("SCORE", 99, 0, 10, 1.0),
        candidate("LEADER", 80, 12, 35, 2.0),
        candidate("MIDDLE", 85, 5, 20, 1.2),
    ]

    ranked = rank_candidates(
        candidates,
        RankingMethod.CROSS_SECTIONAL_LEADERSHIP,
    )

    assert [item.symbol for item in ranked] == [
        "LEADER",
        "MIDDLE",
        "SCORE",
    ]


def test_cross_sectional_leadership_parser_supports_cli_name() -> None:
    assert parse_ranking_method("cross_sectional_leadership") is (
        RankingMethod.CROSS_SECTIONAL_LEADERSHIP
    )
