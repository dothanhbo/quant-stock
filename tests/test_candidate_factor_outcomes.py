from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, fields, replace
import os
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType

import pytest

from quantlab.evaluation import (
    CandidateFactorOutcomeEvaluationResult,
    DailyFactorOutcomeEvaluation,
    DESCRIPTIVE_WARNING,
    FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1,
    FactorHorizonEvaluationSummary,
    FactorOutcomeEvaluationSpec,
    evaluate_candidate_factor_outcomes,
)
from quantlab.outcomes import ForwardOutcomeStatus


@dataclass(frozen=True)
class _Candidate:
    candidate_key: str
    symbol: str
    signal_date: str
    quality_score: object
    score: object = 1.0
    relative_strength_20d: object = 1.0
    adx: object = 1.0
    atr_percent: object = 1.0
    rsi14: object = 1.0
    volume_ratio: object = 1.0
    breadth_ema50_pct: object = 1.0
    breadth_ema50_change_10d: object = 1.0


@dataclass(frozen=True)
class _Batch:
    candidates: tuple[_Candidate, ...]
    batch_identity: str = "candidate-batch"


@dataclass(frozen=True)
class _Outcome:
    candidate_key: str
    symbol: str
    signal_date: str
    horizon_sessions: int
    status: ForwardOutcomeStatus
    stock_forward_return_pct: object
    excess_forward_return_percentage_points: object
    outcome_identity: str


@dataclass(frozen=True)
class _OutcomeSet:
    outcomes: tuple[_Outcome, ...]
    source_candidate_batch_identity: str = "candidate-batch"
    set_identity: str = "outcome-set"
    requested_horizons: tuple[int, ...] = (5,)


def _spec(
    *,
    factors: tuple[str, ...] = ("quality_score",),
    horizons: tuple[int, ...] = (5,),
    outcomes: tuple[str, ...] = ("stock_forward_return_pct", "excess_forward_return_pct_points"),
    minimum: int = 5,
    low: float = .30,
    high: float = .70,
    name: str = "test-factor-outcomes",
) -> FactorOutcomeEvaluationSpec:
    return FactorOutcomeEvaluationSpec(
        name=name,
        version="1",
        factors=factors,
        horizons=horizons,
        outcome_fields=outcomes,
        minimum_cross_section_size=minimum,
        low_bucket_max_percentile=low,
        high_bucket_min_percentile=high,
    )


def _candidates(
    factor_values: tuple[object, ...],
    *,
    signal_date: str = "2024-01-02",
    factor: str = "quality_score",
) -> tuple[_Candidate, ...]:
    result = []
    for index, value in enumerate(factor_values):
        base = _Candidate(f"C{index}:{signal_date}", f"C{index}", signal_date, 1.0)
        result.append(replace(base, **{factor: value}))
    return tuple(result)


def _outcome_set(
    candidates: tuple[_Candidate, ...],
    stock: tuple[object, ...],
    *,
    excess: tuple[object, ...] | None = None,
    horizon: int = 5,
    statuses: tuple[ForwardOutcomeStatus, ...] | None = None,
    set_identity: str = "outcome-set",
) -> _OutcomeSet:
    excess_values = stock if excess is None else excess
    status_values = statuses or (ForwardOutcomeStatus.AVAILABLE,) * len(candidates)
    outcomes = tuple(
        _Outcome(
            candidate.candidate_key,
            candidate.symbol,
            candidate.signal_date,
            horizon,
            status,
            stock_value,
            excess_value,
            f"outcome-{index}-{status.value}-{stock_value}-{excess_value}",
        )
        for index, (candidate, stock_value, excess_value, status) in enumerate(
            zip(candidates, stock, excess_values, status_values, strict=True)
        )
    )
    return _OutcomeSet(outcomes, set_identity=set_identity, requested_horizons=(horizon,))


def _daily(result: CandidateFactorOutcomeEvaluationResult, outcome_field: str = "stock_forward_return_pct") -> DailyFactorOutcomeEvaluation:
    return next(item for item in result.daily_evaluations if item.outcome_field == outcome_field)


def test_hand_calculable_rank_ic_buckets_and_stock_excess_separation() -> None:
    candidates = _candidates((1, 2, 3, 4, 5))
    outcomes = _outcome_set(candidates, (5, 4, 3, 2, 1), excess=(1, 2, 3, 4, 5))
    result = evaluate_candidate_factor_outcomes(_Batch(candidates), outcomes, _spec())
    stock = _daily(result)
    excess = _daily(result, "excess_forward_return_pct_points")

    assert stock.rank_ic == pytest.approx(-1.0)
    assert (stock.low_bucket_count, stock.high_bucket_count) == (2, 2)
    assert (stock.low_bucket_mean_outcome, stock.low_bucket_median_outcome) == (4.5, 4.5)
    assert (stock.high_bucket_mean_outcome, stock.high_bucket_median_outcome) == (1.5, 1.5)
    assert (stock.high_minus_low_mean_spread, stock.high_minus_low_median_spread) == (-3.0, -3.0)
    assert excess.rank_ic == pytest.approx(1.0)
    assert excess.high_minus_low_mean_spread == pytest.approx(3.0)


def test_average_ranks_preserve_factor_and_outcome_ties_without_splitting_buckets() -> None:
    candidates = _candidates((1, 1, 2, 3, 4))
    outcomes = _outcome_set(candidates, (1, 2, 2, 4, 5))
    daily = _daily(evaluate_candidate_factor_outcomes(_Batch(candidates), outcomes, _spec(outcomes=("stock_forward_return_pct",))))
    assert daily.rank_ic == pytest.approx(0.9210526315789473)
    # Factor ranks 1.5, 1.5, 3, 4, 5 map to percentiles .125, .125, .5, .75, 1.
    assert (daily.low_bucket_count, daily.high_bucket_count) == (2, 2)
    assert daily.low_bucket_mean_outcome == 1.5


def test_minimum_boundary_constant_reasons_and_breadth_negative_control() -> None:
    four = _candidates((1, 2, 3, 4))
    four_daily = _daily(evaluate_candidate_factor_outcomes(_Batch(four), _outcome_set(four, (1, 2, 3, 4)), _spec(outcomes=("stock_forward_return_pct",))))
    assert four_daily.rank_ic is None
    assert four_daily.ic_undefined_reason == "fewer_than_minimum_pairwise_finite_observations"

    five = _candidates((1, 2, 3, 4, 5))
    assert _daily(evaluate_candidate_factor_outcomes(_Batch(five), _outcome_set(five, (1, 2, 3, 4, 5)), _spec(outcomes=("stock_forward_return_pct",)))).rank_ic == pytest.approx(1.0)

    constant_factor = _candidates((7, 7, 7, 7, 7), factor="breadth_ema50_pct")
    breadth_daily = _daily(evaluate_candidate_factor_outcomes(
        _Batch(constant_factor), _outcome_set(constant_factor, (1, 2, 3, 4, 5)),
        _spec(factors=("breadth_ema50_pct",), outcomes=("stock_forward_return_pct",)),
    ))
    assert breadth_daily.factor_constant is True
    assert breadth_daily.ic_undefined_reason == "constant_factor"
    assert breadth_daily.bucket_undefined_reason == "empty_low_bucket"

    constant_outcome = _daily(evaluate_candidate_factor_outcomes(
        _Batch(five), _outcome_set(five, (2, 2, 2, 2, 2)),
        _spec(outcomes=("stock_forward_return_pct",)),
    ))
    assert constant_outcome.ic_undefined_reason == "constant_outcome"
    assert constant_outcome.bucket_undefined_reason is None
    assert constant_outcome.high_minus_low_mean_spread == 0.0


def test_unavailable_labels_are_counted_never_zero_filled() -> None:
    candidates = _candidates((1, 2, 3, 4, 5))
    statuses = (
        ForwardOutcomeStatus.CENSORED_AFTER_DATA_END,
        ForwardOutcomeStatus.MISSING_SIGNAL_CLOSE,
        ForwardOutcomeStatus.MISSING_TARGET_CLOSE,
        ForwardOutcomeStatus.MISSING_BENCHMARK_SIGNAL_CLOSE,
        ForwardOutcomeStatus.MISSING_BENCHMARK_TARGET_CLOSE,
    )
    outcomes = _outcome_set(candidates, (None,) * 5, statuses=statuses)
    result = evaluate_candidate_factor_outcomes(_Batch(candidates), outcomes, _spec(outcomes=("stock_forward_return_pct",)))
    daily = _daily(result)
    assert daily.available_labeled_candidates == daily.pairwise_finite_count == 0
    assert daily.ic_undefined_reason == daily.bucket_undefined_reason == "unavailable_outcome_status"
    assert all(result.status_counts_by_horizon[5][status] == 1 for status in statuses)


def test_join_rejects_identity_duplicate_missing_extra_and_provenance_errors() -> None:
    candidates = _candidates((1, 2, 3, 4, 5))
    batch = _Batch(candidates)
    valid = _outcome_set(candidates, (1, 2, 3, 4, 5))
    spec = _spec(outcomes=("stock_forward_return_pct",))
    with pytest.raises(ValueError, match="identity does not match"):
        evaluate_candidate_factor_outcomes(batch, replace(valid, source_candidate_batch_identity="other"), spec)
    with pytest.raises(ValueError, match="duplicate candidate/horizon"):
        evaluate_candidate_factor_outcomes(batch, replace(valid, outcomes=valid.outcomes + (valid.outcomes[0],)), spec)
    with pytest.raises(ValueError, match="missing candidate/horizon"):
        evaluate_candidate_factor_outcomes(batch, replace(valid, outcomes=valid.outcomes[:-1]), spec)
    extra = replace(valid.outcomes[0], candidate_key="EXTRA", symbol="EXTRA", outcome_identity="extra")
    with pytest.raises(ValueError, match="extra outcome candidate"):
        evaluate_candidate_factor_outcomes(batch, replace(valid, outcomes=valid.outcomes + (extra,)), spec)
    wrong_date = replace(valid.outcomes[0], signal_date="2024-01-03", outcome_identity="wrong-date")
    with pytest.raises(ValueError, match="provenance mismatch"):
        evaluate_candidate_factor_outcomes(batch, replace(valid, outcomes=(wrong_date,) + valid.outcomes[1:]), spec)


def test_bucket_empty_overlap_and_signed_spread_reasons() -> None:
    tied = _candidates((1, 1, 1, 1, 1))
    outcomes = _outcome_set(tied, (1, 2, 3, 4, 5))
    empty_low = _daily(evaluate_candidate_factor_outcomes(
        _Batch(tied), outcomes, _spec(outcomes=("stock_forward_return_pct",), low=.3, high=.7),
    ))
    assert empty_low.bucket_undefined_reason == "empty_low_bucket"
    empty_high = _daily(evaluate_candidate_factor_outcomes(
        _Batch(tied), outcomes, _spec(outcomes=("stock_forward_return_pct",), low=.7, high=.8, name="empty-high"),
    ))
    assert empty_high.bucket_undefined_reason == "empty_high_bucket"
    overlap = _daily(evaluate_candidate_factor_outcomes(
        _Batch(tied), outcomes, _spec(outcomes=("stock_forward_return_pct",), low=.7, high=.3, name="overlap"),
    ))
    assert overlap.bucket_undefined_reason == "overlapping_buckets"


def test_daily_aggregation_equal_weights_dates_and_never_pools_rank_ic() -> None:
    day1 = _candidates((1, 2, 3, 4, 5), signal_date="2024-01-02")
    day2 = _candidates((10, 20, 30, 40, 50), signal_date="2024-01-03")
    candidates = day1 + day2
    outcomes = _outcome_set(candidates, (1, 2, 3, 4, 5, 500, 400, 300, 200, 100))
    result = evaluate_candidate_factor_outcomes(_Batch(candidates), outcomes, _spec(outcomes=("stock_forward_return_pct",)))
    summary = result.summary_for("quality_score", 5, "stock_forward_return_pct")
    assert tuple(item.rank_ic for item in result.daily_evaluations) == pytest.approx((1.0, -1.0))
    assert summary.mean_daily_rank_ic == pytest.approx(0.0)
    assert summary.median_daily_rank_ic == pytest.approx(0.0)
    assert summary.population_std_daily_ic == pytest.approx(1.0)
    assert (summary.positive_ic_date_count, summary.zero_ic_date_count, summary.negative_ic_date_count) == (1, 0, 1)
    assert summary.positive_ic_rate == .5
    assert summary.ic_coverage_pct == summary.bucket_coverage_pct == 100.0
    assert summary.warning == DESCRIPTIVE_WARNING


def test_positive_zero_negative_counts_and_coverage_use_defined_dates_only() -> None:
    dates_values = (
        ("2024-01-02", (1, 2, 3, 4, 5)),
        ("2024-01-03", (5, 4, 3, 2, 1)),
        ("2024-01-04", (1, 2, 3, 2, 1)),
    )
    candidate_groups = tuple(_candidates((1, 2, 3, 4, 5), signal_date=day) for day, _ in dates_values)
    candidates = tuple(item for group in candidate_groups for item in group)
    outcomes = _outcome_set(candidates, tuple(value for _, values in dates_values for value in values))
    result = evaluate_candidate_factor_outcomes(_Batch(candidates), outcomes, _spec(outcomes=("stock_forward_return_pct",)))
    summary = result.summary_for("quality_score", 5, "stock_forward_return_pct")
    assert tuple(item.rank_ic for item in result.daily_evaluations) == pytest.approx((1.0, -1.0, 0.0))
    assert (summary.positive_ic_date_count, summary.zero_ic_date_count, summary.negative_ic_date_count) == (1, 1, 1)
    assert summary.positive_ic_rate == pytest.approx(1 / 3)


def test_ordering_identity_stability_sensitivity_and_immutability() -> None:
    candidates = _candidates((1, 2, 3, 4, 5))
    outcomes = _outcome_set(candidates, (1, 2, 3, 4, 5))
    spec = _spec(factors=("score", "quality_score"), outcomes=("stock_forward_return_pct",))
    first = evaluate_candidate_factor_outcomes(_Batch(tuple(reversed(candidates))), replace(outcomes, outcomes=tuple(reversed(outcomes.outcomes))), spec)
    repeated = evaluate_candidate_factor_outcomes(_Batch(candidates), outcomes, spec)
    assert first.result_identity == repeated.result_identity
    assert tuple(item.factor for item in first.daily_evaluations) == ("score", "quality_score")
    assert tuple(item.factor for item in first.summaries) == ("score", "quality_score")

    changed_candidates = (replace(candidates[0], quality_score=9.0),) + candidates[1:]
    changed_factor = evaluate_candidate_factor_outcomes(_Batch(changed_candidates), outcomes, spec)
    assert changed_factor.result_identity != repeated.result_identity
    changed_outcome_record = replace(outcomes.outcomes[0], stock_forward_return_pct=9.0, outcome_identity="changed")
    changed_outcome = evaluate_candidate_factor_outcomes(_Batch(candidates), replace(outcomes, outcomes=(changed_outcome_record,) + outcomes.outcomes[1:], set_identity="changed-set"), spec)
    assert changed_outcome.result_identity != repeated.result_identity

    changed_outcome_identity_record = replace(outcomes.outcomes[0], outcome_identity="identity-only-change")
    changed_outcome_identity = evaluate_candidate_factor_outcomes(
        _Batch(candidates),
        replace(outcomes, outcomes=(changed_outcome_identity_record,) + outcomes.outcomes[1:]),
        spec,
    )
    assert changed_outcome_identity.daily_evaluations[0].identity != repeated.daily_evaluations[0].identity
    assert changed_outcome_identity.result_identity != repeated.result_identity

    changed_status_record = replace(
        outcomes.outcomes[0],
        status=ForwardOutcomeStatus.CENSORED_AFTER_DATA_END,
        outcome_identity="status-only-change",
    )
    changed_status = evaluate_candidate_factor_outcomes(
        _Batch(candidates),
        replace(outcomes, outcomes=(changed_status_record,) + outcomes.outcomes[1:]),
        spec,
    )
    assert changed_status.daily_evaluations[0].identity != repeated.daily_evaluations[0].identity
    assert changed_status.result_identity != repeated.result_identity

    changed_source_identity = evaluate_candidate_factor_outcomes(
        replace(_Batch(candidates), batch_identity="changed-candidate-batch"),
        replace(outcomes, source_candidate_batch_identity="changed-candidate-batch"),
        spec,
    )
    assert changed_source_identity.result_identity != repeated.result_identity
    assert _spec(minimum=4).fingerprint != _spec(minimum=5).fingerprint
    assert _spec(low=.2, name="cutoff").fingerprint != _spec(low=.3, name="cutoff").fingerprint
    with pytest.raises(TypeError):
        repeated.status_counts_by_horizon[5][ForwardOutcomeStatus.AVAILABLE] = 0  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        repeated.daily_evaluations[0].factor = "changed"  # type: ignore[misc]


def test_empty_batch_and_public_contracts_have_no_significance_fields() -> None:
    result = evaluate_candidate_factor_outcomes(
        _Batch(()), _OutcomeSet((), requested_horizons=(5,)),
        _spec(outcomes=("stock_forward_return_pct",)),
    )
    assert result.daily_evaluations == ()
    assert len(result.summaries) == 1
    assert result.summaries[0].total_signal_dates == 0
    forbidden = {"p_value", "pvalue", "t_stat", "t_statistic", "confidence_interval", "significance"}
    for contract in (DailyFactorOutcomeEvaluation, FactorHorizonEvaluationSummary, CandidateFactorOutcomeEvaluationResult):
        assert not forbidden.intersection(item.name for item in fields(contract))


def test_evaluation_performs_no_runtime_or_data_access() -> None:
    candidates = _candidates((1, 2, 3, 4, 5))
    batch = _Batch(candidates)
    outcomes = _outcome_set(candidates, (1, 2, 3, 4, 5))
    # Structural inputs deliberately provide no database, cache, feature,
    # ranking, execution, or simulator methods that could be called.
    result = evaluate_candidate_factor_outcomes(batch, outcomes, _spec(outcomes=("stock_forward_return_pct",)))
    assert result.summaries[0].ic_defined_date_count == 1


def test_fresh_import_loads_no_production_runtime_modules(tmp_path: Path) -> None:
    missing = tmp_path / "must-not-exist.db"
    script = (
        "import sys; import quantlab.evaluation; "
        "forbidden={"
        "'quantlab.features.builtins','quantlab.alpha.frozen_q70','quantlab.candidates.frozen_q70',"
        "'quantlab.ranking.cross_sectional','quantlab.diagnostics.candidate_factors',"
        "'strategy.scanner','backtesting.trade','backtesting.portfolio_simulator'}; "
        "loaded=forbidden.intersection(sys.modules); "
        "assert not loaded, f'forbidden evaluation imports: {sorted(loaded)}'"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, MARKET_DATABASE_PATH=str(missing), PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert not missing.exists()


def test_reference_spec_is_frozen_descriptive_single_factor_contract() -> None:
    spec = FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1
    assert spec.horizons == (5, 10, 20)
    assert spec.minimum_cross_section_size == 5
    assert (spec.low_bucket_max_percentile, spec.high_bucket_min_percentile) == (.30, .70)
    assert spec.outcome_fields == ("stock_forward_return_pct", "excess_forward_return_pct_points")
    assert "breadth_ema50_pct" in spec.factors and "breadth_ema50_change_10d" in spec.factors
