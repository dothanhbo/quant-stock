from __future__ import annotations

"""Append-only, session-keyed evidence for prospective paper portfolios.

This module reads the paper store directly in SQLite read-only mode.  It must
not instantiate :class:`execution.persistence.PaperTradingStore`: construction
of that operational object initializes and can migrate its database.  The only
write performed here is to the separate prospective-evidence ledger.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from core.evidence_market_binding import (
    COVERAGE_UNPROVEN,
    MarketBindingSession,
    compact_leg,
    ensure_binding_schema,
    insert_binding,
    read_bindings,
    read_qualification_events,
)
from core.paths import PROJECT_ROOT, resolve_market_database_path
from quantlab.identity import canonical_identity_value, canonical_json


EVIDENCE_CONTRACT = "quantlab.prospective_portfolio_evidence"
EVIDENCE_VERSION = "v1"
DEFAULT_EVIDENCE_DATABASE_PATH = PROJECT_ROOT / "data" / "prospective_portfolio_evidence.db"
_TABLE = "prospective_portfolio_evidence"
# R3 (additive): market-data bindings live in the same file as the observation,
# written in the observation's transaction.  Observations created before R3 have
# no binding (LEGACY_UNBOUND) and are never retro-bound.
BINDING_PREFIX = "prospective"
OBSERVATION_BINDING_KIND = "PAPER_OBSERVATION"
_BENCHMARK = "VNINDEX"
_REGIME_COMPUTATION_IDENTITY = "strategy.market_regime.prepare_market_regime_history@v1"


def _identity(payload: Mapping[str, Any]) -> str:
    return sha256(canonical_json(canonical_identity_value(payload))).hexdigest()


def _canonical_path(path: str | Path, *, root: Path = PROJECT_ROOT) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve()


def resolve_evidence_database_path(
    database_path: str | Path | None = None,
    *,
    root: Path = PROJECT_ROOT,
) -> Path:
    """Resolve the dedicated evidence-ledger path without creating it."""
    if database_path is None:
        return (root / "data" / DEFAULT_EVIDENCE_DATABASE_PATH.name).resolve()
    return _canonical_path(database_path, root=root)


def _as_date(value: str | date | datetime) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError as exc:
        raise ValueError(f"invalid observation date: {value!r}") from exc


def _finite_or_none(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _required_finite(value: object, *, name: str) -> float:
    result = _finite_or_none(value)
    if result is None:
        raise ValueError(f"{name} must be finite")
    return result


def _normalized_symbol(value: object) -> str:
    symbol = str(value or "").strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    return symbol


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def paper_store_identity(
    *,
    store_id: str,
    strategy_identity: str,
    database_path: str | Path,
    account_epoch_id: str | None = None,
) -> str:
    """Return a deterministic identity for one isolated paper-account epoch."""
    normalized_store = str(store_id).strip()
    normalized_strategy = str(strategy_identity).strip()
    if not normalized_store or not normalized_strategy:
        raise ValueError("paper store and strategy identities are required")
    normalized_epoch = None
    if account_epoch_id is not None:
        normalized_epoch = str(account_epoch_id).strip()
        if not normalized_epoch:
            raise ValueError("paper account epoch must be non-empty when supplied")
    return _identity(
        {
            "contract": EVIDENCE_CONTRACT,
            "version": EVIDENCE_VERSION,
            "store_id": normalized_store,
            "strategy_identity": normalized_strategy,
            "database_path": str(_canonical_path(database_path)),
            "account_epoch_id": normalized_epoch,
        }
    )


@dataclass(frozen=True, slots=True)
class PaperEventCursor:
    """Durable high-water marks used instead of wall-clock fill timestamps."""

    fill_id: int = 0
    closed_trade_id: int = 0

    def __post_init__(self) -> None:
        if self.fill_id < 0 or self.closed_trade_id < 0:
            raise ValueError("event cursor values must be non-negative")

    def as_dict(self) -> dict[str, int]:
        return {"fill_id": self.fill_id, "closed_trade_id": self.closed_trade_id}


@dataclass(frozen=True, slots=True)
class ProspectivePositionEvidence:
    symbol: str
    quantity: int
    average_price: float
    valuation_price: float
    cost_basis: float
    market_value: float
    unrealized_pnl: float
    entry_date: str | None = None
    entry_order_id: str | None = None
    strategy_version: str | None = None
    policy_fingerprint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _normalized_symbol(self.symbol))
        if self.quantity <= 0:
            raise ValueError("position quantity must be positive")
        for name in (
            "average_price",
            "valuation_price",
            "cost_basis",
            "market_value",
            "unrealized_pnl",
        ):
            object.__setattr__(self, name, _required_finite(getattr(self, name), name=name))
        expected_cost_basis = self.quantity * self.average_price
        expected_market_value = self.quantity * self.valuation_price
        expected_unrealized = expected_market_value - expected_cost_basis
        if not math.isclose(self.cost_basis, expected_cost_basis, rel_tol=0.0, abs_tol=1e-6):
            raise ValueError("position cost basis does not match quantity and average price")
        if not math.isclose(self.market_value, expected_market_value, rel_tol=0.0, abs_tol=1e-6):
            raise ValueError("position market value does not match quantity and valuation price")
        if not math.isclose(self.unrealized_pnl, expected_unrealized, rel_tol=0.0, abs_tol=1e-6):
            raise ValueError("position unrealized PnL does not match cost basis and market value")
        if self.entry_date is not None:
            object.__setattr__(self, "entry_date", _as_date(self.entry_date))

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "average_price": self.average_price,
            "valuation_price": self.valuation_price,
            "cost_basis": self.cost_basis,
            "market_value": self.market_value,
            "unrealized_pnl": self.unrealized_pnl,
            "entry_date": self.entry_date,
            "entry_order_id": self.entry_order_id,
            "strategy_version": self.strategy_version,
            "policy_fingerprint": self.policy_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class ProspectiveFillEvidence:
    fill_id: int
    order_id: str
    source_intent_id: str | None
    pending_signal_id: int | None
    signal_date: str | None
    signal_reference_price: float | None
    symbol: str
    side: str
    quantity: int
    execution_reference_price: float | None
    fill_price: float
    gross_value: float
    commission: float
    slippage_cost: float
    net_cash_flow: float
    created_at: str
    execution_classification: str = "PAPER_MODELED"
    spread_or_market_impact: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _normalized_symbol(self.symbol))
        side = str(self.side).strip().upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("fill side must be BUY or SELL")
        object.__setattr__(self, "side", side)
        if self.fill_id <= 0 or self.quantity <= 0:
            raise ValueError("fill id and quantity must be positive")
        for name in ("fill_price", "gross_value", "commission", "slippage_cost", "net_cash_flow"):
            object.__setattr__(self, name, _required_finite(getattr(self, name), name=name))
        for name in ("execution_reference_price", "signal_reference_price", "spread_or_market_impact"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _required_finite(value, name=name))
        if self.signal_date is not None:
            object.__setattr__(self, "signal_date", _as_date(self.signal_date))

    def as_dict(self) -> dict[str, Any]:
        return {
            "fill_id": self.fill_id,
            "order_id": self.order_id,
            "source_intent_id": self.source_intent_id,
            "pending_signal_id": self.pending_signal_id,
            "signal_date": self.signal_date,
            "signal_reference_price": self.signal_reference_price,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "execution_reference_price": self.execution_reference_price,
            "fill_price": self.fill_price,
            "gross_value": self.gross_value,
            "commission": self.commission,
            "slippage_cost": self.slippage_cost,
            "net_cash_flow": self.net_cash_flow,
            "created_at": self.created_at,
            "execution_classification": self.execution_classification,
            "spread_or_market_impact": self.spread_or_market_impact,
        }


@dataclass(frozen=True, slots=True)
class ProspectiveExitEvidence:
    closed_trade_id: int
    order_id: str
    symbol: str
    entry_date: str
    exit_date: str
    quantity: int
    lifecycle_entry_price: float
    closed_exit_price: float
    gross_proceeds: float
    exit_commission: float
    realized_pnl: float
    return_pct: float
    holding_days: int
    exit_reason: str
    entry_order_id: str | None = None
    strategy_version: str | None = None
    policy_fingerprint: str | None = None
    signal_date: str | None = None
    signal_reference_price: float | None = None
    entry_execution_reference_price: float | None = None
    entry_fill_price: float | None = None
    entry_commission: float | None = None
    entry_slippage_cost: float | None = None
    exit_execution_reference_price: float | None = None
    exit_fill_price: float | None = None
    exit_slippage_cost: float | None = None
    gross_pnl: float | None = None
    provenance_status: str = "UNAVAILABLE_PRE_EVIDENCE_EXIT_LINK"
    spread_or_market_impact: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _normalized_symbol(self.symbol))
        if self.closed_trade_id <= 0 or self.quantity <= 0 or self.holding_days < 0:
            raise ValueError("invalid completed-trade evidence")
        object.__setattr__(self, "entry_date", _as_date(self.entry_date))
        object.__setattr__(self, "exit_date", _as_date(self.exit_date))
        for name in (
            "lifecycle_entry_price",
            "closed_exit_price",
            "gross_proceeds",
            "exit_commission",
            "realized_pnl",
            "return_pct",
        ):
            object.__setattr__(self, name, _required_finite(getattr(self, name), name=name))
        for name in (
            "signal_reference_price",
            "entry_execution_reference_price",
            "entry_fill_price",
            "entry_commission",
            "entry_slippage_cost",
            "exit_execution_reference_price",
            "exit_fill_price",
            "exit_slippage_cost",
            "gross_pnl",
            "spread_or_market_impact",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _required_finite(value, name=name))
        if self.signal_date is not None:
            object.__setattr__(self, "signal_date", _as_date(self.signal_date))

    def as_dict(self) -> dict[str, Any]:
        return {
            "closed_trade_id": self.closed_trade_id,
            "order_id": self.order_id,
            "symbol": self.symbol,
            "entry_date": self.entry_date,
            "exit_date": self.exit_date,
            "quantity": self.quantity,
            "lifecycle_entry_price": self.lifecycle_entry_price,
            "closed_exit_price": self.closed_exit_price,
            "gross_proceeds": self.gross_proceeds,
            "exit_commission": self.exit_commission,
            "realized_pnl": self.realized_pnl,
            "return_pct": self.return_pct,
            "holding_days": self.holding_days,
            "exit_reason": self.exit_reason,
            "entry_order_id": self.entry_order_id,
            "strategy_version": self.strategy_version,
            "policy_fingerprint": self.policy_fingerprint,
            "signal_date": self.signal_date,
            "signal_reference_price": self.signal_reference_price,
            "entry_execution_reference_price": self.entry_execution_reference_price,
            "entry_fill_price": self.entry_fill_price,
            "entry_commission": self.entry_commission,
            "entry_slippage_cost": self.entry_slippage_cost,
            "exit_execution_reference_price": self.exit_execution_reference_price,
            "exit_fill_price": self.exit_fill_price,
            "exit_slippage_cost": self.exit_slippage_cost,
            "gross_pnl": self.gross_pnl,
            "provenance_status": self.provenance_status,
            "spread_or_market_impact": self.spread_or_market_impact,
        }


_STRATEGY_IDENTITY_V3_FIELDS = (
    "strategy_identity_v3",
    "signal_identity",
    "execution_identity",
)
# B5-C contract attestation of a NEW record (quantlab.strategy_contract).
_STRATEGY_CONTRACT_FIELDS = (
    "strategy_contract_version",
    "strategy_contract_status",
    "strategy_contract_deviations",
    "execution_overlay",
)
_STRATEGY_CONTRACT_STATUSES = frozenset({"CONTRACT_MATCHED", "CONTRACT_DEVIATION"})
_STRATEGY_BEHAVIOR_CONTRACT_FIELDS = (
    "strategy_signal_contract",
    "strategy_execution_contract",
)


@dataclass(frozen=True, slots=True)
class ProspectivePortfolioEvidenceRecord:
    """One immutable, effective-session observation of a paper portfolio."""

    evidence_version: str
    strategy_identity: str
    source_store_id: str
    source_store_identity: str
    source_account_epoch_id: str | None
    runtime_configuration_fingerprint: str
    observation_date: str
    captured_at_utc: str
    paper_database_path: str
    market_database_path: str
    market_database_sha256: str | None
    market_data_reference_session: str | None
    benchmark_symbol: str
    benchmark_close: float | None
    market_regime_label: str
    regime_computation_identity: str
    cash: float
    positions_value: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    gross_exposure_pct: float
    open_position_count: int
    positions: tuple[ProspectivePositionEvidence, ...] = ()
    fills_since_previous: tuple[ProspectiveFillEvidence, ...] = ()
    exits_since_previous: tuple[ProspectiveExitEvidence, ...] = ()
    event_cursor: PaperEventCursor = field(default_factory=PaperEventCursor)
    daily_pnl: float | None = None
    daily_return_pct: float | None = None
    running_equity_peak: float | None = None
    drawdown_pct: float | None = None
    forward_protocol_link_status: str = "DISTINCT_FROM_FORWARD_PROTOCOL_EVIDENCE"
    provenance_warnings: tuple[str, ...] = ()
    # Strategy Identity v3 (Phase 3B, shadow/additive). Nullable: records
    # created before v3, or whose shadow identity was INCOMPLETE, carry None.
    # Omitted from the identity payload when None so every pre-v3 record keeps
    # its original ``record_identity`` byte-for-byte.
    strategy_identity_v3: str | None = None
    signal_identity: str | None = None
    execution_identity: str | None = None
    # B5-C (additive, nullable, NEW records only; omitted from the identity
    # payload when None so earlier records keep their exact record_identity).
    strategy_contract_version: str | None = None
    strategy_contract_status: str | None = None
    strategy_contract_deviations: tuple[str, ...] | None = None
    execution_overlay: str | None = None
    # The normalized behavioral contracts the v3 fingerprints describe (P2-1,
    # second review). Every fingerprint, the canonical comparison, deviation
    # paths and status are RECOMPUTED from these on construction; caller
    # metadata can never assert a status the contracts do not produce.
    strategy_signal_contract: Mapping[str, Any] | None = None
    strategy_execution_contract: Mapping[str, Any] | None = None
    record_identity: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.evidence_version.strip() or not self.strategy_identity.strip():
            raise ValueError("evidence version and strategy identity are required")
        if not self.source_store_id.strip() or not self.source_store_identity.strip():
            raise ValueError("source store identity is required")
        if self.source_account_epoch_id is not None:
            epoch = str(self.source_account_epoch_id).strip()
            if not epoch:
                raise ValueError("source account epoch must be non-empty when supplied")
            object.__setattr__(self, "source_account_epoch_id", epoch)
        if not self.runtime_configuration_fingerprint.strip():
            raise ValueError("runtime configuration fingerprint is required")
        object.__setattr__(self, "observation_date", _as_date(self.observation_date))
        object.__setattr__(self, "benchmark_symbol", _normalized_symbol(self.benchmark_symbol))
        if self.benchmark_symbol != _BENCHMARK:
            raise ValueError("prospective portfolio evidence benchmark must be VNINDEX")
        if self.open_position_count < 0:
            raise ValueError("open position count must be non-negative")
        for name in (
            "cash",
            "positions_value",
            "equity",
            "realized_pnl",
            "unrealized_pnl",
            "gross_exposure_pct",
        ):
            object.__setattr__(self, name, _required_finite(getattr(self, name), name=name))
        for name in (
            "benchmark_close",
            "daily_pnl",
            "daily_return_pct",
            "running_equity_peak",
            "drawdown_pct",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _required_finite(value, name=name))
        positions = tuple(sorted(tuple(self.positions), key=lambda item: item.symbol))
        fills = tuple(sorted(tuple(self.fills_since_previous), key=lambda item: item.fill_id))
        exits = tuple(sorted(tuple(self.exits_since_previous), key=lambda item: item.closed_trade_id))
        if len(positions) != self.open_position_count:
            raise ValueError("open position count does not match position evidence")
        if len({item.symbol for item in positions}) != len(positions):
            raise ValueError("position evidence contains duplicate symbols")
        if len({item.fill_id for item in fills}) != len(fills):
            raise ValueError("fill evidence contains duplicate ids")
        if len({item.closed_trade_id for item in exits}) != len(exits):
            raise ValueError("exit evidence contains duplicate ids")
        if not math.isclose(self.cash + self.positions_value, self.equity, rel_tol=0.0, abs_tol=1e-6):
            raise ValueError("equity must equal cash plus open-position value")
        expected_positions_value = sum(item.market_value for item in positions)
        if not math.isclose(expected_positions_value, self.positions_value, rel_tol=0.0, abs_tol=1e-6):
            raise ValueError("positions value does not match position evidence")
        expected_unrealized = sum(item.unrealized_pnl for item in positions)
        if not math.isclose(expected_unrealized, self.unrealized_pnl, rel_tol=0.0, abs_tol=1e-6):
            raise ValueError("unrealized PnL does not match position evidence")
        expected_exposure = self.positions_value / self.equity * 100.0 if self.equity > 0 else 0.0
        if not math.isclose(self.gross_exposure_pct, expected_exposure, rel_tol=0.0, abs_tol=1e-6):
            raise ValueError("gross exposure does not match equity and position evidence")
        object.__setattr__(self, "positions", positions)
        object.__setattr__(self, "fills_since_previous", fills)
        object.__setattr__(self, "exits_since_previous", exits)
        object.__setattr__(self, "provenance_warnings", tuple(sorted(set(self.provenance_warnings))))
        for name in _STRATEGY_IDENTITY_V3_FIELDS:
            value = getattr(self, name)
            if value is not None:
                text = str(value).strip().lower()
                if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
                    raise ValueError(f"{name} must be a SHA-256 hex digest when supplied")
                object.__setattr__(self, name, text)
        self._validate_strategy_identity_v3()
        object.__setattr__(self, "record_identity", _identity(self.identity_payload()))

    def _validate_strategy_identity_v3(self) -> None:
        """Derive and verify every additive v3 field; legacy (all None) passes.

        From the stored behavioral contracts this RECOMPUTES ``signal_identity``,
        ``execution_identity`` and ``strategy_identity_v3`` (for this record's
        strategy) and, when a contract status is present, the canonical
        comparison: the stored status, deviation paths and overlay must equal
        the derived ones exactly. Stale/forged fingerprints, mixed parts from
        different runs, a noncanonical contract labelled CONTRACT_MATCHED,
        blank or invented deviation paths are all rejected.
        """
        prints = [getattr(self, name) for name in _STRATEGY_IDENTITY_V3_FIELDS]
        contracts = [getattr(self, name) for name in _STRATEGY_BEHAVIOR_CONTRACT_FIELDS]
        attestation = [getattr(self, name) for name in _STRATEGY_CONTRACT_FIELDS]
        if all(value is None for value in (*prints, *contracts, *attestation)):
            return  # A: pre-v3 legacy record
        if all(value is None for value in contracts):
            # B: early v3 format (Phase 3B shadow fingerprints / Phase 3D
            # attestation) written before contracts were embedded. Readable as
            # historical metadata only: nothing is recomputed, inferred or
            # promoted (see strategy_identity_verification).
            self._validate_early_v3_format()
            return
        if any(value is None for value in (*prints, *contracts)):
            raise ValueError(
                "v3 identity requires strategy, signal and execution identities and "
                "the signal and execution contracts they fingerprint"
            )
        from quantlab.strategy_identity import build_strategy_identity

        signal_contract = _normalized_contract(self.strategy_signal_contract)
        execution_contract = _normalized_contract(self.strategy_execution_contract)
        rebuilt = build_strategy_identity(
            strategy=self.strategy_identity,
            signal_contract=signal_contract,
            execution_contract=execution_contract,
        )
        for name, expected in rebuilt.fingerprints().items():
            if getattr(self, name) != expected:
                raise ValueError(
                    f"{name} does not match the fingerprint recomputed from this "
                    "record's strategy and behavioral contracts"
                )
        # Deeply immutable internal state: callers cannot mutate the contracts
        # this record's fingerprints and status were derived from.
        object.__setattr__(self, "strategy_signal_contract", _freeze_contract(signal_contract))
        object.__setattr__(self, "strategy_execution_contract", _freeze_contract(execution_contract))
        if all(value is None for value in attestation):
            return
        if any(value is None for value in attestation):
            raise ValueError(
                "strategy contract attestation requires version, status, deviations and overlay"
            )
        if self.strategy_contract_status not in _STRATEGY_CONTRACT_STATUSES:
            raise ValueError("strategy contract status is invalid")
        raw_deviations = self.strategy_contract_deviations
        if isinstance(raw_deviations, (str, bytes)) or not isinstance(raw_deviations, (tuple, list)):
            raise ValueError("strategy contract deviations must be a sequence of field paths")
        if any(not isinstance(item, str) or not item.strip() for item in raw_deviations):
            raise ValueError("strategy contract deviation paths must be non-blank strings")
        deviations = tuple(raw_deviations)
        if self.strategy_contract_status == "CONTRACT_DEVIATION" and not deviations:
            raise ValueError("CONTRACT_DEVIATION requires the deviating field paths")
        if self.strategy_contract_status == "CONTRACT_MATCHED" and deviations:
            raise ValueError("CONTRACT_MATCHED must have no deviations")
        from quantlab.strategy_contract import CONTRACT_VERSION, compare_to_canonical

        if self.strategy_contract_version != CONTRACT_VERSION:
            raise ValueError(
                f"unknown strategy contract version {self.strategy_contract_version!r}; "
                "its canonical contract is not available to verify the status"
            )
        comparison = compare_to_canonical(rebuilt)
        if self.strategy_contract_status != comparison.status:
            raise ValueError(
                f"strategy contract status {self.strategy_contract_status} contradicts "
                f"the status {comparison.status} derived from the recorded contracts"
            )
        if deviations != comparison.deviation_paths:
            raise ValueError(
                "strategy contract deviation paths do not equal the deviations derived "
                "from the recorded contracts"
            )
        if self.execution_overlay != comparison.regime_overlay:
            raise ValueError("execution overlay does not match the recorded execution contract")
        object.__setattr__(self, "strategy_contract_deviations", deviations)

    def _validate_early_v3_format(self) -> None:
        """Format-only checks for early (contract-map-less) v3 records."""
        if self.strategy_contract_status is not None and (
            self.strategy_contract_status not in _STRATEGY_CONTRACT_STATUSES
        ):
            raise ValueError("strategy contract status is invalid")
        deviations = self.strategy_contract_deviations
        if deviations is not None:
            if isinstance(deviations, (str, bytes)) or not isinstance(deviations, (tuple, list)):
                raise ValueError("strategy contract deviations must be a sequence of field paths")
            object.__setattr__(
                self, "strategy_contract_deviations", tuple(str(item) for item in deviations)
            )

    @property
    def strategy_identity_format(self) -> str:
        """A: PRE_V3, B: EARLY_V3_INCOMPLETE, C: CONTRACT_COMPLETE_V3."""
        fields = (
            *_STRATEGY_IDENTITY_V3_FIELDS,
            *_STRATEGY_CONTRACT_FIELDS,
            *_STRATEGY_BEHAVIOR_CONTRACT_FIELDS,
        )
        if all(getattr(self, name) is None for name in fields):
            return "PRE_V3"
        if self.strategy_signal_contract is None:
            return "EARLY_V3_INCOMPLETE"
        return "CONTRACT_COMPLETE_V3"

    @property
    def historical_contract_status(self) -> str | None:
        """Contract status exactly as stored (historical metadata, unverified
        for early-v3 records; verified for contract-complete records)."""
        return self.strategy_contract_status

    @property
    def strategy_identity_verification(self) -> str:
        """Current verification state, separate from any stored status."""
        form = self.strategy_identity_format
        if form == "PRE_V3":
            return "NO_STRATEGY_IDENTITY_V3"
        if form == "EARLY_V3_INCOMPLETE":
            return "LEGACY_INCOMPLETE_V3_IDENTITY"
        # Construction re-derived every field from the embedded contracts.
        return self.strategy_contract_status or "STRATEGY_IDENTITY_V3_RECORDED"

    @property
    def strategy_identity_provenance(self) -> tuple[str, ...]:
        """Provenance labels for this record (never stored; derived, read-only).

        Pre-v3 records and early v3 records without embedded behavioral
        contracts are LEGACY_UNVERIFIED; a stored status on an early record is
        never promoted to a verified label.
        """
        form = self.strategy_identity_format
        if form == "PRE_V3":
            return ("LEGACY_UNVERIFIED", "NO_STRATEGY_IDENTITY_V3")
        if form == "EARLY_V3_INCOMPLETE":
            return ("LEGACY_UNVERIFIED", "LEGACY_INCOMPLETE_V3_IDENTITY")
        labels = ["STRATEGY_IDENTITY_V3_RECORDED"]
        if self.strategy_contract_status is not None:
            labels.append(self.strategy_contract_status)
        return tuple(labels)

    def identity_payload(self) -> dict[str, Any]:
        """Return all immutable content, excluding capture wall-clock time."""
        return {
            "contract": EVIDENCE_CONTRACT,
            "evidence_version": self.evidence_version,
            "strategy_identity": self.strategy_identity,
            "source_store_id": self.source_store_id,
            "source_store_identity": self.source_store_identity,
            "source_account_epoch_id": self.source_account_epoch_id,
            "runtime_configuration_fingerprint": self.runtime_configuration_fingerprint,
            "observation_date": self.observation_date,
            "paper_database_path": self.paper_database_path,
            "market_database_path": self.market_database_path,
            "market_database_sha256": self.market_database_sha256,
            "market_data_reference_session": self.market_data_reference_session,
            "benchmark_symbol": self.benchmark_symbol,
            "benchmark_close": self.benchmark_close,
            "market_regime_label": self.market_regime_label,
            "regime_computation_identity": self.regime_computation_identity,
            "cash": self.cash,
            "positions_value": self.positions_value,
            "equity": self.equity,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "gross_exposure_pct": self.gross_exposure_pct,
            "open_position_count": self.open_position_count,
            "positions": [item.as_dict() for item in self.positions],
            "fills_since_previous": [item.as_dict() for item in self.fills_since_previous],
            "exits_since_previous": [item.as_dict() for item in self.exits_since_previous],
            "event_cursor": self.event_cursor.as_dict(),
            "daily_pnl": self.daily_pnl,
            "daily_return_pct": self.daily_return_pct,
            "running_equity_peak": self.running_equity_peak,
            "drawdown_pct": self.drawdown_pct,
            "forward_protocol_link_status": self.forward_protocol_link_status,
            "provenance_warnings": self.provenance_warnings,
            # Additive v3 fields appear only when present (see field comment).
            **{
                name: getattr(self, name)
                for name in (
                    *_STRATEGY_IDENTITY_V3_FIELDS,
                    *_STRATEGY_CONTRACT_FIELDS,
                    *_STRATEGY_BEHAVIOR_CONTRACT_FIELDS,
                )
                if getattr(self, name) is not None
            },
        }

    def as_dict(self) -> dict[str, Any]:
        """Plain, fully independent copy (mutating it never affects the record)."""
        payload = self.identity_payload()
        payload["captured_at_utc"] = self.captured_at_utc
        payload["record_identity"] = self.record_identity
        return json.loads(canonical_json(canonical_identity_value(payload)).decode("utf-8"))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProspectivePortfolioEvidenceRecord":
        return cls(
            evidence_version=str(payload["evidence_version"]),
            strategy_identity=str(payload["strategy_identity"]),
            source_store_id=str(payload["source_store_id"]),
            source_store_identity=str(payload["source_store_identity"]),
            source_account_epoch_id=(
                None
                if payload.get("source_account_epoch_id") is None
                else str(payload["source_account_epoch_id"])
            ),
            runtime_configuration_fingerprint=str(payload["runtime_configuration_fingerprint"]),
            observation_date=str(payload["observation_date"]),
            captured_at_utc=str(payload["captured_at_utc"]),
            paper_database_path=str(payload["paper_database_path"]),
            market_database_path=str(payload["market_database_path"]),
            market_database_sha256=payload.get("market_database_sha256"),
            market_data_reference_session=payload.get("market_data_reference_session"),
            benchmark_symbol=str(payload.get("benchmark_symbol", _BENCHMARK)),
            benchmark_close=payload.get("benchmark_close"),
            market_regime_label=str(payload.get("market_regime_label", "UNKNOWN")),
            regime_computation_identity=str(payload.get("regime_computation_identity", _REGIME_COMPUTATION_IDENTITY)),
            cash=payload["cash"],
            positions_value=payload["positions_value"],
            equity=payload["equity"],
            realized_pnl=payload["realized_pnl"],
            unrealized_pnl=payload["unrealized_pnl"],
            gross_exposure_pct=payload["gross_exposure_pct"],
            open_position_count=int(payload["open_position_count"]),
            positions=tuple(ProspectivePositionEvidence(**item) for item in payload.get("positions", ())),
            fills_since_previous=tuple(ProspectiveFillEvidence(**item) for item in payload.get("fills_since_previous", ())),
            exits_since_previous=tuple(ProspectiveExitEvidence(**item) for item in payload.get("exits_since_previous", ())),
            event_cursor=PaperEventCursor(**dict(payload.get("event_cursor", {}))),
            daily_pnl=payload.get("daily_pnl"),
            daily_return_pct=payload.get("daily_return_pct"),
            running_equity_peak=payload.get("running_equity_peak"),
            drawdown_pct=payload.get("drawdown_pct"),
            forward_protocol_link_status=str(payload.get("forward_protocol_link_status", "DISTINCT_FROM_FORWARD_PROTOCOL_EVIDENCE")),
            provenance_warnings=tuple(str(item) for item in payload.get("provenance_warnings", ())),
            strategy_identity_v3=payload.get("strategy_identity_v3"),
            signal_identity=payload.get("signal_identity"),
            execution_identity=payload.get("execution_identity"),
            strategy_contract_version=payload.get("strategy_contract_version"),
            strategy_contract_status=payload.get("strategy_contract_status"),
            strategy_contract_deviations=(
                None
                if payload.get("strategy_contract_deviations") is None
                else tuple(payload["strategy_contract_deviations"])
            ),
            execution_overlay=payload.get("execution_overlay"),
            strategy_signal_contract=payload.get("strategy_signal_contract"),
            strategy_execution_contract=payload.get("strategy_execution_contract"),
        )


@dataclass(frozen=True, slots=True)
class ProspectiveEvidenceCaptureResult:
    record: ProspectivePortfolioEvidenceRecord
    created: bool


@dataclass(frozen=True, slots=True)
class ProspectiveEvidenceStatus:
    database_path: Path
    exists: bool
    readable: bool
    schema_status: str
    observation_count: int | None = None
    latest_observation_date: str | None = None
    latest_strategy_identity: str | None = None
    latest_configuration_fingerprint: str | None = None
    capture_state: str = "MISSING"
    continuity_state: str = "NOT_STARTED"
    missing_sessions: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    error: str | None = None


class ProspectivePortfolioEvidenceLedger:
    """Append-only dedicated storage for immutable paper evidence records."""

    _REQUIRED_COLUMNS = frozenset(
        {
            "evidence_key",
            "record_identity",
            "evidence_version",
            "strategy_identity",
            "source_store_id",
            "source_store_identity",
            "runtime_configuration_fingerprint",
            "observation_date",
            "captured_at_utc",
            "payload_json",
        }
    )
    _UNIQUE_OBSERVATION_COLUMNS = (
        "evidence_version",
        "strategy_identity",
        "source_store_identity",
        "runtime_configuration_fingerprint",
        "observation_date",
    )

    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = resolve_evidence_database_path(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @classmethod
    def _validate_schema(cls, connection: sqlite3.Connection) -> None:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if _TABLE not in tables:
            raise ValueError("prospective evidence table is missing")
        column_rows = tuple(connection.execute(f"PRAGMA table_info({_TABLE})"))
        columns = {str(row[1]) for row in column_rows}
        missing_columns = sorted(cls._REQUIRED_COLUMNS - columns)
        if missing_columns:
            raise ValueError(
                "prospective evidence schema is missing columns: "
                + ", ".join(missing_columns)
            )
        primary_key_columns = tuple(
            str(row[1])
            for row in sorted(column_rows, key=lambda item: int(item[5]))
            if int(row[5]) > 0
        )
        if primary_key_columns != ("evidence_key",):
            raise ValueError("prospective evidence schema primary key is invalid")
        unique_columns: set[tuple[str, ...]] = set()
        for index in connection.execute(f"PRAGMA index_list({_TABLE})"):
            if not int(index[2]):
                continue
            index_name = str(index[1]).replace('"', '""')
            unique_columns.add(
                tuple(
                    str(row[2])
                    for row in connection.execute(f'PRAGMA index_info("{index_name}")')
                )
            )
        if ("record_identity",) not in unique_columns:
            raise ValueError("prospective evidence schema record identity uniqueness is missing")
        if cls._UNIQUE_OBSERVATION_COLUMNS not in unique_columns:
            raise ValueError("prospective evidence schema observation uniqueness is missing")
        trigger_sql = {
            str(row[0]): str(row[1] or "")
            for row in connection.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger'"
            )
        }
        for operation in ("update", "delete"):
            name = f"{_TABLE}_no_{operation}"
            normalized_sql = "".join(trigger_sql.get(name, "").lower().split())
            expected = (
                f"before{operation}on{_TABLE}beginselect"
                f"raise(abort,'append-onlytable:{_TABLE}');end"
            )
            if expected not in normalized_sql:
                raise ValueError("prospective evidence append-only triggers are invalid")

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> ProspectivePortfolioEvidenceRecord:
        try:
            record = ProspectivePortfolioEvidenceRecord.from_dict(
                json.loads(str(row["payload_json"]))
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("prospective evidence payload is invalid") from exc
        if str(row["record_identity"]) != record.record_identity:
            raise ValueError("prospective evidence payload identity does not match stored identity")
        if str(row["evidence_key"]) != ProspectivePortfolioEvidenceLedger._evidence_key(record):
            raise ValueError("prospective evidence payload does not match stored key")
        expected_columns = {
            "evidence_version": record.evidence_version,
            "strategy_identity": record.strategy_identity,
            "source_store_id": record.source_store_id,
            "source_store_identity": record.source_store_identity,
            "runtime_configuration_fingerprint": record.runtime_configuration_fingerprint,
            "observation_date": record.observation_date,
            "captured_at_utc": record.captured_at_utc,
        }
        row_columns = frozenset(row.keys())
        missing_columns = sorted(set(expected_columns) - row_columns)
        if missing_columns:
            raise ValueError(
                "prospective evidence stored query columns are missing: "
                + ", ".join(missing_columns)
            )
        for column, expected in expected_columns.items():
            if str(row[column]) != str(expected):
                raise ValueError(
                    "prospective evidence stored query column does not match payload: "
                    + column
                )
        return record

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS {_TABLE} (
                    evidence_key TEXT PRIMARY KEY,
                    record_identity TEXT NOT NULL UNIQUE,
                    evidence_version TEXT NOT NULL,
                    strategy_identity TEXT NOT NULL,
                    source_store_id TEXT NOT NULL,
                    source_store_identity TEXT NOT NULL,
                    runtime_configuration_fingerprint TEXT NOT NULL,
                    observation_date TEXT NOT NULL,
                    captured_at_utc TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(
                        evidence_version,
                        strategy_identity,
                        source_store_identity,
                        runtime_configuration_fingerprint,
                        observation_date
                    )
                );
                CREATE INDEX IF NOT EXISTS idx_prospective_evidence_query
                ON {_TABLE}(
                    strategy_identity,
                    source_store_identity,
                    observation_date,
                    runtime_configuration_fingerprint,
                    evidence_version
                );
                CREATE TRIGGER IF NOT EXISTS {_TABLE}_no_update
                BEFORE UPDATE ON {_TABLE}
                BEGIN SELECT RAISE(ABORT, 'append-only table: {_TABLE}'); END;
                CREATE TRIGGER IF NOT EXISTS {_TABLE}_no_delete
                BEFORE DELETE ON {_TABLE}
                BEGIN SELECT RAISE(ABORT, 'append-only table: {_TABLE}'); END;
                """
            )
            ensure_binding_schema(connection, BINDING_PREFIX)
            self._validate_schema(connection)

    @staticmethod
    def _revalidated_for_append(
        record: ProspectivePortfolioEvidenceRecord,
    ) -> ProspectivePortfolioEvidenceRecord:
        """Re-derive the record from its CURRENT payload before persistence.

        Construction-time validation is not trusted: the exact payload that
        would be written is rebuilt from scratch, which recomputes every v3
        fingerprint, the canonical comparison, deviation paths and status from
        the current behavioral contracts and recomputes ``record_identity``.
        Any difference from the cached values (e.g. an injected mutable
        contract mutated after validation) is rejected. New early-v3
        (contract-map-less) records are refused; that format is read-only.
        """
        try:
            rebuilt = ProspectivePortfolioEvidenceRecord.from_dict(
                json.loads(canonical_json(canonical_identity_value(record.as_dict())).decode("utf-8"))
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"evidence record failed re-validation before append: {exc}") from exc
        if rebuilt.record_identity != record.record_identity:
            raise ValueError(
                "evidence record content no longer matches its validated identity; "
                "refusing to persist"
            )
        if rebuilt.strategy_identity_format == "EARLY_V3_INCOMPLETE":
            raise ValueError(
                "new evidence must be pre-v3 or contract-complete v3; the early v3 "
                "format without behavioral contracts is read-only"
            )
        return rebuilt

    @staticmethod
    def _evidence_key(record: ProspectivePortfolioEvidenceRecord) -> str:
        return _identity(
            {
                "contract": EVIDENCE_CONTRACT,
                "evidence_version": record.evidence_version,
                "strategy_identity": record.strategy_identity,
                "source_store_identity": record.source_store_identity,
                "runtime_configuration_fingerprint": record.runtime_configuration_fingerprint,
                "observation_date": record.observation_date,
            }
        )

    def append(
        self,
        record: ProspectivePortfolioEvidenceRecord,
        *,
        market_binding: Mapping[str, Any] | None = None,
    ) -> ProspectiveEvidenceCaptureResult:
        record = self._revalidated_for_append(record)
        self.initialize()
        key = self._evidence_key(record)
        if market_binding is not None and (
            market_binding["subject"]["kind"] != OBSERVATION_BINDING_KIND
            or market_binding["subject"]["ref"] != key
        ):
            raise ValueError("market-data binding does not belong to this observation")
        encoded = canonical_json(record.as_dict()).decode("utf-8")
        with self._connect() as connection:
            # Serialize writers to make concurrent identical captures return
            # the one immutable record rather than surface a uniqueness race.
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                f"SELECT * FROM {_TABLE} WHERE evidence_key=?",
                (key,),
            ).fetchone()
            if existing is not None:
                existing_record = self._record_from_row(existing)
                if existing_record.record_identity == record.record_identity:
                    return ProspectiveEvidenceCaptureResult(existing_record, False)
                raise ValueError(
                    "conflicting immutable prospective evidence for the same "
                    "session, strategy, store, and configuration"
                )
            connection.execute(
                f"""
                INSERT INTO {_TABLE}(
                    evidence_key,record_identity,evidence_version,strategy_identity,
                    source_store_id,source_store_identity,runtime_configuration_fingerprint,
                    observation_date,captured_at_utc,payload_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    key,
                    record.record_identity,
                    record.evidence_version,
                    record.strategy_identity,
                    record.source_store_id,
                    record.source_store_identity,
                    record.runtime_configuration_fingerprint,
                    record.observation_date,
                    record.captured_at_utc,
                    encoded,
                ),
            )
            if market_binding is not None:
                # Same transaction as the observation: both or neither.
                insert_binding(connection, BINDING_PREFIX, market_binding)
        return ProspectiveEvidenceCaptureResult(record, True)

    def market_bindings(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Immutable R3 bindings keyed by ``(kind, evidence_key)``; empty for a pre-R3 file."""
        if not self.database_path.is_file():
            return {}
        with _sqlite_read_only(self.database_path) as connection:
            return read_bindings(connection, BINDING_PREFIX)

    def qualification_events(self) -> dict[tuple[str, str], list[dict[str, Any]]]:
        if not self.database_path.is_file():
            return {}
        with _sqlite_read_only(self.database_path) as connection:
            return read_qualification_events(connection, BINDING_PREFIX)

    def evidence_key(self, record: ProspectivePortfolioEvidenceRecord) -> str:
        return self._evidence_key(record)

    def existing_observation(
        self,
        *,
        evidence_version: str,
        strategy_identity: str,
        source_store_identity: str,
        runtime_configuration_fingerprint: str,
        observation_date: str | date,
    ) -> ProspectivePortfolioEvidenceRecord | None:
        """Return one exact immutable observation without creating storage."""
        if not self.database_path.is_file():
            return None
        key = _identity(
            {
                "contract": EVIDENCE_CONTRACT,
                "evidence_version": str(evidence_version),
                "strategy_identity": str(strategy_identity),
                "source_store_identity": str(source_store_identity),
                "runtime_configuration_fingerprint": str(runtime_configuration_fingerprint),
                "observation_date": _as_date(observation_date),
            }
        )
        with _sqlite_read_only(self.database_path) as connection:
            self._validate_schema(connection)
            row = connection.execute(
                f"SELECT * FROM {_TABLE} WHERE evidence_key=?",
                (key,),
            ).fetchone()
        return None if row is None else self._record_from_row(row)

    def records(
        self,
        *,
        strategy_identity: str | None = None,
        source_store_identity: str | None = None,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        runtime_configuration_fingerprint: str | None = None,
        evidence_version: str | None = None,
    ) -> tuple[ProspectivePortfolioEvidenceRecord, ...]:
        if not self.database_path.is_file():
            return ()
        with _sqlite_read_only(self.database_path) as connection:
            self._validate_schema(connection)
            rows = connection.execute(
                f"SELECT * FROM {_TABLE} "
                "ORDER BY observation_date,strategy_identity,source_store_identity,"
                "runtime_configuration_fingerprint,evidence_version"
            ).fetchall()
        # Validate every immutable row before applying caller filters.  This
        # prevents a tampered denormalized query column from silently hiding a
        # record from the very query that would otherwise detect the mismatch.
        records = tuple(self._record_from_row(row) for row in rows)
        resolved_start = None if start_date is None else _as_date(start_date)
        resolved_end = None if end_date is None else _as_date(end_date)
        return tuple(
            record
            for record in records
            if (
                (strategy_identity is None or record.strategy_identity == str(strategy_identity))
                and (
                    source_store_identity is None
                    or record.source_store_identity == str(source_store_identity)
                )
                and (
                    runtime_configuration_fingerprint is None
                    or record.runtime_configuration_fingerprint
                    == str(runtime_configuration_fingerprint)
                )
                and (evidence_version is None or record.evidence_version == str(evidence_version))
                and (resolved_start is None or record.observation_date >= resolved_start)
                and (resolved_end is None or record.observation_date <= resolved_end)
            )
        )

    def latest_before(
        self,
        *,
        strategy_identity: str,
        source_store_identity: str,
        observation_date: str | date,
    ) -> ProspectivePortfolioEvidenceRecord | None:
        records = self.records(
            strategy_identity=strategy_identity,
            source_store_identity=source_store_identity,
            end_date=observation_date,
        )
        prior = tuple(item for item in records if item.observation_date < _as_date(observation_date))
        if not prior:
            return None
        latest_date = max(item.observation_date for item in prior)
        same_session = tuple(item for item in prior if item.observation_date == latest_date)
        # Configuration changes may create more than one immutable record in a
        # session.  Event deltas must resume from the greatest durable source
        # cursor, never from lexical configuration ordering.
        return max(
            same_session,
            key=lambda item: (
                item.event_cursor.fill_id,
                item.event_cursor.closed_trade_id,
                item.record_identity,
            ),
        )


def _sqlite_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"database file is missing: {path}")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    # Keep all source rows used for one record in the same SQLite read
    # snapshot.  Without an explicit transaction, a concurrent lifecycle
    # commit could mix portfolio state and event high-water marks.
    connection.execute("BEGIN")
    return connection


def _source_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def read_paper_event_cursor(database_path: str | Path) -> PaperEventCursor:
    """Read source high-water marks without initializing or modifying a store."""
    path = _canonical_path(database_path)
    if not path.is_file():
        return PaperEventCursor()
    with _sqlite_read_only(path) as connection:
        tables = _source_tables(connection)
        fill_id = 0
        closed_trade_id = 0
        if "paper_fills" in tables:
            fill_id = int(connection.execute("SELECT COALESCE(MAX(id),0) FROM paper_fills").fetchone()[0])
        if "paper_closed_trades" in tables:
            closed_trade_id = int(
                connection.execute("SELECT COALESCE(MAX(id),0) FROM paper_closed_trades").fetchone()[0]
            )
    return PaperEventCursor(fill_id=fill_id, closed_trade_id=closed_trade_id)


def read_paper_account_epoch(database_path: str | Path) -> str | None:
    """Read the opaque source-account generation without initializing a store.

    Old paper stores can legitimately lack this metadata.  Callers must treat
    that as an unavailable continuity boundary rather than synthesize one.
    """
    path = _canonical_path(database_path)
    if not path.is_file():
        return None
    with _sqlite_read_only(path) as connection:
        if "paper_metadata" not in _source_tables(connection):
            return None
        row = connection.execute(
            "SELECT value FROM paper_metadata WHERE key='account_epoch_id'"
        ).fetchone()
    if row is None:
        return None
    try:
        decoded = json.loads(str(row[0]))
    except (TypeError, ValueError, json.JSONDecodeError):
        decoded = row[0]
    epoch = str(decoded).strip()
    return epoch or None


def _metadata_number(connection: sqlite3.Connection, key: str) -> float:
    row = connection.execute("SELECT value FROM paper_metadata WHERE key=?", (key,)).fetchone()
    if row is None:
        raise ValueError(f"paper metadata is missing required key: {key}")
    try:
        decoded = json.loads(str(row[0]))
    except json.JSONDecodeError:
        decoded = row[0]
    return _required_finite(decoded, name=f"paper metadata {key}")


def _metadata_text_or_none(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM paper_metadata WHERE key=?", (key,)).fetchone()
    if row is None:
        return None
    try:
        decoded = json.loads(str(row[0]))
    except (TypeError, ValueError, json.JSONDecodeError):
        decoded = row[0]
    value = str(decoded).strip()
    return value or None


def _safe_json(value: object) -> dict[str, Any]:
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _signal_reference(payload: Mapping[str, Any]) -> float | None:
    for key in ("entry", "entry_price", "reference_price", "close"):
        value = _finite_or_none(payload.get(key))
        if value is not None:
            return value
    return None


def _read_positions(connection: sqlite3.Connection, tables: set[str]) -> tuple[ProspectivePositionEvidence, ...]:
    lifecycle = "paper_position_lifecycle" in tables
    if lifecycle:
        query = """
            SELECT p.symbol,p.quantity,p.average_price,p.market_price,p.realized_pnl,
                   l.entry_date,l.entry_order_id,l.strategy_version,l.policy_fingerprint
            FROM paper_positions p
            LEFT JOIN paper_position_lifecycle l ON l.symbol=p.symbol
            WHERE p.quantity>0
            ORDER BY UPPER(TRIM(p.symbol))
        """
    else:
        query = """
            SELECT p.symbol,p.quantity,p.average_price,p.market_price,p.realized_pnl,
                   NULL AS entry_date,NULL AS entry_order_id,NULL AS strategy_version,
                   NULL AS policy_fingerprint
            FROM paper_positions p WHERE p.quantity>0 ORDER BY UPPER(TRIM(p.symbol))
        """
    positions: list[ProspectivePositionEvidence] = []
    for row in connection.execute(query):
        quantity = int(row["quantity"])
        average = _required_finite(row["average_price"], name="paper position average price")
        market = _required_finite(row["market_price"], name="paper position market price")
        positions.append(
            ProspectivePositionEvidence(
                symbol=str(row["symbol"]),
                quantity=quantity,
                average_price=average,
                valuation_price=market,
                cost_basis=quantity * average,
                market_value=quantity * market,
                unrealized_pnl=quantity * (market - average),
                entry_date=None if row["entry_date"] is None else str(row["entry_date"]),
                entry_order_id=None if row["entry_order_id"] is None else str(row["entry_order_id"]),
                strategy_version=None if row["strategy_version"] is None else str(row["strategy_version"]),
                policy_fingerprint=None if row["policy_fingerprint"] is None else str(row["policy_fingerprint"]),
            )
        )
    return tuple(positions)


def _fill_rows(
    connection: sqlite3.Connection,
    *,
    after_id: int | None = None,
    ids: tuple[int, ...] | None = None,
) -> tuple[ProspectiveFillEvidence, ...]:
    if ids is not None:
        normalized_ids = tuple(sorted({int(item) for item in ids}))
        if not normalized_ids:
            return ()
        predicate = "f.id IN (" + ",".join("?" for _ in normalized_ids) + ")"
        values: tuple[object, ...] = normalized_ids
    else:
        if after_id is None:
            raise ValueError("fill evidence requires an event cursor or ids")
        predicate = "f.id>?"
        values = (after_id,)
    query = f"""
        SELECT f.id AS fill_id,f.order_id,f.symbol,f.side,f.quantity,f.price,
               f.gross_value,f.commission,f.slippage_cost,f.net_cash_flow,f.created_at,
               o.source_intent_id,o.reference_price,
               ps.id AS pending_signal_id,ps.signal_date,ps.payload
        FROM paper_fills f
        LEFT JOIN paper_orders o ON o.client_order_id=f.order_id
        LEFT JOIN paper_pending_signals ps
          ON o.source_intent_id=('pending_signal:' || ps.id)
        WHERE {predicate}
        ORDER BY f.id
    """
    result: list[ProspectiveFillEvidence] = []
    for row in connection.execute(query, values):
        payload = _safe_json(row["payload"])
        result.append(
            ProspectiveFillEvidence(
                fill_id=int(row["fill_id"]),
                order_id=str(row["order_id"]),
                source_intent_id=None if row["source_intent_id"] is None else str(row["source_intent_id"]),
                pending_signal_id=None if row["pending_signal_id"] is None else int(row["pending_signal_id"]),
                signal_date=None if row["signal_date"] is None else str(row["signal_date"]),
                signal_reference_price=_signal_reference(payload),
                symbol=str(row["symbol"]),
                side=str(row["side"]),
                quantity=int(row["quantity"]),
                execution_reference_price=_finite_or_none(row["reference_price"]),
                fill_price=_required_finite(row["price"], name="paper fill price"),
                gross_value=_required_finite(row["gross_value"], name="paper fill gross value"),
                commission=_required_finite(row["commission"], name="paper fill commission"),
                slippage_cost=_required_finite(row["slippage_cost"], name="paper fill slippage cost"),
                net_cash_flow=_required_finite(row["net_cash_flow"], name="paper fill net cash flow"),
                created_at=str(row["created_at"]),
            )
        )
    return tuple(result)


def _entry_provenance(
    connection: sqlite3.Connection,
    entry_order_id: str | None,
) -> Mapping[str, Any]:
    if not entry_order_id:
        return {}
    row = connection.execute(
        """
        SELECT o.client_order_id,o.reference_price,o.source_intent_id,
               f.price AS fill_price,f.gross_value,f.commission,f.slippage_cost,
               ps.id AS pending_signal_id,ps.signal_date,ps.payload
        FROM paper_orders o
        LEFT JOIN paper_fills f ON f.order_id=o.client_order_id
        LEFT JOIN paper_pending_signals ps
          ON o.source_intent_id=('pending_signal:' || ps.id)
        WHERE o.client_order_id=?
        ORDER BY f.id
        LIMIT 1
        """,
        (entry_order_id,),
    ).fetchone()
    if row is None:
        return {}
    payload = _safe_json(row["payload"])
    return {
        "entry_execution_reference_price": _finite_or_none(row["reference_price"]),
        "entry_fill_price": _finite_or_none(row["fill_price"]),
        "entry_gross_value": _finite_or_none(row["gross_value"]),
        "entry_commission": _finite_or_none(row["commission"]),
        "entry_slippage_cost": _finite_or_none(row["slippage_cost"]),
        "signal_date": None if row["signal_date"] is None else str(row["signal_date"]),
        "signal_reference_price": _signal_reference(payload),
    }


def _exit_rows(
    connection: sqlite3.Connection,
    *,
    after_id: int | None = None,
    ids: tuple[int, ...] | None = None,
) -> tuple[ProspectiveExitEvidence, ...]:
    if ids is not None:
        normalized_ids = tuple(sorted({int(item) for item in ids}))
        if not normalized_ids:
            return ()
        predicate = "c.id IN (" + ",".join("?" for _ in normalized_ids) + ")"
        values: tuple[object, ...] = normalized_ids
    else:
        if after_id is None:
            raise ValueError("exit evidence requires an event cursor or ids")
        predicate = "c.id>?"
        values = (after_id,)
    query = f"""
        SELECT c.id AS closed_trade_id,c.order_id,c.symbol,c.entry_date,c.exit_date,
               c.quantity,c.entry_price,c.exit_price,c.gross_proceeds,c.commission,
               c.realized_pnl,c.return_pct,c.holding_days,c.exit_reason,
               o.reference_price AS exit_reference_price,o.execution_context,
               f.price AS exit_fill_price,f.slippage_cost AS exit_slippage_cost
        FROM paper_closed_trades c
        LEFT JOIN paper_orders o ON o.client_order_id=c.order_id
        LEFT JOIN paper_fills f ON f.order_id=c.order_id
        WHERE {predicate}
        ORDER BY c.id,f.id
    """
    results: list[ProspectiveExitEvidence] = []
    seen: set[int] = set()
    for row in connection.execute(query, values):
        closed_id = int(row["closed_trade_id"])
        if closed_id in seen:
            continue
        seen.add(closed_id)
        context = _safe_json(row["execution_context"])
        exit_context = context.get("exit") if isinstance(context.get("exit"), dict) else {}
        entry_order_id = exit_context.get("entry_order_id")
        entry_order_id = str(entry_order_id) if entry_order_id else None
        entry = _entry_provenance(connection, entry_order_id)
        complete = bool(entry_order_id and entry.get("entry_fill_price") is not None)
        entry_gross = entry.get("entry_gross_value")
        gross_pnl = None
        if entry_gross is not None:
            gross_pnl = _required_finite(row["gross_proceeds"], name="closed trade gross proceeds") - float(entry_gross)
        results.append(
            ProspectiveExitEvidence(
                closed_trade_id=closed_id,
                order_id=str(row["order_id"]),
                symbol=str(row["symbol"]),
                entry_date=str(row["entry_date"]),
                exit_date=str(row["exit_date"]),
                quantity=int(row["quantity"]),
                lifecycle_entry_price=_required_finite(row["entry_price"], name="closed trade entry price"),
                closed_exit_price=_required_finite(row["exit_price"], name="closed trade exit price"),
                gross_proceeds=_required_finite(row["gross_proceeds"], name="closed trade gross proceeds"),
                exit_commission=_required_finite(row["commission"], name="closed trade commission"),
                realized_pnl=_required_finite(row["realized_pnl"], name="closed trade realized PnL"),
                return_pct=_required_finite(row["return_pct"], name="closed trade return pct"),
                holding_days=int(row["holding_days"]),
                exit_reason=str(row["exit_reason"]),
                entry_order_id=entry_order_id,
                strategy_version=(str(exit_context["strategy_version"]) if exit_context.get("strategy_version") else None),
                policy_fingerprint=(str(exit_context["policy_fingerprint"]) if exit_context.get("policy_fingerprint") else None),
                signal_date=entry.get("signal_date"),
                signal_reference_price=entry.get("signal_reference_price"),
                entry_execution_reference_price=entry.get("entry_execution_reference_price"),
                entry_fill_price=entry.get("entry_fill_price"),
                entry_commission=entry.get("entry_commission"),
                entry_slippage_cost=entry.get("entry_slippage_cost"),
                exit_execution_reference_price=_finite_or_none(row["exit_reference_price"]),
                exit_fill_price=_finite_or_none(row["exit_fill_price"]),
                exit_slippage_cost=_finite_or_none(row["exit_slippage_cost"]),
                gross_pnl=gross_pnl,
                provenance_status=("COMPLETE_SOURCE_LINK" if complete else "UNAVAILABLE_PRE_EVIDENCE_EXIT_LINK"),
            )
        )
    return tuple(results)


def _read_source_state(
    paper_database_path: Path,
    *,
    baseline_cursor: PaperEventCursor,
    expected_account_epoch_id: str | None,
    replay_record: ProspectivePortfolioEvidenceRecord | None = None,
) -> tuple[dict[str, Any], PaperEventCursor, str | None]:
    with _sqlite_read_only(paper_database_path) as connection:
        tables = _source_tables(connection)
        required = {"paper_metadata", "paper_positions", "paper_fills", "paper_orders", "paper_closed_trades", "paper_pending_signals"}
        missing = sorted(required - tables)
        if missing:
            raise ValueError("paper database is missing evidence source tables: " + ", ".join(missing))
        account_epoch_id = _metadata_text_or_none(connection, "account_epoch_id")
        if account_epoch_id != expected_account_epoch_id:
            raise RuntimeError(
                "paper account epoch changed while prospective evidence was being captured"
            )
        cash = _metadata_number(connection, "cash")
        realized = _metadata_number(connection, "realized_pnl")
        positions = _read_positions(connection, tables)
        positions_value = sum(item.market_value for item in positions)
        unrealized = sum(item.unrealized_pnl for item in positions)
        equity = cash + positions_value
        exposure = positions_value / equity * 100.0 if equity > 0 else 0.0
        cursor = PaperEventCursor(
            fill_id=int(connection.execute("SELECT COALESCE(MAX(id),0) FROM paper_fills").fetchone()[0]),
            closed_trade_id=int(connection.execute("SELECT COALESCE(MAX(id),0) FROM paper_closed_trades").fetchone()[0]),
        )
        if replay_record is None:
            fills = _fill_rows(connection, after_id=baseline_cursor.fill_id)
            exits = _exit_rows(connection, after_id=baseline_cursor.closed_trade_id)
        else:
            fills = _fill_rows(
                connection,
                ids=tuple(item.fill_id for item in replay_record.fills_since_previous),
            )
            exits = _exit_rows(
                connection,
                ids=tuple(item.closed_trade_id for item in replay_record.exits_since_previous),
            )
    return {
        "cash": cash,
        "realized_pnl": realized,
        "positions": positions,
        "positions_value": positions_value,
        "unrealized_pnl": unrealized,
        "equity": equity,
        "gross_exposure_pct": exposure,
        "fills": fills,
        "exits": exits,
    }, cursor, account_epoch_id


def _market_sessions(
    market_database_path: Path,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[str, ...]:
    if not market_database_path.is_file():
        return ()
    predicates = ["UPPER(TRIM(symbol))=?"]
    values: list[object] = [_BENCHMARK]
    if start_date is not None:
        predicates.append("date(time)>=?")
        values.append(_as_date(start_date))
    if end_date is not None:
        predicates.append("date(time)<=?")
        values.append(_as_date(end_date))
    with _sqlite_read_only(market_database_path) as connection:
        rows = connection.execute(
            "SELECT DISTINCT date(time) FROM prices WHERE " + " AND ".join(predicates) + " ORDER BY date(time)",
            tuple(values),
        ).fetchall()
    return tuple(str(row[0]) for row in rows if row[0] is not None)


def _market_context(market_database_path: Path, observation_date: str) -> tuple[dict[str, Any], tuple[str, ...]]:
    warnings: list[str] = []
    result: dict[str, Any] = {
        "market_database_path": str(market_database_path),
        "market_database_sha256": _hash_file(market_database_path),
        "market_data_reference_session": None,
        "previous_benchmark_session": None,
        "benchmark_close": None,
        "market_regime_label": "UNKNOWN",
        "regime_computation_identity": _REGIME_COMPUTATION_IDENTITY,
    }
    if not market_database_path.is_file():
        warnings.append("MARKET_DATABASE_UNAVAILABLE")
        return result, tuple(warnings)
    try:
        with _sqlite_read_only(market_database_path) as connection:
            benchmark = connection.execute(
                """
                SELECT date(time) AS session,close
                FROM prices
                WHERE UPPER(TRIM(symbol))=? AND date(time)=?
                ORDER BY rowid DESC LIMIT 1
                """,
                (_BENCHMARK, observation_date),
            ).fetchone()
            history = connection.execute(
                """
                SELECT time,close FROM prices
                WHERE UPPER(TRIM(symbol))=? AND date(time)<=?
                ORDER BY time,rowid
                """,
                (_BENCHMARK, observation_date),
            ).fetchall()
            previous = connection.execute(
                """
                SELECT MAX(date(time)) FROM prices
                WHERE UPPER(TRIM(symbol))=? AND date(time)<?
                """,
                (_BENCHMARK, observation_date),
            ).fetchone()
    except sqlite3.Error as exc:
        warnings.append(f"MARKET_DATABASE_READ_ERROR:{type(exc).__name__}")
        return result, tuple(warnings)
    if benchmark is None or _finite_or_none(benchmark["close"]) is None:
        warnings.append("BENCHMARK_CLOSE_UNAVAILABLE_AT_OBSERVATION")
    else:
        result["market_data_reference_session"] = str(benchmark["session"])
        result["benchmark_close"] = _finite_or_none(benchmark["close"])
    if previous is not None and previous[0] is not None:
        result["previous_benchmark_session"] = str(previous[0])
    try:
        import pandas as pd
        from strategy.market_regime import prepare_market_regime_history

        frame = pd.DataFrame(((row["time"], row["close"]) for row in history), columns=("time", "close"))
        prepared = prepare_market_regime_history(frame)
        if not prepared.empty:
            latest = prepared.loc[prepared["time"].dt.strftime("%Y-%m-%d") == observation_date]
            if not latest.empty:
                result["market_regime_label"] = str(latest.iloc[-1]["Market_Regime"])
        if result["market_regime_label"] == "UNKNOWN":
            warnings.append("MARKET_REGIME_UNKNOWN_OR_INSUFFICIENT_HISTORY")
    except (ImportError, ValueError, TypeError, KeyError) as exc:
        warnings.append(f"MARKET_REGIME_UNAVAILABLE:{type(exc).__name__}")
    warnings.append("LOCAL_MARKET_DATABASE_PROVIDER_PROVENANCE_UNAVAILABLE")
    return result, tuple(warnings)


def _source_state_matches_record(
    *,
    state: Mapping[str, Any],
    cursor: PaperEventCursor,
    account_epoch_id: str | None,
    record: ProspectivePortfolioEvidenceRecord,
) -> bool:
    """Recognize a safe same-session replay without hiding changed state."""
    if account_epoch_id != record.source_account_epoch_id or cursor != record.event_cursor:
        return False
    numeric_fields = (
        ("cash", record.cash),
        ("positions_value", record.positions_value),
        ("equity", record.equity),
        ("realized_pnl", record.realized_pnl),
        ("unrealized_pnl", record.unrealized_pnl),
        ("gross_exposure_pct", record.gross_exposure_pct),
    )
    if any(
        not math.isclose(float(state[name]), expected, rel_tol=0.0, abs_tol=1e-6)
        for name, expected in numeric_fields
    ):
        return False
    return (
        tuple(state["positions"]) == record.positions
        and tuple(state["fills"]) == record.fills_since_previous
        and tuple(state["exits"]) == record.exits_since_previous
    )


#: paper prices are market closes times this scale (execution.signal_executor.PRICE_SCALE).
_PAPER_PRICE_SCALE = 1000.0
_MARK_LOOKBACK_SESSIONS = 10


def _mark_checks(
    market_path: Path, observation_date: str, positions: Iterable[ProspectivePositionEvidence]
) -> list[dict[str, Any]]:
    """Prove (or fail to prove) that each stored mark is the observation-session market close."""
    checks: list[dict[str, Any]] = []
    positions = tuple(positions)
    if not positions:
        return checks
    connection = None
    try:
        connection = _sqlite_read_only(market_path)
    except sqlite3.Error:
        connection = None
    for item in positions:
        check: dict[str, Any] = {
            "symbol": item.symbol,
            "stored_mark": item.valuation_price,
            "market_close_scaled": None,
            "matched_session": None,
            "status": "NO_MARKET_CLOSE",
        }
        if connection is not None:
            try:
                rows = connection.execute(
                    "SELECT date(time) AS session, close FROM prices WHERE UPPER(TRIM(symbol))=? "
                    "AND date(time)<=? ORDER BY date(time) DESC, rowid DESC LIMIT ?",
                    (item.symbol, observation_date, _MARK_LOOKBACK_SESSIONS),
                ).fetchall()
            except sqlite3.Error:
                rows = []
            seen: set[str] = set()
            for row in rows:
                session, close = str(row["session"]), _finite_or_none(row["close"])
                if session in seen or close is None:
                    continue
                seen.add(session)
                scaled = close * _PAPER_PRICE_SCALE
                if session == observation_date:
                    check["market_close_scaled"] = scaled
                    check["status"] = "STALE_OR_UNMATCHED"
                if math.isclose(item.valuation_price, scaled, rel_tol=1e-9, abs_tol=1e-6):
                    check["matched_session"] = session
                    check["status"] = (
                        "MATCHES_OBSERVATION_CLOSE" if session == observation_date else "STALE_OR_UNMATCHED"
                    )
                    break
        checks.append(check)
    if connection is not None:
        connection.close()
    return checks


def _closed_trade_count(paper_path: Path, through_closed_trade_id: int) -> int:
    with _sqlite_read_only(paper_path) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM paper_closed_trades WHERE id<=?", (int(through_closed_trade_id),)
            ).fetchone()[0]
        )


def _paper_order_legs(paper_path: Path, order_ids: Iterable[str]) -> dict[str, dict[str, Any] | None]:
    """Compact copies of the market bindings carried by the given paper orders (``None`` = legacy)."""
    wanted = sorted({str(item) for item in order_ids if item})
    result: dict[str, dict[str, Any] | None] = {item: None for item in wanted}
    if not wanted:
        return result
    with _sqlite_read_only(paper_path) as connection:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(paper_orders)")}
        if "execution_context" not in columns:
            return result
        marks = ",".join("?" for _ in wanted)
        for row in connection.execute(
            f"SELECT client_order_id, execution_context FROM paper_orders WHERE client_order_id IN ({marks})",
            wanted,
        ):
            binding = _safe_json(row["execution_context"]).get("market_binding")
            result[str(row["client_order_id"])] = compact_leg(binding) if isinstance(binding, dict) else None
    return result



def capture_prospective_portfolio_evidence(
    *,
    observation_date: str | date | datetime,
    paper_database_path: str | Path,
    source_store_id: str,
    strategy_identity: str,
    runtime_configuration_fingerprint: str,
    market_database_path: str | Path | None = None,
    evidence_database_path: str | Path | None = None,
    baseline_event_cursor: PaperEventCursor | None = None,
    captured_at_utc: str | None = None,
    lifecycle_warnings: Iterable[str] = (),
    strategy_identity_v3: Any = None,
) -> ProspectiveEvidenceCaptureResult:
    """Capture one immutable observation after a successful paper lifecycle.

    The source paper store is only read.  On first activation the optional
    ``baseline_event_cursor`` lets the caller capture events created during the
    just-completed lifecycle while intentionally avoiding pre-activation event
    backfill.  Without either prior evidence or a supplied baseline, historical
    fill/exit events are deliberately not reconstructed.
    """
    resolved_date = _as_date(observation_date)
    paper_path = _canonical_path(paper_database_path)
    market_path = resolve_market_database_path(market_database_path)
    account_epoch_id = read_paper_account_epoch(paper_path)
    source_identity = paper_store_identity(
        store_id=source_store_id,
        strategy_identity=strategy_identity,
        database_path=paper_path,
        account_epoch_id=account_epoch_id,
    )
    ledger = ProspectivePortfolioEvidenceLedger(evidence_database_path)
    existing = ledger.existing_observation(
        evidence_version=EVIDENCE_VERSION,
        strategy_identity=strategy_identity,
        source_store_identity=source_identity,
        runtime_configuration_fingerprint=runtime_configuration_fingerprint,
        observation_date=resolved_date,
    )
    if existing is not None:
        state, cursor, observed_epoch_id = _read_source_state(
            paper_path,
            baseline_cursor=existing.event_cursor,
            expected_account_epoch_id=account_epoch_id,
            replay_record=existing,
        )
        if _source_state_matches_record(
            state=state,
            cursor=cursor,
            account_epoch_id=observed_epoch_id,
            record=existing,
        ):
            return ProspectiveEvidenceCaptureResult(existing, False)
        raise ValueError(
            "conflicting prospective evidence source state for the same "
            "session, strategy, account, and configuration"
        )
    # R3: pin the dataset version at the decision point, before the market
    # context and paper state used by this observation are read.
    binding_session = MarketBindingSession(market_path)
    prior = ledger.latest_before(
        strategy_identity=strategy_identity,
        source_store_identity=source_identity,
        observation_date=resolved_date,
    )
    warnings = list(str(item) for item in lifecycle_warnings if str(item))
    if prior is not None:
        source_baseline = prior.event_cursor
    elif baseline_event_cursor is not None:
        source_baseline = baseline_event_cursor
    else:
        source_baseline = read_paper_event_cursor(paper_path)
        warnings.append("PRE_ACTIVATION_FILL_AND_EXIT_EVENTS_NOT_BACKFILLED")
    state, cursor, observed_epoch_id = _read_source_state(
        paper_path,
        baseline_cursor=source_baseline,
        expected_account_epoch_id=account_epoch_id,
    )
    market, market_warnings = _market_context(market_path, resolved_date)
    warnings.extend(market_warnings)
    daily_pnl = None
    daily_return = None
    running_peak = None
    drawdown = None
    if observed_epoch_id is None:
        warnings.append("SOURCE_ACCOUNT_EPOCH_UNAVAILABLE_DAILY_RETURN_UNAVAILABLE")
    elif prior is None:
        running_peak = state["equity"]
        drawdown = 0.0
        warnings.append("DAILY_RETURN_UNAVAILABLE_FIRST_ACCOUNT_EPOCH_OBSERVATION")
    else:
        expected_previous = market["previous_benchmark_session"]
        if expected_previous is None or prior.observation_date != expected_previous:
            warnings.append("EQUITY_CONTINUITY_GAP_DAILY_RETURN_UNAVAILABLE")
        elif prior.running_equity_peak is None:
            warnings.append("PRIOR_EQUITY_PEAK_UNAVAILABLE")
        else:
            daily_pnl = state["equity"] - prior.equity
            daily_return = (daily_pnl / prior.equity * 100.0) if prior.equity != 0 else None
            running_peak = max(prior.running_equity_peak, state["equity"])
            drawdown = (state["equity"] / running_peak - 1.0) * 100.0 if running_peak != 0 else None
    record = ProspectivePortfolioEvidenceRecord(
        evidence_version=EVIDENCE_VERSION,
        strategy_identity=str(strategy_identity).strip(),
        source_store_id=str(source_store_id).strip(),
        source_store_identity=source_identity,
        source_account_epoch_id=observed_epoch_id,
        runtime_configuration_fingerprint=str(runtime_configuration_fingerprint).strip(),
        observation_date=resolved_date,
        captured_at_utc=captured_at_utc or _utc_now(),
        paper_database_path=str(paper_path),
        market_database_path=str(market_path),
        market_database_sha256=market["market_database_sha256"],
        market_data_reference_session=market["market_data_reference_session"],
        benchmark_symbol=_BENCHMARK,
        benchmark_close=market["benchmark_close"],
        market_regime_label=market["market_regime_label"],
        regime_computation_identity=market["regime_computation_identity"],
        cash=state["cash"],
        positions_value=state["positions_value"],
        equity=state["equity"],
        realized_pnl=state["realized_pnl"],
        unrealized_pnl=state["unrealized_pnl"],
        gross_exposure_pct=state["gross_exposure_pct"],
        open_position_count=len(state["positions"]),
        positions=state["positions"],
        fills_since_previous=state["fills"],
        exits_since_previous=state["exits"],
        event_cursor=cursor,
        daily_pnl=daily_pnl,
        daily_return_pct=daily_return,
        running_equity_peak=running_peak,
        drawdown_pct=drawdown,
        provenance_warnings=tuple(warnings),
        **_strategy_identity_v3_kwargs(strategy_identity_v3),
    )
    # R3: what the observation's valuation actually consumed.
    # * open-position marks: the stored paper mark is only a MARK_CLOSE of the observation
    #   session if it provably equals that session's market close; otherwise it is recorded
    #   as an unproven source (never labelled as a current-session close);
    # * realized PnL / cost basis: compact copies of the entry/exit legs of every trade that
    #   contributed, kept in this immutable binding so they survive a paper-store reset.
    marks = _mark_checks(market_path, resolved_date, record.positions)
    marks_by_symbol = {check["symbol"]: check for check in marks}
    prior_binding = None
    if prior is not None:
        prior_binding = ledger.market_bindings().get((OBSERVATION_BINDING_KIND, ledger.evidence_key(prior)))
    prior_tracked = int(((prior_binding or {}).get("lineage") or {}).get("tracked_trade_count", 0))
    legs = _paper_order_legs(
        paper_path,
        tuple(
            {item.order_id for item in record.exits_since_previous}
            | {item.entry_order_id for item in record.exits_since_previous if item.entry_order_id}
            | {item.entry_order_id for item in record.positions if item.entry_order_id}
        ),
    )
    lineage = {
        "mark_checks": marks,
        "closed_trade_count": _closed_trade_count(paper_path, record.event_cursor.closed_trade_id),
        "tracked_trade_count": prior_tracked + len(record.exits_since_previous),
        "trades_added": [
            {
                "closed_trade_id": item.closed_trade_id,
                "symbol": item.symbol,
                "exit_order_id": item.order_id,
                "entry_order_id": item.entry_order_id,
                "entry": legs.get(item.entry_order_id) if item.entry_order_id else None,
                "exit": legs.get(item.order_id),
            }
            for item in record.exits_since_previous
        ],
        "open_entries": [
            {
                "symbol": item.symbol,
                "entry_order_id": item.entry_order_id,
                "entry": legs.get(item.entry_order_id) if item.entry_order_id else None,
            }
            for item in record.positions
        ],
    }
    binding = binding_session.bind(
        OBSERVATION_BINDING_KIND,
        ledger.evidence_key(record),
        (
            *(
                (
                    ("MARK_CLOSE", item.symbol, resolved_date, item.entry_date)
                    if marks_by_symbol.get(item.symbol, {}).get("status") == "MATCHES_OBSERVATION_CLOSE"
                    else ("STORED_MARK_UNVERIFIED", item.symbol, resolved_date, None, COVERAGE_UNPROVEN)
                )
                for item in record.positions
            ),
            *(
                (("BENCHMARK_CLOSE", _BENCHMARK, resolved_date),)
                if record.benchmark_close is not None
                else ()
            ),
        ),
        context={
            "strategy_identity": record.strategy_identity,
            "source_store_identity": record.source_store_identity,
        },
        lineage=lineage,
    )
    return ledger.append(record, market_binding=binding)


def _strategy_identity_v3_kwargs(identity: Any) -> dict[str, Any]:
    """v3 identity and contract attestation for a NEW record.

    ``identity`` is a ``quantlab.strategy_identity.StrategyIdentityV3`` (or
    None). Fingerprints are recomputed from its contracts and the status is
    derived against the canonical contract here; the record re-verifies all of
    it from the stored contracts. Callers cannot supply a status.
    """
    if identity is None:
        return {}
    from quantlab.strategy_contract import evidence_attestation_fields

    fields = evidence_attestation_fields(identity)
    return {
        name: fields[name]
        for name in (
            *_STRATEGY_IDENTITY_V3_FIELDS,
            *_STRATEGY_CONTRACT_FIELDS,
            *_STRATEGY_BEHAVIOR_CONTRACT_FIELDS,
        )
    }


def _freeze_contract(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_contract(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_contract(item) for item in value)
    return value


def _normalized_contract(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("strategy behavioral contract must be a mapping")
    return json.loads(canonical_json(canonical_identity_value(value)).decode("utf-8"))


def inspect_prospective_portfolio_evidence(
    *,
    evidence_database_path: str | Path | None = None,
    market_database_path: str | Path | None = None,
    source_store_id: str,
    strategy_identity: str,
    paper_database_path: str | Path,
    root: Path = PROJECT_ROOT,
) -> ProspectiveEvidenceStatus:
    """Inspect persisted evidence without creating/migrating any database."""
    path = resolve_evidence_database_path(evidence_database_path, root=root)
    if not path.is_file():
        return ProspectiveEvidenceStatus(path, False, False, "MISSING")
    source_identity = paper_store_identity(
        store_id=source_store_id,
        strategy_identity=strategy_identity,
        database_path=_canonical_path(paper_database_path, root=root),
        account_epoch_id=read_paper_account_epoch(
            _canonical_path(paper_database_path, root=root)
        ),
    )
    try:
        ledger = ProspectivePortfolioEvidenceLedger(path)
        records = ledger.records(
            strategy_identity=strategy_identity,
            source_store_identity=source_identity,
        )
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return ProspectiveEvidenceStatus(
                path,
                True,
                True,
                "SCHEMA_MISMATCH",
                capture_state="UNKNOWN",
                continuity_state="UNKNOWN",
                error=f"{type(exc).__name__}: {exc}",
            )
        return ProspectiveEvidenceStatus(
            path,
            True,
            False,
            "UNREADABLE",
            capture_state="UNKNOWN",
            continuity_state="UNKNOWN",
            error=f"{type(exc).__name__}: {exc}",
        )
    except (OSError, sqlite3.Error, ValueError, KeyError, json.JSONDecodeError) as exc:
        return ProspectiveEvidenceStatus(
            path,
            True,
            False,
            "UNREADABLE",
            capture_state="UNKNOWN",
            continuity_state="UNKNOWN",
            error=f"{type(exc).__name__}: {exc}",
        )
    if not records:
        return ProspectiveEvidenceStatus(
            path,
            True,
            True,
            "OK",
            observation_count=0,
            capture_state="NOT_STARTED",
            continuity_state="INTENTIONALLY_UNAVAILABLE_PRE_ACTIVATION",
        )
    market_path = resolve_market_database_path(market_database_path)
    warnings: list[str] = []
    missing: tuple[str, ...] = ()
    continuity = "UNKNOWN"
    if not market_path.is_file():
        warnings.append("MARKET_SESSION_CONTINUITY_UNAVAILABLE:MARKET_DATABASE_MISSING")
    else:
        try:
            sessions = _market_sessions(market_path, start_date=records[0].observation_date)
            present = {item.observation_date for item in records}
            missing = tuple(item for item in sessions if item not in present)
            continuity = "CONTINUOUS" if not missing else "GAPS_DETECTED"
        except (OSError, sqlite3.Error, ValueError) as exc:
            warnings.append(f"MARKET_SESSION_CONTINUITY_UNAVAILABLE:{type(exc).__name__}")
    latest = records[-1]
    return ProspectiveEvidenceStatus(
        path,
        True,
        True,
        "OK",
        observation_count=len(records),
        latest_observation_date=latest.observation_date,
        latest_strategy_identity=latest.strategy_identity,
        latest_configuration_fingerprint=latest.runtime_configuration_fingerprint,
        capture_state="SUCCEEDED",
        continuity_state=continuity,
        missing_sessions=missing,
        warnings=tuple(warnings),
    )


__all__ = (
    "DEFAULT_EVIDENCE_DATABASE_PATH",
    "EVIDENCE_CONTRACT",
    "EVIDENCE_VERSION",
    "PaperEventCursor",
    "ProspectiveEvidenceCaptureResult",
    "ProspectiveEvidenceStatus",
    "ProspectiveFillEvidence",
    "ProspectivePortfolioEvidenceLedger",
    "ProspectivePortfolioEvidenceRecord",
    "ProspectivePositionEvidence",
    "ProspectiveExitEvidence",
    "capture_prospective_portfolio_evidence",
    "inspect_prospective_portfolio_evidence",
    "paper_store_identity",
    "read_paper_account_epoch",
    "read_paper_event_cursor",
    "resolve_evidence_database_path",
)
