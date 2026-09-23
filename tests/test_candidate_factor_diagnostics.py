from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType

import pytest

from quantlab.candidates import FrozenQ70CandidateBatch, FrozenQ70CandidateRecord
from quantlab.diagnostics import (
    CandidateFactorSet,
    DIAGNOSTIC_NUMERIC_FIELDS,
    FROZEN_Q70_CANDIDATE_DIAGNOSTICS_V1,
    diagnose_candidate_factors,
)


DAY_1 = "2024-01-02"
DAY_2 = "2024-01-03"


def _record(
    symbol: str,
    *,
    day: str = DAY_1,
    quality_score: object = .8,
    score: object = 1.0,
    relative_strength_20d: object = 5.0,
    adx: object = 10.0,
    atr_percent: object = 2.0,
    rsi14: object = 60.0,
    volume_ratio: object = 1.2,
    breadth_ema50_pct: object = 70.0,
    breadth_ema50_change_10d: object = 2.0,
) -> FrozenQ70CandidateRecord:
    symbol = symbol.upper()
    key = f"{symbol}:{day}"
    return FrozenQ70CandidateRecord(
        candidate_key=key,
        evaluation_key=key,
        symbol=symbol,
        signal_date=day,
        snapshot_id="snapshot",
        prepared_v6_identity="prepared",
        phase_3_7_run_identity="phase-3-7",
        entry_policy_identity="entry",
        q70_policy_fingerprint="q70",
        universe_membership_identity="universe",
        score=score,
        relative_strength_20d=relative_strength_20d,
        adx=adx,
        component_percentiles=MappingProxyType({"score": 1.0, "relative_strength_20d": .8, "adx": .7}),
        quality_score=quality_score,
        q70_threshold=.70,
        acceptance_reason="Q0.70_PASS",
        paper_v2_state="HEALTHY_BULL",
        signal_close=10.0,
        atr14=1.0,
        atr_percent=atr_percent,
        rsi14=rsi14,
        volume_ratio=volume_ratio,
        ema10=9.8,
        ema20=9.5,
        ema50=9.0,
        previous_20d_high=9.9,
        donchian_breakout=True,
        market_regime="BULL",
        breadth_ema50_pct=breadth_ema50_pct,
        breadth_ema50_change_10d=breadth_ema50_change_10d,
        breadth_universe_count=100,
    )


def _batch(*records: FrozenQ70CandidateRecord, through: str = DAY_2) -> FrozenQ70CandidateBatch:
    return FrozenQ70CandidateBatch(
        candidates=records,
        requested_symbols=tuple(item.symbol for item in records),
        available_symbols=tuple(item.symbol for item in records),
        start_date=DAY_1,
        through_date=through,
        snapshot_id="snapshot",
        prepared_v6_identity="prepared",
        phase_3_7_run_identity="phase-3-7",
        entry_policy_identity="entry",
        q70_policy_fingerprint="q70",
        universe_membership_identity="universe",
    )


def _set(*fields: str, name: str = "TEST_DIAGNOSTICS", version: str = "1") -> CandidateFactorSet:
    return CandidateFactorSet(name=name, version=version, fields=fields)


def test_builtin_diagnostic_field_set_is_exactly_the_accepted_candidate_subset() -> None:
    expected = (
        "quality_score", "score", "relative_strength_20d", "adx", "atr_percent",
        "rsi14", "volume_ratio", "breadth_ema50_pct", "breadth_ema50_change_10d",
    )
    assert FROZEN_Q70_CANDIDATE_DIAGNOSTICS_V1.name == "FROZEN_Q70_CANDIDATE_DIAGNOSTICS_V1"
    assert FROZEN_Q70_CANDIDATE_DIAGNOSTICS_V1.fields == expected
    assert DIAGNOSTIC_NUMERIC_FIELDS == set(expected)


def test_exact_descriptive_statistics_linear_quantiles_and_ties() -> None:
    source = _batch(
        _record("A", score=1),
        _record("B", score=2),
        _record("C", score=2),
        _record("D", score=5),
        _record("E", score=None),
    )
    diagnostic = diagnose_candidate_factors(source, _set("score")).per_date[0].per_factor["score"]
    assert diagnostic.total_candidate_count == 5
    assert diagnostic.finite_count == 4 and diagnostic.missing_nonfinite_count == 1
    assert diagnostic.finite_coverage_pct == 80.0
    assert (diagnostic.minimum, diagnostic.maximum, diagnostic.mean) == (1.0, 5.0, 2.5)
    assert diagnostic.population_std == 1.5
    assert diagnostic.median == 2.0
    assert diagnostic.percentile_25 == 1.75
    assert diagnostic.percentile_75 == 2.75
    assert diagnostic.interquartile_range == 1.0
    assert diagnostic.unique_finite_count == 3
    assert diagnostic.tie_count == 1 and diagnostic.tie_rate == .25
    assert diagnostic.constant is False


def test_nonfinite_empty_single_and_constant_samples_have_explicit_contract() -> None:
    all_missing = _batch(
        _record("NONE", score=None),
        _record("NAN", score=float("nan")),
        _record("PINF", score=float("inf")),
        _record("NINF", score=float("-inf")),
    )
    missing = diagnose_candidate_factors(all_missing, _set("score")).per_date[0].per_factor["score"]
    assert missing.finite_count == 0 and missing.missing_nonfinite_count == 4
    assert missing.finite_coverage_pct == 0.0
    assert (missing.minimum, missing.maximum, missing.mean, missing.population_std) == (None, None, None, None)
    assert (missing.median, missing.percentile_25, missing.percentile_75, missing.interquartile_range) == (None, None, None, None)
    assert missing.tie_rate is None and missing.constant is False

    single = diagnose_candidate_factors(_batch(_record("ONE", score=7)), _set("score")).per_date[0].per_factor["score"]
    assert (single.mean, single.population_std, single.median, single.percentile_25, single.percentile_75) == (7, 0, 7, 7, 7)
    assert single.constant is True and single.tie_count == 0 and single.tie_rate == 0.0

    constant = diagnose_candidate_factors(
        _batch(_record("A", score=3), _record("B", score=3), _record("C", score=3)),
        _set("score"),
    ).per_date[0].per_factor["score"]
    assert constant.constant is True
    assert constant.unique_finite_count == 1 and constant.tie_count == 2
    assert constant.tie_rate == pytest.approx(2 / 3)


def test_pearson_and_spearman_average_rank_ties_are_exact() -> None:
    source = _batch(
        _record("A", score=1, adx=10),
        _record("B", score=2, adx=20),
        _record("C", score=2, adx=30),
        _record("D", score=4, adx=40),
    )
    date = diagnose_candidate_factors(source, _set("score", "adx")).per_date[0]
    association = date.association_for("score", "adx")
    assert association.pairwise_finite_count == 4
    assert association.pearson_correlation == pytest.approx(0.9233805168766388)
    assert association.spearman_correlation == pytest.approx(0.9486832980505138)
    assert association.undefined_reason is None


def test_pairwise_finite_filtering_and_undefined_reasons() -> None:
    source = _batch(
        _record("A", score=1, adx=10),
        _record("B", score=2, adx=None),
        _record("C", score=float("inf"), adx=30),
    )
    association = diagnose_candidate_factors(source, _set("score", "adx")).per_date[0].association_for("score", "adx")
    assert association.pairwise_finite_count == 1
    assert association.pearson_correlation is None and association.spearman_correlation is None
    assert association.undefined_reason == "fewer_than_two_pairwise_finite_observations"

    constant = diagnose_candidate_factors(
        _batch(_record("A", score=1, adx=10), _record("B", score=1, adx=20)),
        _set("score", "adx"),
    ).per_date[0].association_for("score", "adx")
    assert constant.pairwise_finite_count == 2
    assert constant.pearson_correlation is None and constant.spearman_correlation is None
    assert constant.undefined_reason == "constant_factor:first"


def test_per_date_and_pooled_aggregate_stability_are_separate() -> None:
    source = _batch(
        _record("A", day=DAY_1, score=1, adx=10),
        _record("B", day=DAY_1, score=3, adx=30),
        _record("C", day=DAY_2, score=5, adx=50),
        _record("D", day=DAY_2, score=None, adx=70),
    )
    result = diagnose_candidate_factors(source, _set("score", "adx"))
    assert [item.signal_date for item in result.per_date] == [DAY_1, DAY_2]
    assert [item.per_factor["score"].median for item in result.per_date] == [2.0, 5.0]
    assert result.aggregate.scope == "pooled_descriptive_only_not_cross_sectional_ranking"
    assert result.aggregate.date_count == 2 and result.aggregate.total_candidate_observations == 4
    assert result.aggregate.per_factor["score"].finite_count == 3
    assert result.aggregate.per_factor["score"].median == 3.0
    stability = result.aggregate.date_level_stability["score"]
    assert stability.dates_with_finite_observations == 2
    assert stability.constant_date_count == 1
    assert stability.mean_date_finite_coverage_pct == 75.0
    assert stability.mean_date_median == 3.5
    assert stability.population_std_date_median == 1.5
    assert stability.mean_date_iqr == .5


def test_input_order_and_identities_are_stable_and_sensitive() -> None:
    records = (_record("B", score=2), _record("A", score=1))
    factor_set = _set("score", "adx")
    first = diagnose_candidate_factors(_batch(*records), factor_set)
    repeated = diagnose_candidate_factors(_batch(*reversed(records)), _set("score", "adx"))
    assert first.result_identity == repeated.result_identity
    assert [item.identity for item in first.per_date] == [item.identity for item in repeated.per_date]

    changed_value = diagnose_candidate_factors(_batch(replace(records[0], score=99), records[1]), factor_set)
    assert changed_value.per_date[0].identity != first.per_date[0].identity
    assert changed_value.result_identity != first.result_identity
    changed_set = diagnose_candidate_factors(_batch(*records), _set("adx", "score"))
    assert changed_set.factor_set_fingerprint != first.factor_set_fingerprint
    assert changed_set.result_identity != first.result_identity
    changed_source = diagnose_candidate_factors(replace(_batch(*records), through_date="2024-01-04"), factor_set)
    assert changed_source.result_identity != first.result_identity


def test_factor_set_validation_empty_batch_and_immutability() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        _set("signal_close")
    with pytest.raises(ValueError, match="unsupported"):
        _set("paper_v2_state")
    with pytest.raises(ValueError, match="duplicate"):
        _set("score", "score")
    with pytest.raises(ValueError, match="at least one"):
        _set()

    result = diagnose_candidate_factors(_batch(), _set("score", "adx"))
    assert result.per_date == ()
    assert result.aggregate.date_count == 0 and result.aggregate.total_candidate_observations == 0
    assert result.aggregate.per_factor["score"].finite_count == 0
    assert result.aggregate.association_for("score", "adx").undefined_reason == "fewer_than_two_pairwise_finite_observations"
    assert result.result_identity == diagnose_candidate_factors(_batch(), _set("score", "adx")).result_identity
    with pytest.raises(TypeError):
        result.aggregate.per_factor["score"] = object()  # type: ignore[index]
    with pytest.raises(TypeError):
        result.aggregate.date_level_stability["score"] = object()  # type: ignore[index]
    with pytest.raises(Exception):
        result.aggregate.date_count = 9  # type: ignore[misc]


def test_duplicate_candidate_keys_fail_at_the_immutable_source_boundary() -> None:
    record = _record("A")
    with pytest.raises(ValueError, match="duplicate candidate key"):
        _batch(record, record)


def test_diagnostics_invoke_no_data_feature_q70_trade_simulator_or_pnl_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import backtesting.portfolio_simulator as portfolio_module
    import backtesting.trade as trade_module
    import quantlab.adapters.frozen_q70_candidate_decisions as decision_module
    import quantlab.alpha.frozen_q70 as q70_module
    import quantlab.catalog.market_data_snapshot as snapshot_module
    import quantlab.features.registry as registry_module

    def forbidden(*args, **kwargs):
        raise AssertionError("diagnostics must not invoke upstream, outcomes, or execution")

    monkeypatch.setattr(snapshot_module.MarketDataSnapshot, "load_ohlcv", forbidden)
    monkeypatch.setattr(registry_module.FeatureRegistry, "compute", forbidden)
    monkeypatch.setattr(decision_module, "evaluate_frozen_q70_candidates", forbidden)
    monkeypatch.setattr(q70_module, "score_frozen_q70_batch", forbidden)
    monkeypatch.setattr(trade_module.Trade, "net_pnl", property(forbidden))
    monkeypatch.setattr(portfolio_module, "PortfolioSimulator", forbidden)
    result = diagnose_candidate_factors(_batch(_record("A")), _set("score", "adx"))
    assert result.aggregate.total_candidate_observations == 1


def test_fresh_process_import_has_no_database_cache_or_network_side_effect(tmp_path: Path) -> None:
    missing = tmp_path / "must-not-exist.db"
    completed = subprocess.run(
        [sys.executable, "-c", "import quantlab.diagnostics"],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, MARKET_DATABASE_PATH=str(missing)),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert not missing.exists()
