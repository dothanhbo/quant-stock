from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import Enum
import importlib.util
from pathlib import Path
import sqlite3
import subprocess
from typing import Iterable


QUANTCTL_VERSION = "M1"
EXPECTED_ACTIVE_RUNNER_COUNT = 22
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ACTIVE_RUNNER_PATTERN = "run_quantlab_*.py"
STATE_DATABASES = (
    ("Forward", "forward_validation.db"),
    ("Paper V1", "paper_trading.db"),
    ("Paper V2", "paper_trading_v2.db"),
    ("Paper V3", "paper_trading_v3.db"),
)


class CommandSafety(str, Enum):
    READ_ONLY = "READ_ONLY"


@dataclass(frozen=True, slots=True)
class GitInfo:
    head: str | None
    tag: str | None
    working_tree: str
    available: bool


@dataclass(frozen=True, slots=True)
class RunnerInfo:
    short_name: str
    module: str
    path: Path
    available: bool
    archive_imports: tuple[str, ...]
    error: str | None = None


@dataclass(frozen=True, slots=True)
class MarketDatabaseInfo:
    path: Path
    exists: bool
    readable: bool
    latest_session: str | None = None
    session_count: int | None = None
    symbol_count: int | None = None
    latest_session_symbol_count: int | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PersistentDatabaseInfo:
    label: str
    path: Path
    exists: bool
    readable: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    git: GitInfo
    market: MarketDatabaseInfo
    persistent_databases: tuple[PersistentDatabaseInfo, ...]
    runners: tuple[RunnerInfo, ...]
    archive_isolated: bool
    telegram_module_available: bool


def run_git(arguments: Iterable[str], *, root: Path = PROJECT_ROOT) -> str | None:
    try:
        completed = subprocess.run(
            ("git", *arguments),
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip()


def inspect_git(*, root: Path = PROJECT_ROOT) -> GitInfo:
    head = run_git(("rev-parse", "--short", "HEAD"), root=root)
    if head is None:
        return GitInfo(None, None, "UNKNOWN", False)

    tag = run_git(("describe", "--tags", "--exact-match", "HEAD"), root=root)
    if not tag:
        tag = run_git(("describe", "--tags", "--abbrev=0"), root=root)
    status = run_git(("status", "--porcelain"), root=root)
    return GitInfo(head, tag or None, "UNKNOWN" if status is None else ("DIRTY" if status else "CLEAN"), True)


def _archive_imports(tree: ast.AST) -> tuple[str, ...]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = (item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            names = (node.module or "",)
        else:
            continue
        imports.update(name for name in names if name == "research.archive" or name.startswith("research.archive."))
    return tuple(sorted(imports))


def discover_active_runners(*, root: Path = PROJECT_ROOT) -> tuple[RunnerInfo, ...]:
    research_root = root / "research"
    runners: list[RunnerInfo] = []
    for path in sorted(research_root.glob(ACTIVE_RUNNER_PATTERN), key=lambda item: item.name):
        short_name = path.stem.removeprefix("run_quantlab_").replace("_", "-")
        relative = path.relative_to(root)
        module = ".".join(relative.with_suffix("").parts)
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(relative))
            spec = importlib.util.spec_from_file_location(module, path)
            if spec is None or spec.loader is None:
                raise ImportError("module loader is unavailable")
            archive_imports = _archive_imports(tree)
            runners.append(RunnerInfo(short_name, module, relative, True, archive_imports))
        except (OSError, SyntaxError, ImportError) as exc:
            runners.append(RunnerInfo(short_name, module, relative, False, (), f"{type(exc).__name__}: {exc}"))
    return tuple(runners)


def sqlite_read_only(path: Path) -> sqlite3.Connection:
    # Operational stores may have committed data in an active WAL. Immutable
    # mode skips that journal; mode=ro observes it without creating a database,
    # changing journal mode or checkpointing. query_only also rejects SQL writes.
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    connection.execute("BEGIN")
    return connection


def inspect_market_database(path: Path) -> MarketDatabaseInfo:
    path = path.resolve()
    if not path.is_file():
        return MarketDatabaseInfo(path, False, False, error="database file is missing")
    try:
        with sqlite_read_only(path) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'prices'"
            ).fetchone()
            if table is None:
                raise sqlite3.DatabaseError("required prices table is missing")
            latest, sessions, symbols = connection.execute(
                "SELECT MAX(date(time)), COUNT(DISTINCT date(time)), "
                "COUNT(DISTINCT UPPER(TRIM(symbol))) FROM prices"
            ).fetchone()
            latest_symbols = None
            if latest is not None:
                latest_symbols = connection.execute(
                    "SELECT COUNT(DISTINCT UPPER(TRIM(symbol))) "
                    "FROM prices WHERE date(time) = ?",
                    (latest,),
                ).fetchone()[0]
        return MarketDatabaseInfo(
            path,
            True,
            True,
            None if latest is None else str(latest),
            int(sessions),
            int(symbols),
            None if latest_symbols is None else int(latest_symbols),
        )
    except (OSError, sqlite3.Error) as exc:
        return MarketDatabaseInfo(path, True, False, error=f"{type(exc).__name__}: {exc}")


def sqlite_database_readable(path: Path) -> tuple[bool, str | None]:
    if not path.is_file():
        return False, "database file is missing"
    try:
        with sqlite_read_only(path) as connection:
            connection.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
    except (OSError, sqlite3.Error) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, None


def inspect_system(*, root: Path = PROJECT_ROOT) -> SystemSnapshot:
    data_root = root / "data"
    persistent: list[PersistentDatabaseInfo] = []
    for label, filename in STATE_DATABASES:
        path = (data_root / filename).resolve()
        exists = path.is_file()
        readable, error = sqlite_database_readable(path) if exists else (False, "database file is missing")
        persistent.append(PersistentDatabaseInfo(label, path, exists, readable, error))

    runners = discover_active_runners(root=root)
    return SystemSnapshot(
        git=inspect_git(root=root),
        market=inspect_market_database(data_root / "market.db"),
        persistent_databases=tuple(persistent),
        runners=runners,
        archive_isolated=all(not runner.archive_imports for runner in runners),
        telegram_module_available=(root / "services" / "telegram_bot" / "__init__.py").is_file(),
    )
