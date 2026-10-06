"""Regression tests for the third adversarial review (four P2 findings).

P2-1 evidence contracts are immutable and re-validated at append;
P2-2 early v3 evidence formats stay readable without false verification;
P2-3 research evidence is semantically scoped per claim;
P2-4 CSV / value validation is type-aware and order-independent.
Temporary databases only; no provider, Telegram, market update or live store.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3

import pytest

from quantlab import strategy_contract as contract
from quantlab.strategy_identity_runtime import collect_production_strategy_identity


Q70 = "Q70_FROZEN"
FIXTURES = Path(__file__).parent / "fixtures" / "evidence_compat"
LEGACY_RECORD_IDENTITY = "ad01bb769a41317321c62df965a4bb43957b4c2ba4c1140c8dde833505b007ad"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for name in list(os.environ):
        if name.startswith(("PAPER_", "TRADING_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PAPER_V2_DATABASE_PATH", str(tmp_path / "v2.db"))
    monkeypatch.setenv("PAPER_V3_DATABASE_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("PAPER_DATABASE_PATH", str(tmp_path / "generic.db"))
    return tmp_path


def _record(**overrides):
    from quantlab.evidence.prospective_portfolio import ProspectivePortfolioEvidenceRecord

    values = dict(
        evidence_version="v1", strategy_identity="Q70_FROZEN", source_store_id="q70-frozen",
        source_store_identity="store-identity", source_account_epoch_id="epoch-1",
        runtime_configuration_fingerprint="runtime-fp", observation_date="2026-09-30",
        captured_at_utc="2026-09-30T10:00:00Z", paper_database_path="/x/paper.db",
        market_database_path="/x/market.db", market_database_sha256=None,
        market_data_reference_session="2026-09-30", benchmark_symbol="VNINDEX",
        benchmark_close=1300.5, market_regime_label="BULL", regime_computation_identity="regime-id",
        cash=100.0, positions_value=0.0, equity=100.0, realized_pnl=0.0, unrealized_pnl=0.0,
        gross_exposure_pct=0.0, open_position_count=0,
    )
    values.update(overrides)
    return ProspectivePortfolioEvidenceRecord(**values)


def _canonical_record(**overrides):
    from app.strategy_scan import configure_strategy_runtime

    configure_strategy_runtime(Q70)
    identity = collect_production_strategy_identity(Q70)
    return _record(**{**contract.evidence_attestation_fields(identity), **overrides})


def _ledger(tmp_path: Path):
    from quantlab.evidence.prospective_portfolio import ProspectivePortfolioEvidenceLedger

    return ProspectivePortfolioEvidenceLedger(tmp_path / "evidence.db")


# --------------------------------------------------------------------------
# P2-1 mutability / append re-validation
# --------------------------------------------------------------------------


def test_exported_dict_is_independent_and_internal_contracts_are_immutable(clean_env: Path) -> None:
    record = _canonical_record()
    exported = record.as_dict()
    exported["strategy_execution_contract"]["executor"]["slippage_bps"] = 10.0

    assert record.strategy_execution_contract["executor"]["slippage_bps"] == 5.0
    assert record.as_dict()["strategy_execution_contract"]["executor"]["slippage_bps"] == 5.0
    with pytest.raises(TypeError):
        record.strategy_execution_contract["executor"]["slippage_bps"] = 10.0  # type: ignore[index]


@pytest.mark.parametrize(
    ("block", "field", "value"),
    (
        ("executor", "slippage_bps", 10.0),
        ("executor_fill_policy", "maximum_holding_days", 30),
        ("signal_levels", "maximum_holding_days", 30),
    ),
)
def test_mutable_contract_injected_after_validation_is_rejected_before_persistence(
    clean_env: Path,
    tmp_path: Path,
    block: str,
    field: str,
    value,
) -> None:
    record = _canonical_record()
    injected = json.loads(json.dumps(record.as_dict()["strategy_execution_contract"]))
    object.__setattr__(record, "strategy_execution_contract", injected)  # bypass the frozen view
    injected[block][field] = value  # becomes non-canonical AFTER validation
    ledger = _ledger(tmp_path)

    with pytest.raises(ValueError, match="re-validation before append|no longer matches"):
        ledger.append(record)
    assert ledger.records() == ()


def test_tampered_cached_status_is_rejected_before_persistence(clean_env: Path, tmp_path: Path) -> None:
    record = _canonical_record()
    object.__setattr__(record, "strategy_contract_status", "CONTRACT_DEVIATION")
    ledger = _ledger(tmp_path)
    with pytest.raises(ValueError, match="re-validation before append"):
        ledger.append(record)
    assert ledger.records() == ()


def test_true_canonical_record_appends_and_round_trips(clean_env: Path, tmp_path: Path) -> None:
    record = _canonical_record()
    ledger = _ledger(tmp_path)
    assert ledger.append(record).created
    (stored,) = ledger.records()
    assert stored.record_identity == record.record_identity
    assert stored.strategy_contract_status == contract.CONTRACT_MATCHED
    assert stored.strategy_identity_format == "CONTRACT_COMPLETE_V3"
    assert not ledger.append(record).created  # idempotent


def test_legacy_record_append_and_read_are_unchanged(tmp_path: Path) -> None:
    record = _record()
    ledger = _ledger(tmp_path)
    assert ledger.append(record).created
    (stored,) = ledger.records()
    assert stored.record_identity == LEGACY_RECORD_IDENTITY
    assert stored.strategy_identity_format == "PRE_V3"


# --------------------------------------------------------------------------
# P2-2 early v3 compatibility
# --------------------------------------------------------------------------


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("name", "form", "historical_status"),
    (
        ("pre_v3_legacy.json", "PRE_V3", None),
        ("phase3b_shadow_fingerprints_only.json", "EARLY_V3_INCOMPLETE", None),
        ("phase3d_attested_without_contracts.json", "EARLY_V3_INCOMPLETE", "CONTRACT_MATCHED"),
    ),
)
def test_historical_formats_remain_readable_with_identical_identity(
    name: str,
    form: str,
    historical_status,
) -> None:
    from quantlab.evidence.prospective_portfolio import ProspectivePortfolioEvidenceRecord

    payload = _fixture(name)
    record = ProspectivePortfolioEvidenceRecord.from_dict(payload)

    assert record.record_identity == payload["record_identity"]  # payload identity preserved
    assert record.strategy_identity_format == form
    assert record.historical_contract_status == historical_status
    # Missing behavioral contracts are never invented.
    assert record.strategy_signal_contract is None and record.strategy_execution_contract is None
    assert "strategy_signal_contract" not in record.as_dict()
    if form == "EARLY_V3_INCOMPLETE":
        assert record.strategy_identity_verification == "LEGACY_INCOMPLETE_V3_IDENTITY"
        assert record.strategy_identity_provenance == (
            "LEGACY_UNVERIFIED", "LEGACY_INCOMPLETE_V3_IDENTITY",
        )
        assert contract.CONTRACT_MATCHED not in record.strategy_identity_provenance
    else:
        assert record.strategy_identity_provenance == ("LEGACY_UNVERIFIED", "NO_STRATEGY_IDENTITY_V3")


@pytest.mark.parametrize(
    "name",
    (
        "pre_v3_legacy.json",
        "phase3b_shadow_fingerprints_only.json",
        "phase3d_attested_without_contracts.json",
    ),
)
def test_ledger_reads_previously_persisted_historical_rows(tmp_path: Path, name: str) -> None:
    from quantlab.evidence.prospective_portfolio import (
        ProspectivePortfolioEvidenceLedger,
        ProspectivePortfolioEvidenceRecord,
    )

    ledger = ProspectivePortfolioEvidenceLedger(tmp_path / f"{name}.db")
    ledger.initialize()
    payload = _fixture(name)
    record = ProspectivePortfolioEvidenceRecord.from_dict(payload)
    with sqlite3.connect(ledger.database_path) as connection:
        # Persist exactly as earlier code wrote it (bypasses append).
        connection.execute(
            "INSERT INTO prospective_portfolio_evidence VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                ledger._evidence_key(record), payload["record_identity"],
                record.evidence_version, record.strategy_identity, record.source_store_id,
                record.source_store_identity, record.runtime_configuration_fingerprint,
                record.observation_date, record.captured_at_utc, json.dumps(payload),
            ),
        )
    (read,) = ledger.records()
    assert read.record_identity == payload["record_identity"]
    assert read.as_dict() == payload


def test_new_early_v3_records_cannot_be_appended(tmp_path: Path) -> None:
    from quantlab.evidence.prospective_portfolio import ProspectivePortfolioEvidenceRecord

    early = ProspectivePortfolioEvidenceRecord.from_dict(
        _fixture("phase3d_attested_without_contracts.json")
    )
    with pytest.raises(ValueError, match="early v3 format without behavioral contracts is read-only"):
        _ledger(tmp_path).append(early)


def test_partial_contract_maps_are_not_an_accepted_format(clean_env: Path) -> None:
    complete = _canonical_record()
    payload = complete.as_dict()
    payload.pop("strategy_execution_contract")
    payload.pop("record_identity")
    with pytest.raises(ValueError, match="v3 identity requires"):
        type(complete).from_dict(payload)


# --------------------------------------------------------------------------
# P2-3 semantically scoped research evidence / P2-4 typed, order-free CSV
# --------------------------------------------------------------------------


ARM = "database_coverage_50_history_5_staleness"
POLICY = {
    "name": "Q70_FROZEN", "entry_model": "hybrid_trend_donchian", "quality_enabled": True,
    "quality_threshold": 0.7, "stop_atr_multiplier": 2.0, "target_atr_multiplier": 5.0,
    "max_open_positions": 10,
}
EXECUTION = {
    "position_sizer": "atr_risk", "risk_per_trade_pct": 1.0, "atr_stop_multiplier": 2.0,
    "target_atr_multiplier": 5.0, "maximum_orders_per_scan": 3, "commission_rate": 0.0015,
    "slippage_bps": 5.0, "maximum_open_positions": 10, "sell_tax_rate": 0.001,
}


def _artifact(
    tmp_path: Path,
    name: str,
    fingerprint: dict,
    *,
    manifest_extra: dict | None = None,
    arm_csv: dict[str, str] | None = None,
    root_csv: dict[str, str] | None = None,
) -> Path:
    directory = tmp_path / "research_results" / name
    directory.mkdir(parents=True)
    (directory / "experiment_manifest.json").write_text(
        json.dumps(
            {
                "arms": [ARM],
                "policy_fingerprint": "Q70_FROZEN/hybrid_trend_donchian/ATR2x5/fixed",
                "q70_threshold": 0.7,
                **(manifest_extra or {}),
            }
        ),
        encoding="utf-8",
    )
    arm = directory / ARM
    arm.mkdir()
    (arm / "policy_fingerprint.json").write_text(json.dumps(fingerprint), encoding="utf-8")
    for filename, text in (arm_csv or {}).items():
        (arm / filename).write_text(text, encoding="utf-8")
    for filename, text in (root_csv or {}).items():
        (directory / filename).write_text(text, encoding="utf-8")
    return directory


def _attest(tmp_path: Path, name: str, fingerprint: dict, **kwargs) -> dict:
    from quantlab.research_attestation import build_frozen_q70_research_attestation

    artifact = _artifact(tmp_path, name, fingerprint, **kwargs)
    attestation = build_frozen_q70_research_attestation(artifact, root=tmp_path)
    return {item["claim"]: item for item in attestation["arms"][0]["proven_claims"]}


def _refused(tmp_path: Path, name: str, fingerprint: dict, **kwargs) -> str:
    from quantlab.research_attestation import ResearchAttestationError

    with pytest.raises(ResearchAttestationError) as error:
        _attest(tmp_path, name, fingerprint, **kwargs)
    return str(error.value)


def test_unrelated_benchmark_commission_does_not_contradict_strategy(tmp_path: Path) -> None:
    claims = _attest(
        tmp_path, "benchmark_fee",
        {"policy": POLICY, "paper_execution": EXECUTION,
         "buy_and_hold_benchmark": {"commission_rate": 0.003, "slippage_bps": 25.0}},
        manifest_extra={"benchmark": {"commission_rate": 0.003, "q70_threshold": 0.1}},
    )
    assert claims["commission_rate"]["status"] == "PROVEN"
    assert all("benchmark" not in source for source in claims["commission_rate"]["sources"])
    assert claims["quality_threshold"]["status"] == "PROVEN"


def test_actual_strategy_commission_difference_is_refused(tmp_path: Path) -> None:
    message = _refused(
        tmp_path, "strategy_fee",
        {"policy": POLICY, "paper_execution": {**EXECUTION, "commission_rate": 0.003}},
    )
    assert "paper_execution.commission_rate=0.003" in message


def test_example_config_alone_cannot_prove_max_orders(tmp_path: Path) -> None:
    execution = {key: value for key, value in EXECUTION.items() if key != "maximum_orders_per_scan"}
    claims = _attest(
        tmp_path, "example_only",
        {"policy": POLICY, "paper_execution": execution,
         "example_config": {"maximum_orders_per_scan": 3}},
        manifest_extra={"example": {"maximum_orders_per_scan": 3}},
    )
    assert claims["maximum_orders_per_scan"]["status"] == "UNPROVEN"
    assert claims["maximum_orders_per_scan"]["sources"] == []


def test_disagreeing_example_config_does_not_contradict_actual_evidence(tmp_path: Path) -> None:
    claims = _attest(
        tmp_path, "example_disagrees",
        {"policy": POLICY, "paper_execution": EXECUTION,
         "example_config": {"maximum_orders_per_scan": 99, "slippage_bps": 50.0}},
    )
    assert claims["maximum_orders_per_scan"]["status"] == "PROVEN"
    assert claims["slippage_bps"]["status"] == "PROVEN"


def test_all_legitimate_aliases_are_checked(tmp_path: Path) -> None:
    claims = _attest(
        tmp_path, "aliases",
        {"policy": POLICY, "frozen_q70": POLICY, "paper_execution": EXECUTION,
         "parity": EXECUTION, "paper_parity": EXECUTION},
    )
    prefix = f"{ARM}/policy_fingerprint.json:"
    assert {f"{prefix}policy.entry_model", f"{prefix}frozen_q70.entry_model"} <= set(
        claims["entry_model"]["sources"]
    )
    assert {f"{prefix}parity.slippage_bps", f"{prefix}paper_parity.slippage_bps"} <= set(
        claims["slippage_bps"]["sources"]
    )


def test_csv_evidence_participates_only_in_its_approved_context(tmp_path: Path) -> None:
    decisions = "symbol,quality_threshold\nAAA,0.7\nBBB,0.7\n"
    # In the approved decision ledger, a bad value contradicts...
    message = _refused(
        tmp_path, "csv_in_scope", {"policy": POLICY, "paper_execution": EXECUTION},
        arm_csv={"candidate_decision_oos.csv": "symbol,quality_threshold\nAAA,0.7\nBBB,0.1\n"},
    )
    assert "candidate_decision_oos.csv[1]:quality_threshold=0.1" in message
    # ...and a good value proves; a same-named column elsewhere is ignored.
    claims = _attest(
        tmp_path, "csv_out_of_scope", {"policy": POLICY, "paper_execution": EXECUTION},
        arm_csv={
            "candidate_decision_oos.csv": decisions,
            "benchmark_trades.csv": "symbol,quality_threshold,commission_rate\nX,0.1,0.01\n",
            "summary.csv": "arm,quality_threshold\n" + ARM + ",0.1\n",
        },
        root_csv={"comparison.csv": f"arm,policy_fingerprint,slippage_bps\n{ARM},Q70_FROZEN/hybrid_trend_donchian/ATR2x5/fixed,50\n"},
    )
    assert claims["quality_threshold"]["status"] == "PROVEN"
    assert f"{ARM}/candidate_decision_oos.csv:quality_threshold" in claims["quality_threshold"]["sources"]
    assert all("benchmark_trades" not in source for item in claims.values() for source in item["sources"])
    assert claims["slippage_bps"]["status"] == "PROVEN"


@pytest.mark.parametrize(
    ("values", "expected"),
    (
        ((True, 1), "CONTRADICTED"),
        ((1, True), "CONTRADICTED"),
        ((True, True), "PROVEN"),
        ((True, False), "CONTRADICTED"),
        ((1,), "CONTRADICTED"),
    ),
)
def test_bool_claims_are_type_aware_and_order_independent(values, expected) -> None:
    from quantlab.research_attestation import _validated_claims

    observations = {
        "quality_enabled": [(f"policy_fingerprint.json:policy.quality_enabled#{i}", v) for i, v in enumerate(values)]
    }
    claims, contradictions = _validated_claims(observations=observations, code={})
    status = next(item for item in claims if item["claim"] == "quality_enabled")["status"]
    assert status == expected
    assert bool(contradictions) is (expected != "PROVEN")


def test_number_and_bool_stay_distinct() -> None:
    from quantlab.research_attestation import _parse_csv_cell, _typed_match

    assert _typed_match("number", 0.7, 0.7)
    assert not _typed_match("number", 1.0, True)
    assert not _typed_match("bool", True, 1)
    assert not _typed_match("bool", True, 1.0)
    assert _parse_csv_cell("1") == 1 and type(_parse_csv_cell("1")) is int
    assert _parse_csv_cell("true") is True and _parse_csv_cell("1.0") == 1.0


def test_csv_row_order_never_changes_the_attestation(tmp_path: Path) -> None:
    rows = ["AAA,0.7", "BBB,0.70", "CCC,7e-1"]
    forward = _attest(
        tmp_path, "rows_forward", {"policy": POLICY, "paper_execution": EXECUTION},
        arm_csv={"candidate_decision_oos.csv": "symbol,quality_threshold\n" + "\n".join(rows) + "\n"},
    )
    reverse = _attest(
        tmp_path, "rows_reverse", {"policy": POLICY, "paper_execution": EXECUTION},
        arm_csv={"candidate_decision_oos.csv": "symbol,quality_threshold\n" + "\n".join(reversed(rows)) + "\n"},
    )
    assert forward == reverse
    bad = ["AAA,0.7", "BBB,True"]
    forward_error = _refused(
        tmp_path, "bad_forward", {"policy": POLICY, "paper_execution": EXECUTION},
        arm_csv={"candidate_decision_oos.csv": "symbol,quality_threshold\n" + "\n".join(bad) + "\n"},
    )
    reverse_error = _refused(
        tmp_path, "bad_reverse", {"policy": POLICY, "paper_execution": EXECUTION},
        arm_csv={"candidate_decision_oos.csv": "symbol,quality_threshold\n" + "\n".join(reversed(bad)) + "\n"},
    )
    assert "quality_threshold" in forward_error and "=True" in forward_error
    assert "quality_threshold" in reverse_error and "=True" in reverse_error
