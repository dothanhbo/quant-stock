from __future__ import annotations

"""Immutable future-label contracts for offline Quant Lab research only."""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from hashlib import sha256
import math
from types import MappingProxyType
from typing import Any, Mapping

from quantlab.identity import canonical_identity_value, canonical_json


OUTCOME_CONTRACT_NAME = "quantlab.candidate_forward_close_returns"
OUTCOME_CONTRACT_VERSION = "v1"
TARGET_SESSION_CONVENTION = "hth_benchmark_session_strictly_after_signal_v1"
RETURN_CONVENTION = "exact_close_to_close_percent_v1"


class ForwardOutcomeStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    CENSORED_AFTER_DATA_END = "CENSORED_AFTER_DATA_END"
    MISSING_SIGNAL_CLOSE = "MISSING_SIGNAL_CLOSE"
    MISSING_TARGET_CLOSE = "MISSING_TARGET_CLOSE"
    MISSING_BENCHMARK_SIGNAL_CLOSE = "MISSING_BENCHMARK_SIGNAL_CLOSE"
    MISSING_BENCHMARK_TARGET_CLOSE = "MISSING_BENCHMARK_TARGET_CLOSE"


def _date_text(value: str | date | datetime) -> str:
    text = value.date().isoformat() if isinstance(value, datetime) else (
        value.isoformat() if isinstance(value, date) else str(value)
    )
    date.fromisoformat(text)
    return text


@dataclass(frozen=True, slots=True)
class ForwardOutcomeSpec:
    name: str
    version: str
    horizons: tuple[int, ...]
    benchmark_symbol: str = "VNINDEX"
    target_session_convention: str = TARGET_SESSION_CONVENTION
    return_convention: str = RETURN_CONVENTION
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        name, version = str(self.name).strip(), str(self.version).strip()
        benchmark = str(self.benchmark_symbol).strip().upper()
        if not name or not version:
            raise ValueError("forward-outcome spec name and version are required")
        if not benchmark:
            raise ValueError("benchmark_symbol must be non-empty")
        raw_horizons = tuple(self.horizons)
        if not raw_horizons or any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in raw_horizons):
            raise ValueError("forward-outcome horizons must be positive integers")
        if len(set(raw_horizons)) != len(raw_horizons):
            raise ValueError("forward-outcome horizons must be unique")
        horizons = tuple(sorted(raw_horizons))
        if self.target_session_convention != TARGET_SESSION_CONVENTION:
            raise ValueError(f"unsupported target-session convention: {self.target_session_convention}")
        if self.return_convention != RETURN_CONVENTION:
            raise ValueError(f"unsupported return convention: {self.return_convention}")
        payload = {
            "name": name,
            "version": version,
            "horizons": list(horizons),
            "benchmark_symbol": benchmark,
            "target_session_convention": self.target_session_convention,
            "return_convention": self.return_convention,
        }
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "horizons", horizons)
        object.__setattr__(self, "benchmark_symbol", benchmark)
        object.__setattr__(self, "fingerprint", sha256(canonical_json(payload)).hexdigest())


FORWARD_CLOSE_RETURNS_5_10_20_V1 = ForwardOutcomeSpec(
    name="forward_close_returns_5_10_20",
    version="v1",
    horizons=(5, 10, 20),
    benchmark_symbol="VNINDEX",
)


@dataclass(frozen=True, slots=True)
class CandidateForwardOutcome:
    candidate_key: str
    symbol: str
    signal_date: str
    horizon_sessions: int
    target_market_session_date: str | None
    status: ForwardOutcomeStatus
    signal_close: float | None
    target_close: float | None
    stock_forward_return_pct: float | None
    benchmark_signal_close: float | None
    benchmark_target_close: float | None
    benchmark_forward_return_pct: float | None
    excess_forward_return_percentage_points: float | None
    source_candidate_batch_identity: str
    snapshot_id: str
    outcome_spec_fingerprint: str
    outcome_identity: str = field(init=False)

    def __post_init__(self) -> None:
        candidate_key = str(self.candidate_key).strip()
        symbol = str(self.symbol).strip().upper()
        if not candidate_key or not symbol:
            raise ValueError("outcome candidate key and symbol are required")
        if symbol == "VNINDEX":
            raise ValueError("benchmark cannot be labeled as an equity candidate")
        if isinstance(self.horizon_sessions, bool) or not isinstance(self.horizon_sessions, int) or self.horizon_sessions <= 0:
            raise ValueError("outcome horizon must be a positive integer")
        if not isinstance(self.status, ForwardOutcomeStatus):
            raise ValueError("status must be ForwardOutcomeStatus")
        for name in ("source_candidate_batch_identity", "snapshot_id", "outcome_spec_fingerprint"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be a non-empty string")
        signal_date = _date_text(self.signal_date)
        target_date = None if self.target_market_session_date is None else _date_text(self.target_market_session_date)
        numeric_names = (
            "signal_close", "target_close", "stock_forward_return_pct",
            "benchmark_signal_close", "benchmark_target_close",
            "benchmark_forward_return_pct", "excess_forward_return_percentage_points",
        )
        numeric: dict[str, float | None] = {}
        for name in numeric_names:
            value = getattr(self, name)
            if value is None:
                numeric[name] = None
                continue
            number = float(value)
            if not math.isfinite(number):
                raise ValueError(f"{name} must be finite or None")
            numeric[name] = number
        for name in ("signal_close", "target_close", "benchmark_signal_close", "benchmark_target_close"):
            if numeric[name] is not None and numeric[name] <= 0:
                raise ValueError(f"{name} must be positive or None")
        if self.status is ForwardOutcomeStatus.AVAILABLE and (
            target_date is None or any(numeric[name] is None for name in numeric_names)
        ):
            raise ValueError("available outcome requires complete dates, closes, and returns")
        payload = {
            "candidate_key": candidate_key,
            "symbol": symbol,
            "signal_date": signal_date,
            "horizon_sessions": self.horizon_sessions,
            "target_market_session_date": target_date,
            "status": self.status.value,
            **numeric,
            "source_candidate_batch_identity": self.source_candidate_batch_identity,
            "snapshot_id": self.snapshot_id,
            "outcome_spec_fingerprint": self.outcome_spec_fingerprint,
        }
        object.__setattr__(self, "candidate_key", candidate_key)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "signal_date", signal_date)
        object.__setattr__(self, "target_market_session_date", target_date)
        for name, value in numeric.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "outcome_identity", sha256(canonical_json(canonical_identity_value(payload))).hexdigest())

    def canonical_content(self) -> dict[str, Any]:
        return {
            "candidate_key": self.candidate_key,
            "symbol": self.symbol,
            "signal_date": self.signal_date,
            "horizon_sessions": self.horizon_sessions,
            "target_market_session_date": self.target_market_session_date,
            "status": self.status.value,
            "signal_close": self.signal_close,
            "target_close": self.target_close,
            "stock_forward_return_pct": self.stock_forward_return_pct,
            "benchmark_signal_close": self.benchmark_signal_close,
            "benchmark_target_close": self.benchmark_target_close,
            "benchmark_forward_return_pct": self.benchmark_forward_return_pct,
            "excess_forward_return_percentage_points": self.excess_forward_return_percentage_points,
            "source_candidate_batch_identity": self.source_candidate_batch_identity,
            "snapshot_id": self.snapshot_id,
            "outcome_spec_fingerprint": self.outcome_spec_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class CandidateForwardOutcomeSet:
    outcomes: tuple[CandidateForwardOutcome, ...]
    source_candidate_batch_identity: str
    snapshot_id: str
    outcome_spec_fingerprint: str
    requested_horizons: tuple[int, ...]
    contract_name: str = OUTCOME_CONTRACT_NAME
    contract_version: str = OUTCOME_CONTRACT_VERSION
    set_identity: str = field(init=False)
    status_counts: Mapping[ForwardOutcomeStatus, int] = field(init=False)
    status_counts_by_horizon: Mapping[int, Mapping[ForwardOutcomeStatus, int]] = field(init=False)
    _by_candidate: Mapping[str, tuple[CandidateForwardOutcome, ...]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (self.contract_name, self.contract_version) != (OUTCOME_CONTRACT_NAME, OUTCOME_CONTRACT_VERSION):
            raise ValueError("unsupported candidate-forward-outcome contract")
        for name in ("source_candidate_batch_identity", "snapshot_id", "outcome_spec_fingerprint"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be a non-empty string")
        horizons = tuple(sorted(tuple(self.requested_horizons)))
        if not horizons or len(set(horizons)) != len(horizons) or any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in horizons):
            raise ValueError("requested_horizons must be unique positive integers")
        ordered = tuple(sorted(
            tuple(self.outcomes),
            key=lambda item: (item.signal_date, item.symbol, item.candidate_key, item.horizon_sessions),
        ))
        grouped: dict[str, list[CandidateForwardOutcome]] = {}
        pair_keys: set[tuple[str, int]] = set()
        for item in ordered:
            expected = (self.source_candidate_batch_identity, self.snapshot_id, self.outcome_spec_fingerprint)
            actual = (item.source_candidate_batch_identity, item.snapshot_id, item.outcome_spec_fingerprint)
            if actual != expected:
                raise ValueError(f"outcome provenance mismatch: {item.candidate_key}")
            if item.horizon_sessions not in horizons:
                raise ValueError(f"unexpected outcome horizon: {item.horizon_sessions}")
            pair = (item.candidate_key, item.horizon_sessions)
            if pair in pair_keys:
                raise ValueError(f"duplicate candidate/horizon outcome: {item.candidate_key}/{item.horizon_sessions}")
            pair_keys.add(pair)
            grouped.setdefault(item.candidate_key, []).append(item)
        for candidate_key, items in grouped.items():
            if tuple(item.horizon_sessions for item in items) != horizons:
                raise ValueError(f"incomplete candidate/horizon outcomes: {candidate_key}")
        if len(ordered) != len(grouped) * len(horizons):
            raise ValueError("outcome cardinality must equal candidate_count × horizon_count")
        all_statuses = tuple(ForwardOutcomeStatus)
        status_counts = MappingProxyType({
            status: sum(item.status is status for item in ordered)
            for status in all_statuses
        })
        by_horizon = MappingProxyType({
            horizon: MappingProxyType({
                status: sum(item.horizon_sessions == horizon and item.status is status for item in ordered)
                for status in all_statuses
            })
            for horizon in horizons
        })
        payload = {
            "contract": {"name": self.contract_name, "version": self.contract_version},
            "source_candidate_batch_identity": self.source_candidate_batch_identity,
            "snapshot_id": self.snapshot_id,
            "outcome_spec_fingerprint": self.outcome_spec_fingerprint,
            "requested_horizons": list(horizons),
            "outcomes": [item.canonical_content() for item in ordered],
            "status_counts": {status.value: status_counts[status] for status in all_statuses},
            "status_counts_by_horizon": {
                str(horizon): {status.value: by_horizon[horizon][status] for status in all_statuses}
                for horizon in horizons
            },
        }
        object.__setattr__(self, "outcomes", ordered)
        object.__setattr__(self, "requested_horizons", horizons)
        object.__setattr__(self, "status_counts", status_counts)
        object.__setattr__(self, "status_counts_by_horizon", by_horizon)
        object.__setattr__(self, "_by_candidate", MappingProxyType({key: tuple(grouped[key]) for key in sorted(grouped)}))
        object.__setattr__(self, "set_identity", sha256(canonical_json(payload)).hexdigest())

    @property
    def candidate_count(self) -> int:
        return len(self._by_candidate)

    @property
    def available_count(self) -> int:
        return self.status_counts[ForwardOutcomeStatus.AVAILABLE]

    @property
    def censored_count(self) -> int:
        return self.status_counts[ForwardOutcomeStatus.CENSORED_AFTER_DATA_END]

    @property
    def missing_count(self) -> int:
        return len(self.outcomes) - self.available_count - self.censored_count

    def outcomes_for_candidate(self, candidate_key: str) -> tuple[CandidateForwardOutcome, ...]:
        return self._by_candidate.get(str(candidate_key), ())

    def outcome_for(self, candidate_key: str, horizon_sessions: int) -> CandidateForwardOutcome:
        for item in self.outcomes_for_candidate(candidate_key):
            if item.horizon_sessions == horizon_sessions:
                return item
        raise KeyError(f"unknown candidate/horizon outcome: {candidate_key}/{horizon_sessions}")

    @property
    def outcomes_by_candidate(self) -> Mapping[str, tuple[CandidateForwardOutcome, ...]]:
        return self._by_candidate
