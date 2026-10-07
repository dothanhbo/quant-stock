from __future__ import annotations

"""Read-only presentation models for prospective evidence.

Paper portfolio observations and Forward protocol outcomes intentionally remain
separate populations.  This module projects persisted evidence only; it never
captures, reconciles, matures, or reconstructs a missing observation.
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from statistics import fmean
from typing import Mapping

from core.evidence_market_binding import (
    EXCLUDED_STATES,
    QUALIFIED_STATES,
    EvidenceQualifier,
    Qualification,
)
from quantctl.registry import PROJECT_ROOT
from quantctl.state import (
    ForwardProtocolSnapshot,
    PaperStoreSnapshot,
    inspect_forward_system,
    inspect_paper_system,
)
from quantlab.evidence import (
    ProspectivePortfolioEvidenceLedger,
    paper_store_identity,
    read_paper_account_epoch,
)
from quantlab.forward.contracts import MaturityStatus, OutcomeAvailability
from quantlab.forward.ledger import ForwardValidationLedger


DEFAULT_MAX_PRESENTED_RECORDS = 500


class ForwardEvidenceState(str, Enum):
    AVAILABLE = "AVAILABLE"
    NOT_STARTED = "NOT_STARTED"
    UNAVAILABLE = "UNAVAILABLE"
    SCHEMA_INCOMPATIBLE = "SCHEMA_INCOMPATIBLE"
    SAMPLE_IMMATURE = "SAMPLE_IMMATURE"
    CONTINUITY_GAP = "CONTINUITY_GAP"


@dataclass(frozen=True, slots=True)
class PaperEvidencePoint:
    session: str
    equity: float
    cash: float
    position_value: float
    realized_pnl: float
    unrealized_pnl: float
    drawdown_pct: float | None
    daily_pnl: float | None
    daily_return_pct: float | None
    record_identity: str
    # R3: market-data qualification of this observation (LEGACY_UNBOUND for
    # observations captured before R3; never inferred or back-filled).
    market_data_qualification: str = "LEGACY_UNBOUND"


@dataclass(frozen=True, slots=True)
class PaperExecutionProvenance:
    fill_count: int
    exit_count: int
    total_commission: float | None
    total_modeled_slippage: float | None
    execution_classifications: tuple[str, ...]
    spread_or_market_impact_observation_count: int


@dataclass(frozen=True, slots=True)
class PaperEvidencePresentation:
    state: ForwardEvidenceState
    detail: str
    strategy_identity: str | None
    store_id: str | None
    store_display_name: str | None
    store_identity: str | None
    account_epoch: str | None
    continuity_state: str
    observation_count: int | None
    latest_session: str | None
    points: tuple[PaperEvidencePoint, ...]
    execution: PaperExecutionProvenance | None
    evidence_database_path: Path
    paper_database_path: Path | None
    displayed_record_limit: int
    records_truncated: bool
    warnings: tuple[str, ...]

    @property
    def sessions(self) -> tuple[str, ...]:
        return tuple(item.session for item in self.points)


@dataclass(frozen=True, slots=True)
class ForwardOutcomeSummary:
    horizon_sessions: int
    pending_maturity_count: int
    maturity_count: int
    unavailable_maturity_count: int
    outcome_observation_count: int
    available_outcome_count: int
    mean_stock_forward_return_pct: float | None
    mean_benchmark_forward_return_pct: float | None
    mean_excess_forward_return_pct_points: float | None
    # R3 market-data provenance (counts are over AVAILABLE outcomes).  The three
    # means above are DESCRIPTIVE: they include legacy-unbound / legacy-basis
    # outcomes (labelled by the counts below) and exclude quarantined / rejected
    # ones.  Only ``qualified_*`` fields may be cited as provenance-verified.
    qualified_outcome_count: int = 0
    legacy_unbound_outcome_count: int = 0
    bound_legacy_input_outcome_count: int = 0
    quarantined_outcome_count: int = 0
    reviewed_rejected_outcome_count: int = 0
    qualified_mean_excess_forward_return_pct_points: float | None = None


@dataclass(frozen=True, slots=True)
class ForwardProtocolEvidencePresentation:
    state: ForwardEvidenceState
    detail: str
    protocol: ForwardProtocolSnapshot
    formation_count: int
    displayed_formation_count: int
    pending_maturity_count: int
    matured_maturity_count: int
    unavailable_maturity_count: int
    outcome_count: int
    latest_formation_session: str | None
    formation_sessions: tuple[str, ...]
    missing_formation_sessions: tuple[str, ...]
    summaries: tuple[ForwardOutcomeSummary, ...]
    database_path: Path
    displayed_record_limit: int
    records_truncated: bool
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ForwardEvidenceCatalog:
    paper: PaperEvidencePresentation
    protocols: tuple[ForwardProtocolEvidencePresentation, ...]
    forward_database_path: Path
    forward_state: ForwardEvidenceState
    forward_detail: str


def _in_range(value: str, start_date: str | None, end_date: str | None) -> bool:
    return (start_date is None or value >= start_date) and (end_date is None or value <= end_date)


def _paper_state(store: PaperStoreSnapshot | None, *, has_records: bool) -> tuple[ForwardEvidenceState, str]:
    if store is None:
        return ForwardEvidenceState.UNAVAILABLE, "The active paper store could not be resolved."
    if store.evidence_schema_status == "SCHEMA_MISMATCH":
        return ForwardEvidenceState.SCHEMA_INCOMPATIBLE, "The prospective evidence schema is incompatible."
    if store.evidence_schema_status == "UNREADABLE" and store.evidence_error and (
        "schema" in store.evidence_error.lower() or "table is missing" in store.evidence_error.lower()
    ):
        return ForwardEvidenceState.SCHEMA_INCOMPATIBLE, "The prospective evidence schema is incompatible."
    if store.evidence_schema_status in {"UNREADABLE", "UNKNOWN"}:
        return ForwardEvidenceState.UNAVAILABLE, "Prospective portfolio evidence is not readable."
    if not has_records:
        return ForwardEvidenceState.NOT_STARTED, "No observation exists for the active paper account epoch."
    if store.evidence_continuity_state == "GAPS_DETECTED":
        return ForwardEvidenceState.CONTINUITY_GAP, "Persisted market-session continuity gaps were detected."
    return ForwardEvidenceState.AVAILABLE, "Persisted observations are available for descriptive inspection."


def _paper_presentation(
    *,
    root: Path,
    environ: Mapping[str, str] | None,
    start_date: str | None,
    end_date: str | None,
    max_records: int,
    qualifier: EvidenceQualifier | None = None,
) -> PaperEvidencePresentation:
    system = inspect_paper_system(
        root=root,
        environ=None if environ is None else dict(environ),
    )
    store = system.active_store
    evidence_path = (root / "data" / "prospective_portfolio_evidence.db").resolve()
    if store is None:
        state, detail = _paper_state(None, has_records=False)
        return PaperEvidencePresentation(
            state, detail, None, None, None, None, None, "UNKNOWN", None, None, (), None,
            evidence_path, None, max_records, False, (),
        )

    account_epoch = read_paper_account_epoch(store.database_path)
    source_identity = paper_store_identity(
        store_id=store.store_id,
        strategy_identity=store.strategy_identity,
        database_path=store.database_path,
        account_epoch_id=account_epoch,
    )
    records = ()
    chain = ()
    warnings = list(store.evidence_warnings)
    if evidence_path.is_file() and store.evidence_schema_status == "OK":
        try:
            _read = ProspectivePortfolioEvidenceLedger(evidence_path)
            # R3 (P1-B): qualification needs the COMPLETE chronological series of this
            # strategy/source identity; the caller's date window only filters what is shown.
            chain = tuple(
                _read.records(
                    strategy_identity=store.strategy_identity,
                    source_store_identity=source_identity,
                )
            )
            records = tuple(
                _read.records(
                    strategy_identity=store.strategy_identity,
                    source_store_identity=source_identity,
                    start_date=start_date,
                    end_date=end_date,
                )
            )
        except (OSError, ValueError, KeyError) as exc:
            warnings.append(f"EVIDENCE_READ_FAILED:{type(exc).__name__}")
    records = tuple(records)
    truncated = len(records) > max_records
    presented = records[-max_records:]
    bindings: dict = {}
    events: dict = {}
    if chain and evidence_path.is_file() and store.evidence_schema_status == "OK":
        try:
            evidence_ledger = ProspectivePortfolioEvidenceLedger(evidence_path)
            bindings = evidence_ledger.market_bindings()
            events = evidence_ledger.qualification_events()
        except (OSError, ValueError, KeyError) as exc:
            warnings.append(f"MARKET_BINDING_READ_FAILED:{type(exc).__name__}")
    if qualifier is None:
        qualifier = EvidenceQualifier()

    # The observations are qualified as one chronological series: an observation's equity
    # includes cumulative realized PnL, so it depends on every earlier contributing trade.
    _ledger = ProspectivePortfolioEvidenceLedger(evidence_path)
    _keys = [("PAPER_OBSERVATION", _ledger.evidence_key(item)) for item in chain]
    _series = qualifier.qualify_observation_series(
        [bindings.get(key) for key in _keys],
        reviews=[[e for e in events.get(key, []) if e["source"] == "REVIEW"] for key in _keys],
    ) if chain else []
    _states = {item.record_identity: result.state for item, result in zip(chain, _series)}

    def _qualification(record) -> str:
        return _states.get(record.record_identity, "LEGACY_UNBOUND")

    points = tuple(
        PaperEvidencePoint(
            item.observation_date,
            item.equity,
            item.cash,
            item.positions_value,
            item.realized_pnl,
            item.unrealized_pnl,
            item.drawdown_pct,
            item.daily_pnl,
            item.daily_return_pct,
            item.record_identity,
            _qualification(item),
        )
        for item in presented
    )
    fills = tuple(fill for item in presented for fill in item.fills_since_previous)
    exits = tuple(exit_ for item in presented for exit_ in item.exits_since_previous)
    execution = None
    if points:
        execution = PaperExecutionProvenance(
            len(fills),
            len(exits),
            sum(item.commission for item in fills) if fills else None,
            sum(item.slippage_cost for item in fills) if fills else None,
            tuple(sorted({item.execution_classification for item in fills})),
            sum(item.spread_or_market_impact is not None for item in fills)
            + sum(item.spread_or_market_impact is not None for item in exits),
        )
    state, detail = _paper_state(store, has_records=bool(records))
    return PaperEvidencePresentation(
        state,
        detail,
        store.strategy_identity,
        store.store_id,
        store.display_name,
        source_identity,
        account_epoch,
        store.evidence_continuity_state or "UNKNOWN",
        store.evidence_observation_count,
        records[-1].observation_date if records else store.latest_evidence_date,
        points,
        execution,
        evidence_path,
        store.database_path,
        max_records,
        truncated,
        tuple(warnings),
    )


def _mean(values: tuple[float | None, ...]) -> float | None:
    available = tuple(value for value in values if value is not None)
    return fmean(available) if available else None


def _forward_protocol_presentation(
    *,
    protocol: ForwardProtocolSnapshot,
    database_path: Path,
    start_date: str | None,
    end_date: str | None,
    horizon_sessions: int | None,
    max_records: int,
    qualifier: EvidenceQualifier | None = None,
) -> ForwardProtocolEvidencePresentation:
    ledger = ForwardValidationLedger(database_path)
    formations_all = ledger.formations(protocol.protocol_id)
    formations = tuple(
        item
        for item in formations_all
        if _in_range(item.formation_session, start_date, end_date)
    )
    truncated = len(formations) > max_records
    formations = formations[-max_records:]
    identities = frozenset(item.formation_identity for item in formations)
    maturities = tuple(
        item
        for item in ledger.latest_maturities(protocol.protocol_id)
        if item.formation_identity in identities
        and (horizon_sessions is None or item.horizon_sessions == horizon_sessions)
    )
    outcomes = tuple(
        item
        for item in ledger.outcomes(protocol.protocol_id)
        if item.formation_identity in identities
        and (horizon_sessions is None or item.horizon_sessions == horizon_sessions)
    )
    gaps = tuple(
        item.market_session
        for item in ledger.audit_events(protocol.protocol_id)
        if _in_range(item.market_session, start_date, end_date)
    )
    requested_horizons = (
        (horizon_sessions,) if horizon_sessions is not None else protocol.tracked_horizons
    )
    bindings = ledger.market_bindings()
    events = ledger.qualification_events()
    if qualifier is None:
        qualifier = EvidenceQualifier()
    verdicts = {}
    for item in outcomes:
        reviews = [
            event
            for event in events.get(("OUTCOME", item.outcome_identity), [])
            if event["source"] == "REVIEW"
        ]
        verdicts[item.outcome_identity] = qualifier.qualify(
            (
                (
                    bindings.get(("FORMATION", item.formation_identity)),
                    {item.symbol, protocol.benchmark},
                ),
                (bindings.get(("OUTCOME", item.outcome_identity)), None),
            ),
            reviews=reviews,
        )
    summaries: list[ForwardOutcomeSummary] = []
    for horizon in requested_horizons:
        horizon_maturities = tuple(item for item in maturities if item.horizon_sessions == horizon)
        horizon_outcomes = tuple(item for item in outcomes if item.horizon_sessions == horizon)
        available = tuple(
            item for item in horizon_outcomes if item.availability is OutcomeAvailability.AVAILABLE
        )
        # Quarantined / reviewed-rejected outcomes are never silently averaged.
        included = tuple(
            item for item in available if verdicts[item.outcome_identity].state not in EXCLUDED_STATES
        )
        qualified = tuple(
            item for item in available if verdicts[item.outcome_identity].state in QUALIFIED_STATES
        )

        def _count(state: str) -> int:
            return sum(verdicts[item.outcome_identity].state == state for item in available)

        summaries.append(
            ForwardOutcomeSummary(
                horizon,
                sum(item.status is MaturityStatus.PENDING for item in horizon_maturities),
                sum(item.status is MaturityStatus.MATURED for item in horizon_maturities),
                sum(
                    item.status in {MaturityStatus.OUTCOME_UNAVAILABLE, MaturityStatus.INVALIDATED}
                    for item in horizon_maturities
                ),
                len(horizon_outcomes),
                len(available),
                _mean(tuple(item.stock_forward_return_pct for item in included)),
                _mean(tuple(item.benchmark_forward_return_pct for item in included)),
                _mean(tuple(item.excess_forward_return_pct_points for item in included)),
                len(qualified),
                _count(Qualification.LEGACY_UNBOUND.value),
                _count(Qualification.BOUND_LEGACY_INPUT.value),
                sum(
                    verdicts[item.outcome_identity].state.startswith("QUARANTINED_")
                    for item in available
                ),
                _count(Qualification.REVIEWED_REJECTED.value),
                _mean(tuple(item.excess_forward_return_pct_points for item in qualified)),
            )
        )

    pending = sum(item.status is MaturityStatus.PENDING for item in maturities)
    matured = sum(item.status is MaturityStatus.MATURED for item in maturities)
    unavailable = sum(
        item.status in {MaturityStatus.OUTCOME_UNAVAILABLE, MaturityStatus.INVALIDATED}
        for item in maturities
    )
    available_count = sum(item.available_outcome_count for item in summaries)
    if gaps:
        state = ForwardEvidenceState.CONTINUITY_GAP
        detail = "Missing formation sessions are persisted as audit gaps; no backfill was inferred."
    elif not formations_all:
        state = ForwardEvidenceState.NOT_STARTED
        detail = "The protocol is activated but has no persisted formations."
    elif not available_count:
        state = ForwardEvidenceState.SAMPLE_IMMATURE
        detail = "Formations exist, but no mature available outcome is present for this view."
    else:
        state = ForwardEvidenceState.AVAILABLE
        detail = "Mature descriptive Forward outcomes are available."
    limitations = (
        "Forward observations are prospective labels, not executed trades or paper PnL.",
        "Descriptive means do not establish statistical reliability.",
        "Missing formations and unavailable outcomes are not reconstructed.",
    )
    return ForwardProtocolEvidencePresentation(
        state,
        detail,
        protocol,
        len(formations_all),
        len(formations),
        pending,
        matured,
        unavailable,
        len(outcomes),
        formations[-1].formation_session if formations else None,
        tuple(item.formation_session for item in formations),
        gaps,
        tuple(summaries),
        database_path,
        max_records,
        truncated,
        limitations,
    )


def inspect_forward_evidence_catalog(
    *,
    root: Path = PROJECT_ROOT,
    environ: Mapping[str, str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    horizon_sessions: int | None = None,
    max_records: int = DEFAULT_MAX_PRESENTED_RECORDS,
    market_database_path: Path | str | None = None,
) -> ForwardEvidenceCatalog:
    """Inspect local persisted evidence without creating or mutating stores."""
    if max_records <= 0:
        raise ValueError("max_records must be positive")
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("start_date must not be after end_date")

    resolved_root = Path(root).resolve()
    qualifier = EvidenceQualifier(market_database_path)
    paper = _paper_presentation(
        root=resolved_root,
        environ=environ,
        start_date=start_date,
        end_date=end_date,
        max_records=max_records,
        qualifier=qualifier,
    )
    snapshot = inspect_forward_system(root=resolved_root)
    if not snapshot.exists:
        return ForwardEvidenceCatalog(
            paper,
            (),
            snapshot.database_path,
            ForwardEvidenceState.NOT_STARTED,
            "No Forward evidence database exists.",
        )
    if not snapshot.readable:
        return ForwardEvidenceCatalog(
            paper,
            (),
            snapshot.database_path,
            ForwardEvidenceState.UNAVAILABLE,
            snapshot.error or "Forward evidence is unreadable.",
        )
    if snapshot.schema_status != "OK":
        return ForwardEvidenceCatalog(
            paper,
            (),
            snapshot.database_path,
            ForwardEvidenceState.SCHEMA_INCOMPATIBLE,
            "The Forward evidence schema is incompatible.",
        )

    presentations: list[ForwardProtocolEvidencePresentation] = []
    try:
        for protocol in snapshot.protocols:
            if horizon_sessions is not None and horizon_sessions not in protocol.tracked_horizons:
                continue
            presentations.append(
                _forward_protocol_presentation(
                    protocol=protocol,
                    database_path=snapshot.database_path,
                    start_date=start_date,
                    end_date=end_date,
                    horizon_sessions=horizon_sessions,
                    max_records=max_records,
                    qualifier=qualifier,
                )
            )
    except (OSError, ValueError, KeyError) as exc:
        return ForwardEvidenceCatalog(
            paper,
            (),
            snapshot.database_path,
            ForwardEvidenceState.UNAVAILABLE,
            f"Forward evidence read failed: {type(exc).__name__}",
        )
    state = (
        ForwardEvidenceState.NOT_STARTED
        if not presentations
        else ForwardEvidenceState.AVAILABLE
    )
    return ForwardEvidenceCatalog(
        paper,
        tuple(presentations),
        snapshot.database_path,
        state,
        "Persisted Forward protocols are available." if presentations else "No protocol is persisted.",
    )


__all__ = (
    "DEFAULT_MAX_PRESENTED_RECORDS",
    "ForwardEvidenceCatalog",
    "ForwardEvidenceState",
    "ForwardOutcomeSummary",
    "ForwardProtocolEvidencePresentation",
    "PaperEvidencePoint",
    "PaperEvidencePresentation",
    "PaperExecutionProvenance",
    "inspect_forward_evidence_catalog",
)
