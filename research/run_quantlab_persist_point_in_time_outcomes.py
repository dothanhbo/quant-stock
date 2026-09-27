from __future__ import annotations

"""Persist the frozen Phase 5.9B outcome panel without redefining outcomes."""

import argparse
import json
import math
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Mapping

import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database_coverage import build_database_coverage_index
from quantlab.catalog import build_market_data_snapshot
from quantlab.features import PointInTimeUniverseContext
from quantlab.identity import canonical_identity_value, canonical_json
from quantlab.panels import (
    POINT_IN_TIME_FORWARD_OUTCOMES_5_10_20_V1,
    PointInTimeOutcomePanel,
    build_point_in_time_observation_index,
    build_point_in_time_outcome_panel,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_NAME = "quantlab.point_in_time_outcome_evidence"
CONTRACT_VERSION = "v1"
ARTIFACT_NAME = "point_in_time_forward_outcomes.csv"
MANIFEST_NAME = "point_in_time_forward_outcomes_manifest.json"
DEFAULT_SOURCE_MANIFEST = (
    PROJECT_ROOT / "research_results" /
    "quantlab_neutral_panel_factor_evaluation_2018-08-07_2026-09-17" /
    "experiment_manifest.json"
)
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "research_results" /
    "quantlab_point_in_time_outcome_evidence_2018-08-07_2026-09-17"
)
EXPECTED_SOURCE_MANIFEST_SHA256 = (
    "4e85b178eec49034a41fef2cbc6a99797c1f1af59ccceae8ba19b45ffa2306ea"
)
EXPECTED_SOURCE_MANIFEST_IDENTITY = (
    "a6d1a589f0c74533787cb0151b96b8bbcfb866aa088781902ebb6af4e2a4bf2e"
)
EXPECTED_RUNNER_CONTRACT = "quantlab.neutral_panel_factor_evaluation_runner"
EXPECTED_RUNNER_VERSION = "v6"
EXPECTED_DATABASE_SHA256 = (
    "27b593698b9aac4ee1395cd9d4eb845cbab1a92cc8822ef170098e265258dc67"
)
EXPECTED_SNAPSHOT_ID = (
    "d209f580c4411b59092774888d23a5b92b8823a2acbf1d922aff73f3909c1cbd"
)
EXPECTED_SNAPSHOT_CONTENT = (
    "563391c358e4d5b9312833580c28db091623d7fa962d73d7df17379189d10541"
)
EXPECTED_UNIVERSE_IDENTITY = (
    "bda06f669e1179c14086b6d128c6a2ccca013d91c944e485c564070806a86700"
)
EXPECTED_OBSERVATION_IDENTITY = (
    "c44180b130e12895ce79cdd4a93334860f969a3248444c5d6f7daec16b27fe9b"
)
EXPECTED_OBSERVATION_CONTENT = (
    "53eaa0a254a0578ef91fd2d4b6e5c990fed4857227b07797fd13d4e2b56c223f"
)
EXPECTED_OUTCOME_IDENTITY = (
    "d7c1654402b64a6a5a1701b36972ea46eeb004d8531a99cf4cdafdf669ab9c0a"
)
EXPECTED_OUTCOME_CONTENT = (
    "4a4690e14eccf7e026b3c7f47ab734794c38d3aad5ba3958c36f80a9a8a4011f"
)
EXPECTED_DATASET_IDENTITY = (
    "d480844cff185ed56723eec04615e99d5bfb23f994c372c1cb3d42e02ff75d2d"
)
EXPECTED_DATASET_CONTENT = (
    "de96cd4d5979d3b962ce2f2401ac8c8a6d23666f0409aaa3da9ef798745c2de1"
)
EXPECTED_ROWS = 181_189
EXPECTED_START_DATE = "2018-08-07"
EXPECTED_END_DATE = "2026-09-17"


@dataclass(frozen=True, slots=True)
class VerifiedSourceManifest:
    path: Path
    file_sha256: str
    identity: str
    payload: Mapping[str, Any]


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _load_source_manifest(
    path: str | Path,
    *,
    require_canonical_checkpoint: bool = True,
) -> VerifiedSourceManifest:
    source_path = Path(path).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"missing source Phase 5.9B manifest: {source_path}")
    file_hash = _file_sha256(source_path)
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("source Phase 5.9B manifest is not valid JSON") from exc
    manifest_identity = _identity(payload)
    if require_canonical_checkpoint:
        checks = {
            "manifest SHA-256": (file_hash, EXPECTED_SOURCE_MANIFEST_SHA256),
            "manifest identity": (manifest_identity, EXPECTED_SOURCE_MANIFEST_IDENTITY),
            "runner contract": (payload.get("runner_contract"), EXPECTED_RUNNER_CONTRACT),
            "runner version": (payload.get("runner_version"), EXPECTED_RUNNER_VERSION),
            "completed": (payload.get("completed"), True),
            "start date": (
                (payload.get("requested_bounds") or {}).get("start_date"),
                EXPECTED_START_DATE,
            ),
            "end date": (
                (payload.get("requested_bounds") or {}).get("end_date"),
                EXPECTED_END_DATE,
            ),
            "observation rows": (
                (payload.get("counts") or {}).get("observation_rows"), EXPECTED_ROWS,
            ),
        }
        for name, (actual, expected) in checks.items():
            if actual != expected:
                raise ValueError(
                    f"canonical Phase 5.9B {name} mismatch: expected {expected!r}, "
                    f"got {actual!r}"
                )
    return VerifiedSourceManifest(source_path, file_hash, manifest_identity, payload)


def _validate_source_panel(
    panel: PointInTimeOutcomePanel,
    source: VerifiedSourceManifest,
    *,
    database_sha256: str,
) -> pd.DataFrame:
    if not isinstance(panel, PointInTimeOutcomePanel):
        raise TypeError("panel must be the authoritative PointInTimeOutcomePanel")
    manifest = source.payload
    expected = {
        "database SHA-256": ((manifest.get("database") or {}).get("sha256"), database_sha256),
        "snapshot identity": ((manifest.get("snapshot") or {}).get("snapshot_id"), panel.snapshot_id),
        "universe identity": (manifest.get("universe_membership_identity"), panel.universe_membership_identity),
        "observation identity": ((manifest.get("observation_index") or {}).get("identity"), panel.observation_index_identity),
        "observation content identity": (
            (manifest.get("observation_index") or {}).get("content_identity"),
            panel.observation_content_identity,
        ),
        "outcome identity": ((manifest.get("outcome_panel") or {}).get("identity"), panel.identity),
        "outcome content identity": (
            (manifest.get("outcome_panel") or {}).get("content_identity"),
            panel.outcome_content_identity,
        ),
        "observation rows": ((manifest.get("counts") or {}).get("observation_rows"), panel.observation_row_count),
        "start date": ((manifest.get("requested_bounds") or {}).get("start_date"), panel.requested_start_date),
        "end date": ((manifest.get("requested_bounds") or {}).get("end_date"), panel.requested_through_date),
        "benchmark": (manifest.get("benchmark_symbol"), panel.benchmark_symbol),
    }
    for name, (manifest_value, panel_value) in expected.items():
        if manifest_value != panel_value:
            raise ValueError(
                f"source manifest / outcome panel {name} mismatch: "
                f"{manifest_value!r} != {panel_value!r}"
            )
    if tuple(panel.spec.horizons) != (5, 10, 20):
        raise ValueError("outcome evidence requires exactly horizons 5, 10, and 20")
    if panel.spec.fingerprint != POINT_IN_TIME_FORWARD_OUTCOMES_5_10_20_V1.fingerprint:
        raise ValueError("outcome specification fingerprint is not the authoritative frozen spec")
    frame = panel.frame
    if tuple(frame.columns) != panel.spec.output_columns:
        raise ValueError("outcome panel exposes an unsupported schema")
    ordered = frame.sort_values(["session_date", "symbol"], kind="stable").reset_index(drop=True)
    if not frame.reset_index(drop=True).equals(ordered):
        raise ValueError("outcome rows are not in deterministic session-date/symbol order")
    if frame.duplicated(["session_date", "symbol"]).any():
        raise ValueError("duplicate authoritative session-date/symbol observation")
    if not frame["benchmark_symbol"].astype(str).eq(panel.benchmark_symbol).all():
        raise ValueError("outcome rows contain inconsistent benchmark symbols")
    effective = manifest.get("effective_bounds") or {}
    expected_first = effective.get(
        "observation_first_session", panel.metadata.get("first_observation_session")
    )
    expected_last = effective.get(
        "observation_last_session", panel.metadata.get("last_observation_session")
    )
    if panel.metadata.get("first_observation_session") != expected_first:
        raise ValueError("outcome evidence first observation session does not match source")
    if panel.metadata.get("last_observation_session") != expected_last:
        raise ValueError("outcome evidence last observation session does not match source")
    if not frame.empty and (
        str(frame["session_date"].min()) < panel.requested_start_date
        or str(frame["session_date"].max()) > panel.requested_through_date
    ):
        raise ValueError("outcome evidence rows fall outside the frozen requested bounds")
    for horizon in panel.spec.horizons:
        target = f"target_session_{horizon}"
        stock = f"stock_forward_return_{horizon}_pct"
        benchmark = f"benchmark_forward_return_{horizon}_pct"
        excess = f"excess_forward_return_{horizon}_pct_points"
        availability = f"outcome_{horizon}__availability"
        has_target = frame[target].notna()
        if not (frame.loc[has_target, target].astype(str) > frame.loc[has_target, "session_date"].astype(str)).all():
            raise ValueError(f"horizon {horizon} target is not strictly after formation")
        available = frame[availability].astype(str).eq("AVAILABLE")
        if frame.loc[available, [target, stock, benchmark, excess]].isna().any(axis=None):
            raise ValueError(f"horizon {horizon} available outcome is incomplete")
        if frame.loc[~available, [stock, benchmark, excess]].notna().any(axis=None):
            raise ValueError(f"horizon {horizon} unavailable outcome contains returns")
        differences = (
            frame.loc[available, stock].astype(float)
            - frame.loc[available, benchmark].astype(float)
            - frame.loc[available, excess].astype(float)
        )
        if not all(math.isclose(float(value), 0.0, rel_tol=1e-12, abs_tol=1e-12) for value in differences):
            raise ValueError(f"horizon {horizon} excess return does not reconcile")
    return frame


def _manifest_payload(
    panel: PointInTimeOutcomePanel,
    source: VerifiedSourceManifest,
    *,
    database_path: Path,
    database_sha256: str,
    artifact_sha256: str,
) -> dict[str, Any]:
    manifest = source.payload
    return {
        "contract_name": CONTRACT_NAME,
        "contract_version": CONTRACT_VERSION,
        "completed": True,
        "source_phase59b": {
            "manifest_path": str(source.path),
            "manifest_sha256": source.file_sha256,
            "manifest_identity": source.identity,
            "runner_contract": manifest["runner_contract"],
            "runner_version": manifest["runner_version"],
        },
        "database": {
            "canonical_path": str(database_path.resolve()),
            "sha256": database_sha256,
        },
        "snapshot": {
            "identity": panel.snapshot_id,
            "logical_content_fingerprint": (manifest.get("snapshot") or {})[
                "logical_content_fingerprint"
            ],
        },
        "universe_membership_identity": panel.universe_membership_identity,
        "observation_index": {
            "identity": panel.observation_index_identity,
            "content_identity": panel.observation_content_identity,
        },
        "outcome_panel": {
            "identity": panel.identity,
            "content_identity": panel.outcome_content_identity,
            "specification_fingerprint": panel.spec.fingerprint,
            "benchmark_session_method": panel.spec.benchmark_session_method,
            "return_formulas": list(panel.spec.return_formulas),
            "availability_precedence": list(panel.spec.availability_precedence),
        },
        "research_dataset": dict(manifest["research_dataset"]),
        "row_count": panel.observation_row_count,
        "date_range": {
            "start_date": panel.requested_start_date,
            "end_date": panel.requested_through_date,
        },
        "benchmark_symbol": panel.benchmark_symbol,
        "horizons": list(panel.spec.horizons),
        "ordering": "session_date ascending, then symbol ascending; authoritative panel order",
        "row_identity": "no separate upstream row identity; authoritative key is session_date plus symbol",
        "artifact": {
            "filename": ARTIFACT_NAME,
            "sha256": artifact_sha256,
            "columns": list(panel.spec.output_columns),
        },
        "provenance_assertions": {
            "persistence_only": True,
            "existing_frozen_outcome_semantics_reused": True,
            "new_outcome_model_introduced": False,
            "performance_filtering_performed": False,
            "phase7_analysis_performed": False,
            "network_or_market_data_update_performed": False,
        },
    }


def persist_point_in_time_outcome_evidence(
    panel: PointInTimeOutcomePanel,
    *,
    source: VerifiedSourceManifest,
    database_path: str | Path,
    database_sha256: str,
    output_root: str | Path,
) -> dict[str, Any]:
    """Serialize an already-authoritative outcome panel; never calculate labels."""
    database = Path(database_path).expanduser().resolve()
    output = Path(output_root).expanduser()
    if not output.is_absolute():
        output = (PROJECT_ROOT / output).resolve()
    else:
        output = output.resolve()
    if output.exists():
        raise FileExistsError(f"output directory already exists: {output}")
    if output in {PROJECT_ROOT, PROJECT_ROOT / "research_results", source.path.parent, database}:
        raise ValueError("output_root must be a new dedicated evidence directory")
    if output.is_symlink():
        raise ValueError("output_root must not be a symlink")
    frame = _validate_source_panel(panel, source, database_sha256=database_sha256)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:
        artifact_path = temporary / ARTIFACT_NAME
        frame.to_csv(
            artifact_path,
            index=False,
            encoding="utf-8",
            lineterminator="\n",
            na_rep="",
        )
        artifact_hash = _file_sha256(artifact_path)
        manifest_payload = _manifest_payload(
            panel,
            source,
            database_path=database,
            database_sha256=database_sha256,
            artifact_sha256=artifact_hash,
        )
        (temporary / MANIFEST_NAME).write_text(
            json.dumps(manifest_payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return manifest_payload


def _reconstruct_canonical_panel(source: VerifiedSourceManifest):
    manifest = source.payload
    database = Path(manifest["database"]["canonical_path"]).resolve()
    actual_database_hash = _file_sha256(database)
    if actual_database_hash != manifest["database"]["sha256"]:
        raise ValueError(
            "canonical database SHA-256 mismatch before database open: "
            f"expected {manifest['database']['sha256']}, got {actual_database_hash}"
        )
    snapshot = build_market_data_snapshot(database)
    bounds = manifest["requested_bounds"]
    coverage_settings = manifest["coverage"]
    coverage = build_database_coverage_index(
        snapshot.first_session_date,
        bounds["end_date"],
        minimum_history_sessions=coverage_settings["minimum_history_sessions"],
        maximum_staleness_sessions=coverage_settings["maximum_staleness_sessions"],
        database_path=database,
    )
    universe = PointInTimeUniverseContext.from_coverage_index(coverage)
    observation = build_point_in_time_observation_index(
        snapshot,
        universe,
        benchmark_symbol=manifest["benchmark_symbol"],
        start_date=bounds["start_date"],
        through_date=bounds["end_date"],
    )
    panel = build_point_in_time_outcome_panel(observation, snapshot)
    return database, actual_database_hash, snapshot, universe, observation, panel


def run_point_in_time_outcome_evidence_persistence(
    *,
    source_manifest_path: str | Path = DEFAULT_SOURCE_MANIFEST,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> dict[str, Any]:
    source = _load_source_manifest(source_manifest_path)
    database, database_hash, snapshot, universe, observation, panel = (
        _reconstruct_canonical_panel(source)
    )
    checks = {
        "snapshot identity": (snapshot.snapshot_id, EXPECTED_SNAPSHOT_ID),
        "snapshot content": (snapshot.logical_content_fingerprint, EXPECTED_SNAPSHOT_CONTENT),
        "universe identity": (universe.membership_identity, EXPECTED_UNIVERSE_IDENTITY),
        "observation rows": (observation.total_membership_row_count, EXPECTED_ROWS),
        "observation identity": (observation.identity, EXPECTED_OBSERVATION_IDENTITY),
        "observation content": (observation.content_identity, EXPECTED_OBSERVATION_CONTENT),
        "outcome identity": (panel.identity, EXPECTED_OUTCOME_IDENTITY),
        "outcome content": (panel.outcome_content_identity, EXPECTED_OUTCOME_CONTENT),
    }
    for name, (actual, expected) in checks.items():
        if actual != expected:
            raise ValueError(f"canonical {name} mismatch: expected {expected!r}, got {actual!r}")
    return persist_point_in_time_outcome_evidence(
        panel,
        source=source,
        database_path=database,
        database_sha256=database_hash,
        output_root=output_root,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", default=str(DEFAULT_SOURCE_MANIFEST))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    arguments = parser.parse_args(argv)
    result = run_point_in_time_outcome_evidence_persistence(
        source_manifest_path=arguments.source_manifest,
        output_root=arguments.output_root,
    )
    print(json.dumps({
        "output_root": str(Path(arguments.output_root).resolve()),
        "row_count": result["row_count"],
        "outcome_panel_identity": result["outcome_panel"]["identity"],
        "artifact_sha256": result["artifact"]["sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
