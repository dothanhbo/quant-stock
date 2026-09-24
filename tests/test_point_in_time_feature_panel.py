from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType, SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from quantlab.features.universe_context import PointInTimeUniverseContext
from quantlab.panels import (
    FeatureFieldSpec,
    FeatureValueAvailability,
    PointInTimeFeaturePanelSpec,
    attach_features_to_observation_index,
    build_point_in_time_observation_index,
)
from quantlab.panels.contracts import OBSERVATION_INDEX_COLUMNS
from quantlab.panels.feature_contracts import FEATURE_DIAGNOSTIC_COLUMNS


DATES = ("2024-01-02", "2024-01-04", "2024-01-05")


def _market_frame(symbol: str, dates: tuple[str, ...]) -> pd.DataFrame:
    return pd.DataFrame({
        "symbol": [symbol] * len(dates),
        "time": pd.to_datetime(dates),
        "open": [10.0] * len(dates),
        "high": [11.0] * len(dates),
        "low": [9.0] * len(dates),
        "close": [10.0] * len(dates),
        "volume": [1_000] * len(dates),
    })


class _Snapshot:
    canonical_db_path = Path("unused-market.db")
    schema_version = "schema-v1"
    logical_content_fingerprint = "logical-content"
    symbols = ("AAA", "BBB", "MISS", "VNINDEX")

    def __init__(self, *, snapshot_id: str = "snapshot-v1") -> None:
        self.snapshot_id = snapshot_id
        self.calls = 0

    def load_ohlcv(self, symbols, **_kwargs):
        self.calls += 1
        frames = {
            "VNINDEX": _market_frame("VNINDEX", DATES),
            "AAA": _market_frame("AAA", DATES),
            "BBB": _market_frame("BBB", DATES[1:]),
            "MISS": _market_frame("MISS", DATES[:1]),
        }
        requested = tuple(sorted(symbols))
        selected = {symbol: frames[symbol] for symbol in requested if symbol in frames}
        return SimpleNamespace(
            frames=MappingProxyType(selected),
            missing_symbols=tuple(symbol for symbol in requested if symbol not in selected),
        )


def _observation_index(*, snapshot_id: str = "snapshot-v1", empty: bool = False):
    snapshot = _Snapshot(snapshot_id=snapshot_id)
    memberships = (
        {date: () for date in DATES}
        if empty
        else {
            DATES[0]: ("MISS", "BBB", "AAA"),
            DATES[1]: ("BBB", "AAA"),
            DATES[2]: ("AAA",),
        }
    )
    context = PointInTimeUniverseContext.from_memberships(
        universe_mode="test_point_in_time",
        memberships=memberships,
    )
    index = build_point_in_time_observation_index(
        snapshot,
        context,
        benchmark_symbol="VNINDEX",
        start_date=DATES[0],
        through_date=DATES[-1],
    )
    return snapshot, index


class _FeatureSource:
    def __init__(
        self,
        frames: dict[str, pd.DataFrame],
        *,
        identity: str = "feature-source-v1",
        available_symbols: tuple[str, ...] | None = None,
        missing_symbols: tuple[str, ...] = (),
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.computation_identity = SimpleNamespace(sha256=identity)
        self.available_symbols = available_symbols or tuple(frames)
        self.missing_symbols = missing_symbols
        self.metadata = MappingProxyType(dict(metadata or {}))
        self._frames = {symbol: frame.copy(deep=True) for symbol, frame in frames.items()}
        self.frame_calls: list[str] = []
        self.compute_calls = 0

    def frame_for(self, symbol: str) -> pd.DataFrame:
        normalized = str(symbol).strip().upper()
        self.frame_calls.append(normalized)
        frame = self._frames.get(normalized)
        return pd.DataFrame() if frame is None else frame.copy(deep=True)

    def compute(self):
        self.compute_calls += 1
        raise AssertionError("feature computation must not be called")


def _field(
    source: str = "factor",
    output: str = "factor_v1",
    numeric_type: str = "float",
    *,
    finite_required: bool = True,
) -> FeatureFieldSpec:
    return FeatureFieldSpec(
        source_column=source,
        output_name=output,
        version="v1",
        description=f"test field {output}",
        expected_numeric_type=numeric_type,
        finite_required=finite_required,
    )


def _spec(*fields: FeatureFieldSpec) -> PointInTimeFeaturePanelSpec:
    return PointInTimeFeaturePanelSpec(
        name="test_neutral_features",
        version="v1",
        fields=fields or (_field(),),
    )


def _precedence_source() -> _FeatureSource:
    aaa = pd.DataFrame({
        "time": pd.to_datetime([DATES[0], DATES[2]]),
        "factor": pd.Series([1.5, None], dtype="object"),
    })
    bbb = pd.DataFrame({
        "time": pd.to_datetime([DATES[0], DATES[1]]),
        "factor": [999.0, np.inf],
    })
    extra = pd.DataFrame({
        "time": pd.to_datetime(["2023-12-01", "2025-01-01"]),
        "factor": [7.0, 8.0],
    })
    return _FeatureSource(
        {"BBB": bbb, "EXTRA": extra, "AAA": aaa},
        available_symbols=("EXTRA", "BBB", "AAA"),
        missing_symbols=("MISS",),
    )


def test_exact_left_join_preserves_observations_order_and_base_columns() -> None:
    snapshot, observations = _observation_index()
    base = observations.frame
    source = _precedence_source()
    panel = attach_features_to_observation_index(observations, source, spec=_spec())
    frame = panel.frame
    assert len(frame) == len(base) == 6
    pd.testing.assert_frame_equal(frame.loc[:, OBSERVATION_INDEX_COLUMNS], base)
    assert list(zip(frame["session_date"], frame["symbol"])) == list(zip(base["session_date"], base["symbol"]))
    assert not frame["symbol"].eq("EXTRA").any()
    assert snapshot.calls == 1
    assert source.compute_calls == 0


def test_all_missingness_precedence_branches_and_no_fill() -> None:
    _, observations = _observation_index()
    panel = attach_features_to_observation_index(observations, _precedence_source(), spec=_spec())
    frame = panel.frame.set_index(["session_date", "symbol"])
    status = "factor_v1__availability"
    assert frame.loc[(DATES[0], "AAA"), status] == FeatureValueAvailability.AVAILABLE.value
    assert frame.loc[(DATES[0], "AAA"), "factor_v1"] == 1.5
    # BBB has an exact feature row, but its observation market row is absent.
    assert frame.loc[(DATES[0], "BBB"), status] == FeatureValueAvailability.OBSERVATION_MARKET_ROW_MISSING.value
    assert frame.loc[(DATES[0], "MISS"), status] == FeatureValueAvailability.SOURCE_SYMBOL_MISSING.value
    assert frame.loc[(DATES[1], "AAA"), status] == FeatureValueAvailability.SOURCE_DATE_MISSING.value
    assert frame.loc[(DATES[2], "AAA"), status] == FeatureValueAvailability.SOURCE_VALUE_MISSING.value
    assert frame.loc[(DATES[1], "BBB"), status] == FeatureValueAvailability.SOURCE_VALUE_NONFINITE.value
    assert frame.loc[(DATES[1], "AAA"), "factor_v1"] is pd.NA
    assert frame.loc[(DATES[0], "BBB"), "factor_v1"] is pd.NA


def test_null_nan_positive_and_negative_infinity_remain_identity_distinct() -> None:
    _, observations = _observation_index(empty=False)
    identities: list[str] = []
    expected_statuses = (
        FeatureValueAvailability.SOURCE_VALUE_MISSING.value,
        FeatureValueAvailability.SOURCE_VALUE_NONFINITE.value,
        FeatureValueAvailability.SOURCE_VALUE_NONFINITE.value,
        FeatureValueAvailability.SOURCE_VALUE_NONFINITE.value,
    )
    for value, status in zip((None, np.nan, np.inf, -np.inf), expected_statuses):
        frame = pd.DataFrame({"time": [pd.Timestamp(DATES[0])], "factor": pd.Series([value], dtype="object")})
        source = _FeatureSource({"AAA": frame}, available_symbols=("AAA",), missing_symbols=("BBB", "MISS"))
        panel = attach_features_to_observation_index(observations, source, spec=_spec())
        row = panel.frame.loc[
            (panel.frame["session_date"] == DATES[0]) & (panel.frame["symbol"] == "AAA")
        ].iloc[0]
        assert row["factor_v1__availability"] == status
        assert pd.isna(row["factor_v1"])
        identities.append(panel.feature_content_identity)
    assert len(set(identities)) == 4


def test_complete_row_diagnostics_and_multiple_feature_order() -> None:
    _, observations = _observation_index()
    fields = (_field("factor", "factor_v1"), _field("count", "count_v1", "integer"))
    frames = {
        "AAA": pd.DataFrame({"time": pd.to_datetime(DATES), "factor": [1.0, 2.0, 3.0], "count": [1, 2, 3]}),
        "BBB": pd.DataFrame({"time": pd.to_datetime(DATES[1:]), "factor": [4.0, 5.0], "count": [4, 5]}),
    }
    source = _FeatureSource(frames, missing_symbols=("MISS",))
    panel = attach_features_to_observation_index(observations, source, spec=_spec(*fields))
    expected = (*OBSERVATION_INDEX_COLUMNS, "factor_v1", "factor_v1__availability", "count_v1", "count_v1__availability", *FEATURE_DIAGNOSTIC_COLUMNS)
    assert tuple(panel.frame.columns) == expected
    assert panel.requested_feature_count == 2
    assert panel.complete_feature_row_count == 4
    assert panel.incomplete_feature_row_count == 2
    assert panel.per_feature_available_counts == {"factor_v1": 4, "count_v1": 4}
    complete = panel.frame.loc[panel.frame["complete_feature_row"]]
    assert complete["available_feature_count"].eq(2).all()
    assert str(panel.frame["count_v1"].dtype) == "Int64"


def test_boolean_numeric_contract_and_finite_optional_float() -> None:
    _, observations = _observation_index()
    fields = (
        _field("flag", "flag_v1", "boolean"),
        _field("unbounded", "unbounded_v1", "float", finite_required=False),
    )
    frames = {
        "AAA": pd.DataFrame({"time": pd.to_datetime(DATES), "flag": [True, 0, 1], "unbounded": [1.0, np.inf, -np.inf]}),
        "BBB": pd.DataFrame({"time": pd.to_datetime(DATES[1:]), "flag": [1, 0], "unbounded": [2.0, 3.0]}),
    }
    panel = attach_features_to_observation_index(
        observations, _FeatureSource(frames, missing_symbols=("MISS",)), spec=_spec(*fields),
    )
    assert str(panel.frame["flag_v1"].dtype) == "Int8"
    aaa = panel.frame.loc[panel.frame["symbol"] == "AAA"]
    assert aaa["unbounded_v1__availability"].eq(FeatureValueAvailability.AVAILABLE.value).all()
    assert np.isposinf(aaa.iloc[1]["unbounded_v1"])
    assert np.isneginf(aaa.iloc[2]["unbounded_v1"])


@pytest.mark.parametrize(
    ("frame", "message"),
    [
        (pd.DataFrame({"time": pd.to_datetime([DATES[0], DATES[0]]), "factor": [1.0, 2.0]}), "duplicate"),
        (pd.DataFrame({"time": pd.to_datetime([DATES[1], DATES[0]]), "factor": [1.0, 2.0]}), "monotonic"),
        (pd.DataFrame({"time": ["not-a-date"], "factor": [1.0]}), "invalid feature date"),
        (pd.DataFrame({"factor": [1.0]}), "missing columns"),
    ],
)
def test_malformed_source_dates_and_schema_fail(frame: pd.DataFrame, message: str) -> None:
    _, observations = _observation_index()
    source = _FeatureSource({"AAA": frame}, missing_symbols=("BBB", "MISS"))
    with pytest.raises(ValueError, match=message):
        attach_features_to_observation_index(observations, source, spec=_spec())


@pytest.mark.parametrize(
    ("value", "field", "message"),
    [
        ("1.0", _field(), "non-numeric"),
        ({"value": 1}, _field(), "non-numeric"),
        (1.25, _field(numeric_type="integer"), "non-integral"),
        (2, _field(numeric_type="boolean"), "only 0/1"),
        (True, _field(), "requires boolean"),
    ],
)
def test_unsupported_objects_and_declared_numeric_type_violations_fail(
    value: object, field: FeatureFieldSpec, message: str,
) -> None:
    _, observations = _observation_index()
    frame = pd.DataFrame({"time": [pd.Timestamp(DATES[0])], "factor": pd.Series([value], dtype="object")})
    source = _FeatureSource({"AAA": frame}, missing_symbols=("BBB", "MISS"))
    with pytest.raises(TypeError, match=message):
        attach_features_to_observation_index(observations, source, spec=_spec(field))


def test_source_symbol_and_metadata_consistency_validation() -> None:
    _, observations = _observation_index()
    frame = pd.DataFrame({"time": [pd.Timestamp(DATES[0])], "factor": [1.0]})
    overlapping = _FeatureSource({"AAA": frame}, available_symbols=("AAA",), missing_symbols=("AAA",))
    with pytest.raises(ValueError, match="both available and missing"):
        attach_features_to_observation_index(observations, overlapping, spec=_spec())
    inconsistent = _FeatureSource(
        {"AAA": frame}, metadata={"available_symbols": ("BBB",)}, missing_symbols=("BBB", "MISS"),
    )
    with pytest.raises(ValueError, match="available-symbol metadata"):
        attach_features_to_observation_index(observations, inconsistent, spec=_spec())


@pytest.mark.parametrize(
    "fields",
    [
        (),
        (_field(output="symbol"),),
        (_field(source="x", output="same"), _field(source="y", output="same")),
        (_field(source="same", output="x"), _field(source="same", output="y")),
    ],
)
def test_empty_reserved_and_duplicate_feature_specs_fail(fields: tuple[FeatureFieldSpec, ...]) -> None:
    with pytest.raises(ValueError):
        PointInTimeFeaturePanelSpec(name="invalid", version="v1", fields=fields)


def test_reserved_availability_suffix_fails_at_field_construction() -> None:
    with pytest.raises(ValueError, match="must not end with"):
        _field(output="factor__availability")


def test_defensive_output_and_immutable_specs_and_metadata() -> None:
    _, observations = _observation_index()
    spec = _spec()
    panel = attach_features_to_observation_index(observations, _precedence_source(), spec=spec)
    changed = panel.frame
    changed.loc[0, "symbol"] = "MUTATED"
    assert "MUTATED" not in set(panel.frame["symbol"])
    with pytest.raises(FrozenInstanceError):
        spec.name = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        panel.metadata["x"] = 1  # type: ignore[index]
    with pytest.raises(TypeError):
        panel.metadata["per_feature_available_counts"]["factor_v1"] = 0  # type: ignore[index]


def test_source_presentation_order_does_not_change_output_or_identities() -> None:
    _, observations = _observation_index()
    source = _precedence_source()
    reordered_frames = {
        symbol: frame.loc[:, list(reversed(frame.columns))]
        for symbol, frame in reversed(tuple(source._frames.items()))
    }
    reordered = _FeatureSource(
        reordered_frames,
        identity="feature-source-v1",
        available_symbols=tuple(reversed(source.available_symbols)),
        missing_symbols=source.missing_symbols,
    )
    one = attach_features_to_observation_index(observations, source, spec=_spec())
    two = attach_features_to_observation_index(observations, reordered, spec=_spec())
    pd.testing.assert_frame_equal(one.frame, two.frame)
    assert one.feature_content_identity == two.feature_content_identity
    assert one.identity == two.identity


def test_value_source_and_observation_identity_sensitivity() -> None:
    _, observations = _observation_index(snapshot_id="snapshot-one")
    first_source = _precedence_source()
    first = attach_features_to_observation_index(observations, first_source, spec=_spec())
    changed_frames = {name: frame.copy(deep=True) for name, frame in first_source._frames.items()}
    changed_frames["AAA"].loc[0, "factor"] = 9.0
    changed_value = attach_features_to_observation_index(
        observations,
        _FeatureSource(changed_frames, identity="feature-source-v1", available_symbols=first_source.available_symbols, missing_symbols=first_source.missing_symbols),
        spec=_spec(),
    )
    assert changed_value.feature_content_identity != first.feature_content_identity
    changed_source = attach_features_to_observation_index(
        observations,
        _FeatureSource(first_source._frames, identity="feature-source-v2", available_symbols=first_source.available_symbols, missing_symbols=first_source.missing_symbols),
        spec=_spec(),
    )
    assert changed_source.feature_content_identity != first.feature_content_identity
    _, provenance_changed_observations = _observation_index(snapshot_id="snapshot-two")
    changed_observation = attach_features_to_observation_index(provenance_changed_observations, _precedence_source(), spec=_spec())
    assert changed_observation.feature_content_identity == first.feature_content_identity
    assert changed_observation.identity != first.identity


def test_extra_source_dates_are_bounded_out_of_content_identity() -> None:
    _, observations = _observation_index()
    base_frame = pd.DataFrame({"time": pd.to_datetime(DATES), "factor": [1.0, 2.0, 3.0]})
    extra_frame = pd.concat([
        base_frame,
        pd.DataFrame({"time": [pd.Timestamp("2025-01-01")], "factor": [999.0]}),
    ], ignore_index=True)
    missing = ("BBB", "MISS")
    one = attach_features_to_observation_index(
        observations, _FeatureSource({"AAA": base_frame}, identity="same", missing_symbols=missing), spec=_spec(),
    )
    two = attach_features_to_observation_index(
        observations, _FeatureSource({"AAA": extra_frame}, identity="same", missing_symbols=missing), spec=_spec(),
    )
    pd.testing.assert_frame_equal(one.frame, two.frame)
    assert one.feature_content_identity == two.feature_content_identity


def test_empty_observation_population_retains_schema_and_audit() -> None:
    _, observations = _observation_index(empty=True)
    source = _FeatureSource({}, available_symbols=(), missing_symbols=())
    panel = attach_features_to_observation_index(observations, source, spec=_spec())
    assert panel.frame.empty
    assert tuple(panel.frame.columns) == panel.spec.output_columns
    assert panel.observation_row_count == panel.complete_feature_row_count == panel.incomplete_feature_row_count == 0
    assert len(panel.session_audit) == len(DATES)


def test_fresh_process_import_has_no_computation_or_production_dependencies(tmp_path: Path) -> None:
    code = (
        "import json, pathlib, sys; "
        f"work=pathlib.Path({str(tmp_path)!r}); before=list(work.iterdir()); "
        "import quantlab.panels; import quantlab.panels.feature_panel; "
        "forbidden=('strategy','backtesting','execution','quantlab.alpha','quantlab.candidates','quantlab.outcomes','quantlab.evaluation','quantlab.features.builtins','quantlab.features.registry'); "
        "loaded=sorted(name for name in sys.modules if any(name == item or name.startswith(item + '.') for item in forbidden)); "
        "print(json.dumps({'loaded': loaded, 'created': [str(p) for p in work.iterdir() if p not in before]}))"
    )
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == {"loaded": [], "created": []}
