from __future__ import annotations

"""Immutable contracts for strategy-neutral point-in-time research datasets."""

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import math
from types import MappingProxyType
from typing import Any, Mapping

import pandas as pd

from quantlab.identity import canonical_identity_value, canonical_json

from .contracts import OBSERVATION_INDEX_COLUMNS, ObservationSessionAudit
from .feature_contracts import (
    FEATURE_AVAILABILITY_SUFFIX,
    FEATURE_DIAGNOSTIC_COLUMNS,
    PointInTimeFeaturePanelSpec,
)
from .neutral_features import NEUTRAL_RESEARCH_FEATURE_PANEL_V1
from .outcome_contracts import (
    OUTCOME_BASE_COLUMNS,
    POINT_IN_TIME_FORWARD_OUTCOMES_5_10_20_V1,
    PointInTimeOutcomePanelSpec,
)


RESEARCH_DATASET_CONTRACT = "quantlab.point_in_time_research_dataset"
RESEARCH_DATASET_VERSION = "v1"
RESEARCH_DATASET_JOIN_KEY = ("session_date", "symbol")
POPULATION_RULE = "exact_phase_5_1_population_and_order_v1"
PREDICTOR_RULE = "observation_and_point_in_time_feature_groups_only_v1"
PERMITTED_USE = "offline_research_evaluation_only"
OUTCOME_DIAGNOSTIC_COLUMNS = (
    "requested_horizon_count",
    "available_horizon_count",
    "fully_labeled_outcome_row",
)


class ResearchDatasetUse(str, Enum):
    PREDICTOR_ONLY = "PREDICTOR_ONLY"
    OFFLINE_EVALUATION = "OFFLINE_EVALUATION"


def _outcome_groups(
    spec: PointInTimeOutcomePanelSpec,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    values: list[str] = []
    availability: list[str] = []
    for horizon in spec.horizons:
        values.extend((
            f"target_session_{horizon}",
            f"stock_forward_return_{horizon}_pct",
            f"benchmark_forward_return_{horizon}_pct",
            f"excess_forward_return_{horizon}_pct_points",
        ))
        availability.append(f"outcome_{horizon}__availability")
    return tuple(values), tuple(availability), OUTCOME_DIAGNOSTIC_COLUMNS


@dataclass(frozen=True, slots=True)
class PointInTimeResearchDatasetSpec:
    name: str
    version: str
    feature_panel_spec: PointInTimeFeaturePanelSpec
    outcome_panel_spec: PointInTimeOutcomePanelSpec
    join_key: tuple[str, ...] = RESEARCH_DATASET_JOIN_KEY
    population_rule: str = POPULATION_RULE
    predictor_rule: str = PREDICTOR_RULE
    future_looking: bool = True
    permitted_use: str = PERMITTED_USE
    production_signal_safe: bool = False
    observation_columns: tuple[str, ...] = field(init=False)
    feature_columns: tuple[str, ...] = field(init=False)
    feature_availability_columns: tuple[str, ...] = field(init=False)
    feature_diagnostic_columns: tuple[str, ...] = field(init=False)
    outcome_columns: tuple[str, ...] = field(init=False)
    outcome_availability_columns: tuple[str, ...] = field(init=False)
    outcome_diagnostic_columns: tuple[str, ...] = field(init=False)
    forbidden_predictor_columns: tuple[str, ...] = field(init=False)
    predictor_columns: tuple[str, ...] = field(init=False)
    output_columns: tuple[str, ...] = field(init=False)
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        name, version = str(self.name).strip(), str(self.version).strip()
        if not name or not version:
            raise ValueError("research-dataset spec name and version are required")
        if not isinstance(self.feature_panel_spec, PointInTimeFeaturePanelSpec):
            raise TypeError("feature_panel_spec must be PointInTimeFeaturePanelSpec")
        if not isinstance(self.outcome_panel_spec, PointInTimeOutcomePanelSpec):
            raise TypeError("outcome_panel_spec must be PointInTimeOutcomePanelSpec")
        if tuple(self.join_key) != RESEARCH_DATASET_JOIN_KEY:
            raise ValueError("research-dataset join key must be session_date and symbol")
        if self.population_rule != POPULATION_RULE or self.predictor_rule != PREDICTOR_RULE:
            raise ValueError("unsupported research-dataset population or predictor rule")
        if (
            self.future_looking is not True
            or self.permitted_use != PERMITTED_USE
            or self.production_signal_safe is not False
        ):
            raise ValueError("research datasets are restricted to offline evaluation")
        observation = tuple(OBSERVATION_INDEX_COLUMNS)
        feature_values = tuple(item.output_name for item in self.feature_panel_spec.fields)
        feature_availability = tuple(
            f"{item.output_name}{FEATURE_AVAILABILITY_SUFFIX}"
            for item in self.feature_panel_spec.fields
        )
        feature_diagnostics = tuple(FEATURE_DIAGNOSTIC_COLUMNS)
        outcome_values, outcome_availability, outcome_diagnostics = _outcome_groups(
            self.outcome_panel_spec,
        )
        expected_feature_schema = list(observation)
        for value, status in zip(feature_values, feature_availability):
            expected_feature_schema.extend((value, status))
        expected_feature_schema.extend(feature_diagnostics)
        if tuple(expected_feature_schema) != self.feature_panel_spec.output_columns:
            raise ValueError("feature-panel specification has an unsupported schema")
        expected_outcome_schema = (
            *OUTCOME_BASE_COLUMNS,
            *tuple(
                column
                for horizon in self.outcome_panel_spec.horizons
                for column in (
                    f"target_session_{horizon}",
                    f"stock_forward_return_{horizon}_pct",
                    f"benchmark_forward_return_{horizon}_pct",
                    f"excess_forward_return_{horizon}_pct_points",
                    f"outcome_{horizon}__availability",
                )
            ),
            *outcome_diagnostics,
        )
        if expected_outcome_schema != self.outcome_panel_spec.output_columns:
            raise ValueError("outcome-panel specification has an unsupported schema")
        forbidden = (*outcome_values, *outcome_availability, *outcome_diagnostics)
        predictor = (*observation, *feature_values, *feature_availability, *feature_diagnostics)
        output = (*predictor, *forbidden)
        if len(set(output)) != len(output):
            raise ValueError("research-dataset schema contains duplicate columns")
        payload = {
            "contract": {"name": RESEARCH_DATASET_CONTRACT, "version": RESEARCH_DATASET_VERSION},
            "name": name,
            "version": version,
            "feature_panel_spec_fingerprint": self.feature_panel_spec.fingerprint,
            "outcome_panel_spec_fingerprint": self.outcome_panel_spec.fingerprint,
            "join_key": list(self.join_key),
            "population_rule": self.population_rule,
            "predictor_rule": self.predictor_rule,
            "observation_columns": list(observation),
            "feature_columns": list(feature_values),
            "feature_availability_columns": list(feature_availability),
            "feature_diagnostic_columns": list(feature_diagnostics),
            "outcome_columns": list(outcome_values),
            "outcome_availability_columns": list(outcome_availability),
            "outcome_diagnostic_columns": list(outcome_diagnostics),
            "forbidden_predictor_columns": list(forbidden),
            "output_columns": list(output),
            "future_looking": True,
            "permitted_use": PERMITTED_USE,
            "production_signal_safe": False,
        }
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "join_key", tuple(self.join_key))
        object.__setattr__(self, "observation_columns", observation)
        object.__setattr__(self, "feature_columns", feature_values)
        object.__setattr__(self, "feature_availability_columns", feature_availability)
        object.__setattr__(self, "feature_diagnostic_columns", feature_diagnostics)
        object.__setattr__(self, "outcome_columns", outcome_values)
        object.__setattr__(self, "outcome_availability_columns", outcome_availability)
        object.__setattr__(self, "outcome_diagnostic_columns", outcome_diagnostics)
        object.__setattr__(self, "forbidden_predictor_columns", forbidden)
        object.__setattr__(self, "predictor_columns", predictor)
        object.__setattr__(self, "output_columns", output)
        object.__setattr__(self, "fingerprint", sha256(canonical_json(payload)).hexdigest())


POINT_IN_TIME_NEUTRAL_RESEARCH_DATASET_V1 = PointInTimeResearchDatasetSpec(
    name="point_in_time_neutral_research_dataset",
    version="v1",
    feature_panel_spec=NEUTRAL_RESEARCH_FEATURE_PANEL_V1,
    outcome_panel_spec=POINT_IN_TIME_FORWARD_OUTCOMES_5_10_20_V1,
)


def _immutable(value: Any) -> bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, tuple):
        return all(_immutable(item) for item in value)
    if isinstance(value, MappingProxyType):
        return all(isinstance(key, str) and _immutable(item) for key, item in value.items())
    return False


def _identity_scalar(value: Any) -> Any:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def research_dataset_content_identity(
    *,
    observation_content_identity: str,
    feature_content_identity: str,
    outcome_content_identity: str,
    frame: pd.DataFrame,
    spec: PointInTimeResearchDatasetSpec,
    diagnostics: Mapping[str, Any],
    batch_size: int = 2_048,
) -> str:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
        raise ValueError("research-dataset content hash batch_size must be a positive integer")
    digest = sha256()
    prefix = {
        "observation_content_identity": observation_content_identity,
        "feature_content_identity": feature_content_identity,
        "outcome_content_identity": outcome_content_identity,
        "schema": list(spec.output_columns),
        "diagnostics": diagnostics,
    }
    digest.update(b'{"header":')
    digest.update(canonical_json(canonical_identity_value(prefix)))
    digest.update(b',"rows":[')
    first = True
    for offset in range(0, len(frame), batch_size):
        for row in frame.iloc[offset:offset + batch_size].itertuples(index=False, name=None):
            if not first:
                digest.update(b",")
            content = {
                name: _identity_scalar(value)
                for name, value in zip(spec.output_columns, row)
            }
            digest.update(canonical_json(canonical_identity_value(content)))
            first = False
    digest.update(b"]}")
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class PointInTimeResearchDataset:
    spec: PointInTimeResearchDatasetSpec
    observation_index_identity: str
    observation_content_identity: str
    feature_panel_identity: str
    feature_content_identity: str
    outcome_panel_identity: str
    outcome_content_identity: str
    snapshot_id: str
    universe_membership_identity: str
    benchmark_symbol: str
    requested_start_date: str
    requested_through_date: str
    session_audit: tuple[ObservationSessionAudit, ...]
    metadata: Mapping[str, Any]
    _frame: pd.DataFrame = field(repr=False, compare=False)
    _observation_keys: tuple[tuple[str, str], ...] = field(repr=False, compare=False)
    observation_row_count: int = field(init=False)
    session_count: int = field(init=False)
    symbol_count: int = field(init=False)
    complete_feature_row_count: int = field(init=False)
    fully_labeled_outcome_row_count: int = field(init=False)
    rows_with_any_available_outcome_count: int = field(init=False)
    rows_with_no_available_outcome_count: int = field(init=False)
    per_feature_availability_counts: Mapping[str, int] = field(init=False)
    per_horizon_outcome_availability_counts: Mapping[int, int] = field(init=False)
    content_identity: str = field(init=False)
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.spec, PointInTimeResearchDatasetSpec):
            raise TypeError("spec must be PointInTimeResearchDatasetSpec")
        for name in (
            "observation_index_identity", "observation_content_identity", "feature_panel_identity",
            "feature_content_identity", "outcome_panel_identity", "outcome_content_identity",
            "snapshot_id", "universe_membership_identity", "benchmark_symbol",
            "requested_start_date", "requested_through_date",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.metadata, MappingProxyType) or not _immutable(self.metadata):
            raise ValueError("research-dataset metadata must be immutable")
        if (
            self.metadata.get("future_looking") is not True
            or self.metadata.get("permitted_use") != PERMITTED_USE
            or self.metadata.get("production_signal_safe") is not False
        ):
            raise ValueError("research-dataset metadata must retain the offline-only boundary")
        audits = tuple(self.session_audit)
        if not audits:
            raise ValueError("research dataset requires observation session audits")
        frame = self._frame.copy(deep=True)
        if tuple(frame.columns) != self.spec.output_columns:
            raise ValueError("unsupported research-dataset output schema")
        keys = tuple(zip(frame["session_date"].astype(str), frame["symbol"].astype(str)))
        if keys != tuple(self._observation_keys):
            raise ValueError("research dataset mutated observation keys or row order")
        if len(set(keys)) != len(keys):
            raise ValueError("research dataset contains duplicate observation keys")
        feature_complete = int(frame["complete_feature_row"].astype(bool).sum())
        outcome_available = frame["available_horizon_count"].astype(int)
        fully_labeled = int(frame["fully_labeled_outcome_row"].astype(bool).sum())
        any_outcome = int(outcome_available.gt(0).sum())
        no_outcome = int(outcome_available.eq(0).sum())
        per_feature = {
            name: int(frame[f"{name}{FEATURE_AVAILABILITY_SUFFIX}"].astype(str).eq("AVAILABLE").sum())
            for name in self.spec.feature_columns
        }
        per_horizon = {
            horizon: int(frame[f"outcome_{horizon}__availability"].astype(str).eq("AVAILABLE").sum())
            for horizon in self.spec.outcome_panel_spec.horizons
        }
        diagnostics = {
            "observation_row_count": len(frame),
            "session_count": len(audits),
            "symbol_count": len(set(frame["symbol"].astype(str))),
            "complete_feature_row_count": feature_complete,
            "fully_labeled_outcome_row_count": fully_labeled,
            "rows_with_any_available_outcome_count": any_outcome,
            "rows_with_no_available_outcome_count": no_outcome,
            "per_feature_availability_counts": per_feature,
            "per_horizon_outcome_availability_counts": {
                str(horizon): per_horizon[horizon]
                for horizon in self.spec.outcome_panel_spec.horizons
            },
        }
        for name, expected in diagnostics.items():
            actual = self.metadata.get(name)
            if isinstance(expected, dict):
                actual = dict(actual or {})
            if actual != expected:
                raise ValueError(f"research-dataset metadata is inconsistent: {name}")
        content_identity = research_dataset_content_identity(
            observation_content_identity=self.observation_content_identity,
            feature_content_identity=self.feature_content_identity,
            outcome_content_identity=self.outcome_content_identity,
            frame=frame,
            spec=self.spec,
            diagnostics=diagnostics,
        )
        benchmark = self.benchmark_symbol.strip().upper()
        payload = {
            "contract": {"name": RESEARCH_DATASET_CONTRACT, "version": RESEARCH_DATASET_VERSION},
            "observation_index_identity": self.observation_index_identity,
            "feature_panel_identity": self.feature_panel_identity,
            "outcome_panel_identity": self.outcome_panel_identity,
            "snapshot_id": self.snapshot_id,
            "universe_membership_identity": self.universe_membership_identity,
            "benchmark_symbol": benchmark,
            "requested_start_date": self.requested_start_date,
            "requested_through_date": self.requested_through_date,
            "specification_fingerprint": self.spec.fingerprint,
            "content_identity": content_identity,
            "metadata": self.metadata,
        }
        object.__setattr__(self, "benchmark_symbol", benchmark)
        object.__setattr__(self, "session_audit", audits)
        object.__setattr__(self, "_frame", frame)
        object.__setattr__(self, "_observation_keys", keys)
        object.__setattr__(self, "observation_row_count", len(frame))
        object.__setattr__(self, "session_count", len(audits))
        object.__setattr__(self, "symbol_count", diagnostics["symbol_count"])
        object.__setattr__(self, "complete_feature_row_count", feature_complete)
        object.__setattr__(self, "fully_labeled_outcome_row_count", fully_labeled)
        object.__setattr__(self, "rows_with_any_available_outcome_count", any_outcome)
        object.__setattr__(self, "rows_with_no_available_outcome_count", no_outcome)
        object.__setattr__(self, "per_feature_availability_counts", MappingProxyType(per_feature))
        object.__setattr__(self, "per_horizon_outcome_availability_counts", MappingProxyType(per_horizon))
        object.__setattr__(self, "content_identity", content_identity)
        object.__setattr__(self, "identity", sha256(canonical_json(canonical_identity_value(payload))).hexdigest())

    @property
    def observation_columns(self) -> tuple[str, ...]:
        return self.spec.observation_columns

    @property
    def feature_columns(self) -> tuple[str, ...]:
        return self.spec.feature_columns

    @property
    def feature_availability_columns(self) -> tuple[str, ...]:
        return self.spec.feature_availability_columns

    @property
    def feature_diagnostic_columns(self) -> tuple[str, ...]:
        return self.spec.feature_diagnostic_columns

    @property
    def outcome_columns(self) -> tuple[str, ...]:
        return self.spec.outcome_columns

    @property
    def outcome_availability_columns(self) -> tuple[str, ...]:
        return self.spec.outcome_availability_columns

    @property
    def outcome_diagnostic_columns(self) -> tuple[str, ...]:
        return self.spec.outcome_diagnostic_columns

    @property
    def forbidden_predictor_columns(self) -> tuple[str, ...]:
        return self.spec.forbidden_predictor_columns

    @property
    def research_use(self) -> ResearchDatasetUse:
        return ResearchDatasetUse.OFFLINE_EVALUATION

    @property
    def predictor_projection_use(self) -> ResearchDatasetUse:
        return ResearchDatasetUse.PREDICTOR_ONLY

    def predictor_frame(self) -> pd.DataFrame:
        """Return the point-in-time-only projection; never includes outcomes."""
        return self._frame.loc[:, self.spec.predictor_columns].copy(deep=True)

    def evaluation_frame(self) -> pd.DataFrame:
        """Return complete future-looking content for offline research only."""
        return self._frame.copy(deep=True)
