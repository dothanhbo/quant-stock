"""R3: bind market-data version / session provenance into forward and paper evidence.

This module is the single implementation of three things:

1. **Binding capture** (:class:`MarketBindingSession`).  At the *decision point*
   a session pins the dataset version it is about to consume (read from the R1/R2
   observation log).  ``bind`` then records, per required ``(symbol, session)``
   dependency, which observation / dataset version supplied it.  A binding is
   never built against "latest": if the dataset version moved after the session
   was pinned, :class:`StaleDatasetVersionError` is raised and nothing is bound.

2. **Qualification** (:class:`EvidenceQualifier`).  A pure, deterministic function
   of ``(immutable binding, current log state, reviews)``.  A later unresolved
   revision, a changed baseline or a drifted session never rewrites the binding;
   it only changes the *derived* qualification, which can additionally be
   appended to an append-only events table (``sync_qualification_events``).

3. **Storage helpers** for the additive, append-only tables that live in the
   *same SQLite file as the evidence* (``<prefix>_market_bindings`` and
   ``<prefix>_qualification_events``), so a binding and its evidence are written
   in one transaction and no cross-database write ever exists.

Legacy evidence (rows with no binding) is never backfilled: it is reported as
``LEGACY_UNBOUND`` (label ``LEGACY_UNVERIFIED``) and can never become verified.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping, Sequence

from core.market_observation_log import (
    open_observation_log,
    resolve_observation_log_path,
)
from core.paths import resolve_market_database_path

CONTRACT = "core.evidence_market_binding"
VERSION = "v1"
LEGACY_LABEL = "LEGACY_UNVERIFIED"

LOG_BOUND = "BOUND"
LOG_MISSING = "NO_OBSERVATION_LOG"
LOG_UNREADABLE = "LOG_UNREADABLE"

#: session origins that mean "no attributable provenance for this session".
MISSING_ORIGINS = frozenset(
    {"PENDING_APPLICATION", "UNATTRIBUTED", "REMOVED", "ABSENT", "NO_LOG", "UNPROVEN_SOURCE"}
)
COVERAGE_POINT = "POINT"
COVERAGE_WINDOW = "WINDOW"
#: the consumer holds a value it cannot prove came from the claimed session (never verifiable).
COVERAGE_UNPROVEN = "UNPROVEN"
#: worst-first order used to reduce a consumed window to one origin.
_WINDOW_ORIGIN_ORDER = (
    "PENDING_APPLICATION", "REMOVED", "UNATTRIBUTED", "ABSENT", "BASELINE_LEGACY", "OBSERVATION",
)
BASELINE_ORIGIN = "BASELINE_LEGACY"
OBSERVATION_ORIGIN = "OBSERVATION"


class Qualification(str, Enum):
    PROVENANCE_VERIFIED = "PROVENANCE_VERIFIED"
    BOUND_LEGACY_INPUT = "BOUND_LEGACY_INPUT"
    LEGACY_UNBOUND = "LEGACY_UNBOUND"
    QUARANTINED_UNRESOLVED_REVISION = "QUARANTINED_UNRESOLVED_REVISION"
    QUARANTINED_INCOMPATIBLE_BASIS = "QUARANTINED_INCOMPATIBLE_BASIS"
    QUARANTINED_MISSING_PROVENANCE = "QUARANTINED_MISSING_PROVENANCE"
    REVIEWED_ACCEPTED = "REVIEWED_ACCEPTED"
    REVIEWED_REJECTED = "REVIEWED_REJECTED"


#: usable where "qualified / reproducible evidence" is claimed.
QUALIFIED_STATES = frozenset({Qualification.PROVENANCE_VERIFIED.value, Qualification.REVIEWED_ACCEPTED.value})
#: usable only as explicitly labelled descriptive evidence.
DESCRIPTIVE_ONLY_STATES = frozenset({Qualification.BOUND_LEGACY_INPUT.value, Qualification.LEGACY_UNBOUND.value})
#: never included in any claim; preserved, never deleted.
EXCLUDED_STATES = frozenset(
    {
        Qualification.QUARANTINED_UNRESOLVED_REVISION.value,
        Qualification.QUARANTINED_INCOMPATIBLE_BASIS.value,
        Qualification.QUARANTINED_MISSING_PROVENANCE.value,
        Qualification.REVIEWED_REJECTED.value,
    }
)
QUARANTINED_STATES = frozenset(state for state in EXCLUDED_STATES if state.startswith("QUARANTINED_"))

# Worst-first precedence when several reasons apply (deterministic).
_PRECEDENCE = (
    Qualification.QUARANTINED_MISSING_PROVENANCE.value,
    Qualification.QUARANTINED_INCOMPATIBLE_BASIS.value,
    Qualification.QUARANTINED_UNRESOLVED_REVISION.value,
    Qualification.LEGACY_UNBOUND.value,
    Qualification.BOUND_LEGACY_INPUT.value,
    Qualification.PROVENANCE_VERIFIED.value,
)


class MarketBindingError(RuntimeError):
    """A binding cannot be built or stored safely."""


class StaleDatasetVersionError(MarketBindingError):
    """The dataset version moved after the decision point; binding to 'latest' is refused."""


class PendingApplicationError(StaleDatasetVersionError):
    """A consumed session (or window) is claimed by an open, unfinalized application."""


class ProvenanceQuarantineError(MarketBindingError):
    """New evidence would be quarantined at bind time; the write is refused (fail closed)."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(value: Any) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def binding_identity(payload: Mapping[str, Any]) -> str:
    """Identity of the immutable binding content (excludes wall-clock fields)."""
    content = {key: value for key, value in payload.items() if key not in {"bound_at_utc", "binding_identity"}}
    return f"mbind-{_sha256(content)[:40]}"


def _normalize_dependency(item: Sequence[Any]) -> tuple[str, str, str, str | None, str]:
    """``(role, symbol, session[, window_start[, coverage]])``.

    ``coverage`` is ``POINT`` (the one session; ``window_start`` only scopes which
    unresolved revisions can affect it) or ``WINDOW`` (every stored session in
    ``[window_start, session]`` was consumed and its lineage is captured).
    """
    if len(item) == 3:
        role, symbol, session = item
        window_start, coverage = None, COVERAGE_POINT
    elif len(item) == 4:
        role, symbol, session, window_start = item
        coverage = COVERAGE_POINT
    elif len(item) == 5:
        role, symbol, session, window_start, coverage = item
    else:  # pragma: no cover - programmer error
        raise MarketBindingError("dependency must be (role, symbol, session[, window_start[, coverage]])")
    role = str(role).strip().upper()
    symbol = str(symbol).strip().upper()
    session = str(session).strip()[:10]
    window = None if window_start is None else str(window_start).strip()[:10]
    coverage = str(coverage).strip().upper()
    if not role or not symbol or len(session) != 10:
        raise MarketBindingError(f"invalid dependency: {item!r}")
    if window is not None and window > session:
        raise MarketBindingError("dependency window must not start after its session")
    if coverage not in {COVERAGE_POINT, COVERAGE_WINDOW, COVERAGE_UNPROVEN}:
        raise MarketBindingError(f"unknown dependency coverage: {coverage!r}")
    if coverage == COVERAGE_WINDOW and window is None:
        raise MarketBindingError("a WINDOW dependency needs a window start")
    return role, symbol, session, window, coverage


def _reduce_window_origin(counts: Mapping[str, int]) -> str:
    present = {origin for origin, count in counts.items() if count}
    for origin in _WINDOW_ORIGIN_ORDER:
        if origin in present:
            return origin
    return "ABSENT"  # an empty window proves nothing was consumed: never "verified"


class MarketBindingSession:
    """Pins the dataset version at the decision point and binds evidence to it."""

    def __init__(
        self,
        market_database_path: str | Path | None = None,
        *,
        log_path: str | Path | None = None,
        expected_dataset_version_id: str | None = None,
    ) -> None:
        self.market_path = resolve_market_database_path(market_database_path)
        self.log_state = LOG_BOUND
        self.log_error: str | None = None
        self._log = None
        self.dataset: dict[str, Any] | None = None
        try:
            resolved = resolve_observation_log_path(self.market_path, log_path)
        except Exception as error:  # pragma: no cover - defensive
            self.log_state, self.log_error = LOG_UNREADABLE, str(error)
            resolved = None
        if resolved is not None and not resolved.is_file():
            self.log_state = LOG_MISSING
        elif resolved is not None:
            try:
                self._log = open_observation_log(
                    self.market_path, resolved, readonly=True, require_baseline=True
                )
                self.dataset = self._dataset_identity()
            except Exception as error:
                self._log = None
                self.log_state, self.log_error = LOG_UNREADABLE, f"{type(error).__name__}: {error}"
        if (
            expected_dataset_version_id is not None
            and self.dataset_version_id != expected_dataset_version_id
        ):
            raise StaleDatasetVersionError(
                f"dataset version {self.dataset_version_id!r} is not the decision-time "
                f"version {expected_dataset_version_id!r}"
            )

    @property
    def dataset_version_id(self) -> str | None:
        return None if self.dataset is None else str(self.dataset["dataset_version_id"])

    def _dataset_identity(self) -> dict[str, Any]:
        identity = self._log.dataset_version_identity()
        return {
            "dataset_version_id": str(identity["dataset_version_id"]),
            "baseline_id": str(identity["baseline_id"]),
            "baseline_content_sha256": str(identity["baseline_content_sha256"]),
            "provenance_label": str(identity["provenance_label"]),
            "consumable": bool(identity["consumable"]),
            "unresolved_block_symbols": list(identity["unresolved_block_symbols"]),
            "pending_applications": list(identity["pending_applications"]),
        }

    def first_session(self, symbol: str) -> str | None:
        """Earliest stored session of ``symbol`` (the start of its full consumed history)."""
        import sqlite3 as _sqlite3

        connection = _sqlite3.connect(f"{self.market_path.as_uri()}?mode=ro", uri=True, timeout=30)
        try:
            row = connection.execute(
                "SELECT MIN(substr(time,1,10)) FROM prices WHERE symbol=?", (str(symbol).strip().upper(),)
            ).fetchone()
        finally:
            connection.close()
        return None if row is None or row[0] is None else str(row[0])

    def _assert_pinned(self, stage: str) -> None:
        current = self._dataset_identity()
        if current["dataset_version_id"] != self.dataset_version_id:
            raise StaleDatasetVersionError(
                f"dataset version advanced {stage} "
                f"({self.dataset_version_id} -> {current['dataset_version_id']}); "
                "refusing to bind a mix of versions or a later ('latest') version"
            )

    def bind(
        self,
        subject_kind: str,
        subject_ref: str,
        dependencies: Iterable[Sequence[Any]],
        *,
        context: Mapping[str, str] | None = None,
        lineage: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build an immutable binding that describes ONE coherent dataset state.

        Serialization model (R1/R2 versions are append-only and totally ordered):
        the version is read before the dependency reads *and* re-read after them.
        Equal reads prove that no application finalized in between, so every
        dependency was read from the pinned state.  A moved version, or a consumed
        session / window claimed by an open application, raises
        :class:`StaleDatasetVersionError` and nothing is bound.  There is no retry
        against the new version: the caller must re-run its own decision.
        """
        kind, ref = str(subject_kind).strip().upper(), str(subject_ref).strip()
        if not kind or not ref:
            raise MarketBindingError("a binding needs a subject kind and reference")
        normalized = sorted(
            {_normalize_dependency(item) for item in dependencies},
            key=lambda d: (d[0], d[1], d[2], d[3] or "", d[4]),
        )
        recorded: list[dict[str, Any]] = []
        if self._log is not None:
            self._assert_pinned("after the decision point")
        for role, symbol, session, window, coverage in normalized:
            record: dict[str, Any] = {
                "role": role,
                "symbol": symbol,
                "session": session,
                "window_start": window,
                "coverage": coverage,
            }
            if coverage == COVERAGE_UNPROVEN:
                record.update(origin="UNPROVEN_SOURCE", observation_id=None, version_id=None)
            elif self._log is None:
                record.update(origin="NO_LOG", observation_id=None, version_id=None)
            elif coverage == COVERAGE_WINDOW:
                summary = self._log.window_provenance(symbol, str(window), session)
                origin = _reduce_window_origin(summary["origin_counts"])
                record.update(
                    origin=origin,
                    observation_id=None,
                    version_id=None,
                    lineage={
                        "session_count": summary["session_count"],
                        "origin_counts": summary["origin_counts"],
                        "first_session": summary["first_session"],
                        "last_session": summary["last_session"],
                        "digest": summary["digest"],
                    },
                )
            else:
                provenance = self._log.session_provenance(symbol, session)
                observation_id = provenance.get("observation_id")
                version_id = provenance.get("version_id")
                record.update(
                    origin=str(provenance["origin"]),
                    observation_id=None if observation_id is None else str(observation_id),
                    version_id=None if version_id is None else str(version_id),
                )
            if record["origin"] == "PENDING_APPLICATION":
                raise PendingApplicationError(
                    f"{role} {symbol} {window or session}..{session} is claimed by an open application; "
                    "not bound"
                )
            recorded.append(record)
        if self._log is not None:
            self._assert_pinned("while its provenance was being read")
            for item in recorded:  # an application may have opened after the read
                if item["coverage"] == COVERAGE_UNPROVEN:
                    continue
                lo = item["window_start"] if item["coverage"] == COVERAGE_WINDOW else item["session"]
                for intent in self._log.open_intents(item["symbol"]):
                    if any(str(lo) <= str(x) <= item["session"] for x in intent["sessions"]):
                        raise PendingApplicationError(
                            f"{item['role']} {item['symbol']} is claimed by an open application; not bound"
                        )
        payload: dict[str, Any] = {
            "contract": CONTRACT,
            "version": VERSION,
            "subject": {"kind": kind, "ref": ref},
            "log_state": self.log_state,
            "log_error": self.log_error,
            "dataset": self.dataset,
            "dependencies": recorded,
            "context": {str(k): str(v) for k, v in sorted((context or {}).items())},
        }
        if lineage:
            payload["lineage"] = json.loads(_canonical(dict(lineage)))
        payload["binding_identity"] = binding_identity(payload)
        payload["bound_at_utc"] = _utc_now()
        return payload


@dataclass(frozen=True, slots=True)
class QualificationResult:
    state: str
    reasons: tuple[str, ...]
    basis_identity: str
    #: qualification of the evidence IGNORING unresolved-revision blocks (``None`` when not
    #: computed, e.g. for stored review verdicts).  Only an ``underlying_base_state`` of
    #: ``PROVENANCE_VERIFIED`` lets an operator ACCEPT override an unresolved-revision block.
    underlying_base_state: str | None = None

    @property
    def qualified(self) -> bool:
        return self.state in QUALIFIED_STATES

    @property
    def excluded(self) -> bool:
        return self.state in EXCLUDED_STATES

    @property
    def descriptive_only(self) -> bool:
        return self.state in DESCRIPTIVE_ONLY_STATES

    @property
    def legacy_label(self) -> str | None:
        return LEGACY_LABEL if self.state in DESCRIPTIVE_ONLY_STATES else None


def _basis(state: str, reasons: Iterable[str], identities: Iterable[str] = ()) -> str:
    return _sha256(
        {"state": state, "reasons": sorted(set(reasons)), "bindings": sorted(set(identities))}
    )[:32]


_REVIEW_ACCEPTED = "REVIEWED_ACCEPTED"
_REVIEW_REJECTED = "REVIEWED_REJECTED"
_REVIEW_SUPERSEDED = "REVIEW_REJECTION_SUPERSEDED"
_REVIEW_ACKNOWLEDGED = "REVIEW_ACKNOWLEDGED"
#: only an unresolved revision is reviewable ...
ACCEPTABLE_STATES = frozenset({Qualification.QUARANTINED_UNRESOLVED_REVISION.value})
#: ... and only when the evidence, ignoring that unresolved-revision block, is fully
#: ``PROVENANCE_VERIFIED``.  Missing, incompatible, legacy-bound and legacy-unbound
#: evidence can never be turned into qualified evidence by an operator ACCEPT.
ACCEPT_REQUIRED_BASE_STATE = Qualification.PROVENANCE_VERIFIED.value


def acceptable(result: QualificationResult) -> bool:
    """May an operator ACCEPT override this result's quarantine?"""
    return (
        result.state in ACCEPTABLE_STATES
        and result.underlying_base_state == ACCEPT_REQUIRED_BASE_STATE
    )


#: pseudo log state of a series whose chronological chain is incomplete (never ``LOG_BOUND``)
INCOMPLETE_CHAIN_STATE = "INCOMPLETE_OBSERVATION_CHAIN"
#: consumed benchmark/regime history of a paper entry (the signal path reads VNINDEX)
REGIME_BENCHMARK_SYMBOL = "VNINDEX"


def trade_leg_symbols(symbol: Any) -> frozenset[str]:
    """Symbols whose dependencies a trade leg is evaluated against: the traded symbol and the
    VNINDEX regime / relative-strength history its entry signal consumed.  An unrelated
    symbol's revision never quarantines the trade; a VNINDEX revision inside the consumed
    window does."""
    return frozenset({str(symbol).strip().upper(), REGIME_BENCHMARK_SYMBOL})


def leg_binding(leg: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Rebuild an evaluable pseudo-binding from a compact stored leg copy (or ``None``)."""
    if leg is None:
        return None
    return {
        "log_state": leg.get("log_state", LOG_BOUND),
        "dataset": leg.get("dataset"),
        "dependencies": list(leg.get("dependencies", ())),
        "source_binding_identity": leg.get("binding_identity"),
    }


def compact_leg(binding: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Compact, immutable copy of a trade-leg binding (header identity + dependencies only)."""
    if binding is None:
        return None
    dataset = binding.get("dataset") or {}
    return {
        "binding_identity": binding.get("binding_identity"),
        "log_state": binding.get("log_state"),
        "dataset": {
            "dataset_version_id": dataset.get("dataset_version_id"),
            "baseline_id": dataset.get("baseline_id"),
        }
        if binding.get("dataset")
        else None,
        "dependencies": [dict(item) for item in binding.get("dependencies", ())],
    }


class EvidenceQualifier:
    """Qualifies bindings against the CURRENT log state, re-validated on every call.

    The log is append-only and exposes a cheap monotonic ``state_signature``; each
    :meth:`qualify` compares it with the signature of the cached snapshot and reloads
    blocks / baseline / session caches when anything changed, so a qualifier that is
    kept and reused (reports, exports) can never evaluate against stale blocks.
    """

    def __init__(self, market_database_path: str | Path | None = None, *, log_path: str | Path | None = None) -> None:
        self.market_path = resolve_market_database_path(market_database_path)
        self._explicit_log_path = log_path
        self.available = False
        self.error: str | None = "NO_OBSERVATION_LOG"
        self.baseline_id: str | None = None
        self.blocks: list[dict[str, Any]] = []
        self._log = None
        self._signature: tuple[int, ...] | None = None
        self._origins: dict[tuple[str, str], dict[str, Any]] = {}
        self._windows: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.refresh()

    # ------------------------------------------------------------------ state
    def refresh(self) -> None:
        """Re-open the log and reload the current state unconditionally."""
        self._origins.clear()
        self._windows.clear()
        self._log, self._signature = None, None
        self.available, self.error, self.baseline_id, self.blocks = False, "NO_OBSERVATION_LOG", None, []
        try:
            resolved = resolve_observation_log_path(self.market_path, self._explicit_log_path)
            if resolved.is_file():
                self._log = open_observation_log(
                    self.market_path, resolved, readonly=True, require_baseline=True
                )
                self.baseline_id = str(self._log.get_baseline()["baseline_id"])
                self._signature = self._log.state_signature()
                self.blocks = list(self._log.unresolved_blocks())
                self.available, self.error = True, None
        except Exception as error:
            self._log = None
            self.available, self.error = False, f"{type(error).__name__}: {error}"

    def _revalidate(self) -> None:
        if self._log is None:
            self.refresh()  # a log created after construction becomes visible
            return
        try:
            current = self._log.state_signature()
        except Exception:
            self.refresh()
            return
        if current != self._signature:
            self.refresh()

    def _current(self, symbol: str, session: str) -> dict[str, Any]:
        key = (symbol, session)
        if key not in self._origins:
            self._origins[key] = self._log.session_provenance(symbol, session)
        return self._origins[key]

    def _window(self, symbol: str, start: str, session: str) -> dict[str, Any]:
        key = (symbol, start, session)
        if key not in self._windows:
            self._windows[key] = self._log.window_provenance(symbol, start, session)
        return self._windows[key]

    # ------------------------------------------------------------------ qualify
    def _evaluate_binding(
        self, binding: Mapping[str, Any], symbols: frozenset[str] | None
    ) -> tuple[str, list[str], str]:
        """Return ``(state, reasons, underlying_base_state)``.

        ``underlying_base_state`` is the qualification with unresolved-revision blocks
        ignored, so the review layer can tell a verified-but-blocked binding (reviewable)
        from legacy / missing / incompatible provenance (never reviewable into qualified).
        """
        reasons: list[str] = []
        states: set[str] = set()
        blocked = False
        if str(binding.get("log_state")) != LOG_BOUND:
            reasons.append(f"LOG_STATE:{binding.get('log_state')}")
            missing = Qualification.QUARANTINED_MISSING_PROVENANCE.value
            return missing, reasons, missing
        stored_identity = binding.get("binding_identity")
        if stored_identity is not None and stored_identity != binding_identity(binding):
            reasons.append("BINDING_IDENTITY_MISMATCH")
            missing = Qualification.QUARANTINED_MISSING_PROVENANCE.value
            return missing, reasons, missing
        dependencies = [
            dep
            for dep in binding.get("dependencies", ())
            if symbols is None or str(dep["symbol"]) in symbols
        ]
        for dep in dependencies:
            if str(dep["origin"]) in MISSING_ORIGINS:
                reasons.append(f"DEPENDENCY_ORIGIN:{dep['origin']}:{dep['symbol']}:{dep['session']}:{dep['role']}")
                states.add(Qualification.QUARANTINED_MISSING_PROVENANCE.value)
        if not self.available:
            reasons.append(f"CURRENT_LOG_UNAVAILABLE:{self.error}")
            states.add(Qualification.QUARANTINED_MISSING_PROVENANCE.value)
        else:
            dataset = binding.get("dataset") or {}
            if str(dataset.get("baseline_id")) != str(self.baseline_id):
                reasons.append(f"BASELINE_CHANGED:{dataset.get('baseline_id')}->{self.baseline_id}")
                states.add(Qualification.QUARANTINED_INCOMPATIBLE_BASIS.value)
            else:
                for dep in dependencies:
                    if str(dep["origin"]) in MISSING_ORIGINS:
                        continue
                    if str(dep.get("coverage", COVERAGE_POINT)) == COVERAGE_WINDOW:
                        now = self._window(str(dep["symbol"]), str(dep["window_start"]), str(dep["session"]))
                        if now["digest"] != (dep.get("lineage") or {}).get("digest"):
                            reasons.append(
                                f"WINDOW_LINEAGE_DRIFT:{dep['symbol']}:{dep['window_start']}..{dep['session']}:{dep['role']}"
                            )
                            states.add(Qualification.QUARANTINED_INCOMPATIBLE_BASIS.value)
                        continue
                    now = self._current(str(dep["symbol"]), str(dep["session"]))
                    if (
                        str(now["origin"]) != str(dep["origin"])
                        or (now.get("observation_id") or None) != (dep.get("observation_id") or None)
                        or (now.get("version_id") or None) != (dep.get("version_id") or None)
                    ):
                        reasons.append(
                            f"SESSION_PROVENANCE_DRIFT:{dep['symbol']}:{dep['session']}:{dep['role']}"
                        )
                        states.add(Qualification.QUARANTINED_INCOMPATIBLE_BASIS.value)
            for block in self.blocks:
                for dep in dependencies:
                    if str(dep["symbol"]) != str(block["symbol"]):
                        continue
                    affected = [str(item) for item in block.get("affected_sessions", ())]
                    start = dep.get("window_start") or dep["session"]
                    hit = (not affected) or any(str(start) <= item <= str(dep["session"]) for item in affected)
                    if hit:
                        reasons.append(
                            f"UNRESOLVED_BLOCK:{block['block_id']}:{block['symbol']}:{dep['session']}:{dep['role']}"
                        )
                        blocked = True
        # underlying base state: everything except the unresolved-revision block
        if not states:
            if any(str(dep["origin"]) == BASELINE_ORIGIN for dep in dependencies):
                states.add(Qualification.BOUND_LEGACY_INPUT.value)
            else:
                states.add(Qualification.PROVENANCE_VERIFIED.value)
        base_state = _worst(states)
        if base_state == Qualification.BOUND_LEGACY_INPUT.value:
            reasons.append("DEPENDENCY_ON_LEGACY_BASELINE_SESSION")
        if blocked:
            states.add(Qualification.QUARANTINED_UNRESOLVED_REVISION.value)
        return _worst(states), reasons, base_state

    def qualify(
        self,
        bindings: Sequence[tuple[Mapping[str, Any] | None, Iterable[str] | None]],
        *,
        reviews: Sequence[Mapping[str, Any]] = (),
    ) -> QualificationResult:
        """Qualify evidence that depends on one or more bindings (against the current log).

        ``bindings`` is a sequence of ``(binding_or_None, symbol_filter_or_None)``.
        A ``None`` binding is legacy evidence (never bound, never verified).  A
        symbol filter restricts that binding to the deps of those symbols, so an
        unrelated symbol's revision does not quarantine this evidence.

        ``reviews`` are the evidence's review events in append order.  A rejection is
        sticky until a dedicated supersede event; an acceptance counts only for an
        unresolved-revision quarantine, only for the exact binding identities and
        reasons it was recorded against, and never for missing provenance.
        """
        self._revalidate()
        states: set[str] = set()
        base_states: set[str] = set()
        reasons: list[str] = []
        identities: list[str] = []
        for binding, symbols in bindings:
            if binding is None:
                states.add(Qualification.LEGACY_UNBOUND.value)
                base_states.add(Qualification.LEGACY_UNBOUND.value)
                reasons.append("NO_MARKET_DATA_BINDING")
                continue
            identity = binding.get("binding_identity") or binding.get("source_binding_identity")
            if identity:
                identities.append(str(identity))
            wanted = None if symbols is None else frozenset(str(s).strip().upper() for s in symbols)
            state, why, base = self._evaluate_binding(binding, wanted)
            states.add(state)
            base_states.add(base)
            reasons.extend(why)
        computed = _worst(states or {Qualification.LEGACY_UNBOUND.value})
        base_state = _worst(base_states or {Qualification.LEGACY_UNBOUND.value})
        basis = _basis(computed, reasons, identities)
        rejected = False
        accepted_basis: str | None = None
        for event in reviews:
            kind = event.get("state")
            if kind == _REVIEW_REJECTED:
                rejected, accepted_basis = True, None
            elif kind == _REVIEW_SUPERSEDED:
                rejected = False
            elif kind == _REVIEW_ACCEPTED and not rejected:
                accepted_basis = event.get("basis_identity")
        if rejected:
            return QualificationResult(
                Qualification.REVIEWED_REJECTED.value, tuple(sorted(set(reasons))), basis, base_state
            )
        if (
            accepted_basis is not None
            and computed in ACCEPTABLE_STATES
            and base_state == ACCEPT_REQUIRED_BASE_STATE
            and accepted_basis == basis
        ):
            return QualificationResult(
                Qualification.REVIEWED_ACCEPTED.value, tuple(sorted(set(reasons))), basis, base_state
            )
        return QualificationResult(computed, tuple(sorted(set(reasons))), basis, base_state)

    def qualify_observation_series(
        self,
        ordered_bindings: Sequence[Mapping[str, Any] | None],
        *,
        reviews: Sequence[Sequence[Mapping[str, Any]]] | None = None,
    ) -> list[QualificationResult]:
        """Qualify a chronological series of paper observations (oldest first).

        Each observation's equity includes cumulative realized PnL and the cost basis
        of open positions, so it depends on the entry/exit legs of every trade that
        contributed (copied compactly into each observation's ``lineage``) and on the
        entry legs of its open positions.  Trades with no tracked provenance, and
        observations captured before R3, make the observation ``LEGACY_UNBOUND``.
        """
        cumulative: list[Mapping[str, Any]] = []
        results: list[QualificationResult] = []
        for index, binding in enumerate(ordered_bindings):
            event_list = () if reviews is None else reviews[index]
            if binding is None:
                results.append(self.qualify(((None, None),), reviews=event_list))
                continue
            lineage = binding.get("lineage") or {}
            cumulative.extend(lineage.get("trades_added", ()))
            parts: list[tuple[Mapping[str, Any] | None, Iterable[str] | None]] = [(binding, None)]
            for trade in cumulative:
                for leg in ("entry", "exit"):
                    parts.append((leg_binding(trade.get(leg)), trade_leg_symbols(trade["symbol"])))
            for position in lineage.get("open_entries", ()):
                parts.append((leg_binding(position.get("entry")), trade_leg_symbols(position["symbol"])))
            if int(lineage.get("closed_trade_count", 0)) > int(lineage.get("tracked_trade_count", 0)):
                parts.append((None, None))  # realized PnL includes trades with no provenance
            if int(lineage.get("tracked_trade_count", 0)) > len(cumulative):
                # The observation says more tracked trade lineages contributed to its realized
                # PnL than this series contains: the caller supplied an incomplete (filtered or
                # truncated) chain.  Never infer the missing trades -- fail closed.
                parts.append(
                    (
                        {"log_state": INCOMPLETE_CHAIN_STATE, "dependencies": []},
                        None,
                    )
                )
            results.append(self.qualify(parts, reviews=event_list))
        return results


def _worst(states: Iterable[str]) -> str:
    present = set(states)
    for state in _PRECEDENCE:
        if state in present:
            return state
    return Qualification.LEGACY_UNBOUND.value


# ----------------------------------------------------------------------------
# Storage helpers (same SQLite file as the evidence)
# ----------------------------------------------------------------------------
def _safe_prefix(prefix: str) -> str:
    if not prefix.isidentifier():
        raise ValueError("invalid table prefix")
    return prefix


def ensure_binding_schema(connection: sqlite3.Connection, prefix: str) -> None:
    """Additive, idempotent creation of ``<prefix>_market_bindings`` and events."""
    prefix = _safe_prefix(prefix)
    bindings, events = f"{prefix}_market_bindings", f"{prefix}_qualification_events"
    connection.execute(
        f"""CREATE TABLE IF NOT EXISTS {bindings} (
            binding_identity TEXT PRIMARY KEY,
            subject_kind TEXT NOT NULL,
            subject_ref TEXT NOT NULL,
            dataset_version_id TEXT,
            log_state TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            bound_at_utc TEXT NOT NULL,
            UNIQUE(subject_kind, subject_ref)
        )"""
    )
    connection.execute(
        f"""CREATE TABLE IF NOT EXISTS {events} (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_kind TEXT NOT NULL,
            subject_ref TEXT NOT NULL,
            state TEXT NOT NULL,
            reasons_json TEXT NOT NULL,
            basis_identity TEXT NOT NULL,
            source TEXT NOT NULL,
            reviewer TEXT,
            note TEXT,
            recorded_at_utc TEXT NOT NULL
        )"""
    )
    for table, collision in (
        (
            bindings,
            f"EXISTS (SELECT 1 FROM {bindings} WHERE binding_identity = NEW.binding_identity "
            f"OR (subject_kind = NEW.subject_kind AND subject_ref = NEW.subject_ref))",
        ),
        (
            events,
            f"NEW.event_id IS NOT NULL AND EXISTS (SELECT 1 FROM {events} WHERE event_id = NEW.event_id)",
        ),
    ):
        connection.execute(
            f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, 'append-only table: {table}'); END"
        )
        connection.execute(
            f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, 'append-only table: {table}'); END"
        )
        # INSERT OR REPLACE resolves a key conflict by deleting the old row, which does not
        # fire DELETE triggers unless recursive_triggers is on.  A BEFORE INSERT trigger fires
        # first, independent of any PRAGMA, and aborts any insert that would collide.
        connection.execute(
            f"CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table} "
            f"WHEN {collision} "
            f"BEGIN SELECT RAISE(ABORT, 'append-only table (no replace): {table}'); END"
        )


def insert_binding(connection: sqlite3.Connection, prefix: str, binding: Mapping[str, Any]) -> bool:
    """Insert one immutable binding inside the caller's transaction (idempotent).

    Returns ``True`` when a row was written.  An identical binding is a no-op; a
    *different* binding for the same subject is refused (never rebound).
    """
    table = f"{_safe_prefix(prefix)}_market_bindings"
    subject = binding["subject"]
    if binding.get("binding_identity") != binding_identity(binding):
        raise MarketBindingError("binding identity does not match its content")
    existing = connection.execute(
        f"SELECT binding_identity FROM {table} WHERE subject_kind=? AND subject_ref=?",
        (subject["kind"], subject["ref"]),
    ).fetchone()
    if existing is not None:
        if existing[0] == binding["binding_identity"]:
            return False
        raise MarketBindingError(
            f"conflicting immutable market-data binding for {subject['kind']}:{subject['ref']}"
        )
    dataset = binding.get("dataset") or {}
    connection.execute(
        f"INSERT INTO {table}(binding_identity,subject_kind,subject_ref,dataset_version_id,log_state,"
        "payload_json,bound_at_utc) VALUES (?,?,?,?,?,?,?)",
        (
            binding["binding_identity"],
            subject["kind"],
            subject["ref"],
            dataset.get("dataset_version_id"),
            binding["log_state"],
            _canonical(dict(binding)),
            binding["bound_at_utc"],
        ),
    )
    return True


def read_bindings(connection: sqlite3.Connection, prefix: str) -> dict[tuple[str, str], dict[str, Any]]:
    """All bindings keyed by ``(kind, ref)``; an old database without the table is empty."""
    table = f"{_safe_prefix(prefix)}_market_bindings"
    try:
        rows = connection.execute(f"SELECT payload_json FROM {table}").fetchall()
    except sqlite3.OperationalError as error:
        if "no such table" in str(error).lower():
            return {}
        raise
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        payload = json.loads(row[0])
        result[(payload["subject"]["kind"], payload["subject"]["ref"])] = payload
    return result


def read_qualification_events(
    connection: sqlite3.Connection, prefix: str
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    table = f"{_safe_prefix(prefix)}_qualification_events"
    try:
        cursor = connection.execute(
            f"SELECT event_id,subject_kind,subject_ref,state,reasons_json,basis_identity,source,reviewer,note,"
            f"recorded_at_utc FROM {table} ORDER BY event_id"
        )
        columns = [item[0] for item in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    except sqlite3.OperationalError as error:
        if "no such table" in str(error).lower():
            return {}
        raise
    result: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        row["reasons"] = json.loads(row.pop("reasons_json"))
        result.setdefault((row["subject_kind"], row["subject_ref"]), []).append(row)
    return result


def append_qualification_event(
    connection: sqlite3.Connection,
    prefix: str,
    *,
    subject_kind: str,
    subject_ref: str,
    result: QualificationResult,
    source: str,
    reviewer: str | None = None,
    note: str | None = None,
) -> bool:
    """Append one qualification event; an unchanged automatic verdict is a no-op."""
    table = f"{_safe_prefix(prefix)}_qualification_events"
    ensure_binding_schema(connection, prefix)
    if source == "AUTOMATIC":
        last = connection.execute(
            f"SELECT state,basis_identity FROM {table} WHERE subject_kind=? AND subject_ref=? "
            "AND source='AUTOMATIC' ORDER BY event_id DESC LIMIT 1",
            (subject_kind, subject_ref),
        ).fetchone()
        if last is not None and last[0] == result.state and last[1] == result.basis_identity:
            return False
    connection.execute(
        f"INSERT INTO {table}(subject_kind,subject_ref,state,reasons_json,basis_identity,source,reviewer,"
        "note,recorded_at_utc) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            subject_kind,
            subject_ref,
            result.state,
            _canonical(list(result.reasons)),
            result.basis_identity,
            source,
            reviewer,
            note,
            _utc_now(),
        ),
    )
    return True


def record_review(
    connection: sqlite3.Connection,
    prefix: str,
    *,
    subject_kind: str,
    subject_ref: str,
    decision: str,
    reviewer: str,
    reason: str,
    current: QualificationResult,
) -> bool:
    """Append a reviewed decision bound to the current verdict basis.

    * ``ACCEPT`` is allowed only for an unresolved-revision quarantine, and counts only
      while that exact basis (same binding identities and reasons) still holds.  Missing
      provenance, an incompatible basis and legacy evidence cannot be accepted into
      qualified evidence by an operator.
    * ``REJECT`` is sticky: a later generic ``ACCEPT`` never clears it.
    * ``SUPERSEDE_REJECT`` is the only way to lift a rejection (it needs an existing one).
    * ``ACKNOWLEDGE`` records that an operator saw the condition and never changes
      qualification.
    """
    reviewer, reason = str(reviewer).strip(), str(reason).strip()
    if not reviewer or not reason:
        raise ValueError("a reviewed decision requires a reviewer and a reason")
    decision = str(decision).strip().upper()
    if decision == "ACCEPT":
        if not acceptable(current):
            raise MarketBindingError(
                "only an unresolved-revision quarantine over otherwise PROVENANCE_VERIFIED "
                f"evidence can be reviewed-accepted; {current.state} (underlying "
                f"{current.underlying_base_state}) cannot be accepted by an operator"
            )
        state = _REVIEW_ACCEPTED
    elif decision == "REJECT":
        state = _REVIEW_REJECTED
    elif decision == "SUPERSEDE_REJECT":
        existing = read_qualification_events(connection, prefix).get((subject_kind, subject_ref), [])
        active = False
        for event in existing:
            if event["state"] == _REVIEW_REJECTED:
                active = True
            elif event["state"] == _REVIEW_SUPERSEDED:
                active = False
        if not active:
            raise MarketBindingError("there is no active rejection to supersede")
        state = _REVIEW_SUPERSEDED
    elif decision == "ACKNOWLEDGE":
        state = _REVIEW_ACKNOWLEDGED
    else:
        raise ValueError("decision must be ACCEPT, REJECT, SUPERSEDE_REJECT or ACKNOWLEDGE")
    verdict = QualificationResult(
        state, current.reasons, current.basis_identity, current.underlying_base_state
    )
    return append_qualification_event(
        connection,
        prefix,
        subject_kind=subject_kind,
        subject_ref=subject_ref,
        result=verdict,
        source="REVIEW",
        reviewer=reviewer,
        note=reason,
    )


def sync_qualification_events(
    connection: sqlite3.Connection,
    prefix: str,
    qualifier: EvidenceQualifier,
    subjects: Iterable[tuple[str, str, Sequence[tuple[Mapping[str, Any] | None, Iterable[str] | None]]]],
) -> int:
    """Append (idempotently) the automatic verdict of each subject; returns events written.

    Evidence rows are never touched.  A quarantine and its later lifting are
    both appended, so the history of the verdict is preserved.
    """
    ensure_binding_schema(connection, prefix)
    written = 0
    for kind, ref, bindings in subjects:
        computed = qualifier.qualify(bindings)  # the automatic verdict never folds in reviews
        if append_qualification_event(
            connection, prefix, subject_kind=kind, subject_ref=ref, result=computed, source="AUTOMATIC"
        ):
            written += 1
    return written


def summarize(results: Iterable[QualificationResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.state] = counts.get(result.state, 0) + 1
    return dict(sorted(counts.items()))


__all__ = (
    "ACCEPTABLE_STATES",
    "ACCEPT_REQUIRED_BASE_STATE",
    "acceptable",
    "INCOMPLETE_CHAIN_STATE",
    "REGIME_BENCHMARK_SYMBOL",
    "trade_leg_symbols",
    "BASELINE_ORIGIN",
    "CONTRACT",
    "COVERAGE_POINT",
    "COVERAGE_UNPROVEN",
    "COVERAGE_WINDOW",
    "DESCRIPTIVE_ONLY_STATES",
    "EXCLUDED_STATES",
    "EvidenceQualifier",
    "LEGACY_LABEL",
    "LOG_BOUND",
    "LOG_MISSING",
    "LOG_UNREADABLE",
    "MarketBindingError",
    "MarketBindingSession",
    "PendingApplicationError",
    "ProvenanceQuarantineError",
    "QUALIFIED_STATES",
    "QUARANTINED_STATES",
    "Qualification",
    "QualificationResult",
    "StaleDatasetVersionError",
    "append_qualification_event",
    "binding_identity",
    "compact_leg",
    "ensure_binding_schema",
    "insert_binding",
    "leg_binding",
    "read_bindings",
    "read_qualification_events",
    "record_review",
    "summarize",
    "sync_qualification_events",
)
