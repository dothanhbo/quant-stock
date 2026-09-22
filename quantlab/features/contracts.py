from __future__ import annotations

"""Immutable, canonical contracts for Phase 2.1 in-memory features.

``FeatureIdentity`` identifies a formula/configuration.  A
``FeatureComputationIdentity`` binds that formula to an observed snapshot,
symbols, bounds, and (where required) point-in-time universe context.
``causal`` is a declared property; focused invariance tests provide evidence,
not automatic proof.  Phase 2.1 deliberately provides no persistent cache.
"""

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import json
import math
from types import MappingProxyType
from typing import Any, Callable, Mapping

import pandas as pd


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


CanonicalValue = None | bool | int | float | str | tuple["CanonicalValue", ...] | tuple[tuple[str, "CanonicalValue"], ...]


def canonicalize(value: Any) -> CanonicalValue:
    """Return supported immutable JSON-like values; reject ambiguous objects."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("feature parameters must not contain non-finite floats")
        return value
    if isinstance(value, (list, tuple)):
        return tuple(canonicalize(item) for item in value)
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("feature parameter mapping keys must be strings")
        return tuple((key, canonicalize(value[key])) for key in sorted(value))
    raise TypeError(f"unsupported feature parameter type: {type(value).__name__}")


def canonical_value_as_json(value: CanonicalValue) -> Any:
    if isinstance(value, tuple):
        if value and all(isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str) for item in value):
            return {key: canonical_value_as_json(item_value) for key, item_value in value}
        return [canonical_value_as_json(item) for item in value]
    return value


class FeatureScope(str, Enum):
    PER_SYMBOL = "per_symbol"
    CROSS_SECTIONAL = "cross_sectional"
    MARKET = "market"


@dataclass(frozen=True, slots=True)
class FeatureRequest:
    name: str
    version: str
    parameters: CanonicalValue = field(default_factory=dict)

    def __post_init__(self) -> None:
        name, version = self.name.strip(), self.version.strip()
        if not name or not version:
            raise ValueError("feature name and explicit version are required")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "parameters", canonicalize(self.parameters))

    @property
    def parameter_mapping(self) -> Mapping[str, Any]:
        if self.parameters == ():
            return MappingProxyType({})
        value = canonical_value_as_json(self.parameters)
        if not isinstance(value, dict):
            raise ValueError("feature parameters must be a mapping")
        return MappingProxyType(value)

    def canonical(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version, "parameters": dict(self.parameter_mapping)}


ComputeFunction = Callable[[Mapping[str, pd.DataFrame], Mapping[FeatureRequest, Mapping[str, pd.DataFrame]], Mapping[str, Any]], Mapping[str, pd.DataFrame]]
WarmupResolver = int | Callable[[Mapping[str, Any]], int]
DependencyResolver = Callable[[FeatureRequest], tuple[FeatureRequest, ...]]


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    name: str
    version: str
    scope: FeatureScope
    required_raw_columns: tuple[str, ...]
    dependencies: tuple[FeatureRequest, ...] | DependencyResolver = ()
    direct_warmup_sessions: WarmupResolver = 0
    causal: bool = True
    output_columns: tuple[str, ...] = ()
    compute: ComputeFunction | None = None

    @property
    def request(self) -> FeatureRequest:
        return FeatureRequest(self.name, self.version)


@dataclass(frozen=True, slots=True)
class FeatureIdentity:
    request: FeatureRequest
    dependency_hashes: tuple[str, ...]
    definition_metadata: tuple[tuple[str, Any], ...]
    sha256: str

    @classmethod
    def create(cls, request: FeatureRequest, dependencies: tuple["FeatureIdentity", ...], definition: FeatureDefinition) -> "FeatureIdentity":
        metadata = {
            "scope": definition.scope.value,
            "required_raw_columns": list(definition.required_raw_columns),
            "direct_warmup_sessions": definition.direct_warmup_sessions if isinstance(definition.direct_warmup_sessions, int) else "parameterized",
            "causal": definition.causal,
            "output_columns": list(definition.output_columns),
        }
        payload = {"request": request.canonical(), "dependencies": [item.sha256 for item in dependencies], "definition": metadata}
        return cls(request, tuple(item.sha256 for item in dependencies), tuple((key, metadata[key]) for key in sorted(metadata)), sha256(canonical_json(payload)).hexdigest())


@dataclass(frozen=True, slots=True)
class FeatureComputationIdentity:
    feature_identity: FeatureIdentity
    snapshot_id: str
    requested_symbols: tuple[str, ...]
    start_date: str | None
    through_date: str | None
    universe_identity: str | None
    sha256: str

    @classmethod
    def create(cls, *, feature_identity: FeatureIdentity, snapshot_id: str, symbols: tuple[str, ...], start_date: str | None, through_date: str | None, universe_identity: str | None) -> "FeatureComputationIdentity":
        payload = {"feature_identity": feature_identity.sha256, "snapshot_id": snapshot_id, "symbols": list(symbols), "start_date": start_date, "through_date": through_date, "universe_identity": universe_identity}
        return cls(feature_identity, snapshot_id, symbols, start_date, through_date, universe_identity, sha256(canonical_json(payload)).hexdigest())


@dataclass(frozen=True, slots=True)
class FeatureResult:
    computation_identity: FeatureComputationIdentity
    metadata: Mapping[str, Any]
    available_symbols: tuple[str, ...]
    missing_symbols: tuple[str, ...]
    _frames: Mapping[str, pd.DataFrame]

    def frame_for(self, symbol: str) -> pd.DataFrame:
        frame = self._frames.get(str(symbol).strip().upper())
        return pd.DataFrame() if frame is None else frame.copy(deep=True)

    @property
    def frames(self) -> Mapping[str, pd.DataFrame]:
        return MappingProxyType({symbol: frame.copy(deep=True) for symbol, frame in self._frames.items()})
