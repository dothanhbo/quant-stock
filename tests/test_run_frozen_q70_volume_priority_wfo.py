from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import csv
import subprocess
import sys

import pandas as pd
import pytest

import research.run_frozen_q70_volume_priority_wfo as runner
from backtesting.frozen_q70_evaluator import FrozenQ70Decision
from backtesting.portfolio_simulator import CandidatePriorityEvidence
from backtesting.walk_forward import WalkForwardFold


def _folds() -> list[WalkForwardFold]:
    result = []
    for fold in range(1, 14):
        start = pd.Timestamp("2020-01-01") + pd.DateOffset(months=fold - 1)
        result.append(WalkForwardFold(
            fold=fold, train_start=start, train_end=start,
            test_start=start, test_end=start,
        ))
    return result


def _decision(date: datetime) -> FrozenQ70Decision:
    key = f"AAA|{date.isoformat()}"
    return FrozenQ70Decision(
        phase="test", symbol="AAA", signal_date=date, candidate_key=key,
        score=80.0, relative_strength_20d=70.0, adx=30.0,
        percentile_score=1.0, percentile_relative_strength_20d=1.0,
        percentile_adx=1.0, quality_score=1.0, quality_threshold=.70,
        market_state="HEALTHY_BULL", breadth_ema50_pct=70.0,
        breadth_ema50_change_10d=1.0, gate_accepted=True,
        gate_reason="PASS", state_policy_eligible=True, state_policy_reason=None,
        execution_disposition="maximum_orders_per_scan", executed_trade_key=None,
    )


def _evidence(date: datetime, fingerprint: str) -> CandidatePriorityEvidence:
    return CandidatePriorityEvidence(
        candidate_key=f"AAA|{date.isoformat()}", symbol="AAA", signal_date=date,
        entry_date=date, volume_ratio=2.0, volume_ratio_finite=True,
        q70_quality_score=1.0, baseline_signal_score=80.0,
        baseline_within_entry_date_ordinal=1,
        signal_date_group_key=date.date().isoformat(), signal_date_group_size=1,
        signal_date_group_slot_ordinals=(1,), variant_within_signal_date_ordinal=1,
        final_simulator_priority_ordinal=1, ranking_policy_fingerprint=fingerprint,
    )


def _audit(date: datetime) -> SimpleNamespace:
    return SimpleNamespace(
        candidate_key=f"AAA|{date.isoformat()}", symbol="AAA", signal_date=date,
        entry_date=date, entry_price=100.0, exit_date=date, exit_price=105.0,
        exit_reason="Take Profit", execution="next_open", signal_score=80.0,
        relative_strength_20d=70.0, adx=30.0, volume_ratio=2.0, atr=2.0,
        stop_price=96.0, market_regime="BULL",
        entry_model="hybrid_trend_donchian", q70_quality_score=1.0,
        q70_gate_reason="PASS",
    )


def _patch_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, list[dict]]:
    database = tmp_path / "market.db"
    database.write_bytes(b"read-only-fixture")
    calls: list[dict] = []
    coverage = SimpleNamespace(
        start_date=runner.DEFAULT_START_DATE, end_date=runner.DEFAULT_END_DATE,
        minimum_history_sessions=50, maximum_staleness_sessions=5,
    )
    breadth = SimpleNamespace(
        start_date=runner.DEFAULT_START_DATE, end_date=runner.DEFAULT_END_DATE,
        universe_mode="database_coverage",
    )
    monkeypatch.setattr(runner, "resolve_market_database_path", lambda value: database)
    monkeypatch.setattr(runner, "build_walk_forward_folds", lambda config: _folds())
    monkeypatch.setattr(runner, "build_database_coverage_index", lambda *args, **kwargs: coverage)
    monkeypatch.setattr(runner, "build_historical_breadth_index", lambda *args, **kwargs: breadth)
    monkeypatch.setattr(runner, "_validate_canonical_baseline", lambda frame, reference: {
        "reference_path": str(reference), "reference_sha256": runner.CANONICAL_TRADE_SHA256,
        "row_count": len(frame),
    })
    reference = tmp_path / "reference.csv"
    reference.write_text("fold\n", encoding="utf-8")

    def fake_evaluator(**kwargs):
        calls.append(kwargs)
        initial = kwargs["parity_config"].initial_cash
        if not kwargs.get("collect_decision_ledger"):
            return [], {"total_trades": 0, "total_return_pct": 0.0}, pd.DataFrame()
        day = pd.Timestamp(kwargs["start_date"]).to_pydatetime()
        policy = kwargs.get("candidate_priority_policy")
        fingerprint = (
            runner.CANONICAL_SIGNAL_SCORE_PRIORITY_FINGERPRINT
            if policy is None else policy.fingerprint
        )
        decision = _decision(day)
        metrics = {
            "final_equity": initial, "total_return_pct": 0.0, "total_trades": 0,
            "win_rate_pct": 0.0, "profit_factor": 0.0, "max_drawdown_pct": 0.0,
            "gross_profit": 0.0, "gross_loss": 0.0, "gross_trading_pnl": 0.0,
            "net_trading_pnl": 0.0, "total_buy_commission": 0.0,
            "total_sell_commission": 0.0, "total_sell_tax": 0.0,
            "total_transaction_cost": 0.0, "total_evaluation_rows": 1,
            "base_entry_candidates": 1, "q70_accepted_candidates": 1,
            "q70_rejection_counts": {}, "q70_rejection_state_counts": {},
            "coverage_eligible_count_min": 1, "coverage_eligible_count_max": 1,
            "coverage_eligible_count_mean": 1.0, "decision_ledger": (decision,),
            "candidate_priority_ledger": (_evidence(day, fingerprint),),
            "accepted_candidate_audit": (_audit(day),),
        }
        curve = pd.DataFrame({"date": [day], "equity": [initial]})
        return [], metrics, curve

    monkeypatch.setattr(runner, "run_frozen_q70_backtest", fake_evaluator)
    return reference, calls


def test_paired_runner_reuses_dependencies_and_writes_exact_artifact_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    reference, calls = _patch_run(monkeypatch, tmp_path)
    output = tmp_path / "results" / "experiment"
    result = runner.run_frozen_q70_volume_priority_wfo(
        database_path=tmp_path / "ignored.db", output_root=output,
        canonical_trade_path=reference,
    )
    assert output == result["output_root"]
    assert len(calls) == 52  # train + test for two arms and 13 folds
    assert {id(call["coverage_index"]) for call in calls} == {id(calls[0]["coverage_index"])}
    assert {id(call["breadth_index"]) for call in calls} == {id(calls[0]["breadth_index"])}
    assert all(call["universe_mode"] == "database_coverage" for call in calls)
    assert sum(call.get("candidate_priority_policy") is not None for call in calls) == 26
    assert all(call["minimum_history_sessions"] == 50 for call in calls)
    assert all(call["maximum_staleness_sessions"] == 5 for call in calls)
    assert {item.name for item in output.iterdir()} == {
        "experiment_manifest.json", "comparison.csv", "accepted_candidate_parity.csv",
        "executed_candidate_comparison.csv", "ranking_change_by_date.csv", "assumptions.md",
        "baseline_signal_score_priority", "volume_ratio_priority",
    }
    for arm, _ in runner.ARM_SPECS:
        assert {item.name for item in (output / arm).iterdir()} == {
            "folds.csv", "summary.csv", "trade_level_oos.csv",
            "candidate_decision_oos.csv", "candidate_priority_oos.csv",
            "policy_fingerprint.json",
        }
        assert len(pd.read_csv(output / arm / "folds.csv")) == 13
    assert pd.read_csv(output / "accepted_candidate_parity.csv")["parity"].all()
    assert set(pd.read_csv(output / "comparison.csv")["total_trades"]) == {0}
    executed = pd.read_csv(output / "executed_candidate_comparison.csv")
    ranking = pd.read_csv(output / "ranking_change_by_date.csv")
    assert tuple(executed.columns) == runner.EXECUTED_COMPARISON_COLUMNS
    assert tuple(ranking.columns) == runner.RANKING_CHANGE_COLUMNS
    comparison = pd.read_csv(output / "comparison.csv")
    assert set(comparison["direct_priority_divergence_count"]) == {0}
    assert set(comparison["downstream_portfolio_state_divergence_count"]) == {0}
    assert set(comparison["unexpected_divergence_count"]) == {0}
    manifest = result["manifest"]
    assert manifest["unexpected_divergence_count"] == 0
    assert "chronologically consistent" in manifest["divergence_causality_scope"]


def test_train_diagnostics_are_excluded_and_capital_chaining_is_arm_local(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    reference, calls = _patch_run(monkeypatch, tmp_path)
    runner.run_frozen_q70_volume_priority_wfo(
        output_root=tmp_path / "out" / "experiment", canonical_trade_path=reference,
    )
    test_calls = [call for call in calls if call.get("collect_decision_ledger")]
    train_calls = [call for call in calls if not call.get("collect_decision_ledger")]
    assert len(test_calls) == len(train_calls) == 26
    assert all(call["parity_config"].initial_cash == 100_000_000 for call in test_calls)
    assert all(call["parity_config"].initial_cash == 100_000_000 for call in train_calls)


def test_output_protection_and_atomic_overwrite_restore(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    reference, _ = _patch_run(monkeypatch, tmp_path)
    output = tmp_path / "out" / "experiment"
    output.mkdir(parents=True)
    marker = output / "keep.txt"
    marker.write_text("old", encoding="utf-8")
    with pytest.raises(FileExistsError):
        runner.run_frozen_q70_volume_priority_wfo(output_root=output, canonical_trade_path=reference)
    assert marker.read_text(encoding="utf-8") == "old"
    runner.run_frozen_q70_volume_priority_wfo(
        output_root=output, canonical_trade_path=reference, overwrite=True,
    )
    assert not marker.exists()
    for unsafe in (tmp_path, runner.PROJECT_ROOT, runner.PROJECT_ROOT / "research_results"):
        if unsafe == tmp_path:
            continue
        with pytest.raises(ValueError):
            runner._validate_output(unsafe, database_path=tmp_path / "market.db", overwrite=True)


def test_canonical_baseline_validation_compares_exact_ordered_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    row = {column: index for index, column in enumerate(runner.TRADE_COLUMNS)}
    row["symbol"] = "AAA"
    frame = pd.DataFrame([row])
    reference = tmp_path / "canonical.csv"
    frame.to_csv(reference, index=False, lineterminator="\n")
    monkeypatch.setattr(runner, "CANONICAL_TRADE_SHA256", runner._file_hash(reference).upper())
    result = runner._validate_canonical_baseline(frame, reference)
    assert result["row_count"] == 1
    changed = frame.copy()
    changed.loc[0, "symbol"] = "DIFFERENT"
    with pytest.raises(ValueError, match="does not match"):
        runner._validate_canonical_baseline(changed, reference)


def test_ranking_change_audit_preserves_subgroup_slots_and_traces_top_three() -> None:
    base = pd.DataFrame([
        {"fold": 1, "entry_date": "2020-01-10", "candidate_key": key,
         "signal_date_group_key": group, "baseline_within_entry_date_ordinal": ordinal,
         "final_simulator_priority_ordinal": ordinal, "execution_disposition": disposition}
        for key, group, ordinal, disposition in (
            ("A", "d1", 1, "executed"), ("B", "d2", 2, "executed"),
            ("C", "d1", 3, "executed"), ("D", "d2", 4, "maximum_orders_per_scan"),
        )
    ])
    variant = base.copy()
    variant["final_simulator_priority_ordinal"] = [3, 4, 1, 2]
    variant["execution_disposition"] = ["executed", "maximum_orders_per_scan", "executed", "executed"]
    audit = runner._ranking_changes({"priority": base}, {"priority": variant}).iloc[0]
    assert audit["changed_final_ordinal_count"] == 4
    assert audit["daily_order_cap_selection_changed"]
    assert audit["top_three_overlap_count"] == 2
    assert len(audit["ranking_change_identity"]) == 64


def _execution_row(
    key: str, *, fold: int, entry_date: str, baseline_ordinal: int,
    variant_ordinal: int, baseline_disposition: str, variant_disposition: str,
) -> dict[str, object]:
    return {
        "fold": fold, "candidate_key": key, "signal_date": entry_date,
        "entry_date": entry_date, "symbol": key,
        "baseline_executed": baseline_disposition == "executed",
        "variant_executed": variant_disposition == "executed",
        "baseline_entry_ordinal": baseline_ordinal,
        "variant_entry_ordinal": variant_ordinal,
        "volume_ratio": 1.0, "q70_quality": .9, "signal_score": 80.0,
        "baseline_disposition": baseline_disposition,
        "variant_disposition": variant_disposition,
    }


def _ranking_row(
    *, fold: int, entry_date: str, changed: int, identity: str,
) -> dict[str, object]:
    return {
        "fold": fold, "entry_date": entry_date, "accepted_entry_event_count": 2,
        "distinct_signal_date_subgroup_count": 1,
        "changed_final_ordinal_count": changed,
        "baseline_top_three_keys": "[]", "variant_top_three_keys": "[]",
        "top_three_overlap_count": 0, "daily_order_cap_selection_changed": changed > 0,
        "baseline_executed_count": 1, "variant_executed_count": 1,
        "baseline_rejection_counts": "{}", "variant_rejection_counts": "{}",
        "ranking_change_identity": identity,
        "direct_execution_divergence_count": 0,
        "downstream_divergence_count": 0, "unexpected_divergence_count": 0,
    }


def _causal_fixture(*, later_fold: int = 1) -> tuple[pd.DataFrame, pd.DataFrame]:
    execution = pd.DataFrame([
        _execution_row(
            "A", fold=1, entry_date="2020-01-02", baseline_ordinal=1,
            variant_ordinal=2, baseline_disposition="executed",
            variant_disposition="maximum_orders_per_scan",
        ),
        _execution_row(
            "B", fold=1, entry_date="2020-01-02", baseline_ordinal=2,
            variant_ordinal=1, baseline_disposition="maximum_orders_per_scan",
            variant_disposition="executed",
        ),
        _execution_row(
            "C", fold=later_fold, entry_date="2020-01-03", baseline_ordinal=1,
            variant_ordinal=1, baseline_disposition="executed",
            variant_disposition="insufficient_cash",
        ),
    ])
    ranking = pd.DataFrame([
        _ranking_row(fold=1, entry_date="2020-01-02", changed=2, identity="a" * 64),
        _ranking_row(fold=later_fold, entry_date="2020-01-03", changed=0, identity="b" * 64),
    ], columns=runner.RANKING_CHANGE_COLUMNS)
    return execution, ranking


def _empty_reconciliation_arms() -> dict[str, dict[str, object]]:
    folds = pd.DataFrame([{
        "fold": 1, "test_initial_equity": 100.0, "test_final_equity": 100.0,
        "test_return_pct": 0.0, "test_trades": 0,
        "test_total_transaction_cost": 0.0, "test_total_buy_commission": 0.0,
        "test_total_sell_commission": 0.0, "test_total_sell_tax": 0.0,
    }])
    trades = pd.DataFrame(columns=runner.TRADE_COLUMNS)
    summary = {"initial_equity": 100.0, "final_equity": 100.0, "total_transaction_cost": 0.0}
    return {
        "baseline_signal_score_priority": {"folds": folds.copy(), "trades": trades.copy(), "summary": dict(summary)},
        "volume_ratio_priority": {"folds": folds.copy(), "trades": trades.copy(), "summary": dict(summary)},
    }


def test_no_ranking_change_and_identical_arms_passes_causal_reconciliation() -> None:
    execution = pd.DataFrame([
        _execution_row(
            "A", fold=1, entry_date="2020-01-02", baseline_ordinal=1,
            variant_ordinal=1, baseline_disposition="executed", variant_disposition="executed",
        )
    ])
    ranking = pd.DataFrame([
        _ranking_row(fold=1, entry_date="2020-01-02", changed=0, identity="a" * 64)
    ], columns=runner.RANKING_CHANGE_COLUMNS)
    classified, changes, counts = runner._classify_execution_divergence(execution, ranking)
    assert counts == {"direct_priority": 0, "downstream_portfolio_state": 0, "unexpected": 0}
    runner._validate_reconciliation(
        _empty_reconciliation_arms(), pd.DataFrame({"parity": [True]}),
        classified, changes, counts,
    )


def test_no_ranking_change_with_execution_divergence_is_unexpected_and_fails() -> None:
    execution = pd.DataFrame([
        _execution_row(
            "A", fold=1, entry_date="2020-01-02", baseline_ordinal=1,
            variant_ordinal=1, baseline_disposition="executed",
            variant_disposition="insufficient_cash",
        )
    ])
    ranking = pd.DataFrame([
        _ranking_row(fold=1, entry_date="2020-01-02", changed=0, identity="a" * 64)
    ], columns=runner.RANKING_CHANGE_COLUMNS)
    classified, changes, counts = runner._classify_execution_divergence(execution, ranking)
    assert classified.iloc[0]["divergence_classification"] == "unexpected"
    with pytest.raises(ValueError, match="no direct priority causal anchor"):
        runner._validate_reconciliation(
            _empty_reconciliation_arms(), pd.DataFrame({"parity": [True]}),
            classified, changes, counts,
        )


def test_divergence_before_first_ranking_change_is_unexpected() -> None:
    execution, ranking = _causal_fixture()
    early = _execution_row(
        "EARLY", fold=1, entry_date="2020-01-01", baseline_ordinal=1,
        variant_ordinal=1, baseline_disposition="executed",
        variant_disposition="insufficient_cash",
    )
    execution = pd.concat([pd.DataFrame([early]), execution], ignore_index=True)
    ranking = pd.concat([
        pd.DataFrame([_ranking_row(fold=1, entry_date="2020-01-01", changed=0, identity="c" * 64)]),
        ranking,
    ], ignore_index=True).loc[:, runner.RANKING_CHANGE_COLUMNS]
    classified, _, counts = runner._classify_execution_divergence(execution, ranking)
    assert classified.loc[classified["candidate_key"] == "EARLY", "divergence_classification"].item() == "unexpected"
    assert counts["unexpected"] == 1


def test_direct_then_unchanged_ordinal_divergence_is_classified_downstream() -> None:
    execution, ranking = _causal_fixture()
    classified, changes, counts = runner._classify_execution_divergence(execution, ranking)
    assert list(classified["divergence_classification"]) == [
        "direct_priority", "direct_priority", "downstream_portfolio_state",
    ]
    later = classified.loc[classified["candidate_key"] == "C"].iloc[0]
    assert later["baseline_entry_ordinal"] == later["variant_entry_ordinal"] == 1
    assert later["causal_anchor_fold"] == 1
    assert later["causal_anchor_entry_date"] == pd.Timestamp("2020-01-02").isoformat()
    assert later["causal_anchor_identity"] == "a" * 64
    assert counts == {"direct_priority": 2, "downstream_portfolio_state": 1, "unexpected": 0}
    assert changes["direct_execution_divergence_count"].sum() == 2
    assert changes["downstream_divergence_count"].sum() == 1
    runner._validate_reconciliation(
        _empty_reconciliation_arms(), pd.DataFrame({"parity": [True]}),
        classified, changes, counts,
    )


def test_chained_fold_boundary_retains_prior_direct_causal_anchor() -> None:
    execution, ranking = _causal_fixture(later_fold=2)
    classified, _, counts = runner._classify_execution_divergence(execution, ranking)
    later = classified.loc[classified["candidate_key"] == "C"].iloc[0]
    assert later["divergence_classification"] == "downstream_portfolio_state"
    assert later["causal_anchor_fold"] == 1
    assert counts["unexpected"] == 0


def test_causal_classification_and_ranking_identities_are_deterministic() -> None:
    execution, ranking = _causal_fixture()
    first = runner._classify_execution_divergence(execution, ranking)
    second = runner._classify_execution_divergence(execution, ranking)
    pd.testing.assert_frame_equal(first[0], second[0])
    pd.testing.assert_frame_equal(first[1], second[1])
    assert first[2] == second[2]


def test_causal_output_columns_use_explicit_nullable_dtypes_and_assign_all_classes(
    tmp_path: Path,
) -> None:
    execution, ranking = _causal_fixture()
    early = _execution_row(
        "EARLY", fold=1, entry_date="2020-01-01", baseline_ordinal=1,
        variant_ordinal=1, baseline_disposition="executed",
        variant_disposition="insufficient_cash",
    )
    unchanged = _execution_row(
        "SAME", fold=1, entry_date="2020-01-04", baseline_ordinal=1,
        variant_ordinal=1, baseline_disposition="executed",
        variant_disposition="executed",
    )
    execution = pd.concat(
        [pd.DataFrame([early]), execution, pd.DataFrame([unchanged])],
        ignore_index=True,
    )
    ranking = pd.concat([
        pd.DataFrame([_ranking_row(fold=1, entry_date="2020-01-01", changed=0, identity="c" * 64)]),
        ranking,
        pd.DataFrame([_ranking_row(fold=1, entry_date="2020-01-04", changed=0, identity="d" * 64)]),
    ], ignore_index=True).loc[:, runner.RANKING_CHANGE_COLUMNS]

    classified, _, counts = runner._classify_execution_divergence(execution, ranking)
    for column in (
        "divergence_classification", "causal_anchor_entry_date", "causal_anchor_identity",
    ):
        assert isinstance(classified[column].dtype, pd.StringDtype)
    assert str(classified["causal_anchor_fold"].dtype) == "Int64"
    assert set(classified["divergence_classification"].dropna()) == {
        "direct_priority", "downstream_portfolio_state", "unexpected",
    }
    direct = classified.loc[classified["candidate_key"] == "A"].iloc[0]
    downstream = classified.loc[classified["candidate_key"] == "C"].iloc[0]
    unexpected = classified.loc[classified["candidate_key"] == "EARLY"].iloc[0]
    same = classified.loc[classified["candidate_key"] == "SAME"].iloc[0]
    assert direct["causal_anchor_fold"] == 1
    assert direct["causal_anchor_entry_date"] == pd.Timestamp("2020-01-02").isoformat()
    assert direct["causal_anchor_identity"] == "a" * 64
    assert downstream["divergence_classification"] == "downstream_portfolio_state"
    assert unexpected["divergence_classification"] == "unexpected"
    assert pd.isna(unexpected["causal_anchor_fold"])
    assert all(pd.isna(same[column]) for column in (
        "divergence_classification", "causal_anchor_fold",
        "causal_anchor_entry_date", "causal_anchor_identity",
    ))
    assert counts == {"direct_priority": 2, "downstream_portfolio_state": 1, "unexpected": 1}

    output = tmp_path / "executed.csv"
    runner._write_frame(output, classified, runner.EXECUTED_COMPARISON_COLUMNS)
    text = output.read_text(encoding="utf-8")
    assert "<NA>" not in text and ",nan," not in text and ",None," not in text
    with output.open("r", encoding="utf-8", newline="") as stream:
        rows = {row["candidate_key"]: row for row in csv.DictReader(stream)}
    assert rows["SAME"]["divergence_classification"] == ""
    assert rows["SAME"]["causal_anchor_fold"] == ""
    assert rows["SAME"]["causal_anchor_entry_date"] == ""
    assert rows["SAME"]["causal_anchor_identity"] == ""
    assert rows["A"]["causal_anchor_fold"] == "1"
    reread = pd.read_csv(output, keep_default_na=False)
    reread_by_key = reread.set_index("candidate_key")
    assert reread_by_key.at["A", "divergence_classification"] == "direct_priority"
    assert reread_by_key.at["C", "divergence_classification"] == "downstream_portfolio_state"
    assert reread_by_key.at["EARLY", "divergence_classification"] == "unexpected"
    assert reread_by_key.at["SAME", "causal_anchor_identity"] == ""


def test_empty_causal_input_has_exact_nullable_schema() -> None:
    execution = pd.DataFrame(columns=runner.EXECUTED_COMPARISON_COLUMNS)
    ranking = pd.DataFrame(columns=runner.RANKING_CHANGE_COLUMNS)
    classified, changes, counts = runner._classify_execution_divergence(execution, ranking)
    assert tuple(classified.columns) == runner.EXECUTED_COMPARISON_COLUMNS
    assert tuple(changes.columns) == runner.RANKING_CHANGE_COLUMNS
    assert isinstance(classified["divergence_classification"].dtype, pd.StringDtype)
    assert isinstance(classified["causal_anchor_entry_date"].dtype, pd.StringDtype)
    assert isinstance(classified["causal_anchor_identity"].dtype, pd.StringDtype)
    assert str(classified["causal_anchor_fold"].dtype) == "Int64"
    assert counts == {"direct_priority": 0, "downstream_portfolio_state": 0, "unexpected": 0}


def test_causal_classification_is_independent_of_input_presentation_order() -> None:
    execution, ranking = _causal_fixture()
    ordered = runner._classify_execution_divergence(execution, ranking)
    reversed_result = runner._classify_execution_divergence(
        execution.iloc[::-1].reset_index(drop=True),
        ranking.iloc[::-1].reset_index(drop=True),
    )
    columns = (
        "candidate_key", "divergence_classification", "causal_anchor_fold",
        "causal_anchor_entry_date", "causal_anchor_identity",
    )
    pd.testing.assert_frame_equal(
        ordered[0].loc[:, columns].sort_values("candidate_key").reset_index(drop=True),
        reversed_result[0].loc[:, columns].sort_values("candidate_key").reset_index(drop=True),
    )
    assert ordered[2] == reversed_result[2]


def test_slot_crossing_still_fails_before_causal_classification() -> None:
    base = pd.DataFrame([
        {"fold": 1, "entry_date": "2020-01-02", "candidate_key": key,
         "signal_date_group_key": group, "baseline_within_entry_date_ordinal": ordinal,
         "final_simulator_priority_ordinal": ordinal, "execution_disposition": "executed"}
        for key, group, ordinal in (("A", "d1", 1), ("B", "d2", 2))
    ])
    variant = base.copy()
    variant["final_simulator_priority_ordinal"] = [2, 1]
    with pytest.raises(ValueError, match="subgroup slots changed"):
        runner._ranking_changes({"priority": base}, {"priority": variant})


def test_accepted_candidate_parity_failure_remains_strict() -> None:
    execution, ranking = _causal_fixture()
    classified, changes, counts = runner._classify_execution_divergence(execution, ranking)
    with pytest.raises(ValueError, match="accepted-candidate parity"):
        runner._validate_reconciliation(
            _empty_reconciliation_arms(), pd.DataFrame({"parity": [False]}),
            classified, changes, counts,
        )


def test_fresh_process_import_does_not_call_network_or_create_output(tmp_path: Path) -> None:
    missing = tmp_path / "must-not-exist"
    code = (
        "import pathlib,sys; "
        "import research.run_frozen_q70_volume_priority_wfo; "
        f"assert not pathlib.Path({str(missing)!r}).exists(); "
        "assert 'vnstock' not in sys.modules"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=runner.PROJECT_ROOT,
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
