from __future__ import annotations

import csv
from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from quantctl.portfolio_risk import (
    PortfolioEvidenceState,
    inspect_portfolio_risk_catalog,
    select_portfolio_risk_evidence,
)


def _csv(path: Path, rows: tuple[dict[str, object], ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _manifest(path: Path, identity: str, limitations: tuple[str, ...] = ()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "completed": True,
                "result_identity": identity,
                "period": {"start_date": "2018-08-07", "end_date": "2026-09-17"},
                "limitations": list(limitations),
            }
        ),
        encoding="utf-8",
    )


def _artifacts(root: Path) -> None:
    results = root / "research_results"
    phase6 = results / "quantlab_portfolio_construction_2018-08-07_2026-09-17"
    phase7 = results / "quantlab_portfolio_outcome_evaluation_2018-08-07_2026-09-17"
    phase10a = results / "quantlab_portfolio_risk_2018-08-07_2026-09-17"
    phase10b = results / "quantlab_portfolio_risk_policy_2018-08-07_2026-09-17"
    phase10c = results / "quantlab_portfolio_risk_policy_outcome_2018-08-07_2026-09-17"
    phase11a = results / "quantlab_execution_foundation"
    phase11b = results / "quantlab_execution_timing_capacity_2018-08-07_2026-09-17"
    phase11e = results / "quantlab_execution_friction_sensitivity_2018-08-07_2026-09-17"
    price = results / "quantlab_execution_price_provenance_2018_2026"
    provider = results / "quantlab_execution_provider_provenance_2018_2026"
    _manifest(phase6 / "portfolio_construction_manifest.json", "phase6", ("database coverage is not historical VN100",))
    _manifest(phase7 / "portfolio_outcome_manifest.json", "phase7", ("overlapping forward outcomes",))
    _manifest(phase10a / "portfolio_risk_manifest.json", "phase10a", ("sector evidence unavailable",))
    _manifest(phase10b / "portfolio_risk_policy_manifest.json", "phase10b")
    _manifest(phase10c / "portfolio_risk_policy_outcome_manifest.json", "phase10c")
    _manifest(phase11a / "execution_contract_manifest.json", "phase11a")
    _manifest(phase11b / "execution_timing_capacity_manifest.json", "phase11b", ("normalized participation is not monetary capacity",))
    _manifest(phase11e / "execution_friction_sensitivity_manifest.json", "phase11e", ("hypothetical cost is not historical cost",))
    _manifest(price / "execution_price_provenance_manifest.json", "price-provenance")
    _manifest(provider / "execution_provider_provenance_manifest.json", "provider-provenance")

    _csv(
        phase6 / "portfolio_construction_summary.csv",
        tuple(
            {
                "candidate_source": "ADX_ONLY",
                "requested_budget": budget,
                "scope_name": "whole_period",
                "mean_selected_count": budget - 0.1,
                "mean_fill_ratio": 0.98,
                "mean_effective_n": float(budget),
                "mean_max_single_name_weight": 1 / budget,
                "mean_herfindahl": 1 / budget,
                "mean_one_way_weight_turnover": 0.08 + budget / 1000,
                "identity": f"structure-{budget}",
            }
            for budget in (5, 10, 20)
        ),
    )
    _csv(
        phase10a / "portfolio_risk_summary.csv",
        tuple(
            {
                "requested_budget": budget,
                "scope_name": "whole_period",
                "mean_annualized_portfolio_volatility": 0.24 - budget / 1000,
                "mean_pairwise_correlation": 0.30 - budget / 1000,
                "mean_benchmark_beta": 0.80 - budget / 1000,
                "mean_max_single_name_weight": 1 / budget,
                "mean_effective_n": float(budget),
                "mean_maximum_component_risk_share": 0.40 - budget / 1000,
                "mean_effective_risk_contributors": budget * 0.8,
                "identity": f"risk-{budget}",
            }
            for budget in (5, 10, 20)
        ),
    )
    _csv(
        phase10b / "portfolio_risk_policy_summary.csv",
        tuple(
            {
                "policy": policy,
                "requested_budget": budget,
                "scope_name": "whole_period",
                "mean_transformation_turnover": 0.0 if policy == "NO_RISK_POLICY" else 0.07,
                "mean_annualized_volatility_after": 0.23 - budget / 1000 if policy == "NO_RISK_POLICY" else 0.20,
                "mean_max_position_weight": 1 / budget if policy == "NO_RISK_POLICY" else 0.8 / budget,
                "mean_effective_n": float(budget),
                "mean_maximum_component_risk_share_after": 0.40 - budget / 1000,
                "mean_effective_risk_contributors_after": budget * 0.8,
                "identity": f"{policy}-{budget}-risk",
            }
            for policy in ("NO_RISK_POLICY", "VOLATILITY_SCALING")
            for budget in (5, 10, 20)
        ),
    )
    _csv(
        phase10c / "portfolio_risk_policy_outcome_summary.csv",
        tuple(
            {
                "policy": policy,
                "requested_budget": budget,
                "horizon_sessions": horizon,
                "subset": "ALL",
                "coverage_pct": 96.0,
                "mean_portfolio_return_pct": 0.5,
                "mean_excess_return_pct_points": 0.3 if policy == "NO_RISK_POLICY" else 0.25,
                "positive_excess_rate": 0.54,
                "identity": f"{policy}-{budget}-{horizon}-outcome",
            }
            for policy in ("NO_RISK_POLICY", "VOLATILITY_SCALING")
            for budget in (5, 10, 20)
            for horizon in (5, 10, 20)
        ),
    )
    _csv(
        phase10c / "portfolio_risk_policy_cost_sensitivity.csv",
        tuple(
            {
                "policy": "VOLATILITY_SCALING",
                "requested_budget": budget,
                "horizon_sessions": horizon,
                "cost_rate_bps": rate,
                "mean_gross_excess_return_pct_points": 0.25,
                "mean_net_excess_return_pct_points": 0.25 - rate / 1000,
                "mean_hypothetical_cost_pct_points": rate / 1000,
                "identity": f"scaled-cost-{budget}-{horizon}-{rate}",
            }
            for budget in (5, 10, 20)
            for horizon in (5, 10, 20)
            for rate in (0, 25)
        ),
    )
    _csv(
        phase11e / "execution_friction_sensitivity_summary.csv",
        tuple(
            {
                "scope_name": "whole_period",
                "requested_budget": budget,
                "horizon_sessions": horizon,
                "hypothetical_all_in_cost_rate_bps": rate,
                "mean_gross_excess_return_pct_points": 0.3,
                "mean_net_excess_return_pct_points": 0.3 - rate / 1000,
                "mean_cost_deduction_pct_points": rate / 1000,
                "label": "HYPOTHETICAL_FRICTION_SENSITIVITY_NOT_HISTORICAL_COST_ESTIMATE",
                "identity": f"friction-{budget}-{horizon}-{rate}",
            }
            for budget in (5, 10, 20)
            for horizon in (5, 10, 20)
            for rate in (0, 25)
        ),
    )
    _csv(
        phase11b / "execution_timing_capacity_summary.csv",
        tuple(
            {
                "requested_budget": budget,
                "scope_name": "whole_period",
                "observation_count": 100,
                "timing_available_count": 98,
                "capacity_available_count": 90,
                "mean_gap_decimal": 0.001,
                "mean_adverse_gap_decimal": 0.002,
                "mean_participation_pct_per_normalized_equity_unit": 0.0001,
                "p95_participation_pct_per_normalized_equity_unit": 0.001,
                "identity": f"execution-{budget}",
            }
            for budget in (5, 10, 20)
        ),
    )
    _csv(
        phase11a / "execution_capability_matrix.csv",
        ({
            "area": "friction",
            "capability": "market_impact",
            "state": "UNAVAILABLE",
            "evidence": "daily OHLCV only",
            "limitation": "no impact or fill-probability model",
        },),
    )
    _csv(
        price / "execution_price_provenance_capability.csv",
        ({
            "operation": "daily_monetary_capacity_proxy",
            "state": "UNSUPPORTED",
            "reason": "verified monetary units are absent",
        },),
    )
    _csv(
        provider / "execution_provider_provenance_capability.csv",
        ({
            "capability": "historical_vnd_order_notional",
            "state": "PARTIALLY_SUPPORTED",
            "basis": "historical provider provenance is incomplete",
        },),
    )


def test_catalog_projects_only_frozen_supported_controls(tmp_path: Path) -> None:
    _artifacts(tmp_path)
    catalog = inspect_portfolio_risk_catalog(root=tmp_path)
    assert catalog.selection_policies == ("ADX_ONLY",)
    assert catalog.budgets == (5, 10, 20)
    assert catalog.horizons == (5, 10, 20)
    assert catalog.risk_policies == ("NO_RISK_POLICY", "VOLATILITY_SCALING")


def test_control_portfolio_projects_structure_risk_outcome_and_budget_profiles(tmp_path: Path) -> None:
    _artifacts(tmp_path)
    result = select_portfolio_risk_evidence(
        inspect_portfolio_risk_catalog(root=tmp_path),
        selection_policy="ADX_ONLY",
        budget=5,
        horizon_sessions=10,
        risk_policy="NO_RISK_POLICY",
    )
    assert result.state is PortfolioEvidenceState.PARTIAL
    assert result.structure is not None and result.structure.mean_fill_ratio == pytest.approx(0.98)
    assert result.structure.max_single_name_weight == pytest.approx(0.2)
    assert result.risk is not None and result.risk.aggregate_pairwise_correlation == pytest.approx(0.295)
    assert result.risk.benchmark_beta == pytest.approx(0.795)
    assert result.outcome is not None and result.outcome.mean_gross_excess_return_pct_points == pytest.approx(0.3)
    assert tuple(item.risk_policy for item in result.risk_policy_outcomes) == (
        "NO_RISK_POLICY",
        "VOLATILITY_SCALING",
    )
    assert tuple(item.budget for item in result.risk_profiles) == (5, 10, 20)


def test_volatility_scaling_uses_persisted_after_policy_risk_without_inferred_beta(tmp_path: Path) -> None:
    _artifacts(tmp_path)
    result = select_portfolio_risk_evidence(
        inspect_portfolio_risk_catalog(root=tmp_path),
        selection_policy="ADX_ONLY",
        budget=10,
        horizon_sessions=5,
        risk_policy="VOLATILITY_SCALING",
    )
    assert result.risk is not None and result.risk.annualized_volatility == pytest.approx(0.20)
    assert result.risk.aggregate_pairwise_correlation is None
    assert result.risk.benchmark_beta is None
    assert result.structure is not None and result.structure.risk_policy_transformation_turnover == pytest.approx(0.07)
    assert result.structure.herfindahl_concentration is None


def test_cost_sensitivity_keeps_hypothetical_contracts_separate(tmp_path: Path) -> None:
    _artifacts(tmp_path)
    catalog = inspect_portfolio_risk_catalog(root=tmp_path)
    control = select_portfolio_risk_evidence(
        catalog, selection_policy="ADX_ONLY", budget=5, horizon_sessions=5,
        risk_policy="NO_RISK_POLICY",
    )
    scaled = select_portfolio_risk_evidence(
        catalog, selection_policy="ADX_ONLY", budget=5, horizon_sessions=5,
        risk_policy="VOLATILITY_SCALING",
    )
    assert tuple(item.cost_rate_bps for item in control.cost_sensitivity) == (0, 25)
    assert "NOT_HISTORICAL_COST_ESTIMATE" in control.cost_sensitivity[0].label
    assert scaled.cost_sensitivity[1].label == "HYPOTHETICAL_RISK_POLICY_TRANSFORMATION_COST"
    assert scaled.cost_sensitivity[1].mean_net_excess_return_pct_points == pytest.approx(0.225)


def test_execution_and_liquidity_semantics_are_partial_or_unavailable(tmp_path: Path) -> None:
    _artifacts(tmp_path)
    result = select_portfolio_risk_evidence(
        inspect_portfolio_risk_catalog(root=tmp_path),
        selection_policy="ADX_ONLY", budget=5, horizon_sessions=5,
        risk_policy="NO_RISK_POLICY",
    )
    assert result.execution is not None
    assert result.execution.timing_coverage_pct == pytest.approx(98.0)
    assert result.execution.capacity_coverage_pct == pytest.approx(90.0)
    dimensions = {item.name: item for item in result.dimensions}
    assert dimensions["Normalized volume participation"].state is PortfolioEvidenceState.PARTIAL
    assert dimensions["Actual fill probability / market impact"].state is PortfolioEvidenceState.UNAVAILABLE
    assert dimensions["Sector concentration"].state is PortfolioEvidenceState.UNAVAILABLE
    assert dimensions["Pairwise correlation matrix"].state is PortfolioEvidenceState.UNAVAILABLE


def test_provenance_and_material_limitations_are_projected(tmp_path: Path) -> None:
    _artifacts(tmp_path)
    result = select_portfolio_risk_evidence(
        inspect_portfolio_risk_catalog(root=tmp_path),
        selection_policy="ADX_ONLY", budget=20, horizon_sessions=20,
        risk_policy="NO_RISK_POLICY",
    )
    identities = {item.result_identity for item in result.provenance}
    assert {"phase6", "phase10a", "phase11b", "price-provenance"}.issubset(identities)
    assert all(item.as_of == "2026-09-17" for item in result.provenance)
    assert "database coverage is not historical VN100" in result.limitations
    assert "normalized participation is not monetary capacity" in result.limitations


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("selection_policy", "ADX_RSI_EQUAL_WEIGHT", "Selection policy"),
        ("budget", 99, "Budget"),
        ("horizon_sessions", 99, "Horizon"),
        ("risk_policy", "ARBITRARY_POLICY", "Risk-policy"),
    ),
)
def test_unsupported_context_is_incompatible(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    _artifacts(tmp_path)
    values: dict[str, object] = {
        "selection_policy": "ADX_ONLY",
        "budget": 5,
        "horizon_sessions": 5,
        "risk_policy": "NO_RISK_POLICY",
    }
    values[field] = value
    result = select_portfolio_risk_evidence(
        inspect_portfolio_risk_catalog(root=tmp_path), **values,  # type: ignore[arg-type]
    )
    assert result.state is PortfolioEvidenceState.INCOMPATIBLE
    assert message in result.detail


def test_missing_and_schema_incompatible_artifacts_fail_closed(tmp_path: Path) -> None:
    missing = inspect_portfolio_risk_catalog(root=tmp_path)
    assert missing.selection_policies == ()
    assert all(item.state is PortfolioEvidenceState.UNAVAILABLE for item in missing.provenance)

    _artifacts(tmp_path)
    path = (
        tmp_path / "research_results" / "quantlab_portfolio_construction_2018-08-07_2026-09-17"
        / "portfolio_construction_summary.csv"
    )
    _csv(path, ({"wrong": "schema"},))
    incompatible = inspect_portfolio_risk_catalog(root=tmp_path)
    structure = next(item for item in incompatible.provenance if item.family == "portfolio_structure")
    assert incompatible.selection_policies == ()
    assert structure.state is PortfolioEvidenceState.UNAVAILABLE
    assert "schema-incompatible" in structure.detail


def test_results_are_immutable_and_adapter_has_no_compute_or_market_access(tmp_path: Path) -> None:
    _artifacts(tmp_path)
    result = select_portfolio_risk_evidence(
        inspect_portfolio_risk_catalog(root=tmp_path),
        selection_policy="ADX_ONLY", budget=5, horizon_sessions=5,
        risk_policy="NO_RISK_POLICY",
    )
    with pytest.raises(FrozenInstanceError):
        result.detail = "changed"  # type: ignore[misc]
    source = (Path(__file__).resolve().parent.parent / "quantctl" / "portfolio_risk.py").read_text(encoding="utf-8")
    forbidden = (
        "sqlite3", "MarketDataSnapshot", "build_market_data_snapshot", "evaluate_portfolio",
        "evaluate_execution", "run_quantlab", "vnstock", "requests.", "composite_risk_score",
    )
    assert all(item not in source for item in forbidden)
