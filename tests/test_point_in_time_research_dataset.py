from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
from types import MappingProxyType, SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from quantlab.features import PointInTimeUniverseContext
from quantlab.panels import (
    POINT_IN_TIME_NEUTRAL_RESEARCH_DATASET_V1,
    ResearchDatasetUse,
    build_neutral_research_feature_panel,
    build_point_in_time_observation_index,
    build_point_in_time_outcome_panel,
    build_point_in_time_research_dataset,
)
from quantlab.panels.contracts import OBSERVATION_INDEX_COLUMNS
from quantlab.panels.research_dataset_contracts import PointInTimeResearchDatasetSpec
from tests.quantlab_panel_test_support import (
    InMemoryMarketDataSnapshot as _Snapshot,
    make_ohlcv_frame,
)


def _frame(symbol: str, sessions: tuple[str, ...], offset: float) -> pd.DataFrame:
    index = np.arange(len(sessions), dtype=float)
    close = offset + index * 0.2 + np.sin(index / 4.0) * 0.25
    return make_ohlcv_frame(
        symbol,
        sessions,
        tuple(close),
        open_values=tuple(close - 0.1),
        high_values=tuple(close + 0.6),
        low_values=tuple(close - 0.5),
        volume_values=tuple(100_000 + np.arange(len(sessions)) * 137),
    )


def _fixture(*, presentation_reversed: bool = False):
    sessions = tuple(pd.bdate_range("2021-01-04", periods=75).strftime("%Y-%m-%d"))
    frames = {
        "VNINDEX": _frame("VNINDEX", sessions, 100.0),
        "AAA": _frame("AAA", sessions, 20.0),
    }
    if presentation_reversed:
        frames = dict(reversed(tuple(frames.items())))
    snapshot = _Snapshot(frames)
    memberships = {session: () for session in sessions[:56]}
    memberships[sessions[20]] = (("AAA", "MISSING") if presentation_reversed else ("MISSING", "AAA"))
    memberships[sessions[21]] = ("AAA",)
    memberships[sessions[22]] = ()
    memberships[sessions[50]] = ("AAA",)
    if presentation_reversed:
        memberships = dict(reversed(tuple(memberships.items())))
    context = PointInTimeUniverseContext.from_memberships(
        universe_mode="test_point_in_time", memberships=memberships,
    )
    observations = build_point_in_time_observation_index(
        snapshot, context, benchmark_symbol="VNINDEX",
        start_date=sessions[0], through_date=sessions[55],
    )
    features = build_neutral_research_feature_panel(
        observations, snapshot, benchmark_symbol="VNINDEX", universe_context=context,
    )
    outcomes = build_point_in_time_outcome_panel(observations, snapshot)
    snapshot.calls.clear()
    return sessions, snapshot, context, observations, features, outcomes


def _tamper(value, **changes):
    changed = copy.copy(value)
    for name, replacement in changes.items():
        object.__setattr__(changed, name, replacement)
    return changed


def test_exact_key_join_schema_groups_population_and_projections() -> None:
    _, _, _, observations, features, outcomes = _fixture()
    dataset = build_point_in_time_research_dataset(observations, features, outcomes)
    evaluation = dataset.evaluation_frame()
    predictor = dataset.predictor_frame()
    spec = POINT_IN_TIME_NEUTRAL_RESEARCH_DATASET_V1
    assert tuple(evaluation.columns) == spec.output_columns
    assert tuple(predictor.columns) == spec.predictor_columns
    assert len(evaluation) == len(observations.frame)
    assert list(zip(evaluation["session_date"], evaluation["symbol"])) == list(
        zip(observations.frame["session_date"], observations.frame["symbol"])
    )
    pd.testing.assert_frame_equal(
        evaluation.loc[:, OBSERVATION_INDEX_COLUMNS], observations.frame, check_exact=True,
    )
    assert set(dataset.forbidden_predictor_columns).isdisjoint(predictor.columns)
    assert set(dataset.outcome_columns).isdisjoint(predictor.columns)
    assert set(dataset.outcome_availability_columns).isdisjoint(predictor.columns)
    assert set(dataset.outcome_diagnostic_columns).isdisjoint(predictor.columns)
    assert dataset.research_use is ResearchDatasetUse.OFFLINE_EVALUATION
    assert dataset.predictor_projection_use is ResearchDatasetUse.PREDICTOR_ONLY
    assert dataset.metadata["future_looking"] is True
    assert dataset.metadata["production_signal_safe"] is False


def test_missingness_nullable_dtypes_and_no_imputation_or_filtering() -> None:
    _, _, _, observations, features, outcomes = _fixture()
    dataset = build_point_in_time_research_dataset(observations, features, outcomes)
    frame = dataset.evaluation_frame()
    feature_frame = features.frame
    outcome_frame = outcomes.frame
    for column in dataset.feature_columns + dataset.feature_availability_columns + dataset.feature_diagnostic_columns:
        assert frame[column].dtype == feature_frame[column].dtype
        pd.testing.assert_series_equal(frame[column], feature_frame[column], check_exact=True)
    for column in dataset.outcome_columns + dataset.outcome_availability_columns + dataset.outcome_diagnostic_columns:
        assert frame[column].dtype == outcome_frame[column].dtype
        pd.testing.assert_series_equal(frame[column], outcome_frame[column], check_exact=True)
    missing = frame.loc[frame["symbol"] == "MISSING"].iloc[0]
    assert not bool(missing["market_row_available"])
    assert pd.isna(missing["close"])
    assert missing["close__availability"] != "AVAILABLE"
    assert missing["outcome_5__availability"] == "OBSERVATION_MARKET_ROW_MISSING"
    assert pd.isna(missing["stock_forward_return_5_pct"])
    assert len(frame) == observations.total_membership_row_count


def test_aggregate_counts_reconcile_with_both_source_panels() -> None:
    _, _, _, observations, features, outcomes = _fixture()
    dataset = build_point_in_time_research_dataset(observations, features, outcomes)
    assert dataset.observation_row_count == observations.total_membership_row_count
    assert dataset.session_count == observations.benchmark_session_count
    assert dataset.symbol_count == observations.distinct_member_symbol_count
    assert dataset.complete_feature_row_count == features.complete_feature_row_count
    assert dataset.fully_labeled_outcome_row_count == outcomes.fully_labeled_row_count
    assert dict(dataset.per_feature_availability_counts) == dict(features.per_feature_available_counts)
    assert dict(dataset.per_horizon_outcome_availability_counts) == dict(outcomes.per_horizon_available_counts)
    assert (
        dataset.rows_with_any_available_outcome_count
        + dataset.rows_with_no_available_outcome_count
        == dataset.observation_row_count
    )


@pytest.mark.parametrize(
    ("source_name", "corruption", "message"),
    [
        pytest.param(source, corruption, message, id=f"{source}-{corruption}")
        for source in ("feature", "outcome")
        for corruption, message in (
            ("duplicate", "duplicate"),
            ("missing", "missing observation keys"),
            ("extra", "extra observation keys"),
            ("reordered", "row order"),
        )
    ],
)
def test_duplicate_missing_extra_and_reordered_keys_fail(
    source_name: str,
    corruption: str,
    message: str,
) -> None:
    _, _, _, observations, features, outcomes = _fixture()
    source = features if source_name == "feature" else outcomes
    frame = source.frame
    changes: dict[str, object]
    if corruption == "duplicate":
        corrupted = frame.copy(deep=True)
        corrupted.loc[1, ["session_date", "symbol"]] = corrupted.loc[
            0, ["session_date", "symbol"]
        ].to_numpy()
        changes = {"_frame": corrupted}
    elif corruption == "missing":
        corrupted = frame.iloc[:-1].reset_index(drop=True)
        changes = {"_frame": corrupted, "observation_row_count": len(corrupted)}
    elif corruption == "extra":
        corrupted = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
        corrupted.loc[len(corrupted) - 1, "symbol"] = "EXTRA"
        changes = {"_frame": corrupted, "observation_row_count": len(corrupted)}
    else:
        corrupted = frame.iloc[::-1].reset_index(drop=True)
        changes = {"_frame": corrupted}
    changed = _tamper(source, **changes)
    args = (
        (observations, changed, outcomes)
        if source_name == "feature"
        else (observations, features, changed)
    )
    with pytest.raises(ValueError, match=message):
        build_point_in_time_research_dataset(*args)


@pytest.mark.parametrize(
    ("mismatch", "message"),
    [
        pytest.param("snapshot", "snapshot_id", id="snapshot"),
        pytest.param("universe", "universe_membership_identity", id="universe"),
        pytest.param("observation-identity", "observation_index_identity", id="observation-identity"),
        pytest.param("content-provenance", "observation_content_identity", id="content-provenance"),
        pytest.param("benchmark", "benchmark", id="benchmark"),
        pytest.param("bounds", "requested bounds", id="bounds"),
        pytest.param("diagnostics", "observation diagnostics", id="diagnostics"),
    ],
)
def test_provenance_bounds_and_observation_diagnostic_mismatches_fail(
    mismatch: str,
    message: str,
) -> None:
    _, _, _, observations, features, outcomes = _fixture()
    feature_panel = features
    outcome_panel = outcomes
    if mismatch == "snapshot":
        feature_panel = _tamper(features, snapshot_id="other")
    elif mismatch == "universe":
        feature_panel = _tamper(features, universe_membership_identity="other")
    elif mismatch == "observation-identity":
        feature_panel = _tamper(features, observation_index_identity="other")
    elif mismatch == "content-provenance":
        feature_panel = _tamper(features, observation_content_identity="other")
    elif mismatch == "benchmark":
        outcome_panel = _tamper(outcomes, benchmark_symbol="OTHER")
    elif mismatch == "bounds":
        outcome_panel = _tamper(outcomes, requested_start_date="1999-01-01")
    else:
        changed_frame = features.frame
        changed_frame.loc[0, "market_row_available"] = not bool(
            changed_frame.loc[0, "market_row_available"]
        )
        feature_panel = _tamper(features, _frame=changed_frame)
    with pytest.raises(ValueError, match=message):
        build_point_in_time_research_dataset(
            observations,
            feature_panel,
            outcome_panel,
        )


def test_identity_stability_and_content_schema_spec_sensitivity() -> None:
    _, _, _, observations, features, outcomes = _fixture()
    first = build_point_in_time_research_dataset(observations, features, outcomes)
    repeated = build_point_in_time_research_dataset(observations, features, outcomes)
    assert first.content_identity == repeated.content_identity
    assert first.identity == repeated.identity
    _, _, _, normalized_observations, normalized_features, normalized_outcomes = _fixture(
        presentation_reversed=True,
    )
    normalized = build_point_in_time_research_dataset(
        normalized_observations, normalized_features, normalized_outcomes,
    )
    pd.testing.assert_frame_equal(first.evaluation_frame(), normalized.evaluation_frame(), check_exact=True)
    assert normalized.content_identity == first.content_identity
    assert normalized.identity == first.identity

    changed_feature_frame = features.frame
    target_index = changed_feature_frame["close"].first_valid_index()
    assert target_index is not None
    changed_feature_frame.loc[target_index, "close"] += 0.125
    changed_feature = _tamper(
        features, _frame=changed_feature_frame,
        feature_content_identity="changed-feature-content", identity="changed-feature-panel",
    )
    feature_dataset = build_point_in_time_research_dataset(observations, changed_feature, outcomes)
    assert feature_dataset.content_identity != first.content_identity
    assert feature_dataset.identity != first.identity

    changed_outcome_frame = outcomes.frame
    outcome_index = changed_outcome_frame["stock_forward_return_5_pct"].first_valid_index()
    assert outcome_index is not None
    changed_outcome_frame.loc[outcome_index, "stock_forward_return_5_pct"] += 0.25
    changed_outcome = _tamper(
        outcomes, _frame=changed_outcome_frame,
        outcome_content_identity="changed-outcome-content", identity="changed-outcome-panel",
    )
    outcome_dataset = build_point_in_time_research_dataset(observations, features, changed_outcome)
    assert outcome_dataset.content_identity != first.content_identity
    assert outcome_dataset.identity != first.identity

    alternate_spec = PointInTimeResearchDatasetSpec(
        name="alternate-neutral-research-dataset",
        version="v1",
        feature_panel_spec=features.spec,
        outcome_panel_spec=outcomes.spec,
    )
    alternate = build_point_in_time_research_dataset(
        observations, features, outcomes, spec=alternate_spec,
    )
    assert alternate.content_identity == first.content_identity
    assert alternate.identity != first.identity


def test_caller_and_returned_frame_mutations_do_not_change_dataset() -> None:
    _, _, _, observations, features, outcomes = _fixture()
    dataset = build_point_in_time_research_dataset(observations, features, outcomes)
    identity = dataset.identity
    evaluation = dataset.evaluation_frame()
    predictor = dataset.predictor_frame()
    evaluation.loc[0, "symbol"] = "MUTATED"
    predictor.loc[0, "symbol"] = "MUTATED"
    assert "MUTATED" not in set(dataset.evaluation_frame()["symbol"])
    assert dataset.identity == identity
    with pytest.raises(FrozenInstanceError):
        dataset.snapshot_id = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        dataset.metadata["production_signal_safe"] = True  # type: ignore[index]


def test_empty_population_with_retained_empty_session_audits_is_deterministic() -> None:
    sessions = tuple(pd.bdate_range("2024-01-02", periods=25).strftime("%Y-%m-%d"))
    frames = {"VNINDEX": _frame("VNINDEX", sessions, 100.0)}
    snapshot = _Snapshot(frames, snapshot_id="empty-snapshot")
    context = PointInTimeUniverseContext.from_memberships(
        universe_mode="empty", memberships={session: () for session in sessions[:2]},
    )
    observations = build_point_in_time_observation_index(
        snapshot, context, benchmark_symbol="VNINDEX",
        start_date=sessions[0], through_date=sessions[1],
    )

    class _EmptySource:
        computation_identity = SimpleNamespace(sha256="empty-features")
        available_symbols = ()
        missing_symbols = ()
        metadata = MappingProxyType({})

        @staticmethod
        def frame_for(_symbol):
            return pd.DataFrame()

    from quantlab.panels import NEUTRAL_RESEARCH_FEATURE_PANEL_V1, attach_features_to_observation_index

    features = attach_features_to_observation_index(
        observations, _EmptySource(), spec=NEUTRAL_RESEARCH_FEATURE_PANEL_V1,
    )
    outcomes = build_point_in_time_outcome_panel(observations, snapshot)
    dataset = build_point_in_time_research_dataset(observations, features, outcomes)
    assert dataset.observation_row_count == 0
    assert dataset.session_count == 2
    assert dataset.symbol_count == 0
    assert dataset.evaluation_frame().empty
    assert tuple(dataset.evaluation_frame().columns) == dataset.spec.output_columns
    assert dataset.identity == build_point_in_time_research_dataset(observations, features, outcomes).identity


def test_join_performs_no_io_computation_cache_or_strategy_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    _, snapshot, _, observations, features, outcomes = _fixture()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("pure research-dataset join crossed a forbidden boundary")

    snapshot.load_ohlcv = forbidden  # type: ignore[method-assign]
    import quantlab.features.registry as registry
    import quantlab.panels.outcome_panel as outcome_builder
    import strategy.scanner as scanner
    import backtesting.portfolio_simulator as simulator
    monkeypatch.setattr(registry.FeatureRegistry, "compute", forbidden)
    monkeypatch.setattr(outcome_builder, "build_point_in_time_outcome_panel", forbidden)
    monkeypatch.setattr(scanner, "evaluate_prepared_row", forbidden)
    monkeypatch.setattr(simulator, "PortfolioSimulator", forbidden)
    dataset = build_point_in_time_research_dataset(observations, features, outcomes)
    assert dataset.observation_row_count == observations.total_membership_row_count
    assert snapshot.calls == []
