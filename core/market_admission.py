"""Revision admission guard for ``market.db`` price writes (R1).

This is the **single** implementation through which provider price batches may
reach the ``prices`` table. ``core.database.save_price_data`` delegates here;
``scripts.update_data`` and ``scripts.backfill_market_data`` only call
``save_price_data``.

Sequence for every symbol batch::

    fetch (caller) -> normalize/validate -> persist immutable observation + receipt
      -> [market write lock] reconcile open intents -> verify binding
         -> compare every overlapping stored OHLCV tuple -> record decision
         -> record application intent (ownership) -> append (plain INSERT)
      -> [lock released] record application result (+ chained version)

* Identical overlap: only genuinely new sessions (later than the symbol's
  latest stored session) are appended. Identical historical rows are not
  rewritten.
* Changed or incomplete overlap, or any history extension: ``market.db`` is
  **not** modified, the candidate stays in the log, and an *unresolved block*
  is recorded. Consumers (integrity gate, scanner, lifecycle, forward) fail
  closed while a block affecting their inputs is unresolved. Nothing is
  repaired or rebuilt.

Ownership and concurrency
-------------------------
Everything from reconciliation to the market commit happens under one
``BEGIN IMMEDIATE`` on ``market.db`` so admissions are serialized. The
application *intent* (the exact sessions an observation is about to insert) is
made durable in the log while holding that lock and before the insert, so it is
the authoritative owner of those sessions. Recovery never infers ownership from
value equality: it only checks whether an open intent's rows reached the market
(then the intent's owner is recorded as applied) or not (then the intent is
abandoned). Two observations proposing the same session/value therefore can
neither double-attribute it nor leave it ambiguous.

The two SQLite files are not one atomic transaction; consistency is by this
ordering and by idempotent identities.
"""

from __future__ import annotations

import sqlite3
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from enum import Enum
from importlib import metadata
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from core.market_observation_log import (
    EVENT_ADMISSION_DECISION,
    EVENT_APPLICATION_RESULT,
    EVENT_INTENT_ABANDONED,
    EVENT_RETRY_RECEIPT,
    EVENT_REVISION_DETECTED,
    EVENT_SUPERSEDED,
    EVENT_VALIDATION_REJECTED,
    INTENT_KIND_REBASE,
    NORMALIZATION_VERSION,
    PRICE_BASIS_UNKNOWN,
    STATE_ADMITTED,
    STATE_APPLIED,
    STATE_BLOCKED,
    STATE_FAILED,
    STATE_SUPERSEDED,
    ObservationLog,
    StoredHistoryError,
    canonical_json,
    canonical_row,
    current_run_id,
    iso_utc,
    open_observation_log,
    read_symbol_rows,
    rows_content_sha256,
    sha256_text,
    utc_now,
)
from core.paths import resolve_market_database_path

# Vietnam has no DST; a fixed offset avoids a tzdata dependency on Windows.
ICT = timezone(timedelta(hours=7))
# HOSE/HNX continuous session and ATC finish by 15:00 local time. This is an
# upper bound only: there is no holiday calendar and no publication-lag evidence
# here (the unwired quantlab.completed_session machinery covers that).
SESSION_COMPLETE_LOCAL_TIME = dtime(15, 0)

INDEX_SYMBOLS = frozenset({"VNINDEX", "VN30", "HNXINDEX", "UPCOMINDEX"})
DIFF_SAMPLE_LIMIT = 25


class AdmissionResult(str, Enum):
    ADMITTED_INITIAL_LOAD = "ADMITTED_INITIAL_LOAD"
    ADMITTED_APPEND = "ADMITTED_APPEND"
    ADMITTED_NO_NEW_SESSIONS = "ADMITTED_NO_NEW_SESSIONS"
    BLOCKED_REVISION_CHANGED = "BLOCKED_REVISION_CHANGED"
    BLOCKED_OVERLAP_INCOMPLETE = "BLOCKED_OVERLAP_INCOMPLETE"
    BLOCKED_HISTORY_EXTENSION = "BLOCKED_HISTORY_EXTENSION"
    BLOCKED_STORED_HISTORY_INVALID = "BLOCKED_STORED_HISTORY_INVALID"
    BLOCKED_DATASET_MISMATCH = "BLOCKED_DATASET_MISMATCH"
    REJECTED_INVALID_CANDIDATE = "REJECTED_INVALID_CANDIDATE"
    FAILED_APPLICATION = "FAILED_APPLICATION"
    #: recorded only by the reviewed per-symbol rebase (core.market_rebase),
    #: never by ingestion.
    REBASED_BY_REVIEW = "REBASED_BY_REVIEW"

    @property
    def admitted(self) -> bool:
        return self.value.startswith("ADMITTED_")

    @property
    def blocked(self) -> bool:
        return self.value.startswith("BLOCKED_")


class CandidateRejected(ValueError):
    """The candidate batch is structurally invalid; nothing may be admitted."""

    def __init__(self, reason: str, message: str, detail: Mapping[str, object] | None = None):
        super().__init__(message)
        self.reason = reason
        self.detail = dict(detail or {})


@dataclass(frozen=True, slots=True)
class AdmissionContext:
    source: str
    endpoint: str
    source_mode: str
    package_name: str
    package_version: str
    request_start: str
    request_end: str
    interval: str = "1D"
    run_id: str | None = None
    fetched_at_utc: datetime | None = None


@dataclass(frozen=True, slots=True)
class OverlapComparison:
    result: AdmissionResult
    reason: str
    stored_sessions: int
    stored_latest: str | None
    stored_window_sessions: int
    candidate_sessions: int
    compared_sessions: int
    identical_sessions: int
    changed: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]
    missing_from_candidate: tuple[str, ...]
    unstored_inside_history: tuple[str, ...]
    new_sessions: tuple[str, ...]
    legacy_sessions_outside_window: int
    stored_window_sha256: str
    candidate_window_sha256: str
    confirmed_sessions: tuple[str, ...] = ()

    @property
    def affected_sessions(self) -> tuple[str, ...]:
        """Sessions whose agreement must be re-established to lift a block."""
        if not self.result.blocked:
            return ()
        affected = sorted(
            {item[0] for item in self.changed}
            | set(self.missing_from_candidate)
            | set(self.unstored_inside_history)
        )
        if not affected and self.stored_latest is not None:
            affected = [self.stored_latest]
        return tuple(affected)

    def to_detail(self) -> dict[str, object]:
        return {
            "result": self.result.value,
            "reason": self.reason,
            "stored_sessions": self.stored_sessions,
            "stored_window_sessions": self.stored_window_sessions,
            "candidate_sessions": self.candidate_sessions,
            "compared_sessions": self.compared_sessions,
            "identical_sessions": self.identical_sessions,
            "changed_count": len(self.changed),
            "changed_sample": [
                {"session": s, "stored": list(a), "candidate": list(b)}
                for s, a, b in self.changed[:DIFF_SAMPLE_LIMIT]
            ],
            "missing_from_candidate_count": len(self.missing_from_candidate),
            "missing_from_candidate_sample": list(self.missing_from_candidate[:DIFF_SAMPLE_LIMIT]),
            "unstored_inside_history_count": len(self.unstored_inside_history),
            "unstored_inside_history_sample": list(self.unstored_inside_history[:DIFF_SAMPLE_LIMIT]),
            "new_sessions": list(self.new_sessions),
            "legacy_sessions_outside_window": self.legacy_sessions_outside_window,
            "stored_window_sha256": self.stored_window_sha256,
            "candidate_window_sha256": self.candidate_window_sha256,
            "affected_sessions": list(self.affected_sessions),
            "auto_resolvable": True,
        }


@dataclass(frozen=True, slots=True)
class AdmissionOutcome:
    symbol: str
    result: AdmissionResult
    observation_id: str | None
    batch_hash: str | None
    reason: str
    applied: bool
    appended_sessions: tuple[str, ...] = ()
    version_id: str | None = None
    detail: Mapping[str, object] = field(default_factory=dict)
    error: str | None = None
    receipt_id: int | None = None
    already_applied: bool = False

    @property
    def blocked(self) -> bool:
        return self.result.blocked

    @property
    def rejected(self) -> bool:
        return self.result is AdmissionResult.REJECTED_INVALID_CANDIDATE

    @property
    def failed(self) -> bool:
        return self.result is AdmissionResult.FAILED_APPLICATION

    @property
    def rows_appended(self) -> int:
        return len(self.appended_sessions)


# ---------------------------------------------------------------------- helpers
def completed_session_cutoff(now: datetime) -> date:
    """Latest session date that can be considered complete at ``now``."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local = now.astimezone(ICT)
    day = local.date()
    if local.time() < SESSION_COMPLETE_LOCAL_TIME:
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _date_text(value: object, name: str) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"{name} is not a valid date: {value!r}")
    return parsed.strftime("%Y-%m-%d")


def units_for(symbol: str) -> tuple[str, str]:
    if symbol in INDEX_SYMBOLS:
        return "INDEX_POINTS", "SHARES"
    return "THOUSAND_VND_PER_SHARE", "SHARES"


def provider_package_version(name: str = "vnstock") -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "UNKNOWN"


def kbs_context(
    mode: str,
    request_start: object,
    request_end: object,
    *,
    interval: str = "1D",
) -> AdmissionContext:
    """Context for the KBS-through-``vnstock.api`` price source."""
    return AdmissionContext(
        source="KBS",
        endpoint="vnstock.api.quote.Quote(source='KBS').history",
        source_mode=mode,
        package_name="vnstock",
        package_version=provider_package_version("vnstock"),
        request_start=_date_text(request_start, "request_start"),
        request_end=_date_text(request_end, "request_end"),
        interval=interval,
        run_id=current_run_id(),
    )


def _session_dates(series: pd.Series) -> pd.Series:
    """Map provider timestamps to exchange-session dates (``YYYY-MM-DD``).

    Rule: tz-naive timestamps are taken as already being exchange-local dates;
    tz-aware timestamps are converted to Vietnam time (UTC+7) before the date is
    taken, so a UTC instant is never read as the session date. Mixed time zones
    are rejected.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            parsed = pd.to_datetime(series, errors="coerce")
    except (ValueError, TypeError) as error:
        raise CandidateRejected("INVALID_TIME", f"unparseable time column: {error}") from error
    if parsed.isna().any():
        raise CandidateRejected(
            "INVALID_TIME", f"{int(parsed.isna().sum())} rows have an invalid time"
        )
    if parsed.dtype == object:
        raise CandidateRejected("MIXED_TIMEZONES", "time column mixes time zones")
    if getattr(parsed.dt, "tz", None) is not None:
        parsed = parsed.dt.tz_convert(ICT)
    return parsed.dt.strftime("%Y-%m-%d")


def _post_normalization_problem(row: Sequence[str]) -> str | None:
    open_, high, low, close = (float(value) for value in row[:4])
    volume = int(row[4])
    if min(open_, high, low, close) <= 0:
        return "non-positive price after rounding"
    if volume < 0:
        return "negative volume"
    if high < low or open_ > high or open_ < low or close > high or close < low:
        return "OHLC relation violated after rounding"
    return None


def normalize_candidate(
    frame: pd.DataFrame | None,
    symbol: str,
    *,
    request_start: str,
    request_end: str,
    cutoff: date,
) -> tuple[dict[str, tuple[str, ...]], list[str]]:
    """Normalize one provider frame into ``{session: canonical OHLCV}``.

    Sessions after the completed-session cutoff are excluded (and returned for
    the record). Anything structurally wrong raises :class:`CandidateRejected`.
    The canonical rows are re-validated after rounding.
    """
    if frame is None or len(frame) == 0:
        return {}, []

    data = frame.copy()
    data.columns = [str(column).lower() for column in data.columns]
    required = ["time", "open", "high", "low", "close", "volume"]
    missing = [column for column in required if column not in data.columns]
    if missing:
        raise CandidateRejected(
            "MISSING_COLUMNS", f"missing columns: {', '.join(missing)}", {"missing": missing}
        )

    if "symbol" in data.columns:
        other = sorted({str(value).strip().upper() for value in data["symbol"]} - {symbol})
        if other:
            raise CandidateRejected(
                "SYMBOL_MISMATCH",
                f"batch for {symbol} contains other symbols: {', '.join(other[:5])}",
                {"other_symbols": other[:10]},
            )

    data["session"] = _session_dates(data["time"])

    excluded = sorted(set(data.loc[data["session"] > cutoff.isoformat(), "session"]))
    data = data[data["session"] <= cutoff.isoformat()]
    if data.empty:
        return {}, excluded

    duplicated = data["session"][data["session"].duplicated(keep=False)]
    if not duplicated.empty:
        sample = sorted(set(duplicated))[:5]
        raise CandidateRejected(
            "DUPLICATE_SESSION_KEYS",
            f"duplicate (symbol, session) keys: {', '.join(sample)}",
            {"sessions": sample},
        )

    weekend = [
        session
        for session in sorted(set(data["session"]))
        if date.fromisoformat(session).weekday() >= 5
    ]
    if weekend:
        raise CandidateRejected(
            "WEEKEND_SESSION",
            f"weekend sessions are not exchange sessions: {', '.join(weekend[:5])}",
            {"sessions": weekend[:10]},
        )

    numeric = ["open", "high", "low", "close", "volume"]
    for column in numeric:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    invalid = (
        ~np.isfinite(data[numeric].to_numpy(dtype=float)).all(axis=1)
        | (data[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | (data["volume"] < 0)
        | (data["high"] < data["low"])
        | (data["open"] > data["high"])
        | (data["open"] < data["low"])
        | (data["close"] > data["high"])
        | (data["close"] < data["low"])
    )
    if invalid.any():
        sample = [f"{symbol}@{s}" for s in data.loc[invalid, "session"].head(5)]
        raise CandidateRejected(
            "INVALID_OHLCV",
            f"{int(invalid.sum())} rows have invalid OHLCV: {', '.join(sample)}",
            {"sample": sample, "stage": "PRE_NORMALIZATION"},
        )

    out_of_range = data.loc[
        (data["session"] < request_start) | (data["session"] > request_end), "session"
    ]
    if not out_of_range.empty:
        sample = sorted(set(out_of_range))[:5]
        raise CandidateRejected(
            "REQUEST_RANGE_VIOLATION",
            f"sessions outside requested range {request_start}..{request_end}: {', '.join(sample)}",
            {"sessions": sample},
        )

    rows: dict[str, tuple[str, ...]] = {}
    try:
        for record in data.itertuples(index=False):
            rows[str(record.session)] = canonical_row(
                record.open, record.high, record.low, record.close, record.volume
            )
    except ValueError as error:
        raise CandidateRejected("INVALID_OHLCV", str(error), {"stage": "NORMALIZATION"}) from error
    for session in sorted(rows):
        problem = _post_normalization_problem(rows[session])
        if problem is not None:
            raise CandidateRejected(
                "INVALID_OHLCV",
                f"{symbol}@{session}: {problem}",
                {"stage": "POST_NORMALIZATION", "sample": [f"{symbol}@{session}"]},
            )
    return rows, excluded


def batch_hash(
    symbol: str,
    context: AdmissionContext,
    rows: Mapping[str, Sequence[str]],
    *,
    price_unit: str,
    volume_unit: str,
) -> str:
    return sha256_text(
        canonical_json(
            {
                "normalization_version": NORMALIZATION_VERSION,
                "source": context.source,
                "symbol": symbol,
                "interval": context.interval,
                "price_unit": price_unit,
                "volume_unit": volume_unit,
                "price_basis": PRICE_BASIS_UNKNOWN,
                "rows": [[session, *rows[session]] for session in sorted(rows)],
            }
        )
    )


def observation_identity(
    symbol: str, context: AdmissionContext, cutoff: date, digest: str
) -> str:
    """Semantic identity: every field that can change parsing/source semantics.

    Fetch time and run identity are deliberately excluded (they live on the
    fetch receipts), so re-fetching the same content maps to the same
    observation, while a different package version, endpoint, mode,
    normalization version, window, cutoff or content does not.
    """
    return "obs-" + sha256_text(
        canonical_json(
            {
                "source": context.source,
                "endpoint": context.endpoint,
                "source_mode": context.source_mode,
                "package_name": context.package_name,
                "package_version": context.package_version,
                "symbol": symbol,
                "interval": context.interval,
                "request_start": context.request_start,
                "request_end": context.request_end,
                "completed_session_cutoff": cutoff.isoformat(),
                "normalization_version": NORMALIZATION_VERSION,
                "batch_hash": digest,
            }
        )
    )


def classify_overlap(
    stored: Mapping[str, tuple[str, ...]],
    candidate: Mapping[str, tuple[str, ...]],
    *,
    request_start: str,
    request_end: str,
) -> OverlapComparison:
    """Compare every overlapping stored tuple with the candidate. Pure function."""
    stored_latest = max(stored) if stored else None
    stored_earliest = min(stored) if stored else None
    window = {s: v for s, v in stored.items() if request_start <= s <= request_end}

    changed = tuple(
        (s, window[s], candidate[s]) for s in sorted(window) if s in candidate and window[s] != candidate[s]
    )
    missing = tuple(s for s in sorted(window) if s not in candidate)
    unstored_inside = tuple(
        s
        for s in sorted(candidate)
        if stored_latest is not None and s <= stored_latest and s not in stored
    )
    new_sessions = tuple(
        s for s in sorted(candidate) if stored_latest is None or s > stored_latest
    )
    compared = [s for s in window if s in candidate]
    identical = sum(1 for s in compared if window[s] == candidate[s])
    legacy_outside = sum(1 for s in stored if s < request_start or s > request_end)

    stored_hash = rows_content_sha256("w", {s: window[s] for s in sorted(window)})
    candidate_hash = rows_content_sha256("w", {s: candidate[s] for s in compared})

    if not stored:
        result, reason = AdmissionResult.ADMITTED_INITIAL_LOAD, "NO_STORED_HISTORY"
    elif not window:
        result, reason = (
            AdmissionResult.BLOCKED_OVERLAP_INCOMPLETE,
            "NO_STORED_SESSION_IN_REQUEST_WINDOW",
        )
    elif changed:
        result, reason = AdmissionResult.BLOCKED_REVISION_CHANGED, "OVERLAP_VALUES_CHANGED"
    elif missing:
        result, reason = (
            AdmissionResult.BLOCKED_OVERLAP_INCOMPLETE,
            "STORED_SESSION_MISSING_FROM_CANDIDATE",
        )
    elif unstored_inside:
        result, reason = (
            AdmissionResult.BLOCKED_HISTORY_EXTENSION,
            "CANDIDATE_SESSION_NOT_STORED_INSIDE_STORED_HISTORY"
            if stored_earliest is not None and unstored_inside[0] >= stored_earliest
            else "CANDIDATE_EXTENDS_HISTORY_BACKWARDS",
        )
    elif new_sessions:
        result, reason = AdmissionResult.ADMITTED_APPEND, "OVERLAP_IDENTICAL_NEW_SESSIONS"
    else:
        result, reason = AdmissionResult.ADMITTED_NO_NEW_SESSIONS, "OVERLAP_IDENTICAL_NOTHING_NEW"

    return OverlapComparison(
        result=result,
        reason=reason,
        stored_sessions=len(stored),
        stored_latest=stored_latest,
        stored_window_sessions=len(window),
        candidate_sessions=len(candidate),
        compared_sessions=len(compared),
        identical_sessions=identical,
        changed=changed,
        missing_from_candidate=missing,
        unstored_inside_history=unstored_inside,
        new_sessions=new_sessions,
        legacy_sessions_outside_window=legacy_outside,
        stored_window_sha256=stored_hash,
        candidate_window_sha256=candidate_hash,
        confirmed_sessions=tuple(sorted(s for s in compared if window[s] == candidate[s])),
    )


# ------------------------------------------------------------------ market I/O
def _market_connection(path: Path, *, write: bool) -> sqlite3.Connection:
    mode = "rw" if write else "ro"
    return sqlite3.connect(f"{path.as_uri()}?mode={mode}", uri=True, timeout=30, isolation_level=None)


def _insert_rows(
    connection: sqlite3.Connection,
    symbol: str,
    candidate: Mapping[str, tuple[str, ...]],
    sessions: Sequence[str],
) -> None:
    """The only statement that adds provider rows: a plain INSERT, never REPLACE."""
    connection.executemany(
        "INSERT INTO prices (symbol, time, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                symbol,
                session,
                float(candidate[session][0]),
                float(candidate[session][1]),
                float(candidate[session][2]),
                float(candidate[session][3]),
                int(candidate[session][4]),
            )
            for session in sessions
        ],
    )


# -------------------------------------------------------------- reconciliation
def _record_block(
    log: ObservationLog,
    *,
    observation_id: str,
    receipt_id: int | None,
    symbol: str,
    result: AdmissionResult,
    reason: str,
    detail: Mapping[str, object],
    run_id: str | None,
) -> None:
    # A repeated identical retry of a still-unresolved block adds only a
    # receipt-level annotation, never a second block to resolve.
    for existing in log.unresolved_blocks([symbol]):
        if (
            existing["observation_id"] == observation_id
            and existing["admission_result"] == result.value
        ):
            log.append_event(
                observation_id=observation_id,
                receipt_id=receipt_id,
                symbol=symbol,
                event_type=EVENT_RETRY_RECEIPT,
                application_state=STATE_BLOCKED,
                admission_result=result.value,
                run_id=run_id,
                detail={"note": "block already unresolved for this observation", "block_id": existing["block_id"]},
            )
            return
    log.append_event(
        observation_id=observation_id,
        receipt_id=receipt_id,
        symbol=symbol,
        event_type=EVENT_ADMISSION_DECISION,
        application_state=STATE_BLOCKED,
        admission_result=result.value,
        run_id=run_id,
        detail={**dict(detail), "reason": reason},
    )
    log.append_event(
        observation_id=observation_id,
        receipt_id=receipt_id,
        symbol=symbol,
        event_type=EVENT_REVISION_DETECTED,
        application_state=STATE_BLOCKED,
        admission_result=result.value,
        run_id=run_id,
        detail={
            "revision_kind": result.value,
            "reason": reason,
            "market_db_modified": False,
            "action": "NO_AUTOMATIC_REPAIR_OR_REBUILD",
        },
    )


def reconcile_open_intents(
    log: ObservationLog,
    symbol: str,
    stored: Mapping[str, tuple[str, ...]],
    *,
    run_id: str | None,
) -> tuple[str, str] | None:
    """Settle application intents left open by a crash (called under the lock).

    Ownership comes from the *intent*, which was made durable under the market
    write lock before the insert. Recovery only checks whether the intent's
    rows reached the market:

    * all present with the observed values -> record the intent's owner as
      applied (``reconciled``); ``market.db`` is not touched;
    * none present -> the insert never committed; abandon the intent;
    * anything else -> ambiguous; returns ``(observation_id, message)`` so the
      caller records a non-auto-resolvable block.
    """
    for intent in log.open_intents(symbol):
        if intent.get("kind") == INTENT_KIND_REBASE:
            # A reviewed rebase interrupted between its durable intent and its
            # finalization: finalize it only if the market holds exactly the
            # reviewed post-rebase rows, abandon it only if the old basis is
            # fully intact, otherwise fail closed.
            from core.market_rebase import reconcile_rebase_intent  # lazy: avoids an import cycle

            if reconcile_rebase_intent(log, intent, stored, run_id=run_id) == "AMBIGUOUS":
                return (
                    intent["observation_id"],
                    f"{symbol}: open reviewed-rebase intent is only partially present in the market",
                )
            continue
        observation = log.get_observation(intent["observation_id"])
        if observation is None:  # pragma: no cover - defensive
            continue
        rows = {str(item[0]): tuple(item[1:]) for item in observation["rows"]}
        sessions = list(intent["sessions"])
        present = [s for s in sessions if stored.get(s) == rows.get(s)]
        any_stored = [s for s in sessions if s in stored]
        if sessions and len(present) == len(sessions):
            decisions = [
                event
                for event in log.list_events(observation_id=intent["observation_id"])
                if event["event_type"] == EVENT_ADMISSION_DECISION
                and event["application_state"] == STATE_ADMITTED
            ]
            log.record_application(
                observation_id=intent["observation_id"],
                symbol=symbol,
                admission_result=(
                    str(decisions[-1]["admission_result"])
                    if decisions
                    else AdmissionResult.ADMITTED_APPEND.value
                ),
                appended_sessions=sessions,
                row_count=len(stored),
                last_session=max(stored) if stored else None,
                content_sha256=rows_content_sha256(symbol, stored),
                run_id=run_id,
                reconciled=True,
                detail={"reconciliation": "OPEN_INTENT_ROWS_PRESENT_IN_MARKET"},
            )
        elif not any_stored:
            log.append_event(
                observation_id=intent["observation_id"],
                symbol=symbol,
                event_type=EVENT_INTENT_ABANDONED,
                application_state=STATE_FAILED,
                admission_result="INTENT_ABANDONED",
                run_id=run_id,
                detail={
                    "reconciliation": "OPEN_INTENT_ROWS_ABSENT_FROM_MARKET",
                    "sessions": sessions,
                },
            )
        else:
            return (
                intent["observation_id"],
                f"{symbol}: open application intent is only partially or differently present "
                f"in the market ({len(present)}/{len(sessions)} identical)",
            )
    return None


def _supersede_stale_pending(
    log: ObservationLog, symbol: str, *, keep_observation_id: str, run_id: str | None
) -> None:
    for pending in log.pending_observations(symbol, include_failed=True):
        if pending["observation_id"] == keep_observation_id:
            continue
        log.append_event(
            observation_id=str(pending["observation_id"]),
            symbol=symbol,
            event_type=EVENT_SUPERSEDED,
            application_state=STATE_SUPERSEDED,
            admission_result="SUPERSEDED",
            run_id=run_id,
            detail={
                "reconciliation": "UNAPPLIED_OBSERVATION_REPLACED_BY_NEWER_ADMISSION",
                "superseded_by": keep_observation_id,
            },
        )


def _receipt_matches_market(
    rows: Mapping[str, tuple[str, ...]], stored: Mapping[str, tuple[str, ...]] | None
) -> bool:
    """May an APPLIED receipt be reused against the CURRENT market (V1 P1-OPS-1)?

    Only if every row of the observation is stored now with exactly the observed
    values. An ordinary application always leaves its observation fully stored,
    so outside a reviewed rebase this is always true. A rebase invalidates it
    when it changed stored values the observation confirmed, or when its
    reviewed observation carried sessions it deliberately did not insert.
    """
    if stored is None:
        return False
    return all(stored.get(session) == values for session, values in rows.items())


def readmission_identity(observation_id: str, basis_version_ref: str) -> str:
    """Identity of the same provider content re-admitted against a changed stored basis.

    The original APPLIED receipt is absorbing and is never deleted or reopened;
    the content is compared again as a new observation bound to the basis it is
    compared with, so repeats on that basis stay idempotent.
    """
    return "obs-" + sha256_text(
        canonical_json(
            {
                "readmission_of": observation_id,
                "basis_version_ref": basis_version_ref,
                "reason": "APPLIED_RECEIPT_INVALIDATED_BY_BASIS_CHANGE",
            }
        )
    )


def _applied_outcome(
    symbol: str, observation_id: str, digest: str, receipt: Mapping[str, object], *, receipt_id: int | None
) -> AdmissionOutcome:
    result = AdmissionResult(str(receipt.get("admission_result") or AdmissionResult.ADMITTED_NO_NEW_SESSIONS.value))
    return AdmissionOutcome(
        symbol=symbol,
        result=result,
        observation_id=observation_id,
        batch_hash=digest,
        reason="ALREADY_APPLIED",
        applied=True,
        appended_sessions=tuple(receipt.get("appended_sessions", ())),
        version_id=str(receipt["version_id"]),
        detail=dict(receipt),
        receipt_id=receipt_id,
        already_applied=True,
    )


# ------------------------------------------------------------------ main entry
class _Done(Exception):
    """Internal control flow: leave the locked section with ``outcome`` set."""


def admit_price_batch(
    frame: pd.DataFrame | None,
    *,
    symbol: str,
    context: AdmissionContext,
    market_db_path: str | Path | None = None,
    log_path: str | Path | None = None,
    now: datetime | None = None,
) -> AdmissionOutcome:
    """Admit (or block) one symbol's provider batch. See module docstring."""
    symbol = str(symbol).strip().upper()
    now_utc = now or utc_now()
    run_id = context.run_id or current_run_id()
    market_path = resolve_market_database_path(market_db_path)
    log = open_observation_log(market_path, log_path)  # verifies dataset binding
    log.ensure_initial_baseline(market_path, now=now_utc)

    request_start = _date_text(context.request_start, "request_start")
    request_end = _date_text(context.request_end, "request_end")
    cutoff = completed_session_cutoff(now_utc)
    price_unit, volume_unit = units_for(symbol)

    def rejected(reason: str, message: str, detail: Mapping[str, object]) -> AdmissionOutcome:
        log.append_event(
            observation_id=None,
            symbol=symbol,
            event_type=EVENT_VALIDATION_REJECTED,
            application_state=STATE_FAILED,
            admission_result=AdmissionResult.REJECTED_INVALID_CANDIDATE.value,
            run_id=run_id,
            detail={
                "reason": reason,
                "message": message,
                "request_start": request_start,
                "request_end": request_end,
                "completed_session_cutoff": cutoff.isoformat(),
                **dict(detail),
            },
        )
        return AdmissionOutcome(
            symbol=symbol,
            result=AdmissionResult.REJECTED_INVALID_CANDIDATE,
            observation_id=None,
            batch_hash=None,
            reason=reason,
            applied=False,
            detail={"message": message, **dict(detail)},
        )

    try:
        rows, excluded = normalize_candidate(
            frame,
            symbol,
            request_start=request_start,
            request_end=request_end,
            cutoff=cutoff,
        )
    except CandidateRejected as error:
        return rejected(error.reason, str(error), error.detail)

    probe = _market_connection(market_path, write=False)
    try:
        has_history = bool(
            probe.execute("SELECT 1 FROM prices WHERE symbol=? LIMIT 1", (symbol,)).fetchone()
        )
    finally:
        probe.close()
    if not rows and not has_history:
        return rejected("EMPTY_CANDIDATE", f"{symbol}: provider returned no completed sessions", {})

    digest = batch_hash(symbol, context, rows, price_unit=price_unit, volume_unit=volume_unit)
    observation_id = observation_identity(symbol, context, cutoff, digest)
    sessions = sorted(rows)

    # 1. Observation + fetch receipt are durable before any market work.
    observation_record: dict[str, object] = {
        "observation_id": observation_id,
        "batch_hash": digest,
        "run_id": run_id,
        "source": context.source,
        "endpoint": context.endpoint,
        "source_mode": context.source_mode,
        "package_name": context.package_name,
        "package_version": context.package_version,
        "normalization_version": NORMALIZATION_VERSION,
        "symbol": symbol,
        "interval": context.interval,
        "request_start": request_start,
        "request_end": request_end,
        "completed_session_cutoff": cutoff.isoformat(),
        "fetched_at_utc": iso_utc(context.fetched_at_utc or now_utc),
        "price_unit": price_unit,
        "volume_unit": volume_unit,
        "price_basis": PRICE_BASIS_UNKNOWN,
        "row_count": len(sessions),
        "first_session": sessions[0] if sessions else None,
        "last_session": sessions[-1] if sessions else None,
        "rows": [[session, *rows[session]] for session in sessions],
        "excluded": excluded,
    }
    persisted = log.persist_observation(observation_record)
    receipt_id = int(persisted["receipt_id"])

    def retry_outcome() -> AdmissionOutcome | None:
        receipt = log.application_result(observation_id)
        if receipt is None:
            return None
        return _applied_outcome(symbol, observation_id, digest, receipt, receipt_id=receipt_id)

    def stored_now() -> dict[str, tuple[str, ...]] | None:
        reader = _market_connection(market_path, write=False)
        try:
            return read_symbol_rows(reader, symbol)
        except StoredHistoryError:
            return None
        finally:
            reader.close()

    # 2. Terminal APPLIED is absorbing: a retry only adds a receipt/annotation,
    # but ONLY while the receipt still holds for the current stored basis. After
    # a reviewed rebase changed stored values (or left reviewed future sessions
    # uninserted), the identical content is re-admitted under a basis-bound
    # identity and goes through the normal overlap comparison.
    existing = retry_outcome()
    if existing is not None:
        if _receipt_matches_market(rows, stored_now()):
            log.append_event(
                observation_id=observation_id,
                receipt_id=receipt_id,
                symbol=symbol,
                event_type=EVENT_RETRY_RECEIPT,
                application_state=STATE_APPLIED,
                admission_result=existing.result.value,
                run_id=run_id,
                detail={"note": "observation already applied; existing receipt returned"},
            )
            return existing
        original_id = observation_id
        observation_id = readmission_identity(original_id, log.current_version_ref(symbol))
        log.append_event(
            observation_id=original_id,
            receipt_id=receipt_id,
            symbol=symbol,
            event_type=EVENT_RETRY_RECEIPT,
            application_state=STATE_APPLIED,
            admission_result=existing.result.value,
            run_id=run_id,
            detail={
                "note": "applied receipt no longer matches the current stored basis; content re-admitted",
                "readmission_observation_id": observation_id,
            },
        )
        persisted = log.persist_observation(
            {**observation_record, "observation_id": observation_id, "readmission_of": original_id}
        )
        receipt_id = int(persisted["receipt_id"])
        existing = retry_outcome()
        if existing is not None and _receipt_matches_market(rows, stored_now()):
            return existing

    # 3. Serialized section under the market write lock.
    connection = _market_connection(market_path, write=True)
    outcome: AdmissionOutcome | None = None
    comparison: OverlapComparison | None = None
    detail: dict[str, object] = {}
    intended: tuple[str, ...] = ()
    content_hash = ""
    row_count = 0
    last_session: str | None = None
    record: dict[str, object] | None = None
    decision_event_id: int | None = None
    bound_version_id: str | None = None
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            try:
                stored = read_symbol_rows(connection, symbol)
            except StoredHistoryError as error:
                _record_block(
                    log,
                    observation_id=observation_id,
                    receipt_id=receipt_id,
                    symbol=symbol,
                    result=AdmissionResult.BLOCKED_STORED_HISTORY_INVALID,
                    reason=str(error),
                    detail={"affected_sessions": [], "auto_resolvable": False},
                    run_id=run_id,
                )
                outcome = AdmissionOutcome(
                    symbol=symbol,
                    result=AdmissionResult.BLOCKED_STORED_HISTORY_INVALID,
                    observation_id=observation_id,
                    batch_hash=digest,
                    reason=str(error),
                    applied=False,
                    receipt_id=receipt_id,
                )
                raise _Done()

            ambiguous = reconcile_open_intents(log, symbol, stored, run_id=run_id)
            if ambiguous is not None:
                _record_block(
                    log,
                    observation_id=observation_id,
                    receipt_id=receipt_id,
                    symbol=symbol,
                    result=AdmissionResult.BLOCKED_DATASET_MISMATCH,
                    reason=ambiguous[1],
                    detail={
                        "affected_sessions": [],
                        "auto_resolvable": False,
                        "ambiguous_intent_observation": ambiguous[0],
                    },
                    run_id=run_id,
                )
                outcome = AdmissionOutcome(
                    symbol=symbol,
                    result=AdmissionResult.BLOCKED_DATASET_MISMATCH,
                    observation_id=observation_id,
                    batch_hash=digest,
                    reason=ambiguous[1],
                    applied=False,
                    receipt_id=receipt_id,
                )
                raise _Done()

            recovered = retry_outcome()
            if recovered is not None:  # this very observation was applied by recovery
                if not _receipt_matches_market(rows, stored):
                    # Its receipt was invalidated by a basis change between the
                    # pre-check and the lock: nothing is written; the next attempt
                    # re-admits the content against the new basis.
                    outcome = AdmissionOutcome(
                        symbol=symbol,
                        result=AdmissionResult.FAILED_APPLICATION,
                        observation_id=observation_id,
                        batch_hash=digest,
                        reason="APPLIED_RECEIPT_INVALIDATED_CONCURRENTLY",
                        applied=False,
                        receipt_id=receipt_id,
                    )
                    raise _Done()
                outcome = recovered
                raise _Done()

            _supersede_stale_pending(
                log, symbol, keep_observation_id=observation_id, run_id=run_id
            )

            issues = log.verify_symbol(symbol, stored)
            if issues:
                reason = f"{symbol}: stored history does not match its provenance: {', '.join(issues)}"
                _record_block(
                    log,
                    observation_id=observation_id,
                    receipt_id=receipt_id,
                    symbol=symbol,
                    result=AdmissionResult.BLOCKED_DATASET_MISMATCH,
                    reason=reason,
                    detail={"affected_sessions": [], "auto_resolvable": False, "issues": issues},
                    run_id=run_id,
                )
                outcome = AdmissionOutcome(
                    symbol=symbol,
                    result=AdmissionResult.BLOCKED_DATASET_MISMATCH,
                    observation_id=observation_id,
                    batch_hash=digest,
                    reason=reason,
                    applied=False,
                    receipt_id=receipt_id,
                )
                raise _Done()

            comparison = classify_overlap(
                stored, rows, request_start=request_start, request_end=request_end
            )
            detail = comparison.to_detail()
            detail["request_start"] = request_start
            detail["request_end"] = request_end
            detail["completed_session_cutoff"] = cutoff.isoformat()
            detail["excluded_after_cutoff"] = excluded

            if comparison.result.blocked:
                _record_block(
                    log,
                    observation_id=observation_id,
                    receipt_id=receipt_id,
                    symbol=symbol,
                    result=comparison.result,
                    reason=comparison.reason,
                    detail={
                        **detail,
                        "stored_window_sha256": comparison.stored_window_sha256,
                        "candidate_window_sha256": comparison.candidate_window_sha256,
                    },
                    run_id=run_id,
                )
                outcome = AdmissionOutcome(
                    symbol=symbol,
                    result=comparison.result,
                    observation_id=observation_id,
                    batch_hash=digest,
                    reason=comparison.reason,
                    applied=False,
                    detail=detail,
                    receipt_id=receipt_id,
                )
                raise _Done()

            # Serialization point: everything the later finalization may rely on
            # is captured here, under the market write lock, and carried with it:
            # the decision's identity (event order) and the dataset version that
            # is current for this symbol (reconcile above has settled any earlier
            # application, so the version cannot move until this lock is released).
            bound_version_id = log.current_version_ref(symbol)
            decision_event_id = log.append_event(
                observation_id=observation_id,
                receipt_id=receipt_id,
                symbol=symbol,
                event_type=EVENT_ADMISSION_DECISION,
                application_state=STATE_ADMITTED,
                admission_result=comparison.result.value,
                run_id=run_id,
                detail=detail,
            )
            content_hash = rows_content_sha256(symbol, stored)
            row_count = len(stored)
            last_session = max(stored) if stored else None
            if comparison.new_sessions:
                intended = comparison.new_sessions
                # Ownership: durable *before* the market change, under the lock.
                log.record_intent(
                    observation_id=observation_id,
                    symbol=symbol,
                    sessions=intended,
                    rows_sha256=rows_content_sha256(
                        symbol, {s: rows[s] for s in intended}
                    ),
                    run_id=run_id,
                    receipt_id=receipt_id,
                )
                try:
                    _insert_rows(connection, symbol, rows, intended)
                    after = read_symbol_rows(connection, symbol)
                except Exception as error:  # a hard crash (BaseException) deliberately escapes
                    connection.execute("ROLLBACK")
                    log.append_event(
                        observation_id=observation_id,
                        receipt_id=receipt_id,
                        symbol=symbol,
                        event_type=EVENT_INTENT_ABANDONED,
                        application_state=STATE_FAILED,
                        admission_result=AdmissionResult.FAILED_APPLICATION.value,
                        run_id=run_id,
                        detail={
                            "error_type": type(error).__name__,
                            "error": str(error),
                            "market_db_modified": False,
                            "sessions": list(intended),
                        },
                    )
                    return AdmissionOutcome(
                        symbol=symbol,
                        result=AdmissionResult.FAILED_APPLICATION,
                        observation_id=observation_id,
                        batch_hash=digest,
                        reason=type(error).__name__,
                        applied=False,
                        detail=detail,
                        error=f"{type(error).__name__}: {error}",
                        receipt_id=receipt_id,
                    )
                connection.execute("COMMIT")
                content_hash = rows_content_sha256(symbol, after)
                row_count = len(after)
                last_session = max(after) if after else None
            else:
                # No-op: nothing is written to market.db, so the receipt, the
                # version binding and the eligible block resolutions are all
                # finalized *inside* the serialization boundary that made the
                # decision. No other admission for this market can run between
                # the decision and its finalization.
                record = log.record_application(
                    observation_id=observation_id,
                    symbol=symbol,
                    admission_result=comparison.result.value,
                    appended_sessions=(),
                    row_count=row_count,
                    last_session=last_session,
                    content_sha256=content_hash,
                    run_id=run_id,
                    receipt_id=receipt_id,
                    decision_event_id=decision_event_id,
                    bound_version_id=bound_version_id,
                    confirmed_sessions=comparison.confirmed_sessions,
                )
                connection.execute("ROLLBACK")  # release the lock
        except _Done:
            connection.execute("ROLLBACK")
        except BaseException:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:  # pragma: no cover - already closed/rolled back
                pass
            raise
    finally:
        connection.close()

    if outcome is not None:
        return outcome
    assert comparison is not None

    # 4. Appends only (a no-op was finalized under the lock above). The market
    # change is committed; record the application with the binding captured at
    # the decision (idempotent; reconciled by the next admission if this process
    # dies right here). Ownership comes from the open intent, and the version
    # must still be the one bound at the decision.
    if record is None:
        record = log.record_application(
            observation_id=observation_id,
            symbol=symbol,
            admission_result=comparison.result.value,
            appended_sessions=intended,
            row_count=row_count,
            last_session=last_session,
            content_sha256=content_hash,
            run_id=run_id,
            receipt_id=receipt_id,
            decision_event_id=decision_event_id,
            bound_version_id=bound_version_id,
            confirmed_sessions=comparison.confirmed_sessions,
        )
    return AdmissionOutcome(
        symbol=symbol,
        result=comparison.result,
        observation_id=observation_id,
        batch_hash=digest,
        reason=comparison.reason,
        applied=True,
        appended_sessions=tuple(record["appended_sessions"]),
        version_id=str(record["version_id"]),
        detail=detail,
        receipt_id=receipt_id,
        already_applied=bool(record.get("already_applied", False)),
    )
