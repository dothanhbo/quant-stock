"""Isolated opt-in adapters; no production caller imports this package."""

from .historical_prepared_features import HistoricalPreparedFeatureBundle, prepare_historical_core_subset

__all__ = ["HistoricalPreparedFeatureBundle", "prepare_historical_core_subset"]
