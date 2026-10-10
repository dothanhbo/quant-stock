"""Daily checkpoints: temporary stores, synthetic prices and no transport."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from app.daily_pipeline import DailyPipeline
from core.market_admission import AdmissionContext, admit_price_batch
from core.market_data_integrity import (
    MarketDataIntegrityResult, MarketDataIntegrityState, check_market_data_integrity,
)
from core.market_observation_log import ObservationLog, resolve_observation_log_path
from quantlab.catalog import research_checkpoint as checkpoint
from quantlab.catalog.market_data_snapshot import build_market_data_snapshot


MEMBERS = """
ACB ANV BAF BCM BID BMP BSI BSR BVH BWE
CII CMG CTD CTG CTR CTS DBC DCM DGW DIG
DPM DSE DXG EIB EVF FPT FRT FTS GAS GEE
GEX GMD GVR HAG HCM HDB HDG HHV HPG HSG
HT1 KBC KDC KDH KOS LPB MBB MCH MSB MSN
MWG NAB NKG NLG NT2 NVL OCB PAN PC1 PDR
PHR PLX PNJ POW PVD PVT REE SAB SBT SHB
SIP SJS SSB SSI STB TAL TCB TCH TCX TPB
VCB VCG VCI VCK VGC VHC VHM VIB VIC VIX
VJC VND VNM VPB VPI VPL VPX VRE VSC VTP
""".split()
SESSION = "2026-10-09"
NOW = datetime(2026, 10, 9, 9, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    import socket
    from urllib.parse import unquote, urlsplit

    def forbidden(*args, **kwargs):
        raise AssertionError("Provider/network calls forbidden in checkpoint tests")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    native = sqlite3.connect

    def connect(database, *args, **kwargs):
        raw = str(database)
        if raw.startswith("file:"):
            raw = unquote(urlsplit(raw).path)
            if len(raw) > 2 and raw[0] == "/" and raw[2] == ":":
                raw = raw[1:]
        assert Path(raw).resolve().is_relative_to(tmp_path), "Non-fixture DB access refused"
        return native(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.delenv("MARKET_OBSERVATION_LOG_PATH", raising=False)
    monkeypatch.delenv("QUANT_OPERATION_RUN_ID", raising=False)


def source(tmp_path, *, wal=False):
    db = tmp_path / "market.db"
    connection = sqlite3.connect(db)
    if wal:
        connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE prices(id INTEGER PRIMARY KEY, symbol TEXT, time TEXT, "
                       "open REAL, high REAL, low REAL, close REAL, volume INTEGER)")
    # VTP is missing, but remains a fixed member. ACB needs the Daily append.
    for symbol in MEMBERS[:-1] + ["VNINDEX"]:
        connection.execute("INSERT INTO prices(symbol,time,open,high,low,close,volume) "
                           "VALUES (?,?,?,?,?,?,?)",
                           (symbol, "2026-10-08" if symbol == "ACB" else SESSION,
                            10, 11, 9, 10.5, 100))
    connection.commit()
    if not wal:
        connection.close()
    cohort = tmp_path / "cohort.json"
    cohort.write_text(json.dumps({
        "tickers": MEMBERS, "membership_sha256": checkpoint.COHORT_V1_SHA256,
        "cohort_identity": "FIXED_COHORT_V1:" + checkpoint.COHORT_V1_SHA256,
        "label": "FIXED_COHORT_V1 / EXISTING_TRACKED_UNIVERSE",
    }), encoding="utf-8")
    retention = tmp_path / "retention.txt"
    retention.write_text("Synthetic fixture only; no third-party data or retention rights asserted.")
    return db, cohort, retention, connection if wal else None


def update(db, provider="KBS", close=10.5):
    frame = pd.DataFrame([
        ["2026-10-08", 10, 11, 9, 10.5, 100],
        [SESSION, 10, 11, 9, close, 100],
    ], columns=["time", "open", "high", "low", "close", "volume"])
    outcome = admit_price_batch(
        frame, symbol="ACB", market_db_path=db, now=NOW,
        context=AdmissionContext(
            source=provider, endpoint=f"vnstock.api.quote.Quote(source='{provider}').history",
            source_mode="UPDATE", package_name="vnstock", package_version="4.0.2",
            request_start="2026-10-08", request_end=SESSION,
        ),
    )
    return (101, []) if outcome.applied else (100, ["ACB"])


def integrity(db):
    return check_market_data_integrity(database_path=db, required_symbols=("ACB", "VNINDEX"),
                                       as_of_date=SESSION)


def capture(tmp_path, db, cohort, retention):
    return checkpoint.create_research_checkpoint(
        integrity(db), root=tmp_path / "checkpoints", cohort_manifest=cohort,
        retention_reference=retention,
    )


def test_successful_daily_update_is_restorable_fixed_and_idempotent(tmp_path, monkeypatch):
    db, cohort, retention, _ = source(tmp_path)
    calls, captures = [], []

    def research(gate):
        calls.append("checkpoint")
        protected = [p for original in (db, resolve_observation_log_path(db))
                     for p in (original, *(Path(str(original) + suffix) for suffix in ("-wal", "-shm")))]
        before = checkpoint._state(protected)
        native = sqlite3.connect

        def private_only(database, *args, **kwargs):
            assert not any(str(p).replace("\\", "/") in str(database).replace("\\", "/")
                           for p in protected), "Hook must never open source with SQLite"
            return native(database, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(sqlite3, "connect", private_only)
            captures.append(checkpoint.create_research_checkpoint(
                gate, root=tmp_path / "checkpoints", cohort_manifest=cohort,
                retention_reference=retention,
            ))
        assert checkpoint._state(protected) == before

    result = DailyPipeline(
        update_market_data=lambda: calls.append("update") or update(db),
        validate_market_data=lambda: calls.append("integrity") or integrity(db),
        research_checkpoint=research,
        run_forward_validation=lambda: calls.append("forward"),
        run_lifecycle=lambda: calls.append("lifecycle"), run_scanner=lambda: calls.append("scan"),
    ).run()
    assert result.success and calls == ["update", "integrity", "checkpoint", "forward", "lifecycle", "scan"]
    directory = Path(captures[0]["path"])
    manifest_bytes = (directory / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    assert manifest["cohort_members"] == MEMBERS and manifest["qualified"] is False
    assert manifest["missing_symbols"] == ["VTP"] and manifest["missing_sessions"]["VTP"] == [SESSION]
    assert manifest["update_provider"] == "KBS" and manifest["price_basis"] == "UNKNOWN"
    assert manifest["classification"] == checkpoint.LABEL
    assert "LEGACY_UNVERIFIED" in manifest["historical_provider"]
    assert manifest["applied_source_evidence"][0]["package_version"] == "4.0.2"
    for name, digest in manifest["files_sha256"].items():
        assert checkpoint._sha(directory / name) == digest
    restored = tmp_path / "restored.sqlite"
    restored.write_bytes((directory / "market.sqlite").read_bytes())
    restored_snapshot = build_market_data_snapshot(restored)
    assert restored_snapshot.snapshot_id == manifest["snapshot_id"]
    assert restored_snapshot.load_ohlcv(["ACB"]).frame_for("ACB").iloc[-1]["close"] == 10.5
    repeated = capture(tmp_path, db, cohort, retention)
    assert repeated["status"] == "EXISTING" and repeated["path"] == str(directory)
    assert (directory / "manifest.json").read_bytes() == manifest_bytes
    assert len(list((tmp_path / "checkpoints").iterdir())) == 1
    # SQLite representation changes and a repeated successful fetch do not
    # create another research dataset when prices and lineage are identical.
    connection = sqlite3.connect(db)
    connection.execute("VACUUM")
    connection.close()
    assert update(db) == (101, [])
    assert capture(tmp_path, db, cohort, retention)["checkpoint_id"] == repeated["checkpoint_id"]
    assert len(list((tmp_path / "checkpoints").iterdir())) == 1


def test_wal_capture_retains_committed_rows_without_source_changes(tmp_path):
    db, cohort, retention, keeper = source(tmp_path, wal=True)
    try:
        assert update(db) == (101, [])
        gate = integrity(db)
        protected = [p for original in (db, resolve_observation_log_path(db))
                     for p in (original, *(Path(str(original) + s) for s in ("-wal", "-shm")))]
        before = checkpoint._state(protected)
        assert before[str(Path(str(db) + "-wal"))]["bytes"] > 0
        result = checkpoint.create_research_checkpoint(gate, root=tmp_path / "checkpoints",
                                                      cohort_manifest=cohort, retention_reference=retention)
        assert checkpoint._state(protected) == before
        snapshot = build_market_data_snapshot(Path(result["path"]) / "market.sqlite")
        assert snapshot.load_ohlcv(["ACB"]).frame_for("ACB").iloc[-1]["time"].strftime("%Y-%m-%d") == SESSION
    finally:
        keeper.close()


@pytest.mark.parametrize("failure", ["update_exception", "incomplete_update", "integrity_failure", "skip_update"])
def test_unsuccessful_or_skipped_update_cannot_checkpoint(tmp_path, failure):
    calls = []
    gate = MarketDataIntegrityResult(
        MarketDataIntegrityState.FAIL_CLOSED if failure == "integrity_failure" else MarketDataIntegrityState.PASS,
        SESSION, ("ACB", "VNINDEX"), ("synthetic",), tmp_path / "unused.db",
    )

    def run_update():
        if failure == "update_exception":
            raise RuntimeError("synthetic update failure")
        return (100, ["ACB"]) if failure == "incomplete_update" else (101, [])

    result = DailyPipeline(update_market_data=run_update, validate_market_data=lambda: gate,
                           research_checkpoint=lambda _: calls.append("checkpoint"),
                           run_lifecycle=lambda: calls.append("lifecycle"),
                           run_scanner=lambda: calls.append("scan")).run(skip_update=failure == "skip_update")
    assert "checkpoint" not in calls
    if failure == "skip_update":
        assert result.success and calls == ["lifecycle", "scan"]
        assert "skipped" in next(s.warning for s in result.stages if s.name == "Research Checkpoint")
    else:
        assert not result.success


def test_checkpoint_failure_is_visible_and_operations_continue(tmp_path):
    calls = []
    gate = MarketDataIntegrityResult(MarketDataIntegrityState.PASS, SESSION, ("ACB", "VNINDEX"), (), tmp_path / "unused.db")

    def fail(_):
        raise RuntimeError("synthetic checkpoint consistency failure")

    result = DailyPipeline(update_market_data=lambda: (101, []), validate_market_data=lambda: gate,
                           research_checkpoint=fail, run_forward_validation=lambda: calls.append("forward"),
                           run_lifecycle=lambda: calls.append("lifecycle"), run_scanner=lambda: calls.append("scan")).run()
    stage = next(s for s in result.stages if s.name == "Research Checkpoint")
    assert not result.success and not stage.success and "consistency failure" in stage.error
    assert calls == ["forward", "lifecycle", "scan"]


@pytest.mark.parametrize("problem", ["retention", "cohort", "provenance_block", "lineage_drift", "non_kbs"])
def test_checkpoint_rejects_invalid_inputs_without_publication(tmp_path, problem):
    db, cohort, retention, _ = source(tmp_path)
    assert update(db, provider="VCI" if problem == "non_kbs" else "KBS") == (101, [])
    gate = integrity(db)
    assert gate.state is MarketDataIntegrityState.PASS
    if problem == "retention":
        retention.unlink()
    elif problem == "cohort":
        data = json.loads(cohort.read_text())
        data["tickers"][-1] = "NEW"
        cohort.write_text(json.dumps(data))
    elif problem == "provenance_block":
        assert update(db, close=10.8) == (100, ["ACB"])
    elif problem == "lineage_drift":
        with sqlite3.connect(db) as connection:
            connection.execute("UPDATE prices SET close=10.8 WHERE symbol='ACB' AND time=?", (SESSION,))
    with pytest.raises(RuntimeError):
        checkpoint.create_research_checkpoint(gate, root=tmp_path / "checkpoints",
                                              cohort_manifest=cohort, retention_reference=retention)
    assert not list((tmp_path / "checkpoints").glob("*/manifest.json"))


def test_concurrent_source_change_refuses_publication(tmp_path, monkeypatch):
    db, cohort, retention, _ = source(tmp_path)
    update(db)
    native = checkpoint._copy_sqlite

    def changing_source(source_path, raw, destination, before):
        native(source_path, raw, destination, before)
        if source_path == resolve_observation_log_path(db):
            with db.open("ab") as stream:
                stream.write(b"synthetic concurrent change")

    monkeypatch.setattr(checkpoint, "_copy_sqlite", changing_source)
    with pytest.raises(RuntimeError, match="Source changed"):
        capture(tmp_path, db, cohort, retention)
    assert not list((tmp_path / "checkpoints").iterdir())


@pytest.mark.parametrize("corruption", ["bytes", "identity"])
def test_corrupt_existing_checkpoint_is_not_reported_successful(tmp_path, corruption):
    db, cohort, retention, _ = source(tmp_path)
    update(db)
    result = capture(tmp_path, db, cohort, retention)
    if corruption == "bytes":
        with (Path(result["path"]) / "market.sqlite").open("ab") as stream:
            stream.write(b"synthetic corruption")
    else:
        path = Path(result["path"]) / "manifest.json"
        data = json.loads(path.read_text())
        data["dataset_version_id"] = "synthetic corrupted identity"
        path.write_text(json.dumps(data))
    with pytest.raises(RuntimeError, match="bytes do not match|identity mismatch"):
        capture(tmp_path, db, cohort, retention)


def test_run_daily_opt_in_wires_checkpoint_without_another_updater(tmp_path, monkeypatch):
    from scripts import run_daily

    db, cohort, retention, _ = source(tmp_path)
    monkeypatch.setattr(run_daily, "load_dotenv", lambda: None)
    monkeypatch.setattr(run_daily, "run_contract_preflight", lambda: None)
    monkeypatch.setattr(run_daily, "apply_active_paper_store_environment", lambda: None)
    monkeypatch.setattr(run_daily, "bootstrap_market_database", lambda: None)
    monkeypatch.setattr(run_daily, "update_market_data", lambda: update(db))
    monkeypatch.setattr(run_daily, "validate_market_data", lambda: integrity(db))
    monkeypatch.setattr(run_daily, "run_forward_validation_daily", lambda: None)
    monkeypatch.setattr(run_daily, "run_paper_v2_lifecycle", lambda: None)
    monkeypatch.setattr(run_daily, "run_strategy_scanner", lambda **kwargs: None)
    monkeypatch.setattr(run_daily, "_use_v3", lambda: False)
    monkeypatch.setattr("sys.argv", ["run_daily", "--research-checkpoint-root", str(tmp_path / "checkpoints"),
                                    "--research-cohort-manifest", str(cohort),
                                    "--research-retention-reference", str(retention)])
    assert run_daily.main() == 0
    assert len(list((tmp_path / "checkpoints").glob("*/market.sqlite"))) == 1
