"""Consumer-side gate over the persistent market observation log.

Every consumer of ``market.db`` that must not read data whose admission is
unresolved goes through :func:`check_market_provenance` (directly, or through
``core.market_data_integrity.check_market_data_integrity``). The decision is
derived only from durable state, so it holds across processes: a block
recorded by ``update_data`` is visible to a later, independent Daily, scanner
or lifecycle process.

Per symbol, the gate fails closed on:

* an UNRESOLVED block (``BLOCKED_REVISION_CHANGED`` and friends): it stays
  active until superseded by a later accepted observation, or explicitly
  resolved by a reviewed resolution event;
* an open application intent (the market change may be half-applied);
* content that no longer matches the baseline slice / latest dataset version
  (a write outside the guard);
* a log bound to a different dataset.

Resolved blocks, pending-but-undecided observations (the market was never
touched) and blocks on *other* symbols do not gate. A market database with no
observation log is ``NOT_APPLICABLE``: the provenance layer was never
activated for it, so there is nothing to enforce (legacy behaviour).

The log is opened read-only and is never created here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from core.market_observation_log import (
    ObservationLogError,
    open_observation_log,
    resolve_observation_log_path,
)
from core.paths import resolve_market_database_path

PASS = "PASS"
FAIL_CLOSED = "FAIL_CLOSED"
NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class MarketProvenanceResult:
    state: str
    reasons: tuple[str, ...]
    blocked_symbols: tuple[str, ...] = ()
    pending_symbols: tuple[str, ...] = ()
    mismatched_symbols: tuple[str, ...] = ()
    database_path: Path | None = None
    log_path: Path | None = None

    @property
    def ok(self) -> bool:
        return self.state != FAIL_CLOSED

    @property
    def message(self) -> str:
        return "; ".join(self.reasons)


def _fail(path: Path, log_path: Path | None, *reasons: str, **extra: tuple[str, ...]) -> MarketProvenanceResult:
    return MarketProvenanceResult(
        state=FAIL_CLOSED,
        reasons=tuple(reasons),
        database_path=path,
        log_path=log_path,
        **extra,
    )


def check_market_provenance(
    database_path: str | Path | None = None,
    symbols: Sequence[str] | None = None,
    *,
    log_path: str | Path | None = None,
    verify_content: bool = True,
) -> MarketProvenanceResult:
    """Return the provenance verdict for ``symbols`` (all symbols when ``None``)."""
    path = resolve_market_database_path(database_path)
    try:
        resolved_log = resolve_observation_log_path(path, log_path)
    except Exception as error:  # pragma: no cover - defensive
        return _fail(path, None, f"market provenance: cannot resolve observation log: {error}")
    if not resolved_log.is_file():
        return MarketProvenanceResult(
            state=NOT_APPLICABLE,
            reasons=("no observation log registered for this market database",),
            database_path=path,
            log_path=resolved_log,
        )
    wanted = None if symbols is None else tuple(sorted({str(s).strip().upper() for s in symbols if str(s).strip()}))
    try:
        log = open_observation_log(path, resolved_log, readonly=True, require_baseline=True)
        blocks = log.unresolved_blocks(wanted)
        intents = log.open_intents()
        issues = log.verify_dataset(path, wanted) if verify_content else {}
    except ObservationLogError as error:
        return _fail(path, resolved_log, f"market provenance: {error}")
    except Exception as error:  # fail closed on any unreadable state
        return _fail(path, resolved_log, f"market provenance: cannot read observation log safely: {error}")

    pending = sorted(
        {
            str(item["symbol"])
            for item in intents
            if wanted is None or str(item["symbol"]) in wanted
        }
    )
    blocked = sorted({str(block["symbol"]) for block in blocks})
    mismatched = sorted(issues)
    reasons: list[str] = []
    for block in blocks:
        reasons.append(
            f"market provenance: unresolved {block['admission_result']} block #{block['block_id']} "
            f"on {block['symbol']} (observation {block['observation_id']}); resolve by a later "
            "accepted observation or a reviewed resolution"
        )
    for symbol in pending:
        reasons.append(f"market provenance: application pending for {symbol}")
    for symbol in mismatched:
        reasons.append(f"market provenance: {symbol} content does not match lineage ({', '.join(issues[symbol])})")
    if reasons:
        return _fail(
            path,
            resolved_log,
            *reasons,
            blocked_symbols=tuple(blocked),
            pending_symbols=tuple(pending),
            mismatched_symbols=tuple(mismatched),
        )
    return MarketProvenanceResult(
        state=PASS,
        reasons=("no unresolved blocks, pending applications or lineage mismatches",),
        database_path=path,
        log_path=resolved_log,
    )


def require_market_provenance(
    database_path: str | Path | None = None,
    symbols: Sequence[str] | None = None,
    *,
    log_path: str | Path | None = None,
    verify_content: bool = True,
) -> MarketProvenanceResult:
    """Raise ``RuntimeError`` unless the provenance verdict is not FAIL_CLOSED."""
    result = check_market_provenance(
        database_path, symbols, log_path=log_path, verify_content=verify_content
    )
    if result.state == FAIL_CLOSED:
        raise RuntimeError("Market provenance gate failed closed: " + result.message)
    return result


__all__ = (
    "FAIL_CLOSED",
    "NOT_APPLICABLE",
    "PASS",
    "MarketProvenanceResult",
    "check_market_provenance",
    "require_market_provenance",
)
