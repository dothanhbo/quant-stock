from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
import json
import math
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np
import pandas as pd
import pytest

from quantlab.evaluation import (
    NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1,
    PanelFactorDirection,
    PanelFactorEvaluationSpec,
    evaluate_point_in_time_panel_factors,
)
from quantlab.panels import POINT_IN_TIME_NEUTRAL_RESEARCH_DATASET_V1


_SPEC = POINT_IN_TIME_NEUTRAL_RESEARCH_DATASET_V1
_FACTORS = NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1.factors


@dataclass(frozen=True)
class _Dataset:
    _frame: pd.DataFrame
    identity: str = "dataset-identity-v1"
    content_identity: str = "dataset-content-v1"
    spec: Any = _SPEC
    universe_mode: str = "database_coverage"

    @property
    def observation_row_count(self) -> int:
        return len(self._frame)

    @property
    def metadata(self) -> Mapping[str, Any]:
        return MappingProxyType({
            "future_looking": True,
            "production_signal_safe": False,
            "permitted_use": "offline_research_evaluation_only",
            "observation_row_count": len(self._frame),
            "universe_mode": self.universe_mode,
        })

    def evaluation_frame(self) -> pd.DataFrame:
        return self._frame.copy(deep=True)


def _spec(
    factors: tuple[str, ...] = ("atr_percent_14",),
    *,
    horizons: tuple[int, ...] = (5,),
    outcomes: tuple[str, ...] = ("stock_forward_return_pct",),
    minimum: int = 5,
    low: float = .30,
    high: float = .70,
) -> PanelFactorEvaluationSpec:
    return PanelFactorEvaluationSpec(
        name="test_panel_factor_evaluation",
        version="1",
        factors=factors,
        factor_directions=(PanelFactorDirection.UNSPECIFIED,) * len(factors),
        horizons=horizons,
        outcome_fields=outcomes,
        minimum_cross_section_size=minimum,
        low_bucket_max_percentile=low,
        high_bucket_min_percentile=high,
    )


def _frame(
    dates: tuple[str, ...],
    symbols: tuple[str, ...],
    *,
    factor_values: Mapping[tuple[str, str], float] | None = None,
    stock_values: Mapping[tuple[str, str], float] | None = None,
    excess_values: Mapping[tuple[str, str], float] | None = None,
    factor_statuses: Mapping[tuple[str, str], str] | None = None,
    outcome_statuses: Mapping[tuple[str, str], str] | None = None,
    reverse: bool = False,
) -> pd.DataFrame:
    factor_values = factor_values or {}
    stock_values = stock_values or {}
    excess_values = excess_values or stock_values
    factor_statuses = factor_statuses or {}
    outcome_statuses = outcome_statuses or {}
    rows: list[dict[str, Any]] = []
    for signal_date in dates:
        for ordinal, symbol in enumerate(symbols):
            key = (signal_date, symbol)
            row = {column: None for column in _SPEC.output_columns}
            row.update({
                "session_date": signal_date,
                "symbol": symbol,
                "benchmark_symbol": "VNINDEX",
                "membership_set_hash": f"members-{signal_date}",
                "member_ordinal": ordinal,
                "market_row_available": True,
                "requested_feature_count": len(_SPEC.feature_columns),
                "available_feature_count": len(_SPEC.feature_columns),
                "complete_feature_row": True,
                "requested_horizon_count": 3,
                "available_horizon_count": 3,
                "fully_labeled_outcome_row": True,
            })
            for feature in _SPEC.feature_columns:
                row[feature] = factor_values.get(key, float(ordinal + 1))
                row[f"{feature}__availability"] = factor_statuses.get(key, "AVAILABLE")
            for horizon in (5, 10, 20):
                row[f"target_session_{horizon}"] = "2099-01-01"
                row[f"stock_forward_return_{horizon}_pct"] = stock_values.get(
                    key, float(ordinal + 1),
                )
                row[f"benchmark_forward_return_{horizon}_pct"] = 0.0
                row[f"excess_forward_return_{horizon}_pct_points"] = excess_values.get(
                    key, float(ordinal + 1),
                )
                row[f"outcome_{horizon}__availability"] = outcome_statuses.get(key, "AVAILABLE")
            rows.append(row)
    if reverse:
        rows.reverse()
    return pd.DataFrame(rows, columns=_SPEC.output_columns)


def _daily(dataset: _Dataset, spec: PanelFactorEvaluationSpec | None = None):
    result = evaluate_point_in_time_panel_factors(dataset, spec or _spec())
    return result, result.daily_evaluations[0]


def test_whole_population_eligibility_and_exclusion_categories_reconcile() -> None:
    day = "2022-01-03"
    symbols = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")
    factor_status = {
        (day, "BBB"): "SOURCE_VALUE_MISSING",
        (day, "DDD"): "SOURCE_DATE_MISSING",
    }
    outcome_status = {
        (day, "CCC"): "CENSORED_AFTER_DATA_END",
        (day, "DDD"): "MISSING_STOCK_TARGET_CLOSE",
    }
    factors = {(day, symbol): float(index + 1) for index, symbol in enumerate(symbols)}
    outcomes = dict(factors)
    factors[(day, "EEE")] = math.inf
    outcomes[(day, "FFF")] = math.nan
    _, daily = _daily(_Dataset(_frame(
        (day,), symbols, factor_values=factors, stock_values=outcomes,
        factor_statuses=factor_status, outcome_statuses=outcome_status,
    )))
    assert daily.total_observation_count == 6
    assert daily.factor_available_count == 4
    assert daily.outcome_available_count == 4
    assert daily.factor_usable_count == 3
    assert daily.outcome_usable_count == 3
    assert daily.pairwise_finite_eligible_count == 1
    assert (
        daily.excluded_for_factor_count,
        daily.excluded_for_outcome_count,
        daily.excluded_for_both_count,
    ) == (2, 2, 1)
    assert daily.factor_nonfinite_available_count == 1
    assert daily.outcome_nonfinite_available_count == 1
    assert sum(daily.factor_status_counts.values()) == 6
    assert sum(daily.outcome_status_counts.values()) == 6


def test_hand_calculated_rank_ic_ties_and_bucket_spreads() -> None:
    day = "2022-01-03"
    symbols = ("A", "B", "C", "D", "E", "F")
    factors = dict(zip(((day, symbol) for symbol in symbols), (1, 1, 2, 3, 4, 5), strict=True))
    outcomes = dict(zip(((day, symbol) for symbol in symbols), (0, 1, 2, 3, 4, 5), strict=True))
    _, daily = _daily(_Dataset(_frame(
        (day,), symbols, factor_values=factors, stock_values=outcomes,
    )))
    expected = np.corrcoef((1.5, 1.5, 3, 4, 5, 6), (1, 2, 3, 4, 5, 6))[0, 1]
    assert daily.rank_ic == pytest.approx(expected)
    assert daily.ic_undefined_reason is None
    assert (daily.low_bucket_count, daily.high_bucket_count) == (2, 2)
    assert daily.high_minus_low_mean_spread == pytest.approx(4.0)
    assert daily.high_minus_low_median_spread == pytest.approx(4.0)
    evidence = {item[0]: item for item in daily.eligible_evidence}
    assert evidence["A"][3:6] == pytest.approx((1.5, 1.0, 0.1))
    assert evidence["B"][3:6] == pytest.approx((1.5, 2.0, 0.1))
    assert evidence["A"][-1] == evidence["B"][-1] == "LOW"


@pytest.mark.parametrize(
    ("count", "factor_values", "outcome_values", "reason"),
    [
        (4, (1, 2, 3, 4), (1, 2, 3, 4), "fewer_than_minimum_pairwise_finite_observations"),
        (5, (1, 1, 1, 1, 1), (2, 2, 2, 2, 2), "constant_factor_and_outcome"),
        (5, (1, 1, 1, 1, 1), (1, 2, 3, 4, 5), "constant_factor"),
        (5, (1, 2, 3, 4, 5), (2, 2, 2, 2, 2), "constant_outcome"),
    ],
)
def test_minimum_and_constant_undefined_reasons(
    count: int,
    factor_values: tuple[float, ...],
    outcome_values: tuple[float, ...],
    reason: str,
) -> None:
    day = "2022-01-03"
    symbols = tuple(chr(ord("A") + index) for index in range(count))
    factors = dict(zip(((day, symbol) for symbol in symbols), factor_values, strict=True))
    outcomes = dict(zip(((day, symbol) for symbol in symbols), outcome_values, strict=True))
    _, daily = _daily(_Dataset(_frame(
        (day,), symbols, factor_values=factors, stock_values=outcomes,
    )))
    assert daily.rank_ic is None
    assert daily.ic_undefined_reason == reason
    if count < 5:
        assert daily.bucket_undefined_reason == reason


def test_aggregate_uses_equal_date_weight_not_pooled_observations() -> None:
    dates = ("2022-01-03", "2022-01-04")
    symbols = ("A", "B", "C", "D", "E", "F")
    factor_values: dict[tuple[str, str], float] = {}
    outcome_values: dict[tuple[str, str], float] = {}
    for index, symbol in enumerate(symbols):
        factor_values[(dates[0], symbol)] = index + 1
        outcome_values[(dates[0], symbol)] = index + 1
        factor_values[(dates[1], symbol)] = 100 + index
        outcome_values[(dates[1], symbol)] = 100 - index
    result = evaluate_point_in_time_panel_factors(
        _Dataset(_frame(dates, symbols, factor_values=factor_values, stock_values=outcome_values)),
        _spec(),
    )
    summary = result.summaries[0]
    assert tuple(item.rank_ic for item in result.daily_evaluations) == pytest.approx((1.0, -1.0))
    assert summary.mean_daily_rank_ic == pytest.approx(0.0)
    pooled = pd.Series(tuple(factor_values.values())).rank(method="average").corr(
        pd.Series(tuple(outcome_values.values())).rank(method="average")
    )
    assert pooled != pytest.approx(summary.mean_daily_rank_ic)


def test_ordering_normalization_identity_stability_and_sensitivity() -> None:
    dates = ("2022-01-03", "2022-01-04")
    symbols = ("A", "B", "C", "D", "E", "F")
    frame = _frame(dates, symbols)
    spec = _spec(
        ("atr_percent_14", "rsi_14"),
        horizons=(5, 10),
        outcomes=("stock_forward_return_pct", "excess_forward_return_pct_points"),
    )
    first = evaluate_point_in_time_panel_factors(_Dataset(frame), spec)
    normalized = evaluate_point_in_time_panel_factors(_Dataset(_frame(dates, symbols, reverse=True)), spec)
    assert first.identity == normalized.identity
    assert [
        (item.factor, item.horizon_sessions, item.outcome_field, item.signal_date)
        for item in first.daily_evaluations
    ] == sorted(
        (
            (factor, horizon, outcome, signal_date)
            for factor in spec.factors
            for horizon in spec.horizons
            for outcome in spec.outcome_fields
            for signal_date in dates
        ),
        key=lambda item: (
            spec.factors.index(item[0]), spec.horizons.index(item[1]),
            spec.outcome_fields.index(item[2]), item[3],
        ),
    )
    changed_factor = frame.copy(deep=True)
    changed_factor.loc[0, "atr_percent_14"] = 999.0
    changed_outcome = frame.copy(deep=True)
    changed_outcome.loc[0, "stock_forward_return_5_pct"] = -999.0
    changed_status = frame.copy(deep=True)
    changed_status.loc[0, "atr_percent_14__availability"] = "SOURCE_VALUE_MISSING"
    identities = {
        evaluate_point_in_time_panel_factors(_Dataset(changed_factor), spec).identity,
        evaluate_point_in_time_panel_factors(_Dataset(changed_outcome), spec).identity,
        evaluate_point_in_time_panel_factors(_Dataset(changed_status), spec).identity,
        evaluate_point_in_time_panel_factors(_Dataset(frame, identity="other-population"), spec).identity,
        evaluate_point_in_time_panel_factors(_Dataset(frame), _spec()).identity,
    }
    assert first.identity not in identities
    assert len(identities) == 5


@pytest.mark.parametrize(
    "factor",
    (
        "session_date",
        "target_session_5",
        "stock_forward_return_5_pct",
        "atr_percent_14__availability",
        "complete_feature_row",
        "not_a_feature",
    ),
)
def test_factor_allowlist_rejects_identifiers_leakage_diagnostics_and_unknowns(factor: str) -> None:
    with pytest.raises(ValueError, match="forbidden or unknown"):
        _spec((factor,))
    with pytest.raises(ValueError, match="unique"):
        _spec(("rsi_14", "rsi_14"))


def test_spec_validation_rejects_bad_horizons_outcomes_minimum_and_buckets() -> None:
    with pytest.raises(ValueError, match="horizon"):
        _spec(horizons=(7,))
    with pytest.raises(ValueError, match="outcome"):
        _spec(outcomes=("benchmark_forward_return_pct",))
    with pytest.raises(ValueError, match="positive"):
        _spec(minimum=0)
    with pytest.raises(ValueError, match="overlap"):
        _spec(low=.70, high=.30)


def test_pure_offline_boundary_immutability_and_caller_mutation_protection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket
    import sqlite3

    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: pytest.fail("database access"))
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("network access"))
    frame = _frame(("2022-01-03",), ("A", "B", "C", "D", "E"))
    result = evaluate_point_in_time_panel_factors(_Dataset(frame), _spec())
    original_identity = result.identity
    frame.loc[:, "atr_percent_14"] = -1_000.0
    assert result.identity == original_identity
    with pytest.raises(FrozenInstanceError):
        result.identity = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        result.selection_bias_metadata["descriptive_only"] = False  # type: ignore[index]
    assert result.selection_bias_metadata["candidate_or_strategy_acceptance_filter_applied"] is False
    assert result.selection_bias_metadata["outcomes_future_looking"] is True


def test_source_validation_and_empty_population_contract() -> None:
    frame = _frame(("2022-01-03",), ("A", "B", "C", "D", "E"))
    duplicate = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_point_in_time_panel_factors(_Dataset(duplicate), _spec())
    malformed = _Dataset(frame, identity="")
    with pytest.raises(ValueError, match="identity"):
        evaluate_point_in_time_panel_factors(malformed, _spec())
    empty = _Dataset(pd.DataFrame(columns=_SPEC.output_columns))
    result = evaluate_point_in_time_panel_factors(empty, _spec())
    assert result.daily_evaluations == ()
    assert len(result.summaries) == 1
    assert result.summaries[0].total_signal_dates == 0
    assert result.summaries[0].mean_daily_rank_ic is None


def test_fresh_public_import_avoids_strategy_candidate_portfolio_and_data_runtimes() -> None:
    script = """
import json
import sys
from quantlab.evaluation import (
    NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1,
    evaluate_point_in_time_panel_factors,
)
forbidden = {
    'backtesting.engine',
    'backtesting.portfolio_simulator',
    'core.database',
    'quantlab.candidates.frozen_q70',
    'quantlab.features.builtins',
    'strategy.paper_v2_gate',
    'strategy.paper_v2_scanner',
}
print(json.dumps(sorted(forbidden.intersection(sys.modules))))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == []
