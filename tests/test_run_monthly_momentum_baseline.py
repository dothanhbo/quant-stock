import pandas as pd
import pytest

from research.run_monthly_momentum_baseline import (
    build_yearly_returns,
    exploratory_gates,
)
from research.run_momentum_v2_comparison import comparison_yearly_returns
from research.run_momentum_component_ablation import (
    build_ablation_configs,
    build_breadth_hysteresis_configs,
    build_breadth_overlay_configs,
    build_top5_risk_configs,
)


def test_yearly_returns_reconcile_strategy_and_benchmark() -> None:
    equity = pd.DataFrame({
        "date": ["2025-01-01", "2025-12-31", "2026-12-31"],
        "equity": [100.0, 110.0, 121.0],
        "benchmark_equity": [100.0, 105.0, 115.5],
    })

    result = build_yearly_returns(equity)

    assert result["strategy_return_pct"].tolist() == pytest.approx([10.0, 10.0])
    assert result["vnindex_return_pct"].tolist() == pytest.approx([5.0, 10.0])


def test_exploratory_result_cannot_be_final_without_prospective_data() -> None:
    summary = {
        "excess_return_vs_vnindex_pct": 1.0,
        "excess_cagr_vs_vnindex_pct": 1.0,
        "strategy_max_drawdown_pct": -10.0,
        "profitable_years": 4,
        "years": 7,
        "annualized_turnover_pct": 100.0,
        "stale_valuation_events": 0,
        "point_in_time_universe_verified": False,
        "prospective_validation_passed": False,
    }

    result = exploratory_gates(summary)

    assert result["exploratory_gate_passed"] is True
    assert result["research_gate_passed"] is False


def test_v2_yearly_comparison_uses_previous_year_end() -> None:
    equity = pd.DataFrame({
        "date": ["2025-01-01", "2025-12-31", "2026-12-31"],
        "baseline_equity": [100.0, 110.0, 121.0],
        "v2_equity": [100.0, 120.0, 126.0],
        "vnindex_equity": [100.0, 105.0, 115.5],
    })

    result = comparison_yearly_returns(equity)

    assert result["v2_return_pct"].tolist() == pytest.approx([20.0, 5.0])
    assert result["v2_excess_vs_baseline_pct"].tolist() == pytest.approx([10.0, -5.0])


def test_component_ablation_has_six_predeclared_cases() -> None:
    cases = build_ablation_configs({
        "minimum_history_rows": 252,
        "minimum_adtv20": 1.0,
        "maximum_volatility63_pct": 100.0,
        "lot_size": 100,
        "commission_pct": 0.15,
        "sell_tax_pct": 0.10,
        "slippage_pct": 0.05,
    })

    assert list(cases) == [
        "baseline_top5",
        "top10_buffer",
        "top10_buffer_regime",
        "top10_buffer_ema_exit",
        "top10_buffer_momentum_exit",
        "full_v2",
    ]
    assert cases["top10_buffer_ema_exit"].daily_exit_ema200_enabled is True
    assert cases["top10_buffer_ema_exit"].daily_exit_momentum_enabled is False


def test_top5_ablation_never_buys_new_below_rank_five() -> None:
    shared = {
        "minimum_history_rows": 252,
        "minimum_adtv20": 1.0,
        "maximum_volatility63_pct": 100.0,
        "lot_size": 100,
        "commission_pct": 0.15,
        "sell_tax_pct": 0.10,
        "slippage_pct": 0.05,
    }
    cases = build_top5_risk_configs(shared)

    assert list(cases) == [
        "baseline_top5",
        "top5_buffer",
        "top5_buffer_regime",
        "top5_buffer_ema_exit",
        "top5_buffer_momentum_exit",
        "top5_full_risk",
    ]
    for case_id, config in cases.items():
        assert config.top_n == 5
        assert config.effective_entry_rank == 5
        if case_id != "baseline_top5":
            assert config.effective_hold_rank == 10


def test_breadth_ablation_has_fixed_60_40_thresholds() -> None:
    shared = {
        "minimum_history_rows": 252,
        "minimum_adtv20": 1.0,
        "maximum_volatility63_pct": 100.0,
        "lot_size": 100,
        "commission_pct": 0.15,
        "sell_tax_pct": 0.10,
        "slippage_pct": 0.05,
    }
    cases = build_breadth_overlay_configs(shared)

    assert list(cases) == [
        "top5_full_risk_control",
        "breadth_monthly",
        "breadth_weekly",
        "breadth_daily",
    ]
    assert cases["breadth_monthly"].breadth_exposure_enabled is True
    assert cases["breadth_weekly"].breadth_review_frequency == "WEEKLY"
    assert cases["breadth_daily"].breadth_review_frequency == "DAILY"
    for case_id, config in cases.items():
        if case_id != "top5_full_risk_control":
            assert config.breadth_risk_on_pct == 60.0
            assert config.breadth_neutral_pct == 40.0


def test_breadth_hysteresis_ablation_has_three_fixed_cases() -> None:
    shared = {
        "minimum_history_rows": 252,
        "minimum_adtv20": 1.0,
        "maximum_volatility63_pct": 100.0,
        "lot_size": 100,
        "commission_pct": 0.15,
        "sell_tax_pct": 0.10,
        "slippage_pct": 0.05,
    }
    cases = build_breadth_hysteresis_configs(shared)

    assert list(cases) == [
        "breadth_weekly_control",
        "breadth_weekly_confirm_2",
        "breadth_weekly_risk_on_monthly",
    ]
    assert (
        cases["breadth_weekly_control"]
        .breadth_recovery_confirmation_periods
        == 1
    )
    assert (
        cases["breadth_weekly_confirm_2"]
        .breadth_recovery_confirmation_periods
        == 2
    )
    assert (
        cases["breadth_weekly_risk_on_monthly"]
        .breadth_recovery_frequency
        == "MONTHLY"
    )
    for config in cases.values():
        assert config.breadth_review_frequency == "WEEKLY"
        assert config.breadth_risk_on_pct == 60.0
        assert config.breadth_neutral_pct == 40.0
