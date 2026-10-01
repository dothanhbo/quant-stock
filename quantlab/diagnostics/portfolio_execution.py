from __future__ import annotations

"""Pure, read-only portfolio and execution diagnostics.

The functions in this module consume already materialized weights, returns and
trade records.  They never change portfolio decisions and never infer missing
market microstructure or corporate-action data.
"""

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import math
from statistics import mean
from types import MappingProxyType
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from quantlab.identity import canonical_identity_value, canonical_json
from quantlab.research_provenance import build_research_provenance


CONTRACT = "quantlab.portfolio_execution_diagnostics"
VERSION = "v1"


def _hash(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


class DiagnosticEvidenceState(str, Enum):
    DEFINED = "DEFINED"
    EMPTY = "EMPTY"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    UNAVAILABLE = "UNAVAILABLE"


class ExecutionCapabilityStatus(str, Enum):
    MODELED = "MODELED"
    PARTIAL = "PARTIAL"
    UNMODELED = "UNMODELED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ConcentrationDiagnostics:
    position_count: int
    gross_exposure: float
    net_exposure: float
    cash_share: float | None
    largest_position_weight: float | None
    top_n: int
    top_n_concentration: float | None
    herfindahl_concentration: float
    equal_weight_herfindahl: float | None
    sector_weights: Mapping[str, float]
    unknown_sector_weight: float
    unknown_sector_share_of_gross: float | None
    evidence_state: DiagnosticEvidenceState
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        sectors = MappingProxyType(dict(sorted(self.sector_weights.items())))
        object.__setattr__(self, "sector_weights", sectors)
        object.__setattr__(self, "identity", _hash({
            "contract": [CONTRACT, VERSION, "concentration"],
            "values": {name: getattr(self, name) for name in (
                "position_count", "gross_exposure", "net_exposure", "cash_share",
                "largest_position_weight", "top_n", "top_n_concentration",
                "herfindahl_concentration", "equal_weight_herfindahl",
                "sector_weights", "unknown_sector_weight", "unknown_sector_share_of_gross",
                "evidence_state",
            )},
        }))


@dataclass(frozen=True, slots=True)
class CorrelationDiagnostics:
    lookback_sessions: int
    minimum_observations: int
    total_pairs: int
    valid_pairs: int
    unavailable_pairs: int
    pair_coverage_pct: float
    average_pairwise_correlation: float | None
    maximum_pairwise_correlation: float | None
    weighted_pairwise_correlation: float | None
    pairwise_correlations: Mapping[str, float]
    evidence_state: DiagnosticEvidenceState
    undefined_reason: str | None
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        values = MappingProxyType(dict(sorted(self.pairwise_correlations.items())))
        object.__setattr__(self, "pairwise_correlations", values)
        object.__setattr__(self, "identity", _hash({
            "contract": [CONTRACT, VERSION, "correlation"],
            "lookback": self.lookback_sessions,
            "minimum_observations": self.minimum_observations,
            "pairs": values,
            "weighted_pairwise_correlation": self.weighted_pairwise_correlation,
            "valid": self.valid_pairs,
            "unavailable": self.unavailable_pairs,
            "state": self.evidence_state,
            "reason": self.undefined_reason,
        }))


@dataclass(frozen=True, slots=True)
class BetaDiagnostics:
    lookback_sessions: int
    minimum_observations: int
    benchmark_symbol: str
    per_symbol_beta: Mapping[str, float]
    per_symbol_observation_count: Mapping[str, int]
    weighted_portfolio_beta: float | None
    weighted_portfolio_correlation: float | None
    evaluated_symbol_count: int
    unavailable_symbol_count: int
    evidence_state: DiagnosticEvidenceState
    undefined_reason: str | None
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "per_symbol_beta", MappingProxyType(dict(sorted(self.per_symbol_beta.items()))))
        object.__setattr__(self, "per_symbol_observation_count", MappingProxyType(dict(sorted(self.per_symbol_observation_count.items()))))
        object.__setattr__(self, "identity", _hash({
            "contract": [CONTRACT, VERSION, "beta"],
            "lookback": self.lookback_sessions,
            "minimum_observations": self.minimum_observations,
            "benchmark": self.benchmark_symbol,
            "beta": self.per_symbol_beta,
            "observations": self.per_symbol_observation_count,
            "weighted": [self.weighted_portfolio_beta, self.weighted_portfolio_correlation],
            "state": self.evidence_state,
            "reason": self.undefined_reason,
        }))


@dataclass(frozen=True, slots=True)
class SectorCrowdingDiagnostics:
    represented_sector_count: int
    largest_sector: str | None
    largest_sector_weight: float | None
    effective_sector_count: float | None
    high_correlation_threshold: float
    high_correlation_pair_count: int
    evaluable_pair_count: int
    high_correlation_pair_share: float | None
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "identity", _hash({
            "contract": [CONTRACT, VERSION, "sector_crowding"],
            "values": {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "identity"},
        }))


@dataclass(frozen=True, slots=True)
class ExecutionRealismContract:
    capabilities: Mapping[str, ExecutionCapabilityStatus]
    limitations: tuple[str, ...]
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        capabilities = MappingProxyType(dict(sorted(self.capabilities.items())))
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "limitations", tuple(self.limitations))
        object.__setattr__(self, "identity", _hash({
            "contract": [CONTRACT, VERSION, "execution_realism"],
            "capabilities": capabilities,
            "limitations": self.limitations,
        }))


@dataclass(frozen=True, slots=True)
class CostSensitivityPoint:
    cost_bps: float
    trade_count: int
    turnover: float
    gross_pnl: float
    estimated_cost: float
    net_pnl: float
    gross_return_pct: float | None
    net_return_pct: float | None
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "identity", _hash({
            "contract": [CONTRACT, VERSION, "cost_point"],
            "values": {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "identity"},
        }))


@dataclass(frozen=True, slots=True)
class LiquidityDiagnostics:
    state: DiagnosticEvidenceState
    participation_by_symbol: Mapping[str, float]
    evaluable_symbol_count: int
    total_symbol_count: int
    coverage_pct: float
    units_known: bool
    warning: str
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "participation_by_symbol", MappingProxyType(dict(sorted(self.participation_by_symbol.items()))))
        object.__setattr__(self, "identity", _hash({
            "contract": [CONTRACT, VERSION, "liquidity"],
            "state": self.state,
            "participation": self.participation_by_symbol,
            "coverage": [self.evaluable_symbol_count, self.total_symbol_count, self.coverage_pct],
            "units_known": self.units_known,
            "warning": self.warning,
        }))


@dataclass(frozen=True, slots=True)
class PortfolioExecutionDiagnosticsResult:
    concentration: ConcentrationDiagnostics
    correlation: CorrelationDiagnostics
    beta: BetaDiagnostics
    sector_crowding: SectorCrowdingDiagnostics
    execution_realism: ExecutionRealismContract
    cost_sensitivity: tuple[CostSensitivityPoint, ...]
    break_even_cost_bps: float | None
    liquidity: LiquidityDiagnostics
    provenance: Mapping[str, Any]
    warnings: tuple[str, ...]
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "cost_sensitivity", tuple(self.cost_sensitivity))
        object.__setattr__(self, "provenance", _freeze(self.provenance))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "identity", _hash({
            "contract": [CONTRACT, VERSION],
            "concentration": self.concentration.identity,
            "correlation": self.correlation.identity,
            "beta": self.beta.identity,
            "sector": self.sector_crowding.identity,
            "execution": self.execution_realism.identity,
            "costs": tuple(item.identity for item in self.cost_sensitivity),
            "break_even_cost_bps": self.break_even_cost_bps,
            "liquidity": self.liquidity.identity,
            "provenance": self.provenance,
            "warnings": self.warnings,
        }))


def _normalize_weights(weights: Mapping[str, float]) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for raw_symbol, raw_weight in weights.items():
        symbol = str(raw_symbol).strip().upper()
        if not symbol or symbol == "VNINDEX":
            raise ValueError("portfolio weights require non-benchmark symbols")
        weight = _finite(raw_weight)
        if weight is None or weight <= 0:
            raise ValueError("portfolio weights must be finite and positive")
        if symbol in normalized:
            raise ValueError(f"duplicate normalized portfolio symbol: {symbol}")
        normalized[symbol] = weight
    return dict(sorted(normalized.items()))


def _trade_value(trade: Any, name: str, default: Any = None) -> Any:
    if isinstance(trade, Mapping):
        return trade.get(name, default)
    return getattr(trade, name, default)


def _trade_totals(trades: Iterable[Any]) -> tuple[int, float, float]:
    count = 0
    gross = turnover = 0.0
    for trade in trades:
        count += 1
        gross_value = _finite(_trade_value(trade, "gross_pnl"))
        if gross_value is None:
            entry = _finite(_trade_value(trade, "entry_price"))
            exit_price = _finite(_trade_value(trade, "exit_price"))
            quantity = _finite(_trade_value(trade, "quantity"))
            gross_value = 0.0 if None in (entry, exit_price, quantity) else (exit_price - entry) * quantity
        gross += gross_value
        entry = _finite(_trade_value(trade, "entry_price"))
        exit_price = _finite(_trade_value(trade, "exit_price"))
        quantity = _finite(_trade_value(trade, "quantity"))
        if None not in (entry, exit_price, quantity):
            turnover += (abs(entry * quantity) + abs(exit_price * quantity))
    return count, gross, turnover


def _returns_frame(daily_returns: pd.DataFrame, symbols: tuple[str, ...], lookback: int) -> pd.DataFrame:
    frame = daily_returns.copy()
    if frame.empty:
        return pd.DataFrame(index=pd.Index([], dtype="datetime64[ns]"), columns=symbols, dtype=float)
    frame.columns = [str(item).strip().upper() for item in frame.columns]
    if len(set(frame.columns)) != len(frame.columns):
        raise ValueError("daily return symbols must be unique after normalization")
    frame.index = pd.to_datetime(frame.index)
    frame = frame.sort_index().reindex(columns=symbols)
    return frame.iloc[-lookback:].apply(pd.to_numeric, errors="coerce")


def _build_correlation(frame: pd.DataFrame, minimum: int, lookback: int, weights: Mapping[str, float]) -> CorrelationDiagnostics:
    symbols = tuple(frame.columns)
    total_pairs = len(symbols) * (len(symbols) - 1) // 2
    values: dict[str, float] = {}
    unavailable = 0
    for left in range(len(symbols)):
        for right in range(left + 1, len(symbols)):
            pair = frame[[symbols[left], symbols[right]]].dropna()
            correlation = pair.iloc[:, 0].corr(pair.iloc[:, 1]) if len(pair) >= minimum else None
            key = f"{symbols[left]}|{symbols[right]}"
            if correlation is None or not math.isfinite(float(correlation)):
                unavailable += 1
            else:
                values[key] = max(-1.0, min(1.0, float(correlation)))
    valid = len(values)
    weighted_values = []
    weighted_total = 0.0
    for key, correlation in values.items():
        left, right = key.split("|", 1)
        pair_weight = weights[left] * weights[right]
        weighted_values.append(pair_weight * correlation)
        weighted_total += pair_weight
    state = DiagnosticEvidenceState.DEFINED if valid else DiagnosticEvidenceState.INSUFFICIENT_HISTORY if total_pairs else DiagnosticEvidenceState.EMPTY
    return CorrelationDiagnostics(
        lookback, minimum, total_pairs, valid, unavailable,
        (valid / total_pairs * 100.0) if total_pairs else 0.0,
        mean(values.values()) if values else None,
        max(values.values()) if values else None,
        (sum(weighted_values) / weighted_total) if weighted_total else None,
        values, state, None if values else "insufficient_pairwise_history",
    )


def _build_beta(
    frame: pd.DataFrame,
    benchmark: pd.Series | None,
    weights: Mapping[str, float],
    minimum: int,
    lookback: int,
    benchmark_symbol: str,
) -> BetaDiagnostics:
    symbols = tuple(frame.columns)
    if benchmark is None:
        return BetaDiagnostics(lookback, minimum, benchmark_symbol, {}, {symbol: 0 for symbol in symbols}, None, None, 0, len(symbols), DiagnosticEvidenceState.UNAVAILABLE, "benchmark_returns_unavailable")
    series = pd.to_numeric(benchmark, errors="coerce").copy()
    series.index = pd.to_datetime(series.index)
    series = series.sort_index().iloc[-lookback:]
    betas: dict[str, float] = {}
    counts: dict[str, int] = {}
    for symbol in symbols:
        aligned = pd.concat([frame[symbol], series.rename("benchmark")], axis=1).dropna()
        counts[symbol] = len(aligned)
        if len(aligned) < minimum:
            continue
        variance = float(aligned["benchmark"].var(ddof=1))
        if variance <= 0.0 or not math.isfinite(variance):
            continue
        beta = float(aligned[symbol].cov(aligned["benchmark"]) / variance)
        if math.isfinite(beta):
            betas[symbol] = beta
    weighted = None
    correlation = None
    selected = [symbol for symbol in symbols if symbol in betas and symbol in weights]
    if selected:
        total_weight = sum(weights[symbol] for symbol in selected)
        weighted = sum(weights[symbol] * betas[symbol] for symbol in selected) / total_weight
        complete = frame[selected].dropna()
        portfolio = complete.mul(pd.Series({symbol: weights[symbol] for symbol in selected}), axis=1).sum(axis=1)
        aligned = pd.concat([portfolio.rename("portfolio"), series.rename("benchmark")], axis=1).dropna()
        if len(aligned) >= minimum and aligned["portfolio"].std(ddof=1) > 0 and aligned["benchmark"].std(ddof=1) > 0:
            correlation = float(aligned["portfolio"].corr(aligned["benchmark"]))
    state = DiagnosticEvidenceState.DEFINED if betas else DiagnosticEvidenceState.INSUFFICIENT_HISTORY
    return BetaDiagnostics(lookback, minimum, benchmark_symbol, betas, counts, weighted, correlation, len(betas), len(symbols) - len(betas), state, None if betas else "insufficient_benchmark_history")


def _execution_contract() -> ExecutionRealismContract:
    return ExecutionRealismContract(
        capabilities={
            "fees": ExecutionCapabilityStatus.MODELED,
            "slippage": ExecutionCapabilityStatus.PARTIAL,
            "bid_ask_spread": ExecutionCapabilityStatus.UNMODELED,
            "market_impact": ExecutionCapabilityStatus.UNMODELED,
            "liquidity_capacity": ExecutionCapabilityStatus.UNKNOWN,
            "vn_price_limits": ExecutionCapabilityStatus.UNKNOWN,
            "auctions": ExecutionCapabilityStatus.UNMODELED,
            "lot_size": ExecutionCapabilityStatus.PARTIAL,
            "fill_timing": ExecutionCapabilityStatus.PARTIAL,
            "corporate_actions": ExecutionCapabilityStatus.UNMODELED,
            "suspension_no_trade": ExecutionCapabilityStatus.UNKNOWN,
        },
        limitations=(
            "fixed commissions and slippage are assumptions, not historical fill evidence",
            "daily OHLCV does not identify spread, impact, queue, auction, or capacity",
            "raw/adjusted price semantics and corporate actions are not fully persisted",
        ),
    )


def evaluate_portfolio_execution_diagnostics(
    *,
    weights: Mapping[str, float],
    daily_returns: pd.DataFrame,
    benchmark_returns: pd.Series | None = None,
    trades: Iterable[Any] = (),
    sector_by_symbol: Mapping[str, str] | None = None,
    daily_traded_value: Mapping[str, float] | None = None,
    price_units_known: bool = False,
    volume_units_known: bool = False,
    lookback_sessions: int = 60,
    minimum_observations: int = 20,
    correlation_threshold: float = 0.80,
    top_n: int = 5,
    cost_grid_bps: Iterable[float] = (0.0, 10.0, 25.0, 50.0, 100.0),
    initial_equity: float | None = None,
    benchmark_symbol: str = "VNINDEX",
    provenance: Mapping[str, Any] | None = None,
) -> PortfolioExecutionDiagnosticsResult:
    if lookback_sessions < 2 or minimum_observations < 2 or minimum_observations > lookback_sessions:
        raise ValueError("invalid lookback/minimum observation configuration")
    if not math.isfinite(correlation_threshold) or not -1.0 <= correlation_threshold <= 1.0:
        raise ValueError("correlation_threshold must be between -1 and 1")
    if top_n < 1:
        raise ValueError("top_n must be positive")
    normalized = _normalize_weights(weights)
    gross = float(sum(normalized.values()))
    net = gross
    sector_map = {str(key).strip().upper(): str(value).strip() for key, value in (sector_by_symbol or {}).items() if str(value).strip()}
    sectors: dict[str, float] = {}
    unknown = 0.0
    for symbol, weight in normalized.items():
        sector = sector_map.get(symbol)
        if sector is None:
            unknown += weight
        else:
            sectors[sector] = sectors.get(sector, 0.0) + weight
    hhi = float(sum(weight * weight for weight in normalized.values()))
    concentration = ConcentrationDiagnostics(
        len(normalized), gross, net, 1.0 - gross,
        max(normalized.values(), default=None), top_n,
        sum(sorted(normalized.values(), reverse=True)[:top_n]) if normalized else None,
        hhi, 1.0 / len(normalized) if normalized else None,
        sectors, unknown, unknown / gross if gross else None,
        DiagnosticEvidenceState.DEFINED if normalized else DiagnosticEvidenceState.EMPTY,
    )
    frame = _returns_frame(daily_returns, tuple(normalized), lookback_sessions)
    correlation = _build_correlation(frame, minimum_observations, lookback_sessions, normalized)
    beta = _build_beta(frame, benchmark_returns, normalized, minimum_observations, lookback_sessions, str(benchmark_symbol).strip().upper())
    high_pairs = sum(value >= correlation_threshold for value in correlation.pairwise_correlations.values())
    sector_crowding = SectorCrowdingDiagnostics(
        len(sectors), max(sectors, key=sectors.get) if sectors else None,
        max(sectors.values(), default=None),
        (1.0 / sum((value / gross) ** 2 for value in sectors.values())) if sectors and gross else None,
        correlation_threshold, high_pairs, correlation.valid_pairs,
        high_pairs / correlation.valid_pairs if correlation.valid_pairs else None,
    )
    trade_items = tuple(trades)
    trade_count, gross_pnl, turnover = _trade_totals(trade_items)
    grid = tuple(sorted({float(value) for value in cost_grid_bps}))
    if any(not math.isfinite(value) or value < 0 for value in grid):
        raise ValueError("cost_grid_bps must be finite and nonnegative")
    cost_points = tuple(CostSensitivityPoint(
        cost_bps=value, trade_count=trade_count, turnover=turnover, gross_pnl=gross_pnl,
        estimated_cost=turnover * value / 10_000.0, net_pnl=gross_pnl - turnover * value / 10_000.0,
        gross_return_pct=None if initial_equity in (None, 0) else gross_pnl / float(initial_equity) * 100.0,
        net_return_pct=None if initial_equity in (None, 0) else (gross_pnl - turnover * value / 10_000.0) / float(initial_equity) * 100.0,
    ) for value in grid)
    break_even_cost_bps = (gross_pnl / turnover * 10_000.0) if gross_pnl > 0.0 and turnover > 0.0 else None
    traded_values = {str(key).strip().upper(): _finite(value) for key, value in (daily_traded_value or {}).items()}
    participation: dict[str, float] = {}
    if price_units_known and volume_units_known and initial_equity is not None and initial_equity > 0:
        for symbol, weight in normalized.items():
            value = traded_values.get(symbol)
            if value is not None and value > 0:
                participation[symbol] = (weight * float(initial_equity)) / value
    liquidity = LiquidityDiagnostics(
        DiagnosticEvidenceState.DEFINED if participation else DiagnosticEvidenceState.UNAVAILABLE,
        participation, len(participation), len(normalized), len(participation) / len(normalized) * 100.0 if normalized else 0.0,
        price_units_known and volume_units_known and initial_equity is not None and initial_equity > 0,
        "rough participation only; verified units and initial equity are required" if not (price_units_known and volume_units_known and initial_equity is not None and initial_equity > 0) else "rough participation, not capacity or impact",
    )
    provenance_result = build_research_provenance("explicit_symbols").as_dict() if provenance is None else provenance
    warnings = list(provenance_result.get("warnings", ())) if isinstance(provenance_result, Mapping) else []
    warnings.extend(_execution_contract().limitations)
    return PortfolioExecutionDiagnosticsResult(
        concentration, correlation, beta, sector_crowding, _execution_contract(), cost_points, break_even_cost_bps,
        liquidity, provenance_result, tuple(dict.fromkeys(str(item) for item in warnings)),
    )


__all__ = [
    "CONTRACT", "VERSION", "DiagnosticEvidenceState", "ExecutionCapabilityStatus",
    "ConcentrationDiagnostics", "CorrelationDiagnostics", "BetaDiagnostics",
    "SectorCrowdingDiagnostics", "ExecutionRealismContract", "CostSensitivityPoint",
    "LiquidityDiagnostics", "PortfolioExecutionDiagnosticsResult",
    "evaluate_portfolio_execution_diagnostics",
]
