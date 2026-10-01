from __future__ import annotations

from quantlab.forward.contracts import AuditEventType, ForwardAuditEvent
from quantlab.forward.semantics import reconcile_missing_formations


def test_missing_forward_formations_are_acknowledged_unrecoverable_without_backfill() -> None:
    events = (
        ForwardAuditEvent(
            "protocol",
            AuditEventType.MISSING_FORMATION,
            "2026-01-05",
            "COMPLETED_SESSION_HAS_NO_FORMATION_RECORD",
            "2026-01-06T00:00:00Z",
            "event-1",
        ),
    )

    reconciled = reconcile_missing_formations(events)

    assert len(reconciled) == 1
    assert reconciled[0].resolution.value == "ACKNOWLEDGED_UNRECOVERABLE"
    assert reconciled[0].action.startswith("DO_NOT_BACKFILL")
    assert reconciled[0].event_identity == "event-1"


def test_reconciliation_is_deterministic_and_ignores_non_gap_events() -> None:
    events = (
        ForwardAuditEvent("p", AuditEventType.MISSING_FORMATION, "2026-01-07", "gap", "t", "b"),
        ForwardAuditEvent("p", AuditEventType.MISSING_FORMATION, "2026-01-05", "gap", "t", "a"),
    )

    first = reconcile_missing_formations(events)
    second = reconcile_missing_formations(tuple(reversed(events)))

    assert first == second
    assert tuple(item.market_session for item in first) == ("2026-01-05", "2026-01-07")
