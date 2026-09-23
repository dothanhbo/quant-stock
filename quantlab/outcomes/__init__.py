"""Offline future-outcome labels for immutable Quant Lab research candidates."""

from .contracts import (
    CandidateForwardOutcome,
    CandidateForwardOutcomeSet,
    FORWARD_CLOSE_RETURNS_5_10_20_V1,
    ForwardOutcomeSpec,
    ForwardOutcomeStatus,
)
from .forward_returns import label_candidate_forward_outcomes

__all__ = [
    "CandidateForwardOutcome",
    "CandidateForwardOutcomeSet",
    "FORWARD_CLOSE_RETURNS_5_10_20_V1",
    "ForwardOutcomeSpec",
    "ForwardOutcomeStatus",
    "label_candidate_forward_outcomes",
]
