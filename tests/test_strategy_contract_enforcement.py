"""B5-A research attestation, B5-C future paper attestation and B6 fail-closed
enforcement of the canonical Q70/V3 contract (owner decisions 2026-10-06).

No live database, provider, Telegram, backtest or WFO is touched.
"""

from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from quantlab import strategy_contract as contract
from quantlab.strategy_identity_runtime import collect_production_strategy_identity
from tests.scan_parity_support import (  # noqa: F401 - fixture import
    Recorder,
    fresh_scanner,
    isolate_environment,
    restore_scanner_module,
    wrap_processors,
)


ROOT = Path(__file__).resolve().parents[1]
Q70 = "Q70_FROZEN"
V3 = "V3_BREADTH_40_60"
SIDE_EFFECTS = (
    "paper_runtime_init",
    "telemetry_write",
    "signal_row_write",
    "paper_queue",
    "telegram_send",
)

pytestmark = pytest.mark.usefixtures("restore_scanner_module")


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for name in list(os.environ):
        if name.startswith(("PAPER_", "TRADING_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PAPER_V2_DATABASE_PATH", str(tmp_path / "v2.db"))
    monkeypatch.setenv("PAPER_V3_DATABASE_PATH", str(tmp_path / "v3.db"))
    return tmp_path


def _pin(strategy: str) -> None:
    if strategy == V3:
        from scripts.run_paper_v3_lifecycle import configure_v3_environment

        configure_v3_environment()
    else:
        from scripts.run_paper_v2_lifecycle import configure_v2_environment

        configure_v2_environment()


# --------------------------------------------------------------------------
# Canonical contract
# --------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", (Q70, V3))
def test_canonical_runtime_matches_canonical_contract(clean_env: Path, strategy: str) -> None:
    _pin(strategy)
    comparison = contract.compare_to_canonical(collect_production_strategy_identity(strategy))

    assert comparison.status == contract.CONTRACT_MATCHED
    assert comparison.deviations == ()
    assert comparison.regime_overlay == contract.REGIME_CAPS_V1


def test_canonical_execution_is_exactly_the_owner_approved_list() -> None:
    expected = {
        "stop_atr_multiplier": 2.0, "target_atr_multiplier": 5.0, "trailing": False,
        "timing": "next_open", "sizer": "atr_risk", "risk": 1.0, "orders": 3,
        "position": 20.0, "gross": 80.0, "open": 10, "buffer": 5.0, "daily_loss": 3.0,
        "lot": 100, "commission": 0.0015, "slippage": 5.0, "sell_tax": 0.001, "holding": 20,
    }
    c = contract.CANONICAL_EXECUTION
    assert c["signal_levels.stop_atr_multiplier"] == c["executor.atr_stop_multiplier"] == c[
        "executor_fill_policy.stop_atr_multiplier"] == expected["stop_atr_multiplier"]
    assert c["signal_levels.target_atr_multiplier"] == c["executor.target_atr_multiplier"] == c[
        "executor_fill_policy.target_atr_multiplier"] == expected["target_atr_multiplier"]
    assert c["lifecycle.trailing_enabled"] is expected["trailing"]
    assert c["signal_levels.execution_timing"] == expected["timing"]
    assert c["executor.position_sizer"] == c["signal_levels.position_sizer"] == expected["sizer"]
    assert c["executor.risk_per_trade_pct"] == expected["risk"]
    assert c["executor.maximum_orders_per_scan"] == expected["orders"]
    assert c["executor.maximum_position_pct"] == expected["position"]
    assert c["executor.maximum_gross_exposure_pct"] == expected["gross"]
    assert c["executor.maximum_open_positions"] == expected["open"]
    assert c["executor.minimum_cash_buffer_pct"] == expected["buffer"]
    assert c["executor.maximum_daily_loss_pct"] == expected["daily_loss"]
    assert c["executor.lot_size"] == expected["lot"]
    assert c["executor.commission_rate"] == expected["commission"]
    assert c["executor.slippage_bps"] == expected["slippage"]
    assert c["executor.sell_tax_rate"] == expected["sell_tax"]
    assert c["signal_levels.maximum_holding_days"] == c[
        "executor_fill_policy.maximum_holding_days"] == expected["holding"]
    # Explicitly NOT enforced (recorded only): no hidden enforcement.
    for path in (
        "executor.maximum_order_adtv20_pct",
        "executor.fixed_fraction_pct",
        "signal_levels.fixed_fraction_pct",
        "lifecycle.risk_limits",
        "regime_portfolio",
        "ranking",
    ):
        assert path not in c
        assert path in contract.RECORDED_NOT_ENFORCED


def test_canonical_signal_snapshot_is_the_phase_3b_signal_identity() -> None:
    from quantlab.strategy_identity import build_strategy_identity

    for strategy, expected in (
        (Q70, "7a4c16c480ec6e14ee4f7f45e451b8a26b6223cbf06700152778b431fe2f0857"),
        (V3, "f03ae140e70b5f1a91320cf7421d591dc63fcda344ff1872040cc7ee9028bb7e"),
    ):
        identity = build_strategy_identity(
            strategy=strategy,
            signal_contract=contract.canonical_signal_contract(strategy),
            execution_contract=None,
        )
        assert identity.signal_identity == expected
    q70 = contract.canonical_signal_contract(Q70)
    assert q70["entry"]["model"]["model"] == "hybrid_trend_donchian"
    assert q70["entry"]["model"]["mode"] == "trend_context"
    assert q70["quality_gate"]["threshold"] == 0.70
    assert q70["breadth_exposure"] is None


# --------------------------------------------------------------------------
# B6: fail closed BEFORE any side effect, through the canonical scan boundary
# --------------------------------------------------------------------------


def _set_env(name: str, value: str):
    def apply(monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(name, value)

    return apply


def _yaml_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    from config.strategy_loader import REGIME_CONFIGS

    monkeypatch.setitem(REGIME_CONFIGS["BULL"], "min_score", 61)


def _gate_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.strategy_scan.Q70_QUALITY_THRESHOLD", 0.65)


def _gate_features(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("strategy.paper_v2_gate.QUALITY_FEATURES", ("score", "adx"))


def _breadth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("strategy.breadth_exposure.BREADTH_FULL_EXPOSURE_PCT", 65.0)


DRIFT_CASES = {
    # category: (strategy, apply, expected deviation path fragment)
    "entry_model": (Q70, _set_env("TRADING_ENTRY_MODEL", "trend"), "signal.entry.model"),
    "yaml_threshold": (Q70, _yaml_threshold, "signal.regime_thresholds.BULL.min_score"),
    "gate_threshold": (Q70, _gate_threshold, "signal.quality_gate.threshold"),
    "gate_definition": (Q70, _gate_features, "signal.quality_gate.features"),
    "breadth": (V3, _breadth, "signal.breadth_exposure.full_exposure_min_pct"),
    "fill_target": (Q70, _set_env("PAPER_ATR_TARGET_MULTIPLIER", "6.0"), "executor.target_atr_multiplier"),
    "timing_holding": (Q70, _set_env("TRADING_MAX_HOLDING_DAYS", "30"), "maximum_holding_days"),
    "sizing": (Q70, _set_env("PAPER_RISK_PER_TRADE_PCT", "2.0"), "executor.risk_per_trade_pct"),
    "limits": (V3, _set_env("PAPER_MAX_OPEN_POSITIONS", "12"), "executor.maximum_open_positions"),
    "orders": (Q70, _set_env("PAPER_MAX_ORDERS_PER_SCAN", "5"), "executor.maximum_orders_per_scan"),
    "costs_slippage": (Q70, _set_env("PAPER_SLIPPAGE_BPS", "10"), "executor.slippage_bps"),
    "costs_commission": (V3, _set_env("PAPER_COMMISSION_RATE", "0.002"), "executor.commission_rate"),
}


@pytest.mark.parametrize("case", sorted(DRIFT_CASES))
def test_drift_fails_closed_before_any_signal_paper_or_telegram_side_effect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: str,
) -> None:
    from scripts import run_daily, run_paper_v2_lifecycle, run_paper_v3_lifecycle

    strategy, apply, fragment = DRIFT_CASES[case]
    isolate_environment(monkeypatch, tmp_path, strategy, hostile=False)
    monkeypatch.setattr(run_daily, "load_dotenv", lambda *a, **k: False)
    apply(monkeypatch)
    recorder = Recorder()
    wrap_processors(monkeypatch, recorder)
    monkeypatch.setattr(
        "app.strategy_scan._default_scanner_loader",
        lambda: fresh_scanner(monkeypatch, recorder, breadth=80.0),
    )
    (run_paper_v3_lifecycle if strategy == V3 else run_paper_v2_lifecycle).main()

    with pytest.raises(contract.StrategyContractMismatch) as error:
        run_daily.run_strategy_scanner(pending_execution_result=None)

    assert any(fragment in path for path in error.value.comparison.deviation_paths)
    assert not set(SIDE_EFFECTS) & set(recorder.events)
    assert recorder.queued == [] and recorder.messages == [] and recorder.saved_signals == []


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("PAPER_DATABASE_PATH", "/elsewhere/generic.db"),
        ("PAPER_V2_DATABASE_PATH", "/elsewhere/v2.db"),
        ("MARKET_DATABASE_PATH", "/elsewhere/market.db"),
        ("QUANT_LOG_DIR", "/elsewhere/logs"),
        ("TELEGRAM_TOKEN", "changed-token"),
        ("CHAT_ID", "-100999"),
        ("QUANT_OPERATION_RUN_ID", "run-42"),
        ("PAPER_INITIAL_CASH", "123456789"),
        ("PAPER_MAX_ORDER_ADTV20_PCT", "2.5"),  # CONFIGURABLE_BUT_RECORDED
        ("PAPER_FIXED_FRACTION_PCT", "33"),  # NOT_APPLICABLE under atr_risk
    ),
)
def test_operational_and_recorded_only_settings_never_trigger_mismatch(
    clean_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(name, value)
    _pin(Q70)
    _identity, comparison = contract.enforce_strategy_contract(Q70)
    assert comparison.status == contract.CONTRACT_MATCHED


def test_canonical_scan_still_runs_and_records_the_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import run_daily, run_paper_v2_lifecycle

    isolate_environment(monkeypatch, tmp_path, Q70)  # Phase 2 hostile env
    monkeypatch.setattr(run_daily, "load_dotenv", lambda *a, **k: False)
    recorder = Recorder()
    wrap_processors(monkeypatch, recorder)
    monkeypatch.setattr(
        "app.strategy_scan._default_scanner_loader",
        lambda: fresh_scanner(monkeypatch, recorder, breadth=80.0),
    )
    run_paper_v2_lifecycle.main()
    run_daily.run_strategy_scanner(pending_execution_result=None)

    assert "telegram_send" in recorder.events
    assert "Strategy contract: Q70_FROZEN CONTRACT_MATCHED" in capsys.readouterr().out


def test_lifecycle_drift_fails_closed_before_first_paper_store_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from config.trading_policy import TradingPolicy
    from scripts import run_paper_lifecycle
    from scripts.run_paper_v2_lifecycle import configure_v2_environment
    from tests import test_prospective_portfolio_evidence_lifecycle as lifecycle_tests

    original_from_env = TradingPolicy.__dict__["from_env"]
    for name in list(os.environ):
        if name.startswith(("PAPER_", "TRADING_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PAPER_V2_DATABASE_PATH", str(tmp_path / "v2.db"))
    configure_v2_environment()
    monkeypatch.setenv("PAPER_SLIPPAGE_BPS", "10")
    market, paper = tmp_path / "market.db", tmp_path / "paper.db"
    lifecycle_tests._market_database(market)
    calls = lifecycle_tests._patch_lifecycle(
        monkeypatch,
        market_database_path=market,
        paper_database_path=paper,
        capture=lambda **_kwargs: pytest.fail("no evidence on mismatch"),
    )
    # Use the real gate and the real policy resolution.
    monkeypatch.setattr(run_paper_lifecycle, "enforce_strategy_contract", contract.enforce_strategy_contract)
    monkeypatch.setattr(TradingPolicy, "from_env", original_from_env)

    with pytest.raises(contract.StrategyContractMismatch, match="executor.slippage_bps"):
        run_paper_lifecycle.main()
    assert not {"baseline-store", "pre-cursor", "manager-init", "manager-run"} & set(calls)


def test_lifecycle_literal_non_true_trailing_flag_now_fails_closed(clean_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pin(Q70)
    monkeypatch.setenv("PAPER_V2_DISABLE_TRAILING", "1")  # legacy parser: trailing ON
    with pytest.raises(contract.StrategyContractMismatch, match="lifecycle.trailing_enabled"):
        contract.enforce_strategy_contract(Q70)


# --------------------------------------------------------------------------
# Holding: research and paper time exits are the same point
# --------------------------------------------------------------------------


def test_twenty_session_time_exit_fills_at_the_same_point_in_research_and_paper() -> None:
    from backtesting.engine import _simulate_exit
    from backtesting.exit_models import ATRExitModel
    from execution.exit_engine import ExitEngine
    from execution.exit_models import ExitBar, PositionExitState
    from execution.lifecycle_manager import PaperLifecycleManager

    sessions = [date(2026, 1, 5) + timedelta(days=i) for i in range(30)]
    closes = [100.0 + 0.1 * i for i in range(30)]
    frame = pd.DataFrame(
        {
            "time": [item.isoformat() for item in sessions],
            "open": closes, "high": [c + 0.2 for c in closes],
            "low": [c - 0.2 for c in closes], "close": closes, "ATR14": [2.0] * 30,
        }
    )
    research = _simulate_exit(
        price_df=frame, entry_index=0,
        config=SimpleNamespace(max_holding_days=20, stop_loss_pct=5.0, take_profit_pct=10.0),
        exit_model=ATRExitModel(stop_atr_multiplier=2.0, target_atr_multiplier=5.0),
    )
    assert research.exit_index == 20
    assert research.exit_price == pytest.approx(closes[20])

    engine = ExitEngine()
    state = PositionExitState(
        symbol="AAA", entry_date=sessions[0], entry_price=closes[0], quantity=100,
        stop_price=closes[0] - 4.0, take_profit_price=closes[0] + 10.0,
        maximum_holding_days=20,
    )
    decisions = {}
    for index in (19, 20):
        held = PaperLifecycleManager._count_holding_sessions(
            sessions=sessions, entry_date=sessions[0], valuation_date=sessions[index]
        )
        bar = ExitBar(
            symbol="AAA", valuation_date=sessions[index], open_price=closes[index],
            high_price=closes[index] + 0.2, low_price=closes[index] - 0.2, close_price=closes[index],
        )
        decisions[index] = (held, engine.evaluate(state=state, bar=bar, holding_sessions=held))
    assert decisions[19][0] == 19 and not decisions[19][1].should_exit
    held, decision = decisions[20]
    assert held == 20 and decision.should_exit
    assert decision.reason.value == "TIME_EXIT"
    # Same session (entry + 20) and same price (that session's close).
    assert decision.valuation_date == research.exit_date.date()
    assert decision.execution_price == pytest.approx(research.exit_price)


# --------------------------------------------------------------------------
# B5-C: future paper attestation on NEW records only
# --------------------------------------------------------------------------


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


def test_new_record_carries_matched_contract_attestation(clean_env: Path, tmp_path: Path) -> None:
    from quantlab.evidence.prospective_portfolio import ProspectivePortfolioEvidenceLedger

    _pin(Q70)
    identity, _comparison = contract.enforce_strategy_contract(Q70)
    fields = contract.evidence_attestation_fields(identity)
    record = _record(observation_date="2026-10-07", market_data_reference_session="2026-10-07", **fields)

    assert record.strategy_contract_status == contract.CONTRACT_MATCHED
    assert record.strategy_contract_deviations == ()
    assert record.execution_overlay == contract.REGIME_CAPS_V1
    assert record.strategy_identity_provenance == (
        contract.STRATEGY_IDENTITY_V3_RECORDED,
        contract.CONTRACT_MATCHED,
    )
    ledger = ProspectivePortfolioEvidenceLedger(tmp_path / "evidence.db")
    old = _record()
    assert ledger.append(old).created and ledger.append(record).created
    stored = {item.observation_date: item for item in ledger.records()}
    assert stored["2026-10-07"].strategy_contract_status == contract.CONTRACT_MATCHED
    assert stored["2026-10-07"].strategy_identity_v3 == identity.strategy_identity_v3
    # The old record is readable, unchanged, and NOT reinterpreted as canonical.
    assert stored["2026-09-30"].record_identity == old.record_identity
    assert stored["2026-09-30"].strategy_identity_provenance == (
        "LEGACY_UNVERIFIED",
        "NO_STRATEGY_IDENTITY_V3",
    )


def test_changed_contract_field_is_recorded_as_deviation_with_field(clean_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pin(Q70)
    monkeypatch.setenv("PAPER_COMMISSION_RATE", "0.002")
    identity = collect_production_strategy_identity(Q70)
    fields = contract.evidence_attestation_fields(identity)
    record = _record(**fields)

    assert record.strategy_contract_status == contract.CONTRACT_DEVIATION
    assert record.strategy_contract_deviations == ("execution.executor.commission_rate",)
    assert record.strategy_identity_provenance[-1] == contract.CONTRACT_DEVIATION


def test_inconsistent_contract_attestation_is_rejected(clean_env: Path) -> None:
    _pin(Q70)
    fields = contract.evidence_attestation_fields(collect_production_strategy_identity(Q70))
    with pytest.raises(ValueError, match="CONTRACT_MATCHED must have no deviations"):
        _record(**{**fields, "strategy_contract_deviations": ("x",)})
    # A status without contracts is the early-v3 (read-only) format: it stays
    # readable but is never treated as a verified CONTRACT_MATCHED.
    early = _record(
        strategy_contract_version="v", strategy_contract_status="CONTRACT_MATCHED",
        strategy_contract_deviations=(), execution_overlay="REGIME_CAPS_V1",
    )
    assert early.strategy_identity_provenance == ("LEGACY_UNVERIFIED", "LEGACY_INCOMPLETE_V3_IDENTITY")
    with pytest.raises(ValueError, match="status is invalid"):
        _record(**{**fields, "strategy_contract_status": "MAYBE"})


def test_pre_v3_record_identity_is_still_byte_identical() -> None:
    assert _record().record_identity == "ad01bb769a41317321c62df965a4bb43957b4c2ba4c1140c8dde833505b007ad"


# --------------------------------------------------------------------------
# B5-A: research attestation
# --------------------------------------------------------------------------


def _write_artifact(directory: Path, *, entry_model: str = "hybrid_trend_donchian") -> None:
    directory.mkdir(parents=True)
    (directory / "experiment_manifest.json").write_text(
        json.dumps(
            {
                "arms": ["database_coverage_50_history_5_staleness"],
                "latest_vnindex_date": "2026-09-17",
                "policy_fingerprint": "Q70_FROZEN/hybrid_trend_donchian/ATR2x5/fixed",
                "q70_threshold": 0.7,
            }
        ),
        encoding="utf-8",
    )
    arm = directory / "database_coverage_50_history_5_staleness"
    arm.mkdir()
    (arm / "policy_fingerprint.json").write_text(
        json.dumps(
            {
                "policy": {"entry_model": entry_model, "quality_threshold": 0.7,
                           "stop_atr_multiplier": 2.0, "target_atr_multiplier": 5.0},
                "parity": {"position_sizer": "atr_risk", "risk_per_trade_pct": 1.0,
                           "maximum_open_positions": 10, "commission_rate": 0.0015,
                           "slippage_bps": 5.0, "sell_tax_rate": 0.001},
            }
        ),
        encoding="utf-8",
    )
    (arm / "summary.csv").write_text("total_return_pct\n12.5\n", encoding="utf-8")


def _tree_hashes(directory: Path) -> dict[str, str]:
    return {
        item.relative_to(directory).as_posix(): sha256(item.read_bytes()).hexdigest()
        for item in sorted(directory.rglob("*"))
        if item.is_file()
    }


def test_research_attestation_preserves_and_references_legacy_identity(tmp_path: Path) -> None:
    from quantlab.research_attestation import (
        RECONSTRUCTED_FROM_CODE_AND_ARTIFACT,
        append_attestation,
        build_frozen_q70_research_attestation,
    )

    artifact = tmp_path / "research_results" / "frozen_q70_test"
    _write_artifact(artifact)
    before = _tree_hashes(artifact)
    first = build_frozen_q70_research_attestation(artifact, root=tmp_path)
    second = build_frozen_q70_research_attestation(artifact, root=tmp_path)

    assert _tree_hashes(artifact) == before  # artifact untouched
    assert first == second  # deterministic
    assert first["label"] == RECONSTRUCTED_FROM_CODE_AND_ARTIFACT
    assert first["artifact"]["file_sha256"] == before  # original hashes preserved
    arm = first["arms"][0]
    assert arm["legacy_identity"]["policy_label"] == "Q70_FROZEN/hybrid_trend_donchian/ATR2x5/fixed"
    assert arm["legacy_identity"]["policy_fingerprint_file_sha256"] == before[
        "database_coverage_50_history_5_staleness/policy_fingerprint.json"
    ]
    assert arm["reconstructed"]["blocks_differing_from_canonical_production_q70"] == ["signal.eligibility"]
    claims = {claim["claim"]: claim for claim in arm["proven_claims"]}
    assert all(claims[name]["proven"] for name in ("entry_model", "quality_enabled", "quality_threshold"))
    # This fixture records no paper_execution block: max orders is narrowed
    # to UNPROVEN instead of being asserted.
    assert claims["maximum_orders_per_scan"]["status"] == "UNPROVEN"
    # Unsupported claims are absent: no execution/paper identity, scope signal-only.
    assert first["scope"] == "signal_only"
    assert "execution_identity" not in json.dumps(first)
    assert any("not claimed" in item for item in first["not_proven"])

    registry = tmp_path / "registry.jsonl"
    assert append_attestation(first, registry) is True
    assert append_attestation(second, registry) is False  # append-only, idempotent
    assert len(registry.read_text(encoding="utf-8").splitlines()) == 1


def test_research_attestation_refuses_unsupported_artifact(tmp_path: Path) -> None:
    from quantlab.research_attestation import ResearchAttestationError, build_frozen_q70_research_attestation

    artifact = tmp_path / "research_results" / "not_q70"
    _write_artifact(artifact, entry_model="trend")
    with pytest.raises(ResearchAttestationError, match="contradicts claims: entry_model"):
        build_frozen_q70_research_attestation(artifact, root=tmp_path)


def test_committed_v4_research_attestation_is_reproducible_when_artifact_present() -> None:
    from quantlab.research_attestation import (
        ATTESTATION_VERSION,
        DEFAULT_REGISTRY_PATH,
        build_frozen_q70_research_attestation,
    )

    lines = DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8").splitlines()
    recorded = [json.loads(line) for line in lines if line.strip()]
    v4 = [item for item in recorded if item["artifact"]["path"].endswith("v4_instrumented")]
    # Append-only: superseded v1/v2/v3 lines are kept; exactly one current line.
    assert [item["version"] for item in v4] == ["v1", "v2", "v3", ATTESTATION_VERSION]
    current = v4[-1]
    artifact = ROOT / current["artifact"]["path"]
    if not artifact.is_dir():
        pytest.skip("v4 research artifact is not present in this checkout")
    rebuilt = build_frozen_q70_research_attestation(artifact)
    assert rebuilt["artifact"]["file_sha256"] == current["artifact"]["file_sha256"]
    assert rebuilt["attestation_id"] == current["attestation_id"]
    assert v4[0]["artifact"]["file_sha256"] == current["artifact"]["file_sha256"]
