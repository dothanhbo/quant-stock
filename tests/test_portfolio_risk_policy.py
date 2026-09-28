from __future__ import annotations

from dataclasses import replace
import inspect

import pytest

from quantlab.portfolio import (
    FrozenRiskPortfolio,
    FrozenRiskPosition,
    PortfolioRiskComponentContribution,
    PortfolioRiskObservation,
    RiskEvidenceState,
    RiskPolicy,
    RiskPolicyDefinition,
    RiskPolicyState,
    RISK_CONTRIBUTION_POLICY_STATE,
    evaluate_portfolio_risk_policies,
)
from research.run_quantlab_portfolio_risk_policy import _portfolio_rows


def _portfolio(*, session: str = "2024-01-31", budget: int = 5, symbols: tuple[str, ...] = ("AAA", "BBB")) -> FrozenRiskPortfolio:
    weight = 1.0 / len(symbols) if symbols else 0.0
    return FrozenRiskPortfolio(
        session, budget, "EQUAL_WEIGHT",
        tuple(FrozenRiskPosition(symbol, weight, f"position-{symbol}") for symbol in symbols),
        1.0 if symbols else 0.0, 0.0 if symbols else 1.0, f"portfolio-{session}-{budget}",
    )


def _risk(portfolio: FrozenRiskPortfolio, *, volatility: float | None = 0.30) -> PortfolioRiskObservation:
    variance = None if volatility is None else (volatility / (252 ** 0.5)) ** 2
    state = RiskEvidenceState.DEFINED if volatility is not None else RiskEvidenceState.INSUFFICIENT_TRAILING_HISTORY
    count = len(portfolio.positions)
    hhi = sum(item.weight**2 for item in portfolio.positions)
    return PortfolioRiskObservation(
        session_date=portfolio.session_date, requested_budget=portfolio.requested_budget,
        weighting_policy="EQUAL_WEIGHT", selected_count=count, gross_weight=portfolio.gross_weight,
        cash_weight=portfolio.cash_weight, max_single_name_weight=max((p.weight for p in portfolio.positions), default=0.0),
        herfindahl_concentration=hhi, effective_n=None if not count else 1 / hhi,
        trailing_session_count=60, covariance_observation_count=60 if volatility is not None else 0,
        pairwise_valid_count=1 if count == 2 else 0, pairwise_unavailable_count=0,
        mean_pairwise_correlation=0.2 if count == 2 else None,
        median_pairwise_correlation=0.2 if count == 2 else None,
        minimum_pairwise_correlation=0.2 if count == 2 else None,
        maximum_pairwise_correlation=0.2 if count == 2 else None,
        daily_portfolio_variance=variance,
        daily_portfolio_volatility=None if variance is None else variance**0.5,
        annualized_portfolio_volatility=volatility,
        maximum_component_risk_share=0.5 if volatility is not None else None,
        risk_contribution_herfindahl=0.5 if volatility is not None else None,
        effective_risk_contributors=2.0 if volatility is not None else None,
        benchmark_observation_count=60 if volatility is not None else 0,
        benchmark_correlation=0.7 if volatility is not None else None,
        benchmark_beta=0.8 if volatility is not None else None,
        covariance_evidence_state=state,
        covariance_undefined_reason=None if volatility is not None else "insufficient_VNINDEX_sessions",
        benchmark_undefined_reason=None if volatility is not None else "portfolio_covariance_unavailable",
        sector_evidence_state="SECTOR_EVIDENCE_UNAVAILABLE",
        source_portfolio_identity=portfolio.portfolio_identity,
        component_identity_count=count if volatility is not None else 0,
        component_identities_sha256="components", identity=f"risk-{portfolio.session_date}-{portfolio.requested_budget}-{volatility}",
    )


def _components(portfolio: FrozenRiskPortfolio, risk: PortfolioRiskObservation) -> tuple[PortfolioRiskComponentContribution, ...]:
    if risk.daily_portfolio_variance is None or not portfolio.positions:
        return ()
    share = 1 / len(portfolio.positions)
    return tuple(
        PortfolioRiskComponentContribution(
            portfolio.session_date, portfolio.requested_budget, item.symbol, item.weight,
            risk.daily_portfolio_variance, risk.daily_portfolio_variance * share, share, f"component-{item.symbol}",
        ) for item in portfolio.positions
    )


def _evaluate(portfolios, risks=None, components=None):
    portfolios = tuple(portfolios)
    risks = tuple(_risk(item) for item in portfolios) if risks is None else tuple(risks)
    components = (
        tuple(row for item, risk in zip(portfolios, risks, strict=True) for row in _components(item, risk))
        if components is None and len(portfolios) == len(risks)
        else () if components is None
        else tuple(components)
    )
    return evaluate_portfolio_risk_policies(portfolios, risks, components, source_identities={"phase6": "frozen", "phase10a": "frozen"})


def _scenario(result, policy: RiskPolicy):
    return next(item for item in result.portfolios if item.policy is policy)


def test_identity_control_exactly_preserves_frozen_portfolio() -> None:
    source = _portfolio(); item = _scenario(_evaluate((source,)), RiskPolicy.NO_RISK_POLICY)
    assert item.policy_state is RiskPolicyState.IDENTITY_NO_CHANGE
    assert item.exposure_multiplier == 1.0
    assert tuple(position.transformed_weight for position in item.positions) == tuple(position.weight for position in source.positions)
    assert item.transformed_gross_weight == source.gross_weight and item.transformed_cash_weight == source.cash_weight


def test_volatility_scaling_is_long_only_unlevered_and_preserves_relative_weights() -> None:
    source = _portfolio(); item = _scenario(_evaluate((source,)), RiskPolicy.VOLATILITY_SCALING)
    assert item.policy_state is RiskPolicyState.APPLIED
    assert item.exposure_multiplier == pytest.approx(2 / 3)
    assert item.transformed_gross_weight <= source.gross_weight <= 1.0
    assert item.transformed_cash_weight == pytest.approx(1 / 3)
    assert {position.symbol for position in item.positions} == {position.symbol for position in source.positions}
    assert item.positions[0].transformed_weight / item.positions[1].transformed_weight == pytest.approx(1.0)
    assert item.transformation_turnover == pytest.approx(1 / 3)


def test_risk_recomputation_uses_uniform_phase10a_scaling_and_reconciles_components() -> None:
    source = _portfolio(); risk = _risk(source)
    item = _scenario(_evaluate((source,), (risk,)), RiskPolicy.VOLATILITY_SCALING)
    assert item.annualized_volatility_after == pytest.approx(0.20)
    assert item.daily_variance_after == pytest.approx(risk.daily_portfolio_variance * (2 / 3) ** 2)
    assert item.component_contribution_share_sum == pytest.approx(1.0)
    assert item.maximum_component_risk_share_after == item.maximum_component_risk_share_before
    assert item.effective_risk_contributors_after == item.effective_risk_contributors_before


def test_unavailable_risk_is_preserved_explicitly_not_converted_to_zero() -> None:
    source = _portfolio(); risk = _risk(source, volatility=None)
    item = _scenario(_evaluate((source,), (risk,), ()), RiskPolicy.VOLATILITY_SCALING)
    assert item.policy_state is RiskPolicyState.INSUFFICIENT_RISK_EVIDENCE
    assert item.exposure_multiplier == 1.0 and item.annualized_volatility_after is None
    assert item.transformed_gross_weight == source.gross_weight


def test_empty_and_underfilled_portfolios_are_explicit_and_reconcile() -> None:
    empty = _portfolio(symbols=()); underfilled = _portfolio(session="2024-02-01", symbols=("AAA",))
    result = _evaluate((empty, underfilled))
    empty_scaled = next(item for item in result.portfolios if item.session_date == empty.session_date and item.policy is RiskPolicy.VOLATILITY_SCALING)
    underfilled_scaled = next(item for item in result.portfolios if item.session_date == underfilled.session_date and item.policy is RiskPolicy.VOLATILITY_SCALING)
    assert empty_scaled.policy_state is RiskPolicyState.EMPTY_PORTFOLIO and empty_scaled.transformed_cash_weight == 1.0
    assert underfilled_scaled.selected_count == 1
    assert underfilled_scaled.transformed_gross_weight + underfilled_scaled.transformed_cash_weight == pytest.approx(1.0)


def test_policy_fingerprints_are_deterministic_and_parameter_sensitive() -> None:
    first = RiskPolicyDefinition(RiskPolicy.VOLATILITY_SCALING, 0.20)
    second = RiskPolicyDefinition(RiskPolicy.VOLATILITY_SCALING, 0.20)
    changed = RiskPolicyDefinition(RiskPolicy.VOLATILITY_SCALING, 0.15)
    assert first.fingerprint == second.fingerprint != changed.fingerprint


def test_ordering_identity_and_future_independence_are_deterministic() -> None:
    first_portfolio = _portfolio(session="2024-01-31", budget=5)
    second_portfolio = _portfolio(session="2024-02-01", budget=10)
    first = _evaluate((second_portfolio, first_portfolio))
    second = _evaluate((first_portfolio, second_portfolio))
    assert first == second
    assert tuple((item.session_date, item.requested_budget, item.policy.value) for item in first.portfolios) == tuple(
        (item.session_date, item.requested_budget, item.policy.value) for item in second.portfolios
    )
    assert "snapshot" not in inspect.signature(evaluate_portfolio_risk_policies).parameters


def test_outcome_firewall_sector_absence_and_risk_contribution_deferral() -> None:
    result = _evaluate((_portfolio(),))
    assert result.deferred_policies == (RISK_CONTRIBUTION_POLICY_STATE,)
    assert tuple(item.policy for item in result.policies) == (RiskPolicy.NO_RISK_POLICY, RiskPolicy.VOLATILITY_SCALING)
    assert all(not hasattr(item, "return") and not hasattr(item, "pnl") for item in result.portfolios)
    assert "outcome" not in inspect.signature(evaluate_portfolio_risk_policies).parameters
    assert any("sector policy unavailable" in item for item in result.limitations)
    projected = _portfolio_rows(result)
    assert "positions" not in projected[0]
    assert not ({"return", "pnl", "future_return"} & set(projected[0]))


def test_source_key_or_identity_mismatch_fails_without_silent_substitution() -> None:
    source = _portfolio(); risk = _risk(source)
    with pytest.raises(ValueError, match="identical keys"):
        _evaluate((source,), ())
    with pytest.raises(ValueError, match="provenance"):
        _evaluate((source,), (replace(risk, source_portfolio_identity="wrong"),), _components(source, risk))
