from __future__ import annotations

"""Neutral, research-only execution contracts over supported repository evidence."""

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import math
from types import MappingProxyType
from typing import Any, Mapping

from quantlab.identity import canonical_identity_value, canonical_json


CONTRACT = "quantlab.neutral_execution_foundation"
VERSION = "v1"


def _hash(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _symbol(value: str) -> str:
    normalized = str(value).strip().upper()
    if not normalized:
        raise ValueError("symbol must be non-empty")
    return normalized


class CapabilityState(str, Enum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNAVAILABLE = "UNAVAILABLE"


class ConstraintImplementationState(str, Enum):
    IMPLEMENTED_CANONICALLY = "IMPLEMENTED_CANONICALLY"
    CURRENT_OPERATIONAL_ONLY = "CURRENT_OPERATIONAL_ONLY"
    SUPPORTED_BUT_NOT_IMPLEMENTED = "SUPPORTED_BUT_NOT_IMPLEMENTED"
    EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"
    DEFERRED = "DEFERRED"


class FrictionProvenance(str, Enum):
    CANONICAL = "CANONICAL"
    ASSUMPTION = "ASSUMPTION"
    CURRENT_OPERATIONAL_ONLY = "CURRENT_OPERATIONAL_ONLY"
    UNAVAILABLE = "UNAVAILABLE"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderIntentState(str, Enum):
    AVAILABLE = "AVAILABLE"
    NO_ORDER_REQUIRED = "NO_ORDER_REQUIRED"
    MISSING_REFERENCE_PRICE = "MISSING_REFERENCE_PRICE"
    NONPOSITIVE_REFERENCE_PRICE = "NONPOSITIVE_REFERENCE_PRICE"


class ParticipationState(str, Enum):
    AVAILABLE_DESCRIPTIVE_ONLY = "AVAILABLE_DESCRIPTIVE_ONLY"
    NO_ORDER_REQUIRED = "NO_ORDER_REQUIRED"
    ORDER_INTENT_UNAVAILABLE = "ORDER_INTENT_UNAVAILABLE"
    VOLUME_UNAVAILABLE = "VOLUME_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ExecutionCapability:
    area: str
    capability: str
    state: CapabilityState
    implementation_state: ConstraintImplementationState
    audit_classification: str
    evidence: str
    limitation: str
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "identity", _hash({
            "contract": (CONTRACT, VERSION), "area": self.area,
            "capability": self.capability, "state": self.state.value,
            "implementation_state": self.implementation_state.value,
            "audit_classification": self.audit_classification,
            "evidence": self.evidence, "limitation": self.limitation,
        }))


CAPABILITY_MATRIX = (
    ExecutionCapability("historical_data", "daily_ohlc", CapabilityState.SUPPORTED, ConstraintImplementationState.IMPLEMENTED_CANONICALLY, "A_REUSABLE_NEUTRAL_INFRA", "prices.open/high/low/close at daily frequency", "daily bars cannot identify intraday path or fill sequence"),
    ExecutionCapability("historical_data", "daily_volume", CapabilityState.SUPPORTED, ConstraintImplementationState.IMPLEMENTED_CANONICALLY, "A_REUSABLE_NEUTRAL_INFRA", "prices.volume", "supports participation diagnostics only, not fill probability"),
    ExecutionCapability("historical_data", "traded_value", CapabilityState.UNAVAILABLE, ConstraintImplementationState.EVIDENCE_UNAVAILABLE, "G_EVIDENCE_UNAVAILABLE", "no traded-value column", "close times volume is only a proxy, not canonical traded value"),
    ExecutionCapability("historical_data", "adjusted_vs_raw_price", CapabilityState.PARTIALLY_SUPPORTED, ConstraintImplementationState.EVIDENCE_UNAVAILABLE, "G_EVIDENCE_UNAVAILABLE", "updater stores provider EOD OHLC without an adjustment flag", "execution-safe raw-price and corporate-action provenance are not established"),
    ExecutionCapability("historical_data", "bid_ask_intraday_order_book_auction_foreign_room", CapabilityState.UNAVAILABLE, ConstraintImplementationState.EVIDENCE_UNAVAILABLE, "G_EVIDENCE_UNAVAILABLE", "no canonical fields or tables", "spread, queue, auction, and intraday fills cannot be reconstructed"),
    ExecutionCapability("market_rule", "board_lot", CapabilityState.PARTIALLY_SUPPORTED, ConstraintImplementationState.CURRENT_OPERATIONAL_ONLY, "D_PAPER_LIVE_OPERATIONAL", "paper/backtest configuration defaults to 100 shares", "not historically versioned and not applied by the neutral translator"),
    ExecutionCapability("market_rule", "tick_size", CapabilityState.UNAVAILABLE, ConstraintImplementationState.EVIDENCE_UNAVAILABLE, "F_MISSING", "no canonical rule metadata", "no price rounding is applied"),
    ExecutionCapability("market_rule", "price_band", CapabilityState.UNAVAILABLE, ConstraintImplementationState.EVIDENCE_UNAVAILABLE, "F_MISSING", "no canonical rule metadata", "no ceiling/floor validation is applied"),
    ExecutionCapability("market_rule", "tradability_status", CapabilityState.UNAVAILABLE, ConstraintImplementationState.EVIDENCE_UNAVAILABLE, "F_MISSING", "no suspension or trading-status history", "an OHLCV row does not prove unrestricted tradability"),
    ExecutionCapability("price_timing", "next_session_open", CapabilityState.PARTIALLY_SUPPORTED, ConstraintImplementationState.SUPPORTED_BUT_NOT_IMPLEMENTED, "C_BACKTEST_ASSUMPTION", "daily open and VNINDEX session calendar support next-open research timing", "Phase 11A produces intents only and does not assume a realized fill"),
    ExecutionCapability("friction", "commission_fee", CapabilityState.PARTIALLY_SUPPORTED, ConstraintImplementationState.CURRENT_OPERATIONAL_ONLY, "D_PAPER_LIVE_OPERATIONAL", "paper/backtest configurable rates", "no canonical historically versioned fee schedule"),
    ExecutionCapability("friction", "sell_side_tax", CapabilityState.PARTIALLY_SUPPORTED, ConstraintImplementationState.CURRENT_OPERATIONAL_ONLY, "D_PAPER_LIVE_OPERATIONAL", "paper/backtest configurable rates", "no canonical historically versioned tax schedule"),
    ExecutionCapability("friction", "spread_slippage", CapabilityState.PARTIALLY_SUPPORTED, ConstraintImplementationState.CURRENT_OPERATIONAL_ONLY, "C_BACKTEST_ASSUMPTION", "fixed-bps paper/backtest assumption", "not measured bid/ask spread or a fitted historical model"),
    ExecutionCapability("friction", "market_impact", CapabilityState.UNAVAILABLE, ConstraintImplementationState.EVIDENCE_UNAVAILABLE, "G_EVIDENCE_UNAVAILABLE", "daily OHLCV only", "no defensible impact or fill-probability model"),
    ExecutionCapability("settlement", "cash_and_sell_availability", CapabilityState.PARTIALLY_SUPPORTED, ConstraintImplementationState.CURRENT_OPERATIONAL_ONLY, "D_PAPER_LIVE_OPERATIONAL", "paper broker checks current cash and held quantity", "no historically versioned settlement or T+ schedule"),
    ExecutionCapability("corporate_action", "share_and_price_adjustment", CapabilityState.UNAVAILABLE, ConstraintImplementationState.EVIDENCE_UNAVAILABLE, "G_EVIDENCE_UNAVAILABLE", "no corporate-action table or raw/adjusted marker", "historical executable quantities and raw prices cannot be guaranteed"),
)


@dataclass(frozen=True, slots=True)
class ExecutionFrictionComponent:
    name: str
    rate_bps: float | None
    provenance: FrictionProvenance
    note: str

    def __post_init__(self) -> None:
        if self.rate_bps is not None and (not math.isfinite(self.rate_bps) or self.rate_bps < 0):
            raise ValueError("friction rate_bps must be finite and nonnegative")
        if self.provenance is FrictionProvenance.UNAVAILABLE and self.rate_bps is not None:
            raise ValueError("unavailable friction cannot carry a numeric rate")


@dataclass(frozen=True, slots=True)
class ExecutionFrictionSpec:
    commission_fee: ExecutionFrictionComponent
    sell_side_tax: ExecutionFrictionComponent
    spread_slippage: ExecutionFrictionComponent
    market_impact: ExecutionFrictionComponent
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "fingerprint", _hash({
            "contract": (CONTRACT, VERSION),
            "components": tuple((item.name, item.rate_bps, item.provenance.value, item.note) for item in (
                self.commission_fee, self.sell_side_tax, self.spread_slippage, self.market_impact,
            )),
        }))


NEUTRAL_EXECUTION_FRICTION_V1 = ExecutionFrictionSpec(
    ExecutionFrictionComponent("commission_fee", None, FrictionProvenance.UNAVAILABLE, "historically versioned schedule unavailable"),
    ExecutionFrictionComponent("sell_side_tax", None, FrictionProvenance.UNAVAILABLE, "historically versioned schedule unavailable"),
    ExecutionFrictionComponent("spread_slippage", None, FrictionProvenance.UNAVAILABLE, "bid/ask and fitted slippage evidence unavailable"),
    ExecutionFrictionComponent("market_impact", None, FrictionProvenance.UNAVAILABLE, "daily OHLCV cannot identify market impact"),
)


@dataclass(frozen=True, slots=True)
class ExecutionSpec:
    name: str = "NEUTRAL_EXECUTION_FOUNDATION_V1"
    version: str = "1"
    long_only: bool = True
    quantity_semantics: str = "fractional_theoretical_share_delta_no_rounding"
    price_semantics: str = "known_reference_price_only_not_a_fill_price"
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if self.version != "1" or not self.long_only:
            raise ValueError("unsupported execution foundation specification")
        object.__setattr__(self, "fingerprint", _hash({
            "contract": (CONTRACT, VERSION), "name": self.name, "version": self.version,
            "long_only": self.long_only, "quantity_semantics": self.quantity_semantics,
            "price_semantics": self.price_semantics,
            "constraints": {
                "lot": "CURRENT_OPERATIONAL_ONLY_NOT_APPLIED",
                "tick": "EVIDENCE_UNAVAILABLE_NOT_APPLIED",
                "price_band": "EVIDENCE_UNAVAILABLE_NOT_APPLIED",
                "tradability": "EVIDENCE_UNAVAILABLE_NOT_INFERRED_FROM_OHLCV",
            },
            "participation": "absolute_theoretical_share_delta_divided_by_exact_date_daily_volume_descriptive_only",
            "market_impact": "EVIDENCE_UNAVAILABLE",
            "friction": NEUTRAL_EXECUTION_FRICTION_V1.fingerprint,
        }))


NEUTRAL_EXECUTION_FOUNDATION_V1 = ExecutionSpec()


@dataclass(frozen=True, slots=True)
class TheoreticalOrderIntent:
    symbol: str
    side: OrderSide | None
    state: OrderIntentState
    current_quantity: int
    target_weight: float
    target_notional: float
    reference_price: float | None
    theoretical_target_quantity: float | None
    theoretical_share_delta: float | None
    formation_date: str
    reference_price_date: str
    price_semantics: str
    identity: str


@dataclass(frozen=True, slots=True)
class TargetToOrderResult:
    formation_date: str
    reference_price_date: str
    portfolio_equity: float
    current_cash: float
    target_risky_weight: float
    target_cash_weight: float
    intents: tuple[TheoreticalOrderIntent, ...]
    specification_fingerprint: str
    limitations: tuple[str, ...]
    identity: str


@dataclass(frozen=True, slots=True)
class VolumeParticipationDiagnostic:
    symbol: str
    state: ParticipationState
    order_shares: float | None
    daily_volume: float | None
    participation_pct: float | None
    volume_date: str
    interpretation: str
    identity: str


def translate_target_portfolio(
    *,
    formation_date: str,
    reference_price_date: str,
    portfolio_equity: float,
    current_cash: float,
    current_holdings: Mapping[str, int],
    target_weights: Mapping[str, float],
    reference_prices: Mapping[str, float | None],
    spec: ExecutionSpec = NEUTRAL_EXECUTION_FOUNDATION_V1,
) -> TargetToOrderResult:
    if reference_price_date > formation_date:
        raise ValueError("reference_price_date cannot be after formation_date; future execution prices are unavailable")
    if not math.isfinite(portfolio_equity) or portfolio_equity <= 0:
        raise ValueError("portfolio_equity must be finite and positive")
    if not math.isfinite(current_cash) or current_cash < 0:
        raise ValueError("current_cash must be finite and nonnegative")
    holdings: dict[str, int] = {}
    for raw, quantity in current_holdings.items():
        symbol = _symbol(raw)
        if isinstance(quantity, bool) or int(quantity) != quantity or quantity < 0:
            raise ValueError("current holding quantities must be nonnegative integers")
        if symbol in holdings: raise ValueError(f"duplicate normalized holding symbol: {symbol}")
        holdings[symbol] = int(quantity)
    targets: dict[str, float] = {}
    for raw, weight in target_weights.items():
        symbol = _symbol(raw); value = float(weight)
        if symbol in targets: raise ValueError(f"duplicate normalized target symbol: {symbol}")
        if not math.isfinite(value) or value < 0: raise ValueError("target weights must be finite and nonnegative")
        targets[symbol] = value
    risky_weight = sum(targets.values())
    if risky_weight > 1.0 + 1e-12: raise ValueError("long-only target weights must sum to at most one")
    prices: dict[str, float | None] = {}
    for raw, value in reference_prices.items():
        symbol = _symbol(raw)
        if symbol in prices: raise ValueError(f"duplicate normalized price symbol: {symbol}")
        prices[symbol] = None if value is None else float(value)
    intents: list[TheoreticalOrderIntent] = []
    for symbol in sorted(set(holdings) | set(targets)):
        current = holdings.get(symbol, 0); weight = targets.get(symbol, 0.0); notional = portfolio_equity * weight
        price = prices.get(symbol)
        if current == 0 and weight == 0:
            state, side, target_quantity, delta = OrderIntentState.NO_ORDER_REQUIRED, None, 0.0, 0.0
        elif price is None:
            state, side, target_quantity, delta = OrderIntentState.MISSING_REFERENCE_PRICE, None, None, None
        elif not math.isfinite(price) or price <= 0:
            state, side, target_quantity, delta = OrderIntentState.NONPOSITIVE_REFERENCE_PRICE, None, None, None
        else:
            target_quantity = notional / price; delta = target_quantity - current
            if abs(delta) <= 1e-12:
                state, side, delta = OrderIntentState.NO_ORDER_REQUIRED, None, 0.0
            else:
                state, side = OrderIntentState.AVAILABLE, OrderSide.BUY if delta > 0 else OrderSide.SELL
        payload = {
            "symbol": symbol, "side": None if side is None else side.value, "state": state.value,
            "current_quantity": current, "target_weight": weight, "target_notional": notional,
            "reference_price": price, "target_quantity": target_quantity, "delta": delta,
            "formation_date": formation_date, "reference_price_date": reference_price_date,
            "price_semantics": spec.price_semantics, "specification": spec.fingerprint,
        }
        intents.append(TheoreticalOrderIntent(symbol, side, state, current, weight, notional, price, target_quantity, delta, formation_date, reference_price_date, spec.price_semantics, _hash(payload)))
    limitations = (
        "theoretical fractional share deltas only; no lot, tick, price-band, or tradability rule is applied",
        "reference prices are known inputs, not modeled or realized fills",
        "no fee, tax, spread, slippage, market-impact, settlement, or corporate-action transformation is applied",
    )
    payload = {
        "contract": (CONTRACT, VERSION), "specification": spec.fingerprint,
        "formation_date": formation_date, "reference_price_date": reference_price_date,
        "portfolio_equity": portfolio_equity, "current_cash": current_cash,
        "target_risky_weight": risky_weight, "target_cash_weight": 1.0 - risky_weight,
        "intents": tuple(item.identity for item in intents), "limitations": limitations,
    }
    return TargetToOrderResult(formation_date, reference_price_date, portfolio_equity, current_cash, risky_weight, 1.0-risky_weight, tuple(intents), spec.fingerprint, limitations, _hash(payload))


def describe_volume_participation(
    intent: TheoreticalOrderIntent,
    *,
    daily_volume: float | None,
    volume_date: str,
) -> VolumeParticipationDiagnostic:
    if volume_date > intent.formation_date:
        raise ValueError("volume_date cannot be after formation_date")
    if intent.state is OrderIntentState.NO_ORDER_REQUIRED:
        state = ParticipationState.NO_ORDER_REQUIRED; shares = volume = participation = None
    elif intent.state is not OrderIntentState.AVAILABLE or intent.theoretical_share_delta is None:
        state = ParticipationState.ORDER_INTENT_UNAVAILABLE; shares = volume = participation = None
    elif daily_volume is None or not math.isfinite(float(daily_volume)) or float(daily_volume) <= 0:
        state = ParticipationState.VOLUME_UNAVAILABLE; shares = abs(intent.theoretical_share_delta); volume = participation = None
    else:
        state = ParticipationState.AVAILABLE_DESCRIPTIVE_ONLY; shares = abs(intent.theoretical_share_delta)
        volume = float(daily_volume); participation = shares / volume * 100.0
    interpretation = "DESCRIPTIVE_PARTICIPATION_NOT_FILL_PROBABILITY_OR_MARKET_IMPACT"
    payload = {"intent": intent.identity, "state": state.value, "shares": shares, "volume": volume, "participation": participation, "volume_date": volume_date, "interpretation": interpretation}
    return VolumeParticipationDiagnostic(intent.symbol, state, shares, volume, participation, volume_date, interpretation, _hash(payload))


@dataclass(frozen=True, slots=True)
class ExecutionFoundationResult:
    contract_name: str
    contract_version: str
    specification_fingerprint: str
    friction_fingerprint: str
    capabilities: tuple[ExecutionCapability, ...]
    limitations: tuple[str, ...]
    identity: str


def build_execution_foundation_result() -> ExecutionFoundationResult:
    capabilities = tuple(sorted(CAPABILITY_MATRIX, key=lambda item: (item.area, item.capability)))
    limitations = (
        "Phase 11A implements theoretical order intents, not executable orders or fills",
        "current paper/backtest lot and friction settings are not canonical historical market rules",
        "daily OHLCV supports descriptive participation only",
        "market impact, spread, queue priority, settlement history, price bands, tradability, and corporate actions remain unavailable",
        "upstream selection, portfolio construction, risk policy, Forward V1, paper, scanner, and production execution are isolated",
    )
    payload = {"contract": (CONTRACT, VERSION), "specification": NEUTRAL_EXECUTION_FOUNDATION_V1.fingerprint, "friction": NEUTRAL_EXECUTION_FRICTION_V1.fingerprint, "capabilities": tuple(item.identity for item in capabilities), "limitations": limitations}
    return ExecutionFoundationResult(CONTRACT, VERSION, NEUTRAL_EXECUTION_FOUNDATION_V1.fingerprint, NEUTRAL_EXECUTION_FRICTION_V1.fingerprint, capabilities, limitations, _hash(payload))
