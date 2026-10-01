from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantlab.diagnostics import (
    DiagnosticEvidenceState,
    ExecutionCapabilityStatus,
    evaluate_portfolio_execution_diagnostics,
)


def _returns(rows: int = 30) -> tuple[pd.DataFrame, pd.Series]:
    benchmark = np.asarray([0.01, 0.02, -0.01, 0.015, 0.005] * (rows // 5 + 1), dtype=float)[:rows]
    independent = np.asarray([0.03, -0.02, 0.01, 0.0, -0.01] * (rows // 5 + 1), dtype=float)[:rows]
    index = pd.date_range("2024-01-01", periods=rows, freq="D")
    return pd.DataFrame({"AAA": benchmark, "BBB": 2.0 * benchmark, "CCC": independent}, index=index), pd.Series(benchmark, index=index)


def test_concentration_and_unknown_sector_are_explicit() -> None:
    returns, benchmark = _returns()
    concentrated = evaluate_portfolio_execution_diagnostics(
        weights={"AAA": 0.8, "BBB": 0.1}, daily_returns=returns, benchmark_returns=benchmark,
        sector_by_symbol={"AAA": "BANK"},
    )
    diversified = evaluate_portfolio_execution_diagnostics(
        weights={"AAA": 0.5, "BBB": 0.5}, daily_returns=returns, benchmark_returns=benchmark,
        sector_by_symbol={"AAA": "BANK", "BBB": "TECH"},
    )
    assert concentrated.concentration.herfindahl_concentration > diversified.concentration.herfindahl_concentration
    assert concentrated.concentration.unknown_sector_weight == pytest.approx(0.1)
    assert sum(concentrated.concentration.sector_weights.values()) == pytest.approx(0.8)


def test_perfect_and_lower_pairwise_correlation_are_distinguished() -> None:
    returns, benchmark = _returns()
    result = evaluate_portfolio_execution_diagnostics(
        weights={"AAA": 0.4, "BBB": 0.3, "CCC": 0.3}, daily_returns=returns, benchmark_returns=benchmark,
        minimum_observations=20,
    )
    assert result.correlation.pairwise_correlations["AAA|BBB"] == pytest.approx(1.0)
    assert result.correlation.pairwise_correlations["AAA|CCC"] < 1.0
    assert result.correlation.valid_pairs == 3
    assert result.correlation.weighted_pairwise_correlation is not None
    assert result.sector_crowding.high_correlation_pair_share is not None


def test_insufficient_history_is_not_fabricated() -> None:
    returns, benchmark = _returns(8)
    result = evaluate_portfolio_execution_diagnostics(
        weights={"AAA": 0.5, "BBB": 0.5}, daily_returns=returns, benchmark_returns=benchmark,
        lookback_sessions=20, minimum_observations=10,
    )
    assert result.correlation.valid_pairs == 0
    assert result.correlation.unavailable_pairs == 1
    assert result.correlation.average_pairwise_correlation is None
    assert result.beta.weighted_portfolio_beta is None
    assert result.beta.evidence_state is DiagnosticEvidenceState.INSUFFICIENT_HISTORY


def test_beta_copy_and_scaled_copy_are_transparent() -> None:
    returns, benchmark = _returns()
    result = evaluate_portfolio_execution_diagnostics(
        weights={"AAA": 0.5, "BBB": 0.5}, daily_returns=returns, benchmark_returns=benchmark,
    )
    assert result.beta.per_symbol_beta["AAA"] == pytest.approx(1.0)
    assert result.beta.per_symbol_beta["BBB"] == pytest.approx(2.0)
    assert result.beta.weighted_portfolio_beta == pytest.approx(1.5)


def test_execution_contract_does_not_call_fixed_slippage_realism() -> None:
    returns, benchmark = _returns()
    result = evaluate_portfolio_execution_diagnostics(
        weights={"AAA": 1.0}, daily_returns=returns, benchmark_returns=benchmark,
    )
    capabilities = result.execution_realism.capabilities
    assert capabilities["fees"] is ExecutionCapabilityStatus.MODELED
    assert capabilities["slippage"] is ExecutionCapabilityStatus.PARTIAL
    assert capabilities["bid_ask_spread"] is ExecutionCapabilityStatus.UNMODELED
    assert capabilities["market_impact"] is ExecutionCapabilityStatus.UNMODELED
    assert capabilities["corporate_actions"] is ExecutionCapabilityStatus.UNMODELED


def test_cost_sensitivity_is_downstream_and_monotonic() -> None:
    returns, benchmark = _returns()
    trades = [
        {"entry_price": 100.0, "exit_price": 110.0, "quantity": 10},
        {"entry_price": 100.0, "exit_price": 95.0, "quantity": 10},
    ]
    result = evaluate_portfolio_execution_diagnostics(
        weights={"AAA": 1.0}, daily_returns=returns, benchmark_returns=benchmark,
        trades=trades, initial_equity=10_000.0, cost_grid_bps=(0, 10, 100),
    )
    assert result.cost_sensitivity[0].net_pnl == pytest.approx(result.cost_sensitivity[0].gross_pnl)
    assert [point.net_pnl for point in result.cost_sensitivity] == sorted(
        (point.net_pnl for point in result.cost_sensitivity), reverse=True,
    )
    assert all(point.trade_count == 2 for point in result.cost_sensitivity)
    assert result.break_even_cost_bps == pytest.approx(123.45679012345678)


def test_liquidity_participation_requires_verified_units() -> None:
    returns, benchmark = _returns()
    unavailable = evaluate_portfolio_execution_diagnostics(
        weights={"AAA": 0.5}, daily_returns=returns, benchmark_returns=benchmark,
        daily_traded_value={"AAA": 1_000_000}, initial_equity=100_000,
    )
    available = evaluate_portfolio_execution_diagnostics(
        weights={"AAA": 0.5}, daily_returns=returns, benchmark_returns=benchmark,
        daily_traded_value={"AAA": 1_000_000}, initial_equity=100_000,
        price_units_known=True, volume_units_known=True,
    )
    assert unavailable.liquidity.state is DiagnosticEvidenceState.UNAVAILABLE
    assert available.liquidity.participation_by_symbol["AAA"] == pytest.approx(0.05)
    assert "not capacity" in available.liquidity.warning


def test_result_is_immutable_and_provenance_warnings_propagate() -> None:
    returns, benchmark = _returns()
    result = evaluate_portfolio_execution_diagnostics(
        weights={"AAA": 1.0}, daily_returns=returns, benchmark_returns=benchmark,
    )
    with pytest.raises(TypeError):
        result.concentration.sector_weights["NEW"] = 1.0
    assert any("corporate" in warning.lower() for warning in result.warnings)
    assert result.identity


def test_invalid_configuration_fails_clearly() -> None:
    returns, benchmark = _returns()
    with pytest.raises(ValueError, match="lookback"):
        evaluate_portfolio_execution_diagnostics(
            weights={"AAA": 1.0}, daily_returns=returns, benchmark_returns=benchmark,
            lookback_sessions=2, minimum_observations=3,
        )
