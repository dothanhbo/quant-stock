from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from quantlab.evaluation import (
    EvidenceSourceStatus,
    EvidenceStrength,
    evaluate_portfolio_risk_evidence,
    evaluate_portfolio_risk_evidence_sources,
)


def _returns(rows: int = 30) -> tuple[pd.DataFrame, pd.Series]:
    benchmark = np.asarray([0.01, 0.02, -0.01, 0.015, 0.005] * (rows // 5 + 1), dtype=float)[:rows]
    independent = np.asarray([0.03, -0.02, 0.01, 0.0, -0.01] * (rows // 5 + 1), dtype=float)[:rows]
    index = pd.date_range("2024-01-01", periods=rows, freq="D")
    return (
        pd.DataFrame({"AAA": benchmark, "BBB": 2.0 * benchmark, "CCC": independent}, index=index),
        pd.Series(benchmark, index=index),
    )


def _observations(count: int = 4, *, with_costs: bool = True) -> list[dict[str, object]]:
    returns, benchmark = _returns()
    trades = (
        {"entry_price": 100.0, "exit_price": 110.0, "quantity": 10},
        {"entry_price": 100.0, "exit_price": 95.0, "quantity": 10},
    ) if with_costs else ()
    observations: list[dict[str, object]] = []
    for number in range(count):
        observations.append({
            "observation_date": f"2024-01-{number + 1:02d}",
            "weights": {"AAA": 0.5, "BBB": 0.5},
            "daily_returns": returns,
            "benchmark_returns": benchmark,
            "trades": trades,
            "sector_by_symbol": {"AAA": "BANK", "BBB": "TECH"},
            "initial_equity": 10_000.0,
            "price_units_known": True,
            "volume_units_known": True,
            "daily_traded_value": {"AAA": 1_000_000.0, "BBB": 1_000_000.0},
            "drawdown": -float(number + 1),
            "regime": "BULL" if number % 2 == 0 else "BEAR",
            "provenance": {"classification": "EXPLICIT_SYMBOLS", "source": "fixture"},
        })
    return observations


def test_aggregates_concentration_correlation_beta_and_unknown_sector() -> None:
    observations = _observations(2, with_costs=False)
    observations[0]["weights"] = {"AAA": 0.8, "BBB": 0.1}
    observations[0]["sector_by_symbol"] = {"AAA": "BANK"}
    result = evaluate_portfolio_risk_evidence(source_name="smoke", observations=observations)
    assert result.observation_count == 2
    assert result.diagnostic_configuration["top_n"] == 3
    assert result.concentration_summaries["herfindahl_concentration"].mean is not None
    assert result.correlation_summaries["average_pairwise_correlation"].defined_count == 2
    assert set(result.correlation_threshold_summaries) == {
        "pairs_at_or_above_0.50", "pairs_at_or_above_0.70", "pairs_at_or_above_0.85",
    }
    assert result.beta_summaries["weighted_portfolio_beta"].mean is not None
    assert result.beta_summaries["weighted_portfolio_beta"].mean > 0.0
    assert result.concentration_summaries["unknown_sector_share_of_gross"].mean == pytest.approx(1.0 / 18.0)


def test_cost_sensitivity_is_monotonic_and_retains_break_even() -> None:
    result = evaluate_portfolio_risk_evidence(
        source_name="costs", observations=_observations(3), cost_grid_bps=(100, 0, 10),
    )
    assert [item.cost_bps for item in result.cost_sensitivity] == [0.0, 10.0, 100.0]
    assert [item.mean_net_pnl for item in result.cost_sensitivity] == sorted(
        (item.mean_net_pnl for item in result.cost_sensitivity), reverse=True,
    )
    assert result.cost_sensitivity[0].mean_break_even_cost_bps == pytest.approx(123.45679012345678)


def test_drawdown_association_uses_only_defined_drawdowns() -> None:
    observations = _observations(4, with_costs=False)
    observations[1]["drawdown"] = None
    result = evaluate_portfolio_risk_evidence(source_name="drawdown", observations=observations)
    assert result.drawdown_association.total_observations == 3
    assert result.drawdown_association.worst_group_count == 1
    assert result.drawdown_association.interpretation.endswith("not causal")


def test_undefined_history_is_explicit_and_policy_stays_conservative() -> None:
    observations = _observations(1, with_costs=False)
    result = evaluate_portfolio_risk_evidence(
        source_name="short", observations=observations, lookback_sessions=20, minimum_observations=20,
    )
    summary = result.correlation_summaries["average_pairwise_correlation"]
    assert summary.defined_count == 1
    assert summary.evidence_strength is EvidenceStrength.WEAK
    assert all(item.production_control_justified == "NOT YET — MORE EVIDENCE NEEDED" for item in result.policy_implications)


def test_not_evaluable_source_status_is_preserved_without_triggering_policy() -> None:
    result = evaluate_portfolio_risk_evidence(
        source_name="missing-artifact", observations=(), source_status=EvidenceSourceStatus.NOT_EVALUABLE,
    )
    assert result.source_status is EvidenceSourceStatus.NOT_EVALUABLE
    assert result.observation_count == 0
    assert all(item.mean is None for item in result.concentration_summaries.values())
    assert any(item.production_control_justified == "NOT EVALUABLE" for item in result.policy_implications)


def test_regime_segments_and_provenance_are_deterministic() -> None:
    first = evaluate_portfolio_risk_evidence(source_name="source", observations=_observations(4))
    second = evaluate_portfolio_risk_evidence(source_name="source", observations=reversed(_observations(4)))
    assert first == second
    assert [item.regime for item in first.regime_segments] == ["BEAR", "BULL"]
    assert first.provenance["classification"] == "EXPLICIT_SYMBOLS"
    assert first.identity


def test_sources_are_sorted_and_names_are_unique() -> None:
    result = evaluate_portfolio_risk_evidence_sources([
        {"source_name": "z", "observations": _observations(1, with_costs=False)},
        {"source_name": "a", "observations": _observations(1, with_costs=False)},
    ])
    assert tuple(item.source_name for item in result.sources) == ("a", "z")
    assert result.identity
    with pytest.raises(ValueError, match="unique"):
        evaluate_portfolio_risk_evidence_sources([
            {"source_name": "a", "observations": ()},
            {"source_name": "a", "observations": ()},
        ])


def test_invalid_configuration_and_nonfinite_drawdown_do_not_become_zero() -> None:
    with pytest.raises(ValueError, match="correlation_threshold"):
        evaluate_portfolio_risk_evidence(source_name="bad", observations=(), correlation_threshold=2.0)
    observations = _observations(2, with_costs=False)
    observations[0]["drawdown"] = float("nan")
    result = evaluate_portfolio_risk_evidence(source_name="nan", observations=observations)
    assert result.drawdown_association.total_observations == 1
    assert not math.isnan(result.drawdown_association.worst_mean_beta or 0.0)


def test_module_is_descriptive_only_and_returns_immutable_mappings() -> None:
    result = evaluate_portfolio_risk_evidence(source_name="immutable", observations=_observations(1, with_costs=False))
    with pytest.raises(TypeError):
        result.concentration_summaries["new"] = result.concentration_summaries["gross_exposure"]  # type: ignore[index]
    with pytest.raises((AttributeError, TypeError)):
        result.warnings += ("mutation",)  # type: ignore[misc]
    assert all("production control" in item.lower() for item in result.limitations[:1])
