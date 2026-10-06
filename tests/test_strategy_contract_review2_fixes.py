"""Regression tests for the second adversarial review (three P2 findings).

P2-1 evidence status is derived from the recorded behavioral contracts;
P2-2 research attestation checks every persisted source of every claim;
P2-3 the retained executor's ACTUAL position sizer is validated.
Temporary databases only; no provider, Telegram, market update or live store.
"""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path

import pytest

from quantlab import strategy_contract as contract
from quantlab.strategy_identity_runtime import collect_production_strategy_identity
from tests.scan_parity_support import (  # noqa: F401 - fixture import
    REFERENCE_DATE,
    Recorder,
    fresh_scanner,
    isolate_environment,
    restore_scanner_module,
    wrap_processors,
)


Q70 = "Q70_FROZEN"
V3 = "V3_BREADTH_40_60"
SIDE_EFFECTS = {"telemetry_write", "signal_row_write", "paper_queue", "telegram_send"}
LEGACY_RECORD_IDENTITY = "ad01bb769a41317321c62df965a4bb43957b4c2ba4c1140c8dde833505b007ad"

pytestmark = pytest.mark.usefixtures("restore_scanner_module")


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for name in list(os.environ):
        if name.startswith(("PAPER_", "TRADING_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PAPER_V2_DATABASE_PATH", str(tmp_path / "v2.db"))
    monkeypatch.setenv("PAPER_V3_DATABASE_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("PAPER_DATABASE_PATH", str(tmp_path / "generic.db"))
    return tmp_path


def _pin(strategy: str) -> None:
    from app.strategy_scan import configure_strategy_runtime

    configure_strategy_runtime(strategy)


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


def _identities(monkeypatch: pytest.MonkeyPatch):
    """(canonical, 10 bps slippage) identities of the same Q70 runtime."""
    _pin(Q70)
    canonical = collect_production_strategy_identity(Q70)
    monkeypatch.setenv("PAPER_SLIPPAGE_BPS", "10")
    slippage_10 = collect_production_strategy_identity(Q70)
    monkeypatch.delenv("PAPER_SLIPPAGE_BPS")
    assert canonical.signal_identity == slippage_10.signal_identity
    assert canonical.execution_identity != slippage_10.execution_identity
    return canonical, slippage_10


def _coherent_fields(identity) -> dict:
    """Coherent fingerprints + contracts for ``identity`` (no status)."""
    return {
        **identity.fingerprints(),
        "strategy_signal_contract": json.loads(json.dumps(identity.as_dict()["signal"])),
        "strategy_execution_contract": json.loads(json.dumps(identity.as_dict()["execution"])),
    }


# --------------------------------------------------------------------------
# P2-1 evidence binding
# --------------------------------------------------------------------------


def test_true_canonical_contract_is_matched(clean_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    canonical, _ = _identities(monkeypatch)
    record = _record(**contract.evidence_attestation_fields(canonical))

    assert record.strategy_contract_status == contract.CONTRACT_MATCHED
    assert record.strategy_contract_deviations == ()
    assert record.strategy_identity_provenance == (
        contract.STRATEGY_IDENTITY_V3_RECORDED,
        contract.CONTRACT_MATCHED,
    )


def test_ten_bps_execution_is_never_representable_as_matched(
    clean_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical, slippage_10 = _identities(monkeypatch)
    derived = contract.evidence_attestation_fields(slippage_10)
    assert derived["strategy_contract_status"] == contract.CONTRACT_DEVIATION
    assert derived["strategy_contract_deviations"] == ("execution.executor.slippage_bps",)
    assert _record(**derived).strategy_contract_status == contract.CONTRACT_DEVIATION

    stale_matched = {
        key: value
        for key, value in contract.evidence_attestation_fields(canonical).items()
        if key.startswith(("strategy_contract_", "execution_overlay"))
    }
    # Coherent 10 bps contracts/fingerprints + stale MATCHED metadata.
    with pytest.raises(ValueError, match="contradicts the status CONTRACT_DEVIATION derived"):
        _record(**_coherent_fields(slippage_10), **stale_matched)


def test_stale_execution_fingerprint_in_otherwise_canonical_contract_map_is_detected(
    clean_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical, slippage_10 = _identities(monkeypatch)
    # Identity object whose contract map is 10 bps but whose fingerprints are
    # the canonical (stale) ones.
    stale = replace(canonical, execution_contract=slippage_10.execution_contract)
    with pytest.raises(contract.StrategyContractError, match="stale or edited"):
        contract.evidence_attestation_fields(stale)
    # Same at the record: the stored execution contract no longer produces
    # the stored execution fingerprint.
    matched = contract.evidence_attestation_fields(canonical)
    forged = {**matched, "strategy_execution_contract": _coherent_fields(slippage_10)["strategy_execution_contract"]}
    with pytest.raises(ValueError, match="does not match the fingerprint recomputed"):
        _record(**forged)


def test_mixed_signal_and_execution_fingerprints_from_two_runs_are_rejected(
    clean_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical, slippage_10 = _identities(monkeypatch)
    matched = contract.evidence_attestation_fields(canonical)
    with pytest.raises(ValueError, match="does not match the fingerprint recomputed"):
        _record(**{**matched, "execution_identity": slippage_10.execution_identity})
    monkeypatch.setenv("TRADING_ENTRY_MODEL", "trend")
    trend = collect_production_strategy_identity(Q70)
    with pytest.raises(ValueError, match="does not match the fingerprint recomputed"):
        _record(**{**matched, "signal_identity": trend.signal_identity})


@pytest.mark.parametrize("paths", ([], [""], ["   "], ("execution.executor.slippage_bps", " ")))
def test_deviation_status_with_empty_or_blank_paths_is_rejected(
    clean_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    paths,
) -> None:
    _, slippage_10 = _identities(monkeypatch)
    derived = contract.evidence_attestation_fields(slippage_10)
    with pytest.raises(ValueError, match="requires the deviating field paths|non-blank strings"):
        _record(**{**derived, "strategy_contract_deviations": paths})


@pytest.mark.parametrize(
    "paths",
    (
        ("execution.executor.commission_rate",),  # invented
        ("execution.executor.slippage_bps", "execution.executor.commission_rate"),  # extra
        ("signal.entry",),  # unrelated
    ),
)
def test_caller_supplied_false_deviation_paths_cannot_override_derived_paths(
    clean_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    paths,
) -> None:
    _, slippage_10 = _identities(monkeypatch)
    derived = contract.evidence_attestation_fields(slippage_10)
    with pytest.raises(ValueError, match="do not equal the deviations derived"):
        _record(**{**derived, "strategy_contract_deviations": paths})


def test_unknown_contract_version_cannot_carry_a_status(clean_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    canonical, _ = _identities(monkeypatch)
    matched = contract.evidence_attestation_fields(canonical)
    with pytest.raises(ValueError, match="unknown strategy contract version"):
        _record(**{**matched, "strategy_contract_version": "made-up"})


def test_recorded_contracts_round_trip_and_are_reverified_on_read(
    clean_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from quantlab.evidence.prospective_portfolio import (
        ProspectivePortfolioEvidenceLedger,
        _strategy_identity_v3_kwargs,
    )

    _, slippage_10 = _identities(monkeypatch)
    record = _record(observation_date="2026-10-07", market_data_reference_session="2026-10-07",
                     **_strategy_identity_v3_kwargs(slippage_10))
    ledger = ProspectivePortfolioEvidenceLedger(tmp_path / "evidence.db")
    assert ledger.append(record).created
    (stored,) = ledger.records()
    assert stored.record_identity == record.record_identity
    assert stored.strategy_contract_status == contract.CONTRACT_DEVIATION
    assert stored.strategy_contract_deviations == ("execution.executor.slippage_bps",)


def test_legacy_record_without_v3_metadata_is_unchanged() -> None:
    record = _record()
    assert record.record_identity == LEGACY_RECORD_IDENTITY
    assert type(record).from_dict(json.loads(json.dumps(record.as_dict()))).record_identity == LEGACY_RECORD_IDENTITY
    assert record.strategy_identity_provenance == ("LEGACY_UNVERIFIED", "NO_STRATEGY_IDENTITY_V3")


# --------------------------------------------------------------------------
# P2-2 research attestation checks every persisted source
# --------------------------------------------------------------------------


ARM = "database_coverage_50_history_5_staleness"
CANONICAL_POLICY = {
    "name": "Q70_FROZEN", "entry_model": "hybrid_trend_donchian", "quality_enabled": True,
    "quality_threshold": 0.7, "stop_atr_multiplier": 2.0, "target_atr_multiplier": 5.0,
    "max_open_positions": 10,
}
CANONICAL_EXECUTION = {
    "position_sizer": "atr_risk", "risk_per_trade_pct": 1.0, "atr_stop_multiplier": 2.0,
    "target_atr_multiplier": 5.0, "maximum_orders_per_scan": 3, "commission_rate": 0.0015,
    "slippage_bps": 5.0, "maximum_open_positions": 10, "sell_tax_rate": 0.001,
}


def _artifact(directory: Path, fingerprint: dict, *, csv_threshold: str | None = None) -> Path:
    directory.mkdir(parents=True)
    (directory / "experiment_manifest.json").write_text(
        json.dumps(
            {
                "arms": [ARM],
                "policy_fingerprint": "Q70_FROZEN/hybrid_trend_donchian/ATR2x5/fixed",
                "q70_threshold": 0.7,
            }
        ),
        encoding="utf-8",
    )
    arm = directory / ARM
    arm.mkdir()
    (arm / "policy_fingerprint.json").write_text(json.dumps(fingerprint), encoding="utf-8")
    if csv_threshold is not None:
        (arm / "candidate_decision_oos.csv").write_text(
            f"symbol,quality_threshold\nAAA,0.7\nBBB,{csv_threshold}\n", encoding="utf-8"
        )
    return directory


def _build(tmp_path: Path, name: str, fingerprint: dict, **kwargs):
    from quantlab.research_attestation import build_frozen_q70_research_attestation

    artifact = _artifact(tmp_path / "research_results" / name, fingerprint, **kwargs)
    return build_frozen_q70_research_attestation(artifact, root=tmp_path)


@pytest.mark.parametrize(
    ("name", "fingerprint", "fragment"),
    (
        ("policy_ok_frozen_bad",
         {"policy": CANONICAL_POLICY, "frozen_q70": {**CANONICAL_POLICY, "entry_model": "trend"},
          "paper_execution": CANONICAL_EXECUTION},
         "frozen_q70.entry_model"),
        ("frozen_ok_policy_bad",
         {"policy": {**CANONICAL_POLICY, "quality_threshold": 0.1}, "frozen_q70": CANONICAL_POLICY,
          "paper_execution": CANONICAL_EXECUTION},
         "policy.quality_threshold"),
        ("parity_ok_paper_parity_bad",
         {"policy": CANONICAL_POLICY, "paper_execution": CANONICAL_EXECUTION,
          "parity": CANONICAL_EXECUTION, "paper_parity": {**CANONICAL_EXECUTION, "slippage_bps": 10.0}},
         "paper_parity.slippage_bps"),
        ("paper_parity_ok_parity_bad",
         {"policy": CANONICAL_POLICY, "paper_execution": CANONICAL_EXECUTION,
          "parity": {**CANONICAL_EXECUTION, "maximum_orders_per_scan": 99},
          "paper_parity": CANONICAL_EXECUTION},
         "parity.maximum_orders_per_scan"),
        ("trailing_enabled_true",
         {"policy": CANONICAL_POLICY, "paper_execution": CANONICAL_EXECUTION, "trailing_enabled": True},
         "trailing_enabled"),
        ("quality_disabled_in_alias",
         {"policy": CANONICAL_POLICY, "frozen_q70": {**CANONICAL_POLICY, "quality_enabled": False},
          "paper_execution": CANONICAL_EXECUTION},
         "frozen_q70.quality_enabled"),
        ("timing_wrong",
         {"policy": CANONICAL_POLICY, "paper_execution": {**CANONICAL_EXECUTION, "execution_timing": "close"}},
         "execution_timing"),
    ),
)
def test_any_contradicting_source_refuses_attestation(
    tmp_path: Path,
    name: str,
    fingerprint: dict,
    fragment: str,
) -> None:
    from quantlab.research_attestation import ResearchAttestationError

    with pytest.raises(ResearchAttestationError, match=f"contradicts claims: .*{fragment}"):
        _build(tmp_path, name, fingerprint)


def test_contradicting_value_in_a_csv_column_refuses_attestation(tmp_path: Path) -> None:
    from quantlab.research_attestation import ResearchAttestationError

    with pytest.raises(ResearchAttestationError, match=r"candidate_decision_oos.csv\[1\]:quality_threshold=0.1"):
        _build(
            tmp_path, "csv_threshold",
            {"policy": CANONICAL_POLICY, "paper_execution": CANONICAL_EXECUTION},
            csv_threshold="0.1",
        )


def test_one_source_missing_another_supports_and_all_agreeing(tmp_path: Path) -> None:
    # paper_execution absent; parity alone records max orders -> PROVEN by it.
    attestation = _build(
        tmp_path, "parity_only",
        {"policy": CANONICAL_POLICY, "frozen_q70": CANONICAL_POLICY,
         "parity": {**CANONICAL_EXECUTION, "execution_timing": "next_open"},
         "paper_parity": CANONICAL_EXECUTION, "trailing_enabled": False},
        csv_threshold="0.7",
    )
    claims = {item["claim"]: item for item in attestation["arms"][0]["proven_claims"]}
    assert claims["maximum_orders_per_scan"]["status"] == "PROVEN"
    assert set(claims["maximum_orders_per_scan"]["sources"]) == {
        f"{ARM}/policy_fingerprint.json:parity.maximum_orders_per_scan",
        f"{ARM}/policy_fingerprint.json:paper_parity.maximum_orders_per_scan",
    }
    assert f"{ARM}/policy_fingerprint.json:frozen_q70.entry_model" in claims["entry_model"]["sources"]
    assert f"{ARM}/policy_fingerprint.json:trailing_enabled" in claims["trailing_enabled"]["sources"]
    assert f"{ARM}/candidate_decision_oos.csv:quality_threshold" in claims["quality_threshold"]["sources"]
    assert all(item["status"] == "PROVEN" for item in claims.values())


# --------------------------------------------------------------------------
# P2-3 actual retained position sizer
# --------------------------------------------------------------------------


def _retained_scanner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, recorder: Recorder, *, sizer=None):
    from execution.signal_executor import PaperExecutionConfig, PaperSignalExecutor

    isolate_environment(monkeypatch, tmp_path, Q70, hostile=False)
    _pin(Q70)
    scanner = fresh_scanner(
        monkeypatch, recorder, breadth=80.0, real_executor=True, signal_date=REFERENCE_DATE
    )
    config = PaperExecutionConfig.from_env()
    executor = (
        PaperSignalExecutor(config) if sizer is None
        else PaperSignalExecutor(config, position_sizer=sizer)
    )
    original_queue = executor.queue_signals

    def recording_queue(*args, **kwargs):
        recorder.events.append("paper_queue")
        return original_queue(*args, **kwargs)

    monkeypatch.setattr(executor, "queue_signals", recording_queue)
    monkeypatch.setattr(scanner, "paper_signal_executor", executor)
    return scanner, executor


def test_retained_executor_with_actual_atr_risk_sizer_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.strategy_scan import _verify_retained_executor
    from backtesting.position_sizers import AtrRiskSizer

    scanner, executor = _retained_scanner(monkeypatch, tmp_path, Recorder())
    assert type(executor.position_sizer) is AtrRiskSizer
    assert executor.config.position_sizer == "atr_risk"
    _verify_retained_executor(scanner, Q70)


@pytest.mark.parametrize(
    ("label", "sizer_factory", "fragment"),
    (
        ("fixed_fraction_behind_atr_label", lambda: __import__(
            "backtesting.position_sizers", fromlist=["FixedFractionSizer"]
        ).FixedFractionSizer(position_size_pct=20.0), "FixedFractionSizer"),
        ("atr_risk_2pct", lambda: __import__(
            "backtesting.position_sizers", fromlist=["AtrRiskSizer"]
        ).AtrRiskSizer(risk_per_trade_pct=2.0, atr_stop_multiplier=2.0, max_position_size_pct=20.0),
         "risk_per_trade_pct=2.0"),
        ("atr_risk_wrong_cap", lambda: __import__(
            "backtesting.position_sizers", fromlist=["AtrRiskSizer"]
        ).AtrRiskSizer(risk_per_trade_pct=1.0, atr_stop_multiplier=2.0, max_position_size_pct=50.0),
         "max_position_size_pct=50.0"),
    ),
)
def test_retained_sizer_that_differs_from_its_config_fails_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    label: str,
    sizer_factory,
    fragment: str,
) -> None:
    from app.strategy_scan import ScanConfigurationError, run_strategy_scan

    recorder = Recorder()
    wrap_processors(monkeypatch, recorder)
    scanner, executor = _retained_scanner(monkeypatch, tmp_path, recorder, sizer=sizer_factory())
    assert executor.config.position_sizer == "atr_risk"  # the label says atr_risk

    with pytest.raises(ScanConfigurationError, match=f"position sizer .*{fragment}"):
        run_strategy_scan(strategy_identity=Q70, scanner_loader=lambda: scanner)
    assert not SIDE_EFFECTS & set(recorder.events)


def test_dormant_fixed_fraction_value_is_not_enforced_while_atr_risk_is_active(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.strategy_scan import _verify_retained_executor

    monkeypatch.setenv("PAPER_FIXED_FRACTION_PCT", "33")
    scanner, _executor = _retained_scanner(monkeypatch, tmp_path, Recorder())
    _verify_retained_executor(scanner, Q70)  # no exception
