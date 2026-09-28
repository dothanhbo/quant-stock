from __future__ import annotations

"""Publish the post-evidence Phase 10D neutral risk-policy governance gate."""

import argparse
import csv
from dataclasses import fields
from enum import Enum
from hashlib import sha256
import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quantlab.identity import canonical_identity_value, canonical_json
from quantlab.portfolio import (
    DECISION_GATE_TIMING,
    FORWARD_PROTOCOL_V1,
    NEUTRAL_RISK_POLICY_DECISION_GATE_V1,
    PolicyDecisionFacts,
    RiskPolicyDecisionInput,
    RiskPolicyDecisionResult,
    evaluate_risk_policy_decision_gate,
)
from quantlab.portfolio.risk_policy_decision import (
    EXPECTED_PHASE10A_RESULT,
    EXPECTED_PHASE10A_SPEC,
    EXPECTED_PHASE10B_RESULT,
    EXPECTED_PHASE10B_SPEC,
    EXPECTED_PHASE10C_RESULT,
    EXPECTED_PHASE10C_SPEC,
    EXPECTED_POLICY_FINGERPRINTS,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PHASE10A_ROOT = PROJECT_ROOT / "research_results/quantlab_portfolio_risk_2018-08-07_2026-09-17"
DEFAULT_PHASE10B_ROOT = PROJECT_ROOT / "research_results/quantlab_portfolio_risk_policy_2018-08-07_2026-09-17"
DEFAULT_PHASE10C_ROOT = PROJECT_ROOT / "research_results/quantlab_portfolio_risk_policy_outcome_2018-08-07_2026-09-17"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "research_results/quantlab_risk_policy_decision_2018-08-07_2026-09-17"
FILES = (
    "risk_policy_decision_summary.csv",
    "risk_policy_decision_evidence.csv",
    "risk_policy_decision_manifest.json",
    "risk_policy_decision_report.md",
)
TOLERANCE = 1e-12


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest(root: Path, name: str, result: str, specification: str) -> tuple[dict[str, Any], str]:
    path = root / name
    if not path.is_file():
        raise FileNotFoundError(f"missing frozen manifest: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("completed") is not True:
        raise ValueError(f"incomplete frozen manifest: {name}")
    if manifest.get("result_identity") != result or manifest.get("specification_fingerprint") != specification:
        raise ValueError(f"{name} result/specification provenance mismatch")
    for artifact_name, metadata in manifest.get("artifacts", {}).items():
        artifact = root / artifact_name
        if not artifact.is_file() or _file_hash(artifact) != metadata.get("sha256"):
            raise ValueError(f"{name} artifact provenance mismatch: {artifact_name}")
    return manifest, _file_hash(path)


def _csv(path: Path) -> tuple[dict[str, str], ...]:
    with path.open(encoding="utf-8", newline="") as handle:
        return tuple(dict(row) for row in csv.DictReader(handle))


def _float(row: dict[str, str], name: str) -> float | None:
    value = row.get(name, "")
    if value == "":
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"non-finite frozen evidence: {name}")
    return result


def _structural_facts(
    policy: str,
    policy_rows: tuple[dict[str, str], ...],
    position_rows: tuple[dict[str, str], ...],
) -> tuple[int, int, int, int, int, int]:
    selected = tuple(row for row in policy_rows if row["policy"] == policy)
    grouped_positions: dict[str, list[dict[str, str]]] = {}
    for position in position_rows:
        grouped_positions.setdefault(position["portfolio_identity"], []).append(position)
    positions = {key: tuple(value) for key, value in grouped_positions.items()}
    key_index = {(row["session_date"], row["requested_budget"], row["policy"]): row for row in policy_rows}
    failures = applied = defined_applied = mechanical = temporal_mechanical = 0
    for row in selected:
        original = float(row["original_gross_weight"]); transformed = float(row["transformed_gross_weight"])
        cash = float(row["transformed_cash_weight"]); multiplier = float(row["exposure_multiplier"])
        current_positions = positions.get(row["identity"], ())
        structural_ok = (
            abs(transformed + cash - 1.0) <= TOLERANCE
            and transformed <= original + TOLERANCE and transformed <= 1.0 + TOLERANCE
            and -TOLERANCE <= multiplier <= 1.0 + TOLERANCE
            and all(abs(float(item["transformed_weight"]) - float(item["original_weight"]) * multiplier) <= TOLERANCE for item in current_positions)
        )
        other_policy = "VOLATILITY_SCALING" if policy == "NO_RISK_POLICY" else "NO_RISK_POLICY"
        other = key_index.get((row["session_date"], row["requested_budget"], other_policy))
        if other is None:
            structural_ok = False
        else:
            other_symbols = {item["symbol"] for item in positions.get(other["identity"], ())}
            if {item["symbol"] for item in current_positions} != other_symbols:
                structural_ok = False
        failures += not structural_ok
        if policy == "VOLATILITY_SCALING" and row["policy_state"] == "APPLIED":
            applied += 1
            before = _float(row, "annualized_volatility_before"); after = _float(row, "annualized_volatility_after")
            if before is not None and after is not None:
                defined_applied += 1
                expected_multiplier = min(1.0, 0.20 / before) if before > 0 else 1.0
                failed = after > before + TOLERANCE or abs(multiplier - expected_multiplier) > TOLERANCE or abs(after - before * multiplier) > TOLERANCE
                mechanical += failed; temporal_mechanical += failed
    return len(selected), failures, applied, defined_applied, mechanical, temporal_mechanical


def _load_decision_input(phase10a: Path, phase10b: Path, phase10c: Path) -> RiskPolicyDecisionInput:
    manifest_a, hash_a = _manifest(phase10a, "portfolio_risk_manifest.json", EXPECTED_PHASE10A_RESULT, EXPECTED_PHASE10A_SPEC)
    manifest_b, hash_b = _manifest(phase10b, "portfolio_risk_policy_manifest.json", EXPECTED_PHASE10B_RESULT, EXPECTED_PHASE10B_SPEC)
    manifest_c, hash_c = _manifest(phase10c, "portfolio_risk_policy_outcome_manifest.json", EXPECTED_PHASE10C_RESULT, EXPECTED_PHASE10C_SPEC)
    fingerprints = {item["name"]: item["fingerprint"] for item in manifest_b.get("policies", ())}
    if fingerprints != dict(EXPECTED_POLICY_FINGERPRINTS) or manifest_c.get("policy_fingerprints") != dict(EXPECTED_POLICY_FINGERPRINTS):
        raise ValueError("frozen policy fingerprint provenance mismatch")
    if next(item for item in manifest_b["policies"] if item["name"] == "VOLATILITY_SCALING").get("target_annualized_volatility") != 0.20:
        raise ValueError("frozen volatility target provenance mismatch")
    if manifest_c.get("path_metric_status") != "NOT_APPLICABLE" or manifest_c.get("path_metric_reason") != "OVERLAPPING_FORWARD_HORIZONS":
        raise ValueError("frozen path-evidence limitation mismatch")

    policy_rows = _csv(phase10b / "portfolio_risk_policy_by_date.csv")
    position_rows = _csv(phase10b / "portfolio_risk_policy_positions.csv")
    summaries = _csv(phase10c / "portfolio_risk_policy_outcome_summary.csv")
    blocks = _csv(phase10c / "portfolio_risk_policy_outcome_by_block.csv")
    contrasts = _csv(phase10c / "portfolio_risk_policy_outcome_contrasts.csv")
    if len({row["identity"] for row in policy_rows}) != len(policy_rows):
        raise ValueError("duplicate Phase 10B portfolio identity")

    facts: list[PolicyDecisionFacts] = []
    for policy in sorted(EXPECTED_POLICY_FINGERPRINTS):
        observation_count, structural_failures, applied, defined_applied, mechanical, temporal_failures = _structural_facts(policy, policy_rows, position_rows)
        all_summaries = tuple(row for row in summaries if row["policy"] == policy and row["subset"] == "ALL")
        summary_keys = {(int(row["requested_budget"]), int(row["horizon_sessions"])) for row in all_summaries}
        expected_keys = {(budget, horizon) for budget in (5, 10, 20) for horizon in (5, 10, 20)}
        evaluable = sum(int(row["evaluable_dates"]) > 0 for row in all_summaries)
        policy_blocks = tuple(row for row in blocks if row["policy"] == policy)
        complete_blocks = sum(
            all(int(row["evaluable_dates"]) > 0 for row in policy_blocks if row["block_name"] == block)
            and sum(row["block_name"] == block for row in policy_blocks) == 9
            for block in ("early_2018_2020", "middle_2021_2022", "middle_2023_2024", "recent_2025_2026")
        )
        deltas = tuple(_float(row, "mean_portfolio_return_delta_pct_points") for row in contrasts) if policy == "VOLATILITY_SCALING" else ()
        defined_deltas = tuple(value for value in deltas if value is not None)
        facts.append(PolicyDecisionFacts(
            policy, fingerprints[policy], observation_count, structural_failures, applied, defined_applied, mechanical,
            summary_keys == expected_keys and len(all_summaries) == 9, evaluable, 9, complete_blocks, 4,
            temporal_failures, len(defined_deltas), sum(value > 0 for value in defined_deltas),
            sum(value == 0 for value in defined_deltas), sum(value < 0 for value in defined_deltas),
            manifest_c["path_metric_status"], manifest_c["path_metric_reason"],
        ))
    return RiskPolicyDecisionInput(
        manifest_a["result_identity"], manifest_a["specification_fingerprint"],
        manifest_b["result_identity"], manifest_b["specification_fingerprint"],
        manifest_c["result_identity"], manifest_c["specification_fingerprint"],
        {"phase10a": hash_a, "phase10b": hash_b, "phase10c": hash_c}, fingerprints, tuple(facts),
    )


def _rows(items: tuple[Any, ...]) -> list[dict[str, Any]]:
    result = []
    for item in items:
        row = {}
        for field in fields(item):
            value = getattr(item, field.name)
            if isinstance(value, Enum): value = value.value
            elif isinstance(value, tuple): value = "|".join(value)
            elif isinstance(value, Mapping): value = json.dumps(dict(value), sort_keys=True, separators=(",", ":"))
            row[field.name] = value
        result.append(row)
    return result


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]), lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def _report(result: RiskPolicyDecisionResult) -> str:
    lines = [
        "# Phase 10D Neutral Risk Policy Decision Gate", "",
        f"Decision gate timing: `{result.decision_gate_timing}`.", "",
        "This principle-based governance gate was formalized after Phase 10C evidence existed. It is not independent prospective confirmation.", "",
        "| Policy | Role | Research-governance state | Reason |", "|---|---|---|---|",
    ]
    for item in result.decisions:
        lines.append(f"| {item.policy} | {item.role} | {item.decision.value} | {item.reason_code} |")
    lines.extend(("", "No policy is ranked, selected, recommended, production-authorized, or added to Forward V1.", "", "## Limitations", ""))
    lines.extend(f"- {item}" for item in result.limitations); lines.append("")
    return "\n".join(lines)


def run_risk_policy_decision(
    *, phase10a_root: str | Path = DEFAULT_PHASE10A_ROOT,
    phase10b_root: str | Path = DEFAULT_PHASE10B_ROOT,
    phase10c_root: str | Path = DEFAULT_PHASE10C_ROOT,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> RiskPolicyDecisionResult:
    roots = tuple(Path(item).resolve() for item in (phase10a_root, phase10b_root, phase10c_root))
    output = Path(output_root).resolve()
    if output.exists(): raise FileExistsError(f"output directory already exists: {output}")
    if output in {PROJECT_ROOT, PROJECT_ROOT / "research_results", *roots}: raise ValueError("output root must be a new dedicated Phase 10D directory")
    source = _load_decision_input(*roots)
    result = evaluate_risk_policy_decision_gate(source)
    summary_rows, evidence_rows = _rows(result.decisions), _rows(result.evidence)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:
        _write_csv(temporary / FILES[0], summary_rows); _write_csv(temporary / FILES[1], evidence_rows)
        (temporary / FILES[3]).write_text(_report(result), encoding="utf-8", newline="\n")
        artifacts = {
            FILES[0]: {"rows": len(summary_rows), "sha256": _file_hash(temporary / FILES[0])},
            FILES[1]: {"rows": len(evidence_rows), "sha256": _file_hash(temporary / FILES[1])},
            FILES[3]: {"rows": 1, "sha256": _file_hash(temporary / FILES[3])},
        }
        manifest = {
            "contract": result.contract_name, "version": result.contract_version,
            "specification_fingerprint": result.specification_fingerprint, "result_identity": result.identity,
            "decision_gate_timing": DECISION_GATE_TIMING,
            "timing_disclosure": "principle-based gate formalized after immutable Phase 10C evidence existed; not independent prospective confirmation",
            "source_identities": dict(result.source_identities), "source_manifest_hashes": dict(result.source_manifest_hashes),
            "policy_fingerprints": dict(EXPECTED_POLICY_FINGERPRINTS),
            "evidence_dimensions": list(NEUTRAL_RISK_POLICY_DECISION_GATE_V1.dimensions),
            "decision_precedence": [
                "provenance mismatch fails closed before evaluation",
                "material structural/mechanical failure => REJECT_FOR_NOW",
                "required evidence unavailable => INSUFFICIENT_EVIDENCE",
                "valid declared purpose plus complete frozen evidence and no contradiction => ELIGIBLE_FOR_FUTURE_FORWARD_PROTOCOL",
                "otherwise => HOLD",
                "NO_RISK_POLICY => CONTROL_BASELINE",
            ],
            "no_post_hoc_numerical_thresholds": True, "no_scoring": True, "no_ranking": True,
            "phase10b_parameters_frozen": True, "phase10c_evidence_immutable": True,
            "forward_v1_protocol": FORWARD_PROTOCOL_V1, "forward_v1_isolation": "unchanged; no mutation, activation, or backfill",
            "path_metric_status": "NOT_APPLICABLE", "path_metric_reason": "OVERLAPPING_FORWARD_HORIZONS",
            "artifacts": artifacts, "limitations": list(result.limitations), "completed": True,
        }
        (temporary / FILES[2]).write_bytes(canonical_json(canonical_identity_value(manifest)) + b"\n")
        temporary.replace(output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True); raise
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase10a-root", type=Path, default=DEFAULT_PHASE10A_ROOT)
    parser.add_argument("--phase10b-root", type=Path, default=DEFAULT_PHASE10B_ROOT)
    parser.add_argument("--phase10c-root", type=Path, default=DEFAULT_PHASE10C_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args(argv)
    result = run_risk_policy_decision(phase10a_root=args.phase10a_root, phase10b_root=args.phase10b_root, phase10c_root=args.phase10c_root, output_root=args.output_root)
    print(f"Phase 10D complete: {len(result.decisions)} governance decisions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
