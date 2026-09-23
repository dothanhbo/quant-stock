"""Outcome-free immutable diagnostics for Quant Lab research candidates."""

from .candidate_factors import FROZEN_Q70_CANDIDATE_DIAGNOSTICS_V1, diagnose_candidate_factors
from .contracts import (
    CandidateFactorDiagnosticsResult,
    CandidateFactorSet,
    DIAGNOSTIC_NUMERIC_FIELDS,
    FactorAggregateDiagnostics,
    FactorAssociationDiagnostics,
    FactorDateDiagnostics,
    FactorDateStabilityDiagnostics,
    FactorDescriptiveDiagnostics,
)

__all__ = [
    "CandidateFactorDiagnosticsResult",
    "CandidateFactorSet",
    "DIAGNOSTIC_NUMERIC_FIELDS",
    "FROZEN_Q70_CANDIDATE_DIAGNOSTICS_V1",
    "FactorAggregateDiagnostics",
    "FactorAssociationDiagnostics",
    "FactorDateDiagnostics",
    "FactorDateStabilityDiagnostics",
    "FactorDescriptiveDiagnostics",
    "diagnose_candidate_factors",
]
