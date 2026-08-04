from dataclasses import dataclass

import pytest

from backtesting.trade_risk import (
    TradeRiskMetadata,
    resolve_candidate_stop_price,
)


@dataclass
class CandidateStub:
    entry_price: float
    quantity: int
    stop_price: float | None = None
    atr: float | None = None


def test_build_trade_risk_metadata():
    metadata = TradeRiskMetadata.build(
        entry_price=30_000,
        stop_price=28_000,
        quantity=500,
        portfolio_equity=100_000_000,
    )

    assert metadata.stop_price == 28_000
    assert metadata.risk_per_share == 2_000
    assert metadata.risk_amount == 1_000_000
    assert metadata.risk_pct == pytest.approx(
        1.0
    )


def test_build_uses_absolute_distance():
    metadata = TradeRiskMetadata.build(
        entry_price=100,
        stop_price=105,
        quantity=100,
        portfolio_equity=100_000,
    )

    assert metadata.risk_per_share == 5
    assert metadata.risk_amount == 500
    assert metadata.risk_pct == pytest.approx(
        0.5
    )


def test_from_trade_reads_stop_price():
    candidate = CandidateStub(
        entry_price=100,
        stop_price=95,
        quantity=200,
    )

    metadata = TradeRiskMetadata.from_trade(
        candidate,
        portfolio_equity=100_000,
    )

    assert metadata.stop_price == 95
    assert metadata.risk_amount == 1_000


def test_resolve_prefers_direct_stop_price():
    candidate = CandidateStub(
        entry_price=100,
        stop_price=94,
        quantity=100,
        atr=3,
    )

    assert (
        resolve_candidate_stop_price(
            candidate,
            atr_stop_multiplier=2,
        )
        == 94
    )


def test_resolve_builds_atr_stop():
    candidate = CandidateStub(
        entry_price=100,
        quantity=100,
        atr=3,
    )

    assert (
        resolve_candidate_stop_price(
            candidate,
            atr_stop_multiplier=2,
        )
        == 94
    )


def test_resolve_returns_none_without_stop_data():
    candidate = CandidateStub(
        entry_price=100,
        quantity=100,
    )

    assert (
        resolve_candidate_stop_price(
            candidate
        )
        is None
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
def test_build_validates_inputs(
    entry_price,
    stop_price,
    quantity,
    equity,
):
    with pytest.raises(ValueError):
        TradeRiskMetadata.build(
            entry_price=entry_price,
            stop_price=stop_price,
            quantity=quantity,
            portfolio_equity=equity,
        )
