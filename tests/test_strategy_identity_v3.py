"""Strategy Identity v3 (Phase 3B: B1–B4, shadow/additive only).

These tests pin the pure builder, the runtime collectors, the shadow wiring
and the additive persistence/research metadata. They never touch live
databases, providers or Telegram.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from config.strategy_loader import COMMON_CONFIG, REGIME_CONFIGS, load_strategy_config
from config.trading_policy import TradingPolicy
from quantlab import strategy_identity as sid
from quantlab.strategy_identity_runtime import (
    collect_frozen_research_signal_identity,
    collect_production_strategy_identity,
    shadow_strategy_identity,
)
from strategy.hybrid_trend_donchian_entry import HybridTrendDonchianEntryModel


ROOT = Path(__file__).resolve().parents[1]
Q70 = "Q70_FROZEN"
V3 = "V3_BREADTH_40_60"
HEX64 = set("0123456789abcdef")


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX64


@pytest.fixture
def clean_strategy_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Remove every strategy/paper override, then pin stores to temp paths."""
    for name in list(os.environ):
        if name.startswith(("PAPER_", "TRADING_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PAPER_V2_DATABASE_PATH", str(tmp_path / "v2.db"))
    monkeypatch.setenv("PAPER_V3_DATABASE_PATH", str(tmp_path / "v3.db"))
    return tmp_path


def _pinned(strategy: str) -> None:
    if strategy == V3:
        from scripts.run_paper_v3_lifecycle import configure_v3_environment

        configure_v3_environment()
    else:
        from scripts.run_paper_v2_lifecycle import configure_v2_environment

        configure_v2_environment()


def _base_contracts() -> tuple[dict, dict]:
    policy = TradingPolicy()
    signal = sid.build_signal_contract(
        eligibility={"path": "unit", "universe": "unit", "min_data_rows": 80},
        entry_model=HybridTrendDonchianEntryModel(),
        regime_configs=deepcopy(REGIME_CONFIGS),
        rsi_min=COMMON_CONFIG["rsi_min"],
        rsi_max=COMMON_CONFIG["rsi_max"],
        quality_threshold=0.70,
        quality_features=("score", "relative_strength_20d", "adx"),
        quality_universe="same_session_evaluations",
        market_state_min_history_sessions=50,
        breadth_exposure=None,
    )
    from backtesting.regime_policy import RegimePortfolioPolicy
    from execution.signal_executor import PaperExecutionConfig

    execution = sid.build_execution_contract(
        signal_level_policy=policy,
        executor_config=PaperExecutionConfig(),
        lifecycle_risk_limits={
            "maximum_position_pct": 20.0,
            "maximum_gross_exposure_pct": 80.0,
            "maximum_open_positions": 5,
            "maximum_daily_loss_pct": 3.0,
            "minimum_cash_buffer_pct": 5.0,
        },
        lifecycle_trailing_enabled=False,
        regime_portfolio_policy=RegimePortfolioPolicy(),
        ranking=("score", "relative_strength_20d", "volume_ratio", "adx"),
        executor_fill_policy=policy,
    )
    return signal, execution


def _build(signal: dict, execution: dict | None, strategy: str = Q70) -> sid.StrategyIdentityV3:
    return sid.build_strategy_identity(
        strategy=strategy, signal_contract=signal, execution_contract=execution
    )


# --------------------------------------------------------------------------
# B1: determinism, sensitivity, isolation, secrets
# --------------------------------------------------------------------------


def test_same_contract_yields_identical_identity() -> None:
    first = _build(*_base_contracts())
    second = _build(*_base_contracts())

    assert first.fingerprints() == second.fingerprints()
    assert all(_is_sha256(value) for value in first.fingerprints().values())
    assert first.as_dict() == second.as_dict()


def test_identity_is_independent_of_import_order_and_process(clean_strategy_env: Path) -> None:
    _pinned(Q70)
    in_process = collect_production_strategy_identity(Q70).fingerprints()
    script = (
        "import json, strategy.paper_v2_gate, execution.signal_executor\n"
        "from scripts.run_paper_v2_lifecycle import configure_v2_environment\n"
        "configure_v2_environment()\n"
        "from quantlab.strategy_identity_runtime import collect_production_strategy_identity\n"
        "print(json.dumps(collect_production_strategy_identity('Q70_FROZEN').fingerprints()))\n"
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PAPER_", "TRADING_"))
    }
    env["PAPER_V2_DATABASE_PATH"] = str(clean_strategy_env / "v2.db")
    env["PAPER_V3_DATABASE_PATH"] = str(clean_strategy_env / "v3.db")
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout.strip().splitlines()[-1]) == in_process


SIGNAL_MUTATIONS = {
    "regime_threshold": lambda s: s["regime_thresholds"]["BULL"].update(min_score=61.0),
    "entry_min_hybrid_score": lambda s: s["entry"]["model"].update(min_hybrid_score=61),
    "entry_mode": lambda s: s["entry"]["model"].update(mode="strict"),
    "rsi_band": lambda s: s["entry"].update(rsi_min=46.0),
    "quality_threshold": lambda s: s["quality_gate"].update(threshold=0.71),
    "quality_features": lambda s: s["quality_gate"].update(features=("score", "adx")),
    "eligibility": lambda s: s["eligibility"].update(min_data_rows=81),
    "breadth": lambda s: s.update(breadth_exposure={"full_exposure_min_pct": 60.0}),
}
EXECUTION_MUTATIONS = {
    "stop": lambda e: e["signal_levels"].update(stop_atr_multiplier=2.5),
    "fill_target": lambda e: e["executor_fill_policy"].update(target_atr_multiplier=6.0),
    "max_holding": lambda e: e["signal_levels"].update(maximum_holding_days=30),
    "max_open_positions": lambda e: e["lifecycle"]["risk_limits"].update(maximum_open_positions=10),
    "orders_per_scan": lambda e: e["executor"].update(maximum_orders_per_scan=4),
    "costs": lambda e: e["executor"].update(commission_rate=0.002),
    "trailing": lambda e: e["lifecycle"].update(trailing_enabled=True),
    "ranking": lambda e: e.update(ranking=("signal_score",)),
    "regime_caps": lambda e: e.update(regime_portfolio=None),
    "entry_selector": lambda e: e["signal_levels"].update(entry_model="trend"),
}


@pytest.mark.parametrize("name", sorted(SIGNAL_MUTATIONS))
def test_signal_field_change_moves_signal_and_strategy_identity_only(name: str) -> None:
    signal, execution = _base_contracts()
    base = _build(signal, execution)
    mutated_signal = deepcopy(signal)
    SIGNAL_MUTATIONS[name](mutated_signal)
    changed = _build(mutated_signal, execution)

    assert changed.signal_identity != base.signal_identity
    assert changed.strategy_identity_v3 != base.strategy_identity_v3
    assert changed.execution_identity == base.execution_identity


@pytest.mark.parametrize("name", sorted(EXECUTION_MUTATIONS))
def test_execution_field_change_moves_execution_and_strategy_identity_only(name: str) -> None:
    signal, execution = _base_contracts()
    base = _build(signal, execution)
    mutated_execution = deepcopy(execution)
    EXECUTION_MUTATIONS[name](mutated_execution)
    changed = _build(signal, mutated_execution)

    assert changed.execution_identity != base.execution_identity
    assert changed.strategy_identity_v3 != base.strategy_identity_v3
    assert changed.signal_identity == base.signal_identity


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("PAPER_DATABASE_PATH", "/elsewhere/generic.db"),
        ("PAPER_V2_DATABASE_PATH", "/elsewhere/v2.db"),
        ("PAPER_V3_DATABASE_PATH", "/elsewhere/v3.db"),
        ("MARKET_DATABASE_PATH", "/elsewhere/market.db"),
        ("PAPER_INITIAL_CASH", "123456789"),
        ("PAPER_TRADING_ENABLED", "false"),
        ("QUANT_RUN_ID", "run-42"),
        ("TELEGRAM_TOKEN", "changed-token"),
        ("TELEGRAM_CHAT_ID", "-100999"),
        ("LOG_LEVEL", "DEBUG"),
        ("PAPER_V2_QUALITY_THRESHOLD", "0.55"),  # inert: not read by the gate
    ),
)
def test_run_context_and_operational_values_do_not_move_identity(
    clean_strategy_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    _pinned(Q70)
    before = collect_production_strategy_identity(Q70).fingerprints()
    monkeypatch.setenv(name, value)
    after = collect_production_strategy_identity(Q70).fingerprints()

    assert after == before


def test_yaml_formatting_comments_and_unconsumed_keys_do_not_move_identity(tmp_path: Path) -> None:
    source = (ROOT / "config" / "strategy.yaml").read_text(encoding="utf-8")
    reformatted = "# reviewer comment\n\n" + source.replace(":", ":  ").replace("\n", "\n\n")
    reformatted_path = tmp_path / "reformatted.yaml"
    reformatted_path.write_text(reformatted, encoding="utf-8")
    original = load_strategy_config(ROOT / "config" / "strategy.yaml")
    reloaded = load_strategy_config(reformatted_path)

    assert sid.describe_regime_thresholds(reloaded["regimes"]) == sid.describe_regime_thresholds(
        original["regimes"]
    )
    # rr_ratio / atr_stop_multiplier are overwritten in production (class E).
    unconsumed = deepcopy(original["regimes"])
    unconsumed["BULL"]["rr_ratio"] = 9.9
    unconsumed["BULL"]["atr_stop_multiplier"] = 9.9
    assert sid.describe_regime_thresholds(unconsumed) == sid.describe_regime_thresholds(
        original["regimes"]
    )
    consumed = deepcopy(original["regimes"])
    consumed["SIDEWAY"]["max_return_3d"] = 14
    assert sid.describe_regime_thresholds(consumed) != sid.describe_regime_thresholds(
        original["regimes"]
    )


def test_secrets_never_enter_serialized_identity(
    clean_strategy_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets = {
        "TELEGRAM_TOKEN": "SECRET-TELEGRAM-7f3a",
        "TELEGRAM_BOT_TOKEN": "SECRET-BOT-91c2",
        "TELEGRAM_CHAT_ID": "SECRET-CHAT-5521",
        "VNSTOCK_API_KEY": "SECRET-PROVIDER-0bd4",
        "OPENAI_API_KEY": "SECRET-OTHER-77aa",
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    for strategy in (Q70, V3):
        _pinned(strategy)
        encoded = json.dumps(collect_production_strategy_identity(strategy).as_dict())
        for name, value in secrets.items():
            assert value not in encoded
            assert name not in encoded
        assert "database_path" not in encoded
        assert "initial_cash" not in encoded


def test_builder_rejects_missing_and_non_finite_values_instead_of_defaulting() -> None:
    signal, execution = _base_contracts()
    regimes = deepcopy(REGIME_CONFIGS)
    del regimes["BEAR"]["min_adx"]
    with pytest.raises(sid.StrategyIdentityError, match="BEAR.min_adx is missing"):
        sid.describe_regime_thresholds(regimes)
    regimes = deepcopy(REGIME_CONFIGS)
    regimes["BULL"]["min_score"] = float("nan")
    with pytest.raises(sid.StrategyIdentityError, match="finite"):
        sid.describe_regime_thresholds(regimes)
    with pytest.raises(sid.StrategyIdentityError, match="unsupported entry model"):
        sid.describe_entry_model(object())


def test_builder_module_is_pure_and_import_light() -> None:
    script = (
        "import sys\n"
        "before = set(sys.modules)\n"
        "import quantlab.strategy_identity\n"
        "loaded = set(sys.modules) - before\n"
        "bad = [m for m in loaded if m.split('.')[0] in "
        "{'strategy','execution','config','core','vnstock','sqlalchemy','pandas','app','scripts'}]\n"
        "print(sorted(bad))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == "[]"


# --------------------------------------------------------------------------
# Q70 / V3 contracts as constructed (observed, never enforced)
# --------------------------------------------------------------------------


def test_q70_default_runtime_records_hybrid_trend_context(clean_strategy_env: Path) -> None:
    _pinned(Q70)
    identity = collect_production_strategy_identity(Q70)
    entry = identity.signal_contract["entry"]["model"]

    assert entry["model"] == "hybrid_trend_donchian"
    assert entry["mode"] == "trend_context"
    assert entry["min_hybrid_score"] == 60
    assert identity.signal_contract["breadth_exposure"] is None
    assert identity.signal_contract["quality_gate"]["threshold"] == 0.70


def test_q70_hostile_environment_is_reported_accurately_and_not_rewritten(
    clean_strategy_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRADING_ENTRY_MODEL", "trend")
    monkeypatch.setenv("PAPER_ATR_TARGET_MULTIPLIER", "7.0")
    monkeypatch.setenv("TRADING_MAX_HOLDING_DAYS", "15")
    monkeypatch.setenv("PAPER_MAX_OPEN_POSITIONS", "4")
    _pinned(Q70)
    environment_before = dict(os.environ)

    identity = collect_production_strategy_identity(Q70)

    # The collector neither writes the environment nor "corrects" Q70.
    assert dict(os.environ) == environment_before
    assert os.environ["TRADING_ENTRY_MODEL"] == "trend"
    assert TradingPolicy.from_env().entry_model == "trend"
    # ...and it reports what was actually constructed.
    assert identity.signal_contract["entry"]["model"]["model"] == "trend_v1"
    assert identity.execution_contract["signal_levels"]["entry_model"] == "trend"
    assert identity.execution_contract["executor_fill_policy"]["target_atr_multiplier"] == 7.0
    assert identity.execution_contract["signal_levels"]["maximum_holding_days"] == 15
    assert identity.execution_contract["executor"]["maximum_open_positions"] == 4
    assert identity.execution_contract["lifecycle"]["risk_limits"]["maximum_open_positions"] == 4

    monkeypatch.delenv("TRADING_ENTRY_MODEL")
    hybrid = collect_production_strategy_identity(Q70)
    assert hybrid.signal_identity != identity.signal_identity


def test_v3_identity_represents_breadth_exposure_and_differs_only_there(
    clean_strategy_env: Path,
) -> None:
    _pinned(Q70)
    q70 = collect_production_strategy_identity(Q70)
    _pinned(V3)
    v3 = collect_production_strategy_identity(V3)
    breadth = v3.signal_contract["breadth_exposure"]

    assert breadth == {
        "version": "V3_BREADTH_40_60",
        "measure": "breadth_ema50_pct",
        "full_exposure_min_pct": 60.0,
        "half_exposure_min_pct": 40.0,
        "multiplier_at_full": 1.0,
        "multiplier_at_half": 0.5,
        "multiplier_below_half": 0.0,
        "multiplier_missing": 0.0,
        "applied_to": "order_quantity",
    }
    changed_signal_blocks = {
        key
        for key, value in v3.block_fingerprints.items()
        if key.startswith("signal.") and q70.block_fingerprints[key] != value
    }
    assert changed_signal_blocks == {"signal.breadth_exposure"}
    assert v3.signal_identity != q70.signal_identity


# Phase 3B goldens (holding 30, the runtime default when they were captured).
# They must still reproduce exactly when holding is explicitly 30: proof that
# the 2026-10-06 approved transition changed ONLY maximum holding.
GOLDEN_PHASE_3B_HOLDING_30 = {
    Q70: {
        "strategy_identity_v3": "680ee2fb783ec6024830175161856bf3b11374e5405d81bd1acf9e974cbb1aec",
        "signal_identity": "7a4c16c480ec6e14ee4f7f45e451b8a26b6223cbf06700152778b431fe2f0857",
        "execution_identity": "6d8fe10e3ef7b05f2eb27a1126a311d200be2dc8acd36ab1e818efd533576129",
    },
    V3: {
        "strategy_identity_v3": "bc54c7a2a8bcfbc4250d6a4675721ddb7336d3ed6f26334c95cb4106691e7aad",
        "signal_identity": "f03ae140e70b5f1a91320cf7421d591dc63fcda344ff1872040cc7ee9028bb7e",
        "execution_identity": "6d8fe10e3ef7b05f2eb27a1126a311d200be2dc8acd36ab1e818efd533576129",
    },
}
# Canonical goldens after the approved contract transition (holding 30 -> 20
# market sessions; owner decision 2026-10-06). Signal identities unchanged.
GOLDEN = {
    Q70: {
        "strategy_identity_v3": "b3b12579cc004637f37013067001111cc00d9b4aac3caa4021d68e7222854bf7",
        "signal_identity": "7a4c16c480ec6e14ee4f7f45e451b8a26b6223cbf06700152778b431fe2f0857",
        "execution_identity": "10851657f2e0ae907d9b86d10239e8dfe2b89d64ec813afe70b01c591792ebd8",
    },
    V3: {
        "strategy_identity_v3": "e4e2d4388b1bfefbed246082fbe56860e9c0d098802632631e3c6eba35612e1f",
        "signal_identity": "f03ae140e70b5f1a91320cf7421d591dc63fcda344ff1872040cc7ee9028bb7e",
        "execution_identity": "10851657f2e0ae907d9b86d10239e8dfe2b89d64ec813afe70b01c591792ebd8",
    },
}


@pytest.mark.parametrize("strategy", (Q70, V3))
def test_golden_identity_under_wrapper_pins_and_default_environment(
    clean_strategy_env: Path,
    strategy: str,
) -> None:
    """A change here must be a deliberate contract change, never drift."""
    _pinned(strategy)
    assert collect_production_strategy_identity(strategy).fingerprints() == GOLDEN[strategy]


@pytest.mark.parametrize("strategy", (Q70, V3))
def test_phase_3b_goldens_reproduce_with_explicit_holding_30(
    clean_strategy_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    strategy: str,
) -> None:
    monkeypatch.setenv("TRADING_MAX_HOLDING_DAYS", "30")
    _pinned(strategy)
    assert (
        collect_production_strategy_identity(strategy).fingerprints()
        == GOLDEN_PHASE_3B_HOLDING_30[strategy]
    )


def test_unresolved_owner_values_are_recorded_side_by_side_not_resolved(
    clean_strategy_env: Path,
) -> None:
    _pinned(Q70)
    execution = collect_production_strategy_identity(Q70).execution_contract

    # Design Q5: same variable, two production defaults. Both are recorded.
    assert execution["executor"]["maximum_open_positions"] == 10
    assert execution["lifecycle"]["risk_limits"]["maximum_open_positions"] == 5
    # Holding is now canonical 20 sessions; Q3/Q4 recorded as constructed.
    assert execution["signal_levels"]["maximum_holding_days"] == 20
    assert execution["regime_portfolio"]["rules"]["BEAR"]["allow_new_positions"] is False
    assert tuple(execution["ranking"]) == ("score", "relative_strength_20d", "volume_ratio", "adx")


# --------------------------------------------------------------------------
# Backward compatibility of legacy fingerprints
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entry_model", "holding", "expected"),
    (
        # Legacy candidates (holding 30): unchanged function, unchanged hashes.
        ("hybrid", "30", "072312b7aba83b2b1a0d0b8940a0bc7d12139aa85c084ae1b0cc535c699645e7"),
        ("trend", "30", "72f0a8fdb081ac70c9061af695e64241c0382acc1fae3e1fffd6b744ae88fb23"),
        # Canonical new-position fingerprint after the approved transition.
        ("hybrid", "20", "85ca6c14d76ecf836369aeecc2795f8ed100e8cfaf8b2e34b79474e3dd37ddb1"),
    ),
)
def test_executor_policy_fingerprint_is_byte_identical(
    clean_strategy_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry_model: str,
    holding: str,
    expected: str,
) -> None:
    monkeypatch.setenv("TRADING_ENTRY_MODEL", entry_model)
    monkeypatch.setenv("TRADING_MAX_HOLDING_DAYS", holding)
    _pinned(Q70)
    from execution.signal_executor import PaperSignalExecutor

    executor = PaperSignalExecutor.__new__(PaperSignalExecutor)
    executor.policy = replace(
        TradingPolicy.from_env(), stop_atr_multiplier=2.0, target_atr_multiplier=5.0
    )
    assert executor._policy_fingerprint() == expected
    assert (
        hashlib.sha256(
            json.dumps(asdict(executor.policy), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        == expected
    )


def test_runtime_configuration_v2_contract_is_unchanged(clean_strategy_env: Path) -> None:
    from quantctl.runtime_configuration import CONTRACT, VERSION, resolve_runtime_configuration

    _pinned(Q70)
    payload = resolve_runtime_configuration().as_dict()

    assert (CONTRACT, VERSION) == ("quantctl.runtime_configuration", "v2")
    assert not any("identity_v3" in key or key in {"signal_identity", "execution_identity"} for key in payload)


# --------------------------------------------------------------------------
# B2: shadow wiring never changes the scan
# --------------------------------------------------------------------------


def test_scan_contract_cannot_be_verified_fails_closed() -> None:
    from app.strategy_scan import enforce_scan_strategy_contract
    from quantlab.strategy_contract import StrategyContractError

    with pytest.raises(StrategyContractError, match="could not be verified"):
        enforce_scan_strategy_contract(Q70, SimpleNamespace(), SimpleNamespace())


def test_scan_contract_uses_scanner_constructed_objects(
    clean_strategy_env: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.strategy_scan import build_scan_processor, enforce_scan_strategy_contract

    _pinned(Q70)
    policy = TradingPolicy.from_env()
    scanner = SimpleNamespace(TRADING_POLICY=policy, strategy=policy.build_entry_model())
    identity, comparison = enforce_scan_strategy_contract(Q70, scanner, build_scan_processor(Q70))

    assert comparison.status == "CONTRACT_MATCHED"
    assert identity.fingerprints() == collect_production_strategy_identity(Q70).fingerprints()
    assert "Strategy contract: Q70_FROZEN CONTRACT_MATCHED" in capsys.readouterr().out


def test_shadow_wrapper_turns_any_failure_into_diagnostic() -> None:
    def boom() -> None:
        raise RuntimeError("unresolved")

    result = shadow_strategy_identity(boom)
    assert result.status == "INCOMPLETE"
    assert result.diagnostic == "RuntimeError: unresolved"
    assert result.fingerprints() == {
        "strategy_identity_v3": None,
        "signal_identity": None,
        "execution_identity": None,
    }


# --------------------------------------------------------------------------
# B3: additive persistence on NEW records only
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


# Computed with the pre-Phase-3B implementation for the same record.
PRE_V3_RECORD_IDENTITY = "ad01bb769a41317321c62df965a4bb43957b4c2ba4c1140c8dde833505b007ad"


def test_pre_v3_record_identity_and_payload_are_byte_identical() -> None:
    record = _record()

    assert record.record_identity == PRE_V3_RECORD_IDENTITY
    assert not {"strategy_identity_v3", "signal_identity", "execution_identity"} & set(record.as_dict())
    legacy_payload = json.loads(json.dumps(record.as_dict()))
    assert type(record).from_dict(legacy_payload).record_identity == PRE_V3_RECORD_IDENTITY


def test_new_record_carries_v3_fingerprints_and_ledger_reads_old_and_new(
    clean_strategy_env: Path,
    tmp_path: Path,
) -> None:
    from quantlab.evidence.prospective_portfolio import (
        ProspectivePortfolioEvidenceLedger,
        _strategy_identity_v3_kwargs,
    )

    _pinned(Q70)
    identity = collect_production_strategy_identity(Q70)
    prints = identity.fingerprints()
    old = _record()
    new = _record(
        observation_date="2026-10-01",
        market_data_reference_session="2026-10-01",
        **_strategy_identity_v3_kwargs(identity),
    )
    assert new.record_identity != PRE_V3_RECORD_IDENTITY
    ledger = ProspectivePortfolioEvidenceLedger(tmp_path / "evidence.db")
    assert ledger.append(old).created
    assert ledger.append(new).created

    stored = {item.observation_date: item for item in ledger.records()}
    assert stored["2026-09-30"].record_identity == PRE_V3_RECORD_IDENTITY
    assert stored["2026-09-30"].strategy_identity_v3 is None
    assert {name: getattr(stored["2026-10-01"], name) for name in prints} == prints
    # Re-appending the identical old record is still idempotent.
    assert not ledger.append(old).created


def test_v3_fields_must_be_sha256_when_supplied() -> None:
    with pytest.raises(ValueError, match="signal_identity must be a SHA-256"):
        _record(signal_identity="not-a-digest")


def test_lifecycle_fails_closed_before_any_paper_write_when_identity_unverifiable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests import test_prospective_portfolio_evidence_lifecycle as lifecycle_tests
    from scripts import run_paper_lifecycle
    import quantlab.strategy_identity_runtime as runtime
    from quantlab.strategy_contract import StrategyContractError, enforce_strategy_contract

    market, paper = tmp_path / "market.db", tmp_path / "paper.db"
    lifecycle_tests._market_database(market)
    calls = lifecycle_tests._patch_lifecycle(
        monkeypatch,
        market_database_path=market,
        paper_database_path=paper,
        capture=lambda **_kwargs: pytest.fail("evidence must not be captured"),
    )
    # Restore the real gate (the harness stubs it) and break identity collection.
    monkeypatch.setattr(run_paper_lifecycle, "enforce_strategy_contract", enforce_strategy_contract)
    monkeypatch.setattr(
        runtime,
        "collect_production_strategy_identity",
        lambda strategy: (_ for _ in ()).throw(RuntimeError("unresolved")),
    )
    with pytest.raises(StrategyContractError, match="could not be verified"):
        run_paper_lifecycle.main()

    assert "baseline-store" not in calls and "pre-cursor" not in calls
    assert "manager-init" not in calls and "manager-run" not in calls


# --------------------------------------------------------------------------
# B4: research signal identity metadata
# --------------------------------------------------------------------------


def test_research_signal_identity_matches_production_q70_signal_rules(
    clean_strategy_env: Path,
) -> None:
    from strategy.paper_v2_gate import QUALITY_FEATURES

    _pinned(Q70)
    production = collect_production_strategy_identity(Q70)
    research = collect_frozen_research_signal_identity(
        entry_model=HybridTrendDonchianEntryModel(),
        quality_threshold=0.70,
        quality_features=QUALITY_FEATURES,
        universe_mode="database_coverage",
        warmup_bars=60,
    )

    assert research.execution_identity is None and research.strategy_identity_v3 is None
    differing = {
        key
        for key, value in research.block_fingerprints.items()
        if production.block_fingerprints[key] != value
    }
    # Only the universe/eligibility path differs; every decision rule matches.
    assert differing == {"signal.eligibility"}


def test_frozen_evaluator_emits_signal_identity_without_changing_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backtesting import frozen_q70_evaluator as evaluator
    from strategy.paper_v2_gate import PaperV2QualityGate
    from tests import test_frozen_q70_evaluator as evaluator_tests

    rows = [evaluator_tests._row("AAA", passed=True, score=100)]
    candidate = [evaluator_tests._trade("AAA")]
    with monkeypatch.context() as patch:
        patch.setattr(PaperV2QualityGate, "_add_sector_rs_telemetry", lambda self, signal: dict(signal))
        (trades, metrics, equity), _ = evaluator_tests._run(patch, rows, candidate)
    with monkeypatch.context() as patch:
        patch.setattr(PaperV2QualityGate, "_add_sector_rs_telemetry", lambda self, signal: dict(signal))
        patch.setattr(evaluator, "_research_signal_identity_metadata", lambda **_kwargs: {})
        (base_trades, base_metrics, base_equity), _ = evaluator_tests._run(patch, rows, candidate)

    assert _is_sha256(metrics["signal_identity"])
    block = metrics["strategy_identity_v3"]
    assert block["status"] == "COMPLETE"
    assert block["scope"] == "signal_only"
    assert block["signal_identity"] == metrics["signal_identity"]
    assert block["signal_contract"]["entry"]["model"]["model"] == "hybrid_trend_donchian"
    # Numerical research outputs are unchanged; only additive keys appear.
    assert trades == base_trades
    assert equity.equals(base_equity)
    assert {
        key: value
        for key, value in metrics.items()
        if key not in {"signal_identity", "strategy_identity_v3"}
    } == base_metrics
    assert metrics["policy_fingerprint"] == "Q70_FROZEN/hybrid_trend_donchian/ATR2x5/fixed"
