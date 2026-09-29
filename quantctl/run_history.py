from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import json
import os
from pathlib import Path
import re
import sqlite3
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4

from quantctl.registry import PROJECT_ROOT, sqlite_read_only


HISTORY_PATH_ENV = "QUANT_OPERATION_HISTORY_PATH"
RUN_ID_ENV = "QUANT_OPERATION_RUN_ID"
DEFAULT_HISTORY_PATH = Path("data/operation_history.db")
_MAX_ERROR_LENGTH = 500
_MAX_CAPTURED_OUTPUT_LENGTH = 20_000
_TRUNCATION_MARKER = "\n[OUTPUT TRUNCATED]"
_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[^\s]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password|chat[_-]?id)\s*[=:]\s*)[^\s,;]+"),
)


class RunStatus(str, Enum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class HistoryReadError(RuntimeError):
    """Raised when an existing operation-history store cannot be read safely."""


@dataclass(frozen=True, slots=True)
class RunStep:
    run_id: str
    sequence: int
    step_name: str
    started_at_utc: str
    finished_at_utc: str
    status: RunStatus
    duration_ms: int
    exit_code: int | None = None
    error_message: str | None = None
    metadata: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class RunSummary:
    run_id: str
    operation: str
    started_at_utc: str
    finished_at_utc: str | None
    status: RunStatus
    exit_code: int | None
    duration_ms: int | None
    capabilities: tuple[str, ...]
    failed_step: str | None
    error_type: str | None
    error_message: str | None


@dataclass(frozen=True, slots=True)
class RunDetail:
    summary: RunSummary
    steps: tuple[RunStep, ...]
    metadata: Mapping[str, Any]


def history_path(*, root: Path = PROJECT_ROOT, path: Path | None = None) -> Path:
    configured = path or Path(os.environ.get(HISTORY_PATH_ENV, DEFAULT_HISTORY_PATH))
    return configured.resolve() if configured.is_absolute() else (root / configured).resolve()


def _redact_secrets(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"\1[REDACTED]", text)
    return text


def sanitize_diagnostic(value: object | None) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    text = _redact_secrets(text)
    return text[:_MAX_ERROR_LENGTH] or None


def sanitize_captured_output(value: object | None) -> str:
    """Redact and bound subprocess output before returning it to a UI or terminal."""
    if value is None:
        return ""
    text = _redact_secrets(str(value))
    if len(text) <= _MAX_CAPTURED_OUTPUT_LENGTH:
        return text
    retained = _MAX_CAPTURED_OUTPUT_LENGTH - len(_TRUNCATION_MARKER)
    return text[:retained] + _TRUNCATION_MARKER


def _json_object(value: str | None) -> Mapping[str, Any]:
    if not value:
        return MappingProxyType({})
    try:
        loaded = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return MappingProxyType({})
    return MappingProxyType(dict(loaded) if isinstance(loaded, dict) else {})


class OperationHistoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    def _write_connection(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS operation_runs (
                run_id TEXT PRIMARY KEY,
                operation TEXT NOT NULL,
                started_at_utc TEXT NOT NULL,
                finished_at_utc TEXT,
                status TEXT NOT NULL,
                exit_code INTEGER,
                duration_ms INTEGER,
                capabilities_json TEXT NOT NULL,
                failed_step TEXT,
                error_type TEXT,
                error_message TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS operation_steps (
                run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                step_name TEXT NOT NULL,
                started_at_utc TEXT NOT NULL,
                finished_at_utc TEXT NOT NULL,
                status TEXT NOT NULL,
                exit_code INTEGER,
                duration_ms INTEGER NOT NULL,
                error_message TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (run_id, sequence),
                FOREIGN KEY (run_id) REFERENCES operation_runs(run_id)
            );
            CREATE INDEX IF NOT EXISTS idx_operation_runs_started
                ON operation_runs(started_at_utc DESC, run_id DESC);
            """
        )
        return connection

    def begin_run(
        self,
        operation: str,
        capabilities: Iterable[str],
        *,
        run_id: str | None = None,
        started_at_utc: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        identity = run_id or str(uuid4())
        started = started_at_utc or datetime.now(timezone.utc).isoformat()
        with self._write_connection() as connection:
            connection.execute(
                "INSERT INTO operation_runs "
                "(run_id,operation,started_at_utc,status,capabilities_json,metadata_json) "
                "VALUES (?,?,?,?,?,?)",
                (
                    identity,
                    operation,
                    started,
                    RunStatus.RUNNING.value,
                    json.dumps(tuple(capabilities), separators=(",", ":")),
                    json.dumps(dict(metadata or {}), sort_keys=True, separators=(",", ":"), default=str),
                ),
            )
        return identity

    def finish_run(
        self,
        run_id: str,
        *,
        status: RunStatus,
        exit_code: int,
        duration_ms: int,
        finished_at_utc: str | None = None,
        failed_step: str | None = None,
        error_type: str | None = None,
        error_message: object | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if status is RunStatus.RUNNING:
            raise ValueError("a finished run cannot retain RUNNING status")
        with self._write_connection() as connection:
            updated = connection.execute(
                "UPDATE operation_runs SET finished_at_utc=?,status=?,exit_code=?,duration_ms=?,"
                "failed_step=?,error_type=?,error_message=?,metadata_json=? WHERE run_id=?",
                (
                    finished_at_utc or datetime.now(timezone.utc).isoformat(),
                    status.value,
                    int(exit_code),
                    max(0, int(duration_ms)),
                    sanitize_diagnostic(failed_step),
                    sanitize_diagnostic(error_type),
                    sanitize_diagnostic(error_message),
                    json.dumps(dict(metadata or {}), sort_keys=True, separators=(",", ":"), default=str),
                    run_id,
                ),
            ).rowcount
            if updated != 1:
                raise KeyError(f"unknown operation run: {run_id}")

    def record_steps(self, run_id: str, steps: Iterable[RunStep]) -> None:
        rows = tuple(steps)
        with self._write_connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM operation_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if exists is None:
                raise KeyError(f"unknown operation run: {run_id}")
            connection.execute("DELETE FROM operation_steps WHERE run_id=?", (run_id,))
            connection.executemany(
                "INSERT INTO operation_steps VALUES (?,?,?,?,?,?,?,?,?,?)",
                tuple(
                    (
                        item.run_id,
                        item.sequence,
                        item.step_name,
                        item.started_at_utc,
                        item.finished_at_utc,
                        item.status.value,
                        item.exit_code,
                        item.duration_ms,
                        sanitize_diagnostic(item.error_message),
                        json.dumps(dict(item.metadata), sort_keys=True, separators=(",", ":"), default=str),
                    )
                    for item in rows
                ),
            )

    def _run_from_row(self, row: sqlite3.Row) -> RunSummary:
        capabilities = json.loads(row["capabilities_json"])
        return RunSummary(
            str(row["run_id"]),
            str(row["operation"]),
            str(row["started_at_utc"]),
            None if row["finished_at_utc"] is None else str(row["finished_at_utc"]),
            RunStatus(str(row["status"])),
            None if row["exit_code"] is None else int(row["exit_code"]),
            None if row["duration_ms"] is None else int(row["duration_ms"]),
            tuple(str(item) for item in capabilities),
            None if row["failed_step"] is None else str(row["failed_step"]),
            None if row["error_type"] is None else str(row["error_type"]),
            None if row["error_message"] is None else str(row["error_message"]),
        )

    def list_runs(self, limit: int = 20) -> tuple[RunSummary, ...]:
        if limit <= 0:
            raise ValueError("history limit must be positive")
        if not self.path.is_file():
            return ()
        try:
            with sqlite_read_only(self.path) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT * FROM operation_runs ORDER BY started_at_utc DESC, run_id DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
            return tuple(self._run_from_row(row) for row in rows)
        except (sqlite3.Error, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HistoryReadError("Operation history is unavailable or malformed.") from exc

    def get_run(self, run_id: str) -> RunDetail | None:
        if not self.path.is_file():
            return None
        try:
            with sqlite_read_only(self.path) as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute(
                    "SELECT * FROM operation_runs WHERE run_id=?", (run_id,)
                ).fetchone()
                if row is None:
                    return None
                step_rows = connection.execute(
                    "SELECT * FROM operation_steps WHERE run_id=? ORDER BY sequence", (run_id,)
                ).fetchall()
            steps = tuple(
                RunStep(
                    str(item["run_id"]),
                    int(item["sequence"]),
                    str(item["step_name"]),
                    str(item["started_at_utc"]),
                    str(item["finished_at_utc"]),
                    RunStatus(str(item["status"])),
                    int(item["duration_ms"]),
                    None if item["exit_code"] is None else int(item["exit_code"]),
                    None if item["error_message"] is None else str(item["error_message"]),
                    _json_object(item["metadata_json"]),
                )
                for item in step_rows
            )
            return RunDetail(self._run_from_row(row), steps, _json_object(row["metadata_json"]))
        except (sqlite3.Error, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HistoryReadError("Operation history is unavailable or malformed.") from exc

    def latest_run(self) -> RunDetail | None:
        rows = self.list_runs(1)
        return None if not rows else self.get_run(rows[0].run_id)


def record_daily_pipeline_steps(result: Any, *, root: Path = PROJECT_ROOT) -> None:
    run_id = os.environ.get(RUN_ID_ENV, "").strip()
    configured_path = os.environ.get(HISTORY_PATH_ENV, "").strip()
    if not run_id or not configured_path:
        return
    store = OperationHistoryStore(history_path(root=root, path=Path(configured_path)))
    detail = store.get_run(run_id)
    if detail is None:
        return
    cursor = datetime.fromisoformat(detail.summary.started_at_utc)
    steps: list[RunStep] = []
    for sequence, stage in enumerate(result.stages, start=1):
        duration_ms = max(0, round(float(stage.duration_seconds) * 1000))
        finished = cursor + timedelta(milliseconds=duration_ms)
        metadata = MappingProxyType({"warning": sanitize_diagnostic(stage.warning)}) if stage.warning else MappingProxyType({})
        steps.append(
            RunStep(
                run_id,
                sequence,
                str(stage.name),
                cursor.isoformat(),
                finished.isoformat(),
                RunStatus.SUCCESS if stage.success else RunStatus.FAILED,
                duration_ms,
                0 if stage.success else 1,
                sanitize_diagnostic(stage.error),
                metadata,
            )
        )
        cursor = finished
    store.record_steps(run_id, steps)


def run_tracked_entrypoint(
    operation: str,
    function: Callable[[], Any],
    *,
    root: Path = PROJECT_ROOT,
) -> int:
    from quantctl.runtime import configure_utf8_stdio

    configure_utf8_stdio()
    existing_run_id = os.environ.get(RUN_ID_ENV, "").strip()
    if existing_run_id:
        value = function()
        return int(value) if isinstance(value, int) else 0

    from quantctl.operations import get_operation

    spec = get_operation(operation)
    store_path = history_path(root=root)
    store = OperationHistoryStore(store_path)
    started = datetime.now(timezone.utc)
    run_id = store.begin_run(operation, (item.value for item in spec.capabilities), started_at_utc=started.isoformat())
    previous_id = os.environ.get(RUN_ID_ENV)
    previous_path = os.environ.get(HISTORY_PATH_ENV)
    os.environ[RUN_ID_ENV] = run_id
    os.environ[HISTORY_PATH_ENV] = str(store_path)
    try:
        value = function()
        exit_code = int(value) if isinstance(value, int) else 0
        status = (
            RunStatus.SUCCESS
            if exit_code == 0
            else (RunStatus.CANCELLED if exit_code == 130 else RunStatus.FAILED)
        )
        interim = store.get_run(run_id)
        failed_step = next(
            (
                item.step_name
                for item in (interim.steps if interim else ())
                if item.status is RunStatus.FAILED
            ),
            None,
        )
        store.finish_run(
            run_id,
            status=status,
            exit_code=exit_code,
            duration_ms=round((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            failed_step=failed_step,
            error_type=None if exit_code == 0 else ("KeyboardInterrupt" if exit_code == 130 else "OperationExit"),
            error_message=None if exit_code == 0 else f"Operation returned exit code {exit_code}.",
        )
        return exit_code
    except KeyboardInterrupt:
        store.finish_run(
            run_id,
            status=RunStatus.CANCELLED,
            exit_code=130,
            duration_ms=round((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            error_type="KeyboardInterrupt",
            error_message="Operation cancelled by user.",
        )
        raise
    except BaseException as error:
        store.finish_run(
            run_id,
            status=RunStatus.FAILED,
            exit_code=1,
            duration_ms=round((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            error_type=type(error).__name__,
            error_message=error,
        )
        raise
    finally:
        if previous_id is None:
            os.environ.pop(RUN_ID_ENV, None)
        else:
            os.environ[RUN_ID_ENV] = previous_id
        if previous_path is None:
            os.environ.pop(HISTORY_PATH_ENV, None)
        else:
            os.environ[HISTORY_PATH_ENV] = previous_path
