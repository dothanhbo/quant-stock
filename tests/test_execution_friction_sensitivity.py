from __future__ import annotations

import pytest

from quantlab.execution.friction_sensitivity import (
    COST_FORMULA,
    EXPECTED_PROVENANCE,
    FRICTION_COMPONENT_EVIDENCE,
    NEUTRAL_EXECUTION_FRICTION_SENSITIVITY_V1,
    PATH_METRIC_REASON,
    PATH_METRIC_STATUS,
    FrictionProvenance,
    FrozenPortfolioOutcomeObservation,
    evaluate_execution_friction_sensitivity,
)


def _source_ids() -> dict[str, str]:
    return dict(EXPECTED_PROVENANCE)


def _obs(date: str, *, budget: int = 5, horizon: int = 5, turnover: float | None = 0.5,
         stock: float | None = 1.0, excess: float | None = 0.8, state: str = "FULLY_EVALUABLE"):
    if state != "FULLY_EVALUABLE":
        stock = excess = None
    return FrozenPortfolioOutcomeObservation(date, budget, horizon, state, turnover, stock, excess, f"phase7:{date}:{budget}:{horizon}")


def _eval(observations):
    return evaluate_execution_friction_sensitivity(tuple(observations), _source_ids())


def test_frozen_grid_zero_cost_reconciles_to_phase7_scope_and_returns() -> None:
    observations = (_obs("2018-08-07", turnover=0.5), _obs("2018-08-08", turnover=0.25, stock=-1, excess=-0.5))
    result = _eval(observations)
    zero = next(row for row in result.summaries if row.hypothetical_all_in_cost_rate_bps == 0)
    assert NEUTRAL_EXECUTION_FRICTION_SENSITIVITY_V1.cost_grid_bps == (0, 10, 25, 50)
    assert NEUTRAL_EXECUTION_FRICTION_SENSITIVITY_V1.cost_grid_provenance == FrictionProvenance.HYPOTHETICAL_ASSUMPTION
    assert zero.mean_gross_stock_return_pct == 0.0
    assert zero.mean_net_stock_return_pct == 0.0
    assert zero.mean_gross_excess_return_pct_points == pytest.approx(0.15)
    assert zero.mean_net_excess_return_pct_points == pytest.approx(0.15)
    assert zero.included_date_count == 2


def test_phase6_one_way_weight_turnover_formula_is_reused_exactly() -> None:
    row = next(item for item in _eval((_obs("2018-08-07", turnover=0.25),)).summaries if item.hypothetical_all_in_cost_rate_bps == 10)
    assert row.mean_cost_deduction_pct_points == pytest.approx(0.025)
    assert "Phase6_one_way_weight_turnover" in NEUTRAL_EXECUTION_FRICTION_SENSITIVITY_V1.turnover_definition
    assert "0.5 *" in NEUTRAL_EXECUTION_FRICTION_SENSITIVITY_V1.turnover_definition


def test_turnover_input_is_not_phase11b_acquisition_quantity_or_notional() -> None:
    observation = _obs("2018-08-07", turnover=0.4)
    row = next(item for item in _eval((observation,)).summaries if item.hypothetical_all_in_cost_rate_bps == 25)
    assert row.mean_cost_deduction_pct_points == pytest.approx(0.1)
    assert not hasattr(observation, "target_acquisition_notional")
    assert not hasattr(observation, "acquisition_shares")
    assert "Phase 11B TARGET_ACQUISITION_NOTIONAL" in _eval((observation,)).limitations[1]


def test_cost_formula_matches_one_way_turnover_times_bps_and_is_monotonic() -> None:
    result = _eval((_obs("2018-08-07", turnover=0.75, stock=2.0, excess=1.5),))
    assert COST_FORMULA == "deduction_percentage_points = Phase6_one_way_weight_turnover * hypothetical_all_in_rate_bps / 100"
    rows = sorted(
        (item for item in result.summaries if item.requested_budget == 5 and item.horizon_sessions == 5),
        key=lambda item: item.hypothetical_all_in_cost_rate_bps,
    )
    assert [row.hypothetical_all_in_cost_rate_bps for row in rows] == [0, 10, 25, 50]
    assert [row.mean_cost_deduction_pct_points for row in rows] == pytest.approx([0, 0.075, 0.1875, 0.375])
    assert all(right.mean_net_stock_return_pct <= left.mean_net_stock_return_pct for left, right in zip(rows, rows[1:]))
    assert all(right.mean_net_excess_return_pct_points <= left.mean_net_excess_return_pct_points for left, right in zip(rows, rows[1:]))


def test_forward_observation_deductions_do_not_compound_and_path_metrics_stay_unavailable() -> None:
    observations = (_obs("2018-08-07", turnover=0.5, stock=1.0, excess=1.0), _obs("2018-08-08", turnover=0.5, stock=2.0, excess=2.0))
    result = _eval(observations)
    row = next(item for item in result.summaries if item.hypothetical_all_in_cost_rate_bps == 10)
    assert row.mean_net_excess_return_pct_points == pytest.approx(1.45)
    assert row.path_metric_status == PATH_METRIC_STATUS == "NOT_APPLICABLE"
    assert row.path_metric_reason == PATH_METRIC_REASON == "OVERLAPPING_FORWARD_HORIZONS"
    assert not hasattr(row, "cagr")
    assert any("No compounded costs" in item for item in result.limitations)


def test_phase11b_open_gap_is_not_an_applied_cost_and_market_impact_stays_unavailable() -> None:
    result = _eval((_obs("2018-08-07", turnover=0.5),))
    assert "reference_to_next_open_gap" not in result.summaries[0].__dataclass_fields__
    impact = next(item for item in result.friction_components if item.component == "MARKET_IMPACT")
    assert impact.provenance == FrictionProvenance.EVIDENCE_UNAVAILABLE
    assert impact.rate_bps is None
    assert "not treated as slippage" in result.limitations[3]


def test_operational_assumptions_are_not_canonical_historical_costs() -> None:
    result = _eval((_obs("2018-08-07"),))
    assert len(FRICTION_COMPONENT_EVIDENCE) == 4
    assert all(item.provenance != FrictionProvenance.CANONICAL for item in result.friction_components)
    assert all(item.rate_bps is None for item in result.friction_components)
    assert all(item.provenance == FrictionProvenance.OPERATIONAL_ASSUMPTION for item in result.friction_components[:3])
    assert result.friction_components[-1].provenance == FrictionProvenance.EVIDENCE_UNAVAILABLE
    assert "not treated as a historical" in result.friction_components[0].note


def test_break_even_is_analytical_and_explicitly_descriptive() -> None:
    result = _eval((_obs("2018-08-07", turnover=0.5, excess=1.0), _obs("2018-08-08", turnover=0.5, excess=2.0)))
    item = next(row for row in result.break_even if row.scope_name == "whole_period")
    assert item.descriptive_break_even_friction_bps == pytest.approx(300.0)
    assert item.label == "DESCRIPTIVE_BREAK_EVEN_FRICTION"
    negative = _eval((_obs("2018-08-07", turnover=0.5, excess=-1.0),))
    negative_be = next(row for row in negative.break_even if row.scope_name == "whole_period")
    assert negative_be.descriptive_break_even_friction_bps is None
    assert negative_be.undefined_reason == "gross_mean_excess_is_negative_no_nonnegative_break_even_rate"


def test_temporal_scope_dimensions_and_no_policy_ranking() -> None:
    observations = tuple(_obs("2018-08-07", budget=budget, horizon=horizon) for budget in (5, 10, 20) for horizon in (5, 10, 20))
    result = _eval(observations)
    assert len(result.summaries) == 3 * 3 * 4
    assert len(result.block_summaries) == 3 * 3 * 4 * 4
    assert len(result.break_even) == 3 * 3 * 5
    assert result.summaries[0].requested_budget == 5
    assert result.summaries[0].horizon_sessions == 5
    assert "no policy" in " ".join(result.limitations).lower()
    assert len({(row.requested_budget, row.horizon_sessions, row.hypothetical_all_in_cost_rate_bps) for row in result.summaries}) == len(result.summaries)


def test_unavailable_outcomes_and_undefined_turnover_are_not_fabricated() -> None:
    result = _eval((_obs("2018-08-07", state="EMPTY_PORTFOLIO", turnover=None), _obs("2018-08-08", turnover=None)))
    row = next(item for item in result.summaries if item.hypothetical_all_in_cost_rate_bps == 0)
    assert row.phase7_evaluable_date_count == 1
    assert row.turnover_defined_date_count == 0
    assert row.included_date_count == 0
    assert row.mean_net_stock_return_pct is None
    be = next(item for item in result.break_even if item.scope_name == "whole_period")
    assert be.descriptive_break_even_friction_bps is None


def test_deterministic_identity_order_and_frozen_provenance_validation() -> None:
    observations = (_obs("2018-08-08"), _obs("2018-08-07", turnover=0.25))
    first = _eval(observations)
    second = _eval(tuple(reversed(observations)))
    assert first.identity == second.identity
    assert first.summaries == second.summaries
    assert first.block_summaries == second.block_summaries
    bad_sources = _source_ids()
    bad_sources["phase11d_result_identity"] = "wrong"
    with pytest.raises(ValueError, match="frozen upstream provenance mismatch"):
        evaluate_execution_friction_sensitivity(observations, bad_sources)
