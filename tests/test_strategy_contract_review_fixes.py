"""Regression tests for the 2026-10-06 adversarial review of B5/B6.

P1-1 Daily preflight, P1-2 retained executor, P1-3 historical holding
migration, P2-1 research attestation overclaims, P2-2 evidence binding.
Temporary databases only; no provider, Telegram, market update or live store.
"""

from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
import sqlite3

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


# --------------------------------------------------------------------------
# P1-1 Daily preflight: zero Daily-side writes before a contract failure
# --------------------------------------------------------------------------


def _record_daily_stages(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    from scripts import run_daily
    from tests.test_daily_pipeline import _pass_integrity

    import app.strategy_scan as service

    original_preflight = service.preflight_strategy_contract

    def preflight(*args, **kwargs):
        events.append("preflight")
        return original_preflight(*args, **kwargs)

    monkeypatch.setattr(service, "preflight_strategy_contract", preflight)
    monkeypatch.setattr(run_daily, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(run_daily, "bootstrap_market_database", lambda: events.append("bootstrap"))
    monkeypatch.setattr(
        run_daily, "update_market_data", lambda: events.append("market_update") or (101, [])
    )
    monkeypatch.setattr(
        run_daily, "validate_market_data", lambda: events.append("integrity") or _pass_integrity()
    )
    monkeypatch.setattr(
        run_daily, "run_forward_validation_daily", lambda: events.append("forward_evidence")
    )
    monkeypatch.setattr(run_daily, "run_paper_v2_lifecycle", lambda: events.append("lifecycle"))
    monkeypatch.setattr(run_daily, "run_paper_v3_lifecycle", lambda: events.append("lifecycle"))
    monkeypatch.setattr(
        run_daily,
        "run_strategy_scanner",
        lambda pending_execution_result=None: events.append("scanner"),
    )
    monkeypatch.setattr(run_daily, "get_market_date", lambda: date.today().isoformat())
    monkeypatch.setattr(
        "quantctl.run_history.record_daily_pipeline_steps",
        lambda _result: events.append("run_history"),
    )
    monkeypatch.setattr("sys.argv", ["run_daily"])


DAILY_DRIFT = {
    "slippage_10bps": (Q70, "PAPER_SLIPPAGE_BPS", "10"),
    "holding_30": (Q70, "TRADING_MAX_HOLDING_DAYS", "30"),
    "entry_trend": (Q70, "TRADING_ENTRY_MODEL", "trend"),
    "v3_commission": (V3, "PAPER_COMMISSION_RATE", "0.002"),
}


@pytest.mark.parametrize("case", sorted(DAILY_DRIFT))
def test_daily_contract_mismatch_happens_before_any_daily_write(
    clean_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    from scripts import run_daily

    strategy, name, value = DAILY_DRIFT[case]
    monkeypatch.setenv("PAPER_STRATEGY_VERSION", strategy)
    monkeypatch.setenv(name, value)
    events: list[str] = []
    _record_daily_stages(monkeypatch, events)

    with pytest.raises(contract.StrategyContractMismatch):
        run_daily.main()

    # Only the preflight ran: no bootstrap, market update, forward evidence,
    # lifecycle, scanner or run-history step, and no store file was created.
    assert events == ["preflight"]
    assert not any(clean_env.iterdir())


@pytest.mark.parametrize("skip", ([], ["--skip-update"], ["--skip-lifecycle", "--skip-scan"]))
def test_daily_preflight_runs_first_and_canonical_daily_is_unchanged(
    clean_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    skip: list[str],
) -> None:
    from scripts import run_daily

    monkeypatch.setenv("PAPER_STRATEGY_VERSION", Q70)
    events: list[str] = []
    _record_daily_stages(monkeypatch, events)
    monkeypatch.setattr("sys.argv", ["run_daily", *skip])

    assert run_daily.main() == 0
    assert events[0] == "preflight"
    assert events[1] == "bootstrap"
    if not skip:
        assert events[2:] == [
            "market_update", "integrity", "forward_evidence", "lifecycle", "scanner", "run_history",
        ]


# --------------------------------------------------------------------------
# P1-2 retained executor cannot route to another strategy's store
# --------------------------------------------------------------------------


def _scanner_with_retained_executor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    retained_for: str,
    recorder: Recorder,
):
    from execution.signal_executor import PaperSignalExecutor

    isolate_environment(monkeypatch, tmp_path, retained_for, hostile=False)
    _pin(retained_for)
    scanner = fresh_scanner(
        monkeypatch, recorder, breadth=80.0, real_executor=True, signal_date=REFERENCE_DATE
    )
    executor = PaperSignalExecutor.from_env()  # what initialize_scanner_runtime retains
    original_queue = executor.queue_signals

    def recording_queue(*args, **kwargs):
        recorder.events.append("paper_queue")
        return original_queue(*args, **kwargs)

    monkeypatch.setattr(executor, "queue_signals", recording_queue)
    monkeypatch.setattr(scanner, "paper_signal_executor", executor)
    return scanner, executor


@pytest.mark.parametrize(("retained_for", "selected"), ((Q70, V3), (V3, Q70)))
def test_retained_executor_of_other_strategy_fails_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    retained_for: str,
    selected: str,
) -> None:
    from app.strategy_scan import ScanConfigurationError, run_strategy_scan

    recorder = Recorder()
    wrap_processors(monkeypatch, recorder)
    scanner, executor = _scanner_with_retained_executor(
        monkeypatch, tmp_path, retained_for=retained_for, recorder=recorder
    )
    retained_store = Path(executor.config.database_path)
    retained_bytes = retained_store.read_bytes() if retained_store.exists() else None
    monkeypatch.setenv("PAPER_STRATEGY_VERSION", selected)

    with pytest.raises(ScanConfigurationError, match="retains a paper executor"):
        run_strategy_scan(strategy_identity=selected, scanner_loader=lambda: scanner)

    assert not SIDE_EFFECTS & set(recorder.events)
    selected_store = tmp_path / ("v3.db" if selected == V3 else "q70.db")
    assert not selected_store.exists()
    assert (retained_store.read_bytes() if retained_store.exists() else None) == retained_bytes


def test_retained_executor_of_selected_strategy_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.strategy_scan import _verify_retained_executor

    recorder = Recorder()
    scanner, _executor = _scanner_with_retained_executor(
        monkeypatch, tmp_path, retained_for=Q70, recorder=recorder
    )
    _verify_retained_executor(scanner, Q70)  # no exception


def test_retained_executor_with_drifted_config_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.strategy_scan import ScanConfigurationError, _verify_retained_executor

    recorder = Recorder()
    scanner, _executor = _scanner_with_retained_executor(
        monkeypatch, tmp_path, retained_for=Q70, recorder=recorder
    )
    monkeypatch.setenv("PAPER_MAX_ORDERS_PER_SCAN", "4")  # env changed after retention
    with pytest.raises(ScanConfigurationError, match="maximum_orders_per_scan"):
        _verify_retained_executor(scanner, Q70)


# --------------------------------------------------------------------------
# P1-3 historical NULL holding is never inferred from today's default
# --------------------------------------------------------------------------


def _legacy_store(path: Path, rows: list[tuple[str, str | None, dict | None]]) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE paper_position_lifecycle (symbol TEXT PRIMARY KEY, entry_date TEXT, "
            "maximum_holding_days INTEGER, entry_order_id TEXT)"
        )
        connection.execute(
            "CREATE TABLE paper_orders (client_order_id TEXT PRIMARY KEY, execution_context TEXT)"
        )
        for symbol, order_id, context in rows:
            connection.execute(
                "INSERT INTO paper_position_lifecycle VALUES (?, '2026-09-01', NULL, ?)",
                (symbol, order_id),
            )
            if order_id is not None:
                connection.execute(
                    "INSERT INTO paper_orders VALUES (?, ?)",
                    (order_id, None if context is None else json.dumps(context)),
                )


def _holdings(path: Path) -> dict[str, int | None]:
    with sqlite3.connect(path) as connection:
        return dict(connection.execute(
            "SELECT symbol, maximum_holding_days FROM paper_position_lifecycle"
        ).fetchall())


def test_migration_never_backfills_null_holding_from_current_default(
    clean_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import migrate_open_positions_policy as migrate

    store = clean_env / "legacy.db"
    _legacy_store(
        store,
        [
            ("NOEV", None, None),  # no entry order at all
            ("EMPTY", "o-1", None),  # entry order without context
            ("EVID", "o-2", {"lifecycle": {"maximum_holding_days": 30}}),  # recorded evidence
            ("BAD", "o-3", {"lifecycle": {"maximum_holding_days": True}}),  # not a valid value
        ],
    )
    monkeypatch.setattr(migrate, "load_dotenv", lambda: False)

    assert migrate.main(["--database", str(store)]) == 0  # dry run writes nothing
    assert set(_holdings(store).values()) == {None}
    assert migrate.main(["--apply", "--database", str(store)]) == 0

    assert _holdings(store) == {"NOEV": None, "EMPTY": None, "EVID": 30, "BAD": None}
    output = capsys.readouterr().out
    assert "NO_EVIDENCE" in output and "RECORDED_ENTRY_EVIDENCE" in output
    assert "SET 20" not in output  # today's canonical default is never used


# --------------------------------------------------------------------------
# P2-1 research attestation cannot overclaim
# --------------------------------------------------------------------------


def _artifact(directory: Path, *, policy_overrides=None, paper_overrides=None, manifest_overrides=None):
    directory.mkdir(parents=True)
    manifest = {
        "arms": ["database_coverage_50_history_5_staleness"],
        "latest_vnindex_date": "2026-09-17",
        "policy_fingerprint": "Q70_FROZEN/hybrid_trend_donchian/ATR2x5/fixed",
        "q70_threshold": 0.7,
        **(manifest_overrides or {}),
    }
    policy = {
        "name": "Q70_FROZEN", "entry_model": "hybrid_trend_donchian", "quality_enabled": True,
        "quality_threshold": 0.7, "stop_atr_multiplier": 2.0, "target_atr_multiplier": 5.0,
        "max_open_positions": 10, **(policy_overrides or {}),
    }
    paper = {
        "position_sizer": "atr_risk", "risk_per_trade_pct": 1.0, "atr_stop_multiplier": 2.0,
        "target_atr_multiplier": 5.0, "maximum_orders_per_scan": 3, "commission_rate": 0.0015,
        "slippage_bps": 5.0, "maximum_open_positions": 10, "sell_tax_rate": 0.001,
        **(paper_overrides or {}),
    }
    parity = {key: paper[key] for key in (
        "position_sizer", "risk_per_trade_pct", "atr_stop_multiplier", "commission_rate",
        "slippage_bps", "maximum_open_positions", "sell_tax_rate",
    )}
    (directory / "experiment_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    arm = directory / "database_coverage_50_history_5_staleness"
    arm.mkdir()
    (arm / "policy_fingerprint.json").write_text(
        json.dumps({"policy": policy, "paper_execution": paper, "parity": parity}), encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("label", "overrides", "fragment"),
    (
        ("max_orders_99", {"paper_overrides": {"maximum_orders_per_scan": 99}}, "maximum_orders_per_scan"),
        ("policy_threshold_010", {"policy_overrides": {"quality_threshold": 0.10}}, "quality_threshold"),
        ("manifest_threshold_010", {"manifest_overrides": {"q70_threshold": 0.10}}, "quality_threshold"),
        ("quality_disabled", {"policy_overrides": {"quality_enabled": False}}, "quality_enabled"),
        ("paper_slippage_vs_parity", {"paper_overrides": {"slippage_bps": 10.0}}, "slippage_bps"),
    ),
)
def test_contradictory_research_evidence_is_never_attested_as_proven(
    tmp_path: Path,
    label: str,
    overrides: dict,
    fragment: str,
) -> None:
    from quantlab.research_attestation import (
        ResearchAttestationError,
        build_frozen_q70_research_attestation,
    )

    artifact = tmp_path / "research_results" / label
    _artifact(artifact, **overrides)
    with pytest.raises(ResearchAttestationError, match=f"contradicts claims: .*{fragment}"):
        build_frozen_q70_research_attestation(artifact, root=tmp_path)


def test_consistent_research_evidence_proves_every_claim_with_sources(tmp_path: Path) -> None:
    from quantlab.research_attestation import build_frozen_q70_research_attestation

    artifact = tmp_path / "research_results" / "consistent"
    _artifact(artifact)
    attestation = build_frozen_q70_research_attestation(artifact, root=tmp_path)
    claims = {item["claim"]: item for item in attestation["arms"][0]["proven_claims"]}

    # execution_timing is not recorded by this artifact: UNPROVEN, not asserted.
    assert claims["execution_timing"]["status"] == "UNPROVEN"
    assert all(
        item["proven"] and item["sources"]
        for name, item in claims.items()
        if name != "execution_timing"
    )
    arm = "database_coverage_50_history_5_staleness"
    assert claims["maximum_orders_per_scan"]["sources"] == [
        f"{arm}/policy_fingerprint.json:paper_execution.maximum_orders_per_scan"
    ]
    assert set(claims["quality_threshold"]["sources"]) == {
        "experiment_manifest.json:q70_threshold",
        f"{arm}/policy_fingerprint.json:policy.quality_threshold",
        "code:frozen_q70_evaluator._Q70_THRESHOLD",
    }


# --------------------------------------------------------------------------
# P2-2 contract status is bound to the identity it describes
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


def test_status_is_derived_from_the_identity_at_the_evidence_boundary(
    clean_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from quantlab.evidence.prospective_portfolio import _strategy_identity_v3_kwargs

    _pin(Q70)
    monkeypatch.setenv("TRADING_ENTRY_MODEL", "trend")
    deviating = collect_production_strategy_identity(Q70)
    fields = _strategy_identity_v3_kwargs(deviating)

    assert fields["strategy_contract_status"] == contract.CONTRACT_DEVIATION
    assert any(path.startswith("signal.entry.model") for path in fields["strategy_contract_deviations"])
    assert _record(**fields).strategy_contract_status == contract.CONTRACT_DEVIATION


def test_matched_status_paired_with_another_identity_is_rejected(
    clean_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin(Q70)
    matched = contract.evidence_attestation_fields(collect_production_strategy_identity(Q70))
    assert matched["strategy_contract_status"] == contract.CONTRACT_MATCHED
    monkeypatch.setenv("TRADING_ENTRY_MODEL", "trend")
    deviating = collect_production_strategy_identity(Q70)
    forged = {**matched, **deviating.fingerprints()}  # reviewer's reproduction

    with pytest.raises(ValueError, match="does not match the fingerprint recomputed"):
        _record(**forged)
    # Mixing parts from different identities is rejected by the binding hash.
    mixed = {**matched, "execution_identity": deviating.execution_identity}
    with pytest.raises(ValueError, match="does not match the fingerprint recomputed"):
        _record(**mixed)


def test_contract_status_requires_complete_metadata(clean_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pin(Q70)
    matched = contract.evidence_attestation_fields(collect_production_strategy_identity(Q70))
    without_prints = {
        key: value
        for key, value in matched.items()
        if key not in {"strategy_identity_v3", "signal_identity", "execution_identity"}
    }
    with pytest.raises(ValueError, match="v3 identity requires"):
        _record(**without_prints)
    with pytest.raises(ValueError, match="v3 identity requires"):
        _record(**{**matched, "execution_identity": None})
    monkeypatch.setenv("PAPER_COMMISSION_RATE", "0.002")
    deviation = contract.evidence_attestation_fields(collect_production_strategy_identity(Q70))
    with pytest.raises(ValueError, match="requires the deviating field paths"):
        _record(**{**deviation, "strategy_contract_deviations": ()})
    with pytest.raises(ValueError, match="requires version, status, deviations and overlay"):
        _record(**{**deviation, "execution_overlay": None})


def test_legacy_records_without_v3_fields_are_unchanged() -> None:
    record = _record()
    assert record.record_identity == "ad01bb769a41317321c62df965a4bb43957b4c2ba4c1140c8dde833505b007ad"
    assert record.strategy_identity_provenance == ("LEGACY_UNVERIFIED", "NO_STRATEGY_IDENTITY_V3")
