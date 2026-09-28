from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import inspect
import json
from pathlib import Path
from types import MappingProxyType

import pytest

from quantlab.portfolio import (
    FrozenForwardOutcome,
    PortfolioOutcomeAvailability,
    RiskPolicy,
    RiskPolicyPortfolio,
    RiskPolicyPosition,
    RiskPolicyState,
    evaluate_risk_policy_outcomes,
)
from quantlab.portfolio.outcome_evaluation import PATH_METRIC_REASON, PATH_METRIC_STATUS
from research import run_quantlab_portfolio_risk_policy_outcome as runner


FINGERPRINTS = {RiskPolicy.NO_RISK_POLICY: "baseline-fingerprint", RiskPolicy.VOLATILITY_SCALING: "scaled-fingerprint"}


def _portfolio(policy: RiskPolicy, *, multiplier: float, state: RiskPolicyState, session: str = "2024-01-31", budget: int = 5, zero_second: bool = False) -> RiskPolicyPortfolio:
    weights = (0.5 * multiplier, 0.0 if zero_second else 0.5 * multiplier)
    positions = tuple(RiskPolicyPosition(symbol, 0.5, weight, f"source-{symbol}", f"position-{policy.value}-{symbol}") for symbol, weight in zip(("AAA","BBB"), weights, strict=True))
    gross = sum(weights); cash = 1-gross
    return RiskPolicyPortfolio(
        session, budget, "EQUAL_WEIGHT", policy, FINGERPRINTS[policy], state, "fixture",
        f"source-portfolio-{session}-{budget}", "source-risk", positions, 1.0, gross, cash,
        multiplier, 1-gross, 1-gross, 2, max(weights), sum(w*w for w in weights),
        2.0, 0.3, 0.3*multiplier, 0.01, 0.01*multiplier*multiplier, 0.2,
        0.5, 0.5, 2.0, 2.0, 1.0, f"portfolio-{policy.value}-{session}-{budget}",
    )


def _outcome(symbol: str, *, returns=(10.0, 20.0, 30.0), available: bool = True, benchmark=(5.0, 6.0, 7.0)) -> FrozenForwardOutcome:
    horizons=(5,10,20)
    return FrozenForwardOutcome(
        "2024-01-31", symbol, "VNINDEX",
        MappingProxyType({h:f"2024-02-{day:02d}" for h,day in zip(horizons,(7,14,28),strict=True)}),
        MappingProxyType({h:(r if available else None) for h,r in zip(horizons,returns,strict=True)}),
        MappingProxyType({h:(r if available else None) for h,r in zip(horizons,benchmark,strict=True)}),
        MappingProxyType({h:((r-b) if available else None) for h,r,b in zip(horizons,returns,benchmark,strict=True)}),
        MappingProxyType({h:("AVAILABLE" if available else "MISSING_STOCK_TARGET_CLOSE") for h in horizons}),
    )


def _evaluate(portfolios=None, outcomes=None):
    portfolios = portfolios or (
        _portfolio(RiskPolicy.NO_RISK_POLICY,multiplier=1,state=RiskPolicyState.IDENTITY_NO_CHANGE),
        _portfolio(RiskPolicy.VOLATILITY_SCALING,multiplier=.8,state=RiskPolicyState.APPLIED),
    )
    outcomes = outcomes or (_outcome("AAA"), _outcome("BBB",returns=(-10,-20,-30)))
    return evaluate_risk_policy_outcomes(portfolios,outcomes,source_identities={"phase10b":"frozen","phase65":"frozen"})


def _daily(result, policy, horizon=5):
    return next(item for item in result.observations if item.policy is policy and item.horizon_sessions==horizon)


def test_identity_policy_matches_phase7_weighted_return_and_benchmark_semantics() -> None:
    item=_daily(_evaluate(),RiskPolicy.NO_RISK_POLICY)
    assert item.portfolio_forward_return_pct==pytest.approx(0.0)
    assert item.benchmark_forward_return_pct==pytest.approx(5.0)
    assert item.portfolio_benchmark_contribution_pct==pytest.approx(5.0)
    assert item.portfolio_excess_return_pct_points==pytest.approx(-5.0)


def test_scaled_exposure_is_not_renormalized_and_cash_earns_zero() -> None:
    outcomes=(_outcome("AAA",returns=(10,10,10)),_outcome("BBB",returns=(10,10,10)))
    item=_daily(_evaluate(outcomes=outcomes),RiskPolicy.VOLATILITY_SCALING)
    assert item.risky_gross_weight==pytest.approx(.8) and item.cash_weight==pytest.approx(.2)
    assert item.portfolio_forward_return_pct==pytest.approx(8.0)
    assert item.portfolio_benchmark_contribution_pct==pytest.approx(4.0)
    assert item.portfolio_excess_return_pct_points==pytest.approx(4.0)


def test_missing_active_constituent_invalidates_but_zero_weight_does_not() -> None:
    missing=(_outcome("AAA"),_outcome("BBB",available=False))
    assert _daily(_evaluate(outcomes=missing),RiskPolicy.VOLATILITY_SCALING).availability is PortfolioOutcomeAvailability.UNAVAILABLE_CONSTITUENT_OUTCOME
    scaled=_portfolio(RiskPolicy.VOLATILITY_SCALING,multiplier=.8,state=RiskPolicyState.APPLIED,zero_second=True)
    baseline=_portfolio(RiskPolicy.NO_RISK_POLICY,multiplier=1,state=RiskPolicyState.IDENTITY_NO_CHANGE,zero_second=True)
    result=_evaluate((baseline,scaled),missing)
    assert _daily(result,RiskPolicy.VOLATILITY_SCALING).availability is PortfolioOutcomeAvailability.FULLY_EVALUABLE


def test_horizon_targets_and_overlapping_path_limitations_are_frozen() -> None:
    result=_evaluate(); item=_daily(result,RiskPolicy.VOLATILITY_SCALING,20)
    assert item.target_session=="2024-02-28"
    assert all(summary.path_metric_status==PATH_METRIC_STATUS and summary.path_metric_reason==PATH_METRIC_REASON for summary in result.summaries)


def test_same_date_contrast_and_applied_subset_are_paired() -> None:
    result=_evaluate()
    all_row=next(item for item in result.contrasts if item.requested_budget==5 and item.horizon_sessions==5 and item.subset=="ALL")
    applied=next(item for item in result.contrasts if item.requested_budget==5 and item.horizon_sessions==5 and item.subset=="APPLIED")
    assert all_row.paired_date_count==applied.paired_date_count==1
    assert all_row.mean_risky_exposure_delta==pytest.approx(-.2)
    assert all_row.mean_cash_delta==pytest.approx(.2)


def test_cost_sensitivity_uses_transformation_turnover_only_once() -> None:
    result=_evaluate()
    scaled=next(item for item in result.cost_sensitivity if item.policy is RiskPolicy.VOLATILITY_SCALING and item.requested_budget==5 and item.horizon_sessions==5 and item.cost_rate_bps==50)
    baseline=next(item for item in result.cost_sensitivity if item.policy is RiskPolicy.NO_RISK_POLICY and item.requested_budget==5 and item.horizon_sessions==5 and item.cost_rate_bps==50)
    assert scaled.mean_transformation_turnover==pytest.approx(.2)
    assert scaled.mean_hypothetical_cost_pct_points==pytest.approx(.1)
    assert baseline.mean_hypothetical_cost_pct_points==pytest.approx(0.0)


def test_temporal_blocks_and_conditional_summaries_are_predeclared() -> None:
    result=_evaluate()
    assert len(result.block_summaries)==2*3*3*4
    subsets={(item.policy,item.subset) for item in result.summaries}
    assert (RiskPolicy.NO_RISK_POLICY,"ALL") in subsets
    assert {(RiskPolicy.VOLATILITY_SCALING,name) for name in ("ALL","APPLIED","IDENTITY_NO_CHANGE")} <= subsets


def test_deterministic_order_identity_and_no_decision_or_parameter_search() -> None:
    portfolios=(
        _portfolio(RiskPolicy.NO_RISK_POLICY,multiplier=1,state=RiskPolicyState.IDENTITY_NO_CHANGE),
        _portfolio(RiskPolicy.VOLATILITY_SCALING,multiplier=.8,state=RiskPolicyState.APPLIED),
    )
    outcomes=(_outcome("AAA"),_outcome("BBB",returns=(-10,-20,-30)))
    assert _evaluate(portfolios,outcomes)==_evaluate(tuple(reversed(portfolios)),tuple(reversed(outcomes)))
    result=_evaluate()
    assert not hasattr(result,"decision") and not hasattr(result,"winner")
    signature=inspect.signature(evaluate_risk_policy_outcomes)
    assert "snapshot" not in signature.parameters and "database" not in signature.parameters


def test_policy_fingerprint_changes_are_identity_bound() -> None:
    portfolios=(
        _portfolio(RiskPolicy.NO_RISK_POLICY,multiplier=1,state=RiskPolicyState.IDENTITY_NO_CHANGE),
        replace(_portfolio(RiskPolicy.VOLATILITY_SCALING,multiplier=.8,state=RiskPolicyState.APPLIED),policy_fingerprint="changed"),
    )
    assert _evaluate().identity != _evaluate(portfolios=portfolios).identity


def test_runner_rejects_changed_upstream_policy_fingerprint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root=tmp_path/"phase10b";root.mkdir()
    manifest={"result_identity":runner.EXPECTED_PHASE10B_RESULT,"specification_fingerprint":runner.EXPECTED_PHASE10B_SPEC,"policies":[{"name":"NO_RISK_POLICY","fingerprint":"changed","target_annualized_volatility":None},{"name":"VOLATILITY_SCALING","fingerprint":runner.EXPECTED_POLICY_FINGERPRINTS["VOLATILITY_SCALING"],"target_annualized_volatility":.2}],"artifacts":{}}
    path=root/"portfolio_risk_policy_manifest.json";path.write_text(json.dumps(manifest),encoding="utf-8")
    monkeypatch.setattr(runner,"EXPECTED_PHASE10B_MANIFEST_SHA256",sha256(path.read_bytes()).hexdigest())
    with pytest.raises(ValueError,match="frozen policy provenance"):
        runner._load_phase10b(root)


def test_runner_rejects_changed_phase65_artifact_hash(tmp_path: Path) -> None:
    path=tmp_path/"outcomes.csv";path.write_text("original",encoding="utf-8")
    expected=sha256(path.read_bytes()).hexdigest();path.write_text("changed",encoding="utf-8")
    with pytest.raises(ValueError,match="SHA-256 mismatch"):
        runner._require_hash(path,expected,"Phase 6.5 outcome artifact")
