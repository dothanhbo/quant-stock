from __future__ import annotations

"""Causal, descriptive risk diagnostics for frozen historical portfolios."""

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import math
from statistics import median
from types import MappingProxyType
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from quantlab.catalog import MarketDataSnapshot
from quantlab.identity import canonical_identity_value, canonical_json
from quantlab.portfolio.construction import BLOCKS, WHOLE_SCOPE


CONTRACT = "quantlab.portfolio_risk_diagnostics"
VERSION = "v1"
SECTOR_EVIDENCE_UNAVAILABLE = "SECTOR_EVIDENCE_UNAVAILABLE"


def _hash(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _mean(values: Iterable[float]) -> float | None:
    items = tuple(values)
    return None if not items else float(sum(items) / len(items))


def _median(values: Iterable[float]) -> float | None:
    items = tuple(values)
    return None if not items else float(median(items))


class RiskEvidenceState(str, Enum):
    DEFINED = "DEFINED"
    EMPTY_PORTFOLIO = "EMPTY_PORTFOLIO"
    INSUFFICIENT_TRAILING_HISTORY = "INSUFFICIENT_TRAILING_HISTORY"
    MISSING_CONSTITUENT_PRICES = "MISSING_CONSTITUENT_PRICES"
    COVARIANCE_UNAVAILABLE = "COVARIANCE_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class PortfolioRiskSpec:
    name: str = "NEUTRAL_PORTFOLIO_RISK_V1"
    version: str = "1"
    trailing_market_sessions: int = 60
    minimum_return_observations: int = 40
    annualization_factor: int = 252
    benchmark_symbol: str = "VNINDEX"
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if self.version != "1":
            raise ValueError("unsupported portfolio-risk specification version")
        if self.trailing_market_sessions < 2 or self.minimum_return_observations < 2:
            raise ValueError("risk lookback and minimum observations must be at least two")
        if self.minimum_return_observations > self.trailing_market_sessions:
            raise ValueError("minimum observations cannot exceed the trailing window")
        if self.annualization_factor <= 0:
            raise ValueError("annualization factor must be positive")
        benchmark = self.benchmark_symbol.strip().upper()
        if not benchmark:
            raise ValueError("benchmark symbol must be non-empty")
        object.__setattr__(self, "benchmark_symbol", benchmark)
        object.__setattr__(self, "fingerprint", _hash({
            "contract": (CONTRACT, VERSION), "name": self.name, "version": self.version,
            "window": self.trailing_market_sessions,
            "minimum_observations": self.minimum_return_observations,
            "return": "exact_consecutive_VNINDEX_session_close_to_close_decimal_no_forward_fill",
            "covariance": "complete_case_sample_covariance_ddof_1",
            "pairwise": "pairwise_complete_pearson_minimum_overlap",
            "annualization": ("sqrt", self.annualization_factor),
            "benchmark": benchmark,
            "sector": SECTOR_EVIDENCE_UNAVAILABLE,
            "restrictions": ("descriptive_only", "no_outcomes", "no_optimization", "no_ranking", "no_reweighting"),
        }))


NEUTRAL_PORTFOLIO_RISK_V1 = PortfolioRiskSpec()


@dataclass(frozen=True, slots=True)
class FrozenRiskPosition:
    symbol: str
    weight: float
    position_identity: str

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        if not symbol or symbol == "VNINDEX":
            raise ValueError("risk position must be a non-benchmark symbol")
        if not math.isfinite(self.weight) or self.weight <= 0.0:
            raise ValueError("position weight must be finite and positive")
        object.__setattr__(self, "symbol", symbol)


@dataclass(frozen=True, slots=True)
class FrozenRiskPortfolio:
    session_date: str
    requested_budget: int
    weighting_policy: str
    positions: tuple[FrozenRiskPosition, ...]
    gross_weight: float
    cash_weight: float
    portfolio_identity: str

    def __post_init__(self) -> None:
        pd.Timestamp(self.session_date)
        positions = tuple(sorted(self.positions, key=lambda item: item.symbol))
        if len({item.symbol for item in positions}) != len(positions):
            raise ValueError("portfolio positions must be unique")
        if abs(sum(item.weight for item in positions) - self.gross_weight) > 1e-10:
            raise ValueError("position weights do not reconcile to gross weight")
        if abs(self.gross_weight + self.cash_weight - 1.0) > 1e-10:
            raise ValueError("gross weight plus cash weight must equal one")
        object.__setattr__(self, "positions", positions)


@dataclass(frozen=True, slots=True)
class PortfolioRiskComponentContribution:
    session_date: str
    requested_budget: int
    symbol: str
    weight: float
    marginal_variance_contribution: float
    component_variance_contribution: float
    component_variance_contribution_share: float
    identity: str


@dataclass(frozen=True, slots=True)
class PortfolioRiskObservation:
    session_date: str
    requested_budget: int
    weighting_policy: str
    selected_count: int
    gross_weight: float
    cash_weight: float
    max_single_name_weight: float
    herfindahl_concentration: float
    effective_n: float | None
    trailing_session_count: int
    covariance_observation_count: int
    pairwise_valid_count: int
    pairwise_unavailable_count: int
    mean_pairwise_correlation: float | None
    median_pairwise_correlation: float | None
    minimum_pairwise_correlation: float | None
    maximum_pairwise_correlation: float | None
    daily_portfolio_variance: float | None
    daily_portfolio_volatility: float | None
    annualized_portfolio_volatility: float | None
    maximum_component_risk_share: float | None
    risk_contribution_herfindahl: float | None
    effective_risk_contributors: float | None
    benchmark_observation_count: int
    benchmark_correlation: float | None
    benchmark_beta: float | None
    covariance_evidence_state: RiskEvidenceState
    covariance_undefined_reason: str | None
    benchmark_undefined_reason: str | None
    sector_evidence_state: str
    source_portfolio_identity: str
    component_identity_count: int
    component_identities_sha256: str
    identity: str


@dataclass(frozen=True, slots=True)
class PortfolioRiskSummary:
    requested_budget: int
    weighting_policy: str
    scope_name: str
    scope_start_date: str
    scope_end_date: str
    observation_count: int
    covariance_defined_count: int
    benchmark_defined_count: int
    mean_annualized_portfolio_volatility: float | None
    median_annualized_portfolio_volatility: float | None
    mean_pairwise_correlation: float | None
    median_pairwise_correlation: float | None
    mean_benchmark_beta: float | None
    median_benchmark_beta: float | None
    mean_max_single_name_weight: float | None
    mean_effective_n: float | None
    mean_maximum_component_risk_share: float | None
    mean_effective_risk_contributors: float | None
    observation_identities_sha256: str
    identity: str


@dataclass(frozen=True, slots=True)
class PortfolioRiskResult:
    contract_name: str
    contract_version: str
    specification_fingerprint: str
    source_identities: Mapping[str, str]
    observations: tuple[PortfolioRiskObservation, ...]
    component_contributions: tuple[PortfolioRiskComponentContribution, ...]
    summaries: tuple[PortfolioRiskSummary, ...]
    limitations: tuple[str, ...]
    identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_identities", MappingProxyType(dict(self.source_identities)))


def _returns(frame: pd.DataFrame, sessions: pd.DatetimeIndex) -> pd.Series:
    if frame.empty:
        return pd.Series(index=sessions, dtype="float64")
    closes = frame.assign(time=pd.to_datetime(frame["time"])).set_index("time")["close"].astype(float)
    exact = closes.reindex(sessions)
    return exact.pct_change(fill_method=None)


def _summary(items: tuple[PortfolioRiskObservation, ...], scope: tuple[str, str, str], budget: int) -> PortfolioRiskSummary:
    name, start, end = scope
    included = tuple(item for item in items if item.requested_budget == budget and start <= item.session_date <= end)
    def values(field_name: str) -> tuple[float, ...]:
        return tuple(float(value) for item in included if (value := getattr(item, field_name)) is not None)
    ids = tuple(item.identity for item in included)
    payload = {
        "budget": budget, "weighting": "EQUAL_WEIGHT", "scope": scope, "identities": ids,
        "metrics": tuple((field_name, _mean(values(field_name)), _median(values(field_name))) for field_name in (
            "annualized_portfolio_volatility", "mean_pairwise_correlation", "benchmark_beta",
            "max_single_name_weight", "effective_n", "maximum_component_risk_share", "effective_risk_contributors",
        )),
    }
    return PortfolioRiskSummary(
        budget, "EQUAL_WEIGHT", name, start, end, len(included),
        sum(item.daily_portfolio_variance is not None for item in included),
        sum(item.benchmark_beta is not None for item in included),
        _mean(values("annualized_portfolio_volatility")), _median(values("annualized_portfolio_volatility")),
        _mean(values("mean_pairwise_correlation")), _median(values("mean_pairwise_correlation")),
        _mean(values("benchmark_beta")), _median(values("benchmark_beta")),
        _mean(values("max_single_name_weight")), _mean(values("effective_n")),
        _mean(values("maximum_component_risk_share")), _mean(values("effective_risk_contributors")),
        _hash(ids), _hash(payload),
    )


def evaluate_portfolio_risk(
    portfolios: Iterable[FrozenRiskPortfolio],
    snapshot: MarketDataSnapshot,
    *,
    source_identities: Mapping[str, str],
    spec: PortfolioRiskSpec = NEUTRAL_PORTFOLIO_RISK_V1,
) -> PortfolioRiskResult:
    """Evaluate frozen weights using information no later than each formation date."""
    frozen = tuple(sorted(portfolios, key=lambda item: (item.session_date, item.requested_budget)))
    keys = tuple((item.session_date, item.requested_budget) for item in frozen)
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate portfolio date/budget")
    if not source_identities or any(not str(value).strip() for value in source_identities.values()):
        raise ValueError("source identities must be non-empty")
    symbols = tuple(sorted({position.symbol for item in frozen for position in item.positions} | {spec.benchmark_symbol}))
    through = max((item.session_date for item in frozen), default=snapshot.last_session_date)
    bundle = snapshot.load_ohlcv(symbols, through_date=through) if symbols else None
    benchmark_frame = pd.DataFrame() if bundle is None else bundle.frame_for(spec.benchmark_symbol)
    benchmark_sessions = pd.DatetimeIndex(pd.to_datetime(benchmark_frame.get("time", pd.Series(dtype="datetime64[ns]")))).sort_values().unique()
    return_series = {symbol: _returns(bundle.frame_for(symbol), benchmark_sessions) for symbol in symbols} if bundle is not None else {}
    observations: list[PortfolioRiskObservation] = []
    components: list[PortfolioRiskComponentContribution] = []
    for portfolio in frozen:
        selected = len(portfolio.positions)
        weights = np.asarray([item.weight for item in portfolio.positions], dtype=float)
        hhi = float(np.dot(weights, weights)) if selected else 0.0
        effective_n = None if not selected else 1.0 / hhi
        formation = pd.Timestamp(portfolio.session_date)
        eligible_sessions = benchmark_sessions[benchmark_sessions <= formation]
        window_sessions = eligible_sessions[-spec.trailing_market_sessions:]
        pair_values: list[float] = []
        valid_pairs = unavailable_pairs = 0
        variance = daily_vol = annual_vol = None
        maximum_share = risk_hhi = effective_risk = None
        covariance_count = 0
        component_rows: list[PortfolioRiskComponentContribution] = []
        reason: str | None = None
        state = RiskEvidenceState.DEFINED
        if not selected:
            state, reason = RiskEvidenceState.EMPTY_PORTFOLIO, "empty_portfolio"
        elif len(window_sessions) < spec.minimum_return_observations:
            state, reason = RiskEvidenceState.INSUFFICIENT_TRAILING_HISTORY, "insufficient_VNINDEX_sessions"
        else:
            matrix = pd.DataFrame({item.symbol: return_series[item.symbol].reindex(window_sessions) for item in portfolio.positions})
            pair_counts = matrix.notna().astype("int16").T @ matrix.notna().astype("int16")
            correlations = matrix.corr(min_periods=spec.minimum_return_observations)
            for left in range(selected):
                for right in range(left + 1, selected):
                    overlap = int(pair_counts.iloc[left, right])
                    correlation = correlations.iloc[left, right]
                    if overlap < spec.minimum_return_observations or not math.isfinite(float(correlation)):
                        unavailable_pairs += 1
                    else:
                        pair_values.append(max(-1.0, min(1.0, float(correlation)))); valid_pairs += 1
            complete = matrix.dropna()
            covariance_count = len(complete)
            if covariance_count < spec.minimum_return_observations:
                state, reason = RiskEvidenceState.MISSING_CONSTITUENT_PRICES, "insufficient_complete_constituent_returns"
            else:
                covariance = complete.cov(ddof=1).to_numpy(dtype=float)
                raw_variance = float(weights @ covariance @ weights)
                if not math.isfinite(raw_variance) or raw_variance < -1e-14:
                    state, reason = RiskEvidenceState.COVARIANCE_UNAVAILABLE, "invalid_covariance"
                else:
                    variance = max(0.0, raw_variance)
                    daily_vol = math.sqrt(variance); annual_vol = daily_vol * math.sqrt(spec.annualization_factor)
                    if variance > 0.0:
                        marginal = covariance @ weights
                        contributions = weights * marginal
                        shares = contributions / variance
                        if abs(float(contributions.sum()) - variance) > 1e-10 or abs(float(shares.sum()) - 1.0) > 1e-10:
                            raise ValueError("component variance contributions do not reconcile")
                        maximum_share = float(np.max(shares))
                        risk_hhi = float(np.dot(shares, shares))
                        effective_risk = None if risk_hhi <= 0.0 else 1.0 / risk_hhi
                        for position, marginal_value, contribution, share in zip(portfolio.positions, marginal, contributions, shares, strict=True):
                            payload = (portfolio.session_date, portfolio.requested_budget, position.symbol, position.weight, float(marginal_value), float(contribution), float(share))
                            component_rows.append(PortfolioRiskComponentContribution(*payload, _hash(payload)))
        benchmark_count = 0; benchmark_correlation = benchmark_beta = None; benchmark_reason: str | None = None
        if variance is None:
            benchmark_reason = "portfolio_covariance_unavailable"
        else:
            matrix = pd.DataFrame({item.symbol: return_series[item.symbol].reindex(window_sessions) for item in portfolio.positions})
            complete = matrix.assign(__benchmark=return_series[spec.benchmark_symbol].reindex(window_sessions)).dropna()
            benchmark_count = len(complete)
            if benchmark_count < spec.minimum_return_observations:
                benchmark_reason = "insufficient_benchmark_overlap"
            else:
                portfolio_returns = complete.iloc[:, :-1].to_numpy() @ weights
                benchmark_returns = complete["__benchmark"].to_numpy()
                benchmark_variance = float(np.var(benchmark_returns, ddof=1))
                if benchmark_variance <= 0.0:
                    benchmark_reason = "benchmark_variance_zero"
                else:
                    benchmark_beta = float(np.cov(portfolio_returns, benchmark_returns, ddof=1)[0, 1] / benchmark_variance)
                    if np.std(portfolio_returns, ddof=1) > 0.0:
                        benchmark_correlation = float(np.corrcoef(portfolio_returns, benchmark_returns)[0, 1])
                    else:
                        benchmark_reason = "portfolio_variance_zero"
        component_ids = tuple(item.identity for item in component_rows)
        payload = {
            "portfolio": portfolio.portfolio_identity, "spec": spec.fingerprint,
            "structure": (selected, portfolio.gross_weight, portfolio.cash_weight, hhi, effective_n),
            "window": tuple(item.date().isoformat() for item in window_sessions),
            "pairwise": (valid_pairs, unavailable_pairs, tuple(pair_values)),
            "covariance": (covariance_count, variance, daily_vol, annual_vol, state.value, reason),
            "risk": (maximum_share, risk_hhi, effective_risk, component_ids),
            "benchmark": (benchmark_count, benchmark_correlation, benchmark_beta, benchmark_reason),
            "sector": SECTOR_EVIDENCE_UNAVAILABLE,
        }
        observations.append(PortfolioRiskObservation(
            portfolio.session_date, portfolio.requested_budget, portfolio.weighting_policy, selected,
            portfolio.gross_weight, portfolio.cash_weight,
            max((item.weight for item in portfolio.positions), default=0.0), hhi, effective_n,
            len(window_sessions), covariance_count, valid_pairs, unavailable_pairs,
            _mean(pair_values), _median(pair_values), min(pair_values) if pair_values else None,
            max(pair_values) if pair_values else None, variance, daily_vol, annual_vol,
            maximum_share, risk_hhi, effective_risk, benchmark_count, benchmark_correlation, benchmark_beta,
            state, reason, benchmark_reason, SECTOR_EVIDENCE_UNAVAILABLE,
            portfolio.portfolio_identity, len(component_ids), _hash(component_ids), _hash(payload),
        ))
        components.extend(component_rows)
    observation_tuple = tuple(observations)
    summaries = tuple(_summary(observation_tuple, scope, budget) for budget in (5, 10, 20) for scope in (WHOLE_SCOPE, *BLOCKS))
    limitations = (
        "descriptive diagnostics only; no risk limit, optimization, ranking, or portfolio mutation",
        "trailing estimates use the same historical market database and are not future outcome evidence",
        "sector evidence unavailable: repository has no canonical point-in-time sector classification",
        "database coverage is not necessarily historical VN100 membership",
    )
    sources = MappingProxyType({**dict(source_identities), "market_snapshot": snapshot.snapshot_id})
    result_payload = {
        "contract": (CONTRACT, VERSION), "spec": spec.fingerprint, "sources": dict(sources),
        "observations": tuple(item.identity for item in observation_tuple),
        "components": tuple(item.identity for item in components),
        "summaries": tuple(item.identity for item in summaries), "limitations": limitations,
    }
    return PortfolioRiskResult(CONTRACT, VERSION, spec.fingerprint, sources, observation_tuple, tuple(components), summaries, limitations, _hash(result_payload))
