from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from quantlab.execution import (
    PARTICIPATION_INTERPRETATION,
    CapacityEvidenceState,
    DailyExecutionMarketEvidence,
    FrozenExecutionTargetPortfolio,
    FrozenExecutionTargetPosition,
    OrderSide,
    TimingEvidenceState,
    adverse_direction_gap,
    evaluate_execution_timing_capacity,
)
from quantlab.execution.timing_capacity import (
    EXPECTED_PHASE11A_FRICTION,
    EXPECTED_PHASE11A_RESULT,
    EXPECTED_PHASE11A_SPEC,
    EXPECTED_PHASE6_RESULT,
    EXPECTED_PHASE6_SPEC,
)


def _sources() -> dict[str, str]:
    return {
        "phase11a_result_identity": EXPECTED_PHASE11A_RESULT,
        "phase11a_specification_fingerprint": EXPECTED_PHASE11A_SPEC,
        "phase11a_friction_fingerprint": EXPECTED_PHASE11A_FRICTION,
        "phase6_result_identity": EXPECTED_PHASE6_RESULT,
        "phase6_specification_fingerprint": EXPECTED_PHASE6_SPEC,
    }


def _portfolio(
    *, formation_date: str = "2024-01-02", budget: int = 5,
    positions: tuple[tuple[str, float], ...] = (("AAA", .2),),
) -> FrozenExecutionTargetPortfolio:
    return FrozenExecutionTargetPortfolio(
        formation_date,
        budget,
        tuple(FrozenExecutionTargetPosition(symbol, weight, f"position-{symbol}") for symbol, weight in positions),
        f"portfolio-{formation_date}-{budget}",
    )


def _market(
    *, formation_close: float | None = 10.0,
    next_open: float | None = 11.0,
    next_volume: float | None = 1_000.0,
    t2_open: float | None = 999.0,
    include_next_symbol_row: bool = True,
) -> tuple[DailyExecutionMarketEvidence, ...]:
    rows = [
        DailyExecutionMarketEvidence("VNINDEX", "2024-01-02", 1_000.0, 1_001.0, 10_000.0),
        DailyExecutionMarketEvidence("VNINDEX", "2024-01-03", 1_002.0, 1_003.0, 11_000.0),
        DailyExecutionMarketEvidence("VNINDEX", "2024-01-04", 1_004.0, 1_005.0, 12_000.0),
        DailyExecutionMarketEvidence("AAA", "2024-01-02", 9.5, formation_close, 800.0),
        DailyExecutionMarketEvidence("AAA", "2024-01-04", t2_open, 12.0, 2_000.0),
    ]
    if include_next_symbol_row:
        rows.append(DailyExecutionMarketEvidence("AAA", "2024-01-03", next_open, 10.5, next_volume))
    return tuple(rows)


def _evaluate(
    *, portfolios: tuple[FrozenExecutionTargetPortfolio, ...] | None = None,
    market: tuple[DailyExecutionMarketEvidence, ...] | None = None,
):
    return evaluate_execution_timing_capacity(
        portfolios or (_portfolio(),),
        market or _market(),
        source_identities=_sources(),
        market_snapshot_identity="snapshot",
        market_logical_content_fingerprint="logical",
    )


def test_exact_next_vnindex_session_gap_and_capacity_formula() -> None:
    observation = _evaluate().observations[0]
    assert observation.next_market_session == "2024-01-03"
    assert observation.next_session_open_reference == 11.0
    assert observation.reference_to_next_open_gap_decimal == pytest.approx(.1)
    assert observation.adverse_direction_gap_decimal == pytest.approx(.1)
    assert observation.side is OrderSide.BUY
    assert observation.theoretical_acquisition_shares_per_equity_unit == pytest.approx(.02)
    assert observation.participation_pct_per_normalized_equity_unit == pytest.approx(.002)
    assert observation.participation_interpretation == PARTICIPATION_INTERPRETATION


def test_no_t2_fallback_and_t2_mutation_cannot_change_t1_evidence() -> None:
    missing = _evaluate(market=_market(include_next_symbol_row=False)).observations[0]
    assert missing.next_market_session == "2024-01-03"
    assert missing.timing_state is TimingEvidenceState.MISSING_NEXT_OPEN
    assert missing.capacity_state is CapacityEvidenceState.MISSING_VOLUME
    assert missing.next_session_open_reference is None
    first = _evaluate(market=_market(t2_open=12.0)).observations[0]
    second = _evaluate(market=_market(t2_open=1_200.0)).observations[0]
    assert first == second


@pytest.mark.parametrize(
    ("next_open", "volume", "timing", "capacity"),
    [
        (None, 1_000.0, TimingEvidenceState.MISSING_NEXT_OPEN, CapacityEvidenceState.AVAILABLE),
        (11.0, None, TimingEvidenceState.AVAILABLE, CapacityEvidenceState.MISSING_VOLUME),
        (11.0, 0.0, TimingEvidenceState.AVAILABLE, CapacityEvidenceState.NONPOSITIVE_VOLUME),
    ],
)
def test_missing_timing_and_capacity_evidence_are_independent_and_explicit(
    next_open, volume, timing, capacity,
) -> None:
    observation = _evaluate(market=_market(next_open=next_open, next_volume=volume)).observations[0]
    assert observation.timing_state is timing
    assert observation.capacity_state is capacity
    assert (observation.reference_to_next_open_gap_decimal is None) is (timing is not TimingEvidenceState.AVAILABLE)
    assert (observation.participation_pct_per_normalized_equity_unit is None) is (capacity is not CapacityEvidenceState.AVAILABLE)


def test_nonpositive_prices_and_absent_next_session_fail_closed() -> None:
    bad_formation = _evaluate(market=_market(formation_close=0.0)).observations[0]
    bad_open = _evaluate(market=_market(next_open=0.0)).observations[0]
    assert bad_formation.timing_state is TimingEvidenceState.NONPOSITIVE_PRICE
    assert bad_formation.capacity_state is CapacityEvidenceState.ORDER_INTENT_UNAVAILABLE
    assert bad_open.timing_state is TimingEvidenceState.NONPOSITIVE_PRICE
    market = tuple(row for row in _market() if row.session_date <= "2024-01-02")
    no_next = _evaluate(market=market).observations[0]
    assert no_next.timing_state is TimingEvidenceState.NO_NEXT_MARKET_SESSION
    assert no_next.capacity_state is CapacityEvidenceState.NO_NEXT_MARKET_SESSION


def test_buy_and_sell_adverse_direction_semantics_are_signed_and_untruncated() -> None:
    assert adverse_direction_gap(OrderSide.BUY, .03) == pytest.approx(.03)
    assert adverse_direction_gap(OrderSide.BUY, -.03) == pytest.approx(-.03)
    assert adverse_direction_gap(OrderSide.SELL, .03) == pytest.approx(-.03)
    assert adverse_direction_gap(OrderSide.SELL, -.03) == pytest.approx(.03)


def test_phase11a_translation_is_reused_once_per_target_portfolio(monkeypatch: pytest.MonkeyPatch) -> None:
    import quantlab.execution.timing_capacity as module

    original = module.translate_target_portfolio
    calls: list[dict] = []

    def wrapped(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(module, "translate_target_portfolio", wrapped)
    portfolios = (
        _portfolio(budget=5, positions=(("AAA", .2), ("BBB", .3))),
        _portfolio(budget=10, positions=(("AAA", .1),)),
    )
    market = _market() + (
        DailyExecutionMarketEvidence("BBB", "2024-01-02", 20.0, 20.0, 500.0),
        DailyExecutionMarketEvidence("BBB", "2024-01-03", 21.0, 21.0, 600.0),
    )
    result = _evaluate(portfolios=portfolios, market=market)
    assert len(calls) == 2
    assert len(result.observations) == 3
    assert all(call["current_holdings"] == {} for call in calls)
    assert all(call["portfolio_equity"] == 1.0 and call["current_cash"] == 1.0 for call in calls)


def test_fractional_theoretical_shares_are_not_lot_rounded_and_no_cost_or_fill_fields_exist() -> None:
    observation = _evaluate(portfolios=(_portfolio(positions=(("AAA", .333333),)),)).observations[0]
    assert observation.theoretical_acquisition_shares_per_equity_unit == pytest.approx(.0333333)
    names = set(observation.__dataclass_fields__)
    assert not names.intersection({
        "fill_price", "execution_price", "commission", "tax", "slippage",
        "market_impact", "filled_quantity", "partial_fill",
    })
    assert "target acquisition" in _evaluate().limitations[0].lower()


def test_deterministic_order_identity_summaries_and_immutability() -> None:
    portfolios = (
        _portfolio(budget=10, positions=(("BBB", .2), ("AAA", .1))),
        _portfolio(budget=5),
    )
    market = _market() + (
        DailyExecutionMarketEvidence("BBB", "2024-01-02", 20.0, 20.0, 500.0),
        DailyExecutionMarketEvidence("BBB", "2024-01-03", 21.0, 21.0, 600.0),
    )
    first = _evaluate(portfolios=portfolios, market=market)
    second = _evaluate(portfolios=tuple(reversed(portfolios)), market=tuple(reversed(market)))
    assert first == second
    assert [(item.requested_budget, item.symbol) for item in first.observations] == [(5, "AAA"), (10, "AAA"), (10, "BBB")]
    assert len(first.summaries) == 3
    assert len(first.block_summaries) == 12
    with pytest.raises(FrozenInstanceError):
        first.observations[0].target_weight = .5  # type: ignore[misc]
    with pytest.raises(TypeError):
        first.source_identities["other"] = "value"  # type: ignore[index]


def test_upstream_provenance_mismatch_fails_closed() -> None:
    sources = _sources(); sources["phase6_result_identity"] = "wrong"
    with pytest.raises(ValueError, match="provenance mismatch"):
        evaluate_execution_timing_capacity(
            (_portfolio(),), _market(), source_identities=sources,
            market_snapshot_identity="snapshot", market_logical_content_fingerprint="logical",
        )


def test_exact_t1_volume_is_used_and_participation_is_descriptive_not_a_fill_rule() -> None:
    first = _evaluate(market=_market(next_volume=1_000.0)).observations[0]
    second = _evaluate(market=_market(next_volume=2_000.0)).observations[0]
    assert first.participation_pct_per_normalized_equity_unit == pytest.approx(.002)
    assert second.participation_pct_per_normalized_equity_unit == pytest.approx(.001)
    assert first.timing_state is second.timing_state is TimingEvidenceState.AVAILABLE
    assert "NOT_FILL_PROBABILITY" in first.participation_interpretation
    assert not hasattr(first, "fill_probability")

