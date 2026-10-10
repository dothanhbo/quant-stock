from __future__ import annotations

"""Immutable contracts for exact-date numeric feature attachment."""

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Mapping

import pandas as pd

from quantlab.identity import canonical_identity_value, canonical_json

from .contracts import OBSERVATION_INDEX_COLUMNS, ObservationSessionAudit


FEATURE_PANEL_CONTRACT = "quantlab.point_in_time_feature_panel"
FEATURE_PANEL_VERSION = "v1"
FEATURE_AVAILABILITY_SUFFIX = "__availability"
FEATURE_DIAGNOSTIC_COLUMNS = (
    "requested_feature_count",
    "available_feature_count",
    "complete_feature_row",
)
FEATURE_MISSINGNESS_PRECEDENCE = (
    "OBSERVATION_MARKET_ROW_MISSING",
    "SOURCE_SYMBOL_MISSING",
    "SOURCE_DATE_MISSING",
    "SOURCE_VALUE_MISSING",
    "SOURCE_VALUE_NONFINITE",
    "AVAILABLE",
)
SUPPORTED_NUMERIC_TYPES = ("float", "integer", "boolean")


class FeatureValueAvailability(str, Enum):
    AVAILABLE = "AVAILABLE"
    OBSERVATION_MARKET_ROW_MISSING = "OBSERVATION_MARKET_ROW_MISSING"
    SOURCE_SYMBOL_MISSING = "SOURCE_SYMBOL_MISSING"
    SOURCE_DATE_MISSING = "SOURCE_DATE_MISSING"
    SOURCE_VALUE_MISSING = "SOURCE_VALUE_MISSING"
    SOURCE_VALUE_NONFINITE = "SOURCE_VALUE_NONFINITE"


@dataclass(frozen=True, slots=True)
class FeatureFieldSpec:
    source_column: str
    output_name: str
    version: str
    description: str
    expected_numeric_type: str
    finite_required: bool = True

    def __post_init__(self) -> None:
        source = str(self.source_column).strip()
        output = str(self.output_name).strip()
        version = str(self.version).strip()
        description = str(self.description).strip()
        numeric_type = str(self.expected_numeric_type).strip().lower()
        if not source or not output or not version or not description:
            raise ValueError("feature source/output names, version, and description are required")
        if output.endswith(FEATURE_AVAILABILITY_SUFFIX):
            raise ValueError(f"feature output names must not end with {FEATURE_AVAILABILITY_SUFFIX}")
        if numeric_type not in SUPPORTED_NUMERIC_TYPES:
            raise ValueError("expected_numeric_type must be float, integer, or boolean")
        if not isinstance(self.finite_required, bool):
            raise TypeError("finite_required must be boolean")
        object.__setattr__(self, "source_column", source)
        object.__setattr__(self, "output_name", output)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "expected_numeric_type", numeric_type)

    @property
    def output_dtype(self) -> str:
        return {
            "float": "Float64",
            "integer": "Int64",
            "boolean": "Int8",
        }[self.expected_numeric_type]

    def canonical_content(self) -> dict[str, Any]:
        return {
            "source_column": self.source_column,
            "output_name": self.output_name,
            "version": self.version,
            "description": self.description,
            "expected_numeric_type": self.expected_numeric_type,
            "output_dtype": self.output_dtype,
            "finite_required": self.finite_required,
        }


@dataclass(frozen=True, slots=True)
class PointInTimeFeaturePanelSpec:
    name: str
    version: str
    fields: tuple[FeatureFieldSpec, ...]
    missingness_precedence: tuple[str, ...] = FEATURE_MISSINGNESS_PRECEDENCE
    fingerprint: str = field(init=False)
    output_columns: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        name, version = str(self.name).strip(), str(self.version).strip()
        fields = tuple(self.fields)
        if not name or not version:
            raise ValueError("feature-panel spec name and version are required")
        if not fields:
            raise ValueError("feature-panel specification must contain at least one field")
        if any(not isinstance(item, FeatureFieldSpec) for item in fields):
            raise TypeError("feature-panel fields must be FeatureFieldSpec instances")
        outputs = tuple(item.output_name for item in fields)
        sources = tuple(item.source_column for item in fields)
        if len(set(outputs)) != len(outputs):
            raise ValueError("duplicate canonical feature output name")
        if len(set(sources)) != len(sources):
            raise ValueError("duplicate feature source column is ambiguous")
        reserved = set(OBSERVATION_INDEX_COLUMNS) | set(FEATURE_DIAGNOSTIC_COLUMNS)
        status_names = {f"{name}{FEATURE_AVAILABILITY_SUFFIX}" for name in outputs}
        if any(name in reserved for name in outputs):
            raise ValueError("feature output name collides with a base or diagnostic column")
        if any(name in reserved or name in outputs for name in status_names):
            raise ValueError("feature availability column collides with another output column")
        if tuple(self.missingness_precedence) != FEATURE_MISSINGNESS_PRECEDENCE:
            raise ValueError("unsupported feature missingness precedence")
        output_columns = list(OBSERVATION_INDEX_COLUMNS)
        for item in fields:
            output_columns.extend((item.output_name, f"{item.output_name}{FEATURE_AVAILABILITY_SUFFIX}"))
        output_columns.extend(FEATURE_DIAGNOSTIC_COLUMNS)
        payload = {
            "contract": {"name": FEATURE_PANEL_CONTRACT, "version": FEATURE_PANEL_VERSION},
            "name": name,
            "version": version,
            "fields": [item.canonical_content() for item in fields],
            "missingness_precedence": list(self.missingness_precedence),
            "output_columns": output_columns,
        }
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "fields", fields)
        object.__setattr__(self, "output_columns", tuple(output_columns))
        object.__setattr__(self, "fingerprint", sha256(canonical_json(payload)).hexdigest())


def _immutable(value: Any) -> bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, tuple):
        return all(_immutable(item) for item in value)
    if isinstance(value, MappingProxyType):
        return all(isinstance(key, str) and _immutable(item) for key, item in value.items())
    return False


def feature_content_identity(
    *,
    observation_content_identity: str,
    feature_source_identity: str,
    frame: pd.DataFrame,
    spec: PointInTimeFeaturePanelSpec,
    value_evidence: tuple[tuple[tuple[Any, ...], ...], ...],
    batch_size: int = 2_048,
) -> str:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
        raise ValueError("feature content hash batch_size must be a positive integer")
    if len(value_evidence) != len(frame):
        raise ValueError("feature identity evidence must align with panel rows")
    digest = sha256()
    digest.update(b'{"feature_source_identity":')
    digest.update(canonical_json(feature_source_identity))
    digest.update(b',"observation_content_identity":')
    digest.update(canonical_json(observation_content_identity))
    digest.update(b',"rows":[')
    first = True
    column_positions = {name: position for position, name in enumerate(frame.columns)}
    for offset in range(0, len(frame), batch_size):
        batch = frame.iloc[offset:offset + batch_size]
        for local_index, row in enumerate(batch.itertuples(index=False, name=None)):
            absolute_index = offset + local_index
            feature_items = []
            for field_index, field_spec in enumerate(spec.fields):
                status_name = f"{field_spec.output_name}{FEATURE_AVAILABILITY_SUFFIX}"
                feature_items.append({
                    "output_name": field_spec.output_name,
                    "availability": row[column_positions[status_name]],
                    "value_evidence": value_evidence[absolute_index][field_index],
                })
            payload = {
                "session_date": row[column_positions["session_date"]],
                "symbol": row[column_positions["symbol"]],
                "features": feature_items,
                "requested_feature_count": int(row[column_positions["requested_feature_count"]]),
                "available_feature_count": int(row[column_positions["available_feature_count"]]),
                "complete_feature_row": bool(row[column_positions["complete_feature_row"]]),
            }
            if not first:
                digest.update(b",")
            digest.update(canonical_json(canonical_identity_value(payload)))
            first = False
    digest.update(b"]}")
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class PointInTimeFeaturePanel:
    spec: PointInTimeFeaturePanelSpec
    observation_index_identity: str
    observation_content_identity: str
    snapshot_id: str
    universe_membership_identity: str
    feature_source_identity: str
    session_audit: tuple[ObservationSessionAudit, ...]
    metadata: Mapping[str, Any]
    _frame: pd.DataFrame = field(repr=False, compare=False)
    _observation_keys: tuple[tuple[str, str], ...] = field(repr=False, compare=False)
    _value_evidence: tuple[tuple[tuple[Any, ...], ...], ...] = field(repr=False, compare=False)
    observation_row_count: int = field(init=False)
    requested_feature_count: int = field(init=False)
    complete_feature_row_count: int = field(init=False)
    incomplete_feature_row_count: int = field(init=False)
    per_feature_available_counts: Mapping[str, int] = field(init=False)
    per_feature_status_counts: Mapping[str, Mapping[str, int]] = field(init=False)
    effective_first_session_date: str = field(init=False)
    effective_last_session_date: str = field(init=False)
    symbols: tuple[str, ...] = field(init=False)
    feature_content_identity: str = field(init=False)
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.spec, PointInTimeFeaturePanelSpec):
            raise TypeError("spec must be PointInTimeFeaturePanelSpec")
        for name in (
            "observation_index_identity", "observation_content_identity", "snapshot_id",
            "universe_membership_identity", "feature_source_identity",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.metadata, MappingProxyType) or not _immutable(self.metadata):
            raise ValueError("feature-panel metadata must be immutable")
        frame = self._frame.copy(deep=True)
        if tuple(frame.columns) != self.spec.output_columns:
            raise ValueError("unsupported feature-panel output schema")
        keys = tuple(zip(frame["session_date"].astype(str), frame["symbol"].astype(str)))
        if keys != tuple(self._observation_keys):
            raise ValueError("feature attachment mutated observation keys or row order")
        requested = len(self.spec.fields)
        if not bool((frame["requested_feature_count"].astype(int) == requested).all()):
            raise ValueError("requested feature diagnostics are inconsistent")
        available_counts: dict[str, int] = {}
        status_counts: dict[str, Mapping[str, int]] = {}
        allowed_statuses = tuple(item.value for item in FeatureValueAvailability)
        for item in self.spec.fields:
            value_column = item.output_name
            status_column = f"{item.output_name}{FEATURE_AVAILABILITY_SUFFIX}"
            statuses = frame[status_column].astype(str)
            unsupported = sorted(set(statuses).difference(allowed_statuses))
            if unsupported:
                raise ValueError(f"unsupported feature availability status for {item.output_name}")
            available = statuses == FeatureValueAvailability.AVAILABLE.value
            if frame.loc[available, value_column].isna().any():
                raise ValueError("available feature rows must carry a numeric value")
            if frame.loc[~available, value_column].notna().any():
                raise ValueError("unavailable feature rows must not carry emitted values")
            available_counts[item.output_name] = int(available.sum())
            status_counts[item.output_name] = MappingProxyType({
                status: int((statuses == status).sum()) for status in allowed_statuses
            })
        row_available = frame[
            [f"{item.output_name}{FEATURE_AVAILABILITY_SUFFIX}" for item in self.spec.fields]
        ].eq(FeatureValueAvailability.AVAILABLE.value).sum(axis=1)
        if not frame["available_feature_count"].astype(int).equals(row_available.astype(int)):
            raise ValueError("available feature diagnostics are inconsistent")
        complete = row_available == requested
        if not frame["complete_feature_row"].astype(bool).equals(complete.astype(bool)):
            raise ValueError("complete feature-row diagnostics are inconsistent")
        audits = tuple(self.session_audit)
        if not audits:
            raise ValueError("feature panel requires observation session audits")
        content_identity = feature_content_identity(
            observation_content_identity=self.observation_content_identity,
            feature_source_identity=self.feature_source_identity,
            frame=frame,
            spec=self.spec,
            value_evidence=tuple(self._value_evidence),
        )
        symbols = tuple(sorted(set(frame["symbol"].astype(str))))
        complete_count = int(complete.sum())
        payload = {
            "contract": {"name": FEATURE_PANEL_CONTRACT, "version": FEATURE_PANEL_VERSION},
            "observation_index_identity": self.observation_index_identity,
            "snapshot_id": self.snapshot_id,
            "universe_membership_identity": self.universe_membership_identity,
            "panel_spec_fingerprint": self.spec.fingerprint,
            "feature_source_identity": self.feature_source_identity,
            "feature_content_identity": content_identity,
            "observation_row_count": len(frame),
            "requested_feature_count": requested,
            "complete_feature_row_count": complete_count,
            "incomplete_feature_row_count": len(frame) - complete_count,
            "per_feature_available_counts": available_counts,
            "per_feature_status_counts": {
                name: dict(counts) for name, counts in status_counts.items()
            },
            "effective_first_session_date": audits[0].session_date,
            "effective_last_session_date": audits[-1].session_date,
            "symbols": list(symbols),
            "metadata": self.metadata,
        }
        object.__setattr__(self, "session_audit", audits)
        object.__setattr__(self, "_frame", frame)
        object.__setattr__(self, "_observation_keys", keys)
        object.__setattr__(self, "_value_evidence", tuple(self._value_evidence))
        object.__setattr__(self, "observation_row_count", len(frame))
        object.__setattr__(self, "requested_feature_count", requested)
        object.__setattr__(self, "complete_feature_row_count", complete_count)
        object.__setattr__(self, "incomplete_feature_row_count", len(frame) - complete_count)
        object.__setattr__(self, "per_feature_available_counts", MappingProxyType(available_counts))
        object.__setattr__(self, "per_feature_status_counts", MappingProxyType(status_counts))
        object.__setattr__(self, "effective_first_session_date", audits[0].session_date)
        object.__setattr__(self, "effective_last_session_date", audits[-1].session_date)
        object.__setattr__(self, "symbols", symbols)
        object.__setattr__(self, "feature_content_identity", content_identity)
        object.__setattr__(self, "identity", sha256(canonical_json(canonical_identity_value(payload))).hexdigest())

    @property
    def frame(self) -> pd.DataFrame:
        return self._frame.copy(deep=True)
