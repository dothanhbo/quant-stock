from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType

import pytest

from quantlab.candidates import FrozenQ70CandidateBatch, FrozenQ70CandidateRecord
from quantlab.ranking import (
    CandidateRankingPolicy,
    FROZEN_Q70_QUALITY_RANK_V1,
    MissingValuePolicy,
    RANKABLE_NUMERIC_FIELDS,
    RankingDirection,
    RankingFactor,
    rank_candidate_batch,
)


DAY_1 = "2024-01-02"
DAY_2 = "2024-01-03"


def _record(
    symbol: str,
    *,
    day: str = DAY_1,
    quality_score: object = .8,
    score: object = 50.0,
    relative_strength_20d: object = 5.0,
    adx: object = 20.0,
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


def _policy(
    *factors: RankingFactor,
    name: str = "TEST_RANK",
    version: str = "1",
) -> CandidateRankingPolicy:
    return CandidateRankingPolicy(name=name, version=version, factors=factors)


def _factor(
    field: str,
    *,
    weight: float = 1.0,
    direction: RankingDirection = RankingDirection.HIGHER_IS_BETTER,
    missing: MissingValuePolicy = MissingValuePolicy.WORST,
) -> RankingFactor:
    return RankingFactor(field, weight, direction, missing)


def test_builtin_quality_policy_and_deterministic_tie_breaking() -> None:
    source = _batch(
        _record("CCC", quality_score=.9),
        _record("AAA", quality_score=.8),
        _record("BBB", quality_score=.9),
    )
    result = rank_candidate_batch(source, FROZEN_Q70_QUALITY_RANK_V1)
    assert FROZEN_Q70_QUALITY_RANK_V1.name == "FROZEN_Q70_QUALITY_RANK_V1"
    assert len(FROZEN_Q70_QUALITY_RANK_V1.factors) == 1
    factor = FROZEN_Q70_QUALITY_RANK_V1.factors[0]
    assert (factor.field_name, factor.weight, factor.direction, factor.missing_value_policy) == (
        "quality_score", 1.0, RankingDirection.HIGHER_IS_BETTER, MissingValuePolicy.WORST,
    )
    ranked = result[0].ranked_candidates
    assert [item.candidate.symbol for item in ranked] == ["BBB", "CCC", "AAA"]
    assert [item.factor_percentiles["quality_score"] for item in ranked] == [1.0, 1.0, 1 / 3]
    assert [item.ordinal for item in ranked] == [1, 2, 3]
    assert ranked[0].tie_break_evidence == {
        "composite_score_desc": 1.0,
        "quality_score_desc": .9,
        "symbol_asc": "BBB",
        "candidate_key_asc": f"BBB:{DAY_1}",
    }


def test_signal_dates_rank_independently_and_input_order_is_irrelevant() -> None:
    records = (
        _record("BBB", day=DAY_2, score=1),
        _record("AAA", day=DAY_1, score=1),
        _record("CCC", day=DAY_1, score=2),
    )
    policy = _policy(_factor("score"))
    first = rank_candidate_batch(_batch(*records), policy)
    second = rank_candidate_batch(_batch(*reversed(records)), policy)
    assert [item.signal_date for item in first] == [DAY_1, DAY_2]
    assert [item.factor_percentiles["score"] for item in first[0].ranked_candidates] == [1.0, .5]
    assert first[1].ranked_candidates[0].factor_percentiles["score"] == 1.0
    assert [item.result_identity for item in first] == [item.result_identity for item in second]


def test_weak_empirical_percentiles_preserve_ties_in_both_directions() -> None:
    source = _batch(
        _record("LOW", score=1, quality_score=.7),
        _record("TIEA", score=2, quality_score=.8),
        _record("TIEB", score=2, quality_score=.9),
    )
    higher = rank_candidate_batch(source, _policy(_factor("score")))[0]
    assert {item.candidate.symbol: item.factor_percentiles["score"] for item in higher.ranked_candidates} == {
        "LOW": 1 / 3, "TIEA": 1.0, "TIEB": 1.0,
    }
    lower = rank_candidate_batch(source, _policy(_factor("score", direction=RankingDirection.LOWER_IS_BETTER)))[0]
    assert {item.candidate.symbol: item.factor_percentiles["score"] for item in lower.ranked_candidates} == {
        "LOW": 1.0, "TIEA": 2 / 3, "TIEB": 2 / 3,
    }


@pytest.mark.parametrize("missing_value", [None, float("nan"), float("inf"), float("-inf")])
def test_missing_neutral_worst_and_exclude_policies(missing_value: object) -> None:
    source = _batch(_record("MISS", score=missing_value), _record("FINITE", score=10))
    neutral = rank_candidate_batch(source, _policy(_factor("score", missing=MissingValuePolicy.NEUTRAL)))[0]
    assert next(item for item in neutral.ranked_candidates if item.candidate.symbol == "MISS").composite_score == .5
    worst = rank_candidate_batch(source, _policy(_factor("score", missing=MissingValuePolicy.WORST)))[0]
    assert next(item for item in worst.ranked_candidates if item.candidate.symbol == "MISS").composite_score == 0.0
    excluded = rank_candidate_batch(source, _policy(_factor("score", missing=MissingValuePolicy.EXCLUDE)))[0]
    assert excluded.excluded_candidate_keys == (f"MISS:{DAY_1}",)
    assert excluded.exclusion_reasons[f"MISS:{DAY_1}"] == "missing_nonfinite:score"
    assert [item.candidate.symbol for item in excluded.ranked_candidates] == ["FINITE"]


def test_composite_uses_fixed_total_policy_weight_without_candidate_renormalization() -> None:
    source = _batch(
        _record("MISSING", score=20, adx=None),
        _record("COMPLETE", score=10, adx=30),
    )
    policy = _policy(
        _factor("score", weight=1.0),
        _factor("adx", weight=3.0, missing=MissingValuePolicy.NEUTRAL),
    )
    result = rank_candidate_batch(source, policy)[0]
    by_symbol = {item.candidate.symbol: item for item in result.ranked_candidates}
    assert by_symbol["MISSING"].factor_contributions == {"score": 1.0, "adx": 1.5}
    assert by_symbol["MISSING"].composite_score == .625
    assert by_symbol["COMPLETE"].composite_score == .875


def test_empty_batch_and_policy_validation_and_allowlist() -> None:
    assert rank_candidate_batch(_batch(), FROZEN_Q70_QUALITY_RANK_V1) == ()
    assert RANKABLE_NUMERIC_FIELDS == {
        "quality_score", "score", "relative_strength_20d", "adx", "atr_percent",
        "rsi14", "volume_ratio", "breadth_ema50_pct", "breadth_ema50_change_10d",
    }
    with pytest.raises(ValueError, match="not rankable"):
        _factor("signal_close")
    with pytest.raises(ValueError, match="finite non-negative"):
        _factor("score", weight=float("nan"))
    with pytest.raises(ValueError, match="finite non-negative"):
        _factor("score", weight=-1)
    with pytest.raises(ValueError, match="positive weight"):
        _policy(_factor("score", weight=0))
    with pytest.raises(ValueError, match="duplicate"):
        _policy(_factor("score"), _factor("score", weight=2))
    with pytest.raises(ValueError, match="direction"):
        RankingFactor("score", 1, "higher", MissingValuePolicy.WORST)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="missing policy"):
        RankingFactor("score", 1, RankingDirection.HIGHER_IS_BETTER, "worst")  # type: ignore[arg-type]


def test_duplicate_source_keys_fail_before_ranking() -> None:
    record = _record("AAA")
    with pytest.raises(ValueError, match="duplicate candidate key"):
        _batch(record, record)


def test_policy_and_result_identities_are_stable_sensitive_and_immutable() -> None:
    source = _batch(_record("AAA", score=10), _record("BBB", score=20))
    policy = _policy(_factor("score"))
    repeated_policy = _policy(_factor("score"))
    first = rank_candidate_batch(source, policy)[0]
    repeated = rank_candidate_batch(_batch(*reversed(source.candidates)), repeated_policy)[0]
    assert policy.fingerprint == repeated_policy.fingerprint
    assert first.result_identity == repeated.result_identity

    changed_policy = _policy(_factor("score", direction=RankingDirection.LOWER_IS_BETTER))
    assert rank_candidate_batch(source, changed_policy)[0].result_identity != first.result_identity
    changed_record = replace(source.candidates[0], score=999)
    changed_source = _batch(changed_record, source.candidates[1])
    assert rank_candidate_batch(changed_source, policy)[0].result_identity != first.result_identity
    changed_source_identity = replace(source, through_date="2024-01-04")
    assert rank_candidate_batch(changed_source_identity, policy)[0].result_identity != first.result_identity

    ranked = first.ranked_candidates[0]
    with pytest.raises(TypeError):
        ranked.raw_values["score"] = 0  # type: ignore[index]
    with pytest.raises(TypeError):
        first.exclusion_reasons["x"] = "y"  # type: ignore[index]
    with pytest.raises(Exception):
        ranked.ordinal = 9  # type: ignore[misc]


def test_ranking_invokes_no_data_feature_q70_trade_or_simulator_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import backtesting.portfolio_simulator as portfolio_module
    import backtesting.trade as trade_module
    import quantlab.adapters.frozen_q70_candidate_decisions as decision_module
    import quantlab.alpha.frozen_q70 as q70_module
    import quantlab.catalog.market_data_snapshot as snapshot_module
    import quantlab.features.registry as registry_module

    def forbidden(*args, **kwargs):
        raise AssertionError("ranking must not invoke upstream or execution behavior")

    monkeypatch.setattr(snapshot_module.MarketDataSnapshot, "load_ohlcv", forbidden)
    monkeypatch.setattr(registry_module.FeatureRegistry, "compute", forbidden)
    monkeypatch.setattr(decision_module, "evaluate_frozen_q70_candidates", forbidden)
    monkeypatch.setattr(q70_module, "score_frozen_q70_batch", forbidden)
    monkeypatch.setattr(trade_module, "Trade", forbidden)
    monkeypatch.setattr(portfolio_module, "PortfolioSimulator", forbidden)
    result = rank_candidate_batch(_batch(_record("AAA")), FROZEN_Q70_QUALITY_RANK_V1)
    assert result[0].ranked_candidates[0].candidate_key == f"AAA:{DAY_1}"


def test_fresh_process_import_has_no_database_cache_or_network_side_effect(tmp_path: Path) -> None:
    missing = tmp_path / "must-not-exist.db"
    completed = subprocess.run(
        [sys.executable, "-c", "import quantlab.ranking"],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, MARKET_DATABASE_PATH=str(missing)),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert not missing.exists()
