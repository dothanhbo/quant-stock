from __future__ import annotations

"""Explicit, in-memory computation graph for Phase 2.1 features."""

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any

import pandas as pd

from quantlab.catalog.market_data_snapshot import MarketDataSnapshot
from .contracts import FeatureComputationIdentity, FeatureDefinition, FeatureIdentity, FeatureRequest, FeatureResult, FeatureScope


class FeatureRegistry:
    def __init__(self, definitions: Iterable[FeatureDefinition] = ()) -> None:
        self._definitions: dict[tuple[str, str], FeatureDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: FeatureDefinition) -> None:
        key = (definition.name.strip(), definition.version.strip())
        if not key[0] or not key[1] or definition.compute is None:
            raise ValueError("feature definitions require name, version, and compute")
        if key in self._definitions:
            raise ValueError(f"duplicate feature definition: {key[0]}@{key[1]}")
        self._definitions[key] = definition

    def definition_for(self, request: FeatureRequest) -> FeatureDefinition:
        try:
            return self._definitions[(request.name, request.version)]
        except KeyError as exc:
            raise KeyError(f"unknown feature definition: {request.name}@{request.version}") from exc

    def _resolve(self, request: FeatureRequest, visiting: set[FeatureRequest], resolved: dict[FeatureRequest, tuple[FeatureDefinition, FeatureIdentity, int]]) -> tuple[FeatureDefinition, FeatureIdentity, int]:
        if request in resolved:
            return resolved[request]
        if request in visiting:
            raise ValueError(f"feature dependency cycle detected at {request.name}@{request.version}")
        visiting.add(request); definition = self.definition_for(request)
        dependencies = tuple(self._resolve(item, visiting, resolved) for item in definition.dependencies)
        visiting.remove(request)
        parameters = request.parameter_mapping
        direct = definition.direct_warmup_sessions(parameters) if callable(definition.direct_warmup_sessions) else definition.direct_warmup_sessions
        if not isinstance(direct, int) or direct < 0:
            raise ValueError(f"invalid warmup for {request.name}@{request.version}")
        warmup = direct + max((item[2] for item in dependencies), default=0)
        identity = FeatureIdentity.create(request, tuple(item[1] for item in dependencies), definition)
        resolved[request] = (definition, identity, warmup)
        return resolved[request]

    def compute(self, request: FeatureRequest, snapshot: MarketDataSnapshot, symbols: Iterable[str], *, start_date: str | None = None, through_date: str | None = None, universe_identity: str | None = None) -> FeatureResult:
        resolved: dict[FeatureRequest, tuple[FeatureDefinition, FeatureIdentity, int]] = {}
        definition, identity, warmup = self._resolve(request, set(), resolved)
        if definition.scope is not FeatureScope.PER_SYMBOL and not universe_identity:
            raise ValueError("cross-sectional and market features require universe_identity")
        normalized_symbols = tuple(sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}))
        # Loading all history through the causal boundary ensures warmup is in
        # observed sessions; no calendar-day subtraction is used.
        bundle = snapshot.load_ohlcv(normalized_symbols, through_date=through_date)
        raw_frames = {symbol: bundle.frame_for(symbol) for symbol in bundle.available_symbols}
        outputs: dict[FeatureRequest, Mapping[str, pd.DataFrame]] = {}
        for current, (current_definition, _current_identity, _current_warmup) in resolved.items():
            for frame in raw_frames.values():
                missing = set(current_definition.required_raw_columns).difference(frame.columns)
                if missing:
                    raise ValueError(f"missing raw columns for {current.name}: {', '.join(sorted(missing))}")
            dependency_outputs = {dependency: outputs[dependency] for dependency in current_definition.dependencies}
            computed = current_definition.compute(raw_frames, dependency_outputs, current.parameter_mapping)  # type: ignore[misc]
            checked: dict[str, pd.DataFrame] = {}
            for symbol, frame in computed.items():
                declared_missing = set(current_definition.output_columns).difference(frame.columns)
                if declared_missing:
                    raise ValueError(
                        f"feature {current.name} output is missing declared columns: "
                        + ", ".join(sorted(declared_missing))
                    )
                if list(frame.columns[:1]) != ["time"] or not pd.api.types.is_datetime64_any_dtype(frame["time"]):
                    raise ValueError(f"feature {current.name} must return datetime time column")
                if not frame["time"].is_monotonic_increasing or frame["time"].duplicated().any():
                    raise ValueError(f"feature {current.name} output time must be chronological and unique")
                checked[symbol] = frame.reset_index(drop=True).copy(deep=True)
            outputs[current] = MappingProxyType(checked)
        final = outputs[request]
        if start_date is not None:
            cutoff = pd.Timestamp(start_date)
            final = MappingProxyType({symbol: frame.loc[frame["time"] >= cutoff].reset_index(drop=True).copy(deep=True) for symbol, frame in final.items()})
        computation_identity = FeatureComputationIdentity.create(feature_identity=identity, snapshot_id=snapshot.snapshot_id, symbols=normalized_symbols, start_date=start_date, through_date=through_date, universe_identity=universe_identity)
        return FeatureResult(computation_identity, MappingProxyType({"resolved_warmup_sessions": warmup, "causal": definition.causal}), bundle.available_symbols, bundle.missing_symbols, MappingProxyType(dict(final)))
