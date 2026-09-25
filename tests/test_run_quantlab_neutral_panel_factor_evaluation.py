from __future__ import annotations

import csv
import json
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from quantlab.evaluation import (
    NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V1,
    NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1,
)
from research import run_quantlab_neutral_panel_factor_evaluation as runner


def _evaluation(dates: tuple[str, ...]):
    spec = NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1
    summaries = []
    daily = []
    for factor in spec.factors:
        for horizon in spec.horizons:
            for outcome in spec.outcome_fields:
                daily_ids = tuple(
                    f"daily-{factor}-{horizon}-{outcome}-{signal_date}"
                    for signal_date in dates
                )
                summaries.append(SimpleNamespace(
                    factor=factor,
                    factor_direction="UNSPECIFIED",
                    horizon_sessions=horizon,
                    outcome_field=outcome,
                    total_signal_dates=len(dates),
                    dates_with_any_pairwise_finite_observations=len(dates),
                    ic_defined_date_count=len(dates),
                    ic_coverage_pct=100.0,
                    mean_daily_rank_ic=0.25,
                    median_daily_rank_ic=0.25,
                    population_std_daily_ic=0.0,
                    positive_ic_date_count=len(dates),
                    zero_ic_date_count=0,
                    negative_ic_date_count=0,
                    positive_ic_rate=1.0,
                    bucket_defined_date_count=len(dates),
                    bucket_coverage_pct=100.0,
                    mean_daily_high_minus_low_mean_spread=1.5,
                    median_daily_high_minus_low_mean_spread=1.5,
                    mean_daily_high_minus_low_median_spread=1.5,
                    median_daily_high_minus_low_median_spread=1.5,
                    positive_mean_spread_date_count=len(dates),
                    zero_mean_spread_date_count=0,
                    negative_mean_spread_date_count=0,
                    positive_mean_spread_rate=1.0,
                    average_low_bucket_size=1.0,
                    average_high_bucket_size=1.0,
                    total_eligible_observations=4,
                    average_eligible_cross_section_size=2.0,
                    factor_availability_coverage_pct=100.0,
                    outcome_availability_coverage_pct=100.0,
                    warnings=("descriptive_only",),
                    included_daily_identities=daily_ids,
                    identity=f"summary-{factor}-{horizon}-{outcome}",
                ))
                for signal_date, daily_identity in zip(dates, daily_ids, strict=True):
                    daily.append(SimpleNamespace(
                        factor=factor,
                        factor_direction="UNSPECIFIED",
                        horizon_sessions=horizon,
                        outcome_field=outcome,
                        signal_date=signal_date,
                        total_observation_count=2,
                        factor_available_count=2,
                        outcome_available_count=2,
                        factor_usable_count=2,
                        outcome_usable_count=2,
                        pairwise_finite_eligible_count=2,
                        excluded_for_factor_count=0,
                        excluded_for_outcome_count=0,
                        excluded_for_both_count=0,
                        factor_nonfinite_available_count=0,
                        outcome_nonfinite_available_count=0,
                        rank_ic=None,
                        ic_undefined_reason="fewer_than_minimum_pairwise_finite_observations",
                        low_bucket_count=0,
                        high_bucket_count=0,
                        high_minus_low_mean_spread=None,
                        high_minus_low_median_spread=None,
                        bucket_undefined_reason="fewer_than_minimum_pairwise_finite_observations",
                        identity=daily_identity,
                    ))
    return SimpleNamespace(
        source_dataset_identity="dataset-id",
        source_dataset_content_identity="dataset-content",
        specification_fingerprint=spec.fingerprint,
        summaries=tuple(summaries),
        daily_evaluations=tuple(daily),
        identity="evaluation-id",
    )


def _temporal(evaluation: Any):
    spec = NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V1
    blocks = []
    summaries = []
    daily_by_key = {
        (item.factor, item.horizon_sessions, item.outcome_field): []
        for item in evaluation.daily_evaluations
    }
    for item in evaluation.daily_evaluations:
        daily_by_key[(item.factor, item.horizon_sessions, item.outcome_field)].append(item)
    summary_index = 0
    for factor in spec.factors:
        for horizon in spec.horizons:
            for outcome in spec.outcome_fields:
                block_ids = []
                for block in spec.blocks:
                    included = tuple(
                        item.identity
                        for item in daily_by_key[(factor, horizon, outcome)]
                        if block.start_date <= item.signal_date <= block.end_date
                    )
                    identity = f"block-{factor}-{horizon}-{outcome}-{block.name}"
                    block_ids.append(identity)
                    blocks.append(SimpleNamespace(
                        factor=factor,
                        horizon_sessions=horizon,
                        outcome_field=outcome,
                        block_name=block.name,
                        block_start_date=block.start_date,
                        block_end_date=block.end_date,
                        total_source_signal_dates=len(included),
                        dates_with_any_eligible_observations=len(included),
                        ic_defined_date_count=0,
                        ic_coverage_pct=0.0,
                        mean_daily_rank_ic=None,
                        median_daily_rank_ic=None,
                        population_std_daily_ic=None,
                        minimum_daily_ic=None,
                        maximum_daily_ic=None,
                        positive_ic_date_count=0,
                        zero_ic_date_count=0,
                        negative_ic_date_count=0,
                        positive_ic_rate=None,
                        spread_defined_date_count=0,
                        spread_coverage_pct=0.0,
                        mean_daily_mean_spread=None,
                        median_daily_mean_spread=None,
                        population_std_daily_mean_spread=None,
                        minimum_daily_mean_spread=None,
                        maximum_daily_mean_spread=None,
                        positive_spread_date_count=0,
                        zero_spread_date_count=0,
                        negative_spread_date_count=0,
                        positive_spread_rate=None,
                        mean_daily_median_spread=None,
                        median_daily_median_spread=None,
                        average_low_bucket_size=None,
                        average_high_bucket_size=None,
                        average_eligible_observation_count=(
                            None if not included else 2.0
                        ),
                        minimum_eligible_observation_count=(
                            None if not included else 2
                        ),
                        maximum_eligible_observation_count=(
                            None if not included else 2
                        ),
                        average_factor_availability_coverage_pct=(
                            None if not included else 100.0
                        ),
                        average_outcome_availability_coverage_pct=(
                            None if not included else 100.0
                        ),
                        ic_review_eligible=False,
                        spread_review_eligible=False,
                        included_daily_identities=included,
                        warnings=("synthetic_temporal_block",),
                        ic_undefined_reason="no_defined_daily_ic",
                        spread_undefined_reason="no_defined_daily_spread",
                        identity=identity,
                    ))
                supported = summary_index == 0
                coverage_only = summary_index == 1
                coverage = supported or coverage_only
                summaries.append(SimpleNamespace(
                    factor=factor,
                    horizon_sessions=horizon,
                    outcome_field=outcome,
                    total_block_count=4,
                    ic_review_eligible_block_count=3 if coverage else 0,
                    spread_review_eligible_block_count=3 if coverage else 0,
                    positive_mean_ic_block_count=3 if supported else 0,
                    zero_mean_ic_block_count=0,
                    negative_mean_ic_block_count=0,
                    positive_mean_spread_block_count=3 if supported else 0,
                    zero_mean_spread_block_count=0,
                    negative_mean_spread_block_count=0,
                    mean_ic_across_block_means=0.1 if supported else None,
                    median_ic_across_block_means=0.1 if supported else None,
                    minimum_block_mean_ic=0.1 if supported else None,
                    maximum_block_mean_ic=0.1 if supported else None,
                    range_block_mean_ic=0.0 if supported else None,
                    mean_spread_across_block_means=1.0 if supported else None,
                    median_spread_across_block_means=1.0 if supported else None,
                    minimum_block_mean_spread=1.0 if supported else None,
                    maximum_block_mean_spread=1.0 if supported else None,
                    range_block_mean_spread=0.0 if supported else None,
                    largest_absolute_mean_ic_block_concentration=(
                        1.0 / 3.0 if supported else None
                    ),
                    largest_absolute_mean_spread_block_concentration=(
                        1.0 / 3.0 if supported else None
                    ),
                    all_blocks_positive_ic=False,
                    all_blocks_positive_spread=False,
                    ic_sign_flip_count=0,
                    spread_sign_flip_count=0,
                    coverage_sufficient_for_temporal_review=coverage,
                    directionally_consistent_ic=supported,
                    directionally_consistent_spread=supported,
                    descriptive_temporal_support=supported,
                    included_block_identities=tuple(block_ids),
                    warnings=("descriptive_only",),
                    identity=f"temporal-summary-{factor}-{horizon}-{outcome}",
                ))
                summary_index += 1
    return SimpleNamespace(
        source_result_identity=evaluation.identity,
        source_specification_fingerprint=evaluation.specification_fingerprint,
        temporal_specification_fingerprint=spec.fingerprint,
        contract_name="quantlab.panel_factor_temporal_stability",
        contract_version="v1",
        block_results=tuple(blocks),
        summaries=tuple(summaries),
        identity="temporal-result-id",
    )


def _install_pipeline(monkeypatch: pytest.MonkeyPatch, database: Path):
    dates = ("2021-01-04", "2021-01-05")
    keys = tuple((signal_date, symbol) for signal_date in dates for symbol in ("AAA", "BBB"))
    base = pd.DataFrame(keys, columns=("session_date", "symbol"))
    audits = tuple(
        SimpleNamespace(
            session_date=signal_date,
            membership_count=2,
            emitted_observation_row_count=2,
            available_row_count=2,
            missing_row_count=0,
        )
        for signal_date in dates
    )
    snapshot = SimpleNamespace(
        first_session_date="2020-01-02",
        last_session_date="2021-02-05",
        snapshot_id="snapshot-id",
        logical_content_fingerprint="snapshot-content",
    )
    coverage = SimpleNamespace(
        start_date="2020-01-02",
        end_date=dates[-1],
        effective_start_date="2020-01-02",
        effective_end_date=dates[-1],
    )
    universe = SimpleNamespace(membership_identity="universe-id")
    observation = SimpleNamespace(
        frame=base.copy(deep=True),
        session_audit=audits,
        total_membership_row_count=4,
        observation_row_count=4,
        identity="observation-id",
        content_identity="observation-content",
        effective_first_session_date=dates[0],
        effective_last_session_date=dates[-1],
        distinct_member_symbol_count=2,
        symbols=("AAA", "BBB"),
    )
    source = SimpleNamespace(
        computation_identity=SimpleNamespace(sha256="feature-computation-id"),
        metadata=MappingProxyType({}),
    )
    feature_frame = base.copy(deep=True)
    feature_frame["complete_feature_row"] = True
    feature_panel = SimpleNamespace(
        frame=feature_frame,
        observation_row_count=4,
        observation_index_identity="observation-id",
        identity="feature-panel-id",
        feature_content_identity="feature-content",
    )
    outcome_frame = base.copy(deep=True)
    outcome_frame["available_horizon_count"] = 3
    outcome_frame["fully_labeled_outcome_row"] = True
    outcome_panel = SimpleNamespace(
        frame=outcome_frame,
        observation_row_count=4,
        observation_index_identity="observation-id",
        identity="outcome-panel-id",
        outcome_content_identity="outcome-content",
    )
    dataset = SimpleNamespace(
        evaluation_frame=lambda: base.copy(deep=True),
        observation_row_count=4,
        observation_index_identity="observation-id",
        feature_panel_identity="feature-panel-id",
        outcome_panel_identity="outcome-panel-id",
        identity="dataset-id",
        content_identity="dataset-content",
        forbidden_predictor_columns=(),
    )
    evaluation = _evaluation(dates)
    temporal = _temporal(evaluation)
    calls: dict[str, list[tuple[tuple[Any, ...], dict[str, Any]]]] = {
        name: [] for name in (
            "snapshot", "coverage", "universe", "observation", "source",
            "features", "outcomes", "dataset", "evaluation", "temporal",
        )
    }

    def install(name: str, value: Any):
        def call(*args: Any, **kwargs: Any):
            calls[name].append((args, kwargs))
            return value
        return call

    monkeypatch.setattr(runner, "build_market_data_snapshot", install("snapshot", snapshot))
    monkeypatch.setattr(runner, "build_database_coverage_index", install("coverage", coverage))
    monkeypatch.setattr(
        runner,
        "PointInTimeUniverseContext",
        SimpleNamespace(from_coverage_index=install("universe", universe)),
    )
    monkeypatch.setattr(
        runner, "build_point_in_time_observation_index", install("observation", observation),
    )
    monkeypatch.setattr(
        runner, "prepare_neutral_research_feature_source", install("source", source),
    )
    monkeypatch.setattr(
        runner, "attach_features_to_observation_index", install("features", feature_panel),
    )
    monkeypatch.setattr(
        runner, "build_point_in_time_outcome_panel", install("outcomes", outcome_panel),
    )
    monkeypatch.setattr(
        runner, "build_point_in_time_research_dataset", install("dataset", dataset),
    )
    monkeypatch.setattr(
        runner, "evaluate_point_in_time_panel_factors", install("evaluation", evaluation),
    )
    monkeypatch.setattr(
        runner,
        "evaluate_panel_factor_temporal_stability",
        install("temporal", temporal),
    )
    return calls, SimpleNamespace(
        snapshot=snapshot,
        coverage=coverage,
        universe=universe,
        observation=observation,
        source=source,
        feature_panel=feature_panel,
        outcome_panel=outcome_panel,
        dataset=dataset,
        evaluation=evaluation,
        temporal=temporal,
        dates=dates,
    )


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **changes: Any):
    database = tmp_path / "market.db"
    if not database.exists():
        database.write_bytes(b"immutable-market-fixture")
    calls, objects = _install_pipeline(monkeypatch, database)
    arguments = {
        "database_path": database,
        "start_date": "2021-01-04",
        "end_date": "2021-01-05",
        "output_root": tmp_path / "result",
    }
    arguments.update(changes)
    result = runner.run_neutral_panel_factor_evaluation(**arguments)
    return database, calls, objects, result


def _read_csv(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        return tuple(reader.fieldnames or ()), list(reader)


def test_exact_one_time_orchestration_and_identity_propagation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, calls, objects, result = _run(monkeypatch, tmp_path)
    assert all(len(items) == 1 for items in calls.values())
    assert calls["coverage"][0][0][:2] == ("2020-01-02", "2021-01-05")
    assert calls["observation"][0][1]["start_date"] == "2021-01-04"
    assert calls["observation"][0][1]["through_date"] == "2021-01-05"
    assert calls["outcomes"][0][0] == (objects.observation, objects.snapshot)
    assert calls["evaluation"][0][0][0] is objects.dataset
    assert calls["temporal"] == [((
        objects.evaluation,
        NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V1,
    ), {})]
    manifest = result["manifest"]
    assert manifest["observation_index"]["identity"] == "observation-id"
    assert manifest["features"]["computation_identity"] == "feature-computation-id"
    assert manifest["research_dataset"]["identity"] == "dataset-id"
    assert manifest["evaluation"]["result_identity"] == "evaluation-id"
    assert manifest["temporal_stability"]["source_evaluation_result_identity"] == "evaluation-id"
    assert result["temporal_stability"] is objects.temporal


def test_exact_artifacts_schemas_dimensions_order_and_complete_population(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, objects, result = _run(monkeypatch, tmp_path)
    output = result["output_root"]
    assert tuple(sorted(path.name for path in output.iterdir())) == tuple(
        sorted(runner._REQUIRED_FILENAMES)
    )
    summary_columns, summaries = _read_csv(output / "factor_summary.csv")
    daily_columns, daily = _read_csv(output / "factor_by_date.csv")
    coverage_columns, coverage = _read_csv(output / "factor_coverage.csv")
    observation_columns, observations = _read_csv(output / "observation_counts_by_date.csv")
    temporal_summary_columns, temporal_summaries = _read_csv(
        output / "temporal_stability_summary.csv",
    )
    temporal_block_columns, temporal_blocks = _read_csv(
        output / "temporal_stability_by_block.csv",
    )
    temporal_coverage_columns, temporal_coverage = _read_csv(
        output / "temporal_coverage.csv",
    )
    assert summary_columns == runner._SUMMARY_COLUMNS
    assert daily_columns == runner._DAILY_COLUMNS
    assert coverage_columns == runner._COVERAGE_COLUMNS
    assert observation_columns == runner._OBSERVATION_COLUMNS
    assert temporal_summary_columns == runner._TEMPORAL_SUMMARY_COLUMNS
    assert temporal_block_columns == runner._TEMPORAL_BLOCK_COLUMNS
    assert temporal_coverage_columns == runner._TEMPORAL_COVERAGE_COLUMNS
    assert (len(summaries), len(daily), len(coverage), len(observations)) == (48, 96, 48, 2)
    assert (len(temporal_summaries), len(temporal_blocks), len(temporal_coverage)) == (
        48, 192, 48,
    )
    assert [row["signal_date"] for row in observations] == list(objects.dates)
    assert all(row["observation_row_count"] == "2" for row in observations)
    assert [
        (row["factor"], int(row["horizon_sessions"]), row["outcome_field"], row["signal_date"])
        for row in daily
    ] == [
        (item.factor, item.horizon_sessions, item.outcome_field, item.signal_date)
        for item in objects.evaluation.daily_evaluations
    ]


def test_compact_identity_projection_nulls_json_and_no_evidence_payloads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, objects, result = _run(monkeypatch, tmp_path)
    output = result["output_root"]
    summary_columns, summaries = _read_csv(output / "factor_summary.csv")
    daily_columns, daily = _read_csv(output / "factor_by_date.csv")
    assert "included_daily_identities" not in summary_columns
    assert "eligible_evidence" not in daily_columns
    assert "observation_status_evidence" not in daily_columns
    expected = runner._identity_collection_sha256(
        objects.evaluation.summaries[0].included_daily_identities,
    )
    assert summaries[0]["included_daily_identity_count"] == "2"
    assert summaries[0]["included_daily_identities_sha256"] == expected
    assert daily[0]["rank_ic"] == ""
    assert daily[0]["high_minus_low_mean_spread"] == ""
    manifest_text = (output / "experiment_manifest.json").read_text(encoding="utf-8")
    assert all(token not in manifest_text for token in ("NaN", "Infinity", "<NA>"))
    assert json.loads(manifest_text)["completed"] is True


def test_temporal_projection_hashes_review_statuses_and_manifest_reconcile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, objects, result = _run(monkeypatch, tmp_path)
    output = result["output_root"]
    summary_columns, summaries = _read_csv(output / "temporal_stability_summary.csv")
    block_columns, blocks = _read_csv(output / "temporal_stability_by_block.csv")
    _, coverage = _read_csv(output / "temporal_coverage.csv")
    assert "included_block_identities" not in summary_columns
    assert "included_daily_identities" not in block_columns
    assert summaries[0]["block_identity_count"] == "4"
    assert summaries[0]["block_identities_sha256"] == (
        runner._identity_collection_sha256(
            objects.temporal.summaries[0].included_block_identities,
        )
    )
    assert blocks[0]["included_daily_identity_count"] == "0"
    assert blocks[0]["included_daily_identities_sha256"] == (
        runner._identity_collection_sha256(())
    )
    assert blocks[0]["mean_daily_rank_ic"] == ""
    assert [row["review_status"] for row in coverage[:3]] == [
        "TEMPORAL_SUPPORT", "COVERAGE_ONLY", "INSUFFICIENT_COVERAGE",
    ]
    assert [
        (row["factor"], int(row["horizon_sessions"]), row["outcome_field"])
        for row in coverage
    ] == [
        (item.factor, item.horizon_sessions, item.outcome_field)
        for item in objects.temporal.summaries
    ]
    temporal_manifest = result["manifest"]["temporal_stability"]
    assert temporal_manifest["review_status_counts"] == {
        "TEMPORAL_SUPPORT": 1,
        "COVERAGE_ONLY": 1,
        "INSUFFICIENT_COVERAGE": 46,
    }
    assert temporal_manifest["block_result_count"] == 192
    assert temporal_manifest["summary_count"] == 48
    assert temporal_manifest["temporal_result_identity"] == "temporal-result-id"
    assert temporal_manifest["limitations"] == list(runner._TEMPORAL_LIMITATIONS)


@pytest.mark.parametrize("corruption", ("source_identity", "dimensions"))
def test_temporal_corruption_aborts_overwrite_and_preserves_previous_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    corruption: str,
) -> None:
    database, _, _, first = _run(monkeypatch, tmp_path)
    output = first["output_root"]
    previous_manifest = (output / "experiment_manifest.json").read_bytes()
    _, objects = _install_pipeline(monkeypatch, database)
    if corruption == "source_identity":
        objects.temporal.source_result_identity = "wrong-source"
        message = "source result identity"
    else:
        objects.temporal.block_results = objects.temporal.block_results[:-1]
        message = "dimensions"
    with pytest.raises(ValueError, match=message):
        runner.run_neutral_panel_factor_evaluation(
            database_path=database,
            start_date="2021-01-04",
            end_date="2021-01-05",
            output_root=output,
            overwrite=True,
        )
    assert (output / "experiment_manifest.json").read_bytes() == previous_manifest
    assert not tuple(output.parent.glob(f".{output.name}.tmp-*"))


def test_database_is_unchanged_and_no_network_or_current_vn100_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import socket

    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("network"))
    database, _, _, _ = _run(monkeypatch, tmp_path)
    assert database.read_bytes() == b"immutable-market-fixture"
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "get_vn100_symbols" not in source
    assert "Vnstock(" not in source


@pytest.mark.parametrize(
    "unsafe",
    (
        runner.PROJECT_ROOT,
        runner.PROJECT_ROOT / ".git",
        runner.PROJECT_ROOT / "data",
        runner.PROJECT_ROOT / "research",
        runner.PROJECT_ROOT / "research_results",
    ),
)
def test_existing_unsafe_and_cache_containment_rejection(
    tmp_path: Path,
    unsafe: Path,
) -> None:
    database = tmp_path / "market.db"
    database.write_bytes(b"db")
    with pytest.raises(ValueError, match="dedicated"):
        runner._validate_output_target(unsafe, database_path=database, overwrite=True)
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        runner._validate_output_target(existing, database_path=database, overwrite=False)
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="contain"):
        runner._resolved_cache_root(output / "cache", output=output, database=database)
    with pytest.raises(ValueError, match="contain"):
        runner._resolved_cache_root(tmp_path, output=output, database=database)


def test_overwrite_is_exact_and_failed_validation_preserves_previous_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, _, first = _run(monkeypatch, tmp_path)
    output = first["output_root"]
    previous_manifest = (output / "experiment_manifest.json").read_bytes()
    _run(monkeypatch, tmp_path, overwrite=True)
    assert output.is_dir()
    assert not tuple(output.parent.glob(f".{output.name}.backup-*"))
    monkeypatch.setattr(
        runner,
        "_validate_emitted",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("injected validation failure")),
    )
    with pytest.raises(ValueError, match="injected"):
        _run(monkeypatch, tmp_path, overwrite=True)
    assert (output / "experiment_manifest.json").read_bytes() == previous_manifest
    assert not tuple(output.parent.glob(f".{output.name}.tmp-*"))


def test_cli_defaults_default_output_and_fresh_import_isolation(tmp_path: Path) -> None:
    arguments = runner._parser().parse_args([
        "--database-path", "data/market.db",
        "--start-date", "2021-01-04",
        "--end-date", "2021-01-05",
    ])
    assert arguments.benchmark_symbol == "VNINDEX"
    assert arguments.minimum_history_sessions == 50
    assert arguments.maximum_staleness_sessions == 5
    assert arguments.cache_root is None
    assert arguments.cache_codec == "npz_numeric_v1"
    expected = runner.PROJECT_ROOT / "research_results" / (
        "quantlab_neutral_panel_factor_evaluation_2021-01-04_2021-01-05"
    )
    assert runner._output_path(
        None, start_date="2021-01-04", end_date="2021-01-05",
    ) == expected.resolve()
    script = """
import json
import sys
import research.run_quantlab_neutral_panel_factor_evaluation
forbidden = {'backtesting.engine', 'strategy.paper_v2_scanner', 'vnstock'}
print(json.dumps(sorted(forbidden.intersection(sys.modules))))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=runner.PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == []
