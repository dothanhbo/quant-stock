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

from quantlab.evaluation import NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1
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
    calls: dict[str, list[tuple[tuple[Any, ...], dict[str, Any]]]] = {
        name: [] for name in (
            "snapshot", "coverage", "universe", "observation", "source",
            "features", "outcomes", "dataset", "evaluation",
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
    manifest = result["manifest"]
    assert manifest["observation_index"]["identity"] == "observation-id"
    assert manifest["features"]["computation_identity"] == "feature-computation-id"
    assert manifest["research_dataset"]["identity"] == "dataset-id"
    assert manifest["evaluation"]["result_identity"] == "evaluation-id"


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
    assert summary_columns == runner._SUMMARY_COLUMNS
    assert daily_columns == runner._DAILY_COLUMNS
    assert coverage_columns == runner._COVERAGE_COLUMNS
    assert observation_columns == runner._OBSERVATION_COLUMNS
    assert (len(summaries), len(daily), len(coverage), len(observations)) == (48, 96, 48, 2)
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
