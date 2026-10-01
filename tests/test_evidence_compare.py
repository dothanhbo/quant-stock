from __future__ import annotations

import csv
from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

import quantctl.evidence_compare as compare
from quantctl.evidence_compare import (
    ComparisonFamily,
    ComparisonState,
    EvidenceComparisonCatalog,
    compare_frozen_policies,
    compare_neutral_factors,
    inspect_evidence_comparison_catalog,
)
from quantctl.factor_explore import (
    EvidenceState,
    FactorExploreCatalog,
    FactorPopulationEvidence,
    FactorSummaryEvidence,
    IncrementalEvidence,
    RedundancyEvidence,
    TemporalBlockEvidence,
    ExplorePopulation,
)


_OUTCOME = "stock_forward_return_pct"


def _csv(path: Path, rows: tuple[dict[str, object], ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _block(factor: str, name: str, ic: float, spread: float) -> tuple[str, int, str, TemporalBlockEvidence]:
    return (
        factor,
        5,
        _OUTCOME,
        TemporalBlockEvidence(
            name,
            "2021-01-01",
            "2022-12-31",
            40,
            80.0,
            ic,
            40,
            80.0,
            spread,
            0.60,
            "POSITIVE" if ic > 0 and spread > 0 else "MIXED",
            None,
            f"{factor}-{name}",
        ),
    )


def _factor_catalog() -> EvidenceComparisonCatalog:
    summaries = (
        FactorSummaryEvidence("adx_14", 5, _OUTCOME, 50, 40, 80.0, 0.12, 0.65, 1.5, "adx-summary"),
        FactorSummaryEvidence("rsi_14", 5, _OUTCOME, 50, 40, 80.0, 0.08, 0.55, 1.0, "rsi-summary"),
    )
    blocks = (
        _block("adx_14", "early_2018_2020", 0.10, 1.2),
        _block("rsi_14", "early_2018_2020", 0.07, 0.9),
        _block("adx_14", "middle_2021_2022", 0.14, 1.8),
        _block("rsi_14", "middle_2021_2022", 0.09, 1.1),
    )
    population = FactorPopulationEvidence(
        population=ExplorePopulation.NEUTRAL_PIT,
        label="Neutral PIT factors",
        state=EvidenceState.AVAILABLE,
        factors=("adx_14", "rsi_14"),
        horizons=(5,),
        outcomes=(_OUTCOME,),
        summaries=summaries,
        blocks=blocks,
        temporal_support=(("adx_14", 5, _OUTCOME, True), ("rsi_14", 5, _OUTCOME, False)),
        incremental=(
            ("adx_14", 5, _OUTCOME, IncrementalEvidence("adx_given_rsi", "rsi_14", 75.0, 0.09, -0.03, True, "inc-adx")),
            ("rsi_14", 5, _OUTCOME, IncrementalEvidence("rsi_given_adx", "adx_14", 75.0, 0.03, -0.05, False, "inc-rsi")),
        ),
        redundancy=(
            ("adx_14", RedundancyEvidence("rsi_14", 0.42, 90.0, "same-date cross-sectional correlation", "red-id")),
            ("rsi_14", RedundancyEvidence("adx_14", 0.42, 90.0, "same-date cross-sectional correlation", "red-id")),
        ),
        gate_dispositions=(("adx_14", "ADVANCE"), ("rsi_14", "HOLD")),
        daily_path=Path(),
        artifact_paths=(Path("factor_summary.csv"),),
        artifact_identities=("neutral-result",),
        as_of="2026-09-17",
        universe="point-in-time database coverage; not historical VN100 membership",
        limitations=("descriptive only",),
        detail="Complete neutral evidence.",
    )
    unavailable_policies = compare.FrozenPolicyCatalog(
        ComparisonState.UNAVAILABLE,
        (),
        (),
        (),
        (),
        (),
        (),
        (),
        (),
        "Policy evidence unavailable.",
    )
    return EvidenceComparisonCatalog(FactorExploreCatalog((population,)), unavailable_policies)


def _policy_artifacts(root: Path) -> None:
    target = root / "research_results" / "quantlab_neutral_panel_factor_evaluation_2018-08-07_2026-09-17"
    target.mkdir(parents=True)
    (target / "experiment_manifest.json").write_text(
        json.dumps(
            {
                "composite_comparison": {
                    "completed": True,
                    "result_identity": "composite-result",
                    "limitations": ["descriptive only"],
                },
                "policy_selection_diagnostics": {
                    "completed": True,
                    "result_identity": "selection-result",
                    "limitations": {"cost_model": False},
                },
            }
        ),
        encoding="utf-8",
    )
    policies = (
        ("ADX_ONLY", 0.10, 0.60, 1.0),
        ("RSI_ONLY", 0.07, 0.55, 0.8),
        ("ADX_RSI_EQUAL_WEIGHT", 0.13, 0.64, 1.3),
        ("ADX_RSI_VOLUME_EQUAL_WEIGHT", 0.14, 0.66, 1.4),
    )
    _csv(
        target / "composite_policy_summary.csv",
        tuple(
            {
                "policy_name": name,
                "horizon_sessions": 5,
                "outcome_field": _OUTCOME,
                "mean_daily_rank_ic": ic,
                "positive_spread_rate": rate,
                "ic_coverage_pct": 80,
                "mean_daily_mean_spread": spread,
                "all_blocks_positive_ic": "true",
                "all_blocks_positive_spread": "true",
                "identity": f"{name}-summary",
            }
            for name, ic, rate, spread in policies
        ),
    )
    _csv(
        target / "composite_policy_by_block.csv",
        tuple(
            {
                "policy_name": name,
                "horizon_sessions": 5,
                "outcome_field": _OUTCOME,
                "block_name": "early_2018_2020",
                "mean_daily_rank_ic": ic,
                "positive_spread_rate": rate,
                "ic_coverage_pct": 80,
                "mean_daily_mean_spread": spread,
                "identity": f"{name}-block",
            }
            for name, ic, rate, spread in policies
        ),
    )
    contrasts = (
        ("ADX_RSI_EQUAL_WEIGHT", "ADX_ONLY", 0.03, 0.3),
        ("ADX_RSI_EQUAL_WEIGHT", "RSI_ONLY", 0.06, 0.5),
        ("ADX_RSI_VOLUME_EQUAL_WEIGHT", "ADX_RSI_EQUAL_WEIGHT", 0.01, 0.1),
    )
    for filename, include_block in (
        ("composite_contrast_summary.csv", False),
        ("composite_contrast_by_block.csv", True),
    ):
        _csv(
            target / filename,
            tuple(
                {
                    "variant_policy": variant,
                    "reference_policy": reference,
                    "horizon_sessions": 5,
                    "outcome_field": _OUTCOME,
                    **({"block_name": "early_2018_2020"} if include_block else {}),
                    "mean_daily_ic_delta": ic,
                    "mean_daily_spread_delta": spread,
                    "identity": f"{variant}-{reference}-{'block' if include_block else 'summary'}",
                }
                for variant, reference, ic, spread in contrasts
            ),
        )
    _csv(
        target / "policy_selection_overlap_summary.csv",
        ({
            "selection_budget": 10,
            "scope_name": "whole_period",
            "mean_overlap_coefficient": 0.75,
            "mean_jaccard_similarity": 0.62,
            "exact_selected_set_equality_rate": 0.20,
            "identity": "overlap-id",
        },),
    )
    _csv(
        target / "policy_selection_turnover_summary.csv",
        tuple(
            {
                "policy_name": name,
                "selection_budget": 10,
                "scope_name": "whole_period",
                "mean_one_way_turnover": turnover,
                "total_entry_count": entries,
                "identity": f"{name}-turnover",
            }
            for name, turnover, entries in (
                ("ADX_ONLY", 0.20, 40),
                ("ADX_RSI_EQUAL_WEIGHT", 0.25, 45),
            )
        ),
    )
    gate = root / "research_results" / "quantlab_research_decision_gate_2018-08-07_2026-09-17"
    _csv(
        gate / "research_decision_summary.csv",
        tuple(
            {"candidate_type": "policy", "candidate": name, "decision": decision}
            for name, decision in (
                ("ADX_ONLY", "ADVANCE"),
                ("RSI_ONLY", "HOLD"),
                ("ADX_RSI_EQUAL_WEIGHT", "HOLD"),
                ("ADX_RSI_VOLUME_EQUAL_WEIGHT", "REJECT_FOR_NOW"),
            )
        ),
    )


def test_neutral_pair_is_context_matched_and_projects_paired_evidence() -> None:
    result = compare_neutral_factors(
        _factor_catalog(),
        factor_a="adx_14",
        factor_b="rsi_14",
        horizon_a=5,
        horizon_b=5,
        outcome_a=_OUTCOME,
        outcome_b=_OUTCOME,
    )

    assert result.state is ComparisonState.AVAILABLE
    assert result.evidence_population == "complete point-in-time neutral factor population"
    assert result.evidence_a is not None and result.evidence_a.disposition == "ADVANCE"
    assert result.evidence_b is not None and result.evidence_b.disposition == "HOLD"
    assert result.mean_ic_delta_a_minus_b == pytest.approx(0.04)
    assert len(result.blocks) == 2
    assert result.artifact_identities == ("neutral-result",)


def test_neutral_incremental_redundancy_and_unsupported_dimensions_are_explicit() -> None:
    result = compare_neutral_factors(
        _factor_catalog(),
        factor_a="adx_14",
        factor_b="rsi_14",
        horizon_a=5,
        horizon_b=5,
        outcome_a=_OUTCOME,
        outcome_b=_OUTCOME,
    )

    assert len(result.incremental_notes) == 2
    assert result.redundancy_correlation == pytest.approx(0.42)
    dimensions = {item.name: item for item in result.dimensions}
    assert dimensions["Cost sensitivity"].candidate_a == "UNAVAILABLE"
    assert dimensions["Portfolio consequence"].candidate_b == "UNAVAILABLE"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        ({"factor_a": "adx_14", "factor_b": "adx_14", "horizon_a": 5, "horizon_b": 5, "outcome_a": _OUTCOME, "outcome_b": _OUTCOME}, "distinct"),
        ({"factor_a": "adx_14", "factor_b": "rsi_14", "horizon_a": 5, "horizon_b": 10, "outcome_a": _OUTCOME, "outcome_b": _OUTCOME}, "Horizons"),
        ({"factor_a": "adx_14", "factor_b": "rsi_14", "horizon_a": 5, "horizon_b": 5, "outcome_a": _OUTCOME, "outcome_b": "excess_forward_return_pct_points"}, "Outcomes"),
    ),
)
def test_neutral_incompatible_pair_is_rejected(kwargs: dict[str, object], message: str) -> None:
    result = compare_neutral_factors(_factor_catalog(), **kwargs)  # type: ignore[arg-type]
    assert result.state is ComparisonState.INCOMPATIBLE
    assert message in result.detail
    assert result.evidence_a is None and result.evidence_b is None


@pytest.mark.parametrize(
    ("policy_a", "policy_b", "expected_delta"),
    (
        ("ADX_RSI_EQUAL_WEIGHT", "ADX_ONLY", 0.03),
        ("ADX_RSI_EQUAL_WEIGHT", "RSI_ONLY", 0.06),
        ("ADX_RSI_VOLUME_EQUAL_WEIGHT", "ADX_RSI_EQUAL_WEIGHT", 0.01),
    ),
)
def test_only_canonical_frozen_policy_contrasts_are_loaded(
    tmp_path: Path,
    policy_a: str,
    policy_b: str,
    expected_delta: float,
) -> None:
    _policy_artifacts(tmp_path)
    result = compare_frozen_policies(
        inspect_evidence_comparison_catalog(root=tmp_path),
        policy_a=policy_a,
        policy_b=policy_b,
        horizon_a=5,
        horizon_b=5,
        outcome_a=_OUTCOME,
        outcome_b=_OUTCOME,
    )

    assert result.state is ComparisonState.AVAILABLE
    assert result.mean_ic_delta_a_minus_b == pytest.approx(expected_delta)
    assert result.evidence_a is not None and result.evidence_b is not None
    assert result.artifact_identities == ("composite-result", "selection-result")


def test_overlap_and_turnover_are_projected_only_for_the_persisted_pair(tmp_path: Path) -> None:
    _policy_artifacts(tmp_path)
    catalog = inspect_evidence_comparison_catalog(root=tmp_path)
    paired = compare_frozen_policies(
        catalog,
        policy_a="ADX_RSI_EQUAL_WEIGHT",
        policy_b="ADX_ONLY",
        horizon_a=5,
        horizon_b=5,
        outcome_a=_OUTCOME,
        outcome_b=_OUTCOME,
    )
    volume = compare_frozen_policies(
        catalog,
        policy_a="ADX_RSI_VOLUME_EQUAL_WEIGHT",
        policy_b="ADX_RSI_EQUAL_WEIGHT",
        horizon_a=5,
        horizon_b=5,
        outcome_a=_OUTCOME,
        outcome_b=_OUTCOME,
    )

    assert paired.overlap[0].mean_jaccard_similarity == pytest.approx(0.62)
    assert tuple(item.policy for item in paired.turnover) == ("ADX_ONLY", "ADX_RSI_EQUAL_WEIGHT")
    assert volume.overlap == () and volume.turnover == ()


def test_arbitrary_policy_pair_and_context_mismatch_are_incompatible(tmp_path: Path) -> None:
    _policy_artifacts(tmp_path)
    catalog = inspect_evidence_comparison_catalog(root=tmp_path)
    arbitrary = compare_frozen_policies(
        catalog,
        policy_a="ADX_ONLY",
        policy_b="RSI_ONLY",
        horizon_a=5,
        horizon_b=5,
        outcome_a=_OUTCOME,
        outcome_b=_OUTCOME,
    )
    mismatch = compare_frozen_policies(
        catalog,
        policy_a="ADX_RSI_EQUAL_WEIGHT",
        policy_b="ADX_ONLY",
        horizon_a=5,
        horizon_b=10,
        outcome_a=_OUTCOME,
        outcome_b=_OUTCOME,
    )

    assert arbitrary.state is ComparisonState.INCOMPATIBLE
    assert "predeclared canonical contrast" in arbitrary.detail
    assert mismatch.state is ComparisonState.INCOMPATIBLE
    assert "Horizons" in mismatch.detail


def test_missing_or_incompatible_artifacts_are_distinct_from_bad_pair(tmp_path: Path) -> None:
    catalog = inspect_evidence_comparison_catalog(root=tmp_path)
    missing = compare_frozen_policies(
        catalog,
        policy_a="ADX_RSI_EQUAL_WEIGHT",
        policy_b="ADX_ONLY",
        horizon_a=5,
        horizon_b=5,
        outcome_a=_OUTCOME,
        outcome_b=_OUTCOME,
    )
    bad_pair = compare_frozen_policies(
        catalog,
        policy_a="ADX_ONLY",
        policy_b="RSI_ONLY",
        horizon_a=5,
        horizon_b=5,
        outcome_a=_OUTCOME,
        outcome_b=_OUTCOME,
    )

    assert missing.state is ComparisonState.UNAVAILABLE
    assert "unavailable" in missing.detail.lower()
    assert bad_pair.state is ComparisonState.INCOMPATIBLE


def test_policy_schema_mismatch_is_reported_as_unavailable(tmp_path: Path) -> None:
    _policy_artifacts(tmp_path)
    target = (
        tmp_path
        / "research_results"
        / "quantlab_neutral_panel_factor_evaluation_2018-08-07_2026-09-17"
        / "composite_policy_summary.csv"
    )
    _csv(target, ({"wrong": "schema"},))

    catalog = inspect_evidence_comparison_catalog(root=tmp_path)

    assert catalog.policies.state is ComparisonState.UNAVAILABLE
    assert "missing columns" in catalog.policies.detail


def test_results_are_immutable_and_source_has_no_research_execution_or_preference_semantics() -> None:
    result = compare_neutral_factors(
        _factor_catalog(),
        factor_a="adx_14",
        factor_b="rsi_14",
        horizon_a=5,
        horizon_b=5,
        outcome_a=_OUTCOME,
        outcome_b=_OUTCOME,
    )
    with pytest.raises(FrozenInstanceError):
        result.detail = "changed"  # type: ignore[misc]

    root = Path(__file__).resolve().parent.parent
    source = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in ("quantctl/evidence_compare.py", "manager/pages/compare.py")
    ).lower()
    forbidden = (
        "run_backtest",
        "run_walk_forward",
        "run_quantlab",
        "marketdatasnapshot",
        "vnstock",
        "requests.",
        "winner",
        "overall_score",
    )
    assert all(item not in source for item in forbidden)
