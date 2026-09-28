from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import os
from pathlib import Path
import sys
from typing import Mapping

from quantctl.commands.status import STATE_DATABASES
from quantctl.registry import (
    EXPECTED_ACTIVE_RUNNER_COUNT,
    PROJECT_ROOT,
    discover_active_runners,
    inspect_git,
    inspect_market_database,
    sqlite_database_readable,
)


PACKAGE_IMPORTS = (
    ("pandas", "pandas"),
    ("numpy", "numpy"),
    ("requests", "requests"),
    ("sqlalchemy", "sqlalchemy"),
    ("python-dotenv", "dotenv"),
    ("vnstock", "vnstock"),
    ("PyYAML", "yaml"),
    ("streamlit", "streamlit"),
    ("plotly", "plotly"),
)
REQUIRED_SERVICE_ENVIRONMENT = ("TELEGRAM_TOKEN", "CHAT_ID")


@dataclass(frozen=True, slots=True)
class Check:
    section: str
    status: str
    name: str
    detail: str = ""


def _dotenv_configured_names(path: Path) -> frozenset[str]:
    configured: set[str] = set()
    if not path.is_file():
        return frozenset()
    try:
        for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.removeprefix("export ").split("=", 1)
            if name.strip() and value.strip().strip("'\""):
                configured.add(name.strip())
    except OSError:
        return frozenset()
    return frozenset(configured)


def collect_checks(
    *,
    root: Path = PROJECT_ROOT,
    environ: Mapping[str, str] | None = None,
) -> tuple[Check, ...]:
    environment = os.environ if environ is None else environ
    checks: list[Check] = []
    checks.append(Check("Repository", "PASS" if root.is_dir() else "FAIL", "repository root", str(root)))
    git = inspect_git(root=root)
    checks.append(Check("Repository", "PASS" if git.available else "WARN", "Git metadata", git.head or "unavailable"))
    checks.append(Check("Python", "PASS", "Python runtime", sys.version.split()[0]))
    for display_name, import_name in PACKAGE_IMPORTS:
        available = importlib.util.find_spec(import_name) is not None
        checks.append(Check("Python", "PASS" if available else "FAIL", display_name, "import available" if available else "import missing"))

    dotenv_path = root / ".env"
    dotenv_names = _dotenv_configured_names(dotenv_path)
    checks.append(Check("Environment", "PASS" if dotenv_path.is_file() else "WARN", ".env", "present" if dotenv_path.is_file() else "not present"))
    for name in REQUIRED_SERVICE_ENVIRONMENT:
        configured = bool(str(environment.get(name, "")).strip()) or name in dotenv_names
        checks.append(Check("Environment", "PASS" if configured else "WARN", name, "CONFIGURED" if configured else "NOT_CONFIGURED"))

    market = inspect_market_database(root / "data" / "market.db")
    market_status = "PASS" if market.readable else "FAIL"
    checks.append(Check("Market Data", market_status, "database readable", market.latest_session or market.error or "UNKNOWN"))
    for label, filename in STATE_DATABASES:
        path = root / "data" / filename
        if not path.is_file():
            checks.append(Check("Persistent State", "WARN", label, "MISSING"))
            continue
        readable, error = sqlite_database_readable(path)
        checks.append(Check("Persistent State", "PASS" if readable else "FAIL", label, "readable" if readable else (error or "unreadable")))

    runners = discover_active_runners(root=root)
    count_ok = len(runners) == EXPECTED_ACTIVE_RUNNER_COUNT
    checks.append(Check("Research", "PASS" if count_ok else "FAIL", "active runner count", f"{len(runners)} discovered; expected {EXPECTED_ACTIVE_RUNNER_COUNT}"))
    unavailable = tuple(item.short_name for item in runners if not item.available)
    checks.append(Check("Research", "PASS" if not unavailable else "FAIL", "runner module resolution", "all resolvable without execution" if not unavailable else ", ".join(unavailable)))
    archive_importers = tuple(item.short_name for item in runners if item.archive_imports)
    checks.append(Check("Research", "PASS" if not archive_importers else "FAIL", "archive isolation", "no active runner imports research.archive" if not archive_importers else ", ".join(archive_importers)))

    telegram_module = root / "services" / "telegram_bot" / "__init__.py"
    checks.append(Check("Telegram", "PASS" if telegram_module.is_file() else "WARN", "module surface", "available; not imported" if telegram_module.is_file() else "missing"))
    return tuple(checks)


def overall_status(checks: tuple[Check, ...]) -> str:
    statuses = {item.status for item in checks}
    if "FAIL" in statuses:
        return "FAIL"
    if "WARN" in statuses:
        return "WARN"
    return "PASS"


def render(*, root: Path = PROJECT_ROOT, environ: Mapping[str, str] | None = None) -> str:
    checks = collect_checks(root=root, environ=environ)
    lines = ["QUANT SYSTEM DOCTOR", ""]
    section = None
    for check in checks:
        if check.section != section:
            if section is not None:
                lines.append("")
            section = check.section
            lines.append(section)
        detail = f": {check.detail}" if check.detail else ""
        lines.append(f"  {check.status} {check.name}{detail}")
    lines.extend(("", f"OVERALL: {overall_status(checks)}"))
    return "\n".join(lines)


def run(*, root: Path = PROJECT_ROOT) -> int:
    print(render(root=root))
    return 0
