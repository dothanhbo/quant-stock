from __future__ import annotations

import csv
import hashlib
import json
import sqlite3

import pytest

from quantlab.execution.price_provenance import (
    CONTRACT,
    CorporateActionEvidenceState,
    PriceAdjustmentState,
    PriceCapabilityState,
    PriceUnitState,
    assess_price_provenance,
)
from research.run_quantlab_execution_price_provenance import FILES, run_price_provenance_audit


def _result(**overrides):
    values = {"source_identities": {"market_snapshot_id": "snapshot", "source": "fixture"}}
    values.update(overrides)
    return assess_price_provenance(**values)


def test_canonical_unit_is_partial_and_conversion_convention_is_explicit_but_unverified() -> None:
    result = _result()
    assert result.contract_name == CONTRACT
    assert result.storage_unit_state == PriceUnitState.PARTIALLY_VERIFIED
    assert result.vnd_conversion_scale == 1000.0
    assert result.vnd_conversion_verified is False
    assert result.execution_notional_unit.startswith("UNVERIFIED_VND")
    assert result.monetary_capacity_gate == "MONETARY_CAPACITY_NOT_DEFENSIBLE"


def test_storage_unit_and_adjustment_evidence_are_independent() -> None:
    unit_only = _result(storage_unit_state=PriceUnitState.THOUSAND_VND_CANONICAL)
    assert unit_only.storage_unit_state == PriceUnitState.THOUSAND_VND_CANONICAL
    assert unit_only.adjustment_state == PriceAdjustmentState.UNKNOWN
    adjusted = _result(adjustment_state=PriceAdjustmentState.ADJUSTED_PRICE_VERIFIED)
    assert adjusted.adjustment_state == PriceAdjustmentState.ADJUSTED_PRICE_VERIFIED
    assert adjusted.storage_unit_state == PriceUnitState.PARTIALLY_VERIFIED


def test_missing_adjustment_metadata_cannot_become_raw_price_verified() -> None:
    result = _result()
    assert result.adjustment_state == PriceAdjustmentState.UNKNOWN
    assert any("no raw/adjusted marker" in item for item in result.adjustment_evidence)
    assert result.adjustment_state != PriceAdjustmentState.RAW_TRADABLE_PRICE_VERIFIED
    verified_unit_without_adjustment = _result(
        storage_unit_state=PriceUnitState.THOUSAND_VND_CANONICAL,
        vnd_conversion_verified=True,
    )
    assert verified_unit_without_adjustment.monetary_capacity_gate == "MONETARY_CAPACITY_NOT_DEFENSIBLE"


def test_verified_scale_conversion_requires_verified_unit_and_adjustment_semantics() -> None:
    result = _result(
        storage_unit_state=PriceUnitState.THOUSAND_VND_CANONICAL,
        vnd_conversion_scale=1000.0,
        vnd_conversion_verified=True,
        adjustment_state=PriceAdjustmentState.RAW_TRADABLE_PRICE_VERIFIED,
    )
    assert result.monetary_capacity_gate == "MONETARY_CAPACITY_READY"
    assert next(item for item in result.execution_capabilities if item.operation == "VND_order_notional").state == PriceCapabilityState.SUPPORTED


def test_missing_corporate_action_records_remain_unavailable() -> None:
    result = _result()
    assert set(result.corporate_action_capabilities) == {
        "stock_splits", "reverse_splits", "stock_dividends", "cash_dividends",
        "rights_issues", "bonus_shares", "ticker_changes", "quantity_adjustments",
    }
    assert set(result.corporate_action_capabilities.values()) == {CorporateActionEvidenceState.UNAVAILABLE}
    assert next(item for item in result.execution_capabilities if item.operation == "sequential_historical_share_holdings").state == PriceCapabilityState.UNSUPPORTED


def test_operation_capability_matrix_keeps_relative_price_gaps_distinct_from_monetary_claims() -> None:
    result = _result()
    matrix = {item.operation: item for item in result.execution_capabilities}
    assert matrix["same_date_relative_price_calculation"].state == PriceCapabilityState.SUPPORTED
    assert matrix["formation_close_to_next_open_percentage_gap"].state == PriceCapabilityState.SUPPORTED
    assert matrix["theoretical_share_quantity_from_notional_divided_by_price"].state == PriceCapabilityState.PARTIALLY_SUPPORTED
    assert matrix["daily_monetary_capacity_proxy"].state == PriceCapabilityState.UNSUPPORTED
    assert matrix["executable_historical_pnl"].state == PriceCapabilityState.UNSUPPORTED


def test_phase11b_percentage_gap_is_scale_invariant() -> None:
    close_a, open_a = 65.0, 66.3
    close_b, open_b = close_a * 1000, open_a * 1000
    assert open_a / close_a - 1 == pytest.approx(open_b / close_b - 1)
    assert any("dimensionless" in item for item in _result().phase11b_reconciliation)


def test_material_unit_ambiguity_fails_monetary_capacity_closed() -> None:
    result = _result(storage_unit_state=PriceUnitState.AMBIGUOUS, vnd_conversion_scale=None)
    assert result.monetary_capacity_gate == "MONETARY_CAPACITY_NOT_DEFENSIBLE"
    matrix = {item.operation: item for item in result.execution_capabilities}
    assert matrix["VND_order_notional"].state == PriceCapabilityState.UNSUPPORTED
    assert matrix["theoretical_share_quantity_from_notional_divided_by_price"].state == PriceCapabilityState.UNSUPPORTED


def test_provenance_validation_and_identity_are_deterministic_and_sensitive() -> None:
    with pytest.raises(ValueError, match="source identities are required"):
        assess_price_provenance(source_identities={})
    with pytest.raises(ValueError, match="scale must be positive"):
        _result(vnd_conversion_scale=float("nan"))
    first = _result()
    second = _result(source_identities={"source": "fixture", "market_snapshot_id": "snapshot"})
    changed = _result(adjustment_state=PriceAdjustmentState.PARTIALLY_VERIFIED)
    assert first.identity == second.identity
    assert first.identity != changed.identity
    with pytest.raises(TypeError):
        first.source_identities["x"] = "y"  # type: ignore[index]


def test_runner_reads_database_read_only_and_writes_compact_hashed_artifacts(tmp_path) -> None:
    db = tmp_path / "market.db"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE prices (symbol TEXT, time TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER)")
        connection.executemany("INSERT INTO prices VALUES (?,?,?,?,?,?,?)", [
            ("AAA", "2024-01-02", 10, 11, 9, 10.5, 1000),
            ("VNINDEX", "2024-01-02", 100, 101, 99, 100.5, 2000),
        ])
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    output = tmp_path / "out"
    result, summary, actual_output = run_price_provenance_audit(database_path=db, output_root=output)
    after = hashlib.sha256(db.read_bytes()).hexdigest()
    assert before == after
    assert actual_output == output.resolve()
    assert {path.name for path in output.iterdir()} == set(FILES)
    manifest = json.loads((output / FILES[1]).read_text(encoding="utf-8"))
    with (output / FILES[0]).open(encoding="utf-8", newline="") as handle:
        capabilities = list(csv.DictReader(handle))
    assert manifest["result_identity"] == result.identity
    assert manifest["market"]["row_count"] == 2
    assert manifest["market"]["persisted_provider_adjustment_or_unit_fields"] == []
    assert manifest["market"]["corporate_action_related_tables"] == []
    assert len(capabilities) == len(result.execution_capabilities)
    for name in (FILES[0], FILES[2]):
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == manifest["artifacts"][name]["sha256"]
    assert summary["distinct_normalized_symbols"] == 2
