"""Append-only market-data observation log (R2), schema v2.

A small provenance store in its own SQLite file next to ``market.db``
(``<market stem>_observations.db``). ``market.db`` and its schema are never
modified by this module; it opens the market database read-only to register
and verify a baseline.

Tables
------
``market_baselines`` / ``baseline_symbols``
    The legacy ``market.db`` registered *as found* (file hash, logical content
    hash, per-symbol content hashes, labels) and **bound** to one dataset: the
    canonical market path is part of the record and is verified on every open.
``source_observations``
    Immutable semantic observations: one row per distinct candidate batch. The
    identity covers source, endpoint, mode, package name/version, normalization
    version, symbol, request window, cutoff and the canonical batch hash. The
    actual normalized rows are retained. Re-use of an identity validates every
    immutable field and never overwrites first-fetch metadata.
``fetch_receipts``
    One append-only receipt per fetch occurrence (fetch time, run, mode), so a
    deduplicated observation still has a complete occurrence history.
``admission_events``
    Append-only decision/application events. ``APPLIED`` is absorbing: once an
    observation has an application result nothing can regress it.
``block_resolutions``
    A blocked decision is *unresolved* until a resolution row references it
    (superseded by a later accepted observation that covers the affected
    sessions, or an explicit reviewed resolution). Unresolved blocks gate
    consumers (see ``core.market_provenance_gate``).
``symbol_versions`` / ``applied_sessions``
    Consumed-version binding. Only an *application intent* (written durably
    under the market write lock, before the market change) can own sessions;
    ownership is never inferred from value equality.

Append-only protection: every table has BEFORE UPDATE / DELETE triggers and a
BEFORE INSERT trigger that aborts when an insert would collide with an existing
key, which also blocks ``INSERT OR REPLACE`` (SQLite's REPLACE deletes the
conflicting row without firing delete triggers unless recursive triggers are
on). Connections opened here also enable ``recursive_triggers``. This protects
against ordinary SQL on the file; a privileged writer that drops the triggers
or replaces the file is not prevented. The Python API exposes no mutation of
history.

The log and ``market.db`` are two files and not one atomic transaction.
Consistency comes from ordering (observation durable first, intent durable
before the market change) and from idempotent identities; see
``core.market_admission``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Mapping, Sequence

from core.paths import resolve_market_database_path

LOG_SCHEMA_VERSION = "market-observation-log.v2"
NORMALIZATION_VERSION = "ohlcv-norm.v1"
BASELINE_CONTENT_HASH_VERSION = "prices-content.v1"

OBSERVATION_LOG_ENV = "MARKET_OBSERVATION_LOG_PATH"

PRICE_DECIMALS = 4

# Reviewed vocabulary (see quantctl.historical_qualification).
PRICE_BASIS_UNKNOWN = "PRICE_ADJUSTMENT_UNKNOWN"
BASELINE_KIND_INITIAL_LEGACY = "INITIAL_LEGACY"
BASELINE_PROVENANCE_LABEL = "LEGACY_UNVERIFIED"
BASELINE_POINT_IN_TIME_LABEL = "NOT_POINT_IN_TIME"
BASELINE_UNIVERSE_LABEL = "SURVIVORSHIP_CURRENT_CONSTITUENTS"

# Raw per-event application states.
STATE_OBSERVED = "observed"
STATE_ADMITTED = "admitted"
STATE_BLOCKED = "blocked"
STATE_APPLIED = "applied"
STATE_FAILED = "failed"
STATE_SUPERSEDED = "superseded"
APPLICATION_STATES = (
    STATE_OBSERVED,
    STATE_ADMITTED,
    STATE_BLOCKED,
    STATE_APPLIED,
    STATE_FAILED,
    STATE_SUPERSEDED,
)
PENDING_STATES = (STATE_OBSERVED, STATE_ADMITTED)

# Effective observation statuses (derived, see ObservationLog.observation_status).
STATUS_PENDING_OBSERVED = "PENDING_OBSERVED"
STATUS_PENDING_APPLICATION = "PENDING_APPLICATION"
STATUS_APPLIED = "APPLIED"
STATUS_BLOCKED_UNRESOLVED = "BLOCKED_UNRESOLVED"
STATUS_BLOCKED_RESOLVED = "BLOCKED_RESOLVED"
STATUS_SUPERSEDED = "SUPERSEDED"
STATUS_FAILED = "FAILED"

EVENT_OBSERVED = "OBSERVED"
EVENT_ADMISSION_DECISION = "ADMISSION_DECISION"
EVENT_REVISION_DETECTED = "REVISION_DETECTED"
EVENT_APPLICATION_INTENT = "APPLICATION_INTENT"
EVENT_INTENT_ABANDONED = "INTENT_ABANDONED"
EVENT_APPLICATION_RESULT = "APPLICATION_RESULT"
EVENT_RETRY_RECEIPT = "RETRY_RECEIPT"
EVENT_SUPERSEDED = "SUPERSEDED"
EVENT_VALIDATION_REJECTED = "VALIDATION_REJECTED"

# Events that move an observation between states. Everything else (retry
# receipts, revision annotations, rejections) never changes the status.
STATE_EVENT_TYPES = (
    EVENT_OBSERVED,
    EVENT_ADMISSION_DECISION,
    EVENT_APPLICATION_INTENT,
    EVENT_INTENT_ABANDONED,
    EVENT_APPLICATION_RESULT,
    EVENT_SUPERSEDED,
)
_STATE_EVENT_SQL = ",".join(f"'{name}'" for name in STATE_EVENT_TYPES)

RESOLUTION_SUPERSEDED_BY_ACCEPTED = "SUPERSEDED_BY_ACCEPTED_OBSERVATION"
RESOLUTION_REEVALUATED_ACCEPTED = "REEVALUATED_ACCEPTED"
RESOLUTION_REVIEWED = "REVIEWED"

RUN_ID_ENV = "QUANT_OPERATION_RUN_ID"  # == quantctl.run_history.RUN_ID_ENV

IMMUTABLE_OBSERVATION_FIELDS = (
    "batch_hash",
    "source",
    "endpoint",
    "source_mode",
    "package_name",
    "package_version",
    "normalization_version",
    "symbol",
    "interval",
    "request_start",
    "request_end",
    "completed_session_cutoff",
    "price_unit",
    "volume_unit",
    "price_basis",
    "row_count",
    "first_session",
    "last_session",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS log_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_baselines (
    baseline_id TEXT PRIMARY KEY,
    baseline_kind TEXT NOT NULL,
    market_locator TEXT NOT NULL,
    market_db_path TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    content_hash_version TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    symbol_count INTEGER NOT NULL,
    first_session TEXT,
    last_session TEXT,
    database_file_mtime_utc TEXT,
    registered_at_utc TEXT NOT NULL,
    provenance_label TEXT NOT NULL,
    point_in_time_label TEXT NOT NULL,
    universe_label TEXT NOT NULL,
    price_basis_label TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_market_baselines_initial
    ON market_baselines(baseline_kind) WHERE baseline_kind = 'INITIAL_LEGACY';

CREATE TABLE IF NOT EXISTS baseline_symbols (
    baseline_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    first_session TEXT,
    last_session TEXT,
    content_sha256 TEXT NOT NULL,
    PRIMARY KEY (baseline_id, symbol)
);

CREATE TABLE IF NOT EXISTS source_observations (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id TEXT NOT NULL UNIQUE,
    batch_hash TEXT NOT NULL,
    first_run_id TEXT,
    source TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    source_mode TEXT NOT NULL,
    package_name TEXT NOT NULL,
    package_version TEXT NOT NULL,
    normalization_version TEXT NOT NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    request_start TEXT NOT NULL,
    request_end TEXT NOT NULL,
    completed_session_cutoff TEXT NOT NULL,
    first_fetched_at_utc TEXT NOT NULL,
    price_unit TEXT NOT NULL,
    volume_unit TEXT NOT NULL,
    price_basis TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    first_session TEXT,
    last_session TEXT,
    rows_json TEXT NOT NULL,
    excluded_json TEXT NOT NULL,
    supersedes_observation_id TEXT,
    recorded_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_source_observations_symbol
    ON source_observations(symbol, seq);

CREATE TABLE IF NOT EXISTS fetch_receipts (
    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id TEXT NOT NULL,
    run_id TEXT,
    fetched_at_utc TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    source_mode TEXT NOT NULL,
    request_start TEXT NOT NULL,
    request_end TEXT NOT NULL,
    completed_session_cutoff TEXT NOT NULL,
    package_version TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_fetch_receipts_observation
    ON fetch_receipts(observation_id, receipt_id);

CREATE TABLE IF NOT EXISTS admission_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id TEXT,
    receipt_id INTEGER,
    symbol TEXT NOT NULL,
    event_type TEXT NOT NULL,
    admission_result TEXT,
    application_state TEXT NOT NULL CHECK (application_state IN
        ('observed','admitted','blocked','applied','failed','superseded')),
    run_id TEXT,
    at_utc TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_admission_events_observation
    ON admission_events(observation_id, event_id);
CREATE INDEX IF NOT EXISTS ix_admission_events_symbol
    ON admission_events(symbol, event_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_admission_events_one_application
    ON admission_events(observation_id)
    WHERE event_type = 'APPLICATION_RESULT' AND application_state = 'applied';

CREATE TABLE IF NOT EXISTS block_resolutions (
    resolution_id INTEGER PRIMARY KEY AUTOINCREMENT,
    block_event_id INTEGER NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    resolution_kind TEXT NOT NULL,
    resolved_by_observation_id TEXT,
    reviewer TEXT,
    reason TEXT NOT NULL,
    resolved_at_utc TEXT NOT NULL,
    detail_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS symbol_versions (
    version_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    seq INTEGER NOT NULL,
    parent_version_id TEXT NOT NULL,
    baseline_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    last_session TEXT,
    content_sha256 TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    UNIQUE (symbol, seq)
);

CREATE TABLE IF NOT EXISTS applied_sessions (
    symbol TEXT NOT NULL,
    session TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    applied_at_utc TEXT NOT NULL,
    PRIMARY KEY (symbol, session)
);
"""

# table -> condition (over the table's columns and NEW.*) that means "this
# insert collides with an existing row", used by the no-replace triggers.
_APPEND_ONLY_TABLES = {
    "log_meta": "key = NEW.key",
    "market_baselines": "baseline_id = NEW.baseline_id OR baseline_kind = NEW.baseline_kind",
    "baseline_symbols": "baseline_id = NEW.baseline_id AND symbol = NEW.symbol",
    "source_observations": "observation_id = NEW.observation_id OR seq = NEW.seq",
    "fetch_receipts": "receipt_id = NEW.receipt_id",
    "admission_events": (
        "event_id = NEW.event_id OR (NEW.event_type = 'APPLICATION_RESULT' "
        "AND NEW.application_state = 'applied' "
        "AND observation_id = NEW.observation_id "
        "AND event_type = 'APPLICATION_RESULT' AND application_state = 'applied')"
    ),
    "block_resolutions": "resolution_id = NEW.resolution_id OR block_event_id = NEW.block_event_id",
    "symbol_versions": "version_id = NEW.version_id OR (symbol = NEW.symbol AND seq = NEW.seq)",
    "applied_sessions": "symbol = NEW.symbol AND session = NEW.session",
}
_MESSAGE = "market observation log is append-only"


# ------------------------------------------------------------------- helpers
class ObservationLogError(RuntimeError):
    """The observation log refused an operation or detected an inconsistency."""


class StoredHistoryError(RuntimeError):
    """The stored history for a symbol cannot be canonically compared."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_price(value: object) -> str:
    number = float(value)
    return f"{round(number, PRICE_DECIMALS):.{PRICE_DECIMALS}f}"


def canonical_volume(value: object) -> str:
    number = float(value)
    integral = int(round(number))
    if abs(number - integral) > 1e-9:
        raise ValueError(f"volume is not integral: {value!r}")
    return str(integral)


def canonical_row(open_, high, low, close, volume) -> tuple[str, str, str, str, str]:
    return (
        canonical_price(open_),
        canonical_price(high),
        canonical_price(low),
        canonical_price(close),
        canonical_volume(volume),
    )


def rows_content_sha256(symbol: str, rows: Mapping[str, Sequence[str]]) -> str:
    digest = hashlib.sha256()
    for session in sorted(rows):
        digest.update(f"{symbol}|{session}|{'|'.join(rows[session])}\n".encode("ascii"))
    return digest.hexdigest()


def current_run_id() -> str | None:
    value = os.environ.get(RUN_ID_ENV, "").strip()
    return value or None


def market_locator(path: str | Path) -> str:
    """Canonical dataset locator: resolved, case-normalized absolute path."""
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def _same_file(left: Path, right: Path) -> bool:
    if market_locator(left) == market_locator(right):
        return True
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:  # pragma: no cover - platform specific
        return False


def default_log_path(market_path: Path) -> Path:
    # Derived from the *file name* so two market databases in one directory
    # can never share a log by default.
    return market_path.parent / f"{market_path.stem}_observations.db"


def resolve_observation_log_path(
    market_db_path: str | Path | None = None,
    log_path: str | Path | None = None,
) -> Path:
    if log_path is not None:
        return Path(log_path).expanduser().resolve()
    configured = os.environ.get(OBSERVATION_LOG_ENV, "").strip()
    if configured:
        return resolve_market_database_path(configured)
    return default_log_path(resolve_market_database_path(market_db_path))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def open_market_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=30)


def read_symbol_rows(connection: sqlite3.Connection, symbol: str) -> dict[str, tuple[str, ...]]:
    rows: dict[str, tuple[str, ...]] = {}
    cursor = connection.execute(
        "SELECT time, open, high, low, close, volume FROM prices WHERE symbol=? ORDER BY time, id",
        (symbol,),
    )
    for session, o, h, l, c, v in cursor:
        key = str(session)[:10]
        if key in rows:
            raise StoredHistoryError(f"{symbol}: duplicate stored session {key}")
        try:
            rows[key] = canonical_row(o, h, l, c, v)
        except (TypeError, ValueError) as error:
            raise StoredHistoryError(f"{symbol}@{key}: stored row not comparable: {error}") from error
    return rows


def _trigger_sql() -> list[str]:
    statements = []
    for table, condition in _APPEND_ONLY_TABLES.items():
        statements.append(
            f"CREATE TRIGGER IF NOT EXISTS trg_{table}_no_update BEFORE UPDATE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{_MESSAGE}'); END"
        )
        statements.append(
            f"CREATE TRIGGER IF NOT EXISTS trg_{table}_no_delete BEFORE DELETE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{_MESSAGE}'); END"
        )
        statements.append(
            f"CREATE TRIGGER IF NOT EXISTS trg_{table}_no_replace BEFORE INSERT ON {table} "
            f"WHEN EXISTS (SELECT 1 FROM {table} WHERE {condition}) "
            f"BEGIN SELECT RAISE(ABORT, '{_MESSAGE} (insert would replace an existing row)'); END"
        )
    return statements


# ----------------------------------------------------------------------- log
class ObservationLog:
    """Explicit API over the append-only SQLite store."""

    def __init__(
        self,
        path: str | Path,
        *,
        market_path: str | Path | None = None,
        readonly: bool = False,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        self.readonly = readonly
        self.market_path = None if market_path is None else resolve_market_database_path(market_path)
        if self.market_path is not None and _same_file(self.path, self.market_path):
            raise ObservationLogError(
                f"observation log path must differ from the market database: {self.path}"
            )
        if readonly:
            if not self.path.is_file():
                raise ObservationLogError(f"observation log does not exist: {self.path}")
            self._check_schema()
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()

    # ------------------------------------------------------------------ infra
    def _connect(self) -> sqlite3.Connection:
        if self.readonly:
            connection = sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True, timeout=30)
        else:
            connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA recursive_triggers=ON")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        if self.readonly:
            raise ObservationLogError("observation log was opened read-only")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")
        finally:
            connection.close()

    def _read(self, sql: str, params: Sequence[object] = ()) -> list[sqlite3.Row]:
        connection = self._connect()
        try:
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()

    def _check_schema(self) -> None:
        try:
            rows = self._read("SELECT value FROM log_meta WHERE key='schema_version'")
        except sqlite3.DatabaseError as error:
            raise ObservationLogError(f"not a market observation log: {self.path}") from error
        if not rows or rows[0]["value"] != LOG_SCHEMA_VERSION:
            raise ObservationLogError(
                f"unsupported observation-log schema: {None if not rows else rows[0]['value']}"
            )

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(_SCHEMA)
            for statement in _trigger_sql():
                connection.execute(statement)
            connection.execute(
                "INSERT INTO log_meta(key, value) SELECT 'schema_version', ? "
                "WHERE NOT EXISTS (SELECT 1 FROM log_meta WHERE key='schema_version')",
                (LOG_SCHEMA_VERSION,),
            )
        finally:
            connection.close()
        self._check_schema()

    # --------------------------------------------------------------- baseline
    def get_baseline(self) -> dict[str, object] | None:
        rows = self._read(
            "SELECT * FROM market_baselines WHERE baseline_kind=?", (BASELINE_KIND_INITIAL_LEGACY,)
        )
        if not rows:
            return None
        record = dict(rows[0])
        record["detail"] = json.loads(record.pop("detail_json"))
        return record

    def baseline_symbol(self, symbol: str) -> dict[str, object] | None:
        baseline = self.get_baseline()
        if baseline is None:
            return None
        rows = self._read(
            "SELECT * FROM baseline_symbols WHERE baseline_id=? AND symbol=?",
            (baseline["baseline_id"], symbol),
        )
        return None if not rows else dict(rows[0])

    def assert_bound_to(self, market_db_path: str | Path) -> dict[str, object]:
        """The log must be bound to exactly this market dataset."""
        path = resolve_market_database_path(market_db_path)
        if _same_file(self.path, path):
            raise ObservationLogError("observation log path must differ from the market database")
        baseline = self.get_baseline()
        if baseline is None:
            raise ObservationLogError("observation log has no registered baseline")
        if baseline["market_locator"] != market_locator(path):
            raise ObservationLogError(
                "observation log is bound to a different market dataset "
                f"({baseline['market_db_path']}), not {path}"
            )
        return baseline

    def ensure_initial_baseline(
        self,
        market_db_path: str | Path,
        *,
        now: datetime | None = None,
    ) -> dict[str, object]:
        """Register ``market.db`` as the initial LEGACY baseline (idempotent).

        Read-only on ``market.db``. If a baseline already exists the requested
        database must be the one it is bound to; a different database is an
        error, never a silent reuse.
        """
        path = resolve_market_database_path(market_db_path)
        if not path.is_file():
            raise ObservationLogError(f"market database not found for baseline: {path}")
        if _same_file(self.path, path):
            raise ObservationLogError("observation log path must differ from the market database")
        existing = self.get_baseline()
        if existing is not None:
            self.assert_bound_to(path)
            return existing

        file_hash = _file_sha256(path)
        connection = open_market_readonly(path)
        symbol_rows: list[tuple[str, int, str | None, str | None, str]] = []
        try:
            digest = hashlib.sha256()
            symbol_digest = hashlib.sha256()
            current_symbol: str | None = None
            current_count = 0
            current_first: str | None = None
            current_last: str | None = None
            row_count = 0
            first_session: str | None = None
            last_session: str | None = None

            def flush() -> None:
                if current_symbol is not None:
                    symbol_rows.append(
                        (current_symbol, current_count, current_first, current_last, symbol_digest.hexdigest())
                    )

            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='prices'"
            ).fetchone()
            if exists:
                cursor = connection.execute(
                    "SELECT symbol, time, open, high, low, close, volume "
                    "FROM prices ORDER BY symbol, time, id"
                )
                for symbol, session, o, h, l, c, v in cursor:
                    symbol = str(symbol)
                    session = str(session)[:10]
                    line = f"{symbol}|{session}|" + "|".join(canonical_row(o, h, l, c, v)) + "\n"
                    if symbol != current_symbol:
                        flush()
                        current_symbol, current_count = symbol, 0
                        current_first = current_last = None
                        symbol_digest = hashlib.sha256()
                    encoded = line.encode("ascii")
                    digest.update(encoded)
                    symbol_digest.update(encoded)
                    current_count += 1
                    current_first = session if current_first is None else min(current_first, session)
                    current_last = session if current_last is None else max(current_last, session)
                    row_count += 1
                    first_session = session if first_session is None else min(first_session, session)
                    last_session = session if last_session is None else max(last_session, session)
                flush()
            content_hash = digest.hexdigest()
        finally:
            connection.close()

        stat = path.stat()
        registered = now or utc_now()
        baseline_id = f"baseline-legacy-{content_hash[:16]}"
        detail = {
            "note": (
                "Registered as found. History is not historically verified, not "
                "point-in-time and may mix provider adjustment bases."
            ),
            "content_hash_version": BASELINE_CONTENT_HASH_VERSION,
            "price_decimals": PRICE_DECIMALS,
        }
        with self._transaction() as connection_:
            already = connection_.execute(
                "SELECT market_locator FROM market_baselines WHERE baseline_kind=?",
                (BASELINE_KIND_INITIAL_LEGACY,),
            ).fetchone()
            if already is None:
                connection_.execute(
                    "INSERT INTO market_baselines("
                    "baseline_id, baseline_kind, market_locator, market_db_path, file_sha256,"
                    "content_sha256, content_hash_version, row_count, symbol_count, first_session,"
                    "last_session, database_file_mtime_utc, registered_at_utc, provenance_label,"
                    "point_in_time_label, universe_label, price_basis_label, detail_json)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        baseline_id,
                        BASELINE_KIND_INITIAL_LEGACY,
                        market_locator(path),
                        str(path),
                        file_hash,
                        content_hash,
                        BASELINE_CONTENT_HASH_VERSION,
                        row_count,
                        len(symbol_rows),
                        first_session,
                        last_session,
                        iso_utc(datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)),
                        iso_utc(registered),
                        BASELINE_PROVENANCE_LABEL,
                        BASELINE_POINT_IN_TIME_LABEL,
                        BASELINE_UNIVERSE_LABEL,
                        PRICE_BASIS_UNKNOWN,
                        canonical_json(detail),
                    ),
                )
                connection_.executemany(
                    "INSERT INTO baseline_symbols(baseline_id, symbol, row_count, first_session,"
                    "last_session, content_sha256) VALUES (?,?,?,?,?,?)",
                    [(baseline_id, *item) for item in symbol_rows],
                )
        baseline = self.get_baseline()
        if baseline is None:  # pragma: no cover - defensive
            raise ObservationLogError("baseline registration did not persist")
        self.assert_bound_to(path)
        return baseline

    # ----------------------------------------------------------- verification
    def verify_symbol(self, symbol: str, stored: Mapping[str, Sequence[str]]) -> list[str]:
        """Check one symbol's stored history against baseline and version lineage."""
        baseline = self.get_baseline()
        if baseline is None:
            return ["NO_BASELINE"]
        issues: list[str] = []
        base = self.baseline_symbol(symbol)
        latest = self.latest_symbol_version(symbol)
        if base is not None:
            legacy = {s: v for s, v in stored.items() if s <= base["last_session"]}
            if (
                len(legacy) != base["row_count"]
                or rows_content_sha256(symbol, legacy) != base["content_sha256"]
            ):
                issues.append("BASELINE_SLICE_MISMATCH")
        if latest is not None:
            if rows_content_sha256(symbol, stored) != latest["content_sha256"]:
                issues.append("VERSION_CONTENT_MISMATCH")
        else:
            beyond = [s for s in stored if base is None or s > base["last_session"]]
            if beyond:
                issues.append("UNATTRIBUTED_ROWS")
        return issues

    def verify_dataset(
        self,
        market_db_path: str | Path | None = None,
        symbols: Sequence[str] | None = None,
    ) -> dict[str, list[str]]:
        """Verify binding plus every (selected) symbol; returns ``{symbol: issues}``."""
        path = resolve_market_database_path(market_db_path or self.market_path)
        self.assert_bound_to(path)
        baseline = self.get_baseline()
        assert baseline is not None
        connection = open_market_readonly(path)
        problems: dict[str, list[str]] = {}
        try:
            if symbols is None:
                market_symbols = {
                    str(row[0]) for row in connection.execute("SELECT DISTINCT symbol FROM prices")
                }
                known = {
                    str(row["symbol"])
                    for row in self._read(
                        "SELECT symbol FROM baseline_symbols WHERE baseline_id=? "
                        "UNION SELECT symbol FROM symbol_versions",
                        (baseline["baseline_id"],),
                    )
                }
                targets = sorted(market_symbols | known)
            else:
                targets = sorted({str(item).strip().upper() for item in symbols})
            for symbol in targets:
                try:
                    stored = read_symbol_rows(connection, symbol)
                except StoredHistoryError as error:
                    problems[symbol] = [f"STORED_HISTORY_INVALID: {error}"]
                    continue
                issues = self.verify_symbol(symbol, stored)
                if issues:
                    problems[symbol] = issues
        finally:
            connection.close()
        return problems

    # ----------------------------------------------------------- observations
    def get_observation(self, observation_id: str) -> dict[str, object] | None:
        rows = self._read("SELECT * FROM source_observations WHERE observation_id=?", (observation_id,))
        if not rows:
            return None
        record = dict(rows[0])
        record["rows"] = json.loads(record.pop("rows_json"))
        record["excluded"] = json.loads(record.pop("excluded_json"))
        return record

    def list_observations(self, symbol: str | None = None) -> list[dict[str, object]]:
        if symbol is None:
            rows = self._read("SELECT observation_id FROM source_observations ORDER BY seq")
        else:
            rows = self._read(
                "SELECT observation_id FROM source_observations WHERE symbol=? ORDER BY seq", (symbol,)
            )
        return [self.get_observation(str(row["observation_id"])) for row in rows]  # type: ignore[misc]

    def list_receipts(self, observation_id: str) -> list[dict[str, object]]:
        rows = self._read(
            "SELECT * FROM fetch_receipts WHERE observation_id=? ORDER BY receipt_id", (observation_id,)
        )
        result = []
        for row in rows:
            record = dict(row)
            record["detail"] = json.loads(record.pop("detail_json"))
            events = self._read(
                "SELECT event_type, admission_result, application_state FROM admission_events "
                "WHERE receipt_id=? ORDER BY event_id",
                (record["receipt_id"],),
            )
            record["events"] = [dict(event) for event in events]
            result.append(record)
        return result

    def persist_observation(self, observation: Mapping[str, object]) -> dict[str, object]:
        """Persist an immutable observation and a fetch receipt.

        Idempotent on ``observation_id``: a repeat never inserts a duplicate
        observation and never overwrites its first-fetch metadata, but always
        adds its own receipt. Every immutable field of a reused identity is
        validated; a mismatch is an integrity error.
        """
        rows_json = canonical_json(observation["rows"])
        excluded_json = canonical_json(observation.get("excluded", []))
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM source_observations WHERE observation_id=?",
                (observation["observation_id"],),
            ).fetchone()
            recorded = iso_utc(utc_now())
            supersedes: str | None
            inserted = existing is None
            if existing is not None:
                mismatched = [
                    name
                    for name in IMMUTABLE_OBSERVATION_FIELDS
                    if existing[name] != observation.get(name)
                ]
                if existing["rows_json"] != rows_json:
                    mismatched.append("rows")
                if existing["excluded_json"] != excluded_json:
                    mismatched.append("excluded")
                if mismatched:
                    raise ObservationLogError(
                        "immutable observation metadata mismatch for "
                        f"{observation['observation_id']}: {', '.join(mismatched)}"
                    )
                supersedes = existing["supersedes_observation_id"]
            else:
                previous = connection.execute(
                    "SELECT observation_id FROM source_observations "
                    "WHERE source=? AND symbol=? AND interval=? AND request_start=? "
                    "AND request_end=? AND batch_hash<>? ORDER BY seq DESC LIMIT 1",
                    (
                        observation["source"],
                        observation["symbol"],
                        observation["interval"],
                        observation["request_start"],
                        observation["request_end"],
                        observation["batch_hash"],
                    ),
                ).fetchone()
                supersedes = None if previous is None else str(previous["observation_id"])
                connection.execute(
                    "INSERT INTO source_observations("
                    "observation_id,batch_hash,first_run_id,source,endpoint,source_mode,package_name,"
                    "package_version,normalization_version,symbol,interval,request_start,"
                    "request_end,completed_session_cutoff,first_fetched_at_utc,price_unit,volume_unit,"
                    "price_basis,row_count,first_session,last_session,rows_json,excluded_json,"
                    "supersedes_observation_id,recorded_at_utc)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        observation["observation_id"],
                        observation["batch_hash"],
                        observation.get("run_id"),
                        observation["source"],
                        observation["endpoint"],
                        observation["source_mode"],
                        observation["package_name"],
                        observation["package_version"],
                        observation["normalization_version"],
                        observation["symbol"],
                        observation["interval"],
                        observation["request_start"],
                        observation["request_end"],
                        observation["completed_session_cutoff"],
                        observation["fetched_at_utc"],
                        observation["price_unit"],
                        observation["volume_unit"],
                        observation["price_basis"],
                        observation["row_count"],
                        observation.get("first_session"),
                        observation.get("last_session"),
                        rows_json,
                        excluded_json,
                        supersedes,
                        recorded,
                    ),
                )
            cursor = connection.execute(
                "INSERT INTO fetch_receipts(observation_id,run_id,fetched_at_utc,recorded_at_utc,"
                "source_mode,request_start,request_end,completed_session_cutoff,package_version,"
                "detail_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    observation["observation_id"],
                    observation.get("run_id"),
                    observation["fetched_at_utc"],
                    recorded,
                    observation["source_mode"],
                    observation["request_start"],
                    observation["request_end"],
                    observation["completed_session_cutoff"],
                    observation["package_version"],
                    canonical_json({"first_occurrence": inserted}),
                ),
            )
            receipt_id = int(cursor.lastrowid)
            if inserted:
                self._insert_event(
                    connection,
                    observation_id=str(observation["observation_id"]),
                    receipt_id=receipt_id,
                    symbol=str(observation["symbol"]),
                    event_type=EVENT_OBSERVED,
                    application_state=STATE_OBSERVED,
                    admission_result=None,
                    run_id=observation.get("run_id"),  # type: ignore[arg-type]
                    detail={"supersedes_observation_id": supersedes},
                )
            return {"inserted": inserted, "supersedes": supersedes, "receipt_id": receipt_id}

    # ----------------------------------------------------------------- events
    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        *,
        observation_id: str | None,
        receipt_id: int | None,
        symbol: str,
        event_type: str,
        application_state: str,
        admission_result: str | None,
        run_id: str | None,
        detail: Mapping[str, object] | None,
        at: datetime | None = None,
    ) -> int:
        if application_state not in APPLICATION_STATES:
            raise ValueError(f"unknown application state: {application_state}")
        cursor = connection.execute(
            "INSERT INTO admission_events(observation_id,receipt_id,symbol,event_type,"
            "admission_result,application_state,run_id,at_utc,detail_json)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                observation_id,
                receipt_id,
                symbol,
                event_type,
                admission_result,
                application_state,
                run_id,
                iso_utc(at or utc_now()),
                canonical_json(dict(detail or {})),
            ),
        )
        return int(cursor.lastrowid)

    def append_event(
        self,
        *,
        observation_id: str | None,
        symbol: str,
        event_type: str,
        application_state: str,
        admission_result: str | None = None,
        run_id: str | None = None,
        receipt_id: int | None = None,
        detail: Mapping[str, object] | None = None,
        at: datetime | None = None,
    ) -> int:
        with self._transaction() as connection:
            return self._insert_event(
                connection,
                observation_id=observation_id,
                receipt_id=receipt_id,
                symbol=symbol,
                event_type=event_type,
                application_state=application_state,
                admission_result=admission_result,
                run_id=run_id,
                detail=detail,
                at=at,
            )

    def list_events(
        self, observation_id: str | None = None, symbol: str | None = None
    ) -> list[dict[str, object]]:
        clauses, params = [], []
        if observation_id is not None:
            clauses.append("observation_id=?")
            params.append(observation_id)
        if symbol is not None:
            clauses.append("symbol=?")
            params.append(symbol)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._read(f"SELECT * FROM admission_events {where} ORDER BY event_id", params)
        result = []
        for row in rows:
            record = dict(row)
            record["detail"] = json.loads(record.pop("detail_json"))
            result.append(record)
        return result

    def observation_state(self, observation_id: str) -> str | None:
        """Raw effective state. ``applied`` is absorbing and cannot regress."""
        applied = self._read(
            "SELECT 1 FROM admission_events WHERE observation_id=? AND event_type=? "
            "AND application_state='applied'",
            (observation_id, EVENT_APPLICATION_RESULT),
        )
        if applied:
            return STATE_APPLIED
        rows = self._read(
            "SELECT application_state FROM admission_events WHERE observation_id=? "
            f"AND event_type IN ({_STATE_EVENT_SQL}) ORDER BY event_id DESC LIMIT 1",
            (observation_id,),
        )
        return None if not rows else str(rows[0]["application_state"])

    def observation_status(self, observation_id: str) -> str | None:
        state = self.observation_state(observation_id)
        if state is None:
            return None
        if state == STATE_APPLIED:
            return STATUS_APPLIED
        if state == STATE_OBSERVED:
            return STATUS_PENDING_OBSERVED
        if state == STATE_ADMITTED:
            return STATUS_PENDING_APPLICATION
        if state == STATE_SUPERSEDED:
            return STATUS_SUPERSEDED
        if state == STATE_FAILED:
            return STATUS_FAILED
        blocks = self._read(
            "SELECT event_id FROM admission_events WHERE observation_id=? AND event_type=? "
            "AND application_state='blocked' ORDER BY event_id DESC LIMIT 1",
            (observation_id, EVENT_ADMISSION_DECISION),
        )
        if not blocks:  # pragma: no cover - defensive
            return STATUS_BLOCKED_UNRESOLVED
        resolved = self._read(
            "SELECT 1 FROM block_resolutions WHERE block_event_id=?", (blocks[0]["event_id"],)
        )
        return STATUS_BLOCKED_RESOLVED if resolved else STATUS_BLOCKED_UNRESOLVED

    def pending_observations(
        self, symbol: str | None = None, *, include_failed: bool = False
    ) -> list[dict[str, object]]:
        """Observations whose effective state is ``observed`` or ``admitted``.

        ``include_failed`` also returns unapplied observations whose last
        attempt failed (an application error or an abandoned intent).
        """
        states = "('observed','admitted','failed')" if include_failed else "('observed','admitted')"
        params: list[object] = []
        symbol_clause = ""
        if symbol is not None:
            symbol_clause = "AND o.symbol=?"
            params.append(symbol)
        rows = self._read(
            "SELECT o.observation_id, e.application_state FROM source_observations o "
            "JOIN admission_events e ON e.event_id = ("
            "  SELECT MAX(event_id) FROM admission_events WHERE observation_id=o.observation_id "
            f"  AND event_type IN ({_STATE_EVENT_SQL})) "
            f"WHERE e.application_state IN {states} "
            "AND NOT EXISTS (SELECT 1 FROM admission_events a WHERE a.observation_id=o.observation_id "
            "  AND a.event_type='APPLICATION_RESULT' AND a.application_state='applied') "
            f"{symbol_clause} ORDER BY o.seq",
            params,
        )
        return [
            {
                "observation_id": str(row["observation_id"]),
                "application_state": str(row["application_state"]),
            }
            for row in rows
        ]

    # ---------------------------------------------------------------- intents
    def record_intent(
        self,
        *,
        observation_id: str,
        symbol: str,
        sessions: Sequence[str],
        rows_sha256: str,
        run_id: str | None,
        receipt_id: int | None = None,
    ) -> int:
        """Durably claim ``sessions`` for ``observation_id`` *before* the market change."""
        return self.append_event(
            observation_id=observation_id,
            symbol=symbol,
            event_type=EVENT_APPLICATION_INTENT,
            application_state=STATE_ADMITTED,
            admission_result="APPLICATION_INTENT",
            run_id=run_id,
            receipt_id=receipt_id,
            detail={"sessions": sorted(sessions), "rows_sha256": rows_sha256},
        )

    def open_intents(self, symbol: str | None = None) -> list[dict[str, object]]:
        params: list[object] = []
        symbol_clause = ""
        if symbol is not None:
            symbol_clause = "AND e.symbol=?"
            params.append(symbol)
        rows = self._read(
            "SELECT e.event_id, e.observation_id, e.symbol, e.detail_json FROM admission_events e "
            "WHERE e.event_type='APPLICATION_INTENT' AND e.event_id = ("
            "  SELECT MAX(event_id) FROM admission_events WHERE observation_id=e.observation_id "
            f"  AND event_type IN ({_STATE_EVENT_SQL})) {symbol_clause} ORDER BY e.event_id",
            params,
        )
        result = []
        for row in rows:
            detail = json.loads(row["detail_json"])
            result.append(
                {
                    "event_id": int(row["event_id"]),
                    "observation_id": str(row["observation_id"]),
                    "symbol": str(row["symbol"]),
                    "sessions": list(detail["sessions"]),
                    "rows_sha256": detail["rows_sha256"],
                }
            )
        return result

    # ------------------------------------------------------------------ blocks
    def unresolved_blocks(self, symbols: Sequence[str] | None = None) -> list[dict[str, object]]:
        params: list[object] = [EVENT_ADMISSION_DECISION]
        clause = ""
        if symbols is not None:
            wanted = sorted({str(item).strip().upper() for item in symbols})
            if not wanted:
                return []
            clause = f"AND e.symbol IN ({','.join('?' for _ in wanted)})"
            params.extend(wanted)
        rows = self._read(
            "SELECT e.event_id, e.observation_id, e.symbol, e.admission_result, e.run_id, "
            "e.at_utc, e.detail_json FROM admission_events e "
            "WHERE e.event_type=? AND e.application_state='blocked' "
            "AND NOT EXISTS (SELECT 1 FROM block_resolutions r WHERE r.block_event_id=e.event_id) "
            f"{clause} ORDER BY e.event_id",
            params,
        )
        result = []
        for row in rows:
            detail = json.loads(row["detail_json"])
            result.append(
                {
                    "block_id": int(row["event_id"]),
                    "observation_id": row["observation_id"],
                    "symbol": str(row["symbol"]),
                    "admission_result": row["admission_result"],
                    "run_id": row["run_id"],
                    "at_utc": row["at_utc"],
                    "affected_sessions": list(detail.get("affected_sessions", [])),
                    "auto_resolvable": bool(detail.get("auto_resolvable", True)),
                    "reason": detail.get("reason"),
                }
            )
        return result

    def blocked_symbols(self, run_id: str | None = None) -> list[str]:
        """Symbols that currently have at least one unresolved block."""
        blocks = self.unresolved_blocks()
        if run_id is not None:
            blocks = [block for block in blocks if block["run_id"] == run_id]
        return sorted({str(block["symbol"]) for block in blocks})

    def list_resolutions(self, symbol: str | None = None) -> list[dict[str, object]]:
        if symbol is None:
            rows = self._read("SELECT * FROM block_resolutions ORDER BY resolution_id")
        else:
            rows = self._read(
                "SELECT * FROM block_resolutions WHERE symbol=? ORDER BY resolution_id", (symbol,)
            )
        result = []
        for row in rows:
            record = dict(row)
            record["detail"] = json.loads(record.pop("detail_json"))
            result.append(record)
        return result

    def resolve_block(
        self,
        block_id: int,
        *,
        reviewer: str,
        reason: str,
        detail: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Explicit reviewed resolution of one unresolved block."""
        reviewer, reason = str(reviewer).strip(), str(reason).strip()
        if not reviewer or not reason:
            raise ValueError("a reviewed resolution requires a reviewer and a reason")
        with self._transaction() as connection:
            block = connection.execute(
                "SELECT event_id, symbol, observation_id FROM admission_events "
                "WHERE event_id=? AND event_type=? AND application_state='blocked'",
                (block_id, EVENT_ADMISSION_DECISION),
            ).fetchone()
            if block is None:
                raise ObservationLogError(f"no such block: {block_id}")
            if connection.execute(
                "SELECT 1 FROM block_resolutions WHERE block_event_id=?", (block_id,)
            ).fetchone():
                raise ObservationLogError(f"block {block_id} is already resolved")
            connection.execute(
                "INSERT INTO block_resolutions(block_event_id,symbol,resolution_kind,"
                "resolved_by_observation_id,reviewer,reason,resolved_at_utc,detail_json)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    block_id,
                    block["symbol"],
                    RESOLUTION_REVIEWED,
                    None,
                    reviewer,
                    reason,
                    iso_utc(utc_now()),
                    canonical_json(dict(detail or {})),
                ),
            )
        return {"block_id": block_id, "symbol": str(block["symbol"]), "kind": RESOLUTION_REVIEWED}

    # --------------------------------------------------------------- versions
    def latest_symbol_version(self, symbol: str) -> dict[str, object] | None:
        rows = self._read(
            "SELECT * FROM symbol_versions WHERE symbol=? ORDER BY seq DESC LIMIT 1", (symbol,)
        )
        return None if not rows else dict(rows[0])

    def current_version_ref(self, symbol: str) -> str:
        latest = self.latest_symbol_version(symbol)
        if latest is not None:
            return str(latest["version_id"])
        baseline = self.get_baseline()
        if baseline is None:
            raise ObservationLogError("no baseline registered")
        return f"baseline:{baseline['baseline_id']}"

    def application_result(self, observation_id: str) -> dict[str, object] | None:
        rows = self._read(
            "SELECT detail_json, admission_result FROM admission_events WHERE observation_id=? "
            "AND event_type=? AND application_state='applied'",
            (observation_id, EVENT_APPLICATION_RESULT),
        )
        if not rows:
            return None
        return {**json.loads(rows[0]["detail_json"]), "admission_result": rows[0]["admission_result"]}

    def record_application(
        self,
        *,
        observation_id: str,
        symbol: str,
        admission_result: str,
        appended_sessions: Sequence[str],
        row_count: int,
        last_session: str | None,
        content_sha256: str,
        run_id: str | None,
        receipt_id: int | None = None,
        reconciled: bool = False,
        detail: Mapping[str, object] | None = None,
        decision_event_id: int | None = None,
        bound_version_id: str | None = None,
        confirmed_sessions: Sequence[str] | None = None,
    ) -> dict[str, object]:
        """Record the applied outcome atomically inside the log.

        Decision binding (captured by the caller under the market write lock):

        * ``decision_event_id``: the ``ADMISSION_DECISION`` event this
          application finalizes (mandatory for a zero-session application; the
          caller must prove which serialized decision it finalizes). The observation must still be in exactly that
          admitted state (a blocked, superseded or otherwise re-decided
          observation cannot be finalized, with or without sessions).
        * ``bound_version_id``: the symbol's dataset version *at the decision*.
          A no-op receipt binds to it and never to "latest"; an append must
          still find it current (the new version's parent).
        * ``confirmed_sessions``: sessions whose stored values the decision
          compared and found identical. Only these can lift an earlier block,
          and only blocks recorded *before* ``decision_event_id``.

        * Monotonic/idempotent: a second call for the same observation returns
          the first result (``already_applied``) and writes nothing, so no retry
          can advance the dataset version again or reopen the observation.
        * Ownership: sessions can only be attributed through an *open
          application intent* of this same observation whose session list
          equals ``appended_sessions``. Value equality is never used.
        * Accepted observations resolve earlier blocks they cover.
        """
        sessions = sorted(appended_sessions)
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT detail_json FROM admission_events WHERE observation_id=? "
                "AND event_type=? AND application_state='applied'",
                (observation_id, EVENT_APPLICATION_RESULT),
            ).fetchone()
            if existing is not None:
                return {**json.loads(existing["detail_json"]), "already_applied": True}
            baseline = connection.execute(
                "SELECT baseline_id FROM market_baselines WHERE baseline_kind=?",
                (BASELINE_KIND_INITIAL_LEGACY,),
            ).fetchone()
            if baseline is None:
                raise ObservationLogError("no baseline registered")
            if not sessions:
                # A zero-session finalization is still a semantic state
                # transition: only an observation that is currently admitted
                # (and, when bound, by that very decision) may finalize.
                if decision_event_id is None:
                    raise ObservationLogError(
                        "a no-op application must carry the decision_event_id it finalizes; "
                        "the current decision is never discovered implicitly (empty finalization refused)"
                    )
                if bound_version_id is None:
                    raise ObservationLogError(
                        "a no-op application must carry the dataset version bound at its decision"
                    )
                latest_state = connection.execute(
                    "SELECT event_id, event_type, application_state FROM admission_events "
                    f"WHERE observation_id=? AND event_type IN ({_STATE_EVENT_SQL}) "
                    "ORDER BY event_id DESC LIMIT 1",
                    (observation_id,),
                ).fetchone()
                if (
                    latest_state is None
                    or latest_state["event_type"] != EVENT_ADMISSION_DECISION
                    or latest_state["application_state"] != STATE_ADMITTED
                    or int(latest_state["event_id"]) != int(decision_event_id)
                ):
                    current = None if latest_state is None else latest_state["application_state"]
                    raise ObservationLogError(
                        "observation is not in the admitted state for this decision "
                        f"(current state: {current}); empty finalization refused"
                    )
            if sessions:
                state = connection.execute(
                    "SELECT event_id, event_type, detail_json FROM admission_events "
                    f"WHERE observation_id=? AND event_type IN ({_STATE_EVENT_SQL}) "
                    "ORDER BY event_id DESC LIMIT 1",
                    (observation_id,),
                ).fetchone()
                if (
                    state is None
                    or state["event_type"] != EVENT_APPLICATION_INTENT
                    or sorted(json.loads(state["detail_json"])["sessions"]) != sessions
                ):
                    raise ObservationLogError(
                        "applied sessions must be owned by this observation's open application intent"
                    )
            baseline_id = str(baseline["baseline_id"])
            now = iso_utc(utc_now())
            latest = connection.execute(
                "SELECT version_id, seq FROM symbol_versions WHERE symbol=? ORDER BY seq DESC LIMIT 1",
                (symbol,),
            ).fetchone()
            current_ref = f"baseline:{baseline_id}" if latest is None else str(latest["version_id"])
            if sessions and bound_version_id is not None and bound_version_id != current_ref:
                raise ObservationLogError(
                    "dataset version advanced since the admission decision "
                    f"({bound_version_id} -> {current_ref}); application refused"
                )
            # A no-op receipt keeps the version visible at its own decision,
            # never the (possibly newer) latest one.
            parent = current_ref if sessions or bound_version_id is None else bound_version_id
            version_id = parent
            if sessions:
                seq = 1 if latest is None else int(latest["seq"]) + 1
                version_id = sha256_text(
                    canonical_json(
                        {
                            "parent": parent,
                            "observation_id": observation_id,
                            "content_sha256": content_sha256,
                            "appended": sessions,
                        }
                    )
                )
                connection.execute(
                    "INSERT INTO symbol_versions(version_id,symbol,seq,parent_version_id,"
                    "baseline_id,observation_id,row_count,last_session,content_sha256,created_at_utc)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        version_id,
                        symbol,
                        seq,
                        parent,
                        baseline_id,
                        observation_id,
                        row_count,
                        last_session,
                        content_sha256,
                        now,
                    ),
                )
                try:
                    connection.executemany(
                        "INSERT INTO applied_sessions(symbol,session,observation_id,version_id,"
                        "applied_at_utc) VALUES (?,?,?,?,?)",
                        [(symbol, session, observation_id, version_id, now) for session in sessions],
                    )
                except sqlite3.IntegrityError as error:
                    raise ObservationLogError(
                        f"session ownership conflict for {symbol}: {error}"
                    ) from error
            result = {
                "observation_id": observation_id,
                "symbol": symbol,
                "version_id": version_id,
                "parent_version_id": parent,
                "baseline_id": baseline_id,
                "appended_sessions": sessions,
                "rows_appended": len(sessions),
                "content_sha256": content_sha256,
                "reconciled": reconciled,
                "already_applied": False,
                "decision_event_id": decision_event_id,
                **dict(detail or {}),
            }
            self._insert_event(
                connection,
                observation_id=observation_id,
                receipt_id=receipt_id,
                symbol=symbol,
                event_type=EVENT_APPLICATION_RESULT,
                application_state=STATE_APPLIED,
                admission_result=admission_result,
                run_id=run_id,
                detail=result,
            )
            if decision_event_id is not None and confirmed_sessions is not None:
                self._resolve_covered_blocks(
                    connection,
                    observation_id,
                    symbol,
                    decision_event_id=int(decision_event_id),
                    confirmed_sessions=frozenset(confirmed_sessions),
                )
            return result

    def _resolve_covered_blocks(
        self,
        connection: sqlite3.Connection,
        observation_id: str,
        symbol: str,
        *,
        decision_event_id: int,
        confirmed_sessions: frozenset[str],
    ) -> None:
        """Lift only blocks this very decision actually revalidated.

        A block qualifies when it (1) was recorded before the accepting decision's
        serialization point (``event_id < decision_event_id``), (2) is on the same
        symbol, (3) is auto-resolvable with affected sessions that are all in
        ``confirmed_sessions`` (compared and found identical by this decision),
        and (4) no newer blocking decision on the symbol touches those sessions.
        Request-range containment is never used.
        """
        blocks = connection.execute(
            "SELECT e.event_id, e.observation_id, e.detail_json FROM admission_events e "
            "WHERE e.symbol=? AND e.event_type=? AND e.application_state='blocked' "
            "AND e.event_id < ? "
            "AND NOT EXISTS (SELECT 1 FROM block_resolutions r WHERE r.block_event_id=e.event_id)",
            (symbol, EVENT_ADMISSION_DECISION, decision_event_id),
        ).fetchall()
        newer = [
            set(json.loads(row["detail_json"]).get("affected_sessions", []))
            for row in connection.execute(
                "SELECT detail_json FROM admission_events WHERE symbol=? AND event_type=? "
                "AND application_state='blocked' AND event_id > ?",
                (symbol, EVENT_ADMISSION_DECISION, decision_event_id),
            ).fetchall()
        ]
        for block in blocks:
            detail = json.loads(block["detail_json"])
            if not detail.get("auto_resolvable", True):
                continue
            affected = list(detail.get("affected_sessions", []))
            if not affected or not set(affected) <= confirmed_sessions:
                continue
            if any(set(affected) & later for later in newer):
                continue  # a newer contradictory decision touches these sessions
            kind = (
                RESOLUTION_REEVALUATED_ACCEPTED
                if block["observation_id"] == observation_id
                else RESOLUTION_SUPERSEDED_BY_ACCEPTED
            )
            connection.execute(
                "INSERT INTO block_resolutions(block_event_id,symbol,resolution_kind,"
                "resolved_by_observation_id,reviewer,reason,resolved_at_utc,detail_json)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    block["event_id"],
                    symbol,
                    kind,
                    observation_id,
                    None,
                    "accepting decision revalidated every affected session with identical values",
                    iso_utc(utc_now()),
                    canonical_json(
                        {
                            "affected_sessions": affected,
                            "decision_event_id": decision_event_id,
                        }
                    ),
                ),
            )

    def session_provenance(self, symbol: str, session: str) -> dict[str, object]:
        """Where did ``(symbol, session)`` come from?

        ``origin`` is one of ``BASELINE_LEGACY``, ``OBSERVATION`` (applied
        through the guard), ``PENDING_APPLICATION`` (claimed by an open intent),
        ``UNATTRIBUTED`` (present in the market but owned by nothing),
        ``REMOVED`` (attributed but no longer present) or ``ABSENT``.
        """
        if self.market_path is None:
            raise ObservationLogError("session provenance needs the log to be bound to a market database")
        baseline = self.get_baseline()
        baseline_id = None if baseline is None else str(baseline["baseline_id"])
        connection = open_market_readonly(self.market_path)
        try:
            present = (
                connection.execute(
                    "SELECT 1 FROM prices WHERE symbol=? AND substr(time,1,10)=? LIMIT 1",
                    (symbol, session),
                ).fetchone()
                is not None
            )
        finally:
            connection.close()
        applied = self._read(
            "SELECT observation_id, version_id FROM applied_sessions WHERE symbol=? AND session=?",
            (symbol, session),
        )
        base = {"symbol": symbol, "session": session, "baseline_id": baseline_id}
        if applied:
            origin = "OBSERVATION" if present else "REMOVED"
            return {
                **base,
                "origin": origin,
                "observation_id": str(applied[0]["observation_id"]),
                "version_id": str(applied[0]["version_id"]),
            }
        for intent in self.open_intents(symbol):
            if session in intent["sessions"]:
                return {
                    **base,
                    "origin": "PENDING_APPLICATION",
                    "observation_id": intent["observation_id"],
                    "version_id": None,
                }
        if present:
            row = self.baseline_symbol(symbol)
            if row is not None and session <= str(row["last_session"]):
                return {
                    **base,
                    "origin": "BASELINE_LEGACY",
                    "observation_id": None,
                    "version_id": f"baseline:{baseline_id}",
                }
            return {**base, "origin": "UNATTRIBUTED", "observation_id": None, "version_id": None}
        return {**base, "origin": "ABSENT", "observation_id": None, "version_id": None}

    def state_signature(self) -> tuple[int, int, int, int]:
        """Cheap, monotonic fingerprint of everything that can change a qualification.

        ``(last admission event, resolutions, applied sessions, symbol versions)``.
        Every log table is append-only, so any change to blocks, resolutions,
        applications or versions changes this tuple; an unchanged tuple proves
        that a cached evaluation is still current.
        """
        row = self._read(
            "SELECT (SELECT COALESCE(MAX(event_id),0) FROM admission_events),"
            "(SELECT COUNT(*) FROM block_resolutions),"
            "(SELECT COUNT(*) FROM applied_sessions),"
            "(SELECT COUNT(*) FROM symbol_versions)"
        )[0]
        return (int(row[0]), int(row[1]), int(row[2]), int(row[3]))

    def window_provenance(self, symbol: str, start: str, end: str) -> dict[str, object]:
        """Compact lineage summary of every stored session of ``symbol`` in ``[start, end]``.

        Reads once (no per-session round trips).  ``origin_counts`` counts the
        stored sessions per origin (same classification as :meth:`session_provenance`),
        ``digest`` is a hash over the ordered ``(session, origin, observation_id)``
        triples, so any later change to the window's attribution is detectable
        without storing the bars.
        """
        if self.market_path is None:
            raise ObservationLogError("window provenance needs the log to be bound to a market database")
        baseline = self.get_baseline()
        baseline_id = None if baseline is None else str(baseline["baseline_id"])
        connection = open_market_readonly(self.market_path)
        try:
            sessions = [
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT substr(time,1,10) FROM prices WHERE symbol=? "
                    "AND substr(time,1,10) BETWEEN ? AND ? ORDER BY 1",
                    (symbol, start, end),
                )
            ]
        finally:
            connection.close()
        applied = {
            str(row["session"]): (str(row["observation_id"]), str(row["version_id"]))
            for row in self._read(
                "SELECT session, observation_id, version_id FROM applied_sessions "
                "WHERE symbol=? AND session BETWEEN ? AND ?",
                (symbol, start, end),
            )
        }
        pending: set[str] = set()
        for intent in self.open_intents(symbol):
            pending.update(str(item) for item in intent["sessions"] if start <= str(item) <= end)
        baseline_row = self.baseline_symbol(symbol)
        baseline_last = None if baseline_row is None else str(baseline_row["last_session"])
        counts: dict[str, int] = {}
        triples: list[tuple[str, str, str]] = []
        present = set(sessions)
        for session in sessions:
            if session in applied:
                origin, observation = "OBSERVATION", applied[session][0]
            elif session in pending:
                origin, observation = "PENDING_APPLICATION", ""
            elif baseline_last is not None and session <= baseline_last:
                origin, observation = "BASELINE_LEGACY", ""
            else:
                origin, observation = "UNATTRIBUTED", ""
            counts[origin] = counts.get(origin, 0) + 1
            triples.append((session, origin, observation))
        for session in sorted(set(applied) - present):
            counts["REMOVED"] = counts.get("REMOVED", 0) + 1
            triples.append((session, "REMOVED", applied[session][0]))
        for session in sorted(pending - present - set(applied)):
            counts["PENDING_APPLICATION"] = counts.get("PENDING_APPLICATION", 0) + 1
            triples.append((session, "PENDING_APPLICATION", ""))
        triples.sort()
        return {
            "symbol": symbol,
            "start": start,
            "end": end,
            "baseline_id": baseline_id,
            "session_count": len(triples),
            "origin_counts": dict(sorted(counts.items())),
            "first_session": triples[0][0] if triples else None,
            "last_session": triples[-1][0] if triples else None,
            "digest": sha256_text(canonical_json(triples)),
        }

    def dataset_version_identity(self) -> dict[str, object]:
        """Stable dataset identity plus the state consumers must respect.

        The version id advances only when a successful application appends
        sessions. Blocked or pending observations, retries of applied
        observations and no-op applications never change it.
        """
        baseline = self.get_baseline()
        if baseline is None:
            raise ObservationLogError("no baseline registered")
        rows = self._read(
            "SELECT v.symbol, v.version_id FROM symbol_versions v WHERE v.seq = ("
            "SELECT MAX(seq) FROM symbol_versions WHERE symbol=v.symbol) ORDER BY v.symbol"
        )
        versions = {str(row["symbol"]): str(row["version_id"]) for row in rows}
        identity = sha256_text(
            canonical_json({"baseline_id": baseline["baseline_id"], "versions": versions})
        )
        blocks = self.unresolved_blocks()
        pending = self.open_intents()
        return {
            "dataset_version_id": f"dataset-{identity[:32]}",
            "baseline_id": baseline["baseline_id"],
            "baseline_content_sha256": baseline["content_sha256"],
            "provenance_label": baseline["provenance_label"],
            "symbol_versions": versions,
            "pending_applications": sorted({str(item["symbol"]) for item in pending}),
            "unresolved_block_symbols": sorted({str(block["symbol"]) for block in blocks}),
            "unresolved_block_count": len(blocks),
            "consumable": not blocks and not pending,
        }

    def run_summary(self, run_id: str) -> dict[str, dict[str, object]]:
        """Per-symbol outcome of one run (partial multi-symbol runs stay visible)."""
        rows = self._read(
            "SELECT symbol, observation_id, event_type, admission_result, application_state "
            "FROM admission_events WHERE run_id=? AND event_type IN (?,?,?) ORDER BY event_id",
            (run_id, EVENT_ADMISSION_DECISION, EVENT_APPLICATION_RESULT, EVENT_VALIDATION_REJECTED),
        )
        summary: dict[str, dict[str, object]] = {}
        for row in rows:
            summary[str(row["symbol"])] = {
                "observation_id": row["observation_id"],
                "admission_result": row["admission_result"],
                "application_state": row["application_state"],
            }
        return summary


def open_observation_log(
    market_db_path: str | Path | None = None,
    log_path: str | Path | None = None,
    *,
    readonly: bool = False,
    require_baseline: bool = False,
) -> ObservationLog:
    """Open the log for one market dataset, verifying the binding.

    Rejects a missing market database, a log path equal to the market
    database, and (when a baseline exists) a different dataset.
    """
    market = resolve_market_database_path(market_db_path)
    if not market.is_file():
        raise ObservationLogError(f"market database not found: {market}")
    log = ObservationLog(
        resolve_observation_log_path(market, log_path), market_path=market, readonly=readonly
    )
    if log.get_baseline() is not None:
        log.assert_bound_to(market)
    elif require_baseline:
        raise ObservationLogError("observation log has no registered baseline")
    return log
