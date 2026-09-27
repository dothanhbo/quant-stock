from __future__ import annotations

"""Pure portfolio structure over frozen, outcome-free Phase 5.9 selections."""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from hashlib import sha256
import csv
import json
import math
from pathlib import Path
from statistics import median
from types import MappingProxyType
from typing import Any, Mapping

from quantlab.identity import canonical_identity_value, canonical_json


CONTRACT = "quantlab.neutral_portfolio_construction"
VERSION = "v1"
SELECTION_POLICY = "ADX_ONLY"
BUDGETS = (5, 10, 20)
BLOCKS = (
    ("early_2018_2020", "2018-08-07", "2020-12-31"),
    ("middle_2021_2022", "2021-01-01", "2022-12-31"),
    ("middle_2023_2024", "2023-01-01", "2024-12-31"),
    ("recent_2025_2026", "2025-01-01", "2026-09-17"),
)
WHOLE_SCOPE = ("whole_period", "2018-08-07", "2026-09-17")


class WeightingPolicy(str, Enum):
    EQUAL_WEIGHT = "EQUAL_WEIGHT"


class PortfolioStructuralState(str, Enum):
    STRUCTURALLY_VALID = "STRUCTURALLY_VALID"
    STRUCTURALLY_LIMITED = "STRUCTURALLY_LIMITED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


def _hash(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _text(value: Any, *, name: str) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError(f"{name} must be non-empty")
    return result


def _date(value: Any, *, name: str) -> str:
    result = _text(value, name=name)
    if date.fromisoformat(result).isoformat() != result:
        raise ValueError(f"{name} must use YYYY-MM-DD")
    return result


def _integer(value: Any, *, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    result = int(value)
    if result < 0:
        raise ValueError(f"{name} must be non-negative")
    return result


def _mean(values: tuple[float, ...]) -> float | None:
    return None if not values else sum(values) / len(values)


def _median(values: tuple[float, ...]) -> float | None:
    return None if not values else float(median(values))


@dataclass(frozen=True, slots=True)
class FrozenSelectionObservation:
    session_date: str
    policy_name: str
    policy_identity: str
    selection_budget: int
    eligible_cross_section_count: int
    ordered_selected_symbols: tuple[str, ...]
    selection_identity: str

    def __post_init__(self) -> None:
        session = _date(self.session_date, name="selection session date")
        if self.policy_name != SELECTION_POLICY:
            raise ValueError("Phase 6 accepts only the frozen ADX_ONLY selection policy")
        budget = _integer(self.selection_budget, name="selection budget")
        if budget not in BUDGETS:
            raise ValueError("selection budget must be 5, 10, or 20")
        eligible = _integer(self.eligible_cross_section_count, name="eligible cross-section count")
        symbols = tuple(_text(item, name="selected symbol").upper() for item in self.ordered_selected_symbols)
        if len(symbols) != len(set(symbols)) or len(symbols) > budget:
            raise ValueError("selected symbols must be unique and cannot exceed the budget")
        if len(symbols) > eligible:
            raise ValueError("selected symbols cannot exceed the eligible cross-section")
        object.__setattr__(self, "session_date", session)
        object.__setattr__(self, "selection_budget", budget)
        object.__setattr__(self, "eligible_cross_section_count", eligible)
        object.__setattr__(self, "ordered_selected_symbols", symbols)
        _text(self.policy_identity, name="selection policy identity")
        _text(self.selection_identity, name="selection identity")


@dataclass(frozen=True, slots=True)
class PortfolioConstructionInput:
    phase59_manifest_identity: str
    phase59_selection_result_identity: str
    phase511_manifest_identity: str
    phase511_decision_result_identity: str
    advanced_factor: str
    advanced_factor_identity: str
    selection_policy: str
    selection_policy_identity: str
    start_date: str
    end_date: str
    selections: tuple[FrozenSelectionObservation, ...]

    def __post_init__(self) -> None:
        if self.advanced_factor != "adx_14" or self.selection_policy != SELECTION_POLICY:
            raise ValueError("Phase 6 input must bind ADVANCE adx_14 to ADX_ONLY selections")
        for name in (
            "phase59_manifest_identity", "phase59_selection_result_identity",
            "phase511_manifest_identity", "phase511_decision_result_identity",
            "advanced_factor_identity", "selection_policy_identity",
        ):
            _text(getattr(self, name), name=name)
        start, end = _date(self.start_date, name="start date"), _date(self.end_date, name="end date")
        if start > end:
            raise ValueError("start date must not follow end date")
        selections = tuple(sorted(self.selections, key=lambda item: (item.session_date, item.selection_budget)))
        keys = tuple((item.session_date, item.selection_budget) for item in selections)
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate date/budget selection input")
        if any(item.policy_identity != self.selection_policy_identity for item in selections):
            raise ValueError("selection policy identity is inconsistent")
        dates = tuple(sorted({item.session_date for item in selections}))
        expected = tuple((item, budget) for item in dates for budget in BUDGETS)
        if tuple((item.session_date, item.selection_budget) for item in selections) != expected:
            raise ValueError("every frozen signal date must contain budgets 5, 10, and 20")
        if dates and (dates[0] < start or dates[-1] > end):
            raise ValueError("selection dates fall outside the input bounds")
        object.__setattr__(self, "start_date", start)
        object.__setattr__(self, "end_date", end)
        object.__setattr__(self, "selections", selections)


@dataclass(frozen=True, slots=True)
class PortfolioConstructionSpec:
    name: str = "EQUAL_WEIGHT_ADX_PORTFOLIO_CONSTRUCTION_V1"
    version: str = "1"
    budgets: tuple[int, ...] = BUDGETS
    weighting_policies: tuple[WeightingPolicy, ...] = (WeightingPolicy.EQUAL_WEIGHT,)
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if self.version != "1" or self.budgets != BUDGETS:
            raise ValueError("unsupported portfolio-construction specification")
        if self.weighting_policies != (WeightingPolicy.EQUAL_WEIGHT,):
            raise ValueError("only equal weighting is justified by the frozen input")
        payload = {
            "contract": {"name": CONTRACT, "version": VERSION},
            "name": self.name,
            "version": self.version,
            "candidate_source": SELECTION_POLICY,
            "budgets": self.budgets,
            "weighting_policies": tuple(item.value for item in self.weighting_policies),
            "weight_rule": "selected_count_positive=>one_over_selected_count_else_no_positions",
            "cash_rule": "one_minus_gross_weight_empty_portfolio_cash_one",
            "concentration": "herfindahl=sum_security_weight_squared;effective_n=one_over_herfindahl",
            "weight_turnover": "half_l1_change_over_union_of_security_weights_plus_cash",
            "first_date": "membership_changes_empty_and_weight_turnover_undefined",
            "structural_state": "valid_if_any_nonempty_portfolio;limited_if_all_empty;insufficient_if_no_dates",
            "sector_metrics": "unavailable_not_fabricated",
            "selection_turnover": "Phase_5_9_entries_divided_by_previous_selected_count_kept_distinct",
            "restrictions": (
                "no_future_outcomes", "no_pnl_or_backtest", "no_scenario_ranking",
                "no_transaction_costs", "no_optimized_weights_or_budget",
            ),
        }
        object.__setattr__(self, "fingerprint", _hash(payload))


EQUAL_WEIGHT_ADX_PORTFOLIO_CONSTRUCTION_V1 = PortfolioConstructionSpec()


@dataclass(frozen=True, slots=True)
class PortfolioPosition:
    symbol: str
    selection_rank: int
    weight: float
    selection_identity: str
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        symbol = _text(self.symbol, name="position symbol").upper()
        rank = _integer(self.selection_rank, name="selection rank")
        weight = float(self.weight)
        if rank <= 0 or not math.isfinite(weight) or weight <= 0.0 or weight > 1.0:
            raise ValueError("position rank and weight must be valid and positive")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "selection_rank", rank)
        object.__setattr__(self, "weight", weight)
        object.__setattr__(self, "identity", _hash({
            "symbol": symbol, "selection_rank": rank, "weight": weight,
            "selection_identity": self.selection_identity,
        }))


@dataclass(frozen=True, slots=True)
class DailyConstructedPortfolio:
    session_date: str
    candidate_source: str
    requested_budget: int
    weighting_policy: WeightingPolicy
    selection_identity: str
    positions: tuple[PortfolioPosition, ...]
    eligible_cross_section_count: int
    selected_count: int
    fill_ratio: float
    unfilled_slots: int
    gross_weight: float
    cash_weight: float
    max_single_name_weight: float
    herfindahl_concentration: float
    effective_number_of_positions: float | None
    additions: tuple[str, ...]
    removals: tuple[str, ...]
    retained_positions: tuple[str, ...]
    one_way_weight_turnover: float | None
    weight_stability: float | None
    identity: str


@dataclass(frozen=True, slots=True)
class PortfolioStructuralSummary:
    candidate_source: str
    requested_budget: int
    weighting_policy: WeightingPolicy
    scope_name: str
    scope_start_date: str
    scope_end_date: str
    evaluated_dates: int
    underfilled_date_count: int
    empty_date_count: int
    mean_selected_count: float | None
    median_selected_count: float | None
    mean_fill_ratio: float | None
    median_fill_ratio: float | None
    mean_effective_n: float | None
    median_effective_n: float | None
    mean_max_single_name_weight: float | None
    median_max_single_name_weight: float | None
    mean_herfindahl: float | None
    median_herfindahl: float | None
    defined_weight_turnover_dates: int
    mean_one_way_weight_turnover: float | None
    median_one_way_weight_turnover: float | None
    total_additions: int
    total_removals: int
    mean_weight_stability: float | None
    structural_state: PortfolioStructuralState
    daily_identity_count: int
    daily_identities_sha256: str
    identity: str


@dataclass(frozen=True, slots=True)
class PortfolioStructuralContrast:
    candidate_source: str
    weighting_policy: WeightingPolicy
    scope_name: str
    lower_budget: int
    higher_budget: int
    mean_selected_count_delta: float | None
    mean_fill_ratio_delta: float | None
    mean_effective_n_delta: float | None
    mean_max_single_name_weight_delta: float | None
    mean_herfindahl_delta: float | None
    mean_weight_turnover_delta: float | None
    identity: str


@dataclass(frozen=True, slots=True)
class PortfolioConstructionResult:
    contract_name: str
    contract_version: str
    specification_fingerprint: str
    source_identities: Mapping[str, str]
    daily_portfolios: tuple[DailyConstructedPortfolio, ...]
    summaries: tuple[PortfolioStructuralSummary, ...]
    contrasts: tuple[PortfolioStructuralContrast, ...]
    limitations: tuple[str, ...]
    identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_identities", MappingProxyType(dict(self.source_identities)))


def _daily(selection: FrozenSelectionObservation, previous: DailyConstructedPortfolio | None) -> DailyConstructedPortfolio:
    count = len(selection.ordered_selected_symbols)
    weight = 1.0 / count if count else 0.0
    positions = tuple(
        PortfolioPosition(symbol, rank, weight, selection.selection_identity)
        for rank, symbol in enumerate(selection.ordered_selected_symbols, start=1)
    )
    gross = sum(item.weight for item in positions)
    cash = 1.0 - gross
    hhi = sum(item.weight * item.weight for item in positions)
    effective_n = None if not positions else 1.0 / hhi
    if previous is None:
        additions = removals = retained = ()
        turnover = stability = None
    else:
        previous_weights = {item.symbol: item.weight for item in previous.positions}
        current_weights = {item.symbol: item.weight for item in positions}
        previous_set, current_set = set(previous_weights), set(current_weights)
        additions = tuple(item.symbol for item in positions if item.symbol not in previous_set)
        removals = tuple(item.symbol for item in previous.positions if item.symbol not in current_set)
        retained = tuple(item.symbol for item in positions if item.symbol in previous_set)
        security_change = sum(abs(current_weights.get(symbol, 0.0) - previous_weights.get(symbol, 0.0)) for symbol in previous_set | current_set)
        turnover = 0.5 * (security_change + abs(cash - previous.cash_weight))
        stability = 1.0 - turnover
    payload = {
        "session_date": selection.session_date, "candidate_source": selection.policy_name,
        "budget": selection.selection_budget, "weighting": WeightingPolicy.EQUAL_WEIGHT.value,
        "selection_identity": selection.selection_identity,
        "position_identities": tuple(item.identity for item in positions),
        "eligible": selection.eligible_cross_section_count, "selected": count,
        "fill_ratio": count / selection.selection_budget,
        "gross_weight": gross, "cash_weight": cash, "hhi": hhi,
        "effective_n": effective_n, "additions": additions, "removals": removals,
        "retained": retained, "weight_turnover": turnover, "weight_stability": stability,
    }
    return DailyConstructedPortfolio(
        selection.session_date, selection.policy_name, selection.selection_budget,
        WeightingPolicy.EQUAL_WEIGHT, selection.selection_identity, positions,
        selection.eligible_cross_section_count, count, count / selection.selection_budget,
        selection.selection_budget - count, gross, cash,
        max((item.weight for item in positions), default=0.0), hhi, effective_n,
        additions, removals, retained, turnover, stability, _hash(payload),
    )


def _summary(
    items: tuple[DailyConstructedPortfolio, ...],
    scope: tuple[str, str, str],
    budget: int,
) -> PortfolioStructuralSummary:
    name, start, end = scope
    selected = tuple(float(item.selected_count) for item in items)
    fill = tuple(item.fill_ratio for item in items)
    effective = tuple(item.effective_number_of_positions for item in items if item.effective_number_of_positions is not None)
    maximum = tuple(item.max_single_name_weight for item in items)
    hhi = tuple(item.herfindahl_concentration for item in items)
    turnover = tuple(item.one_way_weight_turnover for item in items if item.one_way_weight_turnover is not None)
    stability = tuple(item.weight_stability for item in items if item.weight_stability is not None)
    if not items:
        state = PortfolioStructuralState.INSUFFICIENT_EVIDENCE
    elif not any(item.selected_count for item in items):
        state = PortfolioStructuralState.STRUCTURALLY_LIMITED
    else:
        state = PortfolioStructuralState.STRUCTURALLY_VALID
    values = {
        "candidate_source": SELECTION_POLICY, "budget": budget,
        "weighting": WeightingPolicy.EQUAL_WEIGHT.value, "scope": scope,
        "daily_identities": tuple(item.identity for item in items), "state": state.value,
        "means": (_mean(selected), _mean(fill), _mean(effective), _mean(maximum), _mean(hhi), _mean(turnover)),
    }
    return PortfolioStructuralSummary(
        SELECTION_POLICY, budget, WeightingPolicy.EQUAL_WEIGHT,
        name, start, end, len(items), sum(item.selected_count < item.requested_budget for item in items),
        sum(item.selected_count == 0 for item in items), _mean(selected), _median(selected),
        _mean(fill), _median(fill), _mean(effective), _median(effective),
        _mean(maximum), _median(maximum), _mean(hhi), _median(hhi), len(turnover),
        _mean(turnover), _median(turnover), sum(len(item.additions) for item in items),
        sum(len(item.removals) for item in items), _mean(stability), state, len(items),
        _hash(tuple(item.identity for item in items)), _hash(values),
    )


def construct_portfolios(
    source: PortfolioConstructionInput,
    spec: PortfolioConstructionSpec = EQUAL_WEIGHT_ADX_PORTFOLIO_CONSTRUCTION_V1,
) -> PortfolioConstructionResult:
    if not isinstance(source, PortfolioConstructionInput):
        raise TypeError("source must be PortfolioConstructionInput")
    previous: dict[int, DailyConstructedPortfolio] = {}
    daily: list[DailyConstructedPortfolio] = []
    for selection in source.selections:
        item = _daily(selection, previous.get(selection.selection_budget))
        daily.append(item)
        previous[selection.selection_budget] = item
    daily_tuple = tuple(daily)
    summaries: list[PortfolioStructuralSummary] = []
    scopes = (WHOLE_SCOPE, *BLOCKS)
    for budget in spec.budgets:
        candidates = tuple(item for item in daily_tuple if item.requested_budget == budget)
        for scope in scopes:
            included = tuple(item for item in candidates if scope[1] <= item.session_date <= scope[2])
            summaries.append(_summary(included, scope, budget))
    summary_index = {(item.scope_name, item.requested_budget): item for item in summaries}
    contrasts: list[PortfolioStructuralContrast] = []
    for scope in scopes:
        for lower, higher in ((5, 10), (5, 20), (10, 20)):
            left, right = summary_index[(scope[0], lower)], summary_index[(scope[0], higher)]
            def delta(name: str) -> float | None:
                a, b = getattr(left, name), getattr(right, name)
                return None if a is None or b is None else b - a
            payload = {
                "scope": scope[0], "lower": lower, "higher": higher,
                "selected": delta("mean_selected_count"), "fill": delta("mean_fill_ratio"),
                "effective_n": delta("mean_effective_n"), "max_weight": delta("mean_max_single_name_weight"),
                "hhi": delta("mean_herfindahl"), "turnover": delta("mean_one_way_weight_turnover"),
            }
            contrasts.append(PortfolioStructuralContrast(
                SELECTION_POLICY, WeightingPolicy.EQUAL_WEIGHT, scope[0], lower, higher,
                payload["selected"], payload["fill"], payload["effective_n"],
                payload["max_weight"], payload["hhi"], payload["turnover"], _hash(payload),
            ))
    source_ids = MappingProxyType({
        "phase59_manifest": source.phase59_manifest_identity,
        "phase59_selection_result": source.phase59_selection_result_identity,
        "phase511_manifest": source.phase511_manifest_identity,
        "phase511_decision_result": source.phase511_decision_result_identity,
        "advanced_factor": source.advanced_factor_identity,
        "selection_policy": source.selection_policy_identity,
    })
    limitations = (
        "portfolio structure only; no future outcome, PnL, backtest, cost, or scenario selection",
        "equal weight is the only weighting policy because frozen input contains no same-date risk field",
        "sector metrics are unavailable because frozen selection artifacts contain no sector identity",
        "database coverage is not necessarily historical VN100 membership",
    )
    payload = {
        "contract": {"name": CONTRACT, "version": VERSION},
        "specification": spec.fingerprint, "source_identities": source_ids,
        "daily": tuple(item.identity for item in daily_tuple),
        "summaries": tuple(item.identity for item in summaries),
        "contrasts": tuple(item.identity for item in contrasts), "limitations": limitations,
    }
    return PortfolioConstructionResult(
        CONTRACT, VERSION, spec.fingerprint, source_ids, daily_tuple, tuple(summaries),
        tuple(contrasts), limitations, _hash(payload),
    )


def load_phase6_construction_input(
    phase59_root: str | Path,
    phase511_root: str | Path,
) -> PortfolioConstructionInput:
    """Load frozen outcome-free selection membership and Phase 5.11 authority."""
    p59, p511 = Path(phase59_root).resolve(), Path(phase511_root).resolve()
    manifest59 = json.loads((p59 / "experiment_manifest.json").read_text(encoding="utf-8"))
    manifest511 = json.loads((p511 / "research_decision_manifest.json").read_text(encoding="utf-8"))
    if manifest59.get("runner_version") != "v6" or manifest59.get("completed") is not True:
        raise ValueError("Phase 6 requires completed Phase 5.9B v6 artifacts")
    if manifest511.get("completed") is not True or manifest511.get("input_canonical_manifest_identity") != _hash(manifest59):
        raise ValueError("Phase 5.11 provenance does not match the supplied Phase 5.9B root")
    with (p511 / "research_decision_summary.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        decisions = tuple(csv.DictReader(handle))
    advanced = tuple(row for row in decisions if row.get("candidate_type") == "factor" and row.get("candidate") == "adx_14")
    if len(advanced) != 1 or advanced[0].get("decision") != "ADVANCE":
        raise ValueError("adx_14 is not authorized for portfolio research by Phase 5.11")
    selection_manifest = manifest59.get("policy_selection_diagnostics") or {}
    policies = {item["name"]: item for item in selection_manifest.get("policies", ())}
    if SELECTION_POLICY not in policies:
        raise ValueError("Phase 5.9B ADX_ONLY selection policy is missing")
    forbidden = {"outcome", "forward_return", "pnl", "profit", "future"}
    selections: list[FrozenSelectionObservation] = []
    with (p59 / "policy_selection_by_date.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if any(any(token in column.lower() for token in forbidden) for column in (reader.fieldnames or ())):
            raise ValueError("selection input contains forbidden future/outcome fields")
        for row in reader:
            if row["policy_name"] != SELECTION_POLICY:
                continue
            symbols = json.loads(row["selected_symbols_json"])
            if not isinstance(symbols, list) or int(row["actual_selected_count"]) != len(symbols):
                raise ValueError("selected-symbol artifact row does not reconcile")
            selections.append(FrozenSelectionObservation(
                row["session_date"], row["policy_name"], policies[SELECTION_POLICY]["fingerprint"],
                int(row["selection_budget"]), int(row["eligible_cross_section_count"]),
                tuple(symbols), row["identity"],
            ))
    bounds = manifest59["requested_bounds"]
    return PortfolioConstructionInput(
        phase59_manifest_identity=_hash(manifest59),
        phase59_selection_result_identity=selection_manifest["result_identity"],
        phase511_manifest_identity=_hash(manifest511),
        phase511_decision_result_identity=manifest511["decision_result_identity"],
        advanced_factor="adx_14",
        advanced_factor_identity=advanced[0]["candidate_identity"],
        selection_policy=SELECTION_POLICY,
        selection_policy_identity=policies[SELECTION_POLICY]["fingerprint"],
        start_date=bounds["start_date"], end_date=bounds["end_date"], selections=tuple(selections),
    )
