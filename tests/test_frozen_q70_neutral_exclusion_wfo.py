from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import research.run_frozen_q70_neutral_exclusion_wfo as runner
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


def _metrics(initial: float, final: float, *, policy_rejected: int) -> dict:
    return {
        "final_equity": final,
        "total_return_pct": (final / initial - 1) * 100,
        "total_trades": 0, "win_rate_pct": 0.0, "profit_factor": 0.0,
        "max_drawdown_pct": 0.0, "gross_profit": 0.0, "gross_loss": 0.0,
        "gross_trading_pnl": 0.0, "net_trading_pnl": 0.0,
        "total_buy_commission": 0.0, "total_sell_commission": 0.0,
        "total_sell_tax": 0.0, "total_transaction_cost": 0.0,
        "total_evaluation_rows": 2, "base_entry_candidates": 1,
        "q70_accepted_candidates": 1,
        "state_policy_rejected_candidates": policy_rejected,
        "state_policy_rejection_state_counts": ({"NEUTRAL": policy_rejected} if policy_rejected else {}),
        "q70_rejection_counts": {}, "q70_rejection_state_counts": {},
        "coverage_eligible_count_min": 1, "coverage_eligible_count_max": 1,
        "coverage_eligible_count_mean": 1.0,
    }


def test_runner_shares_database_dependencies_and_writes_post_hoc_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    builds = {"coverage": 0, "breadth": 0}
    monkeypatch.setattr(runner, "resolve_market_database_path", lambda _: tmp_path / "market.db")
    monkeypatch.setattr(runner, "build_database_coverage_index", lambda *a, **k: (builds.__setitem__("coverage", builds["coverage"] + 1) or _coverage()))
    monkeypatch.setattr(runner, "build_historical_breadth_index", lambda *a, **k: (builds.__setitem__("breadth", builds["breadth"] + 1) or SimpleNamespace(start_date="2020-01-01", end_date="2023-01-01")))

    def fake_evaluator(**kwargs):
        calls.append(kwargs)
        initial = kwargs["parity_config"].initial_cash
        is_test = kwargs.get("collect_decision_ledger", False)
        rejected = 1 if kwargs["allowed_entry_states"] is not None else 0
        final = initial + (500.0 - rejected * 100.0 if is_test else 0.0)
        metrics = _metrics(initial, final, policy_rejected=rejected)
        if is_test:
            disposition = "state_policy_rejected" if rejected else "executed"
            metrics["decision_ledger"] = (SimpleNamespace(
                phase="test", symbol="AAA", signal_date=pd.Timestamp(kwargs["start_date"]),
                candidate_key=f"AAA|{kwargs['start_date']}T00:00:00", score=1.,
                relative_strength_20d=1., adx=1., percentile_score=1.,
                percentile_relative_strength_20d=1., percentile_adx=1., quality_score=1.,
                quality_threshold=.7, market_state="NEUTRAL", breadth_ema50_pct=50.,
                breadth_ema50_change_10d=0., gate_accepted=True, gate_reason="Q0.70_PASS",
                state_policy_eligible=not rejected,
                state_policy_reason=("state_not_allowed" if rejected else None),
                execution_disposition=disposition,
                executed_trade_key=(None if rejected else f"AAA|{kwargs['start_date']}T00:00:00"),
            ),)
        return [], metrics, pd.DataFrame({"equity": [final]})
    monkeypatch.setattr(runner, "run_frozen_q70_backtest", fake_evaluator)

    result = runner.run_frozen_q70_neutral_exclusion_wfo(
        start_date="2020-01-01", end_date="2022-12-31", output_root=tmp_path / "output"
    )
    assert builds == {"coverage": 1, "breadth": 1}
    assert not hasattr(runner, "get_vn100_symbols")
    assert len(calls) == len(result["folds"]) * 4
    assert {id(call["coverage_index"]) for call in calls} == {id(next(iter(calls))["coverage_index"])}
    assert {id(call["breadth_index"]) for call in calls} == {id(next(iter(calls))["breadth_index"])}
    baseline = result["arms"]["baseline_frozen_q70"]
    variant = result["arms"]["exclude_neutral"]
    assert baseline["summary"]["total_state_policy_rejected_candidates"] == 0
    assert variant["summary"]["total_state_policy_rejected_candidates"] == len(result["folds"])
    assert all(value is None for value in [call["allowed_entry_states"] for call in calls if call["allowed_entry_states"] is None])
    assert all(call["allowed_entry_states"] == runner._ALLOWED_NON_NEUTRAL_STATES for call in calls if call["allowed_entry_states"] is not None)
    root = result["output_root"]
    for arm in ("baseline_frozen_q70", "exclude_neutral"):
        assert {p.name for p in (root / arm).iterdir()} == {
            "folds.csv", "summary.csv", "trade_level_oos.csv", "candidate_decision_oos.csv", "policy_fingerprint.json", "assumptions.md"
        }
    assert {p.name for p in root.iterdir()} >= {"comparison.csv", "experiment_manifest.json"}
    assert "post-hoc" in (root / "exclude_neutral" / "assumptions.md").read_text(encoding="utf-8")
    assert result["manifest"]["only_difference"].startswith("exclude_neutral")


def test_output_safety_is_delegated_to_exact_scoped_guard(tmp_path: Path) -> None:
    target = tmp_path / "target"; target.mkdir()
    with pytest.raises(FileExistsError):
        runner._prepare_output(target, overwrite=False)
