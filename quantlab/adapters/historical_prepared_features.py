from __future__ import annotations

"""Partial historical prepared-frame adapter for registered core indicators only."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping

import pandas as pd

from quantlab.catalog.market_data_snapshot import MarketDataSnapshot
from quantlab.features import FeatureRegistry, FeatureRequest, PreparedFeatureCache, builtin_definitions
from quantlab.features.contracts import FeatureComputationIdentity


@dataclass(frozen=True, slots=True)
class HistoricalPreparedFeatureBundle:
    computation_identity: FeatureComputationIdentity
    available_symbols: tuple[str, ...]
    missing_symbols: tuple[str, ...]
    metadata: Mapping[str, object]
    _frames: Mapping[str, pd.DataFrame]

    def frame_for(self, symbol: str) -> pd.DataFrame:
        frame = self._frames.get(str(symbol).strip().upper())
        return pd.DataFrame() if frame is None else frame.copy(deep=True)


def prepare_historical_core_subset(
    snapshot: MarketDataSnapshot,
    symbols: Iterable[str],
    *,
    start_date: str,
    through_date: str,
    universe_identity: str | None = None,
    cache: PreparedFeatureCache | None = None,
    registry: FeatureRegistry | None = None,
) -> HistoricalPreparedFeatureBundle:
    if not through_date:
        raise ValueError("through_date is required for causal historical preparation")
    active_registry = registry or FeatureRegistry(builtin_definitions())
    result = active_registry.compute(
        FeatureRequest("historical_candidate_core_subset", "v1"),
        snapshot,
        symbols,
        start_date=start_date,
        through_date=through_date,
        universe_identity=universe_identity,
        cache=cache,
    )
    return HistoricalPreparedFeatureBundle(result.computation_identity, result.available_symbols, result.missing_symbols, MappingProxyType({**result.metadata, "partial_subset": True}), MappingProxyType({symbol: result.frame_for(symbol) for symbol in result.available_symbols}))


def prepare_historical_per_symbol_subset(
    snapshot: MarketDataSnapshot,
    symbols: Iterable[str],
    *,
    start_date: str,
    through_date: str,
    universe_identity: str | None = None,
    cache: PreparedFeatureCache | None = None,
    registry: FeatureRegistry | None = None,
) -> HistoricalPreparedFeatureBundle:
    """Prepare only the causal, per-symbol historical candidate subset.

    VNINDEX-relative strength, market regime, breadth, sectors, and Q70
    cross-sectional values are intentionally absent from this Phase 3.1 seam.
    """
    if not through_date:
        raise ValueError("through_date is required for causal historical preparation")
    active_registry = registry or FeatureRegistry(builtin_definitions())
    result = active_registry.compute(
        FeatureRequest("historical_candidate_per_symbol_subset", "v2"),
        snapshot,
        symbols,
        start_date=start_date,
        through_date=through_date,
        universe_identity=universe_identity,
        cache=cache,
    )
    metadata = {
        **result.metadata,
        "partial_subset": True,
        "excluded_feature_groups": (
            "relative_strength_vs_vnindex",
            "market_regime",
            "breadth",
            "sector_features",
            "q70_cross_sectional_scoring",
        ),
    }
    return HistoricalPreparedFeatureBundle(
        result.computation_identity,
        result.available_symbols,
        result.missing_symbols,
        MappingProxyType(metadata),
        MappingProxyType({symbol: result.frame_for(symbol) for symbol in result.available_symbols}),
    )
