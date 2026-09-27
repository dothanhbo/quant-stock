from __future__ import annotations

"""Publish Phase 5.11 decisions from existing canonical Phase 5.9B evidence."""

import argparse
import csv
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Iterable, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quantlab.evaluation import (
    ResearchDecisionGateResult,
    evaluate_research_decision_gate,
    load_phase59b_decision_evidence,
)
from quantlab.identity import canonical_json
from quantlab.identity import canonical_identity_value


RUNNER_CONTRACT = "quantlab.research_decision_gate_runner"
RUNNER_VERSION = "v1"
DEFAULT_INPUT_ROOT = Path(
    "research_results/quantlab_neutral_panel_factor_evaluation_2018-08-07_2026-09-17"
)
DEFAULT_OUTPUT_ROOT = Path(
    "research_results/quantlab_research_decision_gate_2018-08-07_2026-09-17"
)

SUMMARY_COLUMNS = (
    "candidate_type", "candidate", "decision", "supportive_dimensions",
    "adverse_dimensions", "mixed_dimensions", "insufficient_dimensions",
    "neutral_dimensions", "reason_codes", "candidate_identity", "identity",
)
EVIDENCE_COLUMNS = (
    "candidate_type", "candidate", "dimension", "status", "defined",
    "decision_relevant", "source_artifact", "source_identity",
    "observation_summary", "reason_code", "rationale", "identity",
)


def _write_csv(path: Path, columns: tuple[str, ...], rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _joined(values: tuple[str, ...]) -> str:
    return " | ".join(values)


def _summary_rows(result: ResearchDecisionGateResult) -> list[dict[str, Any]]:
    return [
        {
            "candidate_type": item.candidate_type,
            "candidate": item.candidate,
            "decision": item.decision.value,
            "supportive_dimensions": _joined(item.supportive_dimensions),
            "adverse_dimensions": _joined(item.adverse_dimensions),
            "mixed_dimensions": _joined(item.mixed_dimensions),
            "insufficient_dimensions": _joined(item.insufficient_dimensions),
            "neutral_dimensions": _joined(item.neutral_dimensions),
            "reason_codes": _joined(item.reason_codes),
            "candidate_identity": item.candidate_identity,
            "identity": item.identity,
        }
        for item in result.decisions
    ]


def _evidence_rows(result: ResearchDecisionGateResult) -> list[dict[str, Any]]:
    return [
        {
            "candidate_type": item.candidate_type,
            "candidate": item.candidate,
            "dimension": item.dimension,
            "status": item.status.value,
            "defined": str(item.defined).lower(),
            "decision_relevant": str(item.decision_relevant).lower(),
            "source_artifact": item.source_artifact,
            "source_identity": item.source_identity,
            "observation_summary": canonical_json(canonical_identity_value(item.observation_summary)).decode("utf-8"),
            "reason_code": item.reason_code,
            "rationale": item.rationale,
            "identity": item.identity,
        }
        for item in result.evidence
    ]


def _report(result: ResearchDecisionGateResult) -> str:
    lines = [
        "# Phase 5.11 Neutral Research Decision Gate",
        "",
        "This report summarizes frozen Phase 5.x evidence. ADVANCE authorizes only later",
        "portfolio-construction research; it does not mean profitable, production-ready,",
        "recommended, or superior.",
        "",
        "| Candidate type | Candidate | Decision | Supportive | Adverse | Mixed | Insufficient |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in result.decisions:
        lines.append(
            f"| {item.candidate_type} | {item.candidate} | {item.decision.value} | "
            f"{_joined(item.supportive_dimensions) or '—'} | "
            f"{_joined(item.adverse_dimensions) or '—'} | "
            f"{_joined(item.mixed_dimensions) or '—'} | "
            f"{_joined(item.insufficient_dimensions) or '—'} |"
        )
    lines.extend(("", "## Interpretation limitations", ""))
    lines.extend(f"- {item}" for item in result.limitations)
    lines.extend((
        "",
        "No portfolio return, PnL, backtest, transaction-cost optimization, policy ranking,",
        "or numeric aggregate score was used.",
        "",
    ))
    return "\n".join(lines)


def run_research_decision_gate(
    *, input_root: str | Path = DEFAULT_INPUT_ROOT,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    overwrite: bool = False,
) -> ResearchDecisionGateResult:
    input_path = Path(input_root).expanduser().resolve()
    output_path = Path(output_root).expanduser().resolve()
    if input_path == output_path or input_path in output_path.parents:
        raise ValueError("decision output must not overwrite or nest inside the canonical input root")
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"output directory already exists: {output_path}")

    source = load_phase59b_decision_evidence(input_path)
    result = evaluate_research_decision_gate(source)
    summary_rows = _summary_rows(result)
    evidence_rows = _evidence_rows(result)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    temporary = Path(tempfile.mkdtemp(prefix=f".{output_path.name}.", dir=output_path.parent))
    try:
        _write_csv(temporary / "research_decision_summary.csv", SUMMARY_COLUMNS, summary_rows)
        _write_csv(temporary / "research_decision_evidence.csv", EVIDENCE_COLUMNS, evidence_rows)
        (temporary / "research_decision_report.md").write_text(_report(result), encoding="utf-8")
        manifest = {
            "runner_contract": RUNNER_CONTRACT,
            "runner_version": RUNNER_VERSION,
            "decision_contract": result.contract_name,
            "decision_contract_version": result.contract_version,
            "decision_semantics_version": result.specification_fingerprint,
            "decision_result_identity": result.identity,
            "input_canonical_root": str(input_path),
            "input_canonical_manifest_identity": result.source_manifest_identity,
            "input_runner_contract": result.source_runner_contract,
            "input_runner_version": result.source_runner_version,
            "input_date_range": {"start_date": result.start_date, "end_date": result.end_date},
            "source_artifacts_used": list(source.tables),
            "source_result_identities": dict(result.source_result_identities),
            "candidate_identities": {
                f"{item.candidate_type}:{item.candidate}": item.candidate_identity
                for item in result.decisions
            },
            "candidate_count": len(result.decisions),
            "evidence_record_count": len(result.evidence),
            "artifacts": {
                "research_decision_summary.csv": len(summary_rows),
                "research_decision_evidence.csv": len(evidence_rows),
                "research_decision_manifest.json": 1,
                "research_decision_report.md": 1,
            },
            "no_portfolio_backtest_pnl_evidence_used": True,
            "no_numeric_overall_score_or_candidate_ranking": True,
            "limitations": list(result.limitations),
            "completed": True,
        }
        (temporary / "research_decision_manifest.json").write_bytes(canonical_json(manifest) + b"\n")
        if output_path.exists():
            shutil.rmtree(output_path)
        temporary.replace(output_path)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = run_research_decision_gate(
        input_root=args.input_root,
        output_root=args.output_root,
        overwrite=args.overwrite,
    )
    print(f"Phase 5.11 complete: {len(result.decisions)} candidates, {len(result.evidence)} evidence records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
