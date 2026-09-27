from __future__ import annotations

import csv
from hashlib import sha256
import json
from pathlib import Path

import pandas as pd
import pytest

from quantlab.features import PointInTimeUniverseContext
from quantlab.panels import (
    POINT_IN_TIME_FORWARD_OUTCOMES_5_10_20_V1,
    build_point_in_time_observation_index,
    build_point_in_time_outcome_panel,
)
from research import run_quantlab_persist_point_in_time_outcomes as runner
from tests.quantlab_panel_test_support import (
    InMemoryMarketDataSnapshot,
    make_ohlcv_frame,
)


def _fixture(tmp_path: Path, *, empty_first_session: bool = False):
    sessions = tuple(pd.bdate_range("2024-01-02", periods=30).strftime("%Y-%m-%d"))
    benchmark = make_ohlcv_frame(
        "VNINDEX", sessions, tuple(100.0 + index for index in range(30))
    )
    aaa = make_ohlcv_frame("AAA", sessions, tuple(10.0 + index for index in range(30)))
    missing = make_ohlcv_frame(
        "MISS", sessions, tuple(20.0 + index for index in range(30))
    )
    first_member_position = 1 if empty_first_session else 0
    missing_target_position = first_member_position + 5
    missing = missing.loc[
        missing["time"] != pd.Timestamp(sessions[missing_target_position])
    ].reset_index(drop=True)
    snapshot = InMemoryMarketDataSnapshot({"VNINDEX": benchmark, "AAA": aaa, "MISS": missing})
    context = PointInTimeUniverseContext.from_memberships(
        universe_mode="persistence_fixture",
        memberships={
            session: (
                () if empty_first_session and index == 0 else ("MISS", "AAA")
            )
            for index, session in enumerate(sessions[: first_member_position + 3])
        },
    )
    observation = build_point_in_time_observation_index(
        snapshot,
        context,
        benchmark_symbol="VNINDEX",
        start_date=sessions[0],
        through_date=sessions[2],
    )
    panel = build_point_in_time_outcome_panel(observation, snapshot)
    database_hash = "d" * 64
    payload = {
        "runner_contract": runner.EXPECTED_RUNNER_CONTRACT,
        "runner_version": runner.EXPECTED_RUNNER_VERSION,
        "completed": True,
        "requested_bounds": {
            "start_date": panel.requested_start_date,
            "end_date": panel.requested_through_date,
        },
        "effective_bounds": {
            "observation_first_session": panel.metadata["first_observation_session"],
            "observation_last_session": panel.metadata["last_observation_session"],
        },
        "counts": {"observation_rows": panel.observation_row_count},
        "database": {"canonical_path": str(tmp_path / "market.db"), "sha256": database_hash},
        "snapshot": {
            "snapshot_id": panel.snapshot_id,
            "logical_content_fingerprint": "fixture-snapshot-content",
        },
        "universe_membership_identity": panel.universe_membership_identity,
        "observation_index": {
            "identity": panel.observation_index_identity,
            "content_identity": panel.observation_content_identity,
        },
        "outcome_panel": {
            "identity": panel.identity,
            "content_identity": panel.outcome_content_identity,
        },
        "research_dataset": {
            "identity": "fixture-dataset-identity",
            "content_identity": "fixture-dataset-content",
        },
        "benchmark_symbol": panel.benchmark_symbol,
    }
    source_path = tmp_path / "experiment_manifest.json"
    source_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    source = runner._load_source_manifest(source_path, require_canonical_checkpoint=False)
    return sessions, panel, source, database_hash


def _persist(tmp_path: Path, name: str = "evidence"):
    sessions, panel, source, database_hash = _fixture(tmp_path)
    output = tmp_path / name
    manifest = runner.persist_point_in_time_outcome_evidence(
        panel,
        source=source,
        database_path=tmp_path / "market.db",
        database_sha256=database_hash,
        output_root=output,
    )
    return sessions, panel, source, output, manifest


def test_required_schema_horizons_ordering_and_authoritative_keys(tmp_path: Path) -> None:
    _, panel, _, output, manifest = _persist(tmp_path)
    artifact = pd.read_csv(output / runner.ARTIFACT_NAME)
    assert tuple(artifact.columns) == POINT_IN_TIME_FORWARD_OUTCOMES_5_10_20_V1.output_columns
    assert manifest["horizons"] == [5, 10, 20]
    assert manifest["row_count"] == panel.observation_row_count == len(artifact)
    assert list(zip(artifact["session_date"], artifact["symbol"], strict=True)) == sorted(
        zip(artifact["session_date"], artifact["symbol"], strict=True)
    )
    assert not artifact.duplicated(["session_date", "symbol"]).any()
    assert manifest["row_identity"].startswith("no separate upstream row identity")


def test_availability_targets_and_nulls_are_serialized_without_recalculation(tmp_path: Path) -> None:
    sessions, panel, _, output, _ = _persist(tmp_path)
    with (output / runner.ARTIFACT_NAME).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    missing = next(
        row for row in rows if row["session_date"] == sessions[0] and row["symbol"] == "MISS"
    )
    authoritative = panel.frame.loc[
        (panel.frame["session_date"] == sessions[0]) & (panel.frame["symbol"] == "MISS")
    ].iloc[0]
    assert missing["outcome_5__availability"] == "MISSING_STOCK_TARGET_CLOSE"
    assert missing["target_session_5"] == str(authoritative["target_session_5"])
    assert missing["stock_forward_return_5_pct"] == ""
    assert missing["benchmark_forward_return_5_pct"] == ""
    assert missing["excess_forward_return_5_pct_points"] == ""


def test_manifest_binds_source_and_all_panel_provenance(tmp_path: Path) -> None:
    _, panel, source, output, manifest = _persist(tmp_path)
    disk = json.loads((output / runner.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert disk == manifest
    assert disk["source_phase59b"] == {
        "manifest_path": str(source.path),
        "manifest_sha256": source.file_sha256,
        "manifest_identity": source.identity,
        "runner_contract": runner.EXPECTED_RUNNER_CONTRACT,
        "runner_version": runner.EXPECTED_RUNNER_VERSION,
    }
    assert disk["observation_index"] == {
        "identity": panel.observation_index_identity,
        "content_identity": panel.observation_content_identity,
    }
    assert disk["outcome_panel"]["identity"] == panel.identity
    assert disk["outcome_panel"]["content_identity"] == panel.outcome_content_identity
    assert disk["outcome_panel"]["specification_fingerprint"] == panel.spec.fingerprint
    assert disk["provenance_assertions"] == {
        "persistence_only": True,
        "existing_frozen_outcome_semantics_reused": True,
        "new_outcome_model_introduced": False,
        "performance_filtering_performed": False,
        "phase7_analysis_performed": False,
        "network_or_market_data_update_performed": False,
    }


def test_artifact_hash_and_repeated_serialization_are_deterministic(tmp_path: Path) -> None:
    _, panel, source, first_output, first = _persist(tmp_path, "first")
    second_output = tmp_path / "second"
    second = runner.persist_point_in_time_outcome_evidence(
        panel,
        source=source,
        database_path=tmp_path / "market.db",
        database_sha256="d" * 64,
        output_root=second_output,
    )
    first_bytes = (first_output / runner.ARTIFACT_NAME).read_bytes()
    second_bytes = (second_output / runner.ARTIFACT_NAME).read_bytes()
    assert first_bytes == second_bytes
    assert first["artifact"]["sha256"] == second["artifact"]["sha256"]
    assert first["artifact"]["sha256"] == sha256(first_bytes).hexdigest()
    assert (first_output / runner.MANIFEST_NAME).read_bytes() == (
        second_output / runner.MANIFEST_NAME
    ).read_bytes()


def test_serializer_never_invokes_observation_or_outcome_builders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, panel, source, database_hash = _fixture(tmp_path)
    monkeypatch.setattr(
        runner, "build_point_in_time_observation_index",
        lambda *args, **kwargs: pytest.fail("serializer rebuilt observations"),
    )
    monkeypatch.setattr(
        runner, "build_point_in_time_outcome_panel",
        lambda *args, **kwargs: pytest.fail("serializer recalculated outcomes"),
    )
    runner.persist_point_in_time_outcome_evidence(
        panel,
        source=source,
        database_path=tmp_path / "market.db",
        database_sha256=database_hash,
        output_root=tmp_path / "evidence",
    )


def test_source_or_database_provenance_mismatch_fails_before_output(tmp_path: Path) -> None:
    _, panel, source, database_hash = _fixture(tmp_path)
    output = tmp_path / "evidence"
    with pytest.raises(ValueError, match="database SHA-256 mismatch"):
        runner.persist_point_in_time_outcome_evidence(
            panel,
            source=source,
            database_path=tmp_path / "market.db",
            database_sha256="wrong",
            output_root=output,
        )
    assert not output.exists()
    altered = dict(source.payload)
    altered["outcome_panel"] = dict(altered["outcome_panel"], identity="wrong")
    bad_path = tmp_path / "bad_manifest.json"
    bad_path.write_text(json.dumps(altered), encoding="utf-8")
    bad_source = runner._load_source_manifest(bad_path, require_canonical_checkpoint=False)
    with pytest.raises(ValueError, match="outcome identity mismatch"):
        runner.persist_point_in_time_outcome_evidence(
            panel,
            source=bad_source,
            database_path=tmp_path / "market.db",
            database_sha256=database_hash,
            output_root=output,
        )
    assert not output.exists()


def test_existing_output_is_never_overwritten(tmp_path: Path) -> None:
    _, panel, source, database_hash = _fixture(tmp_path)
    output = tmp_path / "evidence"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("unchanged", encoding="utf-8")
    with pytest.raises(FileExistsError, match="already exists"):
        runner.persist_point_in_time_outcome_evidence(
            panel,
            source=source,
            database_path=tmp_path / "market.db",
            database_sha256=database_hash,
            output_root=output,
        )
    assert marker.read_text(encoding="utf-8") == "unchanged"


def test_requested_start_may_have_empty_membership_without_changing_bounds(
    tmp_path: Path,
) -> None:
    sessions, panel, source, database_hash = _fixture(
        tmp_path, empty_first_session=True
    )
    assert panel.requested_start_date == sessions[0]
    assert panel.frame["session_date"].min() == sessions[1]
    output = tmp_path / "evidence"
    manifest = runner.persist_point_in_time_outcome_evidence(
        panel,
        source=source,
        database_path=tmp_path / "market.db",
        database_sha256=database_hash,
        output_root=output,
    )
    assert manifest["date_range"]["start_date"] == sessions[0]
