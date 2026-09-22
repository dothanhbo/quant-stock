from __future__ import annotations

"""Partial historical prepared-frame adapter for registered core indicators only."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping

import pandas as pd

from quantlab.catalog.market_data_snapshot import MarketDataSnapshot
from quantlab.features import FeatureRegistry, FeatureRequest, PreparedFeatureCache, builtin_definitions
from quantlab.features.contracts import FeatureComputationIdentity
from quantlab.features.universe_context import PointInTimeUniverseContext


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


def prepare_historical_benchmark_relative_subset(
    snapshot: MarketDataSnapshot,
    symbols: Iterable[str],
    *,
    benchmark_symbol: str,
    start_date: str,
    through_date: str,
    universe_identity: str | None = None,
    cache: PreparedFeatureCache | None = None,
    registry: FeatureRegistry | None = None,
) -> HistoricalPreparedFeatureBundle:
    """Prepare the v3 partial subset with an explicit local reference series."""
    if not through_date:
        raise ValueError("through_date is required for causal historical preparation")
    benchmark = str(benchmark_symbol).strip().upper()
    if not benchmark:
        raise ValueError("benchmark_symbol is required")
    primary = tuple(sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()} - {benchmark}))
    if not primary:
        raise ValueError("at least one primary equity symbol is required after excluding the benchmark")
    loaded_symbols = (*primary, benchmark)
    active_registry = registry or FeatureRegistry(builtin_definitions())
    result = active_registry.compute(
        FeatureRequest("historical_candidate_benchmark_relative_subset", "v3", {
            "benchmark_symbol": benchmark,
            "period": 20,
        }),
        snapshot,
        loaded_symbols,
        start_date=start_date,
        through_date=through_date,
        universe_identity=universe_identity,
        cache=cache,
    )
    if benchmark not in result.available_symbols:
        raise ValueError(f"benchmark series is unavailable: {benchmark}")
    available = tuple(symbol for symbol in primary if symbol in result.available_symbols)
    missing = tuple(symbol for symbol in primary if symbol not in result.available_symbols)
    metadata = {
        **result.metadata,
        "partial_subset": True,
        "benchmark_symbol": benchmark,
        "benchmark_available": True,
        "primary_symbols": primary,
        "excluded_feature_groups": (
            "market_regime", "breadth", "sector_features", "q70_cross_sectional_scoring",
        ),
    }
    return HistoricalPreparedFeatureBundle(
        result.computation_identity,
        available,
        missing,
        MappingProxyType(metadata),
        MappingProxyType({symbol: result.frame_for(symbol) for symbol in available}),
    )


def prepare_historical_market_context_subset(
    snapshot: MarketDataSnapshot,
    symbols: Iterable[str],
    *,
    benchmark_symbol: str,
    start_date: str,
    through_date: str,
    market_context_identity: str | None = None,
    cache: PreparedFeatureCache | None = None,
    registry: FeatureRegistry | None = None,
) -> HistoricalPreparedFeatureBundle:
    """Prepare the v4 subset with the causal VNINDEX Market_Regime field."""
    if not through_date:
        raise ValueError("through_date is required for causal historical preparation")
    benchmark = str(benchmark_symbol).strip().upper()
    if not benchmark:
        raise ValueError("benchmark_symbol is required")
    primary = tuple(sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()} - {benchmark}))
    if not primary:
        raise ValueError("at least one primary equity symbol is required after excluding the benchmark")
    context = market_context_identity or f"historical_market_reference:{benchmark}"
    active_registry = registry or FeatureRegistry(builtin_definitions())
    result = active_registry.compute(
        FeatureRequest("historical_candidate_market_context_subset", "v4", {"benchmark_symbol": benchmark}),
        snapshot,
        (*primary, benchmark),
        start_date=start_date,
        through_date=through_date,
        universe_identity=context,
        cache=cache,
    )
    if benchmark not in result.available_symbols:
        raise ValueError(f"benchmark series is unavailable: {benchmark}")
    available = tuple(symbol for symbol in primary if symbol in result.available_symbols)
    missing = tuple(symbol for symbol in primary if symbol not in result.available_symbols)
    regime = result.frame_for(benchmark)
    metadata = {
        **result.metadata,
        "partial_subset": True,
        "benchmark_symbol": benchmark,
        "benchmark_available": True,
        "primary_symbols": primary,
        "market_context_identity": context,
        "market_regime_available": not regime.empty,
        "market_regime_start": None if regime.empty else regime["time"].min().date().isoformat(),
        "market_regime_end": None if regime.empty else regime["time"].max().date().isoformat(),
        "excluded_feature_groups": ("breadth", "sector_features", "q70_cross_sectional_scoring"),
    }
    return HistoricalPreparedFeatureBundle(
        result.computation_identity,
        available,
        missing,
        MappingProxyType(metadata),
        MappingProxyType({symbol: result.frame_for(symbol) for symbol in available}),
    )


def prepare_historical_breadth_context_subset(
    snapshot: MarketDataSnapshot,
    symbols: Iterable[str],
    *,
    benchmark_symbol: str,
    universe_context: PointInTimeUniverseContext,
    start_date: str,
    through_date: str,
    market_context_identity: str | None = None,
    cache: PreparedFeatureCache | None = None,
    registry: FeatureRegistry | None = None,
) -> HistoricalPreparedFeatureBundle:
    """Prepare v5 with causal breadth from immutable point-in-time members."""
    if not through_date:
        raise ValueError("through_date is required for causal historical preparation")
    if not isinstance(universe_context, PointInTimeUniverseContext):
        raise TypeError("universe_context must be PointInTimeUniverseContext")
    benchmark = str(benchmark_symbol).strip().upper()
    if not benchmark:
        raise ValueError("benchmark_symbol is required")
    primary = tuple(sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()} - {benchmark}))
    if not primary:
        raise ValueError("at least one primary equity symbol is required after excluding the benchmark")
    context = market_context_identity or universe_context.membership_identity
    # The membership hash is the computation identity.  A caller-provided
    # market alias cannot replace it, preventing a context/content mismatch.
    if market_context_identity is not None and market_context_identity != universe_context.membership_identity:
        raise ValueError("market_context_identity must equal universe_context membership_identity")
    active_registry = registry or FeatureRegistry(builtin_definitions())
    loaded = tuple(sorted(set(primary) | set(universe_context.candidate_symbols) | {benchmark}))
    result = active_registry.compute(
        FeatureRequest("historical_candidate_breadth_context_subset", "v5", {"benchmark_symbol": benchmark}),
        snapshot,
        loaded,
        start_date=start_date,
        through_date=through_date,
        universe_identity=context,
        execution_context=universe_context,
        cache=cache,
    )
    if benchmark not in result.available_symbols:
        raise ValueError(f"benchmark series is unavailable: {benchmark}")
    available = tuple(symbol for symbol in primary if symbol in result.available_symbols)
    missing = tuple(symbol for symbol in primary if symbol not in result.available_symbols)
    metadata = {
        **result.metadata,
        "partial_subset": True,
        "benchmark_symbol": benchmark,
        "primary_symbols": primary,
        "breadth_universe_mode": universe_context.universe_mode,
        "breadth_membership_identity": universe_context.membership_identity,
        "breadth_universe_metadata": dict(universe_context.metadata),
        "excluded_feature_groups": ("paper_market_state", "sector_features", "q70_cross_sectional_scoring"),
    }
    return HistoricalPreparedFeatureBundle(result.computation_identity, available, missing, MappingProxyType(metadata), MappingProxyType({symbol: result.frame_for(symbol) for symbol in available}))


def prepare_historical_paper_state_subset(
    snapshot: MarketDataSnapshot,
    symbols: Iterable[str],
    *,
    benchmark_symbol: str,
    universe_context: PointInTimeUniverseContext,
    start_date: str,
    through_date: str,
    market_context_identity: str | None = None,
    cache: PreparedFeatureCache | None = None,
    registry: FeatureRegistry | None = None,
) -> HistoricalPreparedFeatureBundle:
    """Prepare v6 fields and label only; it never scores or gates candidates."""
    if not through_date:
        raise ValueError("through_date is required for causal historical preparation")
    if not isinstance(universe_context, PointInTimeUniverseContext):
        raise TypeError("universe_context must be PointInTimeUniverseContext")
    benchmark = str(benchmark_symbol).strip().upper()
    if not benchmark:
        raise ValueError("benchmark_symbol is required")
    primary = tuple(sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()} - {benchmark}))
    if not primary:
        raise ValueError("at least one primary equity symbol is required after excluding the benchmark")
    if market_context_identity is not None and market_context_identity != universe_context.membership_identity:
        raise ValueError("market_context_identity must equal universe_context membership_identity")
    active_registry = registry or FeatureRegistry(builtin_definitions())
    loaded = tuple(sorted(set(primary) | set(universe_context.candidate_symbols) | {benchmark}))
    result = active_registry.compute(
        FeatureRequest("historical_candidate_paper_state_subset", "v6", {"benchmark_symbol": benchmark}),
        snapshot, loaded, start_date=start_date, through_date=through_date,
        universe_identity=universe_context.membership_identity,
        execution_context=universe_context, cache=cache,
    )
    if benchmark not in result.available_symbols:
        raise ValueError(f"benchmark series is unavailable: {benchmark}")
    available = tuple(symbol for symbol in primary if symbol in result.available_symbols)
    missing = tuple(symbol for symbol in primary if symbol not in result.available_symbols)
    state_counts: dict[str, int] = {}
    if available:
        state_counts = result.frame_for(available[0])["paper_v2_state"].value_counts(dropna=False).to_dict()
    metadata = {
        **result.metadata, "partial_subset": True, "benchmark_symbol": benchmark,
        "primary_symbols": primary, "breadth_membership_identity": universe_context.membership_identity,
        "paper_v2_state_available": bool(available), "paper_v2_state_counts": MappingProxyType(state_counts),
        "excluded_feature_groups": ("q70_component_percentiles", "q70_quality_score", "gate_decisions", "paper_v3_exposure"),
    }
    return HistoricalPreparedFeatureBundle(result.computation_identity, available, missing, MappingProxyType(metadata), MappingProxyType({symbol: result.frame_for(symbol) for symbol in available}))
