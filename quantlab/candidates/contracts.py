from __future__ import annotations

"""Immutable research-candidate contracts; deliberately no execution fields."""

from dataclasses import dataclass, field, fields
from datetime import date, datetime
from hashlib import sha256
import math
from types import MappingProxyType
from typing import Any, Mapping

from quantlab.features.contracts import canonical_json


FROZEN_Q70_CANDIDATE_CONTRACT = "quantlab.frozen_q70_candidate_batch"
FROZEN_Q70_CANDIDATE_CONTRACT_VERSION = "v1"
Q70_COMPONENTS = ("score", "relative_strength_20d", "adx")


def _scalar(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, AttributeError):
            return value
    return value


def _identity_value(value: Any) -> Any:
    """Encode immutable candidate content, including non-finite floats."""
    value = _scalar(value)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return {"__nonfinite_float__": "NaN"}
        if math.isinf(value):
            return {"__nonfinite_float__": "Infinity" if value > 0 else "-Infinity"}
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("candidate identity mapping keys must be strings")
        return {key: _identity_value(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_identity_value(item) for item in value]
    raise TypeError(f"unsupported candidate identity value: {type(value).__name__}")


def _date_text(value: str | date | datetime) -> str:
    text = value.date().isoformat() if isinstance(value, datetime) else (
        value.isoformat() if isinstance(value, date) else str(value)
    )
    date.fromisoformat(text)
    return text


def _symbols(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({
        str(value).strip().upper()
        for value in values
        if str(value).strip() and str(value).strip().upper() != "VNINDEX"
    }))


@dataclass(frozen=True, slots=True)
class FrozenQ70CandidateRecord:
    candidate_key: str
    evaluation_key: str
    symbol: str
    signal_date: str
    snapshot_id: str
    prepared_v6_identity: str
    phase_3_7_run_identity: str
    entry_policy_identity: str
    q70_policy_fingerprint: str
    universe_membership_identity: str
    score: Any
    relative_strength_20d: Any
    adx: Any
    component_percentiles: Mapping[str, float]
    quality_score: float
    q70_threshold: float
    acceptance_reason: str
    paper_v2_state: str
    signal_close: Any
    atr14: Any
    atr_percent: Any
    rsi14: Any
    volume_ratio: Any
    ema10: Any
    ema20: Any
    ema50: Any
    previous_20d_high: Any
    donchian_breakout: Any
    market_regime: Any
    breadth_ema50_pct: Any
    breadth_ema50_change_10d: Any
    breadth_universe_count: Any

    def __post_init__(self) -> None:
        symbol = str(self.symbol).strip().upper()
        if not symbol or symbol == "VNINDEX":
            raise ValueError("candidate record requires a non-benchmark symbol")
        if not str(self.candidate_key) or not str(self.evaluation_key):
            raise ValueError("candidate_key and evaluation_key are required")
        for name in (
            "snapshot_id", "prepared_v6_identity", "phase_3_7_run_identity",
            "entry_policy_identity", "q70_policy_fingerprint",
            "universe_membership_identity", "acceptance_reason", "paper_v2_state",
        ):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be a non-empty string")
        components = {
            str(key): float(_scalar(value))
            for key, value in self.component_percentiles.items()
        }
        if tuple(sorted(components)) != tuple(sorted(Q70_COMPONENTS)):
            raise ValueError("component_percentiles must contain score, relative_strength_20d, and adx")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "signal_date", _date_text(self.signal_date))
        object.__setattr__(self, "component_percentiles", MappingProxyType({key: components[key] for key in Q70_COMPONENTS}))
        for item in fields(self):
            if item.name not in {"symbol", "signal_date", "component_percentiles"}:
                object.__setattr__(self, item.name, _scalar(getattr(self, item.name)))

    def canonical_content(self) -> dict[str, Any]:
        return {
            item.name: _identity_value(getattr(self, item.name))
            for item in fields(self)
        }


@dataclass(frozen=True, slots=True)
class FrozenQ70CandidateBatch:
    candidates: tuple[FrozenQ70CandidateRecord, ...]
    requested_symbols: tuple[str, ...]
    available_symbols: tuple[str, ...]
    start_date: str
    through_date: str
    snapshot_id: str
    prepared_v6_identity: str
    phase_3_7_run_identity: str
    entry_policy_identity: str
    q70_policy_fingerprint: str
    universe_membership_identity: str
    contract_name: str = FROZEN_Q70_CANDIDATE_CONTRACT
    contract_version: str = FROZEN_Q70_CANDIDATE_CONTRACT_VERSION
    batch_identity: str = field(init=False)
    _by_signal_date: Mapping[str, tuple[FrozenQ70CandidateRecord, ...]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (self.contract_name, self.contract_version) != (
            FROZEN_Q70_CANDIDATE_CONTRACT,
            FROZEN_Q70_CANDIDATE_CONTRACT_VERSION,
        ):
            raise ValueError("unsupported frozen-Q70 candidate contract")
        start, through = _date_text(self.start_date), _date_text(self.through_date)
        if start > through:
            raise ValueError("start_date must not be after through_date")
        for name in (
            "snapshot_id", "prepared_v6_identity", "phase_3_7_run_identity",
            "entry_policy_identity", "q70_policy_fingerprint",
            "universe_membership_identity",
        ):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be a non-empty string")
        ordered = tuple(sorted(
            tuple(self.candidates),
            key=lambda item: (item.signal_date, item.symbol, item.candidate_key),
        ))
        candidate_keys = tuple(item.candidate_key for item in ordered)
        evaluation_keys = tuple(item.evaluation_key for item in ordered)
        if len(set(candidate_keys)) != len(candidate_keys):
            raise ValueError("duplicate candidate key")
        if len(set(evaluation_keys)) != len(evaluation_keys):
            raise ValueError("duplicate evaluation key")
        for item in ordered:
            if not start <= item.signal_date <= through:
                raise ValueError(f"candidate signal_date outside requested bounds: {item.candidate_key}")
            expected = (
                self.snapshot_id, self.prepared_v6_identity, self.phase_3_7_run_identity,
                self.entry_policy_identity, self.q70_policy_fingerprint,
                self.universe_membership_identity,
            )
            actual = (
                item.snapshot_id, item.prepared_v6_identity, item.phase_3_7_run_identity,
                item.entry_policy_identity, item.q70_policy_fingerprint,
                item.universe_membership_identity,
            )
            if actual != expected:
                raise ValueError(f"candidate provenance mismatch: {item.candidate_key}")
        grouped: dict[str, list[FrozenQ70CandidateRecord]] = {}
        for item in ordered:
            grouped.setdefault(item.signal_date, []).append(item)
        requested, available = _symbols(tuple(self.requested_symbols)), _symbols(tuple(self.available_symbols))
        payload = {
            "contract": {"name": self.contract_name, "version": self.contract_version},
            "candidates": [item.canonical_content() for item in ordered],
            "phase_3_7_run_identity": self.phase_3_7_run_identity,
            "prepared_v6_identity": self.prepared_v6_identity,
            "snapshot_id": self.snapshot_id,
            "entry_policy_identity": self.entry_policy_identity,
            "q70_policy_fingerprint": self.q70_policy_fingerprint,
            "universe_membership_identity": self.universe_membership_identity,
            "requested_symbols": list(requested),
            "available_symbols": list(available),
            "start_date": start,
            "through_date": through,
        }
        object.__setattr__(self, "candidates", ordered)
        object.__setattr__(self, "requested_symbols", requested)
        object.__setattr__(self, "available_symbols", available)
        object.__setattr__(self, "start_date", start)
        object.__setattr__(self, "through_date", through)
        object.__setattr__(self, "_by_signal_date", MappingProxyType({key: tuple(grouped[key]) for key in sorted(grouped)}))
        object.__setattr__(self, "batch_identity", sha256(canonical_json(payload)).hexdigest())

    @property
    def accepted_candidate_keys(self) -> tuple[str, ...]:
        return tuple(item.candidate_key for item in self.candidates)

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    @property
    def signal_dates(self) -> tuple[str, ...]:
        return tuple(self._by_signal_date)

    def candidates_for_signal_date(self, signal_date: str | date | datetime) -> tuple[FrozenQ70CandidateRecord, ...]:
        return self._by_signal_date.get(_date_text(signal_date), ())

    @property
    def candidates_by_signal_date(self) -> Mapping[str, tuple[FrozenQ70CandidateRecord, ...]]:
        return self._by_signal_date
