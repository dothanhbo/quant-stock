from __future__ import annotations

from dataclasses import FrozenInstanceError
import csv
import json
from pathlib import Path
import subprocess
import sys

import pytest

from quantlab.execution import (
    CAPABILITY_MATRIX,
    NEUTRAL_EXECUTION_FRICTION_V1,
    CapabilityState,
    ConstraintImplementationState,
    FrictionProvenance,
    OrderIntentState,
    OrderSide,
    ParticipationState,
    TheoreticalOrderIntent,
    build_execution_foundation_result,
    describe_volume_participation,
    translate_target_portfolio,
)
from research.run_quantlab_execution_foundation import FILES, run_execution_foundation


def _translation(**overrides):
    values = {
        "formation_date": "2026-09-25",
        "reference_price_date": "2026-09-25",
        "portfolio_equity": 1_000_000.0,
        "current_cash": 400_000.0,
        "current_holdings": {"AAA": 100, "CCC": 20},
        "target_weights": {"AAA": 0.3, "BBB": 0.2},
        "reference_prices": {"AAA": 2_000.0, "BBB": 4_000.0, "CCC": 5_000.0},
    }
    values.update(overrides)
    return translate_target_portfolio(**values)


def _intent(result, symbol: str):
    return next(item for item in result.intents if item.symbol == symbol)


def test_deterministic_target_translation_buy_sell_and_reconciliation() -> None:
    result = _translation()
    assert [item.symbol for item in result.intents] == ["AAA", "BBB", "CCC"]
    assert _intent(result, "AAA").side is OrderSide.BUY
    assert _intent(result, "BBB").side is OrderSide.BUY
    assert _intent(result, "CCC").side is OrderSide.SELL
    assert _intent(result, "AAA").theoretical_share_delta == pytest.approx(50.0)
    assert _intent(result, "CCC").theoretical_share_delta == pytest.approx(-20.0)
    assert result.target_risky_weight + result.target_cash_weight == pytest.approx(1.0)
    reordered = _translation(current_holdings={"CCC": 20, "AAA": 100}, target_weights={"BBB": .2, "AAA": .3}, reference_prices={"CCC": 5000, "BBB": 4000, "AAA": 2000})
    assert reordered == result


def test_no_unintended_security_and_no_silent_quantity_rounding() -> None:
    result = _translation(target_weights={"AAA": .333333})
    assert {item.symbol for item in result.intents} == {"AAA", "CCC"}
    intent = _intent(result, "AAA")
    assert isinstance(intent, TheoreticalOrderIntent)
    assert intent.theoretical_target_quantity == pytest.approx(166.6665)
    assert intent.theoretical_target_quantity % 100 != 0
    assert "no lot" in result.limitations[0]


def test_missing_and_nonpositive_prices_are_explicitly_unavailable() -> None:
    missing = _translation(reference_prices={"AAA": 2000, "CCC": 5000})
    assert _intent(missing, "BBB").state is OrderIntentState.MISSING_REFERENCE_PRICE
    assert _intent(missing, "BBB").theoretical_share_delta is None
    invalid = _translation(reference_prices={"AAA": 2000, "BBB": 0, "CCC": 5000})
    assert _intent(invalid, "BBB").state is OrderIntentState.NONPOSITIVE_REFERENCE_PRICE


def test_long_only_input_validation_and_no_lookahead_reference_price() -> None:
    with pytest.raises(ValueError, match="sum to at most one"):
        _translation(target_weights={"AAA": .7, "BBB": .4})
    with pytest.raises(ValueError, match="nonnegative"):
        _translation(target_weights={"AAA": -.1})
    with pytest.raises(ValueError, match="cannot be after formation"):
        _translation(reference_price_date="2026-09-28")


def test_theoretical_intent_is_not_an_executable_order_or_fill() -> None:
    intent = _intent(_translation(), "AAA")
    assert type(intent).__name__ == "TheoreticalOrderIntent"
    assert not hasattr(intent, "filled_quantity")
    assert not hasattr(intent, "average_fill_price")
    assert intent.price_semantics == "known_reference_price_only_not_a_fill_price"


def test_lot_tick_band_and_tradability_are_not_silently_applied() -> None:
    capabilities = {item.capability: item for item in CAPABILITY_MATRIX}
    assert capabilities["board_lot"].implementation_state is ConstraintImplementationState.CURRENT_OPERATIONAL_ONLY
    for name in ("tick_size", "price_band", "tradability_status"):
        assert capabilities[name].implementation_state is ConstraintImplementationState.EVIDENCE_UNAVAILABLE
    assert _intent(_translation(target_weights={"AAA": .333333}), "AAA").theoretical_target_quantity == pytest.approx(166.6665)


def test_daily_volume_participation_is_descriptive_only_and_causal() -> None:
    intent = _intent(_translation(), "AAA")
    diagnostic = describe_volume_participation(intent, daily_volume=10_000, volume_date="2026-09-25")
    assert diagnostic.state is ParticipationState.AVAILABLE_DESCRIPTIVE_ONLY
    assert diagnostic.participation_pct == pytest.approx(.5)
    assert "NOT_FILL_PROBABILITY" in diagnostic.interpretation
    with pytest.raises(ValueError, match="cannot be after formation"):
        describe_volume_participation(intent, daily_volume=10_000, volume_date="2026-09-28")


def test_unavailable_volume_and_market_impact_remain_unavailable() -> None:
    intent = _intent(_translation(), "AAA")
    diagnostic = describe_volume_participation(intent, daily_volume=None, volume_date="2026-09-25")
    assert diagnostic.state is ParticipationState.VOLUME_UNAVAILABLE
    impact = next(item for item in CAPABILITY_MATRIX if item.capability == "market_impact")
    assert impact.state is CapabilityState.UNAVAILABLE
    assert NEUTRAL_EXECUTION_FRICTION_V1.market_impact.provenance is FrictionProvenance.UNAVAILABLE
    assert NEUTRAL_EXECUTION_FRICTION_V1.market_impact.rate_bps is None


def test_friction_components_keep_separate_unavailable_provenance() -> None:
    components = (
        NEUTRAL_EXECUTION_FRICTION_V1.commission_fee,
        NEUTRAL_EXECUTION_FRICTION_V1.sell_side_tax,
        NEUTRAL_EXECUTION_FRICTION_V1.spread_slippage,
        NEUTRAL_EXECUTION_FRICTION_V1.market_impact,
    )
    assert [item.name for item in components] == ["commission_fee", "sell_side_tax", "spread_slippage", "market_impact"]
    assert all(item.provenance is FrictionProvenance.UNAVAILABLE and item.rate_bps is None for item in components)


def test_deterministic_identities_immutable_contracts_and_output_order() -> None:
    first = _translation(); second = _translation()
    assert first == second and first.identity == second.identity
    assert build_execution_foundation_result() == build_execution_foundation_result()
    with pytest.raises(FrozenInstanceError): first.portfolio_equity = 2  # type: ignore[misc]


def test_runner_writes_only_compact_capability_artifacts(tmp_path: Path) -> None:
    first = tmp_path / "first"; second = tmp_path / "second"
    result = run_execution_foundation(output_root=first)
    run_execution_foundation(output_root=second)
    assert {path.name for path in first.iterdir()} == set(FILES)
    rows = list(csv.DictReader((first / FILES[0]).open(encoding="utf-8")))
    manifest = json.loads((first / FILES[1]).read_text(encoding="utf-8"))
    assert len(rows) == len(result.capabilities)
    assert manifest["result_identity"] == result.identity
    assert manifest["market_impact_model"] == "EVIDENCE_UNAVAILABLE"
    assert manifest["no_operational_execution"] is True
    assert (first / FILES[0]).read_bytes() == (second / FILES[0]).read_bytes()
    assert (first / FILES[1]).read_bytes() == (second / FILES[1]).read_bytes()


def test_fresh_import_has_no_operational_or_data_side_effect_modules() -> None:
    code = (
        "import sys; import quantlab.execution; "
        "forbidden={'execution.paper_broker','execution.signal_executor','core.database','backtesting.portfolio_simulator'}; "
        "loaded=forbidden.intersection(sys.modules); assert not loaded, sorted(loaded)"
    )
    completed = subprocess.run([sys.executable, "-c", code], check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
