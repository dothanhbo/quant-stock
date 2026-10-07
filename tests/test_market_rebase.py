"""V1 P1-OPS-1: reviewed per-symbol rebase of one corporate-action revision block.

Offline and deterministic: temporary SQLite databases only, no provider calls, no
live ``market.db``, no paper store outside ``tmp_path``. Every block is produced
by the real R1/R2 admission path and every binding by the real R3 session, so the
rebase is validated against genuine log state.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from core import market_rebase
from core.evidence_market_binding import (
    EvidenceQualifier,
    MarketBindingError,
    MarketBindingSession,
    PendingApplicationError,
    Qualification,
    ensure_binding_schema,
    insert_binding,
)
from core.market_admission import AdmissionContext, AdmissionResult, admit_price_batch
from core.market_observation_log import (
    ObservationLog,
    ObservationLogError,
    open_observation_log,
    resolve_observation_log_path,
)
from core.market_provenance_gate import check_market_provenance
from core.market_rebase import (
    RebaseRefused,
    preview_rebase,
    rebase_symbol,
    recover_rebase,
)

NOW = datetime(2026, 10, 5, 9, 30, tzinfo=timezone.utc)  # 16:30 ICT: 2026-10-05 is complete
SCHEMA = """
CREATE TABLE prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT, time TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER
);
CREATE UNIQUE INDEX ux_prices_symbol_time ON prices(symbol, time);
"""
SYMBOLS = ("VNINDEX", "AAA", "BBB")
FACTOR = 0.8  # the provider back-adjusts every session before the ex-date by this factor


class SimulatedCrash(BaseException):
    """Stands in for a process kill: bypasses ``except Exception`` handlers."""


def weekdays_ending(end: date, count: int) -> tuple[str, ...]:
    days, cursor = [], end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return tuple(sorted(days))


#: 31 weekday sessions ending 2026-10-05; the market stores 0..29, index 30 is "today".
SESS = weekdays_ending(date(2026, 10, 5), 31)
LAST_STORED = 29
TODAY = 30
EX_INDEX = 27  # sessions 0..26 are back-adjusted by the provider, 27.. are unchanged
BASELINE_COUNT = 25  # 0..24 legacy baseline, 25..29 admitted through the guard


def bar(symbol: str, index: int, sessions: tuple[str, ...] = SESS) -> tuple:
    rank = SYMBOLS.index(symbol) if symbol in SYMBOLS else 3
    close = round(100.0 + rank * 10.0 + index * (1.0 + rank / 10.0), 4)
    return (sessions[index], round(close - 0.5, 4), round(close + 1.0, 4), round(close - 1.0, 4), close, 1000 + index)


def adjusted(symbol: str, index: int, sessions: tuple[str, ...] = SESS, ex_index: int = EX_INDEX) -> tuple:
    row = bar(symbol, index, sessions)
    if index >= ex_index:
        return row
    day, o, h, l, c, v = row
    return (day, *(round(x * FACTOR, 4) for x in (o, h, l, c)), v)


def context(start: str, end: str, run_id: str | None = None) -> AdmissionContext:
    return AdmissionContext(
        source="KBS",
        endpoint="vnstock.api.quote.Quote(source='KBS').history",
        source_mode="UPDATE",
        package_name="vnstock",
        package_version="4.0.2",
        request_start=start,
        request_end=end,
        run_id=run_id,
    )


def admit(market: Path, symbol: str, rows: list[tuple], start: str, end: str, run_id: str | None = None):
    data = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    data["symbol"] = symbol
    return admit_price_batch(data, symbol=symbol, context=context(start, end, run_id), market_db_path=market, now=NOW)


def make_market(directory: Path, sessions: tuple[str, ...] = SESS, baseline_count: int = BASELINE_COUNT,
                last_stored: int = LAST_STORED, symbols: tuple[str, ...] = SYMBOLS) -> Path:
    path = directory / "market.db"
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.executemany(
        "INSERT INTO prices(symbol,time,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)",
        [(s, *bar(s, i, sessions)) for s in symbols for i in range(baseline_count)],
    )
    connection.commit()
    connection.close()
    open_observation_log(path).ensure_initial_baseline(path, now=NOW)
    for symbol in symbols:  # sessions after the baseline arrive through the real guard
        rows = [bar(symbol, i, sessions) for i in range(baseline_count - 3, last_stored + 1)]
        outcome = admit(path, symbol, rows, sessions[baseline_count - 3], sessions[last_stored])
        assert outcome.result is AdmissionResult.ADMITTED_APPEND, outcome
    return path


def corporate_action_block(market: Path, symbol: str = "AAA", *, first: int = 0, through: int = TODAY,
                           run_id: str | None = "run-ca", sessions: tuple[str, ...] = SESS,
                           ex_index: int = EX_INDEX):
    """The provider proposes its back-adjusted history (long window, e.g. backfill --symbols)."""
    rows = [adjusted(symbol, i, sessions, ex_index) for i in range(first, through + 1)]
    outcome = admit(market, symbol, rows, sessions[first], sessions[through], run_id)
    assert outcome.result is AdmissionResult.BLOCKED_REVISION_CHANGED, outcome
    log = open_observation_log(market, readonly=True)
    (block,) = [b for b in log.unresolved_blocks([symbol]) if b["observation_id"] == outcome.observation_id]
    return outcome, block


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prices(market: Path, symbol: str | None = None) -> list[tuple]:
    with sqlite3.connect(market) as connection:
        if symbol is None:
            return connection.execute("SELECT * FROM prices ORDER BY id").fetchall()
        return connection.execute("SELECT * FROM prices WHERE symbol=? ORDER BY id", (symbol,)).fetchall()


def log_fingerprint(market: Path) -> dict[str, int]:
    path = resolve_observation_log_path(market)
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
        tables = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {t: connection.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in sorted(tables)}


def version_count(market: Path, symbol: str) -> int:
    path = resolve_observation_log_path(market)
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
        return connection.execute("SELECT COUNT(*) FROM symbol_versions WHERE symbol=?", (symbol,)).fetchone()[0]


def dataset_version(market: Path) -> str:
    return str(open_observation_log(market, readonly=True).dataset_version_identity()["dataset_version_id"])


def paper_store(directory: Path, *, positions=(), pending=(), name: str = "paper.db") -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE paper_positions (symbol TEXT PRIMARY KEY, quantity INTEGER NOT NULL,
            average_price REAL NOT NULL, market_price REAL NOT NULL, realized_pnl REAL NOT NULL);
        CREATE TABLE paper_pending_signals (id INTEGER PRIMARY KEY AUTOINCREMENT, signal_date TEXT NOT NULL,
            symbol TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
            processed_date TEXT, reason TEXT, created_at TEXT NOT NULL, UNIQUE(signal_date, symbol));
        """
    )
    connection.executemany("INSERT INTO paper_positions VALUES (?,?,?,?,?)",
                           [(s, q, 100.0, 100.0, 0.0) for s, q in positions])
    connection.executemany(
        "INSERT INTO paper_pending_signals(signal_date,symbol,payload,status,created_at) VALUES (?,?,?,?,?)",
        [(SESS[LAST_STORED], s, "{}", status, "2026-10-02T10:00:00Z") for s, status in pending],
    )
    connection.commit()
    connection.close()
    return path


#: Paper routing for each test: an isolated root whose ACTIVE canonical store
#: (Q70 default ``data/paper_trading_v2.db``) exists and is clean. No live store
#: and no project ``.env`` is ever read by these tests.
PAPER: dict[str, object] = {}


@pytest.fixture(autouse=True)
def isolated_paper_routing(tmp_path: Path):
    root = tmp_path / "paper-root"
    active = paper_store(root / "data", name="paper_trading_v2.db")
    PAPER.clear()
    PAPER.update(root=root, environ={}, active=active)
    yield PAPER
    PAPER.clear()


def do_rebase(market: Path, block: dict, *, paper_paths=(), **overrides):
    arguments = dict(
        paper_environ=PAPER["environ"],
        paper_root=PAPER["root"],
        symbol=block["symbol"],
        block_id=block["block_id"],
        observation_id=block["observation_id"],
        category="CORPORATE_ACTION_REBASE",
        reviewer="owner",
        reason="reviewed issuer disclosure: 25% stock dividend, ex-date " + SESS[EX_INDEX],
        note="external evidence: issuer notice (operator-checked)",
        paper_paths=paper_paths,
    )
    arguments.update(overrides)
    return rebase_symbol(market, **arguments)


def refused(code: str, market: Path, block: dict, **overrides) -> RebaseRefused:
    before_market, before_log = file_sha(market), log_fingerprint(market)
    with pytest.raises(RebaseRefused) as caught:
        do_rebase(market, block, **overrides)
    assert caught.value.code == code, caught.value
    assert file_sha(market) == before_market  # refused -> market.db byte-identical
    assert log_fingerprint(market) == before_log  # ... and nothing appended to the log
    return caught.value


@pytest.fixture()
def blocked(tmp_path: Path):
    market = make_market(tmp_path)
    outcome, block = corporate_action_block(market)
    return market, outcome, block


# ============================================================ 1. normal ingestion
def test_01_a_normal_revision_still_blocks_without_mutating_history(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    before, version = file_sha(market), dataset_version(market)

    outcome, block = corporate_action_block(market)
    # the routine 7-day update window keeps blocking too: ingestion never rebases
    again = admit(market, "AAA", [adjusted("AAA", i) for i in range(25, TODAY + 1)], SESS[25], SESS[TODAY])

    assert again.result is AdmissionResult.BLOCKED_REVISION_CHANGED
    assert file_sha(market) == before and dataset_version(market) == version
    assert block["affected_sessions"] == list(SESS[:EX_INDEX])
    assert check_market_provenance(market, ["AAA"]).state == "FAIL_CLOSED"
    log = open_observation_log(market, readonly=True)
    assert log.symbol_rebases() == [] and log.dataset_version_identity()["consumable"] is False


# ============================================================ 2-4. eligibility
def test_02_rebase_without_a_block_is_refused(blocked) -> None:
    market, outcome, block = blocked
    log = open_observation_log(market, readonly=True)
    applied = [e for e in log.list_events() if e["event_type"] == "APPLICATION_RESULT"][0]
    refused("NO_SUCH_BLOCK", market, {**block, "block_id": 999_999})
    refused("NO_SUCH_BLOCK", market, {**block, "block_id": applied["event_id"]})  # a real event, not a block
    observed = [e for e in log.list_events(outcome.observation_id) if e["event_type"] == "OBSERVED"][0]
    refused("NO_SUCH_BLOCK", market, {**block, "block_id": observed["event_id"]})


def test_03_wrong_block_review_or_category_is_refused(blocked, tmp_path: Path) -> None:
    market, outcome, block = blocked
    refused("BLOCK_SYMBOL_MISMATCH", market, block, symbol="BBB")
    other = admit(market, "BBB", [bar("BBB", i) for i in range(25, LAST_STORED + 1)], SESS[25], SESS[LAST_STORED])
    refused("BLOCK_OBSERVATION_MISMATCH", market, block, observation_id=other.observation_id)
    refused("INVALID_CATEGORY", market, block, category="SPLIT_GUESS")
    refused("REVIEW_REQUIRED", market, block, reviewer=" ")
    refused("REVIEW_REQUIRED", market, block, reason="")

    # a newer block on the same symbol: the reviewed block is no longer the latest proposal
    newer, newer_block = corporate_action_block(market, first=20, run_id="run-2")
    refused("BLOCK_NOT_LATEST", market, block)

    # a block already resolved by ordinary review cannot be rebased
    open_observation_log(market).resolve_block(newer_block["block_id"], reviewer="owner", reason="keep")
    refused("BLOCK_NOT_UNRESOLVED", market, newer_block)


def test_03b_only_a_pure_value_revision_block_can_be_rebased(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    # the provider omits a stored session: BLOCKED_OVERLAP_INCOMPLETE, never rebaseable
    rows = [adjusted("AAA", i) for i in range(0, TODAY + 1) if i != 10]
    outcome = admit(market, "AAA", rows, SESS[0], SESS[TODAY])
    assert outcome.result is AdmissionResult.BLOCKED_REVISION_CHANGED
    (block,) = open_observation_log(market, readonly=True).unresolved_blocks(["AAA"])
    refused("REBASE_REQUIRES_PURE_VALUE_REVISION", market, block)

    incomplete = admit(market, "BBB", [bar("BBB", i) for i in range(20, LAST_STORED + 1) if i != 24],
                       SESS[20], SESS[LAST_STORED])
    assert incomplete.result is AdmissionResult.BLOCKED_OVERLAP_INCOMPLETE
    (bbb,) = open_observation_log(market, readonly=True).unresolved_blocks(["BBB"])
    refused("REBASE_REQUIRES_REVISION_BLOCK", market, bbb)


def test_04_stale_pre_rebase_basis_is_refused(blocked) -> None:
    market, outcome, block = blocked
    with sqlite3.connect(market) as connection:  # the stored basis drifts after the review
        connection.execute("UPDATE prices SET close=close+1 WHERE symbol='AAA' AND time=?", (SESS[3],))
    error = refused("PRE_REBASE_BASIS_DRIFT", market, block)
    assert "VERSION_CONTENT_MISMATCH" in str(error)
    with pytest.raises(RebaseRefused, match="PRE_REBASE_BASIS_DRIFT"):
        preview_rebase(market, symbol="AAA", block_id=block["block_id"],
                       observation_id=block["observation_id"], paper_environ={}, paper_root=PAPER["root"])


def test_04b_a_short_window_never_rebases_part_of_the_history(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    # only a short window was ever observed: rebasing it would leave sessions 0..21 on the old basis
    _, block = corporate_action_block(market, first=22)
    error = refused("INCOMPLETE_REBASE_HISTORY_COVERAGE", market, block)
    assert error.detail["uncovered_sessions"] == 22 and error.detail["first_uncovered"] == SESS[0]


# ============================================================ 5-9. the rebase itself
def test_05_06_07_08_exact_rebase_changes_only_the_reviewed_sessions(blocked) -> None:
    market, outcome, block = blocked
    before_all = prices(market)
    before_other = {s: prices(market, s) for s in ("VNINDEX", "BBB")}
    before_aaa = prices(market, "AAA")
    log = open_observation_log(market, readonly=True)
    other_versions = {s: log.current_version_ref(s) for s in ("VNINDEX", "BBB")}
    old_version, old_aaa_versions = dataset_version(market), version_count(market, "AAA")
    plan = preview_rebase(market, symbol="AAA", block_id=block["block_id"],
                          observation_id=block["observation_id"], paper_environ={}, paper_root=PAPER["root"])
    assert prices(market) == before_all  # the dry run changed nothing

    result = do_rebase(market, block)

    assert result.applied and not result.already_applied
    record = result.rebase
    # 5. only AAA's changed sessions, ids preserved, exact observed values, no append of "today"
    after_aaa = prices(market, "AAA")
    assert [r[0] for r in after_aaa] == [r[0] for r in before_aaa]
    changed = {r[2][:10] for r, o in zip(after_aaa, before_aaa) if r != o}
    assert changed == set(SESS[:EX_INDEX]) == set(plan.sessions)
    for row in after_aaa:
        index = SESS.index(row[2][:10])
        expected = adjusted("AAA", index)
        assert tuple(row[3:]) == (*expected[1:5], expected[5])
    assert SESS[TODAY] not in {r[2][:10] for r in after_aaa}
    assert len(prices(market)) == len(before_all)
    # 6. unrelated symbols byte-for-byte unchanged, versions untouched
    assert {s: prices(market, s) for s in ("VNINDEX", "BBB")} == before_other
    log = open_observation_log(market, readonly=True)
    assert {s: log.current_version_ref(s) for s in ("VNINDEX", "BBB")} == other_versions
    assert log.verify_dataset(market) == {}
    # 7. the dataset version advances exactly once, to the recorded identity
    assert version_count(market, "AAA") == old_aaa_versions + 1
    assert record["pre_dataset_version_id"] == old_version != record["new_dataset_version_id"]
    assert dataset_version(market) == record["new_dataset_version_id"]
    # 8. the reviewed block resolved (only now), with the full audit receipt
    assert log.unresolved_blocks() == []
    (resolution,) = [r for r in log.list_resolutions("AAA") if r["block_event_id"] == block["block_id"]]
    assert resolution["resolution_kind"] == "REBASED_BY_REVIEW"
    assert record["block_event_id"] == block["block_id"] and record["observation_id"] == outcome.observation_id
    assert record["category"] == "CORPORATE_ACTION_REBASE" and record["reviewer"] == "owner"
    assert record["note"].startswith("external evidence")
    assert (record["first_session"], record["last_session"], record["session_count"]) == (
        SESS[0], SESS[EX_INDEX - 1], EX_INDEX)
    assert record["old_content_sha256"] == plan.old_rows_sha256 and record["new_content_sha256"] == plan.new_rows_sha256
    assert record["detail"]["resulting_block_state"] == "REBASED_BY_REVIEW"
    assert sorted(record["detail"]["old_rows"]) == list(SESS[:EX_INDEX])
    assert log.observation_status(outcome.observation_id) != "BLOCKED_UNRESOLVED"
    assert check_market_provenance(market, ["AAA"]).state == "PASS"
    assert log.dataset_version_identity()["consumable"] is True
    # the rebase record and attribution are append-only like every other log table
    path = resolve_observation_log_path(market)
    with sqlite3.connect(path) as connection:
        for statement in ("UPDATE symbol_rebases SET reviewer='x'", "DELETE FROM rebased_sessions"):
            with pytest.raises(sqlite3.DatabaseError):
                connection.execute(statement)


def test_08_block_stays_unresolved_when_the_application_fails(blocked, monkeypatch) -> None:
    market, outcome, block = blocked
    before, version = file_sha(market), dataset_version(market)
    real = market_rebase._update_rows

    def failing(connection, symbol, rows):
        real(connection, symbol, dict(list(rows.items())[:3]))  # partial write inside the transaction
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(market_rebase, "_update_rows", failing)
    with pytest.raises(RebaseRefused, match="REBASE_APPLICATION_FAILED"):
        do_rebase(market, block)
    monkeypatch.setattr(market_rebase, "_update_rows", real)

    assert file_sha(market) == before and dataset_version(market) == version  # full rollback
    log = open_observation_log(market, readonly=True)
    assert [b["block_id"] for b in log.unresolved_blocks()] == [block["block_id"]]
    assert log.open_intents() == [] and log.symbol_rebases() == []
    assert check_market_provenance(market, ["AAA"]).state == "FAIL_CLOSED"

    assert do_rebase(market, block).applied  # the same reviewed rebase then succeeds
    assert open_observation_log(market, readonly=True).unresolved_blocks() == []


def test_09_retry_is_idempotent(blocked) -> None:
    market, outcome, block = blocked
    first = do_rebase(market, block)
    snapshot, fingerprint, version = file_sha(market), log_fingerprint(market), dataset_version(market)

    second = do_rebase(market, block)
    third = do_rebase(market, block, reason="a different reason text", note=None)

    assert second.already_applied and third.already_applied
    assert second.rebase["rebase_id"] == third.rebase["rebase_id"] == first.rebase["rebase_id"]
    assert (file_sha(market), log_fingerprint(market), dataset_version(market)) == (snapshot, fingerprint, version)
    with pytest.raises(RebaseRefused, match="BLOCK_ALREADY_REBASED_DIFFERENTLY"):
        do_rebase(market, block, symbol="BBB")


# ============================================================ 10. next-day behaviour
def test_10_the_next_identical_provider_overlap_does_not_re_block(blocked) -> None:
    market, outcome, block = blocked
    do_rebase(market, block)
    rebased_version = dataset_version(market)

    # next Daily update: the provider's 7-day window on the (now stored) revised basis + the new session
    nxt = admit(market, "AAA", [adjusted("AAA", i) for i in range(25, TODAY + 1)], SESS[25], SESS[TODAY], "run-next")
    assert nxt.result is AdmissionResult.ADMITTED_APPEND and nxt.appended_sessions == (SESS[TODAY],)
    # re-fetching the reviewed long window is the same observation: its existing receipt, no re-block
    same = admit(market, "AAA", [adjusted("AAA", i) for i in range(0, TODAY + 1)], SESS[0], SESS[TODAY], "run-same")
    assert same.already_applied and same.applied and not same.blocked
    assert same.result is AdmissionResult.REBASED_BY_REVIEW and same.observation_id == outcome.observation_id
    # any other full-history window on the revised basis is identical as well
    full = admit(market, "AAA", [adjusted("AAA", i) for i in range(1, TODAY + 1)], SESS[1], SESS[TODAY], "run-full")
    assert full.result is AdmissionResult.ADMITTED_NO_NEW_SESSIONS

    log = open_observation_log(market, readonly=True)
    assert log.unresolved_blocks() == [] and log.open_intents() == []
    assert all(log.blocked_symbols(run) == [] for run in ("run-next", "run-same", "run-full"))
    assert check_market_provenance(market).state == "PASS"
    identity = log.dataset_version_identity()
    assert identity["consumable"] is True and identity["dataset_version_id"] != rebased_version
    assert log.session_provenance("AAA", SESS[TODAY])["origin"] == "OBSERVATION"
    assert log.session_provenance("AAA", SESS[0])["origin"] == "REBASED_HISTORY"


# ============================================================ 11-13. R3 evidence
def _bind_old_evidence(market: Path, evidence_db: Path):
    session = MarketBindingSession(market)
    window = session.bind("FORMATION", "old-window",
                          (("FORMATION_FEATURE_HISTORY", "AAA", SESS[LAST_STORED], SESS[0], "WINDOW"),))
    rebased_point = session.bind("FORMATION", "old-point-rebased", (("FORMATION_CLOSE", "AAA", SESS[26]),))
    kept_point = session.bind("FORMATION", "old-point-kept", (("FORMATION_CLOSE", "AAA", SESS[28]),))
    unrelated = session.bind("FORMATION", "old-bbb",
                             (("FORMATION_FEATURE_HISTORY", "BBB", SESS[LAST_STORED], SESS[0], "WINDOW"),))
    with sqlite3.connect(evidence_db) as connection:
        ensure_binding_schema(connection, "fwd")
        for binding in (window, rebased_point, kept_point, unrelated):
            assert insert_binding(connection, "fwd", binding)
    return session.dataset_version_id, window, rebased_point, kept_point, unrelated


def _stored_bindings(evidence_db: Path) -> list[tuple]:
    with sqlite3.connect(evidence_db) as connection:
        return connection.execute("SELECT * FROM fwd_market_bindings ORDER BY binding_identity").fetchall()


def test_11_12_old_evidence_stays_immutable_and_becomes_incompatible_basis(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    evidence_db = tmp_path / "evidence.db"
    old_version, window, rebased_point, kept_point, unrelated = _bind_old_evidence(market, evidence_db)
    stored_before = _stored_bindings(evidence_db)
    q = EvidenceQualifier(market)
    assert q.qualify(((window, None),)).state == Qualification.BOUND_LEGACY_INPUT.value
    assert q.qualify(((kept_point, None),)).state == Qualification.PROVENANCE_VERIFIED.value

    _, block = corporate_action_block(market)
    q = EvidenceQualifier(market)
    assert q.qualify(((window, None),)).state == Qualification.QUARANTINED_UNRESOLVED_REVISION.value

    result = do_rebase(market, block)

    # 11. the persisted evidence rows are untouched and can never be rebound
    assert _stored_bindings(evidence_db) == stored_before
    assert window["dataset"]["dataset_version_id"] == old_version != result.rebase["new_dataset_version_id"]
    rebound = MarketBindingSession(market).bind(
        "FORMATION", "old-window",
        (("FORMATION_FEATURE_HISTORY", "AAA", SESS[LAST_STORED], SESS[0], "WINDOW"),))
    with sqlite3.connect(evidence_db) as connection, pytest.raises(MarketBindingError):
        insert_binding(connection, "fwd", rebound)
    assert _stored_bindings(evidence_db) == stored_before

    # 12. old-basis evidence is quarantined as incompatible, never verified, never lifted by the rebase
    q = EvidenceQualifier(market)
    old_window = q.qualify(((window, None),))
    assert old_window.state == Qualification.QUARANTINED_INCOMPATIBLE_BASIS.value
    assert any(r.startswith("WINDOW_LINEAGE_DRIFT:AAA") for r in old_window.reasons)
    old_point = q.qualify(((rebased_point, None),))
    assert old_point.state == Qualification.QUARANTINED_INCOMPATIBLE_BASIS.value
    assert any(r.startswith(f"SESSION_PROVENANCE_DRIFT:AAA:{SESS[26]}") for r in old_point.reasons)
    # a session the corporate action did not change keeps its qualification; other symbols too
    assert q.qualify(((kept_point, None),)).state == Qualification.PROVENANCE_VERIFIED.value
    assert q.qualify(((unrelated, None),)).state == Qualification.BOUND_LEGACY_INPUT.value


def test_13_new_evidence_binds_the_new_version_and_is_never_verified(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    _, block = corporate_action_block(market)
    during = MarketBindingSession(market).bind("FORMATION", "during", (("FORMATION_CLOSE", "AAA", SESS[3]),))
    assert EvidenceQualifier(market).qualify(((during, None),)).state == (
        Qualification.QUARANTINED_UNRESOLVED_REVISION.value)

    result = do_rebase(market, block)

    session = MarketBindingSession(market)
    assert session.dataset_version_id == result.rebase["new_dataset_version_id"]
    binding = session.bind(
        "FORMATION", "new",
        (("FORMATION_CLOSE", "AAA", SESS[3]),
         ("FORMATION_FEATURE_HISTORY", "AAA", SESS[LAST_STORED], SESS[0], "WINDOW")),
    )
    assert binding["dataset"]["dataset_version_id"] == result.rebase["new_dataset_version_id"]
    point = next(d for d in binding["dependencies"] if d.get("coverage", "POINT") == "POINT")
    assert point["origin"] == "REBASED_HISTORY" and point["version_id"] == result.rebase["new_version_id"]
    assert point["observation_id"] == block["observation_id"]
    window = next(d for d in binding["dependencies"] if d.get("coverage") == "WINDOW")
    assert window["lineage"]["origin_counts"] == {"OBSERVATION": 3, "REBASED_HISTORY": EX_INDEX}
    verdict = EvidenceQualifier(market).qualify(((binding, None),))
    # operator-asserted history binds descriptively; the rebase never manufactures verification
    assert verdict.state == Qualification.BOUND_LEGACY_INPUT.value and not verdict.qualified


# ============================================================ 14. paper exposure
def test_14_open_paper_position_or_pending_signal_refuses_the_rebase(blocked, tmp_path: Path) -> None:
    market, outcome, block = blocked
    with_position = paper_store(tmp_path, positions=[("AAA", 100)], name="p1.db")
    error = refused("OPEN_POSITION_REQUIRES_CORPORATE_ACTION_RECONCILIATION", market, block,
                    paper_paths=(with_position,))
    assert error.detail["open_positions"][0]["quantity"] == 100
    with_pending = paper_store(tmp_path, pending=[("AAA", "PENDING")], name="p2.db")
    refused("PENDING_SIGNAL_REQUIRES_CORPORATE_ACTION_RECONCILIATION", market, block, paper_paths=(with_pending,))
    unreadable = tmp_path / "corrupt.db"
    unreadable.write_bytes(b"this is not a sqlite database at all" * 100)
    refused("PAPER_STORE_UNREADABLE", market, block, paper_paths=(unreadable,))
    with sqlite3.connect(with_position) as connection:
        position_before = connection.execute("SELECT * FROM paper_positions").fetchall()

    # no exposure on AAA (closed position, other symbol, executed signal) -> allowed; paper stores untouched
    clean = paper_store(tmp_path, positions=[("AAA", 0), ("BBB", 50)], pending=[("AAA", "EXECUTED")], name="p3.db")
    clean_before, active_before = file_sha(clean), file_sha(PAPER["active"])
    result = do_rebase(market, block, paper_paths=(clean,))
    checked = {c["path"]: c for c in result.rebase["detail"]["open_position_check"]["checked_stores"]}
    assert checked[str(clean.resolve())]["roles"] == ["SUPPLEMENTAL"]
    assert checked[str(PAPER["active"].resolve())]["active"] is True  # the mandatory store was checked too
    assert file_sha(clean) == clean_before and file_sha(PAPER["active"]) == active_before
    with sqlite3.connect(with_position) as connection:
        assert connection.execute("SELECT * FROM paper_positions").fetchall() == position_before


# ============================================================ 15-16. crash model
def test_15_crash_before_the_market_commit_leaves_nothing_applied(blocked, monkeypatch) -> None:
    market, outcome, block = blocked
    before, version = file_sha(market), dataset_version(market)

    def crash(connection, symbol, rows):
        raise SimulatedCrash()

    monkeypatch.setattr(market_rebase, "_update_rows", crash)
    with pytest.raises(SimulatedCrash):
        do_rebase(market, block)
    monkeypatch.undo()

    assert file_sha(market) == before and dataset_version(market) == version
    log = open_observation_log(market, readonly=True)
    assert log.symbol_rebases() == [] and [b["block_id"] for b in log.unresolved_blocks()] == [block["block_id"]]
    # the dangling intent keeps every gate closed until it is settled
    assert check_market_provenance(market, ["AAA"]).state == "FAIL_CLOSED"
    with pytest.raises(PendingApplicationError):
        MarketBindingSession(market).bind("FORMATION", "x", (("FORMATION_CLOSE", "AAA", SESS[3]),))

    result = do_rebase(market, block)  # retry abandons the stale intent and applies once
    assert result.applied and not result.recovered
    events = open_observation_log(market, readonly=True).list_events(outcome.observation_id)
    assert [e["admission_result"] for e in events if e["event_type"] == "INTENT_ABANDONED"] == [
        "REBASE_INTENT_ABANDONED"]
    assert version_count(market, "AAA") == 2


@pytest.mark.parametrize("recovery", ["same-command", "next-admission"])
def test_16_crash_after_the_market_commit_is_fail_closed_and_recoverable(blocked, monkeypatch, recovery) -> None:
    market, outcome, block = blocked
    version = dataset_version(market)

    def crash(self, **kwargs):
        raise SimulatedCrash()

    monkeypatch.setattr(ObservationLog, "record_rebase", crash)
    with pytest.raises(SimulatedCrash):
        do_rebase(market, block)
    monkeypatch.undo()

    # market changed, log not finalized: nothing looks resolved, every consumer stays closed
    assert prices(market, "AAA")[0][6] == adjusted("AAA", 0)[4]
    log = open_observation_log(market, readonly=True)
    assert [b["block_id"] for b in log.unresolved_blocks()] == [block["block_id"]]
    assert log.symbol_rebases() == [] and dataset_version(market) == version
    assert len(log.open_intents("AAA")) == 1
    gate = check_market_provenance(market, ["AAA"])
    assert gate.state == "FAIL_CLOSED"
    assert log.dataset_version_identity()["consumable"] is False
    assert log.session_provenance("AAA", SESS[3])["origin"] == "PENDING_APPLICATION"
    with pytest.raises(PendingApplicationError):
        MarketBindingSession(market).bind("FORMATION", "x", (("FORMATION_CLOSE", "AAA", SESS[3]),))

    if recovery == "same-command":
        result = do_rebase(market, block)
        assert result.recovered and result.applied
    else:  # the next Daily admission of AAA finalizes the committed rebase first, then admits
        nxt = admit(market, "AAA", [adjusted("AAA", i) for i in range(25, TODAY + 1)], SESS[25], SESS[TODAY])
        assert nxt.result is AdmissionResult.ADMITTED_APPEND
    log = open_observation_log(market, readonly=True)
    assert log.unresolved_blocks() == [] and log.open_intents() == []
    (record,) = log.symbol_rebases("AAA")
    assert record["block_event_id"] == block["block_id"]
    assert check_market_provenance(market).state == "PASS"
    assert do_rebase(market, block).already_applied


def test_16b_a_partially_present_rebase_is_ambiguous_and_never_finalized(blocked, monkeypatch) -> None:
    market, outcome, block = blocked
    monkeypatch.setattr(ObservationLog, "record_rebase", lambda self, **kw: (_ for _ in ()).throw(SimulatedCrash()))
    with pytest.raises(SimulatedCrash):
        do_rebase(market, block)
    monkeypatch.undo()
    with sqlite3.connect(market) as connection:  # someone half-reverts the market by hand
        connection.execute("UPDATE prices SET close=? WHERE symbol='AAA' AND time=?", (bar("AAA", 0)[4], SESS[0]))

    with pytest.raises(RebaseRefused, match="AMBIGUOUS_REBASE_STATE"):
        do_rebase(market, block)
    nxt = admit(market, "AAA", [adjusted("AAA", i) for i in range(25, TODAY + 1)], SESS[25], SESS[TODAY])
    assert nxt.blocked and not nxt.applied
    log = open_observation_log(market, readonly=True)
    assert log.symbol_rebases() == [] and check_market_provenance(market, ["AAA"]).state == "FAIL_CLOSED"


# ============================================================ 17-18. compatibility, gates
def test_17_ordinary_block_resolution_still_works(blocked, capsys) -> None:
    from scripts import resolve_market_block as cli

    market, outcome, block = blocked
    before = file_sha(market)
    assert cli.main(["--db", str(market), "resolve", "--block-id", str(block["block_id"]),
                     "--reviewer", "owner", "--reason", "x", "--confirm-symbol", "AAA"]) == 2  # no implicit rebase
    assert open_observation_log(market, readonly=True).unresolved_blocks() != []
    assert cli.main(["--db", str(market), "resolve", "--block-id", str(block["block_id"]),
                     "--reviewer", "owner", "--reason", "reviewed: keep stored history"]) == 0
    log = open_observation_log(market, readonly=True)
    assert log.unresolved_blocks() == [] and file_sha(market) == before
    assert log.list_resolutions("AAA")[0]["resolution_kind"] == "REVIEWED"
    assert log.symbol_rebases() == []
    # the deadlock this batch closes: the same proposal blocks again after a plain review
    again = admit(market, "AAA", [adjusted("AAA", i) for i in range(25, TODAY + 1)], SESS[25], SESS[TODAY])
    assert again.result is AdmissionResult.BLOCKED_REVISION_CHANGED


def isolate_cli_paper_routing(monkeypatch, directory: Path, **positions) -> dict[str, Path]:
    """The CLI resolves stores through config.paper_store; point every canonical store at tmp files."""
    import config.paper_store as routing

    stores = {
        "PAPER_DATABASE_PATH": paper_store(directory / "cli", name="generic.db"),
        "PAPER_V2_DATABASE_PATH": paper_store(directory / "cli", name="q70.db", **positions),
        "PAPER_V3_DATABASE_PATH": paper_store(directory / "cli", name="v3.db"),
    }
    cli_environ = {key: str(path) for key, path in stores.items()}
    monkeypatch.setattr(routing, "configured_paper_environment", lambda *, root, environ=None: dict(cli_environ))
    return stores


def test_17b_cli_dry_run_then_explicit_rebase(blocked, capsys, tmp_path: Path, monkeypatch) -> None:
    from scripts import resolve_market_block as cli

    market, outcome, block = blocked
    paper = paper_store(tmp_path)
    isolate_cli_paper_routing(monkeypatch, tmp_path)
    base = ["--db", str(market), "resolve", "--block-id", str(block["block_id"]), "--reviewer", "owner",
            "--reason", "issuer notice reviewed", "--action", "rebase", "--paper-db", str(paper)]
    assert cli.main(base) == 2  # observation id, confirmed symbol and category are mandatory
    assert "REBASE_ARGUMENTS_REQUIRED" in capsys.readouterr().out
    full = [*base, "--observation-id", block["observation_id"], "--confirm-symbol", "AAA",
            "--category", "CORPORATE_ACTION_REBASE"]
    before = file_sha(market)
    assert cli.main([*full, "--dry-run"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["status"] == "ELIGIBLE_DRY_RUN" and dry["plan"]["rebased_session_count"] == EX_INDEX
    assert file_sha(market) == before
    assert cli.main([*full[:-6], "--observation-id", block["observation_id"], "--confirm-symbol", "BBB",
                     "--category", "CORPORATE_ACTION_REBASE"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "BLOCK_SYMBOL_MISMATCH"
    assert cli.main(full) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "REBASED"
    assert cli.main(full) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ALREADY_REBASED"
    assert cli.main(["--db", str(market), "rebases", "--symbol", "aaa"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1


def test_18_global_gates_stay_fail_closed_for_other_unresolved_blocks(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    _, aaa = corporate_action_block(market, "AAA")
    _, bbb = corporate_action_block(market, "BBB")
    assert check_market_provenance(market).state == "FAIL_CLOSED"

    do_rebase(market, aaa)

    log = open_observation_log(market, readonly=True)
    assert [b["block_id"] for b in log.unresolved_blocks()] == [bbb["block_id"]]  # untouched
    assert check_market_provenance(market).state == "FAIL_CLOSED"
    assert check_market_provenance(market, ["BBB"]).state == "FAIL_CLOSED"
    assert check_market_provenance(market, ["AAA", "VNINDEX"]).state == "PASS"
    assert log.dataset_version_identity()["consumable"] is False


def test_older_consistent_blocks_are_superseded_and_inconsistent_ones_are_not(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    # day 1 and day 2: the routine 7-day window blocks on the revised basis
    day1 = admit(market, "AAA", [adjusted("AAA", i) for i in range(22, TODAY + 1)], SESS[22], SESS[TODAY])
    assert day1.blocked
    # a different (wrong) proposal on another day
    odd = [(bar("AAA", i)[0], *(round(x * 0.5, 4) for x in bar("AAA", i)[1:5]), bar("AAA", i)[5])
           for i in range(20, 25)]
    assert admit(market, "AAA", odd, SESS[20], SESS[24]).blocked
    _, block = corporate_action_block(market)  # the long-window observation the operator reviews

    result = do_rebase(market, block)

    log = open_observation_log(market, readonly=True)
    kinds = sorted(r["resolution_kind"] for r in log.list_resolutions("AAA"))
    assert kinds == ["REBASED_BY_REVIEW", "SUPERSEDED_BY_REVIEWED_REBASE"]
    remaining = log.unresolved_blocks(["AAA"])
    assert len(remaining) == 1 and remaining[0]["affected_sessions"] == list(SESS[20:25])
    assert len(result.rebase["detail"]["superseded_block_ids"]) == 1
    assert check_market_provenance(market, ["AAA"]).state == "FAIL_CLOSED"  # still needs its own review




# =====================================================================
# Adversarial review round 1 (P1-1 .. P1-5). Offline, temp SQLite only.
# =====================================================================
from core.market_admission import readmission_identity  # noqa: E402
from core.market_observation_log import BlockOwnedByPendingRebase  # noqa: E402


def setup_window_rows(symbol: str = "AAA") -> list[tuple]:
    """The exact rows make_market admitted for ``symbol`` (an APPLIED observation)."""
    return [bar(symbol, i) for i in range(BASELINE_COUNT - 3, LAST_STORED + 1)]


def replay_setup(market: Path, symbol: str = "AAA"):
    return admit(market, symbol, setup_window_rows(symbol), SESS[BASELINE_COUNT - 3], SESS[LAST_STORED])


# ------------------------------------------------------------ P1-1 receipts
def test_p1_1_case1_old_applied_receipt_conflicting_with_the_rebased_basis_blocks(blocked) -> None:
    market, outcome, block = blocked
    log = open_observation_log(market, readonly=True)
    before = replay_setup(market)  # before the rebase the old receipt is still valid
    assert before.already_applied and before.applied
    do_rebase(market, block)
    snapshot = file_sha(market)

    again = replay_setup(market)  # the very same old APPLIED content (sessions 22..26 now rebased)

    assert not again.already_applied and again.blocked
    assert again.result is AdmissionResult.BLOCKED_REVISION_CHANGED
    assert again.observation_id == readmission_identity(before.observation_id, log.current_version_ref("AAA"))
    assert file_sha(market) == snapshot
    log = open_observation_log(market, readonly=True)
    assert log.observation_status(before.observation_id) == "APPLIED"  # the historical receipt is kept
    assert check_market_provenance(market, ["AAA"]).state == "FAIL_CLOSED"
    # a repeat on the same basis is the same re-admission: no second block
    third = replay_setup(market)
    assert third.observation_id == again.observation_id and third.blocked
    assert len([b for b in log.unresolved_blocks(["AAA"])]) == 1


def test_p1_1_case2_reviewed_observation_replayed_after_the_rebase_appends_its_future_session(blocked) -> None:
    market, outcome, block = blocked
    do_rebase(market, block)
    assert SESS[TODAY] not in {r[2][:10] for r in prices(market, "AAA")}

    replay = admit(market, "AAA", [adjusted("AAA", i) for i in range(0, TODAY + 1)], SESS[0], SESS[TODAY])

    assert replay.result is AdmissionResult.ADMITTED_APPEND and replay.appended_sessions == (SESS[TODAY],)
    assert not replay.already_applied and replay.observation_id != outcome.observation_id
    log = open_observation_log(market, readonly=True)
    assert log.unresolved_blocks() == [] and log.verify_dataset(market) == {}
    assert log.session_provenance("AAA", SESS[TODAY])["observation_id"] == replay.observation_id
    assert check_market_provenance(market).state == "PASS"


def test_p1_1_case3_fully_identical_repeat_after_all_sessions_exist_is_idempotent(blocked) -> None:
    market, outcome, block = blocked
    do_rebase(market, block)
    first = admit(market, "AAA", [adjusted("AAA", i) for i in range(0, TODAY + 1)], SESS[0], SESS[TODAY])
    assert first.result is AdmissionResult.ADMITTED_APPEND
    snapshot, fingerprint, version = file_sha(market), log_fingerprint(market), dataset_version(market)

    second = admit(market, "AAA", [adjusted("AAA", i) for i in range(0, TODAY + 1)], SESS[0], SESS[TODAY])
    third = admit(market, "AAA", [adjusted("AAA", i) for i in range(0, TODAY + 1)], SESS[0], SESS[TODAY])

    assert second.already_applied and third.already_applied and not second.blocked
    assert (file_sha(market), dataset_version(market)) == (snapshot, version)
    observations = log_fingerprint(market)["source_observations"]
    assert observations == fingerprint["source_observations"]  # no new observation identities


def test_p1_1_case4_a_genuinely_new_revision_after_the_rebase_still_blocks(blocked) -> None:
    market, outcome, block = blocked
    do_rebase(market, block)
    snapshot = file_sha(market)
    revised = [adjusted("AAA", i) for i in range(20, LAST_STORED + 1)]
    day, o, h, l, c, v = revised[3]
    revised[3] = (day, *(round(x * 0.5, 4) for x in (o, h, l, c)), v)

    result = admit(market, "AAA", revised, SESS[20], SESS[LAST_STORED])

    assert result.result is AdmissionResult.BLOCKED_REVISION_CHANGED and file_sha(market) == snapshot


# ------------------------------------------------------------ P1-2 paper coverage
def test_p1_2_supplemental_db_never_replaces_the_active_q70_store(blocked, tmp_path: Path) -> None:
    market, outcome, block = blocked
    clean = paper_store(tmp_path, name="clean-supplement.db")
    # 1. the ACTIVE default store holds an open position; a clean supplemental DB is named
    exposed_root = tmp_path / "root-position"
    paper_store(exposed_root / "data", positions=[("AAA", 300)], name="paper_trading_v2.db")
    error = refused("OPEN_POSITION_REQUIRES_CORPORATE_ACTION_RECONCILIATION", market, block,
                    paper_paths=(clean,), paper_root=exposed_root)
    assert error.detail["open_positions"][0]["store"].endswith("paper_trading_v2.db")
    # 2. the active store holds a pending signal
    pending_root = tmp_path / "root-pending"
    paper_store(pending_root / "data", pending=[("AAA", "PENDING")], name="paper_trading_v2.db")
    refused("PENDING_SIGNAL_REQUIRES_CORPORATE_ACTION_RECONCILIATION", market, block,
            paper_paths=(clean,), paper_root=pending_root)
    # 3. a mistyped supplemental path is refused, never skipped
    refused("PAPER_STORE_MISSING", market, block, paper_paths=(tmp_path / "paper_trading_v2_typo.db",))
    # an existing file that is not a paper store (e.g. market.db) is refused too
    refused("PAPER_STORE_NOT_A_PAPER_STORE", market, block, paper_paths=(market,))


def test_p1_2_missing_required_canonical_stores_refuse(blocked, tmp_path: Path) -> None:
    market, outcome, block = blocked
    empty_root = tmp_path / "root-without-stores"  # the ACTIVE Q70 store does not exist
    error = refused("PAPER_STORE_MISSING", market, block, paper_root=empty_root)
    assert error.detail["store"]["active"] is True
    # a CONFIGURED (inactive) store path that is missing also refuses
    refused("PAPER_STORE_MISSING", market, block,
            paper_environ={"PAPER_V3_DATABASE_PATH": str(tmp_path / "configured-but-missing.db")})


def test_p1_2_absent_inactive_unconfigured_store_is_allowed_and_all_clean_is_eligible(blocked, tmp_path: Path) -> None:
    market, outcome, block = blocked
    supplement = paper_store(tmp_path, name="supplement.db")
    # generic and V3 defaults do not exist under the routing root, are inactive and unconfigured
    plan = preview_rebase(market, symbol="AAA", block_id=block["block_id"], observation_id=block["observation_id"],
                          paper_paths=(supplement,), paper_environ={}, paper_root=PAPER["root"])
    states = {Path(c["path"]).name: c["state"] for c in plan.open_position_check["checked_stores"]}
    assert states == {"paper_trading.db": "ABSENT_INACTIVE_UNCONFIGURED",
                      "paper_trading_v2.db": "CHECKED",
                      "paper_trading_v3.db": "ABSENT_INACTIVE_UNCONFIGURED",
                      "supplement.db": "CHECKED"}
    assert do_rebase(market, block, paper_paths=(supplement,)).applied


def test_p1_2_cli_paper_db_is_supplemental(blocked, tmp_path: Path, monkeypatch, capsys) -> None:
    from scripts import resolve_market_block as cli

    market, outcome, block = blocked
    isolate_cli_paper_routing(monkeypatch, tmp_path, positions=[("AAA", 100)])  # exposure in active Q70
    clean = paper_store(tmp_path, name="clean.db")
    before = file_sha(market)
    code = cli.main(["--db", str(market), "resolve", "--block-id", str(block["block_id"]), "--reviewer", "owner",
                     "--reason", "r", "--action", "rebase", "--observation-id", block["observation_id"],
                     "--confirm-symbol", "AAA", "--category", "CORPORATE_ACTION_REBASE", "--paper-db", str(clean)])
    assert code == 2 and file_sha(market) == before
    assert json.loads(capsys.readouterr().out)["code"] == "OPEN_POSITION_REQUIRES_CORPORATE_ACTION_RECONCILIATION"


# ------------------------------------------------------------ P1-3 full history
def test_p1_3_full_stored_history_coverage_is_eligible_and_rebases(blocked) -> None:
    market, outcome, block = blocked
    plan = preview_rebase(market, symbol="AAA", block_id=block["block_id"], observation_id=block["observation_id"],
                          paper_environ={}, paper_root=PAPER["root"])
    assert plan.summary()["history_coverage"] == "FULL_STORED_HISTORY"
    assert plan.summary()["stored_history"] == [SESS[0], SESS[LAST_STORED]]
    result = do_rebase(market, block)
    assert result.rebase["detail"]["history_coverage"] == "FULL_STORED_HISTORY"


def test_p1_3_latest_250_of_300_sessions_is_refused(tmp_path: Path) -> None:
    sessions = weekdays_ending(date(2026, 10, 2), 300)
    market = make_market(tmp_path, sessions=sessions, baseline_count=297, last_stored=299, symbols=("AAA",))
    _, block = corporate_action_block(market, first=50, through=299, sessions=sessions, ex_index=290)
    error = refused("INCOMPLETE_REBASE_HISTORY_COVERAGE", market, block)
    assert error.detail["uncovered_sessions"] == 50


def test_p1_3_noncontiguous_changed_ranges_are_refused(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    rows = [adjusted("AAA", i) for i in range(0, TODAY + 1)]
    for i in range(10, 15):  # a middle segment left on the old basis by the proposal
        rows[i] = bar("AAA", i)
    outcome = admit(market, "AAA", rows, SESS[0], SESS[TODAY])
    assert outcome.result is AdmissionResult.BLOCKED_REVISION_CHANGED
    (block,) = open_observation_log(market, readonly=True).unresolved_blocks(["AAA"])
    error = refused("REBASE_NONCONTIGUOUS_BASIS_CHANGE", market, block)
    assert error.detail["unchanged_inside_changed_range"] == 5


def test_p1_3_recursive_indicators_after_an_allowed_rebase_equal_the_full_adjusted_basis(blocked) -> None:
    from strategy.indicators import add_indicators

    market, outcome, block = blocked
    do_rebase(market, block)
    columns = ["time", "open", "high", "low", "close", "volume"]
    stored = pd.DataFrame([r[2:] for r in prices(market, "AAA")], columns=columns)
    provider = pd.DataFrame([adjusted("AAA", i) for i in range(0, LAST_STORED + 1)], columns=columns)
    pd.testing.assert_frame_equal(add_indicators(stored), add_indicators(provider), check_dtype=False)
    # what V1 refuses: the same data with an old-basis prefix diverges in recursive state at the END
    seam = provider.copy()
    seam.loc[:4, ["open", "high", "low", "close"]] = [list(bar("AAA", i)[1:5]) for i in range(5)]
    assert add_indicators(seam)["EMA50"].iloc[-1] != add_indicators(provider)["EMA50"].iloc[-1]


# ------------------------------------------------------------ P1-4 older blocks
def _older_block(market: Path, rows: list[tuple], start: int, end: int) -> int:
    result = admit(market, "AAA", rows, SESS[start], SESS[end])
    assert result.blocked, result
    blocks = open_observation_log(market, readonly=True).unresolved_blocks(["AAA"])
    return [b["block_id"] for b in blocks if b["observation_id"] == result.observation_id][0]


def _resolution_kinds(market: Path) -> dict[int, str]:
    return {r["block_event_id"]: r["resolution_kind"]
            for r in open_observation_log(market, readonly=True).list_resolutions()}


def test_p1_4_fully_matching_older_proposal_auto_closes(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    older = _older_block(market, [adjusted("AAA", i) for i in range(20, LAST_STORED + 1)], 20, LAST_STORED)
    _, block = corporate_action_block(market)
    do_rebase(market, block)
    assert _resolution_kinds(market)[older] == "SUPERSEDED_BY_REVIEWED_REBASE"


def test_p1_4_originally_unchanged_session_that_now_conflicts_keeps_the_block(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    # older proposal: 20..22 adjusted (recorded), 23..29 equal to the then-stored values (not recorded)
    rows = [adjusted("AAA", i, ex_index=23) for i in range(20, LAST_STORED + 1)]
    older = _older_block(market, rows, 20, LAST_STORED)
    assert open_observation_log(market, readonly=True).unresolved_blocks(["AAA"])[0]["affected_sessions"] == list(
        SESS[20:23])
    _, block = corporate_action_block(market)  # the reviewed basis also adjusts 23..26
    result = do_rebase(market, block)
    assert older not in _resolution_kinds(market)
    assert [b["block_id"] for b in open_observation_log(market, readonly=True).unresolved_blocks()] == [older]
    assert result.rebase["detail"]["superseded_block_ids"] == []


def test_p1_4_unrelated_and_partially_conflicting_blocks_stay_unresolved(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    rows = [adjusted("AAA", i) for i in range(20, LAST_STORED + 1)]
    for i in (3, 4):  # sessions 23, 24 proposed with a different factor
        day, o, h, l, c, v = bar("AAA", 20 + i)
        rows[i] = (day, *(round(x * 0.5, 4) for x in (o, h, l, c)), v)
    partial = _older_block(market, rows, 20, LAST_STORED)
    _, bbb = corporate_action_block(market, "BBB")
    _, block = corporate_action_block(market)
    do_rebase(market, block)
    kinds = _resolution_kinds(market)
    assert partial not in kinds and bbb["block_id"] not in kinds


def test_p1_4_matching_proposal_with_future_sessions_closes_without_attributing_them(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    older_rows = [adjusted("AAA", i) for i in range(22, TODAY + 1)]  # includes the future session TODAY
    older = _older_block(market, older_rows, 22, TODAY)
    _, block = corporate_action_block(market)
    do_rebase(market, block)
    log = open_observation_log(market, readonly=True)
    assert _resolution_kinds(market)[older] == "SUPERSEDED_BY_REVIEWED_REBASE"
    assert log.session_provenance("AAA", SESS[TODAY])["origin"] == "ABSENT"
    assert SESS[TODAY] not in {r[2][:10] for r in prices(market, "AAA")}
    older_obs = [b for b in log.list_resolutions("AAA") if b["block_event_id"] == older][0]["resolved_by_observation_id"]
    assert older_obs == block["observation_id"]  # resolved by the reviewed rebase, not applied


# ------------------------------------------------------------ P1-5 ownership
def _crash_after_commit(market: Path, block: dict, monkeypatch) -> None:
    def crash(self, **kwargs):
        raise SimulatedCrash()

    monkeypatch.setattr(ObservationLog, "record_rebase", crash)
    with pytest.raises(SimulatedCrash):
        do_rebase(market, block)
    monkeypatch.undo()


def test_p1_5_ordinary_review_cannot_strand_a_committed_rebase_and_exact_retry_finalizes_once(
    blocked, monkeypatch, capsys
) -> None:
    from scripts import resolve_market_block as cli

    market, outcome, block = blocked
    versions_before = version_count(market, "AAA")
    _crash_after_commit(market, block, monkeypatch)  # 1-3: intent, market commit, crash
    log = open_observation_log(market)

    with pytest.raises(BlockOwnedByPendingRebase, match="BLOCK_OWNED_BY_PENDING_REBASE"):  # 4
        log.resolve_block(block["block_id"], reviewer="owner", reason="just unstick it")
    assert cli.main(["--db", str(market), "resolve", "--block-id", str(block["block_id"]),
                     "--reviewer", "owner", "--reason", "x"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "BLOCK_OWNED_BY_PENDING_REBASE"
    assert log.list_resolutions("AAA") == []

    result = do_rebase(market, block)  # 5: exact retry recovers
    assert result.recovered and result.applied

    log = open_observation_log(market, readonly=True)
    assert version_count(market, "AAA") == versions_before + 1  # exactly one new version
    (resolution,) = log.list_resolutions("AAA")
    assert resolution["resolution_kind"] == "REBASED_BY_REVIEW" and resolution["block_event_id"] == block["block_id"]
    assert log.open_intents() == []
    assert log.dataset_version_identity()["consumable"] is True
    assert check_market_provenance(market).state == "PASS"
    assert do_rebase(market, block).already_applied  # and never twice
    assert version_count(market, "AAA") == versions_before + 1


def test_p1_5_abandoned_pre_commit_rebase_releases_the_block_for_ordinary_review(blocked, monkeypatch) -> None:
    market, outcome, block = blocked
    before = file_sha(market)
    monkeypatch.setattr(market_rebase, "_update_rows", lambda *a: (_ for _ in ()).throw(SimulatedCrash()))
    with pytest.raises(SimulatedCrash):
        do_rebase(market, block)
    monkeypatch.undo()
    log = open_observation_log(market)
    with pytest.raises(BlockOwnedByPendingRebase):  # still owned until settled
        log.resolve_block(block["block_id"], reviewer="owner", reason="x")

    settled = recover_rebase(market, symbol="AAA")  # explicit abandonment path

    assert [item["status"] for item in settled] == ["ABANDONED"] and file_sha(market) == before
    assert log.resolve_block(block["block_id"], reviewer="owner", reason="reviewed: keep")["kind"] == "REVIEWED"
    with pytest.raises(RebaseRefused, match="BLOCK_NOT_UNRESOLVED"):
        do_rebase(market, block)


def test_p1_5_completed_rebase_resolution_cannot_be_overwritten_and_unrelated_blocks_stay_reviewable(
    tmp_path: Path, monkeypatch
) -> None:
    market = make_market(tmp_path)
    _, bbb = corporate_action_block(market, "BBB")
    _, block = corporate_action_block(market)
    _crash_after_commit(market, block, monkeypatch)
    log = open_observation_log(market)
    # 5. an unrelated block is reviewable while AAA's rebase is pending
    assert log.resolve_block(bbb["block_id"], reviewer="owner", reason="keep stored")["kind"] == "REVIEWED"
    do_rebase(market, block)
    # 4. after completion the historical resolution is immutable
    with pytest.raises(ObservationLogError, match="already resolved"):
        log.resolve_block(block["block_id"], reviewer="owner", reason="overwrite")
    kinds = _resolution_kinds(market)
    assert kinds[block["block_id"]] == "REBASED_BY_REVIEW" and kinds[bbb["block_id"]] == "REVIEWED"


def test_p1_5_a_review_racing_the_plan_is_caught_by_the_atomic_intent(blocked, monkeypatch) -> None:
    market, outcome, block = blocked
    before = file_sha(market)
    real = market_rebase.plan_rebase

    def plan_then_review(*args, **kwargs):
        plan = real(*args, **kwargs)
        open_observation_log(market).resolve_block(block["block_id"], reviewer="other", reason="raced")
        return plan

    monkeypatch.setattr(market_rebase, "plan_rebase", plan_then_review)
    with pytest.raises(RebaseRefused, match="BLOCK_NOT_UNRESOLVED"):
        do_rebase(market, block)
    log = open_observation_log(market, readonly=True)
    assert file_sha(market) == before and log.open_intents() == [] and log.symbol_rebases() == []
