from __future__ import annotations

"""Publish neutral Phase 6 portfolio-structure evidence from frozen selections."""

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

from quantlab.identity import canonical_json, canonical_identity_value
from quantlab.portfolio import construct_portfolios, load_phase6_construction_input


RUNNER_CONTRACT = "quantlab.portfolio_construction_runner"
RUNNER_VERSION = "v1"
DEFAULT_PHASE59_ROOT = Path("research_results/quantlab_neutral_panel_factor_evaluation_2018-08-07_2026-09-17")
DEFAULT_PHASE511_ROOT = Path("research_results/quantlab_research_decision_gate_2018-08-07_2026-09-17")
DEFAULT_OUTPUT_ROOT = Path("research_results/quantlab_portfolio_construction_2018-08-07_2026-09-17")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty artifact: {path.name}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _summary_rows(result) -> list[dict[str, Any]]:
    return [{key: _value(value) for key, value in {
        "candidate_source": item.candidate_source, "requested_budget": item.requested_budget,
        "weighting_policy": item.weighting_policy, "scope_name": item.scope_name,
        "scope_start_date": item.scope_start_date, "scope_end_date": item.scope_end_date,
        "evaluated_dates": item.evaluated_dates, "underfilled_date_count": item.underfilled_date_count,
        "empty_date_count": item.empty_date_count, "mean_selected_count": item.mean_selected_count,
        "median_selected_count": item.median_selected_count, "mean_fill_ratio": item.mean_fill_ratio,
        "median_fill_ratio": item.median_fill_ratio, "mean_effective_n": item.mean_effective_n,
        "median_effective_n": item.median_effective_n,
        "mean_max_single_name_weight": item.mean_max_single_name_weight,
        "median_max_single_name_weight": item.median_max_single_name_weight,
        "mean_herfindahl": item.mean_herfindahl, "median_herfindahl": item.median_herfindahl,
        "defined_weight_turnover_dates": item.defined_weight_turnover_dates,
        "mean_one_way_weight_turnover": item.mean_one_way_weight_turnover,
        "median_one_way_weight_turnover": item.median_one_way_weight_turnover,
        "total_additions": item.total_additions, "total_removals": item.total_removals,
        "mean_weight_stability": item.mean_weight_stability,
        "structural_state": item.structural_state, "daily_identity_count": item.daily_identity_count,
        "daily_identities_sha256": item.daily_identities_sha256, "identity": item.identity,
    }.items()} for item in result.summaries]


def _daily_rows(result) -> list[dict[str, Any]]:
    return [{
        "session_date": item.session_date, "candidate_source": item.candidate_source,
        "requested_budget": item.requested_budget, "weighting_policy": item.weighting_policy.value,
        "selection_identity": item.selection_identity,
        "eligible_cross_section_count": item.eligible_cross_section_count,
        "selected_count": item.selected_count, "fill_ratio": item.fill_ratio,
        "unfilled_slots": item.unfilled_slots, "gross_weight": item.gross_weight,
        "cash_weight": item.cash_weight, "max_single_name_weight": item.max_single_name_weight,
        "herfindahl_concentration": item.herfindahl_concentration,
        "effective_number_of_positions": item.effective_number_of_positions,
        "additions_json": canonical_json(item.additions).decode("utf-8"),
        "removals_json": canonical_json(item.removals).decode("utf-8"),
        "retained_positions_json": canonical_json(item.retained_positions).decode("utf-8"),
        "one_way_weight_turnover": item.one_way_weight_turnover,
        "weight_stability": item.weight_stability, "identity": item.identity,
    } for item in result.daily_portfolios]


def _position_rows(result) -> list[dict[str, Any]]:
    return [{
        "session_date": daily.session_date, "candidate_source": daily.candidate_source,
        "requested_budget": daily.requested_budget, "weighting_policy": daily.weighting_policy.value,
        "symbol": item.symbol, "selection_rank": item.selection_rank,
        "weight": item.weight, "selection_identity": item.selection_identity,
        "position_identity": item.identity, "portfolio_identity": daily.identity,
    } for daily in result.daily_portfolios for item in daily.positions]


def _contrast_rows(result) -> list[dict[str, Any]]:
    return [{
        "candidate_source": item.candidate_source, "weighting_policy": item.weighting_policy.value,
        "scope_name": item.scope_name, "lower_budget": item.lower_budget,
        "higher_budget": item.higher_budget,
        "mean_selected_count_delta": item.mean_selected_count_delta,
        "mean_fill_ratio_delta": item.mean_fill_ratio_delta,
        "mean_effective_n_delta": item.mean_effective_n_delta,
        "mean_max_single_name_weight_delta": item.mean_max_single_name_weight_delta,
        "mean_herfindahl_delta": item.mean_herfindahl_delta,
        "mean_weight_turnover_delta": item.mean_weight_turnover_delta,
        "identity": item.identity,
    } for item in result.contrasts]


def _report(result) -> str:
    whole = tuple(item for item in result.summaries if item.scope_name == "whole_period")
    lines = [
        "# Phase 6 Neutral Portfolio Construction Research", "",
        "Portfolio structure is evaluated without future outcomes, PnL, backtests, costs,",
        "or scenario selection. Structural validity is not economic superiority.", "",
        "| Candidate source | Budget | Weighting | Dates | State | Mean fill | Mean effective N | Mean turnover |",
        "|---|---:|---|---:|---|---:|---:|---:|",
    ]
    for item in whole:
        lines.append(
            f"| {item.candidate_source} | {item.requested_budget} | {item.weighting_policy.value} | "
            f"{item.evaluated_dates} | {item.structural_state.value} | {item.mean_fill_ratio:.6f} | "
            f"{item.mean_effective_n:.6f} | {item.mean_one_way_weight_turnover:.6f} |"
        )
    lines.extend(("", "## Limitations", ""))
    lines.extend(f"- {item}" for item in result.limitations)
    lines.append("")
    return "\n".join(lines)


def run_portfolio_construction(
    *, phase59_root: str | Path = DEFAULT_PHASE59_ROOT,
    phase511_root: str | Path = DEFAULT_PHASE511_ROOT,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    overwrite: bool = False,
):
    p59, p511, output = Path(phase59_root).resolve(), Path(phase511_root).resolve(), Path(output_root).resolve()
    if output in (p59, p511) or p59 in output.parents or p511 in output.parents:
        raise ValueError("Phase 6 output must remain separate from upstream canonical roots")
    if output.exists() and not overwrite:
        raise FileExistsError(f"output directory already exists: {output}")
    source = load_phase6_construction_input(p59, p511)
    result = construct_portfolios(source)
    rows = {
        "portfolio_construction_summary.csv": _summary_rows(result),
        "portfolio_construction_by_date.csv": _daily_rows(result),
        "portfolio_positions.csv": _position_rows(result),
        "portfolio_structural_contrasts.csv": _contrast_rows(result),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        for name, content in rows.items():
            _write_csv(temporary / name, content)
        (temporary / "portfolio_construction_report.md").write_text(_report(result), encoding="utf-8")
        manifest = {
            "runner_contract": RUNNER_CONTRACT, "runner_version": RUNNER_VERSION,
            "construction_contract": result.contract_name,
            "construction_contract_version": result.contract_version,
            "specification_fingerprint": result.specification_fingerprint,
            "result_identity": result.identity,
            "source_identities": dict(result.source_identities),
            "candidate_source": "ADX_ONLY", "advanced_factor": "adx_14",
            "budgets": [5, 10, 20], "weighting_policies": ["EQUAL_WEIGHT"],
            "period": {"start_date": source.start_date, "end_date": source.end_date},
            "turnover_definitions": {
                "selection_one_way_turnover": "Phase 5.9 entries divided by previous selected count; not recomputed or relabeled",
                "one_way_weight_turnover": "0.5 times L1 change across security weights plus cash weight",
                "first_date": "undefined",
            },
            "structural_metrics": {
                "fill_ratio": "selected_count divided by requested_budget",
                "herfindahl": "sum of squared security weights",
                "effective_n": "one divided by Herfindahl when nonempty",
                "cash_weight": "one minus gross security weight",
                "weight_stability": "one minus one_way_weight_turnover",
            },
            "artifacts": {
                **{name: len(content) for name, content in rows.items()},
                "portfolio_construction_manifest.json": 1,
                "portfolio_construction_report.md": 1,
            },
            "limitations": list(result.limitations),
            "no_future_return_pnl_backtest_evidence_used_for_construction_or_scenario_selection": True,
            "no_scenario_ranking_or_winner": True,
            "completed": True,
        }
        (temporary / "portfolio_construction_manifest.json").write_bytes(canonical_json(canonical_identity_value(manifest)) + b"\n")
        if output.exists():
            shutil.rmtree(output)
        temporary.replace(output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase59-root", type=Path, default=DEFAULT_PHASE59_ROOT)
    parser.add_argument("--phase511-root", type=Path, default=DEFAULT_PHASE511_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    result = run_portfolio_construction(
        phase59_root=args.phase59_root, phase511_root=args.phase511_root,
        output_root=args.output_root, overwrite=args.overwrite,
    )
    print(f"Phase 6 complete: {len(result.daily_portfolios)} daily portfolios, {len(result.summaries)} summaries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
