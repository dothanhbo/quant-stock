from __future__ import annotations

"""Pure key-based assembly of point-in-time features and future outcomes."""

from types import MappingProxyType
from typing import Any

import pandas as pd

from .contracts import OBSERVATION_INDEX_COLUMNS, PointInTimeObservationIndex
from .feature_contracts import PointInTimeFeaturePanel
from .outcome_contracts import OUTCOME_BASE_COLUMNS, PointInTimeOutcomePanel
from .research_dataset_contracts import (
    PERMITTED_USE,
    POINT_IN_TIME_NEUTRAL_RESEARCH_DATASET_V1,
    RESEARCH_DATASET_JOIN_KEY,
    PointInTimeResearchDataset,
    PointInTimeResearchDatasetSpec,
)


def _keys(frame: pd.DataFrame, *, source_name: str) -> tuple[tuple[str, str], ...]:
    missing = set(RESEARCH_DATASET_JOIN_KEY).difference(frame.columns)
    if missing:
        raise ValueError(f"{source_name} is missing canonical join keys: {', '.join(sorted(missing))}")
    keys = tuple(zip(frame["session_date"].astype(str), frame["symbol"].astype(str)))
    if len(set(keys)) != len(keys):
        raise ValueError(f"{source_name} contains duplicate canonical observation keys")
    return keys


def _validate_population(
    reference_keys: tuple[tuple[str, str], ...],
    source_keys: tuple[tuple[str, str], ...],
    *,
    source_name: str,
) -> None:
    reference_set, source_set = set(reference_keys), set(source_keys)
    missing = sorted(reference_set.difference(source_set))
    extra = sorted(source_set.difference(reference_set))
    if missing:
        raise ValueError(f"{source_name} is missing observation keys: {missing[:3]}")
    if extra:
        raise ValueError(f"{source_name} contains extra observation keys: {extra[:3]}")
    if source_keys != reference_keys:
        raise ValueError(f"{source_name} changed canonical observation row order")


def _assert_shared_columns(
    reference: pd.DataFrame,
    source: pd.DataFrame,
    columns: tuple[str, ...],
    *,
    source_name: str,
) -> None:
    try:
        pd.testing.assert_frame_equal(
            reference.loc[:, columns].reset_index(drop=True),
            source.loc[:, columns].reset_index(drop=True),
            check_exact=True,
            check_dtype=True,
        )
    except AssertionError as exc:
        raise ValueError(f"{source_name} observation diagnostics do not match Phase 5.1") from exc


def _key_reindex(
    frame: pd.DataFrame,
    reference_keys: tuple[tuple[str, str], ...],
    columns: tuple[str, ...],
) -> pd.DataFrame:
    if not reference_keys:
        return frame.loc[:, columns].iloc[0:0].reset_index(drop=True)
    keyed = frame.set_index(list(RESEARCH_DATASET_JOIN_KEY), drop=False)
    ordered_index = pd.MultiIndex.from_tuples(reference_keys, names=RESEARCH_DATASET_JOIN_KEY)
    return keyed.reindex(ordered_index).loc[:, columns].reset_index(drop=True)


def _freeze_counts(values: dict[str, int]) -> MappingProxyType:
    return MappingProxyType(dict(values))


def build_point_in_time_research_dataset(
    observation_index: PointInTimeObservationIndex,
    feature_panel: PointInTimeFeaturePanel,
    outcome_panel: PointInTimeOutcomePanel,
    *,
    spec: PointInTimeResearchDatasetSpec = POINT_IN_TIME_NEUTRAL_RESEARCH_DATASET_V1,
) -> PointInTimeResearchDataset:
    """Validate and key-join already-built panels without I/O or computation."""
    if not isinstance(observation_index, PointInTimeObservationIndex):
        raise TypeError("observation_index must be PointInTimeObservationIndex")
    if not isinstance(feature_panel, PointInTimeFeaturePanel):
        raise TypeError("feature_panel must be PointInTimeFeaturePanel")
    if not isinstance(outcome_panel, PointInTimeOutcomePanel):
        raise TypeError("outcome_panel must be PointInTimeOutcomePanel")
    if not isinstance(spec, PointInTimeResearchDatasetSpec):
        raise TypeError("spec must be PointInTimeResearchDatasetSpec")
    if feature_panel.spec.fingerprint != spec.feature_panel_spec.fingerprint:
        raise ValueError("feature-panel specification does not match research-dataset specification")
    if outcome_panel.spec.fingerprint != spec.outcome_panel_spec.fingerprint:
        raise ValueError("outcome-panel specification does not match research-dataset specification")

    provenance = {
        "observation_index_identity": observation_index.identity,
        "observation_content_identity": observation_index.content_identity,
        "snapshot_id": observation_index.snapshot_id,
        "universe_membership_identity": observation_index.universe_membership_identity,
    }
    feature_provenance = {
        "observation_index_identity": feature_panel.observation_index_identity,
        "observation_content_identity": feature_panel.observation_content_identity,
        "snapshot_id": feature_panel.snapshot_id,
        "universe_membership_identity": feature_panel.universe_membership_identity,
    }
    outcome_provenance = {
        "observation_index_identity": outcome_panel.observation_index_identity,
        "observation_content_identity": outcome_panel.observation_content_identity,
        "snapshot_id": outcome_panel.snapshot_id,
        "universe_membership_identity": outcome_panel.universe_membership_identity,
    }
    for name, expected in provenance.items():
        if feature_provenance[name] != expected:
            raise ValueError(f"feature-panel provenance mismatch: {name}")
        if outcome_provenance[name] != expected:
            raise ValueError(f"outcome-panel provenance mismatch: {name}")
    if outcome_panel.benchmark_symbol != observation_index.benchmark_symbol:
        raise ValueError("outcome-panel benchmark does not match observation index")
    if (
        outcome_panel.requested_start_date != observation_index.requested_start_date
        or outcome_panel.requested_through_date != observation_index.requested_through_date
    ):
        raise ValueError("outcome-panel requested bounds do not match observation index")
    if tuple(feature_panel.session_audit) != tuple(observation_index.session_audit):
        raise ValueError("feature-panel observation session diagnostics do not match")
    if tuple(outcome_panel.session_audit) != tuple(observation_index.session_audit):
        raise ValueError("outcome-panel observation session diagnostics do not match")

    observation_frame = observation_index.frame
    feature_frame = feature_panel.frame
    outcome_frame = outcome_panel.frame
    declared_rows = (
        observation_index.total_membership_row_count,
        feature_panel.observation_row_count,
        outcome_panel.observation_row_count,
    )
    actual_rows = (len(observation_frame), len(feature_frame), len(outcome_frame))
    if declared_rows != actual_rows:
        raise ValueError("source panel declared row counts do not match their frames")
    observation_keys = _keys(observation_frame, source_name="observation index")
    feature_keys = _keys(feature_frame, source_name="feature panel")
    outcome_keys = _keys(outcome_frame, source_name="outcome panel")
    _validate_population(observation_keys, feature_keys, source_name="feature panel")
    _validate_population(observation_keys, outcome_keys, source_name="outcome panel")
    _assert_shared_columns(
        observation_frame, feature_frame, tuple(OBSERVATION_INDEX_COLUMNS), source_name="feature panel",
    )
    _assert_shared_columns(
        observation_frame, outcome_frame, tuple(OUTCOME_BASE_COLUMNS), source_name="outcome panel",
    )

    feature_columns = (
        *spec.feature_columns,
        *spec.feature_availability_columns,
        *spec.feature_diagnostic_columns,
    )
    outcome_columns = (
        *spec.outcome_columns,
        *spec.outcome_availability_columns,
        *spec.outcome_diagnostic_columns,
    )
    feature_values = _key_reindex(feature_frame, observation_keys, feature_columns)
    outcome_values = _key_reindex(outcome_frame, observation_keys, outcome_columns)
    joined = pd.concat([
        observation_frame.loc[:, spec.observation_columns].reset_index(drop=True),
        feature_values,
        outcome_values,
    ], axis=1)
    joined = joined.loc[:, spec.output_columns].copy(deep=True)
    if len(joined) != len(observation_frame):
        raise ValueError("research-dataset join changed the observation population")

    per_feature = {
        name: int(joined[f"{name}__availability"].astype(str).eq("AVAILABLE").sum())
        for name in spec.feature_columns
    }
    per_horizon = {
        str(horizon): int(joined[f"outcome_{horizon}__availability"].astype(str).eq("AVAILABLE").sum())
        for horizon in spec.outcome_panel_spec.horizons
    }
    source_feature_counts = dict(feature_panel.per_feature_available_counts)
    if per_feature != source_feature_counts:
        raise ValueError("joined feature availability counts do not reconcile")
    source_outcome_counts = {
        str(horizon): int(outcome_panel.per_horizon_available_counts[horizon])
        for horizon in spec.outcome_panel_spec.horizons
    }
    if per_horizon != source_outcome_counts:
        raise ValueError("joined outcome availability counts do not reconcile")
    outcome_available = joined["available_horizon_count"].astype(int)
    diagnostics: dict[str, Any] = {
        "observation_row_count": len(joined),
        "session_count": len(observation_index.session_audit),
        "symbol_count": len(observation_index.symbols),
        "complete_feature_row_count": int(joined["complete_feature_row"].astype(bool).sum()),
        "fully_labeled_outcome_row_count": int(joined["fully_labeled_outcome_row"].astype(bool).sum()),
        "rows_with_any_available_outcome_count": int(outcome_available.gt(0).sum()),
        "rows_with_no_available_outcome_count": int(outcome_available.eq(0).sum()),
        "per_feature_availability_counts": _freeze_counts(per_feature),
        "per_horizon_outcome_availability_counts": _freeze_counts(per_horizon),
    }
    if diagnostics["complete_feature_row_count"] != feature_panel.complete_feature_row_count:
        raise ValueError("joined complete-feature count does not reconcile")
    if diagnostics["fully_labeled_outcome_row_count"] != outcome_panel.fully_labeled_row_count:
        raise ValueError("joined fully-labeled outcome count does not reconcile")
    metadata = MappingProxyType({
        "future_looking": True,
        "permitted_use": PERMITTED_USE,
        "production_signal_safe": False,
        "predictor_projection_use": "point_in_time_research_predictors_only",
        "observation_index_identity": observation_index.identity,
        "observation_content_identity": observation_index.content_identity,
        "feature_panel_identity": feature_panel.identity,
        "feature_content_identity": feature_panel.feature_content_identity,
        "outcome_panel_identity": outcome_panel.identity,
        "outcome_content_identity": outcome_panel.outcome_content_identity,
        "snapshot_id": observation_index.snapshot_id,
        "universe_membership_identity": observation_index.universe_membership_identity,
        "benchmark_symbol": observation_index.benchmark_symbol,
        "requested_start_date": observation_index.requested_start_date,
        "requested_through_date": observation_index.requested_through_date,
        **diagnostics,
    })
    return PointInTimeResearchDataset(
        spec=spec,
        observation_index_identity=observation_index.identity,
        observation_content_identity=observation_index.content_identity,
        feature_panel_identity=feature_panel.identity,
        feature_content_identity=feature_panel.feature_content_identity,
        outcome_panel_identity=outcome_panel.identity,
        outcome_content_identity=outcome_panel.outcome_content_identity,
        snapshot_id=observation_index.snapshot_id,
        universe_membership_identity=observation_index.universe_membership_identity,
        benchmark_symbol=observation_index.benchmark_symbol,
        requested_start_date=observation_index.requested_start_date,
        requested_through_date=observation_index.requested_through_date,
        session_audit=observation_index.session_audit,
        metadata=metadata,
        _frame=joined,
        _observation_keys=observation_keys,
    )
