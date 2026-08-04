import pytest

from backtesting.regime_policy import (
    RegimePortfolioPolicy,
    RegimePortfolioRule,
    normalize_market_regime,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("bull", "BULL"),
        ("BULLISH", "BULL"),
        ("uptrend", "BULL"),
        ("sideway", "SIDEWAY"),
        ("sideways", "SIDEWAY"),
        ("neutral", "SIDEWAY"),
        ("bear", "BEAR"),
        ("bearish", "BEAR"),
        ("downtrend", "BEAR"),
        ("", "UNKNOWN"),
        (None, "UNKNOWN"),
        ("something_new", "UNKNOWN"),
    ],
)
def test_normalize_market_regime(
    raw,
    expected,
):
    assert (
        normalize_market_regime(raw)
        == expected
    )


def test_default_bull_rule():
    decision = (
        RegimePortfolioPolicy()
        .resolve("BULL")
    )

    assert decision.allow_new_positions
    assert decision.max_positions == 5
    assert (
        decision.max_portfolio_heat_pct
        == 5.0
    )


def test_default_sideway_rule():
    decision = (
        RegimePortfolioPolicy()
        .resolve("SIDEWAY")
    )

    assert decision.allow_new_positions
    assert decision.max_positions == 3
    assert (
        decision.max_portfolio_heat_pct
        == 4.0
    )


def test_default_bear_rule_blocks_entries():
    decision = (
        RegimePortfolioPolicy()
        .resolve("BEAR")
    )

    assert (
        decision.allow_new_positions
        is False
    )
    assert decision.max_positions == 0
    assert (
        decision.max_portfolio_heat_pct
        is None
    )


def test_unknown_regime_is_defensive():
    decision = (
        RegimePortfolioPolicy()
        .resolve("NEW_REGIME")
    )

    assert (
        decision.normalized_regime
        == "UNKNOWN"
    )
    assert (
        decision.allow_new_positions
        is False
    )


def test_custom_rules():
    policy = RegimePortfolioPolicy(
        rules={
            "BULL": RegimePortfolioRule(
                allow_new_positions=True,
                max_positions=4,
                max_portfolio_heat_pct=4.5,
            ),
            "SIDEWAY": RegimePortfolioRule(
                allow_new_positions=True,
                max_positions=2,
                max_portfolio_heat_pct=2.5,
            ),
            "BEAR": RegimePortfolioRule(
                allow_new_positions=True,
                max_positions=1,
                max_portfolio_heat_pct=1.0,
            ),
        }
    )

    assert (
        policy.resolve("bull")
        .max_positions
        == 4
    )
    assert (
        policy.resolve("bear")
        .allow_new_positions
        is True
    )


def test_rule_validates_negative_positions():
    with pytest.raises(ValueError):
        RegimePortfolioRule(
            allow_new_positions=False,
            max_positions=-1,
            max_portfolio_heat_pct=None,
        )


def test_enabled_rule_requires_position_slot():
    with pytest.raises(ValueError):
        RegimePortfolioRule(
            allow_new_positions=True,
            max_positions=0,
            max_portfolio_heat_pct=1.0,
        )


def test_rule_validates_heat():
    with pytest.raises(ValueError):
        RegimePortfolioRule(
            allow_new_positions=True,
            max_positions=1,
            max_portfolio_heat_pct=0.0,
        )
