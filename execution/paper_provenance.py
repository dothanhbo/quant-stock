"""R3: read-only qualification of paper evidence from the persisted market-data bindings.

A paper entry/exit order carries its immutable market-data binding inside the
order's ``execution_context["market_binding"]`` (written atomically with the
order, fill and lifecycle/closed-trade rows).  This module only *reads* the
paper store (SQLite read-only) and evaluates each closed trade through
:class:`core.evidence_market_binding.EvidenceQualifier`.  It never writes to the
paper store.  A trade whose entry or exit order has no binding is legacy
(``LEGACY_UNBOUND``) and can never become verified.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from core.evidence_market_binding import (
    EvidenceQualifier,
    QualificationResult,
    trade_leg_symbols,
)


def _read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _context(raw: object) -> dict[str, Any]:
    try:
        value = json.loads(str(raw)) if raw else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def order_bindings(paper_database_path: str | Path) -> dict[str, dict[str, Any]]:
    """``client_order_id -> binding`` for every order that carries one (read-only)."""
    path = Path(paper_database_path)
    if not path.is_file():
        return {}
    with _read_only(path) as connection:
        tables = {str(r[0]) for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "paper_orders" not in tables:
            return {}
        columns = {str(r[1]) for r in connection.execute("PRAGMA table_info(paper_orders)")}
        if "execution_context" not in columns:
            return {}
        rows = connection.execute(
            "SELECT client_order_id, execution_context FROM paper_orders WHERE execution_context IS NOT NULL"
        ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        binding = _context(row["execution_context"]).get("market_binding")
        if isinstance(binding, dict):
            result[str(row["client_order_id"])] = binding
    return result


def qualify_closed_trades(
    paper_database_path: str | Path,
    qualifier: EvidenceQualifier,
) -> dict[str, QualificationResult]:
    """Qualification of every closed trade, keyed by its exit ``order_id``.

    A trade is only as qualified as its weakest leg: the entry fill and the exit
    fill are each evaluated against their own binding, restricted to the traded
    symbol plus the VNINDEX regime/relative-strength history the entry signal consumed, so an
    unrelated symbol's unresolved revision does not affect it.
    """
    path = Path(paper_database_path)
    if not path.is_file():
        return {}
    bindings = order_bindings(path)
    with _read_only(path) as connection:
        tables = {str(r[0]) for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "paper_closed_trades" not in tables:
            return {}
        trades = connection.execute("SELECT symbol, order_id FROM paper_closed_trades ORDER BY id").fetchall()
        contexts: dict[str, dict[str, Any]] = {}
        if "paper_orders" in tables:
            for row in connection.execute("SELECT client_order_id, execution_context FROM paper_orders"):
                contexts[str(row["client_order_id"])] = _context(row["execution_context"])
    result: dict[str, QualificationResult] = {}
    for trade in trades:
        symbol, exit_order_id = str(trade["symbol"]), str(trade["order_id"])
        exit_context = contexts.get(exit_order_id, {})
        entry_order_id = (exit_context.get("exit") or {}).get("entry_order_id")
        legs: list[tuple[Mapping[str, Any] | None, tuple[str, ...]]] = [
            (bindings.get(exit_order_id), trade_leg_symbols(symbol)),
            (bindings.get(str(entry_order_id)) if entry_order_id else None, trade_leg_symbols(symbol)),
        ]
        result[exit_order_id] = qualifier.qualify(legs)
    return result


__all__ = ("order_bindings", "qualify_closed_trades")
