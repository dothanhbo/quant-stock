from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import research.run_frozen_q70_wfo as runner
from core.database_coverage import CoverageUniverseIndex
from backtesting.walk_forward import WalkForwardFold


def _coverage() -> CoverageUniverseIndex:
    return CoverageUniverseIndex(
        start_date="2020-01-01", end_date="2023-01-01",
        effective_start_date="2020-01-01", effective_end_date="2023-01-01",
        minimum_history_sessions=50, maximum_staleness_sessions=5,
        session_dates=("2020-01-01",), candidate_symbols=("AAA",),
        eligible_count_by_session={"2020-01-01": 1},
        _members_by_session={"2020-01-01": frozenset({"AAA"})},
    )


def test_paired_runner_reuses_dependencies_chains_capital_and_writes_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []
    builds = {"coverage": 0, "breadth": 0, "vn100": 0}
    monkeypatch.setattr(runner, "resolve_market_database_path", lambda path: tmp_path / "market.db")
    def current() -> list[str]:
        builds["vn100"] += 1
        return ["AAA", "VNINDEX"]
    monkeypatch.setattr(runner, "get_vn100_symbols", current)
    def build_coverage(*args, **kwargs):
        builds["coverage"] += 1
        return _coverage()
    monkeypatch.setattr(runner, "build_database_coverage_index", build_coverage)
    def build_breadth(*args, **kwargs):
        builds["breadth"] += 1
        return SimpleNamespace(start_date="2020-01-01", end_date="2023-01-01")
    monkeypatch.setattr(runner, "build_historical_breadth_index", build_breadth)
    def fake_evaluator(**kwargs):
        calls.append(kwargs)
        initial = kwargs["parity_config"].initial_cash
        is_test = kwargs["start_date"] >= "2022-01-01"
        final = initial + 1_000.0 if is_test else initial * 2
        cost = 10.0 if is_test else 1.0
        metrics = {
            "final_equity": final,
            "total_return_pct": (final / initial - 1.0) * 100.0,
            "total_trades": 0,
            "win_rate_pct": 0., "profit_factor": 0., "max_drawdown_pct": 0.,
            "gross_profit": 0., "gross_loss": 0., "gross_trading_pnl": -cost,
            "net_trading_pnl": -cost, "total_transaction_cost": cost,
            "total_buy_commission": cost / 3,
            "total_sell_commission": cost / 3, "total_sell_tax": cost / 3,
            "total_evaluation_rows": 2, "base_entry_candidates": 1,
            "q70_accepted_candidates": 1, "q70_rejection_counts": {},
            "q70_rejection_state_counts": {}, "coverage_eligible_count_min": 1,
            "coverage_eligible_count_max": 1, "coverage_eligible_count_mean": 1.,
        }
        return [], metrics, pd.DataFrame({"equity": [final]})
    monkeypatch.setattr(runner, "run_frozen_q70_backtest", fake_evaluator)

    output = tmp_path / "experiment"
    result = runner.run_paired_frozen_q70_wfo(
        start_date="2020-01-01", end_date="2022-12-31", output_root=output,
    )
    assert builds == {"coverage": 1, "breadth": 2, "vn100": 1}
    assert result["manifest"]["q70_threshold"] == .70
    assert len(calls) == len(result["folds"]) * 4  # diagnostic train + chained test, two arms
    test_calls = [call for call in calls if call["start_date"] != "2020-01-01"]
    assert all(call["minimum_history_sessions"] == 50 for call in calls)
    assert all(call["maximum_staleness_sessions"] == 5 for call in calls)
    assert all(call["current_vn100_symbols"] == ("AAA",) for call in calls if call["universe_mode"] == "current_vn100")
    legacy_dates = [
        (call["start_date"], call["end_date"])
        for call in calls if call["universe_mode"] == "current_vn100"
    ]
    coverage_dates = [
        (call["start_date"], call["end_date"])
        for call in calls if call["universe_mode"] == "database_coverage"
    ]
    assert legacy_dates == coverage_dates
    test_starts = {str(fold.test_start.date()) for fold in result["folds"]}
    for mode in ("current_vn100", "database_coverage"):
        capitals = [
            call["parity_config"].initial_cash
            for call in calls
            if call["universe_mode"] == mode and call["start_date"] in test_starts
        ]
        assert capitals == [100_000_000, 100_001_000]
    for arm in result["arms"].values():
        assert arm["summary"]["total_oos_transaction_cost"] == 20.0
        assert set(arm["folds"]["test_total_transaction_cost"]) == {10.0}
        implied_returns = (
            arm["folds"]["test_final_equity"]
            / arm["folds"]["test_initial_equity"]
            - 1.0
        ) * 100.0
        assert arm["folds"]["test_return_pct"].tolist() == pytest.approx(
            implied_returns.tolist()
        )
        assert (
            math.prod(
                1.0 + value / 100.0
                for value in arm["folds"]["test_return_pct"]
            )
            - 1.0
        ) * 100.0 == pytest.approx(
            arm["summary"]["compounded_chained_oos_return_pct"]
        )
        # Training diagnostics deliberately return +100% in this fixture but
        # must not affect the chained OOS summary.
        assert arm["summary"]["final_equity"] == pytest.approx(100_002_000.0)
    for arm in ("legacy_current_vn100_retroactive", "database_coverage_50_history_5_staleness"):
        arm_dir = output / arm
        assert {path.name for path in arm_dir.iterdir()} == {
            "folds.csv", "summary.csv", "trade_level_oos.csv", "policy_fingerprint.json", "assumptions.md", "universe_manifest.json"
        }
    assert (output / "comparison.csv").exists()
    assert (output / "experiment_manifest.json").exists()
    assert (output / "universe_comparison.json").exists()
    assert test_calls  # retain the test/train distinction in the recorded calls


def test_existing_output_protection_and_exact_overwrite_scope(tmp_path: Path) -> None:
    output = tmp_path / "one"; output.mkdir()
    marker = output / "marker.txt"; marker.write_text("old", encoding="utf-8")
    sibling = tmp_path / "sibling.txt"; sibling.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        runner._prepare_output(output, overwrite=False)
    runner._prepare_output(output, overwrite=True)
    assert output.exists() and not marker.exists()
    assert sibling.read_text(encoding="utf-8") == "keep"
    with pytest.raises(ValueError):
        runner._prepare_output(Path.cwd(), overwrite=True)


def test_runner_rejects_missing_required_financial_metrics() -> None:
    with pytest.raises(ValueError, match="missing required metric: total_transaction_cost"):
        runner._require_test_metrics({
            "final_equity": 1.0, "total_return_pct": 0.0, "total_trades": 0,
            "win_rate_pct": 0.0, "profit_factor": 0.0, "max_drawdown_pct": 0.0,
            "gross_profit": 0.0, "gross_loss": 0.0,
            "gross_trading_pnl": 0.0, "net_trading_pnl": 0.0,
            "total_buy_commission": 0.0, "total_sell_commission": 0.0,
            "total_sell_tax": 0.0,
        })


def test_universe_manifests_are_normalized_hashed_and_compare_each_oos_session() -> None:
    fold = WalkForwardFold(
        fold=1,
        train_start=pd.Timestamp("2020-01-01"), train_end=pd.Timestamp("2020-01-31"),
        test_start=pd.Timestamp("2020-02-01"), test_end=pd.Timestamp("2020-02-02"),
    )
    coverage = CoverageUniverseIndex(
        start_date="2020-01-01", end_date="2020-02-02",
        effective_start_date="2020-01-01", effective_end_date="2020-02-02",
        minimum_history_sessions=50, maximum_staleness_sessions=5,
        session_dates=("2020-02-01", "2020-02-02"),
        candidate_symbols=("AAA", "BBB", "CCC"),
        eligible_count_by_session={"2020-02-01": 2, "2020-02-02": 3},
        _members_by_session={
            "2020-02-01": frozenset({"AAA", "BBB"}),
            "2020-02-02": frozenset({"AAA", "BBB", "CCC"}),
        },
    )
    legacy = runner._arm_universe_manifest(
        arm="legacy_current_vn100_retroactive", folds=[fold],
        session_dates=coverage.session_dates,
        legacy_symbols=["bbb", "VNINDEX", "AAA", "aaa"], coverage_index=None,
    )
    coverage_manifest = runner._arm_universe_manifest(
        arm="database_coverage_50_history_5_staleness", folds=[fold],
        session_dates=coverage.session_dates, legacy_symbols=None, coverage_index=coverage,
    )
    assert legacy["resolved_at_run_symbols"] == ["AAA", "BBB"]
    assert legacy["symbol_count"] == 2
    assert coverage_manifest["candidate_symbols"] == ["AAA", "BBB", "CCC"]
    assert coverage_manifest["minimum_history_sessions"] == 50
    assert coverage_manifest["oos_fold_memberships"][0]["sessions"][1]["member_count"] == 3
    assert runner._arm_universe_manifest(
        arm="legacy_current_vn100_retroactive", folds=[fold],
        session_dates=coverage.session_dates,
        legacy_symbols=["AAA", "BBB"], coverage_index=None,
    )["oos_fold_memberships"][0]["fold_membership_hash"] == legacy["oos_fold_memberships"][0]["fold_membership_hash"]
    comparison = runner._universe_comparison(legacy, coverage_manifest)
    record = comparison["oos_fold_comparisons"][0]
    assert not record["exact_membership_equality_every_oos_session"]
    assert record["symbols_only_in_coverage_union"] == ["CCC"]
    matching = runner._arm_universe_manifest(
        arm="legacy_current_vn100_retroactive", folds=[fold],
        session_dates=coverage.session_dates,
        legacy_symbols=["AAA", "BBB", "CCC"], coverage_index=None,
    )
    assert matching["symbol_list_hash"] != legacy["symbol_list_hash"]
    static_coverage = CoverageUniverseIndex(
        start_date="2020-01-01", end_date="2020-02-02",
        effective_start_date="2020-01-01", effective_end_date="2020-02-02",
        minimum_history_sessions=50, maximum_staleness_sessions=5,
        session_dates=("2020-02-01", "2020-02-02"), candidate_symbols=("AAA", "BBB"),
        eligible_count_by_session={"2020-02-01": 2, "2020-02-02": 2},
        _members_by_session={
            "2020-02-01": frozenset({"AAA", "BBB"}),
            "2020-02-02": frozenset({"AAA", "BBB"}),
        },
    )
    static_manifest = runner._arm_universe_manifest(
        arm="database_coverage_50_history_5_staleness", folds=[fold],
        session_dates=static_coverage.session_dates, legacy_symbols=None,
        coverage_index=static_coverage,
    )
    assert runner._universe_comparison(legacy, static_manifest)["oos_fold_comparisons"][0]["exact_membership_equality_every_oos_session"]


def test_persisted_legacy_manifest_bypasses_live_provider_and_records_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    symbols = ["AAA", "BBB"]
    source = tmp_path / "source.json"
    source.write_text(
        __import__("json").dumps({
            "resolved_at_run_symbols": [" bbb ", "VNINDEX", "AAA", "aaa"],
            "symbol_count": 2,
            "symbol_list_hash": runner._canonical_hash(symbols),
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        runner, "get_vn100_symbols",
        lambda: (_ for _ in ()).throw(AssertionError("live provider must be bypassed")),
    )
    monkeypatch.setattr(runner, "resolve_market_database_path", lambda path: tmp_path / "market.db")
    monkeypatch.setattr(runner, "build_database_coverage_index", lambda *args, **kwargs: _coverage())
    monkeypatch.setattr(
        runner, "build_historical_breadth_index",
        lambda *args, **kwargs: SimpleNamespace(start_date="2020-01-01", end_date="2023-01-01"),
    )
    seen: list[tuple[str, ...] | None] = []
    def fake_arm(**kwargs):
        seen.append(kwargs["current_symbols"])
        return {
            "folds": pd.DataFrame({"fold": [1], "test_total_transaction_cost": [0.0], "evaluation_rows": [0], "base_entry_candidates": [0], "q70_accepted_candidates": [0]}),
            "trades": pd.DataFrame(),
            "summary": {"arm": kwargs["arm"], "total_oos_transaction_cost": 0.0},
        }
    monkeypatch.setattr(runner, "_run_arm", fake_arm)
    result = runner.run_paired_frozen_q70_wfo(
        start_date="2020-01-01", end_date="2022-12-31",
        output_root=tmp_path / "out", legacy_universe_manifest=source,
    )
    assert seen[0] == ("AAA", "BBB")
    provenance = result["universe_manifests"]["legacy_current_vn100_retroactive"]
    assert provenance["source"] == "persisted_manifest"
    assert provenance["source_manifest_path"] == str(source.resolve())
    assert provenance["validated_source_hash"] == runner._canonical_hash(symbols)
    assert result["manifest"]["legacy_universe_provenance"] == {
        "source": "persisted_manifest",
        "source_manifest_path": str(source.resolve()),
        "validated_source_hash": runner._canonical_hash(symbols),
    }


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        {},
        {"resolved_at_run_symbols": []},
        {"resolved_at_run_symbols": ["AAA"], "symbol_count": 2},
        {"resolved_at_run_symbols": ["AAA"], "symbol_list_hash": "bad"},
    ],
)
def test_invalid_persisted_legacy_manifest_fails_before_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: object,
) -> None:
    source = tmp_path / "invalid.json"
    source.write_text(payload if isinstance(payload, str) else __import__("json").dumps(payload), encoding="utf-8")
    output = tmp_path / "must_not_exist"
    monkeypatch.setattr(
        runner, "build_database_coverage_index",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must fail before index build")),
    )
    with pytest.raises((ValueError, FileNotFoundError)):
        runner.run_paired_frozen_q70_wfo(
            start_date="2020-01-01", end_date="2022-12-31",
            output_root=output, legacy_universe_manifest=source,
        )
    assert not output.exists()


def test_missing_persisted_legacy_manifest_fails_before_output(tmp_path: Path) -> None:
    output = tmp_path / "must_not_exist"
    with pytest.raises(FileNotFoundError):
        runner.run_paired_frozen_q70_wfo(
            start_date="2020-01-01", end_date="2022-12-31",
            output_root=output, legacy_universe_manifest=tmp_path / "missing.json",
        )
    assert not output.exists()
