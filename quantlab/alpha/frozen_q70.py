from __future__ import annotations

"""Side-effect-free exact scoring parity for the frozen PaperV2 Q70 policy."""

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import math
from types import MappingProxyType
from typing import Any, Iterable, Mapping

import numpy as np

from quantlab.features.contracts import canonical_json, canonicalize, canonical_value_as_json
from quantlab.features.universe_context import PointInTimeUniverseContext


_COMPONENTS = ("score", "relative_strength_20d", "adx")


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return number if math.isfinite(number) else float("nan")


def _rank(values: np.ndarray, value: Any) -> float:
    finite = values[np.isfinite(values)]
    number = _number(value)
    if not math.isfinite(number) or finite.size == 0:
        return 0.5
    return float(np.searchsorted(np.sort(finite), number, side="right") / finite.size)


def _raw_identity(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
    return canonical_value_as_json(canonicalize(value))


@dataclass(frozen=True, slots=True)
class FrozenQ70Policy:
    name: str = "Q70_FROZEN"
    version: str = "v1"
    threshold: float = 0.70

    @property
    def fingerprint(self) -> str:
        return sha256(canonical_json({"name": self.name, "version": self.version, "threshold": self.threshold, "components": _COMPONENTS, "weights": [1 / 3] * 3, "percentile": "finite_sorted_searchsorted_right"})).hexdigest()

    def __post_init__(self) -> None:
        if (self.name, self.version, self.threshold) != ("Q70_FROZEN", "v1", 0.70):
            raise ValueError("FrozenQ70Policy only supports the canonical Q70_FROZEN v1 policy")


@dataclass(frozen=True, slots=True)
class FrozenQ70EvaluationRow:
    symbol: str
    signal_date: str
    score: Any
    relative_strength_20d: Any
    adx: Any
    base_entry_passed: bool
    evaluation_key: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        if not symbol or symbol == "VNINDEX":
            raise ValueError("evaluation rows require a non-benchmark symbol")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "signal_date", str(self.signal_date))
        for value in (self.score, self.relative_strength_20d, self.adx):
            _raw_identity(value)
        if self.evaluation_key is not None:
            canonicalize(self.evaluation_key)
        if self.reason is not None:
            canonicalize(self.reason)

    def canonical(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "signal_date": self.signal_date, "score": _raw_identity(self.score), "relative_strength_20d": _raw_identity(self.relative_strength_20d), "adx": _raw_identity(self.adx), "base_entry_passed": self.base_entry_passed, "evaluation_key": self.evaluation_key, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class FrozenQ70Decision:
    symbol: str
    evaluation_key: str
    base_entry_passed: bool
    eligible: bool
    component_percentiles: Mapping[str, float]
    quality_score: float
    market_state: str
    accepted: bool
    reason: str


@dataclass(frozen=True, slots=True)
class FrozenQ70BatchResult:
    policy: FrozenQ70Policy
    batch_identity: str
    signal_date: str
    market_state: str
    universe_identity: str
    decisions: tuple[FrozenQ70Decision, ...]
    reference_records: tuple[FrozenQ70Decision, ...]
    counts: Mapping[str, int]
    rejection_counts: Mapping[str, int]


def score_frozen_q70_batch(
    rows: Iterable[FrozenQ70EvaluationRow],
    *,
    signal_date: str,
    market_state: str,
    universe_context: PointInTimeUniverseContext,
    policy: FrozenQ70Policy = FrozenQ70Policy(),
) -> FrozenQ70BatchResult:
    if not isinstance(universe_context, PointInTimeUniverseContext):
        raise TypeError("universe_context must be PointInTimeUniverseContext")
    date_text = str(signal_date); state = str(market_state).upper()
    supplied = tuple(rows)
    if any(row.signal_date != date_text for row in supplied):
        raise ValueError("Frozen Q70 batch rows must share the explicit signal_date")
    if len({(row.symbol, row.evaluation_key or row.symbol) for row in supplied}) != len(supplied):
        raise ValueError("duplicate evaluation row identity")
    eligible_members = universe_context.members_as_of(date_text)
    eligible = tuple(row for row in supplied if row.symbol in eligible_members)
    refs = {name: np.asarray([_number(getattr(row, name)) for row in eligible], dtype=float) for name in _COMPONENTS}
    records: list[FrozenQ70Decision] = []; decisions: list[FrozenQ70Decision] = []; rejections: Counter[str] = Counter()
    for row in sorted(eligible, key=lambda value: (value.symbol, value.evaluation_key or "")):
        components = MappingProxyType({name: _rank(refs[name], getattr(row, name)) for name in _COMPONENTS})
        quality = float(np.mean(list(components.values())))
        key = row.evaluation_key or f"{row.symbol}:{date_text}"
        if state == "BEAR":
            accepted, reason = False, "BEAR"
        elif state == "DIVERGENT_BULL":
            accepted, reason = False, "DIVERGENT_BULL"
        elif quality < policy.threshold:
            accepted, reason = False, f"quality<{policy.threshold:.2f}"
        else:
            accepted, reason = True, f"Q{policy.threshold:.2f}_PASS"
        record = FrozenQ70Decision(row.symbol, key, row.base_entry_passed, True, components, quality, state, accepted if row.base_entry_passed else False, reason if row.base_entry_passed else "reference_only")
        records.append(record)
        if row.base_entry_passed:
            decisions.append(record)
            if not record.accepted:
                rejections[record.reason] += 1
    payload = {"policy": policy.fingerprint, "signal_date": date_text, "market_state": state, "universe_identity": universe_context.membership_identity, "eligible_members": sorted(eligible_members), "rows": [row.canonical() for row in sorted(supplied, key=lambda value: (value.symbol, value.evaluation_key or ""))]}
    counts = {"total_input_rows": len(supplied), "eligible_reference_rows": len(eligible), "ineligible_rows": len(supplied) - len(eligible), "base_entry_candidates": len(decisions), "state_rejected": sum(item.reason in {"BEAR", "DIVERGENT_BULL"} for item in decisions), "quality_rejected": sum(item.reason.startswith("quality<") for item in decisions), "accepted": sum(item.accepted for item in decisions)}
    return FrozenQ70BatchResult(policy, sha256(canonical_json(payload)).hexdigest(), date_text, state, universe_context.membership_identity, tuple(decisions), tuple(records), MappingProxyType(counts), MappingProxyType(dict(rejections)))
