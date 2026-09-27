from __future__ import annotations

"""Publish compact Phase 8 synthesis from frozen Phase 6 and Phase 7 artifacts."""

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
    NEUTRAL_PORTFOLIO_RESEARCH_SYNTHESIS_V1,
    PortfolioResearchSynthesisResult,
    evaluate_portfolio_research_synthesis,
    load_portfolio_research_synthesis_input,
)
from quantlab.portfolio.construction import BLOCKS, BUDGETS
from quantlab.portfolio.outcome_evaluation import COST_GRID_BPS, HORIZONS, PATH_METRIC_REASON, PATH_METRIC_STATUS
from quantlab.portfolio.research_synthesis import DIMENSIONS, EvidenceStatus, PortfolioResearchDecision, RobustnessFlag


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNNER_CONTRACT = "quantlab.portfolio_research_synthesis_runner"
RUNNER_VERSION = "v1"
DEFAULT_PHASE6_ROOT = PROJECT_ROOT / "research_results/quantlab_portfolio_construction_2018-08-07_2026-09-17"
DEFAULT_PHASE7_ROOT = PROJECT_ROOT / "research_results/quantlab_portfolio_outcome_evaluation_2018-08-07_2026-09-17"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "research_results/quantlab_portfolio_research_synthesis_2018-08-07_2026-09-17"
EXPECTED_PHASE6_MANIFEST_SHA256 = "70677377bb4c6f8feaa4774b519ca72b3b1a05d4fb4760fb9e7ed8d915879cba"
EXPECTED_PHASE6_RESULT_IDENTITY = "c23fa10a1b577509f1fa5d7a23d543dc63237b719c05269342ae5e7a6e660697"
EXPECTED_PHASE7_MANIFEST_SHA256 = "0ed2df1a2253b397b7c37aab05c0d4f8164021aee2231afcd0b2417f3229e5b0"
EXPECTED_PHASE7_RESULT_IDENTITY = "3eee3dfffe992d8c8663736c5e96ca7e95b1c47f2bbd97c3ee2ee2e960e25b65"

FILES = (
    "portfolio_research_decisions.csv",
    "portfolio_research_evidence.csv",
    "portfolio_research_flags.csv",
    "portfolio_research_synthesis_manifest.json",
    "portfolio_research_synthesis_report.md",
)


def _hash(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if value is None:
        return ""
    if isinstance(value, tuple):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return value


def _rows(items: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [
        {field.name: _value(getattr(item, field.name)) for field in fields(item)}
        for item in items
    ]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty Phase 8 artifact: {path.name}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _validate_canonical_provenance(source) -> None:
    expected = {
        "phase6_manifest_sha256": EXPECTED_PHASE6_MANIFEST_SHA256,
        "phase6_result_identity": EXPECTED_PHASE6_RESULT_IDENTITY,
        "phase7_manifest_sha256": EXPECTED_PHASE7_MANIFEST_SHA256,
        "phase7_result_identity": EXPECTED_PHASE7_RESULT_IDENTITY,
    }
    for name, expected_value in expected.items():
        if source.source_identities.get(name) != expected_value:
            raise ValueError(f"canonical Phase 8 provenance mismatch: {name}")


def _report(result: PortfolioResearchSynthesisResult) -> str:
    lines = [
        "# Phase 8 Neutral Portfolio Research Synthesis", "",
        "This gate assesses readiness for prospective research only. It does not rank or select a portfolio.", "",
        "| Budget | Horizon | Decision | Flags |", "|---:|---:|---|---|",
    ]
    for item in result.decisions:
        lines.append(f"| {item.requested_budget} | {item.horizon_sessions} | {item.decision.value} | {', '.join(item.flags) or '—'} |")
    counts = {decision.value: sum(item.decision is decision for item in result.decisions) for decision in PortfolioResearchDecision}
    lines.extend(("", "## Decision counts", ""))
    lines.extend(f"- `{name}`: {count}" for name, count in counts.items())
    lines.extend((
        "", "## Path limitation", "",
        f"- Status: `{PATH_METRIC_STATUS}`", f"- Reason: `{PATH_METRIC_REASON}`",
        "- Overlapping forward outcomes do not define an executable equity path.",
        "", "## Interpretation restrictions", "",
    ))
    lines.extend(f"- {item}" for item in result.limitations)
    lines.append("")
    return "\n".join(lines)


def run_portfolio_research_synthesis(
    *,
    phase6_root: str | Path = DEFAULT_PHASE6_ROOT,
    phase7_root: str | Path = DEFAULT_PHASE7_ROOT,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> dict[str, Any]:
    phase6, phase7, output = Path(phase6_root).resolve(), Path(phase7_root).resolve(), Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"output directory already exists: {output}")
    if output in {PROJECT_ROOT, PROJECT_ROOT / "research_results", phase6, phase7}:
        raise ValueError("output root must be a new dedicated Phase 8 directory")
    source = load_portfolio_research_synthesis_input(phase6, phase7)
    _validate_canonical_provenance(source)
    result = evaluate_portfolio_research_synthesis(source)
    rows = {
        FILES[0]: _rows(result.decisions),
        FILES[1]: _rows(result.evidence),
        FILES[2]: _rows(result.flags),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:
        for name, artifact_rows in rows.items():
            _write_csv(temporary / name, artifact_rows)
        (temporary / FILES[4]).write_text(_report(result), encoding="utf-8", newline="\n")
        inventory = {
            name: {"rows": len(artifact_rows), "sha256": _file_hash(temporary / name)}
            for name, artifact_rows in rows.items()
        }
        inventory[FILES[4]] = {"rows": 1, "sha256": _file_hash(temporary / FILES[4])}
        decision_counts = {
            decision.value: sum(item.decision is decision for item in result.decisions)
            for decision in PortfolioResearchDecision
        }
        manifest = {
            "runner_contract": RUNNER_CONTRACT, "runner_version": RUNNER_VERSION,
            "contract_name": result.contract_name, "contract_version": result.contract_version,
            "completed": True, "specification_fingerprint": result.specification_fingerprint,
            "result_identity": result.identity, "source_identities": dict(result.source_identities),
            "phase6_root": str(phase6), "phase7_root": str(phase7),
            "selection_policy": "ADX_ONLY", "weighting_policy": "EQUAL_WEIGHT",
            "budgets": list(BUDGETS), "horizons": list(HORIZONS),
            "evidence_dimensions": list(DIMENSIONS),
            "evidence_statuses": [item.value for item in EvidenceStatus],
            "decision_states": [item.value for item in PortfolioResearchDecision],
            "robustness_flags": [item.value for item in RobustnessFlag],
            "decision_counts": decision_counts,
            "decision_rules": {
                "adverse": "REJECT_FOR_NOW",
                "insufficient_without_adverse": "INSUFFICIENT_EVIDENCE",
                "recent_or_temporal_contradiction": "HOLD",
                "coherent_supportive_or_neutral": "ADVANCE_TO_FORWARD_VALIDATION",
                "tie_breakers": False, "numeric_score": False,
            },
            "cost_grid_provenance": {"label": "HYPOTHETICAL_COST_SENSITIVITY", "bps": list(COST_GRID_BPS), "optimized": False},
            "temporal_partition": [
                {"name": name, "start_date": start, "end_date": end} for name, start, end in BLOCKS
            ],
            "temporal_partition_identity": _hash(BLOCKS),
            "overlap_status": "OVERLAPPING_FORWARD_OUTCOME_OBSERVATIONS",
            "path_metric_applicability": PATH_METRIC_STATUS, "path_metric_reason": PATH_METRIC_REASON,
            "artifacts": inventory,
            "assertions": {
                "no_optimization": True, "no_ranking": True, "no_winner": True,
                "no_retrospective_threshold_fitting": True, "no_production_authorization": True,
                "no_live_authorization": True, "phase7_authoritative_outcome_input": True,
            },
            "limitations": list(result.limitations),
        }
        (temporary / FILES[3]).write_text(
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8", newline="\n",
        )
        temporary.replace(output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {"result": result, "manifest": manifest, "output_root": output}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase6-root", default=str(DEFAULT_PHASE6_ROOT))
    parser.add_argument("--phase7-root", default=str(DEFAULT_PHASE7_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    arguments = parser.parse_args(argv)
    published = run_portfolio_research_synthesis(
        phase6_root=arguments.phase6_root, phase7_root=arguments.phase7_root,
        output_root=arguments.output_root,
    )
    print(json.dumps({
        "output_root": str(published["output_root"]),
        "result_identity": published["result"].identity,
        "decision_counts": published["manifest"]["decision_counts"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
