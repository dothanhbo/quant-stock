from __future__ import annotations

import pandas as pd
import pytest

from research.run_entry_ablation import build_cases, compound_return
from strategy.donchian_breakout_entry import DonchianBreakoutEntryModel
from strategy.hybrid_trend_donchian_entry import (
    HybridTrendDonchianEntryModel,
)


def test_entry_ablation_has_all_four_unique_cases() -> None:
    cases = build_cases()

    assert len(cases) == 4
    assert len({case.case_id for case in cases}) == 4
    assert {
        (
            case.use_regime_thresholds,
            case.require_hybrid_score,
        )
        for case in cases
    } == {
        (False, False),
        (True, False),
        (False, True),
        (True, True),
    }


def test_current_hybrid_defaults_remain_enabled() -> None:
    model = HybridTrendDonchianEntryModel(mode="trend_context")

    assert model.use_regime_thresholds is True
    assert model.require_hybrid_score is True
    assert isinstance(model.donchian_model, DonchianBreakoutEntryModel)
    assert model.donchian_model.use_regime_thresholds is True


def test_legacy_hybrid_switches_are_explicit() -> None:
    model = HybridTrendDonchianEntryModel(
        mode="trend_context",
        use_regime_thresholds=False,
        require_hybrid_score=False,
    )

    assert model.donchian_model.use_regime_thresholds is False
    assert "legacy_thresholds" in model.name
    assert "no_hard_score" in model.name


def test_compound_return() -> None:
    result = compound_return(pd.Series([10.0, -10.0]))

    assert result == pytest.approx(-1.0)


class _TrendWatchlist:
    name = "trend_stub"

    def evaluate(self, **kwargs) -> dict:
        return {
            "status": "WATCHLIST",
            "score": 50,
            "reasons": [],
        }


class _DonchianPassed:
    name = "donchian_stub"
    use_regime_thresholds = False

    def evaluate(self, **kwargs) -> dict:
        return {
            "status": "PASSED",
            "score": 50,
            "conditions": {"breakout_20d": True},
            "reasons": [],
        }


def test_hard_score_switch_changes_only_the_gate() -> None:
    latest = pd.Series({
        "close": 100.0,
        "ATR14": 2.0,
        "EMA20": 98.0,
    })
    market_config = {
        "regime": "SIDEWAY",
        "watchlist_margin": 10,
        "atr_stop_multiplier": 2.0,
        "rr_ratio": 2.0,
    }

    legacy = HybridTrendDonchianEntryModel(
        mode="trend_context",
        trend_model=_TrendWatchlist(),
        donchian_model=_DonchianPassed(),
        require_hybrid_score=False,
    ).evaluate(latest, 0.0, market_config)
    current = HybridTrendDonchianEntryModel(
        mode="trend_context",
        trend_model=_TrendWatchlist(),
        donchian_model=_DonchianPassed(),
        require_hybrid_score=True,
    ).evaluate(latest, 0.0, market_config)

    assert legacy["status"] == "PASSED"
    assert current["status"] == "WATCHLIST"
    assert current["failed_conditions"] == ["hybrid_score"]
