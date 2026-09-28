"""Neutral execution-research contracts; no operational broker integration."""

from .foundation import (
    CAPABILITY_MATRIX,
    NEUTRAL_EXECUTION_FOUNDATION_V1,
    NEUTRAL_EXECUTION_FRICTION_V1,
    CapabilityState,
    ConstraintImplementationState,
    ExecutionCapability,
    ExecutionFoundationResult,
    ExecutionFrictionComponent,
    ExecutionFrictionSpec,
    ExecutionSpec,
    FrictionProvenance,
    OrderIntentState,
    OrderSide,
    ParticipationState,
    TargetToOrderResult,
    TheoreticalOrderIntent,
    VolumeParticipationDiagnostic,
    build_execution_foundation_result,
    describe_volume_participation,
    translate_target_portfolio,
)

__all__ = (
    "CAPABILITY_MATRIX", "NEUTRAL_EXECUTION_FOUNDATION_V1", "NEUTRAL_EXECUTION_FRICTION_V1",
    "CapabilityState", "ConstraintImplementationState", "ExecutionCapability", "ExecutionFoundationResult",
    "ExecutionFrictionComponent", "ExecutionFrictionSpec", "ExecutionSpec", "FrictionProvenance",
    "OrderIntentState", "OrderSide", "ParticipationState", "TargetToOrderResult", "TheoreticalOrderIntent",
    "VolumeParticipationDiagnostic", "build_execution_foundation_result", "describe_volume_participation",
    "translate_target_portfolio",
)
