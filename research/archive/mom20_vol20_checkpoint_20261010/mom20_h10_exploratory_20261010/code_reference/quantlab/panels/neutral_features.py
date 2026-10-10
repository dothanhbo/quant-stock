from __future__ import annotations

"""Strategy-neutral registered numeric feature source and panel adapter."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping

import pandas as pd

from .contracts import PointInTimeObservationIndex
from .feature_contracts import FeatureFieldSpec, PointInTimeFeaturePanel, PointInTimeFeaturePanelSpec
from .feature_panel import attach_features_to_observation_index


NEUTRAL_RESEARCH_SOURCE_TO_CANONICAL_V1 = (
    ("close", "close"),
    ("volume", "volume"),
    ("EMA10", "ema_10"),
    ("EMA20", "ema_20"),
    ("EMA50", "ema_50"),
    ("ATR14", "atr_14"),
    ("ATR_Percent", "atr_percent_14"),
    ("RSI", "rsi_14"),
    ("ADX14", "adx_14"),
    ("Vol_MA20", "volume_ma_20"),
    ("Vol_Ratio", "volume_ratio_20"),
    ("Stock_Return_20D", "stock_return_20d_pct"),
    ("Index_Return_20D", "benchmark_return_20d_pct"),
    ("Relative_Strength_20D", "relative_strength_20d_pct_points"),
    ("Distance_EMA20_Pct", "ema20_distance_pct"),
    ("Return_3D_Pct", "return_3d_pct"),
    ("breadth_ema50_pct", "breadth_ema50_pct"),
    ("breadth_ema50_change_10d", "breadth_ema50_change_10d_pct_points"),
)


def _field(source: str, output: str, description: str, *, integer: bool = False) -> FeatureFieldSpec:
    return FeatureFieldSpec(
        source_column=output,
        output_name=output,
        version="v1",
        description=f"{description}; authoritative source column {source}",
        expected_numeric_type="integer" if integer else "float",
        finite_required=True,
    )


NEUTRAL_RESEARCH_NUMERIC_FEATURES_V1 = (
    _field("close", "close", "exact normalized closing price"),
    _field("volume", "volume", "exact normalized traded volume", integer=True),
    _field("EMA10", "ema_10", "10-session exponential moving average"),
    _field("EMA20", "ema_20", "20-session exponential moving average"),
    _field("EMA50", "ema_50", "50-session exponential moving average"),
    _field("ATR14", "atr_14", "14-session Wilder average true range"),
    _field("ATR_Percent", "atr_percent_14", "ATR14 as percentage of close"),
    _field("RSI", "rsi_14", "14-session Wilder relative strength index"),
    _field("ADX14", "adx_14", "14-session Wilder average directional index"),
    _field("Vol_MA20", "volume_ma_20", "20-session volume moving average"),
    _field("Vol_Ratio", "volume_ratio_20", "volume divided by 20-session volume average"),
    _field("Stock_Return_20D", "stock_return_20d_pct", "20-session stock return percentage"),
    _field("Index_Return_20D", "benchmark_return_20d_pct", "20-session benchmark return percentage"),
    _field("Relative_Strength_20D", "relative_strength_20d_pct_points", "stock minus benchmark 20-session return"),
    _field("Distance_EMA20_Pct", "ema20_distance_pct", "close distance from EMA20 percentage"),
    _field("Return_3D_Pct", "return_3d_pct", "three-session close return percentage"),
    _field("breadth_ema50_pct", "breadth_ema50_pct", "point-in-time universe percentage above EMA50"),
    _field("breadth_ema50_change_10d", "breadth_ema50_change_10d_pct_points", "ten-produced-row breadth change"),
)

NEUTRAL_RESEARCH_FEATURE_PANEL_V1 = PointInTimeFeaturePanelSpec(
    name="neutral_research_numeric_feature_panel",
    version="v1",
    fields=NEUTRAL_RESEARCH_NUMERIC_FEATURES_V1,
)


@dataclass(frozen=True, slots=True)
class _NeutralResearchFeatureSource:
    computation_identity: Any
    available_symbols: tuple[str, ...]
    missing_symbols: tuple[str, ...]
    metadata: Mapping[str, Any]
    _frames: Mapping[str, pd.DataFrame]

    def frame_for(self, symbol: str) -> pd.DataFrame:
        frame = self._frames.get(str(symbol).strip().upper())
        return pd.DataFrame() if frame is None else frame.copy(deep=True)


def _symbols(values: Iterable[Any], *, exclude: str | None = None) -> tuple[str, ...]:
    return tuple(sorted({
        normalized
        for value in values
        if (normalized := str(value).strip().upper()) and normalized != exclude
    }))


def _date_text(value: Any, *, name: str) -> str:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a valid date") from exc
    if pd.isna(timestamp):
        raise ValueError(f"{name} must be a valid date")
    return timestamp.date().isoformat()


def _request_parameters(benchmark: str, primary: tuple[str, ...]) -> dict[str, Any]:
    from quantlab.features.builtins import (
        NEUTRAL_RESEARCH_BENCHMARK_ROLE_V1,
        NEUTRAL_RESEARCH_UNIVERSE_ROLE_V1,
    )

    return {
        "benchmark_symbol": benchmark,
        "period": 20,
        "primary_symbols": list(primary),
        "source_to_canonical_mapping": [
            {"source": source, "canonical": canonical}
            for source, canonical in NEUTRAL_RESEARCH_SOURCE_TO_CANONICAL_V1
        ],
        "benchmark_role": NEUTRAL_RESEARCH_BENCHMARK_ROLE_V1,
        "universe_context_role": NEUTRAL_RESEARCH_UNIVERSE_ROLE_V1,
    }


def prepare_neutral_research_feature_source(
    snapshot: Any,
    symbols: Iterable[str],
    *,
    benchmark_symbol: str,
    universe_context: Any,
    start_date: str,
    through_date: str,
    cache: Any | None = None,
) -> _NeutralResearchFeatureSource:
    """Compute the registered neutral numeric graph once and expose frames only."""
    from quantlab.features import FeatureRegistry, FeatureRequest, builtin_definitions

    if not hasattr(snapshot, "snapshot_id") or not callable(getattr(snapshot, "load_ohlcv", None)):
        raise TypeError("snapshot must provide MarketDataSnapshot-compatible identity and loading")
    if not isinstance(snapshot.snapshot_id, str) or not snapshot.snapshot_id.strip():
        raise ValueError("snapshot_id must be non-empty")
    membership_identity = getattr(universe_context, "membership_identity", None)
    candidates = getattr(universe_context, "candidate_symbols", None)
    if not isinstance(membership_identity, str) or not membership_identity.strip() or candidates is None:
        raise ValueError("universe_context requires immutable membership identity and candidates")
    benchmark = str(benchmark_symbol).strip().upper()
    if not benchmark:
        raise ValueError("benchmark_symbol must be non-empty")
    start = _date_text(start_date, name="start_date")
    through = _date_text(through_date, name="through_date")
    if start > through:
        raise ValueError("start_date must be on or before through_date")
    primary = _symbols(symbols, exclude=benchmark)
    breadth_candidates = _symbols(candidates, exclude=benchmark)
    loaded_symbols = tuple(sorted({benchmark, *primary, *breadth_candidates}))
    request = FeatureRequest(
        "neutral_research_numeric_features",
        "v1",
        _request_parameters(benchmark, primary),
    )
    result = FeatureRegistry(builtin_definitions()).compute(
        request,
        snapshot,
        loaded_symbols,
        start_date=start,
        through_date=through,
        universe_identity=membership_identity,
        execution_context=universe_context,
        cache=cache,
    )
    result_frames = result.frames
    frames = {
        symbol: result_frames[symbol]
        for symbol in primary
        if symbol in result_frames
    }
    available = tuple(sorted(frames))
    missing = tuple(symbol for symbol in primary if symbol not in frames)
    output_columns = ("time", *(item.output_name for item in NEUTRAL_RESEARCH_NUMERIC_FEATURES_V1))
    metadata = MappingProxyType({
        **dict(result.metadata),
        "feature_set": "NEUTRAL_RESEARCH_NUMERIC_FEATURES_V1",
        "feature_set_version": "v1",
        "source_to_canonical_mapping": NEUTRAL_RESEARCH_SOURCE_TO_CANONICAL_V1,
        "output_columns": output_columns,
        "primary_symbols": primary,
        "benchmark_symbol": benchmark,
        "universe_membership_identity": membership_identity,
        "available_symbols": available,
        "missing_symbols": missing,
    })
    return _NeutralResearchFeatureSource(
        computation_identity=result.computation_identity,
        available_symbols=available,
        missing_symbols=missing,
        metadata=metadata,
        _frames=MappingProxyType({symbol: frame.copy(deep=True) for symbol, frame in frames.items()}),
    )


def build_neutral_research_feature_panel(
    observation_index: PointInTimeObservationIndex,
    snapshot: Any,
    *,
    benchmark_symbol: str,
    universe_context: Any,
    cache: Any | None = None,
) -> PointInTimeFeaturePanel:
    """Compute one neutral source for the supplied observation index and attach it."""
    if not isinstance(observation_index, PointInTimeObservationIndex):
        raise TypeError("observation_index must be PointInTimeObservationIndex")
    if getattr(snapshot, "snapshot_id", None) != observation_index.snapshot_id:
        raise ValueError("snapshot identity does not match observation index")
    membership_identity = getattr(universe_context, "membership_identity", None)
    if membership_identity != observation_index.universe_membership_identity:
        raise ValueError("universe membership identity does not match observation index")
    benchmark = str(benchmark_symbol).strip().upper()
    if benchmark != observation_index.benchmark_symbol:
        raise ValueError("benchmark does not match observation index")
    source = prepare_neutral_research_feature_source(
        snapshot,
        observation_index.symbols,
        benchmark_symbol=benchmark,
        universe_context=universe_context,
        start_date=observation_index.requested_start_date,
        through_date=observation_index.requested_through_date,
        cache=cache,
    )
    return attach_features_to_observation_index(
        observation_index,
        source,
        spec=NEUTRAL_RESEARCH_FEATURE_PANEL_V1,
    )
