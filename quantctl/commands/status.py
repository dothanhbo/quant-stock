from __future__ import annotations

from pathlib import Path

from quantctl.registry import (
    PROJECT_ROOT,
    STATE_DATABASES,
    inspect_system,
)


def render(*, root: Path = PROJECT_ROOT) -> str:
    snapshot = inspect_system(root=root)
    git = snapshot.git
    market = snapshot.market
    try:
        from quantctl.runtime_configuration import resolve_runtime_configuration

        configuration = resolve_runtime_configuration()
        configuration_lines = (
            f"  Strategy: {configuration.strategy_identity}",
            f"  Store: {configuration.paper_store_id}",
            f"  Fingerprint: {configuration.fingerprint}",
        )
    except Exception as error:
        configuration_lines = (f"  ERROR: {type(error).__name__}: {error}",)

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
            "Runtime Configuration",
            *configuration_lines,
            "",
            "Persistent State",
    ]
    lines.extend(
        f"  {state.label}: {'AVAILABLE' if state.exists else 'MISSING'}"
        for state in snapshot.persistent_databases
    )
    lines.extend(
        (
            "",
            "Research",
            f"  Active runners: {len(snapshot.runners)}",
            f"  Archive isolation: {'OK' if snapshot.archive_isolated else 'UNKNOWN'}",
            "",
            "Telegram",
            f"  Module: {'AVAILABLE' if snapshot.telegram_module_available else 'MISSING'}",
        )
    )
    return "\n".join(lines)


def run(*, root: Path = PROJECT_ROOT) -> int:
    print(render(root=root))
    return 0
