from __future__ import annotations

"""Immutable evidence classification for historical price provenance."""

from dataclasses import dataclass, field
from hashlib import sha256
import math
from types import MappingProxyType
from typing import Any, Mapping

from quantlab.identity import canonical_identity_value, canonical_json


CONTRACT = "quantlab.execution_price_provenance"
VERSION = "v1"


def _hash(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


class PriceUnitState:
    VND_CANONICAL = "VND_CANONICAL"
    THOUSAND_VND_CANONICAL = "THOUSAND_VND_CANONICAL"
    PROVIDER_NATIVE_WITH_KNOWN_SCALE = "PROVIDER_NATIVE_WITH_KNOWN_SCALE"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    AMBIGUOUS = "AMBIGUOUS"
    UNAVAILABLE = "UNAVAILABLE"


class PriceAdjustmentState:
    RAW_TRADABLE_PRICE_VERIFIED = "RAW_TRADABLE_PRICE_VERIFIED"
    ADJUSTED_PRICE_VERIFIED = "ADJUSTED_PRICE_VERIFIED"
    MIXED_OR_PROVIDER_DEPENDENT = "MIXED_OR_PROVIDER_DEPENDENT"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    UNKNOWN = "UNKNOWN"


class CorporateActionEvidenceState:
    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


class PriceCapabilityState:
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True, slots=True)
class ExecutionPriceCapability:
    operation: str
    state: str
    reason: str


@dataclass(frozen=True, slots=True)
class PriceProvenanceResult:
    contract_name: str
    contract_version: str
    storage_unit_state: str
    storage_unit_evidence: tuple[str, ...]
    display_unit: str
    operational_unit: str
    execution_notional_unit: str
    vnd_conversion_scale: float | None
    vnd_conversion_verified: bool
    adjustment_state: str
    adjustment_evidence: tuple[str, ...]
    corporate_action_capabilities: Mapping[str, str]
    execution_capabilities: tuple[ExecutionPriceCapability, ...]
    monetary_capacity_gate: str
    phase11b_reconciliation: tuple[str, ...]
    unsupported_claims: tuple[str, ...]
    source_identities: Mapping[str, str]
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        corp = dict(sorted(self.corporate_action_capabilities.items()))
        sources = dict(sorted(self.source_identities.items()))
        object.__setattr__(self, "corporate_action_capabilities", MappingProxyType(corp))
        object.__setattr__(self, "source_identities", MappingProxyType(sources))
        object.__setattr__(self, "identity", _hash({
            "contract": (self.contract_name, self.contract_version),
            "storage_unit_state": self.storage_unit_state,
            "storage_unit_evidence": self.storage_unit_evidence,
            "display_unit": self.display_unit,
            "operational_unit": self.operational_unit,
            "execution_notional_unit": self.execution_notional_unit,
            "vnd_conversion_scale": self.vnd_conversion_scale,
            "vnd_conversion_verified": self.vnd_conversion_verified,
            "adjustment_state": self.adjustment_state,
            "adjustment_evidence": self.adjustment_evidence,
            "corporate_action_capabilities": corp,
            "execution_capabilities": tuple({
                "operation": item.operation, "state": item.state, "reason": item.reason,
            } for item in self.execution_capabilities),
            "monetary_capacity_gate": self.monetary_capacity_gate,
            "phase11b_reconciliation": self.phase11b_reconciliation,
            "unsupported_claims": self.unsupported_claims,
            "source_identities": sources,
        }))


def assess_price_provenance(
    *,
    source_identities: Mapping[str, str],
    storage_unit_state: str = PriceUnitState.PARTIALLY_VERIFIED,
    vnd_conversion_scale: float | None = 1000.0,
    vnd_conversion_verified: bool = False,
    adjustment_state: str = PriceAdjustmentState.UNKNOWN,
    corporate_action_capabilities: Mapping[str, str] | None = None,
) -> PriceProvenanceResult:
    """Classify claims supported by persisted/local provenance, failing closed.

    A conversion convention recorded by consumers is not, by itself, proof of
    the provider's canonical unit. Likewise, a plausible price range does not
    prove raw/adjusted semantics.
    """
    if not source_identities or any(not key or not value for key, value in source_identities.items()):
        raise ValueError("price provenance source identities are required")
    known_units = {
        PriceUnitState.VND_CANONICAL, PriceUnitState.THOUSAND_VND_CANONICAL,
        PriceUnitState.PROVIDER_NATIVE_WITH_KNOWN_SCALE, PriceUnitState.PARTIALLY_VERIFIED,
        PriceUnitState.AMBIGUOUS, PriceUnitState.UNAVAILABLE,
    }
    if storage_unit_state not in known_units:
        raise ValueError("unsupported storage unit state")
    known_adjustments = {
        PriceAdjustmentState.RAW_TRADABLE_PRICE_VERIFIED,
        PriceAdjustmentState.ADJUSTED_PRICE_VERIFIED,
        PriceAdjustmentState.MIXED_OR_PROVIDER_DEPENDENT,
        PriceAdjustmentState.PARTIALLY_VERIFIED, PriceAdjustmentState.UNKNOWN,
    }
    if adjustment_state not in known_adjustments:
        raise ValueError("unsupported adjustment state")
    if vnd_conversion_scale is not None and (not math.isfinite(float(vnd_conversion_scale)) or vnd_conversion_scale <= 0):
        raise ValueError("VND conversion scale must be positive")
    corp = dict(corporate_action_capabilities or {
        name: CorporateActionEvidenceState.UNAVAILABLE for name in (
            "stock_splits", "reverse_splits", "stock_dividends", "cash_dividends",
            "rights_issues", "bonus_shares", "ticker_changes", "quantity_adjustments",
        )
    })
    if any(value not in {CorporateActionEvidenceState.SUPPORTED, CorporateActionEvidenceState.PARTIAL, CorporateActionEvidenceState.UNAVAILABLE} for value in corp.values()):
        raise ValueError("invalid corporate-action evidence state")
    adjustment_known = adjustment_state in {
        PriceAdjustmentState.RAW_TRADABLE_PRICE_VERIFIED,
        PriceAdjustmentState.ADJUSTED_PRICE_VERIFIED,
    }
    conversion_ready = storage_unit_state in {
        PriceUnitState.VND_CANONICAL, PriceUnitState.THOUSAND_VND_CANONICAL,
        PriceUnitState.PROVIDER_NATIVE_WITH_KNOWN_SCALE,
    } and vnd_conversion_scale is not None and vnd_conversion_verified and adjustment_known
    corp_safe = bool(corp) and all(value == CorporateActionEvidenceState.SUPPORTED for value in corp.values())
    capabilities = (
        ExecutionPriceCapability("same_date_relative_price_calculation", PriceCapabilityState.SUPPORTED, "Dimensionless same-series ratios cancel a common positive unit scale."),
        ExecutionPriceCapability("formation_close_to_next_open_percentage_gap", PriceCapabilityState.SUPPORTED, "Phase 11B uses exact same-series close/open values and the ratio is scale-invariant; it remains a reference gap, not slippage."),
        ExecutionPriceCapability("theoretical_share_quantity_from_notional_divided_by_price", PriceCapabilityState.PARTIALLY_SUPPORTED if vnd_conversion_scale is not None else PriceCapabilityState.UNSUPPORTED, "Arithmetic is defined, but canonical storage-unit provenance is incomplete; absolute shares may differ materially under another scale."),
        ExecutionPriceCapability("VND_order_notional", PriceCapabilityState.SUPPORTED if conversion_ready else PriceCapabilityState.PARTIALLY_SUPPORTED if vnd_conversion_scale is not None else PriceCapabilityState.UNSUPPORTED, "VND conversion is operationally configured but is not independently verified from persisted provider provenance." if not conversion_ready else "Verified unit and conversion evidence is available."),
        ExecutionPriceCapability("daily_monetary_capacity_proxy", PriceCapabilityState.UNSUPPORTED, "Canonical traded value and verified VND price units are absent; price multiplied by volume would be an unverified proxy."),
        ExecutionPriceCapability("sequential_historical_share_holdings", PriceCapabilityState.SUPPORTED if corp_safe else PriceCapabilityState.UNSUPPORTED, "Corporate-action and quantity adjustments are not recorded sufficiently to reconcile historical shares." if not corp_safe else "All required corporate-action records are available."),
        ExecutionPriceCapability("executable_historical_pnl", PriceCapabilityState.UNSUPPORTED, "Price adjustment/corporate-action provenance and historical execution/fill evidence are incomplete."),
    )
    gate = "MONETARY_CAPACITY_READY" if conversion_ready else "MONETARY_CAPACITY_NOT_DEFENSIBLE"
    evidence = (
        "prices stores REAL OHLC and INTEGER volume with no unit/source/adjustment columns",
        "Quote.history is called with source=KBS and rows are numerically validated and persisted without a scale conversion",
        "paper executor, display formatter, and lifecycle consumers apply a 1000 multiplier; ordinary backtest scale is configurable and defaults to 1.0",
        "existing parity audit checked a latest-date sample, multiplier agreement, and plausible scaled median; it did not compare provider reference records",
        "market.db values are consistent with a thousand-VND equity quote convention but observed plausibility is not independent unit proof",
    )
    adjustment_evidence = (
        "the prices schema has no raw/adjusted marker or adjustment factor",
        "ingestion code does not persist provider adjustment mode or corporate-action provenance",
        "the provider call contains no locally recorded adjustment semantics",
    )
    reconciliation = (
        "Phase 11B reference_to_next_open_gap remains dimensionless and scale-invariant when both observations share the same stored series convention",
        "Phase 11B normalized participation remains a scale-linear descriptive coefficient, not a monetary-capacity estimate",
    )
    unsupported = (
        "Do not claim provider-native raw tradable prices.",
        "Do not claim provider-adjusted historical prices.",
        "Do not claim VND notionals are independently verified from canonical provider provenance.",
        "Do not claim corporate-action-safe share quantities, sequential holdings, or executable historical PnL.",
        "Do not label close times volume as canonical traded value.",
    )
    return PriceProvenanceResult(
        CONTRACT, VERSION, storage_unit_state, evidence,
        "DISPLAY_VND_AFTER_CONFIGURED_MULTIPLIER_1000_FOR_EQUITIES",
        "OPERATIONAL_VND_CONVENTION_SCALE_1000_IN_PAPER_PATHS_BACKTEST_CONFIGURABLE_DEFAULT_1",
        "UNVERIFIED_VND_NOTIONAL; STORED_PRICE_UNIT_UNRESOLVED",
        vnd_conversion_scale, vnd_conversion_verified, adjustment_state,
        adjustment_evidence, corp, capabilities, gate, reconciliation, unsupported,
        source_identities,
    )
