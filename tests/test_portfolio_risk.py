from __future__ import annotations

import csv
import json
from pathlib import Path
import sqlite3

import numpy as np
import pytest

from quantlab.catalog import build_market_data_snapshot
from quantlab.portfolio import (
    FrozenRiskPortfolio,
    FrozenRiskPosition,
    RiskEvidenceState,
    SECTOR_EVIDENCE_UNAVAILABLE,
    evaluate_portfolio_risk,
)
from research.run_quantlab_portfolio_risk import FILES, run_portfolio_risk


def _database(path: Path, *, future_scale: float = 1.0, missing_b: bool = False) -> tuple[str, ...]:
    dates = tuple(item.date().isoformat() for item in __import__("pandas").bdate_range("2020-01-01", periods=85))
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE prices (symbol TEXT,time TEXT,open REAL,high REAL,low REAL,close REAL,volume INTEGER)")
        for symbol, slope in (("VNINDEX", 0.6), ("AAA", 1.0), ("BBB", -0.25)):
            for index, session in enumerate(dates):
                if missing_b and symbol == "BBB" and index > 20:
                    continue
                close = 100.0 + slope * index + (index % 5) * 0.2
                if index >= 80:
                    close *= future_scale
                connection.execute("INSERT INTO prices VALUES (?,?,?,?,?,?,?)", (symbol, session, close, close, close, close, 1000))
    return dates


def _portfolio(session: str, budget: int = 5, symbols: tuple[str, ...] = ("AAA", "BBB")) -> FrozenRiskPortfolio:
    weight = 1.0 / len(symbols) if symbols else 0.0
    return FrozenRiskPortfolio(
        session, budget, "EQUAL_WEIGHT",
        tuple(FrozenRiskPosition(symbol, weight, f"position-{symbol}") for symbol in symbols),
        1.0 if symbols else 0.0, 0.0 if symbols else 1.0, f"portfolio-{session}-{budget}",
    )


def _evaluate(path: Path, portfolios: tuple[FrozenRiskPortfolio, ...]):
    return evaluate_portfolio_risk(portfolios, build_market_data_snapshot(path), source_identities={"phase6": "frozen"})


def test_concentration_covariance_components_and_benchmark_reconcile(tmp_path: Path) -> None:
    path = tmp_path / "market.db"; dates = _database(path)
    result = _evaluate(path, (_portfolio(dates[69]),))
    item = result.observations[0]
    assert item.covariance_evidence_state is RiskEvidenceState.DEFINED
    assert item.trailing_session_count == 60 and item.covariance_observation_count == 60
    assert item.gross_weight + item.cash_weight == pytest.approx(1.0)
    assert item.herfindahl_concentration == pytest.approx(0.5)
    assert item.effective_n == pytest.approx(2.0)
    assert -1 <= item.mean_pairwise_correlation <= 1
    components = result.component_contributions
    assert sum(row.component_variance_contribution for row in components) == pytest.approx(item.daily_portfolio_variance)
    assert sum(row.component_variance_contribution_share for row in components) == pytest.approx(1.0)
    assert item.benchmark_beta is not None and -1 <= item.benchmark_correlation <= 1


def test_future_prices_cannot_change_formation_diagnostics(tmp_path: Path) -> None:
    first, second = tmp_path / "first.db", tmp_path / "second.db"
    dates = _database(first); _database(second, future_scale=9.0)
    left = _evaluate(first, (_portfolio(dates[70]),)).observations[0]
    right = _evaluate(second, (_portfolio(dates[70]),)).observations[0]
    assert left == right


def test_exact_lookback_ignores_returns_before_sixty_session_window(tmp_path: Path) -> None:
    path = tmp_path / "market.db"; dates = _database(path)
    first = _evaluate(path, (_portfolio(dates[79]),)).observations[0]
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE prices SET close=close*100 WHERE symbol='AAA' AND time < ?", (dates[19],))
    second = _evaluate(path, (_portfolio(dates[79]),)).observations[0]
    assert first.daily_portfolio_variance == pytest.approx(second.daily_portfolio_variance)
    assert first.benchmark_beta == pytest.approx(second.benchmark_beta)


def test_insufficient_missing_and_empty_evidence_are_explicit(tmp_path: Path) -> None:
    path = tmp_path / "market.db"; dates = _database(path, missing_b=True)
    result = _evaluate(path, (_portfolio(dates[20]), _portfolio(dates[70], 10), _portfolio(dates[70], 20, ())))
    states = {item.requested_budget: item for item in result.observations}
    assert states[5].covariance_evidence_state is RiskEvidenceState.INSUFFICIENT_TRAILING_HISTORY
    assert states[10].covariance_evidence_state is RiskEvidenceState.MISSING_CONSTITUENT_PRICES
    assert states[20].covariance_evidence_state is RiskEvidenceState.EMPTY_PORTFOLIO
    assert all(item.daily_portfolio_variance is None for item in states.values())
    assert all(item.sector_evidence_state == SECTOR_EVIDENCE_UNAVAILABLE for item in states.values())


def test_deterministic_order_identity_and_summary_dimensions(tmp_path: Path) -> None:
    path = tmp_path / "market.db"; dates = _database(path)
    portfolios = (_portfolio(dates[70], 20), _portfolio(dates[69], 5), _portfolio(dates[70], 10))
    first = _evaluate(path, portfolios); second = _evaluate(path, tuple(reversed(portfolios)))
    assert first == second
    assert tuple((item.session_date, item.requested_budget) for item in first.observations) == tuple(sorted((item.session_date, item.requested_budget) for item in first.observations))
    assert len(first.summaries) == 15
    assert not hasattr(first.observations[0], "future_return")
    with pytest.raises(Exception):
        first.observations[0].cash_weight = 1.0


def test_zero_benchmark_variance_is_undefined_not_zero(tmp_path: Path) -> None:
    path = tmp_path / "market.db"; dates = _database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE prices SET close=100 WHERE symbol='VNINDEX'")
    item = _evaluate(path, (_portfolio(dates[70]),)).observations[0]
    assert item.benchmark_beta is None and item.benchmark_correlation is None
    assert item.benchmark_undefined_reason == "benchmark_variance_zero"


def _phase6_fixture(root: Path, session: str) -> None:
    root.mkdir()
    daily = [] ; positions = []
    for budget in (5, 10, 20):
        daily.append({"session_date": session, "requested_budget": budget, "weighting_policy": "EQUAL_WEIGHT", "gross_weight": 1.0, "cash_weight": 0.0, "identity": f"portfolio-{budget}"})
        for rank, symbol in enumerate(("AAA", "BBB"), 1):
            positions.append({"session_date": session, "requested_budget": budget, "symbol": symbol, "weight": 0.5, "position_identity": f"p-{budget}-{rank}"})
    for name, rows in (("portfolio_construction_by_date.csv", daily), ("portfolio_positions.csv", positions)):
        with (root / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]), lineterminator="\n"); writer.writeheader(); writer.writerows(rows)
    manifest = {"completed": True, "construction_contract": "quantlab.neutral_portfolio_construction", "result_identity": "phase6-result", "specification_fingerprint": "phase6-spec", "artifacts": {"portfolio_construction_by_date.csv": 3}}
    (root / "portfolio_construction_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_runner_writes_compact_provenance_artifacts_without_mutating_sources(tmp_path: Path) -> None:
    database = tmp_path / "market.db"; dates = _database(database)
    phase6 = tmp_path / "phase6"; _phase6_fixture(phase6, dates[70])
    before = {item.name: item.read_bytes() for item in phase6.iterdir()}
    output = tmp_path / "risk"
    result = run_portfolio_risk(phase6_root=phase6, database_path=database, output_root=output)
    assert {item.name for item in output.iterdir()} == set(FILES)
    manifest = json.loads((output / "portfolio_risk_manifest.json").read_text())
    assert manifest["no_future_outcomes_used"] is True
    assert manifest["no_portfolio_optimization_or_ranking"] is True
    assert manifest["sector_evidence_state"] == SECTOR_EVIDENCE_UNAVAILABLE
    assert manifest["result_identity"] == result.identity
    assert {item.name: item.read_bytes() for item in phase6.iterdir()} == before


def test_runner_rejects_existing_output_before_writing(tmp_path: Path) -> None:
    output = tmp_path / "exists"; output.mkdir()
    with pytest.raises(FileExistsError):
        run_portfolio_risk(phase6_root=tmp_path / "missing", database_path=tmp_path / "missing.db", output_root=output)
