from __future__ import annotations

"""Publish outcome-free Phase 10B portfolio risk-policy counterfactuals."""

import argparse
import csv
from dataclasses import fields
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quantlab.identity import canonical_identity_value, canonical_json
from quantlab.portfolio import (
    NEUTRAL_PORTFOLIO_RISK_POLICY_V1,
    PortfolioRiskComponentContribution,
    PortfolioRiskObservation,
    RiskEvidenceState,
    RiskPolicyResult,
    evaluate_portfolio_risk_policies,
)
from research.run_quantlab_portfolio_risk import _load_phase6


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PHASE6_ROOT = PROJECT_ROOT / "research_results/quantlab_portfolio_construction_2018-08-07_2026-09-17"
DEFAULT_PHASE10A_ROOT = PROJECT_ROOT / "research_results/quantlab_portfolio_risk_2018-08-07_2026-09-17"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "research_results/quantlab_portfolio_risk_policy_2018-08-07_2026-09-17"
EXPECTED_PHASE6_RESULT = "c23fa10a1b577509f1fa5d7a23d543dc63237b719c05269342ae5e7a6e660697"
EXPECTED_PHASE10A_RESULT = "8a9aa6e11af005bb422d0065783af8beee22a94d20f0c35359825e8d9996eaee"
EXPECTED_PHASE10A_SPEC = "cea5b328d89a196dc11081c52e9b37378b017cdbfbb5ca3f2c494dd109882a25"
FILES = (
    "portfolio_risk_policy_summary.csv", "portfolio_risk_policy_by_date.csv",
    "portfolio_risk_policy_positions.csv", "portfolio_risk_policy_by_block.csv",
    "portfolio_risk_policy_manifest.json", "portfolio_risk_policy_report.md",
)


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024): digest.update(chunk)
    return digest.hexdigest()


def _rows(items: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [{field.name: (getattr(item, field.name).value if isinstance(getattr(item, field.name), Enum) else "" if getattr(item, field.name) is None else getattr(item, field.name)) for field in fields(item)} for item in items]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows: raise ValueError(f"cannot write empty artifact: {path.name}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]), lineterminator="\n"); writer.writeheader(); writer.writerows(rows)


_INTEGER_FIELDS = {
    "requested_budget", "selected_count", "trailing_session_count", "covariance_observation_count",
    "pairwise_valid_count", "pairwise_unavailable_count", "benchmark_observation_count", "component_identity_count",
}
_STRING_FIELDS = {
    "session_date", "weighting_policy", "sector_evidence_state", "source_portfolio_identity",
    "component_identities_sha256", "identity",
}
_OPTIONAL_STRING_FIELDS = {"covariance_undefined_reason", "benchmark_undefined_reason"}


def _load_phase10a(root: Path) -> tuple[tuple[PortfolioRiskObservation, ...], tuple[PortfolioRiskComponentContribution, ...], dict[str, Any], str]:
    manifest_path = root / "portfolio_risk_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("completed") or manifest.get("result_identity") != EXPECTED_PHASE10A_RESULT or manifest.get("specification_fingerprint") != EXPECTED_PHASE10A_SPEC:
        raise ValueError("canonical Phase 10A provenance mismatch")
    observations: list[PortfolioRiskObservation] = []
    with (root / "portfolio_risk_by_date.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            values: list[Any] = []
            for field in fields(PortfolioRiskObservation):
                value = row[field.name]
                if field.name == "covariance_evidence_state": values.append(RiskEvidenceState(value))
                elif field.name in _INTEGER_FIELDS: values.append(int(value))
                elif field.name in _STRING_FIELDS: values.append(value)
                elif field.name in _OPTIONAL_STRING_FIELDS: values.append(value or None)
                else: values.append(None if value == "" else float(value))
            observations.append(PortfolioRiskObservation(*values))
    components: list[PortfolioRiskComponentContribution] = []
    with (root / "portfolio_risk_component_contributions.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            components.append(PortfolioRiskComponentContribution(
                row["session_date"], int(row["requested_budget"]), row["symbol"], float(row["weight"]),
                float(row["marginal_variance_contribution"]), float(row["component_variance_contribution"]),
                float(row["component_variance_contribution_share"]), row["identity"],
            ))
    if len(observations) != int(manifest["artifacts"]["portfolio_risk_by_date.csv"]["rows"]):
        raise ValueError("Phase 10A observation count does not match manifest")
    if len(components) != int(manifest["artifacts"]["portfolio_risk_component_contributions.csv"]["rows"]):
        raise ValueError("Phase 10A component count does not match manifest")
    return tuple(observations), tuple(components), manifest, _file_hash(manifest_path)


def _position_rows(result: RiskPolicyResult) -> list[dict[str, Any]]:
    return [
        {"session_date": item.session_date, "requested_budget": item.requested_budget, "policy": item.policy.value,
         "policy_fingerprint": item.policy_fingerprint, "policy_state": item.policy_state.value,
         "symbol": position.symbol, "original_weight": position.original_weight,
         "transformed_weight": position.transformed_weight, "source_position_identity": position.source_position_identity,
         "position_identity": position.identity, "portfolio_identity": item.identity}
        for item in result.portfolios for position in item.positions
    ]


def _portfolio_rows(result: RiskPolicyResult) -> list[dict[str, Any]]:
    return [
        {
            field.name: (
                getattr(item, field.name).value
                if isinstance(getattr(item, field.name), Enum)
                else "" if getattr(item, field.name) is None
                else getattr(item, field.name)
            )
            for field in fields(item)
            if field.name != "positions"
        }
        for item in result.portfolios
    ]


def _report(result: RiskPolicyResult) -> str:
    lines = ["# Phase 10B Neutral Portfolio Risk Policy Research", "",
             "Counterfactual structural research only. No outcome evidence, optimization, ranking, or policy selection.", "",
             "Risk-contribution cap: `RISK_CONTRIBUTION_POLICY_DEFERRED`.",
             "Sector policy: unavailable because point-in-time sector evidence is unavailable.", "",
             "| Policy | Budget | Applied | Mean gross before | Mean gross after | Mean volatility before | Mean volatility after |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    shown = lambda value: "—" if value is None else f"{value:.8f}"
    for item in result.summaries:
        if item.scope_name == "whole_period":
            lines.append(f"| {item.policy.value} | {item.requested_budget} | {item.applied_count} | {shown(item.mean_original_gross_weight)} | {shown(item.mean_transformed_gross_weight)} | {shown(item.mean_annualized_volatility_before)} | {shown(item.mean_annualized_volatility_after)} |")
    lines.extend(("", "## Limitations", "")); lines.extend(f"- {item}" for item in result.limitations); lines.append("")
    return "\n".join(lines)


def run_portfolio_risk_policy(
    *, phase6_root: str | Path = DEFAULT_PHASE6_ROOT,
    phase10a_root: str | Path = DEFAULT_PHASE10A_ROOT,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> RiskPolicyResult:
    phase6, phase10a, output = Path(phase6_root).resolve(), Path(phase10a_root).resolve(), Path(output_root).resolve()
    if output.exists(): raise FileExistsError(f"output directory already exists: {output}")
    if output in {PROJECT_ROOT, PROJECT_ROOT / "research_results", phase6, phase10a}: raise ValueError("output root must be a new dedicated Phase 10B directory")
    portfolios, phase6_sources, phase6_manifest = _load_phase6(phase6)
    if phase6_manifest.get("result_identity") != EXPECTED_PHASE6_RESULT: raise ValueError("canonical Phase 6 provenance mismatch")
    observations, components, phase10a_manifest, phase10a_manifest_hash = _load_phase10a(phase10a)
    sources = {
        **phase6_sources, "phase10a_manifest_sha256": phase10a_manifest_hash,
        "phase10a_result_identity": phase10a_manifest["result_identity"],
        "phase10a_specification_fingerprint": phase10a_manifest["specification_fingerprint"],
    }
    frozen = tuple((item.session_date, item.requested_budget, tuple((p.symbol, p.weight) for p in item.positions)) for item in portfolios)
    result = evaluate_portfolio_risk_policies(portfolios, observations, components, source_identities=sources)
    if frozen != tuple((item.session_date, item.requested_budget, tuple((p.symbol, p.weight) for p in item.positions)) for item in portfolios):
        raise ValueError("Phase 10B mutated Phase 6 portfolios")
    whole = tuple(item for item in result.summaries if item.scope_name == "whole_period")
    blocks = tuple(item for item in result.summaries if item.scope_name != "whole_period")
    rows = {FILES[0]: _rows(whole), FILES[1]: _portfolio_rows(result), FILES[2]: _position_rows(result), FILES[3]: _rows(blocks)}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:
        for name, values in rows.items(): _write_csv(temporary / name, values)
        (temporary / FILES[5]).write_text(_report(result), encoding="utf-8", newline="\n")
        artifacts = {name: {"rows": len(values), "sha256": _file_hash(temporary / name)} for name, values in rows.items()}
        artifacts[FILES[5]] = {"rows": 1, "sha256": _file_hash(temporary / FILES[5])}
        manifest = {
            "contract": result.contract_name, "version": result.contract_version,
            "specification_fingerprint": result.specification_fingerprint, "result_identity": result.identity,
            "source_identities": dict(result.source_identities),
            "phase10a_risk_specification": phase10a_manifest["risk_specification"],
            "policies": [{"name": item.policy.value, "target_annualized_volatility": item.target_annualized_volatility, "fingerprint": item.fingerprint} for item in result.policies],
            "deferred_policies": list(result.deferred_policies), "budgets": [5, 10, 20],
            "sector_evidence_state": "SECTOR_EVIDENCE_UNAVAILABLE", "artifacts": artifacts,
            "limitations": list(result.limitations), "outcome_firewall": True,
            "no_policy_ranking_or_optimization": True, "completed": True,
        }
        (temporary / FILES[4]).write_bytes(canonical_json(canonical_identity_value(manifest)) + b"\n")
        temporary.replace(output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True); raise
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase6-root", type=Path, default=DEFAULT_PHASE6_ROOT)
    parser.add_argument("--phase10a-root", type=Path, default=DEFAULT_PHASE10A_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args(argv)
    result = run_portfolio_risk_policy(phase6_root=args.phase6_root, phase10a_root=args.phase10a_root, output_root=args.output_root)
    print(f"Phase 10B complete: {len(result.portfolios)} counterfactual portfolios")
    return 0


if __name__ == "__main__": raise SystemExit(main())
