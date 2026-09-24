from __future__ import annotations

"""Pure exact-date attachment of precomputed numeric features to observations."""

from numbers import Integral, Real
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable

import numpy as np
import pandas as pd

from .contracts import OBSERVATION_INDEX_COLUMNS, PointInTimeObservationIndex
from .feature_contracts import (
    FEATURE_AVAILABILITY_SUFFIX,
    FeatureFieldSpec,
    FeatureValueAvailability,
    PointInTimeFeaturePanel,
    PointInTimeFeaturePanelSpec,
)


@runtime_checkable
class PreparedFeatureSource(Protocol):
    computation_identity: Any
    available_symbols: tuple[str, ...]
    missing_symbols: tuple[str, ...]
    metadata: Mapping[str, Any]

    def frame_for(self, symbol: str) -> pd.DataFrame: ...


def _source_identity(source: Any) -> str:
    value = source.computation_identity
    identity = value if isinstance(value, str) else getattr(value, "sha256", None)
    if not isinstance(identity, str) or not identity.strip():
        raise ValueError("feature source computation identity must be non-empty")
    return identity.strip()


def _normalized_symbols(values: Any, *, name: str) -> tuple[str, ...]:
    try:
        raw = tuple(values)
    except TypeError as exc:
        raise TypeError(f"feature source {name} must be an iterable of symbols") from exc
    normalized = tuple(str(value).strip().upper() for value in raw)
    if any(not value for value in normalized):
        raise ValueError(f"feature source {name} contains an empty symbol")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"feature source {name} contains duplicate normalized symbols")
    return tuple(sorted(normalized))


def _date_text(value: Any, *, symbol: str) -> str:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"invalid feature date for {symbol}") from exc
    if pd.isna(timestamp):
        raise ValueError(f"invalid feature date for {symbol}")
    return timestamp.date().isoformat()


def _is_null(value: Any) -> bool:
    return value is None or value is pd.NA


def _numeric_value(value: Any, field: FeatureFieldSpec) -> tuple[Any, tuple[Any, ...]]:
    if _is_null(value):
        return None, ("null",)
    if isinstance(value, (str, bytes, bytearray, list, tuple, dict, set)):
        raise TypeError(f"unsupported non-numeric feature value: {field.source_column}")
    if not isinstance(value, (Real, np.number, np.bool_)):
        raise TypeError(f"unsupported feature value object: {field.source_column}")
    if isinstance(value, (bool, np.bool_)):
        if field.expected_numeric_type != "boolean":
            raise TypeError(f"boolean value requires boolean feature declaration: {field.source_column}")
        number = int(bool(value))
        return number, ("finite", number)
    if isinstance(value, Integral):
        integer = int(value)
        if field.expected_numeric_type == "boolean":
            if integer not in (0, 1):
                raise TypeError(f"boolean numeric feature must contain only 0/1: {field.source_column}")
            return integer, ("finite", integer)
        if field.expected_numeric_type == "integer":
            return integer, ("finite", integer)
        number = float(integer)
        return number, ("finite", number)
    number = float(value)
    if np.isnan(number):
        return None, ("nonfinite", "NaN")
    if np.isposinf(number):
        return (number if field.expected_numeric_type == "float" and not field.finite_required else None), ("nonfinite", "+Infinity")
    if np.isneginf(number):
        return (number if field.expected_numeric_type == "float" and not field.finite_required else None), ("nonfinite", "-Infinity")
    if field.expected_numeric_type == "integer":
        if not number.is_integer():
            raise TypeError(f"non-integral value for integer feature: {field.source_column}")
        integer = int(number)
        return integer, ("finite", integer)
    if field.expected_numeric_type == "boolean":
        if number not in (0.0, 1.0):
            raise TypeError(f"boolean numeric feature must contain only 0/1: {field.source_column}")
        integer = int(number)
        return integer, ("finite", integer)
    return number, ("finite", number)


def _validated_source_frames(
    source: PreparedFeatureSource,
    spec: PointInTimeFeaturePanelSpec,
) -> tuple[tuple[str, ...], tuple[str, ...], dict[str, dict[str, tuple[Any, ...]]]]:
    if not isinstance(source.metadata, Mapping):
        raise TypeError("feature source metadata must be a mapping")
    available = _normalized_symbols(source.available_symbols, name="available_symbols")
    missing = _normalized_symbols(source.missing_symbols, name="missing_symbols")
    overlap = sorted(set(available).intersection(missing))
    if overlap:
        raise ValueError("feature source symbols cannot be both available and missing")
    values: dict[str, dict[str, tuple[Any, ...]]] = {}
    required = tuple(item.source_column for item in spec.fields)
    exact_schema: tuple[str, ...] | None = None
    for symbol in available:
        frame = source.frame_for(symbol)
        if not isinstance(frame, pd.DataFrame):
            raise TypeError(f"feature source frame must be a DataFrame: {symbol}")
        absent = sorted({"time", *required}.difference(frame.columns))
        if absent:
            raise ValueError(f"feature source frame is missing columns for {symbol}: {absent}")
        current_schema = tuple(str(column) for column in frame.columns)
        if exact_schema is None:
            exact_schema = current_schema
        elif current_schema != exact_schema:
            raise ValueError("feature source frames have inconsistent column schemas")
        if "symbol" in frame.columns:
            frame_symbols = {
                str(value).strip().upper() for value in frame["symbol"] if not _is_null(value)
            }
            if frame_symbols and frame_symbols != {symbol}:
                raise ValueError(f"feature source frame symbol is inconsistent: {symbol}")
        dates = tuple(_date_text(value, symbol=symbol) for value in frame["time"])
        if dates != tuple(sorted(dates)):
            raise ValueError(f"feature source dates must be monotonic: {symbol}")
        if len(set(dates)) != len(dates):
            raise ValueError(f"duplicate symbol/date feature row: {symbol}")
        by_date: dict[str, tuple[Any, ...]] = {}
        for row_index, session in enumerate(dates):
            normalized_values = tuple(
                _numeric_value(frame.iloc[row_index][item.source_column], item)
                for item in spec.fields
            )
            by_date[session] = normalized_values
        values[symbol] = by_date
    for symbol in missing:
        frame = source.frame_for(symbol)
        if not isinstance(frame, pd.DataFrame) or not frame.empty:
            raise ValueError(f"feature source missing-symbol metadata is inconsistent: {symbol}")
    declared_schema = source.metadata.get("output_columns")
    if declared_schema is not None and tuple(str(item) for item in declared_schema) != exact_schema:
        raise ValueError("feature source declared column schema is inconsistent with its frames")
    declared_available = source.metadata.get("available_symbols")
    if declared_available is not None and _normalized_symbols(declared_available, name="metadata available_symbols") != available:
        raise ValueError("feature source available-symbol metadata is inconsistent")
    declared_missing = source.metadata.get("missing_symbols")
    if declared_missing is not None and _normalized_symbols(declared_missing, name="metadata missing_symbols") != missing:
        raise ValueError("feature source missing-symbol metadata is inconsistent")
    return available, missing, values


def _freeze_status_counts(
    status_counts: Mapping[str, Mapping[str, int]],
) -> MappingProxyType:
    return MappingProxyType({
        name: MappingProxyType(dict(counts)) for name, counts in status_counts.items()
    })


def attach_features_to_observation_index(
    observation_index: PointInTimeObservationIndex,
    feature_source: PreparedFeatureSource,
    *,
    spec: PointInTimeFeaturePanelSpec,
) -> PointInTimeFeaturePanel:
    """Left-attach already-computed features by exact symbol and session date."""
    if not isinstance(observation_index, PointInTimeObservationIndex):
        raise TypeError("observation_index must be PointInTimeObservationIndex")
    if not isinstance(spec, PointInTimeFeaturePanelSpec):
        raise TypeError("spec must be PointInTimeFeaturePanelSpec")
    required_source_contract = (
        "computation_identity", "available_symbols", "missing_symbols", "metadata", "frame_for",
    )
    if any(not hasattr(feature_source, name) for name in required_source_contract) or not callable(feature_source.frame_for):
        raise TypeError("feature_source does not satisfy the prepared-feature structural contract")
    source_identity = _source_identity(feature_source)
    available_symbols, missing_symbols, source_values = _validated_source_frames(feature_source, spec)
    available_set = set(available_symbols)
    base = observation_index.frame
    attached = base.copy(deep=True)
    values_by_field: list[list[Any]] = [[] for _ in spec.fields]
    statuses_by_field: list[list[str]] = [[] for _ in spec.fields]
    evidence_rows: list[tuple[tuple[Any, ...], ...]] = []
    row_available_counts: list[int] = []
    for row in base.itertuples(index=False):
        session = str(row.session_date)
        symbol = str(row.symbol)
        market_available = bool(row.market_row_available)
        source_row = source_values.get(symbol, {}).get(session)
        row_evidence: list[tuple[Any, ...]] = []
        available_count = 0
        for field_index, field in enumerate(spec.fields):
            if not market_available:
                value = None
                status = FeatureValueAvailability.OBSERVATION_MARKET_ROW_MISSING
                evidence = ("unavailable", status.value)
            elif symbol not in available_set:
                value = None
                status = FeatureValueAvailability.SOURCE_SYMBOL_MISSING
                evidence = ("unavailable", status.value)
            elif source_row is None:
                value = None
                status = FeatureValueAvailability.SOURCE_DATE_MISSING
                evidence = ("unavailable", status.value)
            else:
                value, raw_evidence = source_row[field_index]
                if raw_evidence[0] == "null":
                    value = None
                    status = FeatureValueAvailability.SOURCE_VALUE_MISSING
                elif raw_evidence[0] == "nonfinite" and (
                    raw_evidence[1] == "NaN" or field.finite_required
                ):
                    value = None
                    status = FeatureValueAvailability.SOURCE_VALUE_NONFINITE
                else:
                    status = FeatureValueAvailability.AVAILABLE
                    available_count += 1
                evidence = raw_evidence
            values_by_field[field_index].append(value if status is FeatureValueAvailability.AVAILABLE else pd.NA)
            statuses_by_field[field_index].append(status.value)
            row_evidence.append(tuple(evidence))
        evidence_rows.append(tuple(row_evidence))
        row_available_counts.append(available_count)
    for field_index, field in enumerate(spec.fields):
        attached[field.output_name] = pd.Series(values_by_field[field_index], dtype=field.output_dtype)
        attached[f"{field.output_name}{FEATURE_AVAILABILITY_SUFFIX}"] = pd.Series(
            statuses_by_field[field_index], dtype="string",
        )
    attached["requested_feature_count"] = pd.Series([len(spec.fields)] * len(attached), dtype="int64")
    attached["available_feature_count"] = pd.Series(row_available_counts, dtype="int64")
    attached["complete_feature_row"] = pd.Series(
        [count == len(spec.fields) for count in row_available_counts], dtype="bool",
    )
    attached = attached.loc[:, spec.output_columns]
    pd.testing.assert_frame_equal(
        attached.loc[:, OBSERVATION_INDEX_COLUMNS],
        base.loc[:, OBSERVATION_INDEX_COLUMNS],
        check_exact=True,
    )
    per_feature_available = {
        item.output_name: int((attached[f"{item.output_name}{FEATURE_AVAILABILITY_SUFFIX}"] == FeatureValueAvailability.AVAILABLE.value).sum())
        for item in spec.fields
    }
    per_feature_status = {
        item.output_name: {
            status.value: int((attached[f"{item.output_name}{FEATURE_AVAILABILITY_SUFFIX}"] == status.value).sum())
            for status in FeatureValueAvailability
        }
        for item in spec.fields
    }
    complete_count = int(attached["complete_feature_row"].sum())
    metadata = MappingProxyType({
        "observation_row_count": len(attached),
        "requested_feature_count": len(spec.fields),
        "complete_feature_row_count": complete_count,
        "incomplete_feature_row_count": len(attached) - complete_count,
        "per_feature_available_counts": MappingProxyType(per_feature_available),
        "per_feature_status_counts": _freeze_status_counts(per_feature_status),
        "source_available_symbol_count": len(available_symbols),
        "source_missing_symbol_count": len(missing_symbols),
        "effective_first_session_date": observation_index.effective_first_session_date,
        "effective_last_session_date": observation_index.effective_last_session_date,
        "ordered_symbols": observation_index.symbols,
        "observation_index_identity": observation_index.identity,
        "feature_source_identity": source_identity,
    })
    observation_keys = tuple(zip(base["session_date"].astype(str), base["symbol"].astype(str)))
    return PointInTimeFeaturePanel(
        spec=spec,
        observation_index_identity=observation_index.identity,
        observation_content_identity=observation_index.content_identity,
        snapshot_id=observation_index.snapshot_id,
        universe_membership_identity=observation_index.universe_membership_identity,
        feature_source_identity=source_identity,
        session_audit=observation_index.session_audit,
        metadata=metadata,
        _frame=attached,
        _observation_keys=observation_keys,
        _value_evidence=tuple(evidence_rows),
    )
