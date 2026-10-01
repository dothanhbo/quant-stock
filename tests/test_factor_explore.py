from __future__ import annotations

import csv
from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

import quantctl.factor_explore as explore
from quantctl.factor_explore import (
    EvidenceState,
    ExplorePopulation,
    inspect_factor_explore_catalog,
    load_daily_factor_evidence,
    select_factor_evidence,
)


def _csv(path: Path, rows: tuple[dict[str, object], ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _neutral(root: Path, *, missing_ic: bool = False) -> Path:
    target = root / "research_results" / "quantlab_neutral_panel_factor_evaluation_2018-08-07_2026-09-17"
    target.mkdir(parents=True)
    (target / "experiment_manifest.json").write_text(
        json.dumps(
            {
                "completed": True,
                "evaluation": {"result_identity": "neutral-result"},
                "effective_bounds": {"outcome_data_through_session": "2026-09-17"},
                "limitations": ["database coverage is not historical VN100"],
            }
        ),
        encoding="utf-8",
    )
    _csv(
        target / "factor_summary.csv",
        (
            {
                "factor": "adx_14",
                "horizon_sessions": "5",
                "outcome_field": "stock_forward_return_pct",
                "total_signal_dates": "40",
                "ic_defined_date_count": "30",
                "ic_coverage_pct": "75",
                "mean_daily_rank_ic": "" if missing_ic else "0.12",
                "positive_mean_spread_rate": "0.6",
                "mean_daily_high_minus_low_mean_spread": "1.5",
                "identity": "summary-id",
            },
            {
                "factor": "adx_14",
                "horizon_sessions": "10",
                "outcome_field": "stock_forward_return_pct",
                "total_signal_dates": "40",
                "ic_defined_date_count": "29",
                "ic_coverage_pct": "72.5",
                "mean_daily_rank_ic": "0.08",
                "positive_mean_spread_rate": "0.55",
                "mean_daily_high_minus_low_mean_spread": "1.1",
                "identity": "summary-id-10",
            },
        ),
    )
    blocks = []
    for index, (name, start, end, ic, spread) in enumerate(
        (
            ("early_2018_2020", "2018-08-07", "2020-12-31", "0.1", "1.0"),
            ("middle_2021_2022", "2021-01-01", "2022-12-31", "-0.1", "0.5"),
            ("middle_2023_2024", "2023-01-01", "2024-12-31", "", ""),
            ("recent_2025_2026", "2025-01-01", "2026-09-17", "0.2", "2.0"),
        )
    ):
        blocks.append(
            {
                "factor": "adx_14",
                "horizon_sessions": "5",
                "outcome_field": "stock_forward_return_pct",
                "block_name": name,
                "block_start_date": start,
                "block_end_date": end,
                "total_source_signal_dates": "10",
                "ic_defined_date_count": "8" if ic else "0",
                "ic_coverage_pct": "80" if ic else "",
                "mean_daily_rank_ic": ic,
                "spread_defined_date_count": "8" if spread else "0",
                "spread_coverage_pct": "80" if spread else "",
                "mean_daily_mean_spread": spread,
                "positive_spread_rate": "0.625" if spread else "",
                "ic_undefined_reason": "" if ic else "no defined IC",
                "spread_undefined_reason": "" if spread else "no defined spread",
                "identity": f"block-{index}",
            }
        )
    _csv(target / "temporal_stability_by_block.csv", tuple(blocks))
    _csv(
        target / "temporal_stability_summary.csv",
        ({
            "factor": "adx_14", "horizon_sessions": "5",
            "outcome_field": "stock_forward_return_pct",
            "descriptive_temporal_support": "false",
        },),
    )
    _csv(
        target / "factor_incremental_summary.csv",
        ({
            "hypothesis_name": "adx_given_rsi", "target_factor": "adx_14",
            "control_factors": '["rsi_14"]', "horizon_sessions": "5",
            "outcome_field": "stock_forward_return_pct",
            "partial_rank_ic_coverage_pct": "70", "mean_daily_partial_rank_ic": "0.09",
            "mean_partial_minus_raw_rank_ic": "-0.03",
            "all_blocks_positive_partial_rank_ic": "true", "identity": "incremental-id",
        },),
    )
    _csv(
        target / "factor_redundancy_summary.csv",
        ({
            "first_factor": "adx_14", "second_factor": "rsi_14",
            "correlation_coverage_pct": "90", "mean_daily_correlation": "0.2",
            "identity": "redundancy-id",
        },),
    )
    daily = target / "factor_by_date.csv"
    _csv(
        daily,
        ({
            "factor": "adx_14", "factor_direction": "UNSPECIFIED",
            "horizon_sessions": "5", "outcome_field": "stock_forward_return_pct",
            "signal_date": "2024-01-02", "rank_ic": "0.3",
            "high_minus_low_mean_spread": "2.2", "identity": "daily-id",
        },),
    )
    gate = root / "research_results" / "quantlab_research_decision_gate_2018-08-07_2026-09-17"
    _csv(
        gate / "research_decision_summary.csv",
        ({"candidate_type": "factor", "candidate": "adx_14", "decision": "ADVANCE"},),
    )
    return daily


def _candidate(root: Path) -> Path:
    outcome = root / "research_results" / "quantlab_factor_outcome_evaluation_2018_2026"
    temporal = root / "research_results" / "quantlab_factor_temporal_stability_2018_2026"
    diagnostic = root / "research_results" / "quantlab_candidate_factor_diagnostics_2018_2026"
    for target in (outcome, temporal, diagnostic):
        target.mkdir(parents=True)
    (outcome / "experiment_manifest.json").write_text(
        json.dumps({
            "completion_status": "complete", "evaluation_result_identity": "candidate-result",
            "effective_coverage_end_date": "2026-09-17", "limitations": ["accepted candidates only"],
        }), encoding="utf-8",
    )
    (temporal / "experiment_manifest.json").write_text(
        json.dumps({"completed": True, "phase_4_7a_temporal_result_identity": "temporal-result"}),
        encoding="utf-8",
    )
    (diagnostic / "experiment_manifest.json").write_text(
        json.dumps({"diagnostics_result_identity": "diagnostic-result"}), encoding="utf-8",
    )
    _csv(
        outcome / "factor_outcome_summary.csv",
        ({
            "factor": "adx", "horizon_sessions": "5",
            "outcome_field": "excess_forward_return_pct_points", "total_signal_dates": "20",
            "ic_defined_date_count": "5", "ic_coverage_pct": "25",
            "mean_daily_rank_ic": "-0.1", "positive_spread_date_rate": "0.4",
            "mean_daily_high_minus_low_mean_spread": "-0.5", "identity": "candidate-summary",
        },),
    )
    _csv(
        temporal / "temporal_stability_by_block.csv",
        ({
            "factor": "adx", "horizon_sessions": "5",
            "outcome_field": "excess_forward_return_pct_points", "block_name": "early_2018_2020",
            "block_start_date": "2018-08-07", "block_end_date": "2020-12-31",
            "total_signal_date_rows": "10", "ic_defined_date_count": "2",
            "bucket_defined_date_count": "2", "mean_daily_rank_ic": "-0.2",
            "mean_daily_high_minus_low_mean_spread": "-1", "positive_mean_spread_rate": "0.25",
            "undefined_reason": "", "identity": "candidate-block",
        },),
    )
    _csv(
        temporal / "temporal_stability_summary.csv",
        ({
            "factor": "adx", "horizon_sessions": "5",
            "outcome_field": "excess_forward_return_pct_points",
            "descriptive_temporal_support": "false",
        },),
    )
    _csv(
        diagnostic / "factor_associations_aggregate.csv",
        ({
            "first_factor": "adx", "second_factor": "score", "pairwise_finite_count": "50",
            "spearman_correlation": "0.15", "undefined_reason": "",
            "scope": "pooled_descriptive_only_not_cross_sectional_ranking",
        },),
    )
    daily = outcome / "factor_outcome_by_date.csv"
    _csv(
        daily,
        ({
            "signal_date": "2024-01-02", "factor": "adx", "horizon_sessions": "5",
            "outcome_field": "excess_forward_return_pct_points", "rank_ic": "-0.3",
            "high_minus_low_mean_spread": "-1.5", "identity": "candidate-daily",
        },),
    )
    return daily


def test_neutral_selection_exposes_summary_blocks_incremental_redundancy_and_gate(tmp_path: Path) -> None:
    _neutral(tmp_path)
    catalog = inspect_factor_explore_catalog(root=tmp_path)

    selected = select_factor_evidence(
        catalog, population=ExplorePopulation.NEUTRAL_PIT, factor="adx_14",
        horizon_sessions=5, outcome_field="stock_forward_return_pct",
    )

    assert selected.state is EvidenceState.AVAILABLE
    assert selected.summary is not None and selected.summary.mean_daily_rank_ic == .12
    assert tuple(item.direction for item in selected.blocks) == ("POSITIVE", "MIXED", "UNKNOWN", "POSITIVE")
    assert selected.blocks[2].mean_daily_rank_ic is None
    assert selected.incremental[0].mean_partial_rank_ic == .09
    assert selected.redundancy[0].other_factor == "rsi_14"
    assert selected.gate_disposition == "ADVANCE"


def test_missing_values_remain_none_and_block_scope_is_explicit(tmp_path: Path) -> None:
    _neutral(tmp_path, missing_ic=True)
    selected = select_factor_evidence(
        inspect_factor_explore_catalog(root=tmp_path),
        population="neutral_pit", factor="adx_14", horizon_sessions=5,
        outcome_field="stock_forward_return_pct", temporal_scope="middle_2023_2024",
    )

    assert selected.summary is not None and selected.summary.mean_daily_rank_ic is None
    assert selected.selected_block is not None
    assert selected.selected_block.ic_coverage_pct is None
    assert selected.selected_block.high_minus_low_spread is None


@pytest.mark.parametrize(
    ("factor", "horizon", "outcome", "scope", "message"),
    (
        ("unsupported", 5, "stock_forward_return_pct", "whole_period", "Factor is unsupported"),
        ("adx_14", 99, "stock_forward_return_pct", "whole_period", "Horizon 99 is unsupported"),
        ("adx_14", 5, "unsupported", "whole_period", "Outcome is unsupported"),
        ("adx_14", 5, "stock_forward_return_pct", "missing_block", "Temporal block missing_block is unavailable"),
    ),
)
def test_unsupported_selection_is_blocked(
    tmp_path: Path, factor: str, horizon: int, outcome: str, scope: str, message: str,
) -> None:
    _neutral(tmp_path)
    selected = select_factor_evidence(
        inspect_factor_explore_catalog(root=tmp_path), population="neutral_pit",
        factor=factor, horizon_sessions=horizon, outcome_field=outcome, temporal_scope=scope,
    )
    assert selected.state is EvidenceState.UNAVAILABLE
    assert message in selected.detail


def test_candidate_population_stays_selection_conditioned_and_does_not_invent_gate_or_incremental(
    tmp_path: Path,
) -> None:
    _candidate(tmp_path)
    selected = select_factor_evidence(
        inspect_factor_explore_catalog(root=tmp_path), population="frozen_q70_accepted",
        factor="adx", horizon_sessions=5, outcome_field="excess_forward_return_pct_points",
    )

    assert selected.state is EvidenceState.AVAILABLE
    assert selected.population_label == "Frozen-Q70 accepted candidates"
    assert selected.gate_disposition is None
    assert selected.incremental == ()
    assert selected.redundancy[0].scope == "pooled_descriptive_only_not_cross_sectional_ranking"
    assert "accepted candidates only" in selected.limitations


def test_daily_evidence_is_lazy_and_filtered_after_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily_path = _neutral(tmp_path)
    calls: list[Path] = []
    original = explore._read_csv_file

    def tracked(path: Path) -> tuple[dict[str, str], ...]:
        calls.append(path)
        return original(path)

    monkeypatch.setattr(explore, "_read_csv_file", tracked)
    catalog = inspect_factor_explore_catalog(root=tmp_path)
    assert daily_path not in calls

    daily = load_daily_factor_evidence(
        catalog, population="neutral_pit", factor="adx_14", horizon_sessions=5,
        outcome_field="stock_forward_return_pct",
    )

    assert daily_path in calls
    assert [(item.signal_date, item.rank_ic, item.high_minus_low_spread) for item in daily] == [
        ("2024-01-02", .3, 2.2)
    ]


def test_incompatible_schema_is_unavailable_without_fallback(tmp_path: Path) -> None:
    _neutral(tmp_path)
    _csv(
        tmp_path / "research_results" / "quantlab_neutral_panel_factor_evaluation_2018-08-07_2026-09-17" / "factor_summary.csv",
        ({"wrong": "schema"},),
    )

    population = inspect_factor_explore_catalog(root=tmp_path).population("neutral_pit")

    assert population is not None and population.state is EvidenceState.UNAVAILABLE
    assert "missing columns" in population.detail


def test_catalog_and_selected_results_are_immutable(tmp_path: Path) -> None:
    _neutral(tmp_path)
    catalog = inspect_factor_explore_catalog(root=tmp_path)
    selected = select_factor_evidence(
        catalog, population="neutral_pit", factor="adx_14", horizon_sessions=5,
        outcome_field="stock_forward_return_pct",
    )
    with pytest.raises(FrozenInstanceError):
        selected.factor = "changed"  # type: ignore[misc]


def test_explore_source_has_no_research_execution_or_price_access() -> None:
    root = Path(__file__).resolve().parent.parent
    sources = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in ("quantctl/factor_explore.py", "manager/pages/explore.py")
    )
    forbidden = (
        "run_backtest", "run_walk_forward", "prepare_historical", "build_point_in_time",
        "run_quantlab", "sqlite3", "MarketDataSnapshot", "vnstock", "requests.",
    )
    assert all(item not in sources for item in forbidden)
    assert "rglob(" not in sources


def test_explore_display_preserves_rate_and_undefined_semantics() -> None:
    from manager.pages.explore import _rate, _yes_no_unknown

    assert _rate(.6) == "60.00%"
    assert _rate(None) == "UNKNOWN"
    assert _yes_no_unknown(None) == "UNKNOWN"
