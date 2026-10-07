"""R1 revision admission guard + R2 append-only observation log.

Offline and deterministic: temporary SQLite databases only, no provider calls,
no live ``market.db``. Fixtures mirror the incidents behind the V1 data
provenance decision (TPB-like rebase, DGW-like small rebase, incomplete
overlap, crash between observation and application, eight-year boundary).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from core import market_admission, market_observation_log
from core.market_admission import (
    AdmissionContext,
    AdmissionResult,
    admit_price_batch,
    completed_session_cutoff,
)
from core.market_observation_log import (
    ObservationLog,
    open_observation_log,
    resolve_observation_log_path,
)

# Monday 2026-10-05 16:30 Vietnam time: the 2026-10-05 session is complete.
NOW = datetime(2026, 10, 5, 9, 30, tzinfo=timezone.utc)

SCHEMA = """
CREATE TABLE prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT, time TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER
);
CREATE UNIQUE INDEX ux_prices_symbol_time ON prices(symbol, time);
"""

A = ("2026-09-24", 10.00, 11.00, 9.50, 10.50, 1000)
B = ("2026-09-25", 10.50, 12.00, 10.00, 11.00, 2000)
C = ("2026-09-28", 11.00, 12.50, 10.80, 12.00, 3000)


class SimulatedCrash(BaseException):
    """Stands in for a process kill: bypasses ``except Exception`` handlers."""


def make_market(directory: Path, rows: dict[str, list[tuple]] | None = None) -> Path:
    path = directory / "market.db"
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    for symbol, items in (rows or {}).items():
        connection.executemany(
            "INSERT INTO prices(symbol,time,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)",
            [(symbol, *item) for item in items],
        )
    connection.commit()
    connection.close()
    return path


def frame(items: list[tuple], symbol: str | None = None) -> pd.DataFrame:
    data = pd.DataFrame(items, columns=["time", "open", "high", "low", "close", "volume"])
    if symbol is not None:
        data["symbol"] = symbol
    return data


def context(start: str = "2026-09-20", end: str = "2026-10-05", mode: str = "UPDATE", **kw) -> AdmissionContext:
    return AdmissionContext(
        source="KBS",
        endpoint="vnstock.api.quote.Quote(source='KBS').history",
        source_mode=mode,
        package_name="vnstock",
        package_version="4.0.2",
        request_start=start,
        request_end=end,
        **kw,
    )


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def table_rows(path: Path) -> list[tuple]:
    connection = sqlite3.connect(path)
    try:
        return connection.execute("SELECT * FROM prices ORDER BY id").fetchall()
    finally:
        connection.close()


def admit(path: Path, items: list[tuple], symbol: str = "AAA", ctx: AdmissionContext | None = None, **kw):
    return admit_price_batch(
        frame(items, symbol),
        symbol=symbol,
        context=ctx or context(),
        market_db_path=path,
        now=kw.pop("now", NOW),
        **kw,
    )


# ------------------------------------------------------------------ fixture 1
def test_identical_overlap_appends_only_the_new_session(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    before = table_rows(market)

    outcome = admit(market, [A, B, C])

    assert outcome.result is AdmissionResult.ADMITTED_APPEND
    assert outcome.applied and not outcome.blocked
    assert outcome.appended_sessions == ("2026-09-28",)

    after = table_rows(market)
    # Old rows keep their row ids and values: nothing was replaced.
    assert after[:2] == before
    assert after[2][1:] == ("AAA", "2026-09-28", 11.0, 12.5, 10.8, 12.0, 3000)
    assert len(after) == 3

    log = open_observation_log(market)
    observations = log.list_observations("AAA")
    assert len(observations) == 1
    assert [row[0] for row in observations[0]["rows"]] == ["2026-09-24", "2026-09-25", "2026-09-28"]
    assert observations[0]["price_basis"] == "PRICE_ADJUSTMENT_UNKNOWN"
    assert log.observation_state(outcome.observation_id) == "applied"
    kinds = [event["event_type"] for event in log.list_events(outcome.observation_id)]
    assert kinds == ["OBSERVED", "ADMISSION_DECISION", "APPLICATION_INTENT", "APPLICATION_RESULT"]

    # Consumed-version binding: only the appended session is attributed to the
    # observation; legacy sessions resolve to the legacy baseline.
    assert log.session_provenance("AAA", "2026-09-28")["observation_id"] == outcome.observation_id
    legacy = log.session_provenance("AAA", "2026-09-24")
    assert legacy["origin"] == "BASELINE_LEGACY" and legacy["observation_id"] is None


def test_identical_rerun_is_a_noop_and_creates_no_duplicate(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    first = admit(market, [A, B, C], ctx=context())
    rows_after_first = table_rows(market)

    log = open_observation_log(market)
    version_after_first = log.dataset_version_identity()["dataset_version_id"]

    second = admit(market, [A, B, C], ctx=context())
    third = admit(market, [A, B, C], ctx=context(start="2026-09-21"))

    # P1-3: an identical retry of an applied observation returns the existing receipt.
    assert second.already_applied and second.applied and second.version_id == first.version_id
    assert third.result is AdmissionResult.ADMITTED_NO_NEW_SESSIONS
    assert table_rows(market) == rows_after_first
    assert first.observation_id == second.observation_id  # same request + same data -> same identity
    assert first.observation_id != third.observation_id  # different request window
    assert len(log.list_observations("AAA")) == 2
    applied = [e for e in log.list_events(first.observation_id) if e["event_type"] == "APPLICATION_RESULT"]
    assert len(applied) == 1
    # Neither the retry nor the no-op application advanced the dataset version.
    assert log.dataset_version_identity()["dataset_version_id"] == version_after_first


# ------------------------------------------------------------------ fixture 2
def test_tpb_like_revision_is_blocked_and_market_is_byte_identical(tmp_path: Path, monkeypatch) -> None:
    sessions = ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"]
    closes = [14.75, 14.80, 14.70, 14.60, 14.55]
    stored = [(s, c - 0.2, c + 0.3, c - 0.4, c, 1_000_000 + i) for i, (s, c) in enumerate(zip(sessions, closes))]
    rebased = [
        (s, round(o * 0.82, 2), round(h * 0.82, 2), round(l * 0.82, 2), round(c * 0.82, 2), v)
        for s, o, h, l, c, v in stored
    ]
    new_session = ("2026-09-28", 12.0, 12.2, 11.9, 12.1, 900_000)
    market = make_market(tmp_path, {"TPB": stored})
    before_hash, before_rows = file_sha(market), table_rows(market)

    outcome = admit(market, [*rebased, new_session], symbol="TPB", ctx=context(run_id="run-1"))

    assert outcome.result is AdmissionResult.BLOCKED_REVISION_CHANGED
    assert outcome.blocked and not outcome.applied
    assert outcome.detail["changed_count"] == 5
    # No partial overwrite and no append of the new session either.
    assert file_sha(market) == before_hash
    assert table_rows(market) == before_rows

    log = open_observation_log(market)
    observation = log.get_observation(outcome.observation_id)
    assert observation["rows"][0][0] == "2026-09-21"  # candidate retained as observed
    assert observation["rows"][-1][0] == "2026-09-28"
    events = log.list_events(outcome.observation_id)
    assert [e["event_type"] for e in events] == ["OBSERVED", "ADMISSION_DECISION", "REVISION_DETECTED"]
    assert events[-1]["detail"]["market_db_modified"] is False
    assert events[-1]["detail"]["action"] == "NO_AUTOMATIC_REPAIR_OR_REBUILD"
    assert log.blocked_symbols("run-1") == ["TPB"]
    assert log.blocked_symbols("other-run") == []
    # A blocked candidate created no version and no applied session.
    assert log.latest_symbol_version("TPB") is None
    assert log.session_provenance("TPB", "2026-09-25")["origin"] == "BASELINE_LEGACY"


# ------------------------------------------------------------------ fixture 3
def test_dgw_like_small_rebase_is_detected_by_exact_comparison(tmp_path: Path) -> None:
    stored = [
        ("2026-09-23", 44.00, 45.00, 43.50, 44.40, 1_500_000),
        ("2026-09-24", 44.40, 45.20, 44.10, 44.60, 1_600_000),
        ("2026-09-25", 44.60, 45.60, 44.20, 44.60, 1_642_200),
    ]
    candidate = [stored[0], stored[1], ("2026-09-25", 44.60, 45.60, 44.20, 44.61, 1_642_200)]
    market = make_market(tmp_path, {"DGW": stored})
    before = file_sha(market)

    outcome = admit(market, candidate, symbol="DGW", ctx=context(start="2026-09-23"))

    assert outcome.result is AdmissionResult.BLOCKED_REVISION_CHANGED
    sample = outcome.detail["changed_sample"]
    assert [item["session"] for item in sample] == ["2026-09-25"]
    assert sample[0]["stored"][3] == "44.6000" and sample[0]["candidate"][3] == "44.6100"
    assert file_sha(market) == before


def test_volume_only_change_is_also_a_revision(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    outcome = admit(market, [A, (*B[:5], B[5] + 1), C])
    assert outcome.result is AdmissionResult.BLOCKED_REVISION_CHANGED


# ------------------------------------------------------------------ fixture 4
def test_provider_omitting_a_stored_overlap_session_is_blocked(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    before = file_sha(market)

    outcome = admit(market, [B, C])  # 2026-09-24 omitted

    assert outcome.result is AdmissionResult.BLOCKED_OVERLAP_INCOMPLETE
    assert outcome.detail["missing_from_candidate_sample"] == ["2026-09-24"]
    assert file_sha(market) == before
    assert open_observation_log(market).observation_state(outcome.observation_id) == "blocked"


def test_empty_candidate_for_a_stored_window_is_blocked_and_retained(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    before = file_sha(market)
    outcome = admit_price_batch(
        None, symbol="AAA", context=context(), market_db_path=market, now=NOW
    )
    assert outcome.result is AdmissionResult.BLOCKED_OVERLAP_INCOMPLETE
    assert file_sha(market) == before
    assert open_observation_log(market).get_observation(outcome.observation_id)["rows"] == []


def test_candidate_without_any_overlap_anchor_is_blocked(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    before = file_sha(market)
    # Window entirely after the stored history: nothing to verify the basis against.
    outcome = admit(market, [("2026-10-05", 12.0, 13.0, 11.5, 12.5, 10)], ctx=context(start="2026-10-04"))
    assert outcome.result is AdmissionResult.BLOCKED_OVERLAP_INCOMPLETE
    assert outcome.reason == "NO_STORED_SESSION_IN_REQUEST_WINDOW"
    assert file_sha(market) == before


def test_unstored_session_inside_stored_history_is_not_silently_filled(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, C]})
    before = file_sha(market)
    outcome = admit(market, [A, B, C])
    assert outcome.result is AdmissionResult.BLOCKED_HISTORY_EXTENSION
    assert outcome.detail["unstored_inside_history_sample"] == ["2026-09-25"]
    assert file_sha(market) == before


# ------------------------------------------------------------------ fixture 5
def test_crash_after_observation_reconciles_without_duplicate_or_silent_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    before = file_sha(market)

    def crash(*_args, **_kwargs):
        raise SimulatedCrash("process killed after observation, before market application")

    with monkeypatch.context() as patch:
        patch.setattr(market_admission, "_insert_rows", crash)
        with pytest.raises(SimulatedCrash):
            admit(market, [A, B, C])

    log = open_observation_log(market)
    pending = log.pending_observations("AAA")
    assert len(pending) == 1 and pending[0]["application_state"] == "admitted"
    assert file_sha(market) == before  # observation durable, market untouched
    observation_id = pending[0]["observation_id"]
    assert len(log.list_observations("AAA")) == 1

    # Next run (same request) resumes the same observation: no duplicate.
    outcome = admit(market, [A, B, C])
    assert outcome.observation_id == observation_id
    assert outcome.result is AdmissionResult.ADMITTED_APPEND
    assert len(log.list_observations("AAA")) == 1
    assert log.observation_state(observation_id) == "applied"
    assert [r[2] for r in table_rows(market)] == ["2026-09-24", "2026-09-25", "2026-09-28"]
    assert log.pending_observations("AAA") == []


def test_crash_after_market_commit_is_reconciled_idempotently(tmp_path: Path, monkeypatch) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})

    def crash(*_args, **_kwargs):
        raise SimulatedCrash("killed after market commit, before the application record")

    with monkeypatch.context() as patch:
        patch.setattr(ObservationLog, "record_application", crash)
        with pytest.raises(SimulatedCrash):
            admit(market, [A, B, C])

    rows_after_crash = table_rows(market)
    assert len(rows_after_crash) == 3  # the append did happen
    log = open_observation_log(market)
    (pending,) = log.pending_observations("AAA")
    first_id = pending["observation_id"]
    assert log.latest_symbol_version("AAA") is None  # not yet attributed

    # Next day's run: different request window, therefore a different candidate.
    next_candidate = [A, B, C, ("2026-10-05", 12.0, 13.0, 11.5, 12.5, 10)]
    outcome = admit(market, next_candidate, ctx=context(start="2026-09-21"))

    assert outcome.result is AdmissionResult.ADMITTED_APPEND
    assert outcome.appended_sessions == ("2026-10-05",)
    assert log.observation_state(first_id) == "applied"
    first_events = log.list_events(first_id)
    reconciled = [e for e in first_events if e["event_type"] == "APPLICATION_RESULT"]
    assert len(reconciled) == 1 and reconciled[0]["detail"]["reconciled"] is True
    assert reconciled[0]["detail"]["appended_sessions"] == ["2026-09-28"]
    # 2026-09-28 is attributed to the recovered observation, exactly once.
    assert log.session_provenance("AAA", "2026-09-28")["observation_id"] == first_id
    assert len(table_rows(market)) == 4 and len(log.list_observations("AAA")) == 2
    # Version chain is linked: second version's parent is the recovered one.
    latest = log.latest_symbol_version("AAA")
    assert latest["seq"] == 2
    first_version = log.session_provenance("AAA", "2026-09-28")["version_id"]
    assert latest["parent_version_id"] == first_version


def test_stale_pending_observation_is_superseded_not_applied(tmp_path: Path, monkeypatch) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})

    def crash(*_args, **_kwargs):
        raise SimulatedCrash("killed")

    with monkeypatch.context() as patch:
        patch.setattr(market_admission, "_insert_rows", crash)
        with pytest.raises(SimulatedCrash):
            admit(market, [A, B, C])
    before = file_sha(market)
    log = open_observation_log(market)
    (pending,) = log.pending_observations("AAA")

    # The provider's data for the same window changed in the meantime.
    changed_c = ("2026-09-28", 11.0, 12.5, 10.8, 12.01, 3000)
    outcome = admit(market, [A, B, changed_c])

    assert outcome.observation_id != pending["observation_id"]
    assert log.observation_state(pending["observation_id"]) == "superseded"
    assert outcome.result is AdmissionResult.ADMITTED_APPEND  # evaluated on its own merits
    superseding = log.get_observation(outcome.observation_id)
    assert superseding["supersedes_observation_id"] == pending["observation_id"]
    # The stale observation's row (close 12.0) was never applied.
    assert file_sha(market) != before
    assert table_rows(market)[-1][6] == 12.01


def test_application_failure_rolls_back_and_is_recorded(tmp_path: Path, monkeypatch) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    before = file_sha(market)

    def boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    with monkeypatch.context() as patch:
        patch.setattr(market_admission, "_insert_rows", boom)
        outcome = admit(market, [A, B, C])
    assert outcome.result is AdmissionResult.FAILED_APPLICATION
    assert file_sha(market) == before
    log = open_observation_log(market)
    assert log.observation_state(outcome.observation_id) == "failed"
    # A retry of the same candidate resumes the same observation and succeeds.
    retry = admit(market, [A, B, C])
    assert retry.observation_id == outcome.observation_id and retry.applied
    assert len(log.list_observations("AAA")) == 1


# ------------------------------------------------------------------ fixture 6
class FakeQuote:
    payload: dict[str, pd.DataFrame] = {}
    calls: list[str] = []

    def __init__(self, symbol: str, source: str) -> None:
        self.symbol, self.source = symbol, source

    def history(self, start: str, end: str, interval: str) -> pd.DataFrame:
        FakeQuote.calls.append(self.symbol)
        return FakeQuote.payload[self.symbol].copy()


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls(2026, 10, 5, 16, 30) if tz is None else NOW.astimezone(tz)


@pytest.fixture()
def wired(tmp_path: Path, monkeypatch):
    """Point the shared writer, updater and backfill at a temporary database."""
    import core.database as database
    import scripts.backfill_market_data as backfill
    import scripts.update_data as updater
    from sqlalchemy import create_engine

    market = make_market(tmp_path, {"AAA": [A, B]})
    monkeypatch.setattr(database, "DATABASE_PATH", market)
    monkeypatch.setattr(database, "engine", create_engine(f"sqlite:///{market.as_posix()}"))
    monkeypatch.setattr(market_admission, "utc_now", lambda: NOW)
    for module in (updater, backfill):
        monkeypatch.setattr(module, "Quote", FakeQuote)
        monkeypatch.setattr(module, "datetime", FixedDatetime)
        monkeypatch.setattr(module.time, "sleep", lambda *_: None)
    monkeypatch.setattr(updater, "REQUEST_DELAY_SECONDS", 0)
    monkeypatch.setattr(updater, "RETRY_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(updater, "FAILED_LOG_PATH", str(tmp_path / "failed.txt"))
    monkeypatch.setattr(backfill, "REQUEST_DELAY_SECONDS", 0)
    FakeQuote.payload, FakeQuote.calls = {}, []
    return market, updater, backfill, database


def test_save_price_data_has_no_unguarded_mode(wired) -> None:
    market, _updater, _backfill, database = wired
    with pytest.raises(TypeError):
        database.save_price_data(frame([A], "AAA"))  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        database.save_price_data(frame([A], "AAA"), context=None)
    with pytest.raises(ValueError):
        database.save_price_data(
            pd.concat([frame([A], "AAA"), frame([A], "BBB")]), context=context()
        )

    before = file_sha(market)
    changed = ("2026-09-25", 10.50, 12.00, 10.00, 11.05, 2000)
    outcome = database.save_price_data(frame([A, changed], "AAA"), context=context())
    assert outcome.blocked and file_sha(market) == before  # the old REPLACE behaviour is gone


def test_updater_blocks_on_revision_and_does_not_retry_it(wired) -> None:
    market, updater, _backfill, _database = wired
    FakeQuote.payload["AAA"] = frame([A, (*B[:4], 11.05, B[5]), C])
    before = file_sha(market)

    status = updater.update_symbol("AAA")
    assert status is updater.UpdateStatus.REVISION_BLOCKED
    assert file_sha(market) == before

    total, issues = updater.update_all_symbols(["AAA"])
    assert total == 0 and issues == ["AAA"]  # fail closed: blocked symbol is a pipeline issue
    assert FakeQuote.calls.count("AAA") == 2  # one per update call; never re-fetched by the retry rounds


def test_updater_identical_overlap_appends_and_succeeds(wired) -> None:
    market, updater, _backfill, _database = wired
    FakeQuote.payload["AAA"] = frame([A, B, C])
    assert updater.update_symbol("AAA") is updater.UpdateStatus.SUCCESS
    assert [row[2] for row in table_rows(market)] == ["2026-09-24", "2026-09-25", "2026-09-28"]
    total, issues = updater.update_all_symbols(["AAA"])
    assert (total, issues) == (1, [])


def test_updater_main_exits_nonzero_for_blocked_symbol(wired, monkeypatch) -> None:
    market, updater, _backfill, _database = wired
    FakeQuote.payload["AAA"] = frame([A, (*B[:4], 11.05, B[5]), C])
    monkeypatch.setattr(sys, "argv", ["update_data", "--symbols", "AAA"])
    assert updater.main() == 1


def test_updater_treats_empty_provider_response_as_failed_fetch(wired) -> None:
    _market, updater, _backfill, _database = wired
    FakeQuote.payload["AAA"] = frame([])
    assert updater.update_symbol("AAA") is updater.UpdateStatus.RETRYABLE_ERROR


def test_backfill_cannot_bypass_the_guard(wired) -> None:
    market, _updater, backfill, _database = wired
    before = file_sha(market)
    FakeQuote.payload["AAA"] = frame([A, (*B[:4], 11.05, B[5]), C])

    ok = backfill.backfill_symbol(
        "AAA", start_date=datetime(2026, 9, 20), end_date=datetime(2026, 10, 5)
    )
    assert ok is False
    assert file_sha(market) == before

    FakeQuote.payload["AAA"] = frame([A, B, C])
    ok = backfill.backfill_symbol(
        "AAA", start_date=datetime(2026, 9, 20), end_date=datetime(2026, 10, 5)
    )
    assert ok is True
    assert [row[2] for row in table_rows(market)] == ["2026-09-24", "2026-09-25", "2026-09-28"]
    log = open_observation_log(market)
    modes = {o["source_mode"] for o in log.list_observations("AAA")}
    assert modes == {"BACKFILL"}  # recorded by the same guard, with its own mode


def test_no_other_source_module_writes_prices_outside_the_guard() -> None:
    """Writer inventory: only reviewed modules may contain price-table writes."""
    root = Path(__file__).resolve().parents[1]
    forbidden = ("INSERT OR REPLACE INTO prices", "REPLACE INTO prices")
    mutating = ("INSERT INTO prices", "DELETE FROM prices", "UPDATE prices")
    allowed_mutation = {
        "core/market_admission.py",  # the guarded append
        "quantlab/transactional_market_data.py",  # D4 shadow store, not wired to production
    }
    offenders: list[str] = []
    skipped_parts = {".venv", "venv", "node_modules", "site-packages", ".git", "research", "tests"}
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        if skipped_parts & set(path.relative_to(root).parts[:-1]):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(token in text for token in forbidden):
            offenders.append(f"{relative}: REPLACE")
        if relative not in allowed_mutation and any(token in text for token in mutating):
            offenders.append(f"{relative}: mutation")
    assert offenders == []

    # The shadow store must not be reachable from a production entrypoint.
    for entrypoint in ("scripts/update_data.py", "scripts/backfill_market_data.py", "scripts/run_daily.py", "core/database.py"):
        source = (root / entrypoint).read_text(encoding="utf-8")
        assert "transactional_market_data" not in source


def test_cleanup_is_dry_run_and_apply_is_disabled(wired) -> None:
    """P1-6: maintenance would change the dataset without advancing its version."""
    market, _updater, _backfill, database = wired
    connection = sqlite3.connect(market)
    connection.execute("DROP INDEX ux_prices_symbol_time")
    connection.execute("INSERT INTO prices(symbol,time,open,high,low,close,volume) VALUES ('AAA','2026-09-24',10,11,9.5,10.5,1000)")
    connection.commit()
    connection.close()
    before = file_sha(market)

    result = database.cleanup_price_duplicates()
    assert result["redundant_identical_rows"] == 1 and result["conflicting_groups"] == 0
    with pytest.raises(database.MaintenanceDisabledError):
        database.cleanup_price_duplicates(apply=True)
    assert file_sha(market) == before  # nothing deleted, no index created
    assert not resolve_observation_log_path(market).exists()  # and nothing logged

    connection = sqlite3.connect(market)
    connection.execute("INSERT INTO prices(symbol,time,open,high,low,close,volume) VALUES ('AAA','2026-09-24',10,11,9.5,10.6,1000)")
    connection.commit()
    connection.close()
    assert database.cleanup_price_duplicates()["conflicting_groups"] == 1


def test_update_cleanup_only_never_mutates(wired, monkeypatch) -> None:
    market, updater, _backfill, _database = wired
    before = file_sha(market)
    monkeypatch.setattr(sys, "argv", ["update_data", "--cleanup-only"])
    assert updater.main() == 0
    assert file_sha(market) == before


def test_quarantine_apply_is_disabled_and_dry_run_is_read_only(tmp_path: Path, monkeypatch, capsys) -> None:
    import scripts.quarantine_invalid_ohlc as quarantine

    market = make_market(tmp_path, {"AAA": [A, B]})
    connection = sqlite3.connect(market)
    connection.execute("INSERT INTO prices(symbol,time,open,high,low,close,volume) VALUES ('AAA','2026-09-28',0,1,1,1,1)")
    connection.commit()
    connection.close()
    before, mtime = file_sha(market), market.stat().st_mtime_ns

    monkeypatch.setattr(sys, "argv", ["quarantine", "--db", str(market), "--apply"])
    assert quarantine.main() == 2
    monkeypatch.setattr(sys, "argv", ["quarantine", "--db", str(market)])
    assert quarantine.main() == 0
    assert "Invalid OHLC rows: 1" in capsys.readouterr().out
    assert file_sha(market) == before and market.stat().st_mtime_ns == mtime
    assert len(table_rows(market)) == 3
    assert not resolve_observation_log_path(market).exists()


# ------------------------------------------------------------------ fixture 7
def test_eight_year_boundary_never_merges_legacy_prefix_with_rebuilt_history(tmp_path: Path) -> None:
    legacy_prefix = [
        ("2018-08-07", 5.0, 5.5, 4.9, 5.2, 10),
        ("2018-08-08", 5.2, 5.6, 5.0, 5.4, 11),
    ]
    market = make_market(tmp_path, {"AAA": [*legacy_prefix, A, B]})
    before_rows = table_rows(market)
    # Backfill window starts well after the oldest stored session (8y boundary).
    ctx = context(start="2026-09-20", end="2026-10-05", mode="BACKFILL")

    ok = admit(market, [A, B, C], ctx=ctx)
    assert ok.result is AdmissionResult.ADMITTED_APPEND
    assert ok.detail["legacy_sessions_outside_window"] == 2
    after = table_rows(market)
    assert after[:4] == before_rows  # legacy prefix and window rows untouched (ids included)
    assert [r[2] for r in after[4:]] == ["2026-09-28"]

    # A rebased window must not be "repaired" and must not touch the legacy prefix.
    second = tmp_path / "second"
    second.mkdir()
    market2 = make_market(second, {"AAA": [*legacy_prefix, A, B]})
    before_hash = file_sha(market2)
    rebased = [(s, o * 0.8, h * 0.8, l * 0.8, c * 0.8, v) for s, o, h, l, c, v in (A, B)]
    blocked = admit(market2, rebased, ctx=ctx)
    assert blocked.result is AdmissionResult.BLOCKED_REVISION_CHANGED
    assert file_sha(market2) == before_hash

    # A candidate reaching back before the stored history is not merged either.
    third = tmp_path / "third"
    third.mkdir()
    market3 = make_market(third, {"AAA": [*legacy_prefix, A, B]})
    before_hash = file_sha(market3)
    earlier = [("2018-08-01", 4.0, 4.5, 3.9, 4.2, 5), *legacy_prefix]
    extension = admit(
        market3,
        [*earlier, A, B],
        ctx=context(start="2018-07-01", end="2026-10-05", mode="BACKFILL"),
    )
    assert extension.result is AdmissionResult.BLOCKED_HISTORY_EXTENSION
    assert extension.reason == "CANDIDATE_EXTENDS_HISTORY_BACKWARDS"
    assert file_sha(market3) == before_hash


def test_initial_backfill_for_a_new_symbol_is_admitted_and_logged(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    outcome = admit(market, [A, B, C], symbol="NEW", ctx=context(mode="BACKFILL"))
    assert outcome.result is AdmissionResult.ADMITTED_INITIAL_LOAD
    assert outcome.rows_appended == 3
    assert [r[2] for r in table_rows(market) if r[1] == "NEW"] == ["2026-09-24", "2026-09-25", "2026-09-28"]


# --------------------------------------------------------------------- baseline
def test_baseline_registration_is_read_only_and_labelled(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B], "BBB": [A]})
    before_hash, before_mtime = file_sha(market), market.stat().st_mtime_ns

    log = open_observation_log(market)
    baseline = log.ensure_initial_baseline(market, now=NOW)

    assert file_sha(market) == before_hash and market.stat().st_mtime_ns == before_mtime
    assert baseline["file_sha256"] == before_hash
    assert baseline["row_count"] == 3 and baseline["symbol_count"] == 2
    assert (baseline["first_session"], baseline["last_session"]) == ("2026-09-24", "2026-09-25")
    assert baseline["provenance_label"] == "LEGACY_UNVERIFIED"
    assert baseline["point_in_time_label"] == "NOT_POINT_IN_TIME"
    assert baseline["universe_label"] == "SURVIVORSHIP_CURRENT_CONSTITUENTS"
    assert baseline["price_basis_label"] == "PRICE_ADJUSTMENT_UNKNOWN"
    assert baseline["registered_at_utc"].startswith("2026-10-05T09:30:00")
    assert baseline["database_file_mtime_utc"]

    # Independently computed logical content hash.
    digest = hashlib.sha256()
    connection = sqlite3.connect(market)
    for symbol, session, o, h, l, c, v in connection.execute(
        "SELECT symbol,time,open,high,low,close,volume FROM prices ORDER BY symbol,time"
    ):
        digest.update(f"{symbol}|{session}|{o:.4f}|{h:.4f}|{l:.4f}|{c:.4f}|{v}\n".encode())
    connection.close()
    assert baseline["content_sha256"] == digest.hexdigest()

    # Idempotent, single, and immutable.
    again = log.ensure_initial_baseline(market, now=datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert again["baseline_id"] == baseline["baseline_id"]
    assert again["registered_at_utc"] == baseline["registered_at_utc"]
    connection = sqlite3.connect(log.path)
    assert connection.execute("SELECT COUNT(*) FROM market_baselines").fetchone()[0] == 1
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        connection.execute("UPDATE market_baselines SET provenance_label='VERIFIED'")
    connection.close()


def test_baseline_is_registered_before_the_first_guarded_mutation(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    original_hash = file_sha(market)
    admit(market, [A, B, C])
    baseline = open_observation_log(market).get_baseline()
    # The baseline describes the database as it was *before* the append.
    assert baseline["file_sha256"] == original_hash and baseline["row_count"] == 2


def test_log_tables_are_append_only(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    outcome = admit(market, [A, B, C])
    connection = sqlite3.connect(open_observation_log(market).path)
    for statement in (
        "DELETE FROM source_observations",
        "UPDATE source_observations SET symbol='X'",
        "DELETE FROM admission_events",
        "UPDATE admission_events SET application_state='blocked'",
        "DELETE FROM symbol_versions",
        "DELETE FROM applied_sessions",
        "DELETE FROM market_baselines",
    ):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute(statement)
    connection.close()
    assert outcome.applied


# ---------------------------------------------------------------------- hashing
def test_hashes_and_identities_are_deterministic(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    base = admit(market, [A, B, C])
    ids = {base.observation_id}
    hashes = {base.batch_hash}

    for variant_dir, variant in (
        ("v1", frame([C, B, A], "AAA")),  # row order
        ("v2", frame([A, B, C], "AAA").rename(columns=str.upper)),  # column case
        (
            "v3",
            frame([A, B, C], "AAA").astype(
                {"open": "float32", "volume": "float64"}
            ).astype({"open": "float64"}),
        ),  # dtype noise
        ("v4", frame([A, B, C], "AAA").assign(close=lambda d: d["close"] + 1e-9)),  # below 4 dp
    ):
        directory = tmp_path / variant_dir
        directory.mkdir()
        other = make_market(directory, {"AAA": [A, B]})
        result = admit_price_batch(
            variant, symbol="AAA", context=context(), market_db_path=other,
            now=datetime(2026, 10, 5, 9, 45, tzinfo=timezone.utc),  # later fetch time
        )
        ids.add(result.observation_id)
        hashes.add(result.batch_hash)
    assert len(ids) == 1 and len(hashes) == 1

    # A real value difference changes both hashes.
    directory = tmp_path / "v5"
    directory.mkdir()
    other = make_market(directory, {"AAA": [A, B]})
    changed = admit(other, [A, B, (*C[:4], 12.01, C[5])])
    assert changed.batch_hash not in hashes and changed.observation_id not in ids

    # Pinned vector: guards against accidental changes to the canonical form.
    log = open_observation_log(market)
    stored = log.get_observation(base.observation_id)
    assert stored["rows"][0] == ["2026-09-24", "10.0000", "11.0000", "9.5000", "10.5000", "1000"]
    assert stored["normalization_version"] == "ohlcv-norm.v1"
    assert stored["price_unit"] == "THOUSAND_VND_PER_SHARE" and stored["volume_unit"] == "SHARES"


def test_observation_carries_the_required_provenance_fields(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    outcome = admit(
        market,
        [A, B, C],
        ctx=context(run_id="run-42", fetched_at_utc=datetime(2026, 10, 5, 9, 29, tzinfo=timezone.utc)),
    )
    record = open_observation_log(market).get_observation(outcome.observation_id)
    assert record["first_run_id"] == "run-42"
    assert (record["source"], record["source_mode"], record["package_name"], record["package_version"]) == (
        "KBS", "UPDATE", "vnstock", "4.0.2",
    )
    assert record["endpoint"].startswith("vnstock.api.quote.Quote")
    assert (record["request_start"], record["request_end"], record["interval"]) == ("2026-09-20", "2026-10-05", "1D")
    assert record["completed_session_cutoff"] == "2026-10-05"
    assert record["first_fetched_at_utc"].startswith("2026-10-05T09:29:00")
    assert record["price_basis"] == "PRICE_ADJUSTMENT_UNKNOWN"
    assert record["batch_hash"] == outcome.batch_hash and record["row_count"] == 3
    assert record["supersedes_observation_id"] is None


def test_run_id_env_name_matches_operation_history() -> None:
    from quantctl.run_history import RUN_ID_ENV

    assert market_observation_log.RUN_ID_ENV == RUN_ID_ENV


def test_kbs_context_links_the_current_run(monkeypatch) -> None:
    monkeypatch.setenv(market_observation_log.RUN_ID_ENV, "run-7")
    ctx = market_admission.kbs_context("UPDATE", "2026-09-25", datetime(2026, 10, 5))
    assert ctx.run_id == "run-7" and ctx.source == "KBS" and ctx.request_end == "2026-10-05"


# ------------------------------------------------------------------- validation
def test_structurally_invalid_candidates_are_rejected_before_any_observation(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    before = file_sha(market)
    log = open_observation_log(market)
    cases = {
        "DUPLICATE_SESSION_KEYS": [A, A, B],
        "INVALID_OHLCV": [A, (*B[:2], 9.0, 10.0, 11.0, B[5]), C],  # high < low
        "REQUEST_RANGE_VIOLATION": [A, B, ("2026-09-01", 9, 10, 8, 9, 1)],
    }
    for reason, items in cases.items():
        outcome = admit(market, items)
        assert outcome.result is AdmissionResult.REJECTED_INVALID_CANDIDATE and outcome.reason == reason
    assert admit(market, [A, B], symbol="AAA", ctx=context()).applied  # valid candidate still works
    wrong = admit_price_batch(
        frame([A, B], "BBB"), symbol="AAA", context=context(), market_db_path=market, now=NOW
    )
    assert wrong.reason == "SYMBOL_MISMATCH"
    assert len(log.list_observations("AAA")) == 1  # only the valid one was observed
    assert file_sha(market) == before  # identical rows: nothing to write at all


def test_sessions_after_the_completed_session_cutoff_are_excluded_and_recorded(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    # Tuesday 13:00 ICT: Monday 2026-10-05 is complete, Tuesday is not.
    before_close = datetime(2026, 10, 6, 6, 0, tzinfo=timezone.utc)
    partial = ("2026-10-06", 11.0, 11.5, 10.9, 11.2, 5)
    outcome = admit(market, [A, B, C, partial], now=before_close, ctx=context(end="2026-10-06"))
    assert outcome.appended_sessions == ("2026-09-28",)
    record = open_observation_log(market).get_observation(outcome.observation_id)
    assert record["excluded"] == ["2026-10-06"]
    assert record["completed_session_cutoff"] == "2026-10-05"
    assert [r[2] for r in table_rows(market)] == ["2026-09-24", "2026-09-25", "2026-09-28"]


def test_completed_session_cutoff_rules() -> None:
    def cutoff(*args):
        return completed_session_cutoff(datetime(*args, tzinfo=timezone.utc)).isoformat()

    assert cutoff(2026, 10, 5, 8, 0) == "2026-10-05"  # Mon 15:00 ICT: complete
    assert cutoff(2026, 10, 5, 7, 59) == "2026-10-02"  # Mon 14:59 ICT -> previous trading weekday (Fri)
    assert cutoff(2026, 10, 6, 6, 0) == "2026-10-05"  # Tue 13:00 ICT -> Mon
    assert cutoff(2026, 10, 3, 9, 0) == "2026-10-02"  # Saturday -> Friday
    assert cutoff(2026, 10, 4, 12, 0) == "2026-10-02"  # Sunday -> Friday
    with pytest.raises(ValueError):
        completed_session_cutoff(datetime(2026, 10, 5, 9, 0))


# =====================================================================
# Review round 2 (Sol 6.1): persistent gating, ownership, monotonic state,
# identity, binding, maintenance, P2 corrections. Offline, no sleeps.
# =====================================================================
from core import market_provenance_gate
from core.market_data_integrity import (
    MarketDataIntegrityState,
    check_market_data_integrity,
    require_market_data_integrity,
)
from core.market_observation_log import ObservationLogError
from core.market_provenance_gate import check_market_provenance, require_market_provenance

D = ("2026-09-26", 1, 1, 1, 1, 1)  # a Saturday (weekend fixture)
REBASED_B = ("2026-09-25", 8.40, 9.80, 8.10, 9.00, 2000)


def universe_market(directory: Path) -> Path:
    return make_market(directory, {"VNINDEX": [A, B], "AAA": [A, B], "BBB": [A, B], "CCC": [A, B]})


def required(*symbols: str) -> tuple[str, ...]:
    return (*symbols, "VNINDEX")


def block_symbol(market: Path, symbol: str = "AAA", run_id: str | None = "run-block"):
    outcome = admit(market, [A, REBASED_B], symbol=symbol, ctx=context(run_id=run_id))
    assert outcome.result is AdmissionResult.BLOCKED_REVISION_CHANGED
    return outcome


def integrity(market: Path, *symbols: str):
    return check_market_data_integrity(
        required_symbols=required(*symbols), database_path=market, as_of_date="2026-09-25"
    )


# ------------------------------------------------------------------- P1-1
def test_block_persists_across_store_instances_and_fails_a_later_integrity_check(tmp_path: Path) -> None:
    market = universe_market(tmp_path)
    assert admit(market, [A, B], symbol="AAA").applied  # registers the baseline, no new session
    assert integrity(market, "AAA", "BBB").state is MarketDataIntegrityState.PASS

    outcome = block_symbol(market, "AAA")

    # A brand-new store instance (as a separate process would create) sees it.
    fresh = ObservationLog(resolve_observation_log_path(market), market_path=market, readonly=True)
    blocks = fresh.unresolved_blocks()
    assert [(b["symbol"], b["observation_id"]) for b in blocks] == [("AAA", outcome.observation_id)]
    assert fresh.observation_status(outcome.observation_id) == "BLOCKED_UNRESOLVED"
    assert fresh.dataset_version_identity()["consumable"] is False

    result = integrity(market, "AAA", "BBB")  # a later, independent check
    assert result.state is MarketDataIntegrityState.FAIL_CLOSED
    assert "market provenance" in result.message and "AAA" in result.message
    with pytest.raises(RuntimeError, match="provenance"):
        require_market_data_integrity(
            required_symbols=required("AAA", "BBB"), database_path=market, as_of_date="2026-09-25"
        )


def test_unrelated_symbol_block_does_not_poison_unrelated_data(tmp_path: Path) -> None:
    market = universe_market(tmp_path)
    admit(market, [A, B], symbol="BBB")
    block_symbol(market, "AAA")

    assert integrity(market, "BBB", "CCC").state is MarketDataIntegrityState.PASS
    assert check_market_provenance(market, ["BBB", "VNINDEX"]).state == "PASS"
    assert check_market_provenance(market, ["AAA"]).state == "FAIL_CLOSED"
    assert check_market_provenance(market).blocked_symbols == ("AAA",)  # whole-dataset consumers


def test_no_observation_log_means_not_applicable_and_the_gate_never_creates_one(tmp_path: Path) -> None:
    market = universe_market(tmp_path)
    result = check_market_provenance(market, ["AAA"])
    assert result.state == "NOT_APPLICABLE" and result.ok
    assert not resolve_observation_log_path(market).exists()
    assert integrity(market, "AAA", "BBB").state is MarketDataIntegrityState.PASS


def test_reviewed_resolution_releases_the_gate_without_touching_the_market(tmp_path: Path) -> None:
    market = universe_market(tmp_path)
    outcome = block_symbol(market, "AAA")
    before = file_sha(market)
    log = open_observation_log(market)
    (block,) = log.unresolved_blocks()

    with pytest.raises(ValueError):
        log.resolve_block(block["block_id"], reviewer="", reason="x")
    log.resolve_block(block["block_id"], reviewer="owner", reason="reviewed: keep stored history")
    with pytest.raises(ObservationLogError, match="already resolved"):
        log.resolve_block(block["block_id"], reviewer="owner", reason="again")

    assert log.unresolved_blocks() == []
    assert log.observation_status(outcome.observation_id) == "BLOCKED_RESOLVED"
    assert log.list_resolutions("AAA")[0]["reviewer"] == "owner"
    assert check_market_provenance(market, ["AAA"]).state == "PASS"
    assert integrity(market, "AAA", "BBB").state is MarketDataIntegrityState.PASS
    assert file_sha(market) == before  # resolving never changes market.db
    assert log.dataset_version_identity()["consumable"] is True


def test_a_later_accepted_observation_supersedes_an_auto_resolvable_block(tmp_path: Path) -> None:
    market = universe_market(tmp_path)
    admit(market, [A, B], symbol="AAA")
    block_symbol(market, "AAA")
    assert check_market_provenance(market, ["AAA"]).state == "FAIL_CLOSED"

    # The provider agrees with the stored history again (a different request window).
    accepted = admit(market, [A, B], symbol="AAA", ctx=context(start="2026-09-21"))
    assert accepted.applied
    log = open_observation_log(market)
    assert log.unresolved_blocks() == []
    (resolution,) = log.list_resolutions("AAA")
    assert resolution["resolution_kind"] == "SUPERSEDED_BY_ACCEPTED_OBSERVATION"
    assert resolution["resolved_by_observation_id"] == accepted.observation_id
    assert check_market_provenance(market, ["AAA"]).state == "PASS"


def test_historical_blocks_are_not_permanently_active(tmp_path: Path) -> None:
    market = universe_market(tmp_path)
    admit(market, [A, B], symbol="AAA")
    block_symbol(market, "AAA")
    admit(market, [A, B], symbol="AAA", ctx=context(start="2026-09-21"))
    # The history still contains the block event, but nothing is unresolved.
    log = open_observation_log(market)
    assert any(e["event_type"] == "ADMISSION_DECISION" and e["application_state"] == "blocked" for e in log.list_events(symbol="AAA"))
    assert log.blocked_symbols() == []


def test_repeated_identical_blocked_retry_does_not_stack_unresolved_blocks(tmp_path: Path) -> None:
    market = universe_market(tmp_path)
    first = block_symbol(market, "AAA")
    second = block_symbol(market, "AAA")
    assert first.observation_id == second.observation_id
    log = open_observation_log(market)
    assert len(log.unresolved_blocks()) == 1
    assert len(log.list_receipts(first.observation_id)) == 2  # both fetches retained


def test_scanner_lifecycle_and_forward_refuse_a_blocked_dataset(tmp_path: Path, monkeypatch) -> None:
    market = universe_market(tmp_path)
    admit(market, [A, B], symbol="AAA")
    block_symbol(market, "AAA")

    import strategy.scanner as scanner

    monkeypatch.setattr(scanner, "resolve_market_database_path", lambda *a, **k: market)
    with pytest.raises(RuntimeError, match="provenance"):
        scanner._require_scanner_integrity(("AAA", "BBB"))

    import scripts.run_paper_lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "resolve_market_database_path", lambda *a, **k: market)
    with pytest.raises(RuntimeError, match="provenance"):
        lifecycle.main()

    from quantlab.forward import daily as forward_daily
    from types import SimpleNamespace

    protocol = SimpleNamespace(protocol_id="p", protocol_fingerprint="f", benchmark="VNINDEX")
    activation = SimpleNamespace(protocol_id="p", protocol_fingerprint="f", operational_start_after_session="2026-01-01")
    monkeypatch.setattr(forward_daily, "load_protocol_spec", lambda *_: protocol)
    monkeypatch.setattr(forward_daily, "verify_phase8_authorization", lambda *_: None)
    monkeypatch.setattr(
        forward_daily, "ForwardValidationLedger", lambda *_: SimpleNamespace(activation=lambda _id: activation)
    )

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("snapshot must not be built on a blocked dataset")

    monkeypatch.setattr(forward_daily, "build_market_data_snapshot", must_not_run)
    with pytest.raises(RuntimeError, match="provenance"):
        forward_daily.run_forward_validation_daily(database_path=market, ledger_path=tmp_path / "l.db")


def test_signal_results_and_paper_positions_are_gated(tmp_path: Path, monkeypatch) -> None:
    market = universe_market(tmp_path)
    admit(market, [A, B], symbol="AAA")
    block_symbol(market, "AAA")
    from sqlalchemy import create_engine

    import scripts.update_signal_results as signals

    monkeypatch.setattr(signals, "engine", create_engine(f"sqlite:///{market.as_posix()}"))
    with pytest.raises(RuntimeError, match="provenance"):
        signals.update_all_open_signals()

    import scripts.update_paper_positions as positions

    monkeypatch.setattr(positions, "resolve_market_database_path", lambda *a, **k: market)
    with pytest.raises(RuntimeError, match="provenance"):
        positions.main()


def test_content_drift_outside_the_guard_fails_the_gate(tmp_path: Path) -> None:
    market = universe_market(tmp_path)
    admit(market, [A, B], symbol="AAA")
    connection = sqlite3.connect(market)
    connection.execute("UPDATE prices SET close = 10.51 WHERE symbol='AAA' AND time='2026-09-24'")
    connection.commit()
    connection.close()
    result = check_market_provenance(market, ["AAA"])
    assert result.state == "FAIL_CLOSED" and "BASELINE_SLICE_MISMATCH" in result.message
    # ...and the guard itself refuses to build on top of it.
    blocked = admit(market, [A, B, C], symbol="AAA")
    assert blocked.result is AdmissionResult.BLOCKED_DATASET_MISMATCH
    assert [r for r in table_rows(market) if r[1] == "AAA" and r[2] == C[0]] == []


def test_unattributed_rows_beyond_the_baseline_fail_the_gate(tmp_path: Path) -> None:
    market = universe_market(tmp_path)
    admit(market, [A, B], symbol="AAA")
    connection = sqlite3.connect(market)
    connection.execute("INSERT INTO prices(symbol,time,open,high,low,close,volume) VALUES ('AAA','2026-09-28',1,2,1,2,5)")
    connection.commit()
    connection.close()
    result = check_market_provenance(market, ["AAA"])
    assert result.state == "FAIL_CLOSED" and "UNATTRIBUTED_ROWS" in result.message
    assert open_observation_log(market).session_provenance("AAA", "2026-09-28")["origin"] == "UNATTRIBUTED"


def test_provenance_gate_cli_helpers_are_read_only(tmp_path: Path) -> None:
    market = universe_market(tmp_path)
    admit(market, [A, B], symbol="AAA")
    log_path = resolve_observation_log_path(market)
    before = (file_sha(market), file_sha(log_path))
    require_market_provenance(market, ["AAA"])
    check_market_provenance(market)
    assert (file_sha(market), file_sha(log_path)) == before


# ------------------------------------------------------------------- P1-2
def test_crash_after_commit_then_equal_valued_proposal_does_not_steal_ownership(
    tmp_path: Path, monkeypatch
) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})

    def crash(*_a, **_k):
        raise SimulatedCrash("killed after the market commit")

    with monkeypatch.context() as patch:
        patch.setattr(ObservationLog, "record_application", crash)
        with pytest.raises(SimulatedCrash):
            admit(market, [A, B, C], ctx=context(start="2026-09-20"))
    log = open_observation_log(market)
    (owner,) = log.open_intents("AAA")

    # Another process proposes the very same session with equal values.
    other = admit(market, [A, B, C], ctx=context(start="2026-09-21"))
    assert other.result is AdmissionResult.ADMITTED_NO_NEW_SESSIONS and other.appended_sessions == ()

    owners = {(r["session"], r["observation_id"]) for r in log._read("SELECT * FROM applied_sessions")}
    assert owners == {(C[0], owner["observation_id"])}  # owned exactly once, by the intent holder
    assert log.observation_status(owner["observation_id"]) == "APPLIED"
    assert log.open_intents() == [] and log.unresolved_blocks() == []
    assert log.latest_symbol_version("AAA")["seq"] == 1
    assert check_market_provenance(market, ["AAA"]).state == "PASS"


def test_interleaved_same_session_observations_attribute_exactly_once(tmp_path: Path, monkeypatch) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    original = ObservationLog.persist_observation
    nested: dict[str, object] = {}

    def interleave(self, observation):
        result = original(self, observation)
        if observation["request_start"] == "2026-09-20" and "outcome" not in nested:
            # Observation B runs to completion between A's persist and A's lock.
            nested["outcome"] = admit(market, [A, B, C], ctx=context(start="2026-09-21"))
        return result

    monkeypatch.setattr(ObservationLog, "persist_observation", interleave)
    first = admit(market, [A, B, C], ctx=context(start="2026-09-20"))
    monkeypatch.undo()

    second = nested["outcome"]
    assert second.appended_sessions == (C[0],)
    assert first.result is AdmissionResult.ADMITTED_NO_NEW_SESSIONS and first.applied
    log = open_observation_log(market)
    rows = log._read("SELECT session, observation_id FROM applied_sessions")
    assert [(r["session"], r["observation_id"]) for r in rows] == [(C[0], second.observation_id)]
    assert len(table_rows(market)) == 3
    assert {log.observation_status(first.observation_id), log.observation_status(second.observation_id)} == {"APPLIED"}
    assert log.latest_symbol_version("AAA")["seq"] == 1  # advanced exactly once


def test_crash_after_intent_before_insert_is_abandoned_and_a_competitor_takes_ownership(
    tmp_path: Path, monkeypatch
) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})

    def crash(*_a, **_k):
        raise SimulatedCrash("killed between the intent and the insert")

    with monkeypatch.context() as patch:
        patch.setattr(market_admission, "_insert_rows", crash)
        with pytest.raises(SimulatedCrash):
            admit(market, [A, B, C], ctx=context(start="2026-09-20"))
    log = open_observation_log(market)
    (intent,) = log.open_intents("AAA")
    assert check_market_provenance(market, ["AAA"]).state == "FAIL_CLOSED"  # pending application gates
    assert log.session_provenance("AAA", C[0])["origin"] == "PENDING_APPLICATION"

    winner = admit(market, [A, B, C], ctx=context(start="2026-09-21"))
    assert winner.appended_sessions == (C[0],)
    assert log.open_intents() == []
    assert log.observation_status(intent["observation_id"]) in {"SUPERSEDED", "FAILED"}
    rows = log._read("SELECT session, observation_id FROM applied_sessions")
    assert [(r["session"], r["observation_id"]) for r in rows] == [(C[0], winner.observation_id)]
    assert check_market_provenance(market, ["AAA"]).state == "PASS"

    # The abandoned observation retried later finds the session already stored.
    retry = admit(market, [A, B, C], ctx=context(start="2026-09-20"))
    assert retry.result is AdmissionResult.ADMITTED_NO_NEW_SESSIONS and retry.applied
    assert len(log._read("SELECT 1 FROM applied_sessions")) == 1


def test_open_intent_with_different_values_in_the_market_is_an_ambiguity_block(tmp_path: Path, monkeypatch) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})

    def crash(*_a, **_k):
        raise SimulatedCrash("killed")

    with monkeypatch.context() as patch:
        patch.setattr(market_admission, "_insert_rows", crash)
        with pytest.raises(SimulatedCrash):
            admit(market, [A, B, C])
    connection = sqlite3.connect(market)  # someone else wrote that session with other values
    connection.execute("INSERT INTO prices(symbol,time,open,high,low,close,volume) VALUES ('AAA','2026-09-28',11,12.5,10.8,12.2,3000)")
    connection.commit()
    connection.close()

    outcome = admit(market, [A, B, C], ctx=context(start="2026-09-21"))
    assert outcome.result is AdmissionResult.BLOCKED_DATASET_MISMATCH
    log = open_observation_log(market)
    (block,) = log.unresolved_blocks()
    assert block["auto_resolvable"] is False
    # An accepted observation does not silently clear an ambiguity.
    assert admit(market, [A, B], ctx=context(start="2026-09-22")).result is AdmissionResult.BLOCKED_DATASET_MISMATCH
    assert check_market_provenance(market, ["AAA"]).state == "FAIL_CLOSED"
    assert log._read("SELECT 1 FROM applied_sessions") == []


# ------------------------------------------------------------------- P1-3
def test_applied_state_is_monotonic_across_repeated_identical_retries(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    first = admit(market, [A, B, C], ctx=context(run_id="r1"))
    log = open_observation_log(market)
    version = log.dataset_version_identity()["dataset_version_id"]

    for number in range(3):
        retry = admit(market, [A, B, C], ctx=context(run_id=f"retry-{number}"))
        assert retry.already_applied and retry.version_id == first.version_id
        assert retry.appended_sessions == first.appended_sessions

    events = log.list_events(first.observation_id)
    kinds = [e["event_type"] for e in events]
    assert kinds.count("APPLICATION_RESULT") == 1 and kinds.count("RETRY_RECEIPT") == 3
    assert kinds.index("APPLICATION_RESULT") < kinds.index("RETRY_RECEIPT")
    # No admitted/pending state is ever appended after the terminal APPLIED.
    after = events[kinds.index("APPLICATION_RESULT") + 1 :]
    assert {e["event_type"] for e in after} == {"RETRY_RECEIPT"}
    assert {e["application_state"] for e in after} == {"applied"}
    assert log.observation_state(first.observation_id) == "applied"
    assert log.pending_observations() == [] and log.open_intents() == []
    assert log.dataset_version_identity()["dataset_version_id"] == version
    assert log.latest_symbol_version("AAA")["seq"] == 1
    assert len(log.list_receipts(first.observation_id)) == 4


def test_same_id_crash_recovery_returns_the_recovered_receipt_without_residue(tmp_path: Path, monkeypatch) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})

    def crash(*_a, **_k):
        raise SimulatedCrash("killed after the market commit")

    with monkeypatch.context() as patch:
        patch.setattr(ObservationLog, "record_application", crash)
        with pytest.raises(SimulatedCrash):
            admit(market, [A, B, C])
    log = open_observation_log(market)
    (pending,) = log.pending_observations("AAA")

    again = admit(market, [A, B, C])  # same window, same data: same observation id
    assert again.observation_id == pending["observation_id"]
    assert again.applied and log.observation_state(again.observation_id) == "applied"
    assert again.appended_sessions == (C[0],)
    kinds = [e["event_type"] for e in log.list_events(again.observation_id)]
    assert kinds.count("APPLICATION_RESULT") == 1
    assert log.pending_observations() == [] and log.open_intents() == []
    assert len(table_rows(market)) == 3 and log.latest_symbol_version("AAA")["seq"] == 1


def test_superseded_observation_is_not_terminal_and_can_be_reevaluated(tmp_path: Path, monkeypatch) -> None:
    """SUPERSEDED / FAILED are non-gating and retryable; APPLIED is the only absorbing state."""
    market = make_market(tmp_path, {"AAA": [A, B]})

    def crash(*_a, **_k):
        raise SimulatedCrash("killed")

    with monkeypatch.context() as patch:
        patch.setattr(market_admission, "_insert_rows", crash)
        with pytest.raises(SimulatedCrash):
            admit(market, [A, B, C])
    log = open_observation_log(market)
    (pending,) = log.pending_observations("AAA")
    changed_c = ("2026-09-28", 11.0, 12.5, 10.8, 12.01, 3000)
    admit(market, [A, B, changed_c])
    assert log.observation_status(pending["observation_id"]) == "SUPERSEDED"
    assert check_market_provenance(market, ["AAA"]).state == "PASS"  # superseded does not gate

    # The original data returns: it conflicts with what is now stored -> blocked, not silently applied.
    revived = admit(market, [A, B, C])
    assert revived.observation_id == pending["observation_id"]
    assert revived.result is AdmissionResult.BLOCKED_REVISION_CHANGED
    assert log.observation_status(revived.observation_id) == "BLOCKED_UNRESOLVED"


# ------------------------------------------------------------------- P1-4
def test_package_version_is_part_of_the_observation_identity(tmp_path: Path) -> None:
    ids, hashes = [], []
    for index, version in enumerate(("4.0.2", "4.0.2", "4.0.3")):
        directory = tmp_path / f"d{index}"
        directory.mkdir()
        market = make_market(directory, {"AAA": [A, B]})
        ctx = AdmissionContext(
            source="KBS", endpoint="e", source_mode="UPDATE", package_name="vnstock",
            package_version=version, request_start="2026-09-20", request_end="2026-10-05",
        )
        outcome = admit(market, [A, B, C], ctx=ctx)
        ids.append(outcome.observation_id)
        hashes.append(outcome.batch_hash)
    assert ids[0] == ids[1] and ids[0] != ids[2]  # same version stable, new version distinct
    assert hashes[0] == hashes[1] == hashes[2]  # the data itself is identical

    other_package = AdmissionContext(
        source="KBS", endpoint="e", source_mode="UPDATE", package_name="vnstock-data",
        package_version="4.0.2", request_start="2026-09-20", request_end="2026-10-05",
    )
    directory = tmp_path / "pkg"
    directory.mkdir()
    assert admit(make_market(directory, {"AAA": [A, B]}), [A, B, C], ctx=other_package).observation_id not in ids


@pytest.mark.parametrize(
    "field,value",
    [("endpoint", "other"), ("source_mode", "BACKFILL"), ("normalization_version", "ohlcv-norm.v0"),
     ("request_start", "2026-09-01"), ("completed_session_cutoff", "2026-10-02")],
)
def test_identity_changes_with_every_identity_field(tmp_path: Path, field: str, value: str) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    base = admit(market, [A, B, C])
    other_dir = tmp_path / "x"
    other_dir.mkdir()
    other = make_market(other_dir, {"AAA": [A, B]})
    kwargs = {"source": "KBS", "endpoint": "vnstock.api.quote.Quote(source='KBS').history", "source_mode": "UPDATE",
              "package_name": "vnstock", "package_version": "4.0.2", "request_start": "2026-09-20", "request_end": "2026-10-05"}
    now = NOW
    if field == "normalization_version":
        pass  # covered through the identity function below
    elif field == "completed_session_cutoff":
        now = datetime(2026, 10, 2, 9, 30, tzinfo=timezone.utc)
        kwargs["request_end"] = "2026-10-02"
        value = "2026-10-02"
    else:
        kwargs[field] = value
    if field == "normalization_version":
        a = market_admission.observation_identity("AAA", context(), completed_session_cutoff(NOW), base.batch_hash)
        original = market_admission.NORMALIZATION_VERSION
        try:
            market_admission.NORMALIZATION_VERSION = value
            b = market_admission.observation_identity("AAA", context(), completed_session_cutoff(NOW), base.batch_hash)
        finally:
            market_admission.NORMALIZATION_VERSION = original
        assert a == base.observation_id and b != a
        return
    items = [A, B] if field == "completed_session_cutoff" else [A, B, C]
    changed = admit(other, items, ctx=AdmissionContext(**kwargs), now=now)
    assert changed.observation_id != base.observation_id


def test_stale_or_mismatching_metadata_on_identity_reuse_is_rejected(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    outcome = admit(market, [A, B, C])
    log = open_observation_log(market)
    record = log.get_observation(outcome.observation_id)
    payload = {name: record[name] for name in market_observation_log.IMMUTABLE_OBSERVATION_FIELDS}
    payload.update(
        observation_id=outcome.observation_id, run_id="x", fetched_at_utc="2026-10-06T00:00:00+00:00",
        rows=record["rows"], excluded=record["excluded"], normalization_version=record["normalization_version"],
    )
    assert log.persist_observation(dict(payload))["inserted"] is False  # an honest repeat is fine
    for field, value in (("package_version", "9.9.9"), ("endpoint", "tampered"), ("rows", [["2026-09-24", "1", "1", "1", "1", "1"]])):
        bad = dict(payload)
        bad[field] = value
        with pytest.raises(ObservationLogError, match="immutable observation metadata mismatch"):
            log.persist_observation(bad)
    assert len(log.list_observations("AAA")) == 1


def test_repeated_fetches_keep_separate_receipts_and_first_fetch_metadata(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    t1, t2 = datetime(2026, 10, 5, 9, 10, tzinfo=timezone.utc), datetime(2026, 10, 5, 9, 20, tzinfo=timezone.utc)
    first = admit(market, [A, B, C], ctx=context(run_id="run-1", fetched_at_utc=t1))
    second = admit(market, [A, B, C], ctx=context(run_id="run-2", fetched_at_utc=t2))
    assert first.observation_id == second.observation_id and first.receipt_id != second.receipt_id

    log = open_observation_log(market)
    observation = log.get_observation(first.observation_id)
    assert observation["first_run_id"] == "run-1" and observation["first_fetched_at_utc"].startswith("2026-10-05T09:10")
    receipts = log.list_receipts(first.observation_id)
    assert [(r["run_id"], r["fetched_at_utc"][:16]) for r in receipts] == [
        ("run-1", "2026-10-05T09:10"), ("run-2", "2026-10-05T09:20"),
    ]
    assert receipts[0]["detail"]["first_occurrence"] is True and receipts[1]["detail"]["first_occurrence"] is False
    # Each receipt carries its own outcome (events written for that fetch).
    assert [e["event_type"] for e in receipts[0]["events"]][-1] == "APPLICATION_RESULT"
    assert [e["event_type"] for e in receipts[1]["events"]] == ["RETRY_RECEIPT"]
    assert log.run_summary("run-1")["AAA"]["application_state"] == "applied"


# ------------------------------------------------------------------- P1-5
def test_same_database_registration_is_idempotent_and_reopen_verifies(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    log = open_observation_log(market)
    first = log.ensure_initial_baseline(market)
    second = open_observation_log(market).ensure_initial_baseline(market)
    assert first["baseline_id"] == second["baseline_id"]
    assert first["market_locator"] == market_observation_log.market_locator(market)
    assert len(first["content_sha256"]) == 64 and len(first["file_sha256"]) == 64
    assert open_observation_log(market, readonly=True, require_baseline=True).verify_dataset() == {}


def test_a_different_database_in_the_same_directory_cannot_use_the_same_log(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    other = tmp_path / "other.db"
    sqlite3.connect(other).executescript(SCHEMA)
    log_path = resolve_observation_log_path(market)
    open_observation_log(market).ensure_initial_baseline(market)

    with pytest.raises(ObservationLogError, match="different market dataset"):
        open_observation_log(other, log_path)
    with pytest.raises(ObservationLogError, match="different market dataset"):
        ObservationLog(log_path, market_path=other).ensure_initial_baseline(other)
    with pytest.raises(ObservationLogError, match="different market dataset"):
        admit_price_batch(frame([A, B], "AAA"), symbol="AAA", context=context(), market_db_path=other, log_path=log_path, now=NOW)
    # Default log names are derived from the database file name: no collision by default.
    assert resolve_observation_log_path(market) != resolve_observation_log_path(other)
    assert resolve_observation_log_path(other).name == "other_observations.db"
    assert check_market_provenance(other, log_path=log_path).state == "FAIL_CLOSED"


def test_nonexistent_database_and_log_equal_to_market_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ObservationLogError, match="market database not found"):
        open_observation_log(tmp_path / "nope.db")
    market = make_market(tmp_path, {"AAA": [A, B]})
    before = file_sha(market)
    with pytest.raises(ObservationLogError, match="must differ from the market database"):
        open_observation_log(market, market)
    with pytest.raises(ObservationLogError, match="must differ from the market database"):
        ObservationLog(market, market_path=market)
    with pytest.raises(ObservationLogError, match="must differ from the market database"):
        admit_price_batch(frame([A, B], "AAA"), symbol="AAA", context=context(), market_db_path=market, log_path=market, now=NOW)
    assert file_sha(market) == before  # the market database was never opened for write by the log


def test_moved_or_copied_database_fails_closed_until_re_registered(tmp_path: Path) -> None:
    import shutil

    market = make_market(tmp_path, {"AAA": [A, B]})
    admit(market, [A, B, C])
    moved_dir = tmp_path / "moved"
    moved_dir.mkdir()
    moved = moved_dir / "market.db"
    shutil.copy2(market, moved)
    shutil.copy2(resolve_observation_log_path(market), resolve_observation_log_path(moved))
    with pytest.raises(ObservationLogError, match="different market dataset"):
        open_observation_log(moved)
    assert check_market_provenance(moved).state == "FAIL_CLOSED"


def test_baseline_content_mismatch_fails_safely_without_writes(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    log = open_observation_log(market)
    log.ensure_initial_baseline(market)
    connection = sqlite3.connect(market)
    connection.execute("UPDATE prices SET volume = volume + 1 WHERE time='2026-09-24'")
    connection.commit()
    connection.close()
    before = file_sha(market)

    assert open_observation_log(market).verify_dataset() == {"AAA": ["BASELINE_SLICE_MISMATCH"]}
    outcome = admit(market, [A, B, C])
    assert outcome.result is AdmissionResult.BLOCKED_DATASET_MISMATCH
    assert file_sha(market) == before


# ------------------------------------------------------------------- P1-6
def test_maintenance_paths_are_disabled_and_cannot_advance_history(tmp_path: Path) -> None:
    import core.database as database

    with pytest.raises(database.MaintenanceDisabledError):
        database.cleanup_price_duplicates(apply=True)
    source = (Path(database.__file__)).read_text(encoding="utf-8")
    for token in ("DELETE FROM prices", "UPDATE prices", "INSERT INTO prices", "INSERT OR REPLACE INTO"):
        assert token not in source
    assert not hasattr(ObservationLog, "record_maintenance")


# ------------------------------------------------------------------- P2
def test_session_provenance_distinguishes_every_origin(tmp_path: Path, monkeypatch) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    outcome = admit(market, [A, B, C])
    log = open_observation_log(market)
    assert log.session_provenance("AAA", A[0])["origin"] == "BASELINE_LEGACY"
    assert log.session_provenance("AAA", C[0])["origin"] == "OBSERVATION"
    absent = log.session_provenance("AAA", "2026-09-29")  # never stored
    assert absent["origin"] == "ABSENT" and absent["version_id"] is None
    assert log.session_provenance("ZZZ", A[0])["origin"] == "ABSENT"  # unknown symbol

    connection = sqlite3.connect(market)
    connection.execute("DELETE FROM prices WHERE symbol='AAA' AND time=?", (C[0],))
    connection.commit()
    connection.close()
    removed = log.session_provenance("AAA", C[0])
    assert removed["origin"] == "REMOVED" and removed["observation_id"] == outcome.observation_id

    pending_dir = tmp_path / "pending"
    pending_dir.mkdir()
    pending_market = make_market(pending_dir, {"AAA": [A, B]})

    def crash(*_a, **_k):
        raise SimulatedCrash("killed")

    with monkeypatch.context() as patch:
        patch.setattr(market_admission, "_insert_rows", crash)
        with pytest.raises(SimulatedCrash):
            admit(pending_market, [A, B, C])
    assert open_observation_log(pending_market).session_provenance("AAA", C[0])["origin"] == "PENDING_APPLICATION"


def test_weekend_sessions_are_rejected_before_any_observation(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    before = file_sha(market)
    outcome = admit(market, [A, B, D])
    assert outcome.result is AdmissionResult.REJECTED_INVALID_CANDIDATE and outcome.reason == "WEEKEND_SESSION"
    assert outcome.detail["sessions"] == [D[0]]
    assert open_observation_log(market).list_observations("AAA") == []
    assert file_sha(market) == before


def test_timezone_rule_naive_is_exchange_local_aware_is_converted_to_vietnam_time(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    # 2026-09-27T17:00Z == 2026-09-28T00:00 ICT (Monday). Read as UTC it would be a Sunday.
    aware = pd.DataFrame(
        {
            "time": pd.to_datetime(["2026-09-23T17:00:00Z", "2026-09-24T17:00:00Z", "2026-09-27T17:00:00Z"], utc=True),
            "open": [10.0, 10.5, 11.0], "high": [11.0, 12.0, 12.5], "low": [9.5, 10.0, 10.8],
            "close": [10.5, 11.0, 12.0], "volume": [1000, 2000, 3000], "symbol": "AAA",
        }
    )
    outcome = admit_price_batch(aware, symbol="AAA", context=context(), market_db_path=market, now=NOW)
    assert outcome.result is AdmissionResult.ADMITTED_APPEND and outcome.appended_sessions == (C[0],)

    naive_dir = tmp_path / "naive"
    naive_dir.mkdir()
    other = make_market(naive_dir, {"AAA": [A, B]})
    naive = frame([A, B, C], "AAA").assign(time=lambda d: pd.to_datetime(d["time"]))
    assert admit_price_batch(naive, symbol="AAA", context=context(), market_db_path=other, now=NOW).appended_sessions == (C[0],)

    mixed_dir = tmp_path / "mixed"
    mixed_dir.mkdir()
    third = make_market(mixed_dir, {"AAA": [A, B]})
    mixed = frame([A, B, C], "AAA").assign(time=["2026-09-24T00:00:00+07:00", "2026-09-25T00:00:00+00:00", "2026-09-28"])
    rejected = admit_price_batch(mixed, symbol="AAA", context=context(), market_db_path=third, now=NOW)
    assert rejected.result is AdmissionResult.REJECTED_INVALID_CANDIDATE
    assert rejected.reason in {"MIXED_TIMEZONES", "INVALID_TIME"}


def test_ohlcv_is_revalidated_after_rounding(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    before = file_sha(market)
    tiny = ("2026-09-28", 0.00004, 1.0, 0.00004, 0.5, 10)  # positive before, 0.0000 after 4dp rounding
    outcome = admit(market, [A, B, tiny])
    assert outcome.result is AdmissionResult.REJECTED_INVALID_CANDIDATE and outcome.reason == "INVALID_OHLCV"
    assert outcome.detail["stage"] == "POST_NORMALIZATION"
    assert open_observation_log(market).list_observations("AAA") == []
    assert file_sha(market) == before


def test_append_only_is_enforced_against_replace_and_the_api_has_no_mutators(tmp_path: Path) -> None:
    """Triggers stop UPDATE/DELETE/INSERT OR REPLACE through SQL; a privileged writer
    that drops triggers or swaps the file is explicitly out of scope (see the design doc)."""
    market = make_market(tmp_path, {"AAA": [A, B]})
    admit(market, [A, B, C])
    log = open_observation_log(market)
    connection = sqlite3.connect(log.path)  # default pragmas: REPLACE would not fire DELETE triggers
    statements = {
        "admission_events": "INSERT OR REPLACE INTO admission_events(event_id,symbol,event_type,application_state,at_utc,detail_json) VALUES (1,'AAA','X','observed','t','{}')",
        "source_observations": "INSERT OR REPLACE INTO source_observations(seq,observation_id,batch_hash,source,endpoint,source_mode,package_name,package_version,normalization_version,symbol,interval,request_start,request_end,completed_session_cutoff,first_fetched_at_utc,price_unit,volume_unit,price_basis,row_count,rows_json,excluded_json,recorded_at_utc) VALUES (1,'x','x','x','x','x','x','x','x','AAA','1D','a','b','c','d','p','v','b',0,'[]','[]','t')",
        "applied_sessions": "INSERT OR REPLACE INTO applied_sessions(symbol,session,observation_id,version_id,applied_at_utc) SELECT symbol,session,observation_id,version_id,applied_at_utc FROM applied_sessions",
        "market_baselines": "INSERT OR REPLACE INTO market_baselines SELECT * FROM market_baselines",
        "symbol_versions": "INSERT OR REPLACE INTO symbol_versions SELECT * FROM symbol_versions",
        "fetch_receipts": "INSERT OR REPLACE INTO fetch_receipts SELECT * FROM fetch_receipts",
    }
    snapshot = {name: connection.execute(f"SELECT COUNT(*), MAX(rowid) FROM {name}").fetchone() for name in statements}
    for name, statement in statements.items():
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute(statement)
        assert connection.execute(f"SELECT COUNT(*), MAX(rowid) FROM {name}").fetchone() == snapshot[name]
    connection.close()
    public = [n for n in dir(ObservationLog) if not n.startswith("_")]
    assert not [n for n in public if n.startswith(("update", "delete", "remove", "replace", "drop", "truncate"))]


# -------------------------------------------------------- dataset versioning
def test_dataset_version_advances_only_on_a_successful_append(tmp_path: Path, monkeypatch) -> None:
    market = universe_market(tmp_path)
    admit(market, [A, B], symbol="AAA")  # no new sessions (baseline registered here)
    log = open_observation_log(market)
    v0 = log.dataset_version_identity()["dataset_version_id"]

    block_symbol(market, "AAA")  # blocked: no advance
    assert log.dataset_version_identity()["dataset_version_id"] == v0

    def crash(*_a, **_k):
        raise SimulatedCrash("killed")

    with monkeypatch.context() as patch:  # pending: no advance
        patch.setattr(market_admission, "_insert_rows", crash)
        with pytest.raises(SimulatedCrash):
            admit(market, [A, B, C], symbol="BBB")
    assert log.dataset_version_identity()["dataset_version_id"] == v0
    assert log.dataset_version_identity()["pending_applications"] == ["BBB"]

    appended = admit(market, [A, B, C], symbol="CCC")  # successful append: advance, once
    v1 = log.dataset_version_identity()["dataset_version_id"]
    assert appended.appended_sessions == (C[0],) and v1 != v0
    assert len(log._read("SELECT 1 FROM symbol_versions")) == 1  # CCC only; BBB stays pending

    admit(market, [A, B, C], symbol="CCC")  # retry of an applied observation: no advance
    admit(market, [A, B, C], symbol="CCC", ctx=context(start="2026-09-21"))  # no-op application: no advance
    assert log.dataset_version_identity()["dataset_version_id"] == v1


def test_partial_multi_symbol_run_is_representable_per_symbol(tmp_path: Path) -> None:
    market = universe_market(tmp_path)
    admit(market, [A, B], symbol="AAA", ctx=context(run_id="warm"))
    ctx = context(run_id="run-multi")
    ok = admit(market, [A, B, C], symbol="BBB", ctx=ctx)
    bad = admit(market, [A, REBASED_B], symbol="AAA", ctx=ctx)
    assert ok.applied and bad.blocked

    log = open_observation_log(market)
    summary = log.run_summary("run-multi")
    assert summary["BBB"]["application_state"] == "applied" and summary["AAA"]["application_state"] == "blocked"
    identity = log.dataset_version_identity()
    assert identity["unresolved_block_symbols"] == ["AAA"] and identity["consumable"] is False
    assert set(identity["symbol_versions"]) == {"BBB"}
    assert check_market_provenance(market, ["BBB"]).state == "PASS"
    assert check_market_provenance(market, ["AAA"]).state == "FAIL_CLOSED"


def test_applied_sessions_can_only_be_attributed_through_the_owners_open_intent(tmp_path: Path) -> None:
    """Ownership is never inferred from equal values: no intent, no attribution."""
    market = make_market(tmp_path, {"AAA": [A, B]})
    admit(market, [A, B], symbol="AAA")  # registers the baseline
    outcome = admit(market, [A, B, C], symbol="AAA", ctx=context(start="2026-09-21"))
    log = open_observation_log(market)
    assert outcome.appended_sessions == (C[0],)
    # A third observation (never admitted, no intent) tries to claim the stored session.
    claimant = admit(market, [A, REBASED_B, C], symbol="AAA", ctx=context(start="2026-09-23"))
    assert claimant.blocked  # an observation that was never admitted and holds no intent
    with pytest.raises(ObservationLogError, match="open application intent"):
        log.record_application(
            observation_id=claimant.observation_id, symbol="AAA", admission_result="ADMITTED_APPEND",
            appended_sessions=[C[0]], row_count=3, last_session=C[0], content_sha256="x", run_id=None,
        )
    # The refused call changed nothing.
    assert [(r["session"], r["observation_id"]) for r in log._read("SELECT session, observation_id FROM applied_sessions")] == [
        (C[0], outcome.observation_id)
    ]


# =====================================================================
# Review round 3 (Sol 6.1): concurrent no-op defects. Deterministic
# interleavings only: the decision's finalization arguments are captured
# and replayed after the competing admission, no sleeps or threads.
# =====================================================================
D2 = ("2026-09-29", 12.0, 12.6, 11.8, 12.4, 4000)


def capture_finalization(market: Path, items: list[tuple], ctx: AdmissionContext, symbol: str = "AAA") -> dict:
    """Run an admission up to the finalization of its no-op/application and
    return the exact arguments it would have passed (a delayed finalization)."""
    holder: dict = {}

    def grab(self, **kwargs):
        holder.update(kwargs)
        raise SimulatedCrash("finalization delayed past the serialization boundary")

    original = ObservationLog.record_application
    ObservationLog.record_application = grab  # type: ignore[assignment]
    try:
        with pytest.raises(SimulatedCrash):
            admit(market, items, symbol=symbol, ctx=ctx)
    finally:
        ObservationLog.record_application = original  # type: ignore[assignment]
    return holder


def no_supersede(monkeypatch) -> None:
    """Disable the stale-pending sweep so the log-level ordering guards are
    exercised on their own (the sweep is a second, independent protection)."""
    monkeypatch.setattr(market_admission, "_supersede_stale_pending", lambda *a, **k: None)


# ------------------------------------------------------------------- P1-1
def test_delayed_noop_cannot_clear_a_newer_revision_block_full_admission(tmp_path: Path) -> None:
    market = universe_market(tmp_path)
    admit(market, [A, B], symbol="AAA")
    delayed = capture_finalization(market, [A, B], context(start="2026-09-21"))
    assert delayed["appended_sessions"] == () and delayed["bound_version_id"].startswith("baseline:")

    newer = admit(market, [A, REBASED_B], symbol="AAA", ctx=context(start="2026-09-22"))
    assert newer.result is AdmissionResult.BLOCKED_REVISION_CHANGED

    log = open_observation_log(market)
    with pytest.raises(ObservationLogError, match="not in the admitted state"):
        log.record_application(**delayed)  # the older no-op was superseded by the newer admission
    assert [b["observation_id"] for b in log.unresolved_blocks()] == [newer.observation_id]
    assert log.observation_status(newer.observation_id) == "BLOCKED_UNRESOLVED"
    assert log.dataset_version_identity()["consumable"] is False
    assert check_market_provenance(market, ["AAA"]).state == "FAIL_CLOSED"
    assert log.list_resolutions("AAA") == []


def test_delayed_noop_that_is_still_admitted_does_not_resolve_a_later_block(tmp_path: Path, monkeypatch) -> None:
    """Ordering guard on its own: the older decision is still legitimately admitted,
    yet a block recorded after its serialization point is never lifted by it."""
    market = universe_market(tmp_path)
    admit(market, [A, B], symbol="AAA")
    delayed = capture_finalization(market, [A, B], context(start="2026-09-21"))
    no_supersede(monkeypatch)
    newer = admit(market, [A, REBASED_B], symbol="AAA", ctx=context(start="2026-09-22"))
    assert newer.blocked

    log = open_observation_log(market)
    (block,) = log.unresolved_blocks()
    assert block["block_id"] > delayed["decision_event_id"]  # created after A's decision

    receipt = log.record_application(**delayed)  # A finalizes late
    assert receipt["appended_sessions"] == []
    assert [b["block_id"] for b in log.unresolved_blocks()] == [block["block_id"]]
    assert log.list_resolutions("AAA") == []
    assert log.dataset_version_identity()["consumable"] is False
    assert check_market_provenance(market, ["AAA"]).state == "FAIL_CLOSED"
    assert log.observation_status(newer.observation_id) == "BLOCKED_UNRESOLVED"


def test_noop_resolves_an_older_block_only_when_it_revalidated_every_affected_session(tmp_path: Path) -> None:
    market = universe_market(tmp_path)
    admit(market, [A, B], symbol="AAA")
    older = block_symbol(market, "AAA")  # block on session 2026-09-25, before the accepting decision
    log = open_observation_log(market)
    (block,) = log.unresolved_blocks()
    assert block["affected_sessions"] == ["2026-09-25"]

    accepted = admit(market, [A, B], symbol="AAA", ctx=context(start="2026-09-21"))
    assert accepted.result is AdmissionResult.ADMITTED_NO_NEW_SESSIONS
    (resolution,) = log.list_resolutions("AAA")
    assert resolution["block_event_id"] == block["block_id"]
    assert resolution["resolved_by_observation_id"] == accepted.observation_id
    assert resolution["detail"]["decision_event_id"] > block["block_id"]
    assert log.observation_status(older.observation_id) == "BLOCKED_RESOLVED"
    assert check_market_provenance(market, ["AAA"]).state == "PASS"


def test_a_block_outside_the_validated_sessions_is_not_resolved(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"VNINDEX": [A, B, C], "AAA": [A, B, C]})
    admit(market, [A, B, C], symbol="AAA")
    block_symbol_c = admit(market, [A, REBASED_B, C], symbol="AAA", ctx=context(start="2026-09-20"))
    assert block_symbol_c.blocked  # affects 2026-09-25 only

    # An accepting no-op whose window only reaches the last session never compared 2026-09-25.
    narrow = admit(market, [C], symbol="AAA", ctx=context(start="2026-09-28"))
    assert narrow.result is AdmissionResult.ADMITTED_NO_NEW_SESSIONS
    log = open_observation_log(market)
    assert [b["observation_id"] for b in log.unresolved_blocks()] == [block_symbol_c.observation_id]
    assert log.list_resolutions("AAA") == []
    assert check_market_provenance(market, ["AAA"]).state == "FAIL_CLOSED"


def test_a_superseded_decision_cannot_resolve_anything(tmp_path: Path) -> None:
    market = universe_market(tmp_path)
    admit(market, [A, B], symbol="AAA")
    block_symbol(market, "AAA")  # an older block that A *would* resolve
    delayed = capture_finalization(market, [A, B], context(start="2026-09-21"))

    log = open_observation_log(market)
    log.append_event(
        observation_id=delayed["observation_id"], symbol="AAA", event_type="SUPERSEDED",
        application_state="superseded", admission_result="SUPERSEDED", detail={"superseded_by": "test"},
    )
    with pytest.raises(ObservationLogError, match="not in the admitted state"):
        log.record_application(**delayed)
    assert len(log.unresolved_blocks()) == 1 and log.list_resolutions("AAA") == []
    assert log.observation_status(delayed["observation_id"]) == "SUPERSEDED"


def test_a_newer_contradictory_decision_on_the_same_sessions_keeps_the_older_block(tmp_path: Path, monkeypatch) -> None:
    market = universe_market(tmp_path)
    admit(market, [A, B], symbol="AAA")
    block_symbol(market, "AAA")  # older block on 2026-09-25
    delayed = capture_finalization(market, [A, B], context(start="2026-09-21"))
    no_supersede(monkeypatch)
    admit(market, [A, (*B[:4], 10.90, B[5])], symbol="AAA", ctx=context(start="2026-09-22"))  # newer, contradicts 09-25

    log = open_observation_log(market)
    assert len(log.unresolved_blocks()) == 2
    log.record_application(**delayed)
    assert len(log.unresolved_blocks()) == 2 and log.list_resolutions("AAA") == []


# ------------------------------------------------------------------- P1-2
def test_delayed_noop_binds_to_its_own_decision_version_not_the_latest(tmp_path: Path, monkeypatch) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    first = admit(market, [A, B, C])  # V1: three rows
    log = open_observation_log(market)
    v1 = log.latest_symbol_version("AAA")
    assert v1["version_id"] == first.version_id

    delayed = capture_finalization(market, [A, B, C], context(start="2026-09-21"))
    assert delayed["bound_version_id"] == v1["version_id"]
    no_supersede(monkeypatch)
    second = admit(market, [A, B, C, D2], ctx=context(start="2026-09-22"))  # V2 appended meanwhile
    v2 = log.latest_symbol_version("AAA")
    assert second.appended_sessions == (D2[0],) and v2["version_id"] != v1["version_id"]

    receipt = log.record_application(**delayed)  # A's delayed finalization
    assert receipt["version_id"] == v1["version_id"] == receipt["parent_version_id"]
    assert receipt["content_sha256"] == v1["content_sha256"]
    assert second.version_id == v2["version_id"]
    # No new version was created by the no-op and no version/content pair contradicts another.
    assert log.latest_symbol_version("AAA")["version_id"] == v2["version_id"]
    assert len(log._read("SELECT 1 FROM symbol_versions WHERE symbol='AAA'")) == 2
    pairs: dict[str, set[str]] = {}
    for event in log.list_events(symbol="AAA"):
        if event["event_type"] == "APPLICATION_RESULT":
            pairs.setdefault(event["detail"]["version_id"], set()).add(event["detail"]["content_sha256"])
    assert all(len(hashes) == 1 for hashes in pairs.values())
    assert pairs[v1["version_id"]] == {v1["content_sha256"]} and pairs[v2["version_id"]] == {v2["content_sha256"]}
    assert v1["content_sha256"] != v2["content_sha256"]

    # A repeated retry of A returns the same V1-bound receipt: no version, no residue.
    retry = admit(market, [A, B, C], ctx=context(start="2026-09-21", run_id="again"))
    assert retry.already_applied and retry.version_id == v1["version_id"]
    assert log.latest_symbol_version("AAA")["version_id"] == v2["version_id"]
    assert log.pending_observations() == [] and log.open_intents() == []
    assert log.observation_state(delayed["observation_id"]) == "applied"


def test_a_noop_without_its_decision_binding_is_refused(tmp_path: Path) -> None:
    market = universe_market(tmp_path)
    admit(market, [A, B], symbol="AAA")
    delayed = capture_finalization(market, [A, B], context(start="2026-09-21"))
    log = open_observation_log(market)
    unbound = {k: v for k, v in delayed.items() if k != "bound_version_id"}
    with pytest.raises(ObservationLogError, match="must carry the dataset version bound"):
        log.record_application(**unbound)  # never falls back to "latest"
    assert log.observation_state(delayed["observation_id"]) == "admitted"  # nothing was written


def test_append_refuses_a_version_that_moved_since_its_decision(tmp_path: Path) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    delayed = capture_finalization(market, [A, B, C], context(start="2026-09-20"))
    assert delayed["appended_sessions"] == (C[0],)
    log = open_observation_log(market)
    stale = dict(delayed, bound_version_id="version-that-no-longer-exists")
    with pytest.raises(ObservationLogError, match="version advanced since the admission decision"):
        log.record_application(**stale)


# -------------------------------------------------- empty-finalization state checks
def test_empty_finalization_validates_the_observation_state(tmp_path: Path) -> None:
    market = universe_market(tmp_path)
    admit(market, [A, B], symbol="AAA")
    log = open_observation_log(market)
    version = log.current_version_ref("AAA")
    base = dict(
        symbol="AAA", admission_result="ADMITTED_NO_NEW_SESSIONS", appended_sessions=(), row_count=2,
        last_session=B[0], content_sha256="x", run_id=None, bound_version_id=version,
    )

    blocked = block_symbol(market, "AAA")  # BLOCKED_UNRESOLVED
    with pytest.raises(ObservationLogError, match="empty finalization refused"):
        log.record_application(observation_id=blocked.observation_id, **base)
    (block,) = log.unresolved_blocks()
    log.resolve_block(block["block_id"], reviewer="owner", reason="reviewed")  # BLOCKED_RESOLVED is still not admitted
    with pytest.raises(ObservationLogError, match="empty finalization refused"):
        log.record_application(observation_id=blocked.observation_id, **base)

    delayed = capture_finalization(market, [A, B], context(start="2026-09-21"))
    log.append_event(
        observation_id=delayed["observation_id"], symbol="AAA", event_type="SUPERSEDED",
        application_state="superseded", admission_result="SUPERSEDED", detail={},
    )
    with pytest.raises(ObservationLogError, match="empty finalization refused"):
        log.record_application(**delayed)

    # Observed only (never decided) and an unknown observation are refused too.
    persisted = open_observation_log(market).persist_observation(
        {**{k: log.get_observation(blocked.observation_id)[k] for k in market_observation_log.IMMUTABLE_OBSERVATION_FIELDS},
         "observation_id": "obs-observed-only", "batch_hash": "h-observed-only", "run_id": None,
         "fetched_at_utc": "2026-10-05T09:30:00+00:00",
         "rows": [[A[0], "10.0000", "11.0000", "9.5000", "10.5000", "1000"]], "excluded": []}
    )
    assert persisted["inserted"]
    for observation_id in ("obs-observed-only", "obs-does-not-exist"):
        with pytest.raises(ObservationLogError, match="empty finalization refused"):
            log.record_application(observation_id=observation_id, **base)
    assert log.dataset_version_identity()["dataset_version_id"]  # nothing was recorded by the refusals
    assert len(log._read("SELECT 1 FROM admission_events WHERE event_type='APPLICATION_RESULT' "
                         "AND observation_id IN ('obs-observed-only','obs-does-not-exist')")) == 0


def test_a_stale_decision_cannot_finalize_after_the_observation_was_redecided(tmp_path: Path) -> None:
    market = universe_market(tmp_path)
    admit(market, [A, B], symbol="AAA")
    delayed = capture_finalization(market, [A, B], context(start="2026-09-21"))
    log = open_observation_log(market)
    log.append_event(
        observation_id=delayed["observation_id"], symbol="AAA", event_type="ADMISSION_DECISION",
        application_state="admitted", admission_result="ADMITTED_NO_NEW_SESSIONS", detail={"affected_sessions": []},
    )
    with pytest.raises(ObservationLogError, match="empty finalization refused"):
        log.record_application(**delayed)  # bound to the earlier decision, which is no longer the current one


# ------------------------------------------------------- serialization model
def test_noop_is_finalized_inside_the_market_lock_and_appends_after_it(tmp_path: Path, monkeypatch) -> None:
    original = ObservationLog.record_application
    observed: list[tuple[bool, bool]] = []

    def lock_free() -> bool:
        probe = sqlite3.connect(market, timeout=0, isolation_level=None)
        try:
            probe.execute("BEGIN IMMEDIATE")
            probe.execute("ROLLBACK")
            return True
        except sqlite3.OperationalError:
            return False
        finally:
            probe.close()

    def spy(self, **kwargs):
        observed.append((bool(kwargs["appended_sessions"]), lock_free()))
        return original(self, **kwargs)

    market = make_market(tmp_path, {"AAA": [A, B]})
    monkeypatch.setattr(ObservationLog, "record_application", spy)
    admit(market, [A, B], ctx=context(start="2026-09-21"))  # no-op
    admit(market, [A, B, C], ctx=context(start="2026-09-22"))  # append
    assert observed == [(False, False), (True, True)]  # no-op: lock held; append: market already committed


# =====================================================================
# Review round 4 (Sol 6.1): decision_event_id is mandatory for no-op finalization.
# =====================================================================
def test_noop_finalization_without_decision_id_is_refused_after_a_newer_state(tmp_path: Path, monkeypatch) -> None:
    market = make_market(tmp_path, {"AAA": [A, B]})
    first = admit(market, [A, B, C])  # V1 / H1
    log = open_observation_log(market)
    v1 = log.latest_symbol_version("AAA")
    delayed = capture_finalization(market, [A, B, C], context(start="2026-09-21"))  # A: no-op decision at V1
    assert delayed["bound_version_id"] == v1["version_id"] and first.applied
    no_supersede(monkeypatch)  # keep A "admitted" so only the API invariant is under test

    newer = admit(market, [A, B, C, D2], ctx=context(start="2026-09-22"))  # B: later append -> V2
    assert newer.appended_sessions == (D2[0],)
    version_after_b = log.latest_symbol_version("AAA")["version_id"]
    events_before = len(log.list_events(symbol="AAA"))
    resolutions_before = log.list_resolutions("AAA")

    omitted = {k: v for k, v in delayed.items() if k != "decision_event_id"}
    with pytest.raises(ObservationLogError, match="decision_event_id"):
        log.record_application(**omitted)
    with pytest.raises(ObservationLogError, match="decision_event_id"):
        log.record_application(**dict(omitted, decision_event_id=None))

    assert log.observation_state(delayed["observation_id"]) == "admitted"  # no APPLIED created
    assert log.application_result(delayed["observation_id"]) is None
    assert log.latest_symbol_version("AAA")["version_id"] == version_after_b  # version untouched
    assert len(log.list_events(symbol="AAA")) == events_before  # nothing written at all
    assert log.list_resolutions("AAA") == resolutions_before == []
    assert log.unresolved_blocks() == []
    assert log.open_intents() == []


def test_omitted_decision_id_cannot_resolve_blocks_or_finalize_a_blocked_state(tmp_path: Path) -> None:
    market = universe_market(tmp_path)
    admit(market, [A, B], symbol="AAA")
    block_symbol(market, "AAA")  # an older block A's stale args would resolve
    delayed = capture_finalization(market, [A, B], context(start="2026-09-21"))
    log = open_observation_log(market)
    omitted = {k: v for k, v in delayed.items() if k != "decision_event_id"}
    with pytest.raises(ObservationLogError, match="decision_event_id"):
        log.record_application(**omitted)
    assert len(log.unresolved_blocks()) == 1 and log.list_resolutions("AAA") == []
    assert check_market_provenance(market, ["AAA"]).state == "FAIL_CLOSED"


def test_stale_decision_id_is_refused_and_the_current_exact_decision_finalizes(tmp_path: Path) -> None:
    market = universe_market(tmp_path)
    admit(market, [A, B], symbol="AAA")
    delayed = capture_finalization(market, [A, B], context(start="2026-09-21"))
    log = open_observation_log(market)
    stale_id = delayed["decision_event_id"]
    newer_id = log.append_event(
        observation_id=delayed["observation_id"], symbol="AAA", event_type="ADMISSION_DECISION",
        application_state="admitted", admission_result="ADMITTED_NO_NEW_SESSIONS", detail={"affected_sessions": []},
    )
    with pytest.raises(ObservationLogError, match="empty finalization refused"):
        log.record_application(**delayed)  # old args WITH the old decision id
    assert log.application_result(delayed["observation_id"]) is None

    receipt = log.record_application(**dict(delayed, decision_event_id=newer_id))  # the exact current decision
    assert receipt["decision_event_id"] == newer_id != stale_id
    assert log.observation_state(delayed["observation_id"]) == "applied"
    assert receipt["version_id"] == delayed["bound_version_id"]
