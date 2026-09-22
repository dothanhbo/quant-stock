"""Isolated opt-in adapters; no production caller imports this package."""

from .historical_prepared_features import (
    HistoricalPreparedFeatureBundle,
    prepare_historical_benchmark_relative_subset,
    prepare_historical_market_context_subset,
    prepare_historical_breadth_context_subset,
    prepare_historical_paper_state_subset,
    prepare_historical_core_subset,
    prepare_historical_per_symbol_subset,
)

__all__ = [
    "HistoricalPreparedFeatureBundle",
    "prepare_historical_benchmark_relative_subset",
    "prepare_historical_market_context_subset",
    "prepare_historical_breadth_context_subset",
    "prepare_historical_paper_state_subset",
    "prepare_historical_core_subset",
    "prepare_historical_per_symbol_subset",
]
