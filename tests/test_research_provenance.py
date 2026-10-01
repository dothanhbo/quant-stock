from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd

import backtesting.engine as engine
import backtesting.walk_forward as walk_forward
from backtesting.walk_forward import WalkForwardConfig
from quantlab.research_provenance import (
    HistoricalBiasStatus,
    HistoricalUniverseMethodology,
    PITMembershipCapability,
    ResearchWarning,
    build_research_provenance,
    canonical_local_price_provenance,
    classify_legacy_provenance,
    universe_methodology_contract,
)


def test_current_membership_is_explicitly_retrospective() -> None:
    provenance = build_research_provenance("current_vn100")
    assert provenance.universe.methodology is HistoricalUniverseMethodology.CURRENT_INDEX_MEMBERSHIP
    assert provenance.universe.bias_status is HistoricalBiasStatus.SURVIVORSHIP_AND_FUTURE_MEMBERSHIP_RISK
    assert provenance.universe.pit_membership_capability is PITMembershipCapability.UNSUPPORTED
    assert ResearchWarning.CURRENT_MEMBERSHIP_RETROSPECTIVE in provenance.warnings
    assert provenance.suitable_for_causal_historical_comparison is False


def test_database_coverage_is_not_historical_vn100() -> None:
    provenance = build_research_provenance("database_coverage")
    assert provenance.universe.methodology is HistoricalUniverseMethodology.DATABASE_COVERAGE
    assert provenance.universe.historical_index_membership is False
    assert ResearchWarning.DATABASE_COVERAGE_NOT_HISTORICAL_VN100 in provenance.warnings
    assert provenance.suitable_for_causal_historical_comparison is True


def test_point_in_time_capability_is_truthfully_unsupported() -> None:
    contract = universe_methodology_contract("POINT_IN_TIME_INDEX_MEMBERSHIP")
    assert contract.pit_membership_capability is PITMembershipCapability.UNSUPPORTED
    assert contract.historical_index_membership is True
    assert ResearchWarning.PIT_MEMBERSHIP_UNAVAILABLE in contract.bias_warnings


def test_symbol_only_identity_does_not_claim_security_history() -> None:
    provenance = build_research_provenance("explicit_symbols")
    identity = provenance.ticker_identity
    assert identity.canonical_security_identity_available is False
    assert identity.ticker_change_history_available is False
    assert identity.delisted_security_history_available is False
    assert identity.symbol_semantics == "historical_observation_label_only"
    assert ResearchWarning.TICKER_IDENTITY_HISTORY_UNAVAILABLE in provenance.warnings


def test_price_provenance_fails_closed() -> None:
    price = canonical_local_price_provenance()
    assert price.price_adjustment_state == "UNKNOWN"
    assert price.corporate_action_coverage == "UNAVAILABLE"
    assert price.execution_suitable is False
    assert price.volume_unit_state == "INTEGER_STORED_UNIT_UNRESOLVED"


def test_provenance_serialization_and_identity_are_deterministic() -> None:
    first = build_research_provenance("database_coverage")
    second = build_research_provenance("database_coverage")
    assert first.identity == second.identity
    assert dict(first.as_mapping()) == dict(second.as_mapping())
    assert first.as_mapping()["universe_methodology"] == "DATABASE_COVERAGE"
    assert json.dumps(first.as_dict(), sort_keys=True)


def test_legacy_metadata_is_not_silently_upgraded() -> None:
    assert classify_legacy_provenance({}) == "LEGACY_UNKNOWN"
    assert classify_legacy_provenance(None) == "LEGACY_UNKNOWN"
    assert classify_legacy_provenance({"research_provenance": {"identity": "x"}}) == "EXPLICIT_PROVENANCE"


def test_engine_metrics_expose_structured_methodology_without_network(
    monkeypatch,
) -> None:
    class FakeSimulator:
        def __init__(self, *args, **kwargs):
            pass

        def simulate(self, trades):
            return SimpleNamespace(
                executed_trades=[],
                rejected_trades=[],
                final_cash=1_000_000_000.0,
                final_market_value=0.0,
                final_open_positions=0,
                equity_curve=pd.DataFrame(),
                final_equity=1_000_000_000.0,
            )

    monkeypatch.setattr(engine, "PortfolioSimulator", FakeSimulator)
    monkeypatch.setattr(engine, "get_vn100_symbols", lambda: ["AAA", "BBB"])
    monkeypatch.setattr(engine, "generate_candidate_trades", lambda *args, **kwargs: [])
    monkeypatch.setattr(engine, "calculate_trade_distribution", lambda *args: {})
    monkeypatch.setattr(engine, "calculate_trade_analytics", lambda *args: {})
    monkeypatch.setattr(engine, "calculate_portfolio_metrics", lambda *args, **kwargs: {"cagr_pct": 0.0})

    _, metrics, _ = engine.run_backtest()

    assert metrics["universe_methodology"] == "CURRENT_INDEX_MEMBERSHIP"
    assert metrics["historical_bias_status"] == "SURVIVORSHIP_AND_FUTURE_MEMBERSHIP_RISK"
    assert metrics["pit_membership_capability"] == "UNSUPPORTED"
    assert "CURRENT_MEMBERSHIP_RETROSPECTIVE" in metrics["research_warnings"]
    assert metrics["research_provenance"]["universe_methodology"] == "CURRENT_INDEX_MEMBERSHIP"


def test_walk_forward_summary_exposes_explicit_symbol_methodology() -> None:
    def run_backtest_fn(**kwargs):
        return [], {
            "final_equity": kwargs["initial_capital"],
            "total_return_pct": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown_pct": 0.0,
            "profit_factor": 0.0,
            "win_rate_pct": 0.0,
            "total_trades": 0,
        }, pd.DataFrame()

    result = walk_forward.run_walk_forward(
        config=WalkForwardConfig(
            start_date="2020-01-01", end_date="2020-03-31",
            train_months=1, test_months=1, step_months=1,
        ),
        initial_capital=1_000_000.0,
        run_backtest_fn=run_backtest_fn,
        backtest_kwargs={"symbols": ["AAA"]},
    )
    assert result.summary["universe_methodology"] == "EXPLICIT_SYMBOL_SET"
    assert result.summary["pit_membership_capability"] == "UNSUPPORTED"
    assert result.folds["historical_bias_status"].iloc[0] == "EXPLICIT_SYMBOL_SCOPE"
