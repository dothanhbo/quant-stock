import pytest

from backtesting.portfolio_heat import (
    PortfolioHeat,
    PositionRisk,
)


def make_risk(
    *,
    symbol: str,
    risk_amount: float,
    equity: float = 100_000_000,
) -> PositionRisk:
    return PositionRisk.from_prices(
        symbol=symbol,
        entry_price=100_000,
        stop_price=(
            100_000
            - risk_amount / 100
        ),
        quantity=100,
        portfolio_equity=equity,
    )


def test_position_risk_from_prices():
    risk = PositionRisk.from_prices(
        symbol="hpg",
        entry_price=30_000,
        stop_price=28_000,
        quantity=500,
        portfolio_equity=100_000_000,
    )

    assert risk.symbol == "HPG"
    assert risk.risk_per_share == 2_000
    assert risk.risk_amount == 1_000_000
    assert risk.risk_pct == pytest.approx(
        1.0
    )


def test_snapshot_multiple_positions():
    heat = PortfolioHeat(
        max_heat_pct=5.0
    )

    snapshot = heat.snapshot(
        portfolio_equity=100_000_000,
        position_risks=[
            make_risk(
                symbol="HPG",
                risk_amount=1_000_000,
            ),
            make_risk(
                symbol="FPT",
                risk_amount=1_500_000,
            ),
        ],
    )

    assert snapshot.positions == 2
    assert snapshot.current_heat_amount == pytest.approx(
        2_500_000
    )
    assert snapshot.current_heat_pct == pytest.approx(
        2.5
    )
    assert snapshot.available_heat_pct == pytest.approx(
        2.5
    )
    assert snapshot.available_heat_amount == pytest.approx(
        2_500_000
    )


def test_decision_allows_trade_within_limit():
    heat = PortfolioHeat(
        max_heat_pct=5.0
    )

    decision = heat.decide(
        portfolio_equity=100_000_000,
        position_risks=[
            make_risk(
                symbol="HPG",
                risk_amount=2_000_000,
            ),
        ],
        proposed_risk=make_risk(
            symbol="FPT",
            risk_amount=1_000_000,
        ),
    )

    assert decision.allowed is True
    assert decision.reason == "within_heat_limit"
    assert decision.projected_heat_pct == pytest.approx(
        3.0
    )


def test_decision_rejects_trade_above_limit():
    heat = PortfolioHeat(
        max_heat_pct=5.0
    )

    decision = heat.decide(
        portfolio_equity=100_000_000,
        position_risks=[
            make_risk(
                symbol="HPG",
                risk_amount=4_000_000,
            ),
        ],
        proposed_risk=make_risk(
            symbol="FPT",
            risk_amount=1_500_000,
        ),
    )

    assert decision.allowed is False
    assert decision.reason == "portfolio_heat_limit"
    assert decision.projected_heat_pct == pytest.approx(
        5.5
    )


def test_decision_allows_exact_limit():
    heat = PortfolioHeat(
        max_heat_pct=5.0
    )

    decision = heat.decide(
        portfolio_equity=100_000_000,
        position_risks=[
            make_risk(
                symbol="HPG",
                risk_amount=4_000_000,
            ),
        ],
        proposed_risk=make_risk(
            symbol="FPT",
            risk_amount=1_000_000,
        ),
    )

    assert decision.allowed is True
    assert decision.projected_heat_pct == pytest.approx(
        5.0
    )


def test_disabled_heat_limit_allows_trade():
    heat = PortfolioHeat(
        max_heat_pct=None
    )

    decision = heat.decide(
        portfolio_equity=100_000_000,
        position_risks=[
            make_risk(
                symbol="HPG",
                risk_amount=8_000_000,
            ),
        ],
        proposed_risk=make_risk(
            symbol="FPT",
            risk_amount=4_000_000,
        ),
    )

    assert decision.allowed is True
    assert decision.reason == "heat_limit_disabled"
    assert decision.max_heat_pct is None


def test_empty_portfolio_has_zero_heat():
    heat = PortfolioHeat(
        max_heat_pct=5.0
    )

    snapshot = heat.snapshot(
        portfolio_equity=100_000_000,
        position_risks=[],
    )

    assert snapshot.positions == 0
    assert snapshot.current_heat_amount == 0
    assert snapshot.current_heat_pct == 0
    assert snapshot.available_heat_pct == pytest.approx(
        5.0
    )


@pytest.mark.parametrize(
    (
        "entry_price",
        "stop_price",
        "quantity",
        "equity",
    ),
    [
        (0, 90, 100, 100_000),
        (100, 0, 100, 100_000),
        (100, 90, 0, 100_000),
        (100, 90, 100, 0),
    ],
)
def test_position_risk_validates_inputs(
    entry_price,
    stop_price,
    quantity,
    equity,
):
    with pytest.raises(
        ValueError
    ):
        PositionRisk.from_prices(
            symbol="HPG",
            entry_price=entry_price,
            stop_price=stop_price,
            quantity=quantity,
            portfolio_equity=equity,
        )
