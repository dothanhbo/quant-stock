"""Reviewed per-symbol rebase (V1 P1-OPS-1).

The revision admission guard (:mod:`core.market_admission`) blocks every
retrospective change of stored history. KBS back-adjusts history after
corporate actions, so after one legitimate corporate action the same revised
history is proposed again on every run and the symbol stays blocked forever.

This module is the ONE sanctioned exception to "stored history is never
rewritten". It is never called by ingestion. It lets an operator who has
reviewed one unresolved ``BLOCKED_REVISION_CHANGED`` block transition that
symbol's stored values to exactly the values of the blocked observation, which
are already persisted (immutably) in the observation log:

    unresolved revision block (exact block id + observation id + symbol)
      -> eligibility (pure value revision covering the symbol's ENTIRE stored
         history, one contiguous changed prefix, unchanged pre-rebase basis,
         latest block, no open application, no open paper exposure in any
         mandatory or supplemental paper store)
      -> [market write lock] durable REBASE intent that atomically claims the
         block (ordinary review is refused until finalized or abandoned)
      -> UPDATE only the reviewed changed sessions of that symbol
      -> verify the whole symbol equals the intended post-rebase rows -> COMMIT
      -> finalize in the log: one new symbol version, rebase record, session
         attribution (origin REBASED_HISTORY), block resolution.

The system does not prove that the revision is a legitimate corporate action;
the operator asserts it (``category`` + reviewer + reason). Nothing is fetched
from the provider here. Existing evidence is never rebound: replaced sessions
change their provenance (origin / observation / version), so R3 bindings that
consumed them become ``QUARANTINED_INCOMPATIBLE_BASIS``.

Crash model (two SQLite files, same ordering as admission): the intent is
durable before the market change. A crash before the market commit leaves the
old rows and an open intent that is abandoned on the next attempt; a crash
after the commit leaves new rows plus an open intent, which keeps every
consumer gate closed (pending application + version-content mismatch +
unresolved block) until the next rebase attempt or the next admission for the
symbol finalizes it.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from core.market_observation_log import (
    EVENT_ADMISSION_DECISION,
    EVENT_INTENT_ABANDONED,
    INTENT_KIND_REBASE,
    STATE_FAILED,
    ObservationLog,
    ObservationLogError,
    StoredHistoryError,
    open_observation_log,
    read_symbol_rows,
    rows_content_sha256,
)
from core.paths import resolve_market_database_path

#: operator-asserted reason categories accepted for a rebase.
REBASE_CATEGORIES = frozenset({"CORPORATE_ACTION_REBASE"})

#: V1 accepts NO partial basis seam: recursive full-history consumers (EMA/RSI/
#: ATR/ADX with ``adjust=False``, breadth, sizing) change materially however old
#: the seam is. The reviewed observation must therefore cover every stored
#: session of the symbol (``INCOMPLETE_REBASE_HISTORY_COVERAGE`` otherwise), and
#: the changed sessions must be one contiguous prefix of the stored history
#: (``REBASE_NONCONTIGUOUS_BASIS_CHANGE`` otherwise). No lookback threshold.
HISTORY_COVERAGE_FULL = "FULL_STORED_HISTORY"

REVISION_CHANGED = "BLOCKED_REVISION_CHANGED"


class RebaseRefused(RuntimeError):
    """A precondition failed; nothing was changed."""

    def __init__(self, code: str, message: str, detail: Mapping[str, object] | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.detail = dict(detail or {})


@dataclass(frozen=True, slots=True)
class RebasePlan:
    symbol: str
    block_id: int
    observation_id: str
    request_start: str
    request_end: str
    sessions: tuple[str, ...]
    old_rows: Mapping[str, tuple[str, ...]]
    new_rows: Mapping[str, tuple[str, ...]]
    old_rows_sha256: str
    new_rows_sha256: str
    pre_symbol_content_sha256: str
    post_symbol_content_sha256: str
    row_count: int
    last_session: str | None
    stored_first_session: str
    stored_last_session: str
    supersedes_block_ids: tuple[int, ...]
    pre_version_id: str
    pre_dataset_version_id: str
    open_position_check: Mapping[str, object] = field(default_factory=dict)

    def summary(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "block_id": self.block_id,
            "observation_id": self.observation_id,
            "request_window": [self.request_start, self.request_end],
            "rebased_session_count": len(self.sessions),
            "first_rebased_session": self.sessions[0],
            "last_rebased_session": self.sessions[-1],
            "old_rows_sha256": self.old_rows_sha256,
            "new_rows_sha256": self.new_rows_sha256,
            "pre_symbol_content_sha256": self.pre_symbol_content_sha256,
            "post_symbol_content_sha256": self.post_symbol_content_sha256,
            "history_coverage": HISTORY_COVERAGE_FULL,
            "stored_history": [self.stored_first_session, self.stored_last_session],
            "supersedes_block_ids": list(self.supersedes_block_ids),
            "pre_version_id": self.pre_version_id,
            "pre_dataset_version_id": self.pre_dataset_version_id,
            "open_position_check": dict(self.open_position_check),
            "sample": [
                {"session": s, "stored": list(self.old_rows[s]), "rebased": list(self.new_rows[s])}
                for s in self.sessions[:5]
            ],
        }


@dataclass(frozen=True, slots=True)
class RebaseOutcome:
    symbol: str
    block_id: int
    applied: bool
    already_applied: bool
    rebase: Mapping[str, object]
    recovered: bool = False


# ------------------------------------------------------------ paper exposure
def paper_store_coverage(
    supplemental: Iterable[str | Path] = (),
    *,
    environ: Mapping[str, str] | None = None,
    root: str | Path | None = None,
) -> list[dict[str, object]]:
    """The paper stores a rebase must check: mandatory canonical stores UNION supplemental.

    Mandatory = every store in ``config.paper_store.KNOWN_PAPER_STORES``,
    resolved exactly like the pipelines (process environment over the project
    ``.env``). A mandatory store is ``required`` (must exist and be readable)
    when it is the active store or its path variable is configured; an
    unconfigured, inactive default that does not exist cannot hold exposure and
    is recorded as absent. Operator-supplied ``--paper-db`` paths are
    supplemental only and always required: a typo refuses, it is never skipped
    and it never replaces a mandatory store. No filesystem scanning.
    """
    from config.paper_store import (
        KNOWN_PAPER_STORES,
        PROJECT_ROOT,
        configured_paper_environment,
        resolve_active_paper_store,
        resolve_store_definition,
    )

    base = PROJECT_ROOT if root is None else Path(root)
    env = dict(configured_paper_environment(root=base) if environ is None else environ)
    try:
        active = resolve_active_paper_store(env, root=base).database_path
    except Exception as error:  # noqa: BLE001 - unresolvable routing is fail closed
        raise RebaseRefused("PAPER_STORE_UNRESOLVED", f"cannot resolve the active paper store: {error}") from error
    entries: dict[Path, dict[str, object]] = {}
    for definition in KNOWN_PAPER_STORES:
        try:
            path = resolve_store_definition(definition, env, root=base).database_path
        except Exception as error:  # noqa: BLE001
            raise RebaseRefused(
                "PAPER_STORE_UNRESOLVED", f"cannot resolve paper store {definition.store_id}: {error}"
            ) from error
        configured = bool(str(env.get(definition.override_variable, "")).strip())
        is_active = Path(path) == Path(active)
        entry = entries.setdefault(
            Path(path),
            {"path": str(path), "roles": [], "store_ids": [], "active": False, "configured": False, "required": False},
        )
        entry["roles"].append("MANDATORY")  # type: ignore[union-attr]
        entry["store_ids"].append(definition.store_id)  # type: ignore[union-attr]
        entry["active"] = bool(entry["active"]) or is_active
        entry["configured"] = bool(entry["configured"]) or configured
        entry["required"] = bool(entry["required"]) or is_active or configured
    for item in supplemental:
        path = Path(item).expanduser().resolve()
        entry = entries.setdefault(
            path,
            {"path": str(path), "roles": [], "store_ids": [], "active": False, "configured": False, "required": False},
        )
        entry["roles"].append("SUPPLEMENTAL")  # type: ignore[union-attr]
        entry["required"] = True
    return list(entries.values())


def paper_exposure(
    symbol: str,
    supplemental: Iterable[str | Path] = (),
    *,
    environ: Mapping[str, str] | None = None,
    root: str | Path | None = None,
) -> dict[str, object]:
    """Read-only scan of mandatory + supplemental paper stores for exposure to ``symbol``.

    An open position or a pending (not yet executed) signal carries an entry,
    stop or target computed on the current price basis; a historical rebase is
    no proof of how the corporate action treats that position economically, so
    any such exposure refuses the rebase (V1 builds no corporate-action
    portfolio engine). A required store that is missing, unreadable or not a
    paper store also refuses (fail closed).
    """
    symbol = str(symbol).strip().upper()
    coverage = paper_store_coverage(supplemental, environ=environ, root=root)
    checked: list[dict[str, object]] = []
    positions: list[dict[str, object]] = []
    pending: list[dict[str, object]] = []
    for entry in coverage:
        path = Path(str(entry["path"]))
        record = {k: entry[k] for k in ("path", "roles", "store_ids", "active", "configured", "required")}
        if not path.is_file():
            if entry["required"]:
                raise RebaseRefused(
                    "PAPER_STORE_MISSING",
                    f"required paper store {path} ({'/'.join(entry['roles'])}) does not exist; refusing "  # type: ignore[arg-type]
                    "(fail closed: an active, configured or operator-named store must be readable)",
                    {"store": record},
                )
            checked.append({**record, "state": "ABSENT_INACTIVE_UNCONFIGURED"})
            continue
        try:
            connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
            try:
                tables = {str(r[0]) for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "paper_positions" not in tables:
                    raise RebaseRefused(
                        "PAPER_STORE_NOT_A_PAPER_STORE",
                        f"{path} has no paper_positions table; refusing (wrong path?)",
                        {"store": record},
                    )
                for row in connection.execute(
                    "SELECT symbol, quantity FROM paper_positions WHERE UPPER(TRIM(symbol))=? AND quantity > 0",
                    (symbol,),
                ):
                    positions.append({"store": str(path), "symbol": str(row[0]), "quantity": row[1]})
                if "paper_pending_signals" in tables:
                    for row in connection.execute(
                        "SELECT id, signal_date, status FROM paper_pending_signals "
                        "WHERE UPPER(TRIM(symbol))=? AND status='PENDING'",
                        (symbol,),
                    ):
                        pending.append({"store": str(path), "id": row[0], "signal_date": row[1]})
            finally:
                connection.close()
        except sqlite3.Error as error:
            raise RebaseRefused(
                "PAPER_STORE_UNREADABLE",
                f"cannot read paper store {path}: {error}; refusing (fail closed)",
                {"store": record},
            ) from error
        checked.append({**record, "state": "CHECKED"})
    return {"checked_stores": checked, "open_positions": positions, "pending_signals": pending}


def _proposal_compatible_with_basis(
    rows: Mapping[str, tuple[str, ...]],
    request_start: str,
    request_end: str,
    post: Mapping[str, tuple[str, ...]],
) -> bool:
    """Is an older persisted proposal COMPLETELY compatible with the resulting basis?

    Relevant proposal = every row of the older observation plus its request
    window. Compatible only if (1) each proposed session up to the latest
    resulting stored session is stored with exactly the proposed values (a
    conflict or an unstored session inside the history is incompatible) and (2)
    no resulting stored session inside the proposal's request window is missing
    from the proposal. Proposed sessions after the latest stored session are
    future sessions: they neither make the proposal incompatible nor count as
    applied (closing a block never attributes or inserts anything).
    """
    if not post:
        return False
    latest = max(post)
    for session, values in rows.items():
        if session <= latest and post.get(session) != values:
            return False
    return all(session in rows for session in post if request_start <= session <= request_end)


# --------------------------------------------------------------- eligibility
def _block_event(log: ObservationLog, block_id: int) -> dict[str, object] | None:
    rows = log._read(  # noqa: SLF001 - same package, read-only
        "SELECT event_id, observation_id, symbol, admission_result, application_state, event_type, "
        "detail_json FROM admission_events WHERE event_id=?",
        (int(block_id),),
    )
    if not rows:
        return None
    record = dict(rows[0])
    record["detail"] = json.loads(record.pop("detail_json"))
    return record


def _observation_rows(log: ObservationLog, observation_id: str) -> dict[str, tuple[str, ...]]:
    observation = log.get_observation(observation_id)
    if observation is None:
        raise RebaseRefused("OBSERVATION_NOT_FOUND", f"observation {observation_id} is not in the log")
    return {str(item[0]): tuple(str(v) for v in item[1:]) for item in observation["rows"]}


def plan_rebase(
    log: ObservationLog,
    stored: Mapping[str, tuple[str, ...]],
    *,
    symbol: str,
    block_id: int,
    observation_id: str,
    paper_paths: Iterable[str | Path] = (),
    paper_environ: Mapping[str, str] | None = None,
    paper_root: str | Path | None = None,
) -> RebasePlan:
    """Evaluate every precondition against ``stored`` (no mutation). Raises RebaseRefused.

    ``paper_paths`` are SUPPLEMENTAL stores; the mandatory canonical stores are
    always checked (``paper_environ`` / ``paper_root`` exist for tests only).
    """
    from core.market_admission import classify_overlap  # lazy: admission imports this module lazily

    symbol = str(symbol).strip().upper()
    event = _block_event(log, block_id)
    if (
        event is None
        or event["event_type"] != EVENT_ADMISSION_DECISION
        or event["application_state"] != "blocked"
    ):
        raise RebaseRefused("NO_SUCH_BLOCK", f"{block_id} is not a recorded admission block")
    if str(event["symbol"]) != symbol:
        raise RebaseRefused("BLOCK_SYMBOL_MISMATCH", f"block {block_id} is on {event['symbol']}, not {symbol}")
    if str(event["observation_id"]) != str(observation_id):
        raise RebaseRefused(
            "BLOCK_OBSERVATION_MISMATCH",
            f"block {block_id} belongs to observation {event['observation_id']}, not {observation_id}",
        )
    unresolved = log.unresolved_blocks([symbol])
    if not any(item["block_id"] == int(block_id) for item in unresolved):
        raise RebaseRefused("BLOCK_NOT_UNRESOLVED", f"block {block_id} is already resolved")
    if event["admission_result"] != REVISION_CHANGED:
        raise RebaseRefused(
            "REBASE_REQUIRES_REVISION_BLOCK",
            f"block {block_id} is {event['admission_result']}; only {REVISION_CHANGED} can be rebased",
        )
    detail = event["detail"]
    if not detail.get("auto_resolvable", True):
        raise RebaseRefused("REBASE_REQUIRES_REVISION_BLOCK", f"block {block_id} is not a plain revision block")
    newer = [item["block_id"] for item in unresolved if item["block_id"] > int(block_id)]
    if newer:
        raise RebaseRefused(
            "BLOCK_NOT_LATEST",
            f"newer unresolved blocks exist on {symbol}: {newer}; review the latest revision",
        )
    intents = log.open_intents(symbol)
    if intents:
        raise RebaseRefused(
            "PENDING_APPLICATION_ON_SYMBOL",
            f"{symbol} has an open market-data application ({[i['observation_id'] for i in intents]})",
        )
    issues = log.verify_symbol(symbol, stored)
    if issues:
        raise RebaseRefused("PRE_REBASE_BASIS_DRIFT", f"{symbol} stored history fails verification: {issues}")

    request_start = str(detail["request_start"])
    request_end = str(detail["request_end"])
    window = {s: v for s, v in stored.items() if request_start <= s <= request_end}
    if rows_content_sha256("w", {s: window[s] for s in sorted(window)}) != detail.get("stored_window_sha256"):
        raise RebaseRefused(
            "PRE_REBASE_BASIS_CHANGED",
            f"{symbol} stored rows in {request_start}..{request_end} differ from the basis the block reviewed",
        )
    stored_sessions = sorted(stored)
    if not stored_sessions:
        raise RebaseRefused("INCOMPLETE_REBASE_HISTORY_COVERAGE", f"{symbol} has no stored history to rebase")
    uncovered = [s for s in stored_sessions if s < request_start or s > request_end]
    if uncovered:
        raise RebaseRefused(
            "INCOMPLETE_REBASE_HISTORY_COVERAGE",
            f"{symbol}: the reviewed observation covers {request_start}..{request_end} but the stored history is "
            f"{stored_sessions[0]}..{stored_sessions[-1]}; {len(uncovered)} stored sessions "
            f"({uncovered[0]}..{uncovered[-1]}) would stay on the previous basis. V1 accepts no partial basis "
            "seam: record and review an observation covering the whole stored history first",
            {"uncovered_sessions": len(uncovered), "first_uncovered": uncovered[0], "last_uncovered": uncovered[-1]},
        )
    candidate = _observation_rows(log, str(observation_id))
    comparison = classify_overlap(stored, candidate, request_start=request_start, request_end=request_end)
    if comparison.result.value != REVISION_CHANGED or comparison.missing_from_candidate or comparison.unstored_inside_history:
        raise RebaseRefused(
            "REBASE_REQUIRES_PURE_VALUE_REVISION",
            f"{symbol}: the observation is not a pure value revision of stored sessions "
            f"(missing={len(comparison.missing_from_candidate)}, "
            f"unstored={len(comparison.unstored_inside_history)}, result={comparison.result.value})",
        )
    sessions = tuple(sorted(item[0] for item in comparison.changed))
    if list(sessions) != sorted(str(s) for s in detail.get("affected_sessions", [])):
        raise RebaseRefused(
            "REBASE_SESSIONS_DIFFER_FROM_REVIEWED_BLOCK",
            f"{symbol}: changed sessions now differ from the sessions the block recorded",
        )
    if list(sessions) != stored_sessions[: len(sessions)]:
        kept = [s for s in stored_sessions if s < sessions[-1] and s not in set(sessions)]
        raise RebaseRefused(
            "REBASE_NONCONTIGUOUS_BASIS_CHANGE",
            f"{symbol}: the changed sessions are not one contiguous prefix of the stored history; "
            f"{len(kept)} unchanged sessions ({kept[0]}..{kept[-1]}) lie between changed ones and would be "
            "an old-basis segment inside the new basis. Not a single back-adjustment: review it, do not rebase",
            {"unchanged_inside_changed_range": len(kept)},
        )
    exposure = paper_exposure(symbol, paper_paths, environ=paper_environ, root=paper_root)
    if exposure["open_positions"]:
        raise RebaseRefused(
            "OPEN_POSITION_REQUIRES_CORPORATE_ACTION_RECONCILIATION",
            f"{symbol} has an open paper position; quantity/cost basis treatment of the corporate action "
            "is unproven, rebase refused",
            exposure,
        )
    if exposure["pending_signals"]:
        raise RebaseRefused(
            "PENDING_SIGNAL_REQUIRES_CORPORATE_ACTION_RECONCILIATION",
            f"{symbol} has a pending paper signal whose entry/stop levels are on the current basis",
            exposure,
        )
    post = dict(stored)
    for session in sessions:
        post[session] = candidate[session]
    supersedes: list[int] = []
    for item in unresolved:
        if item["block_id"] >= int(block_id) or item["admission_result"] != REVISION_CHANGED:
            continue
        if not item["auto_resolvable"] or not item["affected_sessions"]:
            continue
        older = log.get_observation(str(item["observation_id"]))
        if older is None:  # pragma: no cover - defensive
            continue
        older_rows = {str(r[0]): tuple(str(v) for v in r[1:]) for r in older["rows"]}
        if _proposal_compatible_with_basis(
            older_rows, str(older["request_start"]), str(older["request_end"]), post
        ):
            supersedes.append(int(item["block_id"]))
    old_rows = {s: stored[s] for s in sessions}
    new_rows = {s: candidate[s] for s in sessions}
    return RebasePlan(
        symbol=symbol,
        block_id=int(block_id),
        observation_id=str(observation_id),
        request_start=request_start,
        request_end=request_end,
        sessions=sessions,
        old_rows=old_rows,
        new_rows=new_rows,
        old_rows_sha256=rows_content_sha256(symbol, old_rows),
        new_rows_sha256=rows_content_sha256(symbol, new_rows),
        pre_symbol_content_sha256=rows_content_sha256(symbol, stored),
        post_symbol_content_sha256=rows_content_sha256(symbol, post),
        row_count=len(post),
        last_session=max(post) if post else None,
        stored_first_session=stored_sessions[0],
        stored_last_session=stored_sessions[-1],
        supersedes_block_ids=tuple(supersedes),
        pre_version_id=log.current_version_ref(symbol),
        pre_dataset_version_id=str(log.dataset_version_identity()["dataset_version_id"]),
        open_position_check=exposure,
    )


# -------------------------------------------------------------- market I/O
def _market_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True, timeout=30, isolation_level=None)


def _update_rows(connection: sqlite3.Connection, symbol: str, rows: Mapping[str, Sequence[str]]) -> None:
    """The single sanctioned historical rewrite: exactly one stored row per reviewed session."""
    for session in sorted(rows):
        ids = [
            int(r[0])
            for r in connection.execute(
                "SELECT id FROM prices WHERE symbol=? AND substr(time,1,10)=?", (symbol, session)
            )
        ]
        if len(ids) != 1:
            raise ObservationLogError(f"{symbol}@{session}: expected exactly one stored row, found {len(ids)}")
        o, h, l, c, v = rows[session]
        cursor = connection.execute(
            "UPDATE prices SET open=?, high=?, low=?, close=?, volume=? WHERE id=?",
            (float(o), float(h), float(l), float(c), int(v), ids[0]),
        )
        if cursor.rowcount != 1:  # pragma: no cover - defensive
            raise ObservationLogError(f"{symbol}@{session}: update touched {cursor.rowcount} rows")


# ------------------------------------------------------------ reconciliation
def reconcile_rebase_intent(
    log: ObservationLog,
    intent: Mapping[str, object],
    stored: Mapping[str, tuple[str, ...]],
    *,
    run_id: str | None = None,
) -> str:
    """Settle an open REBASE intent under the market write lock.

    * the symbol holds exactly the intended post-rebase rows -> finalize (``FINALIZED``);
    * the reviewed sessions still hold their old rows -> the market change never
      committed -> abandon the intent (``ABANDONED``);
    * anything else -> ``AMBIGUOUS`` (the caller fails closed).
    """
    detail = dict(intent["detail"])  # type: ignore[arg-type]
    symbol = str(intent["symbol"])
    sessions = [str(s) for s in detail["sessions"]]
    if rows_content_sha256(symbol, stored) == detail["post_symbol_content_sha256"]:
        log.record_rebase(intent_event_id=int(intent["event_id"]), run_id=run_id)  # type: ignore[arg-type]
        return "FINALIZED"
    current = {s: stored[s] for s in sessions if s in stored}
    if (
        len(current) == len(sessions)
        and rows_content_sha256(symbol, current) == detail["old_rows_sha256"]
        and rows_content_sha256(symbol, stored) == detail["pre_symbol_content_sha256"]
    ):
        log.append_event(
            observation_id=str(intent["observation_id"]),
            symbol=symbol,
            event_type=EVENT_INTENT_ABANDONED,
            application_state=STATE_FAILED,
            admission_result="REBASE_INTENT_ABANDONED",
            run_id=run_id,
            detail={"reconciliation": "REBASE_ROWS_NOT_IN_MARKET", "intent_event_id": intent["event_id"]},
        )
        return "ABANDONED"
    return "AMBIGUOUS"


# ------------------------------------------------------------------ command
def rebase_symbol(
    market_db_path: str | Path | None = None,
    *,
    symbol: str,
    block_id: int,
    observation_id: str,
    category: str,
    reviewer: str,
    reason: str,
    note: str | None = None,
    paper_paths: Iterable[str | Path] = (),
    paper_environ: Mapping[str, str] | None = None,
    paper_root: str | Path | None = None,
    log_path: str | Path | None = None,
    run_id: str | None = None,
) -> RebaseOutcome:
    """Apply one reviewed per-symbol rebase (see module docstring).

    ``paper_paths`` are supplemental paper stores, checked IN ADDITION to the
    mandatory canonical stores.
    """
    symbol = str(symbol).strip().upper()
    reviewer, reason = str(reviewer).strip(), str(reason).strip()
    category = str(category).strip().upper()
    if category not in REBASE_CATEGORIES:
        raise RebaseRefused("INVALID_CATEGORY", f"category must be one of {sorted(REBASE_CATEGORIES)}")
    if not reviewer or not reason:
        raise RebaseRefused("REVIEW_REQUIRED", "a rebase requires a reviewer and a reason")
    market_path = resolve_market_database_path(market_db_path)
    log = open_observation_log(market_path, log_path, require_baseline=True)
    paper_paths = tuple(paper_paths or ())

    def finished(record: Mapping[str, object], *, recovered: bool = False) -> RebaseOutcome:
        if str(record["symbol"]) != symbol or str(record["observation_id"]) != str(observation_id):
            raise RebaseRefused(
                "BLOCK_ALREADY_REBASED_DIFFERENTLY",
                f"block {block_id} was rebased for {record['symbol']} / {record['observation_id']}",
            )
        return RebaseOutcome(symbol, int(block_id), True, not recovered, record, recovered=recovered)

    existing = log.rebase_for_block(int(block_id))
    if existing is not None:
        return finished(existing)

    connection = _market_connection(market_path)
    plan: RebasePlan | None = None
    intent_event_id: int | None = None
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            stored = read_symbol_rows(connection, symbol)
            for intent in log.open_intents(symbol):
                if intent["kind"] != INTENT_KIND_REBASE:
                    continue
                status = reconcile_rebase_intent(log, intent, stored, run_id=run_id)
                if status == "AMBIGUOUS":
                    raise RebaseRefused(
                        "AMBIGUOUS_REBASE_STATE",
                        f"{symbol}: an earlier rebase intent is only partially present in the market",
                    )
            recovered = log.rebase_for_block(int(block_id))
            if recovered is not None:
                connection.execute("ROLLBACK")
                return finished(recovered, recovered=True)
            plan = plan_rebase(
                log,
                stored,
                symbol=symbol,
                block_id=int(block_id),
                observation_id=str(observation_id),
                paper_paths=paper_paths,
                paper_environ=paper_environ,
                paper_root=paper_root,
            )
            # Ownership: durable BEFORE the market change, under the market lock, and
            # atomic with a log-side re-check that the block is still unresolved. From
            # here on ordinary review of this block is refused (BLOCK_OWNED_BY_PENDING_REBASE).
            try:
                intent_event_id = log.record_rebase_intent(
                    observation_id=plan.observation_id,
                    symbol=symbol,
                    block_event_id=plan.block_id,
                    run_id=run_id,
                    detail={
                        "sessions": list(plan.sessions),
                        "rows_sha256": plan.new_rows_sha256,
                        "old_rows_sha256": plan.old_rows_sha256,
                        "old_rows": {s: list(v) for s, v in plan.old_rows.items()},
                        "category": category,
                        "reviewer": reviewer,
                        "reason": reason,
                        "note": note,
                        "request_start": plan.request_start,
                        "request_end": plan.request_end,
                        "pre_version_id": plan.pre_version_id,
                        "pre_dataset_version_id": plan.pre_dataset_version_id,
                        "pre_symbol_content_sha256": plan.pre_symbol_content_sha256,
                        "post_symbol_content_sha256": plan.post_symbol_content_sha256,
                        "row_count": plan.row_count,
                        "last_session": plan.last_session,
                        "history_coverage": HISTORY_COVERAGE_FULL,
                        "supersedes_block_ids": list(plan.supersedes_block_ids),
                        "open_position_check": dict(plan.open_position_check),
                    },
                )
            except ObservationLogError as error:
                raise RebaseRefused("BLOCK_NOT_UNRESOLVED", f"{error}; nothing applied") from error
            try:
                _update_rows(connection, symbol, plan.new_rows)
                after = read_symbol_rows(connection, symbol)
                if rows_content_sha256(symbol, after) != plan.post_symbol_content_sha256:
                    raise ObservationLogError(f"{symbol}: post-rebase content differs from the reviewed plan")
            except (Exception,) as error:  # a hard crash (BaseException) escapes and stays recoverable
                connection.execute("ROLLBACK")
                log.append_event(
                    observation_id=plan.observation_id,
                    symbol=symbol,
                    event_type=EVENT_INTENT_ABANDONED,
                    application_state=STATE_FAILED,
                    admission_result="REBASE_INTENT_ABANDONED",
                    run_id=run_id,
                    detail={
                        "reconciliation": "REBASE_FAILED_BEFORE_MARKET_COMMIT",
                        "intent_event_id": intent_event_id,
                        "error": f"{type(error).__name__}: {error}",
                        "market_db_modified": False,
                    },
                )
                raise RebaseRefused(
                    "REBASE_APPLICATION_FAILED", f"{type(error).__name__}: {error}; nothing applied"
                ) from error
            connection.execute("COMMIT")
        except RebaseRefused:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()

    assert plan is not None and intent_event_id is not None
    record = log.record_rebase(intent_event_id=intent_event_id, run_id=run_id)
    return RebaseOutcome(symbol, int(block_id), True, bool(record.get("already_applied")), record)


def recover_rebase(
    market_db_path: str | Path | None = None,
    *,
    symbol: str,
    log_path: str | Path | None = None,
    run_id: str | None = None,
) -> list[dict[str, object]]:
    """Explicitly settle open reviewed-rebase intents of ``symbol`` (no new rebase).

    Under the market write lock: an intent whose post-rebase rows are in the
    market is finalized; one whose old basis is fully intact is abandoned
    (afterwards ordinary review of the block is possible again); anything else
    is ``AMBIGUOUS_REBASE_STATE`` and nothing changes. The next admission of
    the symbol performs the same settlement automatically.
    """
    symbol = str(symbol).strip().upper()
    market_path = resolve_market_database_path(market_db_path)
    log = open_observation_log(market_path, log_path, require_baseline=True)
    connection = _market_connection(market_path)
    results: list[dict[str, object]] = []
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            stored = read_symbol_rows(connection, symbol)
            for intent in log.open_intents(symbol):
                if intent["kind"] != INTENT_KIND_REBASE:
                    continue
                status = reconcile_rebase_intent(log, intent, stored, run_id=run_id)
                results.append(
                    {
                        "intent_event_id": intent["event_id"],
                        "block_event_id": intent["detail"].get("block_event_id"),  # type: ignore[union-attr]
                        "status": status,
                    }
                )
                if status == "AMBIGUOUS":
                    raise RebaseRefused(
                        "AMBIGUOUS_REBASE_STATE",
                        f"{symbol}: rebase intent {intent['event_id']} is only partially present in the market; "
                        "restore market.db and the observation log together from the pre-rebase backup",
                        {"results": results},
                    )
        finally:
            if connection.in_transaction:
                connection.execute("ROLLBACK")  # read-only on market.db: the lock is only held
    finally:
        connection.close()
    return results


def preview_rebase(
    market_db_path: str | Path | None = None,
    *,
    symbol: str,
    block_id: int,
    observation_id: str,
    paper_paths: Iterable[str | Path] = (),
    paper_environ: Mapping[str, str] | None = None,
    paper_root: str | Path | None = None,
    log_path: str | Path | None = None,
) -> RebasePlan:
    """Read-only dry run: the exact plan a rebase would apply now, or RebaseRefused."""
    market_path = resolve_market_database_path(market_db_path)
    log = open_observation_log(market_path, log_path, readonly=True, require_baseline=True)
    connection = sqlite3.connect(f"{market_path.as_uri()}?mode=ro", uri=True, timeout=30)
    try:
        try:
            stored = read_symbol_rows(connection, str(symbol).strip().upper())
        except StoredHistoryError as error:
            raise RebaseRefused("STORED_HISTORY_INVALID", str(error)) from error
    finally:
        connection.close()
    return plan_rebase(
        log,
        stored,
        symbol=symbol,
        block_id=int(block_id),
        observation_id=str(observation_id),
        paper_paths=tuple(paper_paths or ()),
        paper_environ=paper_environ,
        paper_root=paper_root,
    )


__all__ = (
    "HISTORY_COVERAGE_FULL",
    "REBASE_CATEGORIES",
    "RebaseOutcome",
    "RebasePlan",
    "RebaseRefused",
    "paper_exposure",
    "paper_store_coverage",
    "plan_rebase",
    "preview_rebase",
    "rebase_symbol",
    "reconcile_rebase_intent",
    "recover_rebase",
)
