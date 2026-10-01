"""Prospective, append-only forward-validation infrastructure."""

from .contracts import (
    AuditEventType,
    ForwardAuditEvent,
    ForwardGapReconciliation,
    ForwardGapResolution,
    ForwardFormation,
    ForwardDailyOperationResult,
    ForwardDailyOperationState,
    ForwardMaturity,
    ForwardOutcome,
    ForwardPosition,
    ForwardProtocolActivation,
    ForwardValidationProtocol,
    ForwardValidationStatus,
    MaturityStatus,
    OutcomeAvailability,
)
from .ledger import ForwardValidationLedger
from .daily import run_forward_validation_daily
from .protocol import create_activation, load_protocol_spec, verify_phase8_authorization
from .semantics import (
    build_forward_formation,
    build_forward_status,
    build_matured_outcomes,
    detect_missing_formations,
    reconcile_missing_formations,
    evaluate_formation_maturities,
)

__all__ = (
    "AuditEventType", "ForwardAuditEvent", "ForwardGapReconciliation", "ForwardGapResolution", "ForwardFormation",
    "ForwardDailyOperationResult", "ForwardDailyOperationState", "ForwardMaturity",
    "ForwardOutcome", "ForwardPosition", "ForwardProtocolActivation",
    "ForwardValidationProtocol", "ForwardValidationStatus", "MaturityStatus",
    "OutcomeAvailability", "ForwardValidationLedger", "create_activation",
    "load_protocol_spec", "verify_phase8_authorization", "build_forward_formation",
    "build_forward_status", "build_matured_outcomes", "detect_missing_formations", "reconcile_missing_formations",
    "evaluate_formation_maturities",
    "run_forward_validation_daily",
)
