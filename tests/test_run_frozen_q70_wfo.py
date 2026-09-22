from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import research.run_frozen_q70_wfo as runner
from core.database_coverage import CoverageUniverseIndex


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
        final = kwargs["parity_config"].initial_cash + 1_000.0
        metrics = {
            "final_equity": final, "total_return_pct": .001, "total_trades": 0,
            "win_rate_pct": 0., "profit_factor": 0., "max_drawdown_pct": 0.,
            "total_transaction_cost": 0., "total_buy_commission": 0.,
            "total_sell_commission": 0., "total_sell_tax": 0.,
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
    assert all(call["current_vn100_symbols"] == ["AAA"] for call in calls if call["universe_mode"] == "current_vn100")
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
    for arm in ("legacy_current_vn100_retroactive", "database_coverage_50_history_5_staleness"):
        arm_dir = output / arm
        assert {path.name for path in arm_dir.iterdir()} == {
            "folds.csv", "summary.csv", "trade_level_oos.csv", "policy_fingerprint.json", "assumptions.md"
        }
    assert (output / "comparison.csv").exists()
    assert (output / "experiment_manifest.json").exists()
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
