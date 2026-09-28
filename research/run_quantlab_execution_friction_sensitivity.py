from __future__ import annotations

"""Publish hypothetical Phase 11E friction sensitivity from frozen Phase 7 evidence only."""

import argparse
import csv
from dataclasses import asdict
from hashlib import sha256
import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quantlab.execution.friction_sensitivity import (
    EXPECTED_PROVENANCE,
    FRICTION_COMPONENT_EVIDENCE,
    HYPOTHETICAL_LABEL,
    NEUTRAL_EXECUTION_FRICTION_SENSITIVITY_V1,
    ExecutionFrictionSensitivityResult,
    FrozenPortfolioOutcomeObservation,
    evaluate_execution_friction_sensitivity,
)
from quantlab.identity import canonical_identity_value, canonical_json
from quantlab.portfolio.construction import BLOCKS, BUDGETS, SELECTION_POLICY, WeightingPolicy
from quantlab.portfolio.outcome_evaluation import COST_GRID_BPS, HORIZONS, PATH_METRIC_REASON, PATH_METRIC_STATUS
from research.run_quantlab_portfolio_outcome_evaluation import (
    DEFAULT_OUTPUT_ROOT as DEFAULT_PHASE7_ROOT,
    EXPECTED_PHASE65_MANIFEST_SHA256,
    EXPECTED_OUTCOME_ARTIFACT_SHA256,
    EXPECTED_OUTCOME_IDENTITY,
    EXPECTED_OUTCOME_CONTENT,
    EXPECTED_OBSERVATION_IDENTITY,
    EXPECTED_OBSERVATION_CONTENT,
    EXPECTED_OUTCOME_ROWS,
    FILES as PHASE7_FILES,
)
from quantlab.execution.timing_capacity import (
    EXPECTED_PHASE11A_FRICTION,
    EXPECTED_PHASE11A_RESULT,
    EXPECTED_PHASE11A_SPEC,
    EXPECTED_PHASE6_RESULT,
    EXPECTED_PHASE6_SPEC,
)
from quantlab.execution.provider_provenance import PHASE11C_RESULT_IDENTITY


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PHASE11A_ROOT = PROJECT_ROOT / "research_results/quantlab_execution_foundation"
DEFAULT_PHASE11B_ROOT = PROJECT_ROOT / "research_results/quantlab_execution_timing_capacity_2018-08-07_2026-09-17"
DEFAULT_PHASE11C_ROOT = PROJECT_ROOT / "research_results/quantlab_execution_price_provenance_2018_2026"
DEFAULT_PHASE11D_ROOT = PROJECT_ROOT / "research_results/quantlab_execution_provider_provenance_2018_2026"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "research_results/quantlab_execution_friction_sensitivity_2018-08-07_2026-09-17"
PHASE7_RESULT_IDENTITY = EXPECTED_PROVENANCE["phase7_result_identity"]
PHASE7_SPECIFICATION_FINGERPRINT = EXPECTED_PROVENANCE["phase7_specification_fingerprint"]
PHASE7_MANIFEST_SHA256 = EXPECTED_PROVENANCE["phase7_manifest_sha256"]
PHASE7_DAILY_SHA256 = EXPECTED_PROVENANCE["phase7_daily_artifact_sha256"]
PHASE7_SUMMARY_SHA256 = "13486592d6a0cdb825c4fa58783903869f64c44b3137890314df7787010f29c6"
PHASE7_COST_SHA256 = "b375bc995b60df4dc4b23a8c91c7748d2df191fcd4ce72ff7aff393adc41933c"
PHASE11A_MANIFEST_SHA256 = "43e66e08948c4f3e69f5479a1584d72d061692461719ad16ded3ba627e1ed878"
PHASE11B_MANIFEST_SHA256 = "7df0b35f22ad555c3c2120cf1b0b470672314457cd6ae1ea8c6ca35151cb2171"
PHASE11C_MANIFEST_SHA256 = "1917a02ec37ecb2121d4f52c098dbcab57d77dfa9a98440b42a0ec33d8b9f696"
PHASE11B_RESULT_IDENTITY = EXPECTED_PROVENANCE["phase11b_result_identity"]
PHASE11B_SPECIFICATION_FINGERPRINT = "a383c24c950753b792c30fbd62010a777d7c3388d755014e5bbf4c28659a2b71"
PHASE11D_MANIFEST_SHA256 = "36fe508535650629c7f70a9455241d433c0496ce7e5722bdf88a494a8624aa44"
FILES = (
    "execution_friction_sensitivity_summary.csv",
    "execution_friction_sensitivity_by_block.csv",
    "execution_friction_break_even.csv",
    "execution_friction_sensitivity_manifest.json",
    "execution_friction_sensitivity_report.md",
)
WHOLE_SCOPE = ("whole_period", BLOCKS[0][1], BLOCKS[-1][2])


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _hash(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _require_hash(path: Path, expected: str, label: str) -> str:
    actual = _hash_file(path)
    if actual != expected:
        raise ValueError(f"{label} SHA-256 mismatch: expected {expected}, got {actual}")
    return actual


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def _validate_manifest_artifacts(root: Path, manifest: dict[str, Any], label: str) -> None:
    for name, metadata in manifest.get("artifacts", {}).items():
        path = root / name
        if not path.is_file() or _hash_file(path) != metadata.get("sha256"):
            raise ValueError(f"{label} artifact SHA-256 mismatch: {name}")


def _load_phase11_chain(
    phase11a_root: Path, phase11b_root: Path, phase11c_root: Path, phase11d_root: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, str]]:
    m11a_path = phase11a_root / "execution_contract_manifest.json"
    m11b_path = phase11b_root / "execution_timing_capacity_manifest.json"
    m11c_path = phase11c_root / "execution_price_provenance_manifest.json"
    m11d_path = phase11d_root / "execution_provider_provenance_manifest.json"
    manifests = tuple(_read_json(path, f"Phase 11{letter} manifest") for path, letter in (
        (m11a_path, "A"), (m11b_path, "B"), (m11c_path, "C"), (m11d_path, "D"),
    ))
    for path, expected, label in (
        (m11a_path, PHASE11A_MANIFEST_SHA256, "Phase 11A manifest"),
        (m11b_path, PHASE11B_MANIFEST_SHA256, "Phase 11B manifest"),
        (m11c_path, PHASE11C_MANIFEST_SHA256, "Phase 11C manifest"),
        (m11d_path, PHASE11D_MANIFEST_SHA256, "Phase 11D manifest"),
    ):
        _require_hash(path, expected, label)
    for root, manifest, label in zip((phase11a_root, phase11b_root, phase11c_root, phase11d_root), manifests, ("Phase 11A", "Phase 11B", "Phase 11C", "Phase 11D"), strict=True):
        if not manifest.get("completed"):
            raise ValueError(f"{label} is not completed")
        _validate_manifest_artifacts(root, manifest, label)
    a, b, c, d = manifests
    if (a.get("result_identity"), a.get("specification_fingerprint"), a.get("friction_specification_fingerprint")) != (EXPECTED_PHASE11A_RESULT, EXPECTED_PHASE11A_SPEC, EXPECTED_PHASE11A_FRICTION):
        raise ValueError("frozen Phase 11A provenance mismatch")
    if (b.get("result_identity"), b.get("specification_fingerprint")) != (PHASE11B_RESULT_IDENTITY, PHASE11B_SPECIFICATION_FINGERPRINT):
        raise ValueError("frozen Phase 11B provenance mismatch")
    if (c.get("result_identity"), c.get("monetary_capacity_gate")) != (PHASE11C_RESULT_IDENTITY, "MONETARY_CAPACITY_NOT_DEFENSIBLE"):
        raise ValueError("frozen Phase 11C provenance mismatch")
    if (d.get("result_identity"), d.get("no_network_or_provider_calls")) != (EXPECTED_PROVENANCE["phase11d_result_identity"], True):
        raise ValueError("frozen Phase 11D provenance mismatch")
    ids = {
        "phase11a_result_identity": a["result_identity"],
        "phase11a_specification_fingerprint": a["specification_fingerprint"],
        "phase11a_friction_fingerprint": a["friction_specification_fingerprint"],
        "phase11a_manifest_sha256": _hash_file(m11a_path),
        "phase11b_result_identity": b["result_identity"],
        "phase11b_specification_fingerprint": b["specification_fingerprint"],
        "phase11b_manifest_sha256": _hash_file(m11b_path),
        "phase11c_result_identity": c["result_identity"],
        "phase11c_manifest_sha256": _hash_file(m11c_path),
        "phase11d_result_identity": d["result_identity"],
        "phase11d_manifest_sha256": _hash_file(m11d_path),
    }
    return a, b, c, d, ids


def _load_phase7(phase7_root: Path) -> tuple[dict[str, Any], tuple[FrozenPortfolioOutcomeObservation, ...], tuple[dict[str, str], ...], tuple[dict[str, str], ...], dict[str, str]]:
    manifest_path = phase7_root / PHASE7_FILES[5]
    manifest_sha = _require_hash(manifest_path, PHASE7_MANIFEST_SHA256, "Phase 7 manifest")
    manifest = _read_json(manifest_path, "Phase 7 manifest")
    if not manifest.get("completed") or (manifest.get("result_identity"), manifest.get("specification_fingerprint")) != (PHASE7_RESULT_IDENTITY, PHASE7_SPECIFICATION_FINGERPRINT):
        raise ValueError("frozen Phase 7 identity mismatch")
    if (manifest.get("selection_policy"), manifest.get("weighting_policy"), manifest.get("budgets"), manifest.get("horizons")) != ("ADX_ONLY", "EQUAL_WEIGHT", list(BUDGETS), list(HORIZONS)):
        raise ValueError("Phase 7 portfolio scope mismatch")
    if (manifest.get("cost_sensitivity_grid_bps"), manifest.get("turnover_definition"), manifest.get("cost_semantics")) != (list(COST_GRID_BPS), "frozen Phase 6 one_way_weight_turnover", "formation turnover times bps; descriptive percentage-point deduction only"):
        raise ValueError("Phase 7 cost/turnover contract mismatch")
    if (manifest.get("path_metric_applicability"), manifest.get("path_metric_reason")) != (PATH_METRIC_STATUS, PATH_METRIC_REASON):
        raise ValueError("Phase 7 overlapping-horizon limitations mismatch")
    if manifest.get("temporal_blocks") != [{"name": name, "start_date": start, "end_date": end} for name, start, end in BLOCKS]:
        raise ValueError("Phase 7 temporal blocks mismatch")
    source = manifest.get("source_identities", {})
    phase65_required = {
        "phase65_manifest_sha256": EXPECTED_PHASE65_MANIFEST_SHA256,
        "phase65_outcome_artifact_sha256": EXPECTED_OUTCOME_ARTIFACT_SHA256,
        "phase65_outcome_row_count": EXPECTED_OUTCOME_ROWS,
        "observation_identity": EXPECTED_OBSERVATION_IDENTITY,
        "observation_content_identity": EXPECTED_OBSERVATION_CONTENT,
        "outcome_panel_identity": EXPECTED_OUTCOME_IDENTITY,
        "outcome_content_identity": EXPECTED_OUTCOME_CONTENT,
        "phase6_result_identity": EXPECTED_PHASE6_RESULT,
        "phase6_manifest_sha256": "70677377bb4c6f8feaa4774b519ca72b3b1a05d4fb4760fb9e7ed8d915879cba",
    }
    if any(source.get(key) != value for key, value in phase65_required.items()):
        raise ValueError("Phase 7 Phase 6/6.5 provenance mismatch")
    daily_path, summary_path, cost_path = (phase7_root / PHASE7_FILES[index] for index in (1, 0, 4))
    daily_sha = _require_hash(daily_path, PHASE7_DAILY_SHA256, "Phase 7 daily outcome")
    summary_sha = _require_hash(summary_path, PHASE7_SUMMARY_SHA256, "Phase 7 summary")
    cost_sha = _require_hash(cost_path, PHASE7_COST_SHA256, "Phase 7 cost sensitivity")
    with daily_path.open("r", encoding="utf-8", newline="") as handle:
        raw_daily = tuple(dict(row) for row in csv.DictReader(handle))
    observations = tuple(FrozenPortfolioOutcomeObservation(
        row["session_date"], int(row["requested_budget"]), int(row["horizon_sessions"]),
        row["availability"], _optional_float(row["one_way_weight_turnover"]),
        _optional_float(row["portfolio_stock_return_pct"]), _optional_float(row["portfolio_excess_return_pct_points"]),
        row["identity"],
    ) for row in raw_daily)
    with summary_path.open("r", encoding="utf-8", newline="") as handle:
        summaries = tuple(dict(row) for row in csv.DictReader(handle))
    with cost_path.open("r", encoding="utf-8", newline="") as handle:
        costs = tuple(dict(row) for row in csv.DictReader(handle))
    declared = manifest["artifacts"]
    for name, actual in ((PHASE7_FILES[1], len(observations)), (PHASE7_FILES[0], len(summaries)), (PHASE7_FILES[4], len(costs))):
        if int(declared[name]["rows"]) != actual:
            raise ValueError(f"Phase 7 row count mismatch: {name}")
    ids = {
        "phase6_result_identity": EXPECTED_PHASE6_RESULT,
        "phase6_specification_fingerprint": EXPECTED_PHASE6_SPEC,
        "phase6_manifest_sha256": source["phase6_manifest_sha256"],
        "phase65_manifest_sha256": EXPECTED_PHASE65_MANIFEST_SHA256,
        "phase65_outcome_artifact_sha256": EXPECTED_OUTCOME_ARTIFACT_SHA256,
        "phase65_observation_identity": EXPECTED_OBSERVATION_IDENTITY,
        "phase65_observation_content_identity": EXPECTED_OBSERVATION_CONTENT,
        "phase65_outcome_panel_identity": EXPECTED_OUTCOME_IDENTITY,
        "phase65_outcome_content_identity": EXPECTED_OUTCOME_CONTENT,
        "phase7_result_identity": manifest["result_identity"],
        "phase7_specification_fingerprint": manifest["specification_fingerprint"],
        "phase7_manifest_sha256": manifest_sha,
        "phase7_daily_artifact_sha256": daily_sha,
        "phase7_summary_artifact_sha256": summary_sha,
        "phase7_cost_artifact_sha256": cost_sha,
    }
    return manifest, observations, summaries, costs, ids


def _optional_float(value: str) -> float | None:
    if value == "":
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("persisted Phase 7 numeric value is not finite")
    return number


def _validate_phase7_reconciliation(result: ExecutionFrictionSensitivityResult, phase7_summaries, phase7_costs) -> dict[str, Any]:
    result_index = {(row.requested_budget, row.horizon_sessions, row.hypothetical_all_in_cost_rate_bps): row for row in result.summaries}
    phase7_cost_index = {(int(row["requested_budget"]), int(row["horizon_sessions"]), int(row["cost_rate_bps"])): row for row in phase7_costs}
    if len(phase7_cost_index) != len(BUDGETS) * len(HORIZONS) * len(COST_GRID_BPS):
        raise ValueError("Phase 7 cost grid is incomplete or duplicated")
    tolerance = {"rel_tol": 1e-10, "abs_tol": 1e-10}
    matched_cost_rows = 0
    for key, row in result_index.items():
        reference = phase7_cost_index.get(key)
        if reference is None:
            raise ValueError(f"Phase 7 cost row missing for {key}")
        fields = (
            ("phase7_evaluable_date_count", int(reference["evaluable_date_count"])),
            ("turnover_defined_date_count", int(reference["turnover_defined_date_count"])),
            ("mean_gross_stock_return_pct", _optional_float(reference["mean_gross_stock_return_pct"])),
            ("mean_cost_deduction_pct_points", _optional_float(reference["mean_estimated_turnover_cost_pct_points"])),
            ("mean_net_stock_return_pct", _optional_float(reference["mean_net_stock_return_pct"])),
            ("mean_gross_excess_return_pct_points", _optional_float(reference["mean_gross_excess_return_pct_points"])),
            ("mean_net_excess_return_pct_points", _optional_float(reference["mean_net_excess_return_pct_points"])),
        )
        if any(actual != expected if isinstance(actual, int) else not math.isclose(float(actual), float(expected), **tolerance) for name, expected in fields if (actual := getattr(row, name)) is not None and expected is not None):
            raise ValueError(f"Phase 11E does not reconcile with Phase 7 cost row {key}")
        matched_cost_rows += 1
    phase7_summary_index = {(int(row["requested_budget"]), int(row["horizon_sessions"])): row for row in phase7_summaries}
    if len(phase7_summary_index) != len(BUDGETS) * len(HORIZONS):
        raise ValueError("Phase 7 outcome summary is incomplete or duplicated")
    # Phase 7's no-friction summary uses every fully evaluable date, including the initial date with undefined turnover.
    return {"phase7_cost_grid_rows_matched": matched_cost_rows, "phase7_no_friction_summary_rows_expected": len(phase7_summary_index)}


def _validate_zero_cost_outcomes(observations, phase7_summaries) -> int:
    expected = {(int(row["requested_budget"]), int(row["horizon_sessions"])): row for row in phase7_summaries}
    groups = {(budget, horizon): tuple(item for item in observations if item.requested_budget == budget and item.horizon_sessions == horizon and item.availability == "FULLY_EVALUABLE") for budget in BUDGETS for horizon in HORIZONS}
    for key, items in groups.items():
        ref = expected[key]
        if len(items) != int(ref["evaluable_dates"]):
            raise ValueError(f"all-evaluable Phase 7 count mismatch for {key}")
        for field, values in (
            ("mean_stock_return_pct", tuple(float(item.portfolio_stock_return_pct) for item in items)),
            ("mean_excess_return_pct_points", tuple(float(item.portfolio_excess_return_pct_points) for item in items)),
        ):
            actual = sum(values) / len(values) if values else None
            persisted = _optional_float(ref[field])
            if actual is None or persisted is None or not math.isclose(actual, persisted, rel_tol=1e-10, abs_tol=1e-10):
                raise ValueError(f"all-evaluable zero-friction Phase 7 reconciliation failed for {key}: {field}")
    return len(groups)


def _csv_rows(items) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        row = asdict(item)
        rows.append({key: "" if value is None else value for key, value in row.items()})
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _report(result: ExecutionFrictionSensitivityResult) -> str:
    lines = [
        "# Phase 11E — Neutral Execution Friction Sensitivity", "",
        f"**Result identity:** `{result.identity}`", "",
        f"**Mandatory label:** `{HYPOTHETICAL_LABEL}`", "",
        "## Frozen scope and formula", "",
        "- Phase 6 `ADX_ONLY` / `EQUAL_WEIGHT`; budgets 5, 10, 20; horizons 5, 10, 20.",
        "- Grid: 0, 10, 25, 50 bps; frozen from Phase 7, not selected from results.",
        "- Cost formula: Phase 6 one-way weight turnover × hypothetical aggregate one-way friction bps / 100, deducted in percentage points once per forward observation.",
        "- No costs are compounded across overlapping observations. No executable wealth path is constructed.",
        "- Phase 6 turnover is half the L1 security-weight change over the symbol union plus the absolute cash-weight change.",
        "- Phase 11B target acquisition quantity/notional is not used as turnover.",
        "- Phase 11B close-to-next-open gap remains separate descriptive evidence; it is not slippage and is not deducted.", "",
        "## Mean net excess sensitivity by budget, horizon and assumed rate", "",
        "| Budget | Horizon | Rate (bps) | Included rows | Mean cost (pp) | Mean net excess (pp) | Positive-net-excess rate |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in result.summaries:
        lines.append(f"| {row.requested_budget} | {row.horizon_sessions} | {row.hypothetical_all_in_cost_rate_bps} | {row.included_date_count} | {row.mean_cost_deduction_pct_points!s} | {row.mean_net_excess_return_pct_points!s} | {row.positive_net_excess_rate!s} |")
    lines += ["", "## Component provenance", "", "| Component | Provenance | Applied component rate | Note |", "|---|---|---:|---|"]
    lines.extend(f"| {item.component} | {item.provenance} | {item.rate_bps!s} | {item.note} |" for item in result.friction_components)
    lines += ["", "## Break-even", "", "Any reported value is an analytical `DESCRIPTIVE_BREAK_EVEN_FRICTION`, not a safe, realistic, or executable cost threshold. It is not searched or optimized.", "", "## Limitations", "", *[f"- {item}" for item in result.limitations], ""]
    return "\n".join(lines)


def run_execution_friction_sensitivity(
    *, phase7_root: str | Path = DEFAULT_PHASE7_ROOT,
    phase11a_root: str | Path = DEFAULT_PHASE11A_ROOT,
    phase11b_root: str | Path = DEFAULT_PHASE11B_ROOT,
    phase11c_root: str | Path = DEFAULT_PHASE11C_ROOT,
    phase11d_root: str | Path = DEFAULT_PHASE11D_ROOT,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> dict[str, Any]:
    roots = tuple(Path(value).resolve() for value in (phase7_root, phase11a_root, phase11b_root, phase11c_root, phase11d_root))
    phase7, phase11a, phase11b, phase11c, phase11d = roots
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"output directory already exists: {output}")
    if output in {PROJECT_ROOT, PROJECT_ROOT / "research_results", *roots} or PROJECT_ROOT not in output.parents:
        raise ValueError("output must be a new dedicated in-repository research_results directory")
    phase7_manifest, observations, phase7_summaries, phase7_costs, phase7_ids = _load_phase7(phase7)
    _, manifest11b, _, _, phase11_ids = _load_phase11_chain(phase11a, phase11b, phase11c, phase11d)
    source_ids = {**phase7_ids, **phase11_ids}
    # Include the Phase 11B contract but never its acquisition observations as a turnover source.
    phase11b_target_meta = manifest11b.get("artifacts", {}).get("execution_timing_capacity_by_target.csv")
    if not isinstance(phase11b_target_meta, dict) or not phase11b_target_meta.get("sha256"):
        raise ValueError("Phase 11B target artifact provenance is missing")
    source_ids["phase11b_target_observation_artifact_sha256"] = phase11b_target_meta["sha256"]
    if source_ids.get("phase11a_result_identity") != EXPECTED_PROVENANCE["phase11a_result_identity"]:
        raise ValueError("Phase 11A source identity mismatch")
    if source_ids.get("phase11b_result_identity") != EXPECTED_PROVENANCE["phase11b_result_identity"]:
        raise ValueError("Phase 11B source identity mismatch")
    result = evaluate_execution_friction_sensitivity(observations, source_ids)
    reconciliation = _validate_phase7_reconciliation(result, phase7_summaries, phase7_costs)
    all_evaluable_reconciliation_count = _validate_zero_cost_outcomes(observations, phase7_summaries)
    # Verify monotonicity only within each frozen budget/horizon, without ranking combinations.
    grouped = {}
    for row in result.summaries:
        grouped.setdefault((row.requested_budget, row.horizon_sessions), []).append(row)
    for key, rows in grouped.items():
        ordered = sorted(rows, key=lambda row: row.hypothetical_all_in_cost_rate_bps)
        for left, right in zip(ordered, ordered[1:]):
            if left.mean_net_stock_return_pct is not None and right.mean_net_stock_return_pct is not None and right.mean_net_stock_return_pct > left.mean_net_stock_return_pct + 1e-12:
                raise ValueError(f"higher friction increased stock return for {key}")
            if left.mean_net_excess_return_pct_points is not None and right.mean_net_excess_return_pct_points is not None and right.mean_net_excess_return_pct_points > left.mean_net_excess_return_pct_points + 1e-12:
                raise ValueError(f"higher friction increased excess return for {key}")
    rows_summary = _csv_rows(result.summaries)
    rows_blocks = _csv_rows(result.block_summaries)
    rows_break_even = _csv_rows(result.break_even)
    manifest: dict[str, Any] = {
        "contract": result.contract_name,
        "version": result.contract_version,
        "specification_fingerprint": result.specification_fingerprint,
        "result_identity": result.identity,
        "scope": {"selection_policy": SELECTION_POLICY, "weighting_policy": WeightingPolicy.EQUAL_WEIGHT.value, "budgets": list(BUDGETS), "horizons": list(HORIZONS)},
        "phase6": {key: value for key, value in source_ids.items() if key.startswith("phase6_")},
        "phase65": {key: value for key, value in source_ids.items() if key.startswith("phase65_")},
        "phase7": {"result_identity": phase7_manifest["result_identity"], "specification_fingerprint": phase7_manifest["specification_fingerprint"], "manifest_sha256": phase7_ids["phase7_manifest_sha256"], "daily_sha256": phase7_ids["phase7_daily_artifact_sha256"], "summary_sha256": phase7_ids["phase7_summary_artifact_sha256"], "cost_sensitivity_sha256": phase7_ids["phase7_cost_artifact_sha256"], "phase7_zero_cost_cost_grid_rows_reconciled": reconciliation["phase7_cost_grid_rows_matched"], "all_evaluable_zero_friction_summary_rows_reconciled": all_evaluable_reconciliation_count},
        "phase11": {
            "phase11a": {key: value for key, value in source_ids.items() if key.startswith("phase11a_")},
            "phase11b": {key: value for key, value in source_ids.items() if key.startswith("phase11b_")},
            "phase11c": {key: value for key, value in source_ids.items() if key.startswith("phase11c_")},
            "phase11d": {key: value for key, value in source_ids.items() if key.startswith("phase11d_")},
            "historical_monetary_capacity_research": "DEFERRED",
        },
        "friction_components": [asdict(item) for item in result.friction_components],
        "hypothetical_aggregate_cost_grid_bps": list(COST_GRID_BPS),
        "cost_grid_label": HYPOTHETICAL_LABEL,
        "cost_grid_semantics": "aggregate one-way hypothetical rate applied to Phase 6 one-way weight turnover; all-in rate is not allocated across named components",
        "turnover_definition": "frozen Phase 6 one_way_weight_turnover; half L1 security-weight change plus absolute cash-weight change",
        "cost_formula": "one_way_weight_turnover * hypothetical_all_in_rate_bps / 100 percentage points; each forward observation separately; no compounding",
        "timing_evidence": "Phase 11B close-to-next-open gap is descriptive only, not a fill/slippage estimate, and is not deducted",
        "path_metric_status": PATH_METRIC_STATUS,
        "path_metric_reason": PATH_METRIC_REASON,
        "temporal_blocks": [{"name": name, "start_date": start, "end_date": end} for name, start, end in BLOCKS],
        "break_even_formula": "mean gross excess percentage points * 100 / mean one-way weight turnover; analytical only; undefined if no positive nonzero denominator or nonnegative zero-crossing is unavailable",
        "limitations": list(result.limitations),
        "no_historical_cost_estimate": HYPOTHETICAL_LABEL,
        "no_policy_ranking_or_selection": True,
        "artifacts": {},
        "completed": True,
    }
    report = _report(result)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:
        _write_csv(temporary / FILES[0], rows_summary)
        _write_csv(temporary / FILES[1], rows_blocks)
        _write_csv(temporary / FILES[2], rows_break_even)
        (temporary / FILES[4]).write_text(report, encoding="utf-8", newline="\n")
        manifest["artifacts"] = {
            FILES[0]: {"rows": len(rows_summary), "sha256": _hash_file(temporary / FILES[0])},
            FILES[1]: {"rows": len(rows_blocks), "sha256": _hash_file(temporary / FILES[1])},
            FILES[2]: {"rows": len(rows_break_even), "sha256": _hash_file(temporary / FILES[2])},
            FILES[4]: {"rows": len(report.splitlines()), "sha256": _hash_file(temporary / FILES[4])},
        }
        (temporary / FILES[3]).write_bytes(canonical_json(canonical_identity_value(manifest)) + b"\n")
        temporary.replace(output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {"result": result, "manifest": manifest, "output_root": output, "reconciliation": reconciliation}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase7-root", type=Path, default=DEFAULT_PHASE7_ROOT)
    parser.add_argument("--phase11a-root", type=Path, default=DEFAULT_PHASE11A_ROOT)
    parser.add_argument("--phase11b-root", type=Path, default=DEFAULT_PHASE11B_ROOT)
    parser.add_argument("--phase11c-root", type=Path, default=DEFAULT_PHASE11C_ROOT)
    parser.add_argument("--phase11d-root", type=Path, default=DEFAULT_PHASE11D_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args(argv)
    published = run_execution_friction_sensitivity(
        phase7_root=args.phase7_root, phase11a_root=args.phase11a_root,
        phase11b_root=args.phase11b_root, phase11c_root=args.phase11c_root,
        phase11d_root=args.phase11d_root, output_root=args.output_root,
    )
    print(f"Phase 11E: EXECUTION_FRICTION_SENSITIVITY_READY; identity={published['result'].identity}; summaries={len(published['result'].summaries)}; blocks={len(published['result'].block_summaries)}; output={published['output_root']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
