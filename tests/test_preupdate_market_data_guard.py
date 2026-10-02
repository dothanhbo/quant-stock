from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

from quantlab.preupdate_market_data_guard import (
    AdjustmentBasis,
    BoundaryEvidence,
    CoverageMetadata,
    CoverageState,
    EvidenceKind,
    GuardDecision,
    GuardReason,
    PriceBasisMetadata,
    PriceBasisState,
    RevisionClaim,
    RevisionEvidence,
    evaluate_preupdate_market_data,
)


START = date(2026, 9, 1)


def _row(day: int, close: float | None = None) -> dict[str, object]:
    base = 100.0 + day
    close_value = base + 0.5 if close is None else close
    return {
        "symbol": "AAA",
        "time": (START + timedelta(days=day)).isoformat(),
        "open": base,
        "high": max(base + 1.0, close_value),
        "low": min(base - 1.0, close_value),
        "close": close_value,
        "volume": 1_000.0 + day,
    }


def _basis(
    adjustment: AdjustmentBasis = AdjustmentBasis.RAW,
    state: PriceBasisState = PriceBasisState.VERIFIED,
) -> PriceBasisMetadata:
    return PriceBasisMetadata(
        "provider_native",
        adjustment,
        state,
        ("provider-contract:v1",) if state is PriceBasisState.VERIFIED else (),
    )


def _coverage(start_day: int, end_day: int) -> CoverageMetadata:
    return CoverageMetadata(
        START + timedelta(days=start_day),
        START + timedelta(days=end_day),
        START + timedelta(days=start_day),
        START + timedelta(days=end_day),
        CoverageState.VERIFIED_COMPLETE,
        ("request-response-hash:fixture",),
    )


def _evaluate(
    existing: list[dict[str, object]],
    incoming: list[dict[str, object]],
    *,
    start_day: int,
    end_day: int,
    **kwargs,
):
    return evaluate_preupdate_market_data(
        existing,
        incoming,
        symbol="AAA",
        requested_start=START + timedelta(days=start_day),
        requested_end=START + timedelta(days=end_day),
        coverage=_coverage(start_day, end_day),
        existing_price_basis=_basis(),
        incoming_price_basis=_basis(),
        **kwargs,
    )


def test_new_daily_row_passes_with_sufficient_unchanged_overlap() -> None:
    existing = [_row(day) for day in range(4)]
    incoming = [_row(day) for day in range(2, 5)]

    result = _evaluate(existing, incoming, start_day=2, end_day=4)

    assert result.decision is GuardDecision.PASS
    assert result.overlap_dates == (START + timedelta(days=2), START + timedelta(days=3))
    assert result.genuinely_new_dates == (START + timedelta(days=4),)
    assert GuardReason.BATCH_SAFE in result.reasons
    assert GuardReason.WHOLE_HISTORY_NOT_CERTIFIED in result.reasons


def test_identical_overlap_is_an_idempotent_pass() -> None:
    existing = [_row(day) for day in range(4)]
    incoming = [_row(day) for day in range(2, 4)]

    first = _evaluate(existing, incoming, start_day=2, end_day=3)
    retry = _evaluate(existing, incoming, start_day=2, end_day=3)

    assert first == retry
    assert first.decision is GuardDecision.PASS
    assert first.revisions == ()
    assert GuardReason.IDENTICAL_OVERLAP in first.reasons


def test_small_exactly_attributed_correction_passes_and_preserves_values() -> None:
    existing = [_row(day) for day in range(4)]
    incoming = [_row(day) for day in range(2, 5)]
    old_close = float(incoming[0]["close"])
    incoming[0]["close"] = old_close + 0.1
    evidence = RevisionEvidence(
        "AAA",
        EvidenceKind.PROVIDER_CORRECTION,
        (RevisionClaim(START + timedelta(days=2), "close", old_close, old_close + 0.1),),
        ("provider-correction-ticket:fixture",),
    )

    result = _evaluate(
        existing,
        incoming,
        start_day=2,
        end_day=4,
        revision_evidence=(evidence,),
    )

    assert result.decision is GuardDecision.PASS
    assert result.revisions[0].existing_value == old_close
    assert result.revisions[0].incoming_value == old_close + 0.1
    assert GuardReason.ATTRIBUTED_HISTORICAL_REVISION in result.reasons


def test_historical_price_revision_with_unknown_cause_blocks() -> None:
    existing = [_row(day) for day in range(4)]
    incoming = [_row(day) for day in range(2, 5)]
    incoming[0]["close"] = float(incoming[0]["close"]) + 0.1

    result = _evaluate(existing, incoming, start_day=2, end_day=4)

    assert result.decision is GuardDecision.BLOCK
    assert GuardReason.UNATTRIBUTED_HISTORICAL_REVISION in result.reasons


def test_synthetic_mixed_adjustment_overlap_blocks_without_rewriting_history() -> None:
    existing = [_row(day) for day in range(4)]
    incoming = [_row(day) for day in range(2, 5)]
    for row in incoming:
        for field in ("open", "high", "low", "close"):
            row[field] = float(row[field]) / 2.0

    result = _evaluate(existing, incoming, start_day=2, end_day=4)

    assert result.decision is GuardDecision.BLOCK
    assert len(result.revisions) == 8
    assert {change.session for change in result.revisions} == {
        START + timedelta(days=2),
        START + timedelta(days=3),
    }
    assert GuardReason.UNATTRIBUTED_HISTORICAL_REVISION in result.reasons


def test_missing_overlap_is_insufficient_evidence_not_a_pass() -> None:
    existing = [_row(day) for day in range(4)]
    incoming = [_row(4)]

    result = _evaluate(existing, incoming, start_day=4, end_day=4)

    assert result.decision is GuardDecision.INSUFFICIENT_EVIDENCE
    assert result.overlap_size == 0
    assert GuardReason.MISSING_OVERLAP in result.reasons


def test_single_overlap_session_is_an_insufficient_anchor() -> None:
    existing = [_row(day) for day in range(4)]
    incoming = [_row(day) for day in range(3, 5)]

    result = _evaluate(existing, incoming, start_day=3, end_day=4)

    assert result.decision is GuardDecision.INSUFFICIENT_EVIDENCE
    assert result.overlap_size == 1
    assert GuardReason.INSUFFICIENT_HISTORICAL_ANCHORS in result.reasons


def test_invalid_incoming_ohlcv_blocks() -> None:
    existing = [_row(day) for day in range(4)]
    incoming = [_row(day) for day in range(2, 5)]
    incoming[0]["high"] = float(incoming[0]["low"]) - 1.0

    result = _evaluate(existing, incoming, start_day=2, end_day=4)

    assert result.decision is GuardDecision.BLOCK
    assert GuardReason.INVALID_INCOMING_OHLCV in result.reasons


def test_unknown_price_basis_cannot_be_promoted_by_unchanged_overlap() -> None:
    existing = [_row(day) for day in range(4)]
    incoming = [_row(day) for day in range(2, 5)]

    result = evaluate_preupdate_market_data(
        existing,
        incoming,
        symbol="AAA",
        requested_start=START + timedelta(days=2),
        requested_end=START + timedelta(days=4),
        coverage=_coverage(2, 4),
        existing_price_basis=_basis(state=PriceBasisState.UNKNOWN),
        incoming_price_basis=_basis(state=PriceBasisState.UNKNOWN),
    )

    assert result.decision is GuardDecision.INSUFFICIENT_EVIDENCE
    assert GuardReason.PRICE_BASIS_UNVERIFIED in result.reasons
    assert GuardReason.WHOLE_HISTORY_NOT_CERTIFIED in result.reasons


def test_large_daily_movement_is_not_assumed_to_be_a_corporate_action() -> None:
    existing = [_row(day) for day in range(4)]
    incoming = [_row(day) for day in range(2, 5)]
    incoming[-1].update({"open": 52.0, "high": 53.0, "low": 51.0, "close": 52.5})

    result = _evaluate(existing, incoming, start_day=2, end_day=4)

    assert result.decision is GuardDecision.INSUFFICIENT_EVIDENCE
    assert result.boundary is not None
    assert result.boundary.attributable is False
    assert GuardReason.BOUNDARY_DISCONTINUITY_UNEXPLAINED in result.reasons


def test_exact_boundary_event_evidence_can_attribute_but_does_not_adjust_values() -> None:
    existing = [_row(day) for day in range(4)]
    incoming = [_row(day) for day in range(2, 5)]
    incoming[-1].update({"open": 52.0, "high": 53.0, "low": 51.0, "close": 52.5})
    event = BoundaryEvidence(
        "AAA",
        EvidenceKind.CORPORATE_ACTION_EVENT,
        START + timedelta(days=3),
        START + timedelta(days=4),
        float(existing[-1]["close"]),
        52.0,
        ("exchange-event:fixture",),
    )

    result = _evaluate(
        existing,
        incoming,
        start_day=2,
        end_day=4,
        boundary_evidence=(event,),
    )

    assert result.decision is GuardDecision.PASS
    assert result.boundary is not None and result.boundary.attributable is True
    assert GuardReason.ATTRIBUTED_BOUNDARY_EVENT in result.reasons


def test_block_never_mutates_persisted_or_candidate_values() -> None:
    existing = [_row(day) for day in range(4)]
    incoming = [_row(day) for day in range(2, 5)]
    incoming[0]["open"] = float(incoming[0]["open"]) + 0.25
    before_existing = deepcopy(existing)
    before_incoming = deepcopy(incoming)

    result = _evaluate(existing, incoming, start_day=2, end_day=4)

    assert result.decision is GuardDecision.BLOCK
    assert existing == before_existing
    assert incoming == before_incoming


def test_seven_unchanged_overlap_sessions_do_not_certify_whole_history() -> None:
    existing = [_row(day) for day in range(8)]
    incoming = [_row(day) for day in range(1, 9)]

    result = _evaluate(existing, incoming, start_day=1, end_day=8)

    assert result.decision is GuardDecision.PASS
    assert result.overlap_size == 7
    assert GuardReason.WHOLE_HISTORY_NOT_CERTIFIED in result.reasons
