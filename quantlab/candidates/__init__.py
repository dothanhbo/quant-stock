"""Immutable Quant Lab research candidates; no portfolio/execution behavior."""

from .contracts import FrozenQ70CandidateBatch, FrozenQ70CandidateRecord
from .frozen_q70 import candidate_batch_from_decisions

__all__ = [
    "FrozenQ70CandidateBatch",
    "FrozenQ70CandidateRecord",
    "candidate_batch_from_decisions",
]
