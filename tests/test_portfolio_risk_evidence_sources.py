from __future__ import annotations

import csv
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys

import pandas as pd
import pytest

from quantlab.evaluation import (
    PersistedEvidenceSourceClassification,
    evaluate_portfolio_risk_evidence,
    load_frozen_q70_cost_evidence,
    load_phase6_portfolio_evidence,
)


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _market_database(path: Path, *, sessions: int = 70) -> list[str]:
    dates = [item.date().isoformat() for item in pd.bdate_range("2020-01-01", periods=sessions)]
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE prices (symbol TEXT,time TEXT,open REAL,high REAL,low REAL,close REAL,volume INTEGER)")
        for symbol, slope in (("VNINDEX", 0.4), ("AAA", 1.0), ("BBB", -0.2)):
            for index, date in enumerate(dates):
                close = 100.0 + slope * index + (index % 5) * 0.1
                connection.execute("INSERT INTO prices VALUES (?,?,?,?,?,?,?)", (symbol, date, close, close, close, close, 1_000))
    return dates


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def _phase6(root: Path, dates: list[str], *, first_row: int = 55) -> None:
    root.mkdir()
    daily_rows = [
        {
            "session_date": dates[first_row], "requested_budget": 5, "weighting_policy": "EQUAL_WEIGHT",
            "selected_count": 2, "gross_weight": 1.0, "cash_weight": 0.0, "identity": "portfolio-a",
        },
        {
            "session_date": dates[first_row + 1], "requested_budget": 5, "weighting_policy": "EQUAL_WEIGHT",
            "selected_count": 1, "gross_weight": 0.4, "cash_weight": 0.6, "identity": "portfolio-b",
        },
    ]
    position_rows = [
        {"session_date": dates[first_row], "requested_budget": 5, "symbol": "AAA", "weight": 0.6},
        {"session_date": dates[first_row], "requested_budget": 5, "symbol": "BBB", "weight": 0.4},
        {"session_date": dates[first_row + 1], "requested_budget": 5, "symbol": "AAA", "weight": 0.4},
    ]
    _write_csv(root / "portfolio_construction_by_date.csv", daily_rows)
    _write_csv(root / "portfolio_positions.csv", position_rows)
    manifest = {
        "completed": True, "construction_contract": "quantlab.neutral_portfolio_construction",
        "candidate_source": "ADX_ONLY", "weighting_policies": ["EQUAL_WEIGHT"], "budgets": [5],
        "period": {"start_date": dates[0], "end_date": dates[-1]}, "result_identity": "phase6-result",
        "specification_fingerprint": "phase6-spec", "artifacts": {"portfolio_construction_by_date.csv": len(daily_rows)},
    }
    (root / "portfolio_construction_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _frozen_q70(root: Path) -> None:
    root.mkdir(); arm = root / "database_coverage_50_history_5_staleness"; arm.mkdir()
    manifest = {
        "arms": [arm.name],
        "folds": [{"test_start": "2020-01-01", "test_end": "2020-06-30"}],
    }
    (root / "experiment_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _write_csv(arm / "summary.csv", [{"policy_fingerprint": "Q70_FROZEN/hybrid_trend_donchian/ATR2x5/fixed"}])
    _write_csv(arm / "trade_level_oos.csv", [{
        "fold": 1, "symbol": "AAA", "entry_date": "2020-01-03", "exit_date": "2020-01-10",
        "entry_price": 100.0, "exit_price": 110.0, "quantity": 10, "net_pnl": 80.0,
        "total_transaction_cost": 20.0,
    }])


def test_phase6_source_loads_read_only_and_preserves_identity_dates_and_positions(tmp_path: Path) -> None:
    database = tmp_path / "market.db"; dates = _market_database(database)
    root = tmp_path / "phase6"; _phase6(root, dates)
    before = _hash(database)
    source = load_phase6_portfolio_evidence(phase6_root=root, database_path=database, requested_budget=5)
    after = _hash(database)
    assert before == after
    assert source.classification is PersistedEvidenceSourceClassification.CANONICAL
    assert source.strategy_identity == "ADX_ONLY/EQUAL_WEIGHT"
    assert (source.start_date, source.end_date) == (dates[0], dates[-1])
    arguments = source.evaluator_arguments()
    assert len(arguments["observations"]) == 2
    assert arguments["observations"][0]["weights"] == {"AAA": 0.6, "BBB": 0.4}
    assert arguments["observations"][1]["weights"] == {"AAA": 0.4}
    assert all(item["daily_returns"].index.max() <= pd.Timestamp(item["observation_date"]) for item in arguments["observations"])


def test_missing_phase6_source_or_market_database_never_creates_a_replacement(tmp_path: Path) -> None:
    missing_root, missing_database = tmp_path / "missing-source", tmp_path / "missing-market.db"
    with pytest.raises(FileNotFoundError):
        load_phase6_portfolio_evidence(phase6_root=missing_root, database_path=missing_database, requested_budget=5)
    assert not missing_root.exists() and not missing_database.exists()


def test_phase6_adapter_output_is_accepted_and_keeps_unknown_sector_explicit(tmp_path: Path) -> None:
    database = tmp_path / "market.db"; dates = _market_database(database)
    root = tmp_path / "phase6"; _phase6(root, dates)
    source = load_phase6_portfolio_evidence(phase6_root=root, database_path=database, requested_budget=5)
    result = evaluate_portfolio_risk_evidence(**source.evaluator_arguments(), minimum_observations=20)
    assert result.observation_count == 2
    assert result.concentration_summaries["unknown_sector_share_of_gross"].mean == pytest.approx(1.0)
    assert result.beta_summaries["weighted_portfolio_beta"].defined_count == 2
    assert result.provenance["phase6_result_identity"] == "phase6-result"
    assert result.provenance["historical_market_regime"]["logic_authority"] == "historical_market_regime@v1"
    liquidity = next(item for item in result.policy_implications if item.risk_dimension == "liquidity")
    assert liquidity.production_control_justified == "NOT EVALUABLE"


def test_insufficient_market_history_stays_unavailable_without_provider_fallback(tmp_path: Path) -> None:
    database = tmp_path / "market.db"; dates = _market_database(database, sessions=25)
    root = tmp_path / "phase6"; _phase6(root, dates, first_row=10)
    source = load_phase6_portfolio_evidence(phase6_root=root, database_path=database, requested_budget=5)
    result = evaluate_portfolio_risk_evidence(**source.evaluator_arguments(), lookback_sessions=20, minimum_observations=20)
    assert result.correlation_summaries["average_pairwise_correlation"].defined_count == 0
    assert result.beta_summaries["weighted_portfolio_beta"].defined_count == 0
    assert "vnstock" not in sys.modules


def test_future_market_rows_do_not_affect_historical_adapter_windows(tmp_path: Path) -> None:
    database = tmp_path / "market.db"; dates = _market_database(database, sessions=75)
    root = tmp_path / "phase6"; _phase6(root, dates, first_row=55)
    first = load_phase6_portfolio_evidence(phase6_root=root, database_path=database, requested_budget=5)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE prices SET close=close*100 WHERE time > ?", (dates[56],))
    second = load_phase6_portfolio_evidence(phase6_root=root, database_path=database, requested_budget=5)
    left = first.evaluator_arguments()["observations"][0]["daily_returns"]
    right = second.evaluator_arguments()["observations"][0]["daily_returns"]
    pd.testing.assert_frame_equal(left, right)


def test_frozen_q70_cost_only_source_preserves_completed_trade_cost_evidence(tmp_path: Path) -> None:
    root = tmp_path / "frozen"; _frozen_q70(root)
    source = load_frozen_q70_cost_evidence(frozen_q70_root=root)
    assert source.classification is PersistedEvidenceSourceClassification.DERIVED
    result = evaluate_portfolio_risk_evidence(**source.evaluator_arguments())
    assert result.evaluable_observation_count == 0
    assert result.cost_sensitivity[0].mean_gross_pnl == pytest.approx(100.0)
    assert result.cost_sensitivity[-1].mean_net_pnl < result.cost_sensitivity[0].mean_net_pnl
    assert result.concentration_summaries["gross_exposure"].defined_count == 0
    assert "cost-only" in source.limitations[0]


def test_adapter_result_defensively_copies_observation_frames(tmp_path: Path) -> None:
    database = tmp_path / "market.db"; dates = _market_database(database)
    root = tmp_path / "phase6"; _phase6(root, dates)
    source = load_phase6_portfolio_evidence(phase6_root=root, database_path=database, requested_budget=5)
    first, second = source.evaluator_arguments(), source.evaluator_arguments()
    first["observations"][0]["daily_returns"].iloc[:, :] = 999.0
    assert not (second["observations"][0]["daily_returns"] == 999.0).all().all()


def test_phase6_market_regime_projection_is_causal_and_binds_feature_provenance(
    tmp_path: Path,
) -> None:
    database = tmp_path / "market.db"; dates = _market_database(database, sessions=240)
    root = tmp_path / "phase6"; _phase6(root, dates, first_row=210)

    first = load_phase6_portfolio_evidence(
        phase6_root=root,
        database_path=database,
        requested_budget=5,
    )
    first_observations = first.evaluator_arguments()["observations"]
    projection = first.provenance["historical_market_regime"]

    assert [item["regime"] for item in first_observations] == ["BULL", "BULL"]
    assert projection["status"] == "EVALUABLE"
    assert projection["label_kind"] == "DERIVED_CURRENT_CANONICAL_MARKET_REGIME"
    assert projection["feature_request"] == {
        "name": "historical_market_regime",
        "version": "v1",
        "parameters": {"benchmark_symbol": "VNINDEX"},
    }
    assert projection["causal_data_cutoff"] == dates[211]
    assert projection["feature_identity"] and projection["computation_identity"]
    assert any("breadth-dependent PaperV2 market state" in item for item in first.limitations)

    # The snapshot identity can change because it fingerprints the complete
    # database, but a fixed historical cutoff must keep earlier labels fixed.
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE prices SET close=close * 100 WHERE time > ?", (dates[211],))
    second = load_phase6_portfolio_evidence(
        phase6_root=root,
        database_path=database,
        requested_budget=5,
    )
    assert [item["regime"] for item in second.evaluator_arguments()["observations"]] == ["BULL", "BULL"]


def test_phase6_regime_projection_never_promotes_missing_breadth_state(
    tmp_path: Path,
) -> None:
    database = tmp_path / "market.db"; dates = _market_database(database, sessions=240)
    root = tmp_path / "phase6"; _phase6(root, dates, first_row=210)

    source = load_phase6_portfolio_evidence(
        phase6_root=root,
        database_path=database,
        requested_budget=5,
    )
    arguments = source.evaluator_arguments()

    assert all("paper_v2_state" not in item for item in arguments["observations"])
    assert source.provenance["historical_market_regime"]["runtime_parity_authority"] == (
        "strategy.market_regime.prepare_market_regime_history"
    )
    assert any("not a provenance-identical Phase 6 artifact field" in item for item in source.provenance["warnings"])
