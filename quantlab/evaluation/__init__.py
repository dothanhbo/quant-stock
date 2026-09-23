"""Pure descriptive evaluation of candidate factors against future labels."""

from .contracts import (
    CandidateFactorOutcomeEvaluationResult,
    DailyFactorOutcomeEvaluation,
    DESCRIPTIVE_WARNING,
    FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1,
    FactorHorizonEvaluationSummary,
    FactorOutcomeEvaluationSpec,
)
from .factor_outcomes import evaluate_candidate_factor_outcomes

__all__ = [
    "CandidateFactorOutcomeEvaluationResult",
    "DailyFactorOutcomeEvaluation",
    "DESCRIPTIVE_WARNING",
    "FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1",
    "FactorHorizonEvaluationSummary",
    "FactorOutcomeEvaluationSpec",
    "evaluate_candidate_factor_outcomes",
]
