from __future__ import annotations

from pathlib import Path

from quantctl.registry import (
    PROJECT_ROOT,
    discover_active_runners,
    inspect_git,
    inspect_market_database,
)


STATE_DATABASES = (
    ("Forward", "forward_validation.db"),
    ("Paper V1", "paper_trading.db"),
    ("Paper V2", "paper_trading_v2.db"),
    ("Paper V3", "paper_trading_v3.db"),
)


def render(*, root: Path = PROJECT_ROOT) -> str:
    git = inspect_git(root=root)
    data_root = root / "data"
    market = inspect_market_database(data_root / "market.db")
    runners = discover_active_runners(root=root)
    archive_isolated = all(not item.archive_imports for item in runners)
    telegram_available = (root / "services" / "telegram_bot" / "__init__.py").is_file()

    lines = [
        "QUANT SYSTEM STATUS",
        "",
        "Repository",
        f"  Version: {git.tag or 'UNKNOWN'}",
        f"  HEAD: {git.head or 'UNKNOWN'}",
        f"  Git: {git.working_tree}",
        "",
        "Market Data",
        f"  Database: {'AVAILABLE' if market.readable else ('MISSING' if not market.exists else 'UNKNOWN')}",
        f"  Latest session: {market.latest_session or 'UNKNOWN'}",
        f"  Sessions: {market.session_count if market.session_count is not None else 'UNKNOWN'}",
        f"  Symbols: {market.symbol_count if market.symbol_count is not None else 'UNKNOWN'}",
        f"  Latest-session symbols: {market.latest_session_symbol_count if market.latest_session_symbol_count is not None else 'UNKNOWN'}",
        "  Status: " + ("AVAILABLE" if market.readable else "UNKNOWN"),
        "",
        "Persistent State",
    ]
    lines.extend(
        f"  {label}: {'AVAILABLE' if (data_root / filename).is_file() else 'MISSING'}"
        for label, filename in STATE_DATABASES
    )
    lines.extend(
        (
            "",
            "Research",
            f"  Active runners: {len(runners)}",
            f"  Archive isolation: {'OK' if archive_isolated else 'UNKNOWN'}",
            "",
            "Telegram",
            f"  Module: {'AVAILABLE' if telegram_available else 'MISSING'}",
        )
    )
    return "\n".join(lines)


def run(*, root: Path = PROJECT_ROOT) -> int:
    print(render(root=root))
    return 0
