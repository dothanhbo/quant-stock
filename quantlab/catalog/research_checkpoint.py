from __future__ import annotations

"""Optional local checkpoint of Daily's existing admitted dataset; no transport."""

from datetime import datetime, timezone
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory

from core.market_data_integrity import MarketDataIntegrityResult, MarketDataIntegrityState
from core.market_observation_log import (
    ObservationLog, read_symbol_rows, resolve_observation_log_path,
)
from quantlab.catalog.market_data_snapshot import build_market_data_snapshot


COHORT_V1_SHA256 = "a56872b859b018d8a8dfb7f034b15ceb9a68ea2805aeb5c7150fa53204d14faf"
LABEL = "EXPLORATORY_ONLY / LEGACY_UNVERIFIED"


def _sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _state(paths: list[Path]) -> dict[str, object]:
    result = {}
    for path in paths:
        if path.exists():
            stat = path.stat()
            result[str(path)] = {"sha256": _sha(path), "bytes": stat.st_size,
                                 "mtime_ns": stat.st_mtime_ns}
        else:
            result[str(path)] = None
    return result


def _copy_sqlite(source: Path, raw: Path, destination: Path, before: dict) -> None:
    """Open SQLite only on private copies, including committed WAL frames."""
    journal = before[str(Path(str(source) + "-journal"))]
    if journal and journal["bytes"]:
        raise RuntimeError("Active rollback journal: research snapshot consistency unavailable")
    shutil.copyfile(source, raw)
    if _sha(raw) != before[str(source)]["sha256"]:
        raise RuntimeError("Database changed while copying research snapshot")
    wal = Path(str(source) + "-wal")
    if before[str(wal)] and before[str(wal)]["bytes"]:
        copied_wal = Path(str(raw) + "-wal")
        shutil.copyfile(wal, copied_wal)
        if _sha(copied_wal) != before[str(wal)]["sha256"]:
            raise RuntimeError("WAL changed while copying research snapshot")
    with closing(sqlite3.connect(raw.as_uri() + "?mode=ro", uri=True)) as source_connection:
        with closing(sqlite3.connect(destination)) as target_connection:
            source_connection.backup(target_connection)
            if target_connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError("Research snapshot SQLite integrity check failed")


def _existing(directory: Path, identity: str, expected: dict[str, object]) -> dict[str, object]:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if (manifest["checkpoint_id"] != identity or manifest["qualified"] is not False
            or manifest["classification"] != LABEL or manifest["price_basis"] != "UNKNOWN"
            or any(manifest.get(key) != value for key, value in expected.items())):
        raise RuntimeError("Existing research checkpoint identity mismatch")
    for name, digest in manifest["files_sha256"].items():
        if _sha(directory / name) != digest:
            raise RuntimeError("Existing research checkpoint bytes do not match manifest")
    return {"status": "EXISTING", "path": str(directory), "checkpoint_id": identity,
            "qualified": False, "classification": LABEL}


def create_research_checkpoint(
    integrity: MarketDataIntegrityResult, *, root: str | Path,
    cohort_manifest: str | Path | None, retention_reference: str | Path | None,
) -> dict[str, object]:
    """Publish a restorable snapshot only after all source/lineage checks pass.

    Retention evidence must be supplied by the operator; this function does not
    grant source rights. Canonical stores are read as bytes, never opened by
    SQLite. Publication is atomic; failed staging creates no checkpoint.
    """
    if integrity.state is not MarketDataIntegrityState.PASS or not integrity.required_session:
        raise RuntimeError("Research checkpoint requires Daily integrity PASS and session")
    if cohort_manifest is None or retention_reference is None:
        raise RuntimeError("Frozen cohort manifest and permitted-retention reference are required")
    cohort_path = Path(cohort_manifest).expanduser().resolve()
    retention_path = Path(retention_reference).expanduser().resolve()
    if not retention_path.is_file() or not retention_path.stat().st_size:
        raise RuntimeError("Permitted-retention evidence is missing or empty")
    cohort_bytes = cohort_path.read_bytes()
    cohort = json.loads(cohort_bytes)
    members = cohort["tickers"]
    ticker_bytes = ("\n".join(members) + "\n").encode("utf-8")
    if (len(members) != 100 or len(set(members)) != 100 or "VNINDEX" in members
            or sha256(ticker_bytes).hexdigest() != COHORT_V1_SHA256
            or cohort["membership_sha256"] != COHORT_V1_SHA256
            or cohort["cohort_identity"] != "FIXED_COHORT_V1:" + COHORT_V1_SHA256
            or cohort["label"] != "FIXED_COHORT_V1 / EXISTING_TRACKED_UNIVERSE"):
        raise RuntimeError("Frozen Cohort V1 membership identity mismatch")
    source = integrity.database_path.expanduser().resolve()
    log_source = resolve_observation_log_path(source)
    root = Path(root).expanduser().resolve()
    inputs = [source, log_source, cohort_path, retention_path]
    if any(path.is_relative_to(root) for path in inputs):
        raise RuntimeError("Research output must be separate from source inputs")
    if not source.is_file() or not log_source.is_file() or source == log_source:
        raise RuntimeError("Research checkpoint requires source database and bound provenance log")
    protected = [p for db in (source, log_source)
                 for p in (db, *(Path(str(db) + s) for s in ("-wal", "-shm", "-journal")))]
    before = _state(protected)
    captured_at = datetime.now(timezone.utc).isoformat()
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".checkpoint-", dir=root) as temporary:
        stage = Path(temporary)
        raw = stage / "raw"
        raw.mkdir()
        market = stage / "market.sqlite"
        ledger = stage / "observations.sqlite"
        _copy_sqlite(source, raw / "market.db", market, before)
        _copy_sqlite(log_source, raw / "observations.db", ledger, before)
        log = ObservationLog(ledger, readonly=True)
        log.assert_bound_to(source)  # verifies locator without opening source
        version = log.dataset_version_identity()
        if not version["consumable"]:
            raise RuntimeError("Research provenance has unresolved blocks or pending applications")
        snapshot = build_market_data_snapshot(market)
        requested = tuple(members) + ("VNINDEX",)
        targets = sorted(set(snapshot.symbols) | set(requested) | set(integrity.required_symbols))
        with closing(sqlite3.connect(market.as_uri() + "?mode=ro", uri=True)) as connection:
            for symbol in targets:
                issues = log.verify_symbol(symbol, read_symbol_rows(connection, symbol))
                if issues:
                    raise RuntimeError(f"Research provenance mismatch on {symbol}: {issues}")
        session = integrity.required_session
        if snapshot.last_session_date != session:
            raise RuntimeError("Research snapshot session differs from Daily integrity session")
        bundle = snapshot.load_ohlcv(requested, through_date=session)
        benchmark = bundle.frame_for("VNINDEX")
        if benchmark.empty:
            raise RuntimeError("Research snapshot lacks benchmark data")
        sessions = tuple(benchmark["time"].dt.strftime("%Y-%m-%d"))
        if not sessions or sessions[-1] != session:
            raise RuntimeError("Research snapshot lacks the Daily benchmark session")
        missing_sessions = {}
        for symbol in members:
            frame = bundle.frame_for(symbol)
            available = set(frame["time"].dt.strftime("%Y-%m-%d")) if not frame.empty else set()
            missing_sessions[symbol] = sorted(set(sessions) - available)
        applied_ids = {str(event["observation_id"]) for event in log.list_events()
                       if event["event_type"] == "APPLICATION_RESULT"
                       and event["application_state"] == "applied"}
        evidence = []
        for observation_id in sorted(applied_ids):
            observation = log.get_observation(observation_id)
            if observation["source"] != "KBS":
                raise RuntimeError("Research checkpoint refuses known non-KBS applied observations")
            evidence.append({key: observation[key] for key in (
                "observation_id", "symbol", "source", "endpoint", "source_mode",
                "package_name", "package_version", "normalization_version", "price_basis",
                "first_fetched_at_utc", "request_start", "request_end", "price_unit", "volume_unit",
            )})
        if not evidence:
            raise RuntimeError("No applied KBS source evidence available for research checkpoint")
        identity_payload = {
            "format": "daily-research-checkpoint.v1", "cohort_identity": cohort["cohort_identity"],
            "source_path": str(source),
            "snapshot_id": snapshot.snapshot_id, "market_session": session,
            "dataset_version_id": version["dataset_version_id"],
        }
        identity = sha256(json.dumps(identity_payload, sort_keys=True).encode()).hexdigest()
        (stage / "cohort_manifest.json").write_bytes(cohort_bytes)
        (stage / "cohort_tickers.txt").write_bytes(ticker_bytes)
        shutil.copyfile(retention_path, stage / "retention_reference.txt")
        shutil.rmtree(raw)  # only our private staging copies
        manifest = {
            **identity_payload, "checkpoint_id": identity, "captured_at_utc": captured_at,
            "classification": LABEL, "qualified": False, "cohort_members": members,
            "membership_sha256": COHORT_V1_SHA256, "cohort_manifest_sha256": sha256(cohort_bytes).hexdigest(),
            "source_file_evidence": before, "dataset_fingerprint": snapshot.logical_content_fingerprint,
            "identity_basis": "source locator, frozen cohort, logical prices snapshot, session and provenance version; physical WAL layout and repeat receipts do not create new datasets",
            "schema_version": snapshot.schema_version, "available_symbols": bundle.available_symbols,
            "missing_symbols": bundle.missing_symbols, "missing_sessions": missing_sessions,
            "missingness_basis": "absent stored bar; suspension/delisting/provider reason not established",
            "session_calendar_basis": "stored VNINDEX sessions; not verified official calendar",
            "update_provider": "KBS", "applied_source_evidence": evidence,
            "provenance_version": version, "price_basis": "UNKNOWN",
            "historical_provider": "LEGACY_UNVERIFIED; baseline source not retrospectively attributed",
            "limitations": ["Cohort is existing tracked universe, not verified VN100 membership",
                            "Historical source, adjustment basis, corporate actions and ticker continuity unverified",
                            "Integrity PASS is software validity, not official EOD completion or scientific qualification",
                            "Full preserved database includes legacy history; no new VCI data or provider requests"],
            "retention_reference": str(retention_path),
            "code_sha256": {str(p): _sha(p) for p in (Path(__file__), Path(__file__).with_name("market_data_snapshot.py"))},
            "files_sha256": {p.name: _sha(p) for p in stage.iterdir() if p.is_file()},
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if _state(protected) != before:
            raise RuntimeError("Source changed during checkpoint; research capture refused")
        destination = root / identity
        if destination.exists():
            return _existing(destination, identity, identity_payload)
        try:
            stage.rename(destination)
        except OSError:
            if destination.is_dir():
                return _existing(destination, identity, identity_payload)
            raise
        return {"status": "CREATED", "path": str(destination), "checkpoint_id": identity,
                "qualified": False, "classification": LABEL}
