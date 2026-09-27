from __future__ import annotations

"""Publish neutral Phase 7 evidence from frozen Phase 6/6.5 artifacts."""

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
    COST_GRID_BPS,
    HORIZONS,
    NEUTRAL_PORTFOLIO_OUTCOMES_V1,
    PortfolioOutcomeEvaluationResult,
    evaluate_portfolio_outcomes,
    load_portfolio_outcome_input,
)
from quantlab.portfolio.construction import BLOCKS, BUDGETS
from quantlab.portfolio.outcome_evaluation import (
    COST_LABEL,
    PATH_METRIC_REASON,
    PATH_METRIC_STATUS,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNNER_CONTRACT = "quantlab.portfolio_outcome_evaluation_runner"
RUNNER_VERSION = "v1"
DEFAULT_PHASE6_ROOT = PROJECT_ROOT / "research_results/quantlab_portfolio_construction_2018-08-07_2026-09-17"
DEFAULT_PHASE65_ROOT = PROJECT_ROOT / "research_results/quantlab_point_in_time_outcome_evidence_2018-08-07_2026-09-17"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "research_results/quantlab_portfolio_outcome_evaluation_2018-08-07_2026-09-17"
EXPECTED_PHASE65_MANIFEST_SHA256 = "46bc2f5ff3c99ea14b9798f0c3a116a258274a9ae50fcd95ac164b01845e0e86"
EXPECTED_OUTCOME_ARTIFACT_SHA256 = "2bea6502a27cf7caccfbaa174c7be0b708bc7a89e175c26fef8f18293e174712"
EXPECTED_OBSERVATION_IDENTITY = "c44180b130e12895ce79cdd4a93334860f969a3248444c5d6f7daec16b27fe9b"
EXPECTED_OBSERVATION_CONTENT = "53eaa0a254a0578ef91fd2d4b6e5c990fed4857227b07797fd13d4e2b56c223f"
EXPECTED_OUTCOME_IDENTITY = "d7c1654402b64a6a5a1701b36972ea46eeb004d8531a99cf4cdafdf669ab9c0a"
EXPECTED_OUTCOME_CONTENT = "4a4690e14eccf7e026b3c7f47ab734794c38d3aad5ba3958c36f80a9a8a4011f"
EXPECTED_OUTCOME_ROWS = "181189"

FILES = (
    "portfolio_outcome_summary.csv",
    "portfolio_outcome_by_date.csv",
    "portfolio_outcome_by_block.csv",
    "portfolio_outcome_contrasts.csv",
    "portfolio_cost_sensitivity.csv",
    "portfolio_outcome_manifest.json",
    "portfolio_outcome_report.md",
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
    return value


def _rows(items: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [
        {item.name: _value(getattr(record, item.name)) for item in fields(record)}
        for record in items
    ]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty artifact: {path.name}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _report(result: PortfolioOutcomeEvaluationResult) -> str:
    lines = [
        "# Phase 7 Neutral Portfolio Outcome Evaluation", "",
        "These are overlapping forward-outcome observations, not an executable return path.",
        "No scenario, budget, horizon, or cost rate is ranked or selected.", "",
        "| Budget | Horizon | Evaluable | Mean stock % | Mean benchmark % | Mean excess pp | Evidence |",
        "|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in result.summaries:
        def shown(value: float | None) -> str:
            return "—" if value is None else f"{value:.6f}"
        lines.append(
            f"| {item.requested_budget} | {item.horizon_sessions} | {item.evaluable_dates} | "
            f"{shown(item.mean_stock_return_pct)} | {shown(item.mean_benchmark_return_pct)} | "
            f"{shown(item.mean_excess_return_pct_points)} | {item.evidence_state.value} |"
        )
    lines.extend(("", "## Path metrics", "", f"- Status: `{PATH_METRIC_STATUS}`", f"- Reason: `{PATH_METRIC_REASON}`", "", "## Limitations", ""))
    lines.extend(f"- {item}" for item in result.limitations)
    lines.append("")
    return "\n".join(lines)


def _validate_provenance(source) -> None:
    expected = {
        "phase65_manifest_sha256": EXPECTED_PHASE65_MANIFEST_SHA256,
        "phase65_outcome_artifact_sha256": EXPECTED_OUTCOME_ARTIFACT_SHA256,
        "phase65_outcome_row_count": EXPECTED_OUTCOME_ROWS,
        "observation_identity": EXPECTED_OBSERVATION_IDENTITY,
        "observation_content_identity": EXPECTED_OBSERVATION_CONTENT,
        "outcome_panel_identity": EXPECTED_OUTCOME_IDENTITY,
        "outcome_content_identity": EXPECTED_OUTCOME_CONTENT,
    }
    for name, value in expected.items():
        if source.source_identities.get(name) != value:
            raise ValueError(f"canonical Phase 7 provenance mismatch: {name}")


def run_portfolio_outcome_evaluation(
    *,
    phase6_root: str | Path = DEFAULT_PHASE6_ROOT,
    phase65_root: str | Path = DEFAULT_PHASE65_ROOT,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> dict[str, Any]:
    phase6, phase65 = Path(phase6_root).resolve(), Path(phase65_root).resolve()
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"output directory already exists: {output}")
    if output in {PROJECT_ROOT, PROJECT_ROOT / "research_results", phase6, phase65}:
        raise ValueError("output root must be a new dedicated Phase 7 directory")
    source = load_portfolio_outcome_input(phase6, phase65)
    _validate_provenance(source)
    frozen_membership = tuple(
        (item.session_date, item.requested_budget, tuple(position.symbol for position in item.positions))
        for item in source.portfolios
    )
    frozen_weights = tuple(
        (item.session_date, item.requested_budget, tuple(position.weight for position in item.positions))
        for item in source.portfolios
    )
    result = evaluate_portfolio_outcomes(source)
    if frozen_membership != tuple(
        (item.session_date, item.requested_budget, tuple(position.symbol for position in item.positions))
        for item in source.portfolios
    ) or frozen_weights != tuple(
        (item.session_date, item.requested_budget, tuple(position.weight for position in item.positions))
        for item in source.portfolios
    ):
        raise ValueError("Phase 7 mutated frozen Phase 6 membership or weights")
    rows = {
        FILES[0]: _rows(result.summaries),
        FILES[1]: _rows(result.daily_outcomes),
        FILES[2]: _rows(result.block_summaries),
        FILES[3]: _rows(result.contrasts),
        FILES[4]: _rows(result.cost_sensitivity),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:
        for name, artifact_rows in rows.items():
            _write_csv(temporary / name, artifact_rows)
        (temporary / FILES[6]).write_text(_report(result), encoding="utf-8", newline="\n")
        inventory = {
            name: {"rows": len(artifact_rows), "sha256": _file_hash(temporary / name)}
            for name, artifact_rows in rows.items()
        }
        inventory[FILES[6]] = {"rows": 1, "sha256": _file_hash(temporary / FILES[6])}
        manifest = {
            "runner_contract": RUNNER_CONTRACT,
            "runner_version": RUNNER_VERSION,
            "contract_name": result.contract_name,
            "contract_version": result.contract_version,
            "completed": True,
            "specification_fingerprint": result.specification_fingerprint,
            "result_identity": result.identity,
            "source_identities": dict(result.source_identities),
            "phase6_root": str(phase6),
            "phase65_root": str(phase65),
            "budgets": list(BUDGETS),
            "horizons": list(HORIZONS),
            "selection_policy": "ADX_ONLY",
            "weighting_policy": "EQUAL_WEIGHT",
            "causal_alignment": "formation session_date plus symbol to frozen outcome observation",
            "join_key": ["session_date", "symbol"],
            "missingness_policy": "all frozen constituents required; no weight renormalization; empty portfolio undefined",
            "benchmark_semantics": "same-date VNINDEX forward return times frozen gross weight; cash has no invented return",
            "aggregation_formula": "sum(frozen_weight_i * frozen_constituent_forward_return_i)",
            "overlap_status": "OVERLAPPING_FORWARD_OUTCOME_OBSERVATIONS",
            "path_metric_applicability": PATH_METRIC_STATUS,
            "path_metric_reason": PATH_METRIC_REASON,
            "turnover_definition": "frozen Phase 6 one_way_weight_turnover",
            "cost_sensitivity_grid_bps": list(COST_GRID_BPS),
            "cost_sensitivity_label": COST_LABEL,
            "cost_semantics": "formation turnover times bps; descriptive percentage-point deduction only",
            "temporal_blocks": [
                {"name": name, "start_date": start, "end_date": end}
                for name, start, end in BLOCKS
            ],
            "temporal_partition_identity": _hash(BLOCKS),
            "artifacts": inventory,
            "assertions": {
                "construction_frozen_before_outcomes": True,
                "scenario_optimization": False,
                "budget_optimization": False,
                "horizon_optimization": False,
                "cost_optimization": False,
                "ranking_or_winner": False,
                "production_authorization": False,
            },
            "limitations": list(result.limitations),
        }
        (temporary / FILES[5]).write_text(
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
    parser.add_argument("--phase65-root", default=str(DEFAULT_PHASE65_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    args = parser.parse_args(argv)
    published = run_portfolio_outcome_evaluation(
        phase6_root=args.phase6_root,
        phase65_root=args.phase65_root,
        output_root=args.output_root,
    )
    print(json.dumps({
        "output_root": str(published["output_root"]),
        "result_identity": published["result"].identity,
        "summary_count": len(published["result"].summaries),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
