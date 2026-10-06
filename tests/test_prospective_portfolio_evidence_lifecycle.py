from __future__ import annotations

from datetime import date
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from quantlab.evidence import PaperEventCursor
from scripts import run_paper_lifecycle


def _market_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE prices(symbol TEXT,time TEXT)")
        connection.execute("INSERT INTO prices VALUES (?,?)", ("VNINDEX", "2026-09-30"))


def _patch_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    *,
    market_database_path: Path,
    paper_database_path: Path,
    capture,
) -> list[str]:
    calls: list[str] = []
    active_store = SimpleNamespace(
        store_id="q70-frozen",
        strategy_identity="Q70_FROZEN",
        database_path=paper_database_path,
    )
    lifecycle_result = SimpleNamespace(
        valuation_date=date(2026, 9, 30),
        held=(),
        exited=(),
        missing_prices=(),
        missing_states=(),
        rejected_exits=(),
        cash=100.0,
        equity=100.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        open_positions=0,
    )

    class _Manager:
        def __init__(self, **_kwargs):
            calls.append("manager-init")

        def run(self, **kwargs):
            calls.append("manager-run")
            calls.append(f"manager-date:{kwargs['valuation_date']}")
            return lifecycle_result

    monkeypatch.setattr(run_paper_lifecycle, "load_dotenv", lambda: calls.append("dotenv"))
    monkeypatch.setattr(run_paper_lifecycle, "resolve_market_database_path", lambda: market_database_path)
    monkeypatch.setattr(run_paper_lifecycle, "require_market_data_integrity", lambda **_kwargs: calls.append("integrity"))
    monkeypatch.setattr(run_paper_lifecycle, "resolve_active_paper_store", lambda: active_store)

    class _BaselineStore:
        def __init__(self, _path):
            calls.append("baseline-store")

        def get_or_create_prospective_evidence_baseline(self, _date):
            calls.append("pre-cursor")
            return (4, 2)

    monkeypatch.setattr(run_paper_lifecycle, "PaperTradingStore", _BaselineStore)
    monkeypatch.setattr(
        run_paper_lifecycle.PaperSignalExecutor,
        "from_env",
        classmethod(lambda _cls: SimpleNamespace(
            execute_pending_signals=lambda **_kwargs: SimpleNamespace(executions=(), filled_count=0, skipped_count=0, rejected_count=0)
        )),
    )
    monkeypatch.setattr(
        run_paper_lifecycle.TradingPolicy,
        "from_env",
        classmethod(lambda _cls: SimpleNamespace(sell_tax_rate=0.001, trailing_atr_multiplier=3.0)),
    )
    monkeypatch.setattr(run_paper_lifecycle, "PaperBroker", lambda **_kwargs: object())
    monkeypatch.setattr(run_paper_lifecycle, "OrderManager", lambda **_kwargs: object())
    monkeypatch.setattr(run_paper_lifecycle, "RiskGuard", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(run_paper_lifecycle, "RiskLimits", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(run_paper_lifecycle, "ExitEngine", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        run_paper_lifecycle,
        "ExitEngineConfig",
        lambda **kwargs: calls.append(f"trailing:{kwargs['enable_trailing_stop']}") or object(),
    )
    monkeypatch.setattr(run_paper_lifecycle, "PaperLifecycleManager", _Manager)
    # These tests exercise lifecycle/evidence wiring with stub policies; the
    # strategy-contract gate itself is covered in test_strategy_contract_enforcement.
    monkeypatch.setattr(
        run_paper_lifecycle,
        "enforce_strategy_contract",
        lambda strategy: calls.append(f"contract:{strategy}") or (None, None),
    )
    monkeypatch.setattr(
        run_paper_lifecycle,
        "resolve_runtime_configuration",
        lambda: SimpleNamespace(
            strategy_identity="Q70_FROZEN",
            paper_store_id="q70-frozen",
            fingerprint="runtime-config-fingerprint",
        ),
    )
    monkeypatch.setattr(run_paper_lifecycle, "capture_prospective_portfolio_evidence", capture)
    return calls


def test_lifecycle_captures_evidence_after_consistent_lifecycle_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    market, paper = tmp_path / "market.db", tmp_path / "paper.db"
    _market_database(market)
    captured: dict[str, object] = {}

    def capture(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            created=True,
            record=SimpleNamespace(observation_date="2026-09-30", record_identity="abcdef1234567890"),
        )

    calls = _patch_lifecycle(
        monkeypatch,
        market_database_path=market,
        paper_database_path=paper,
        capture=capture,
    )
    run_paper_lifecycle.main()

    assert calls.index("contract:Q70_FROZEN") < calls.index("baseline-store")
    assert calls.index("pre-cursor") < calls.index("manager-run")
    assert "manager-date:2026-09-30" in calls
    assert captured["observation_date"] == date(2026, 9, 30)
    assert captured["paper_database_path"] == paper
    assert captured["market_database_path"] == market
    assert captured["source_store_id"] == "q70-frozen"
    assert captured["strategy_identity"] == "Q70_FROZEN"
    assert captured["runtime_configuration_fingerprint"] == "runtime-config-fingerprint"
    assert captured["baseline_event_cursor"] == PaperEventCursor(4, 2)


def test_evidence_capture_failure_is_visible_after_lifecycle_without_trading_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    market, paper = tmp_path / "market.db", tmp_path / "paper.db"
    _market_database(market)

    def fail_capture(**_kwargs):
        raise RuntimeError("evidence storage unavailable")

    calls = _patch_lifecycle(
        monkeypatch,
        market_database_path=market,
        paper_database_path=paper,
        capture=fail_capture,
    )
    with pytest.raises(RuntimeError, match="evidence storage unavailable"):
        run_paper_lifecycle.main()

    assert calls.count("manager-run") == 1


def test_lifecycle_preserves_literal_true_trailing_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    market, paper = tmp_path / "market.db", tmp_path / "paper.db"
    _market_database(market)
    monkeypatch.setenv("PAPER_V2_DISABLE_TRAILING", "1")

    calls = _patch_lifecycle(
        monkeypatch,
        market_database_path=market,
        paper_database_path=paper,
        capture=lambda **_kwargs: SimpleNamespace(
            created=True,
            record=SimpleNamespace(
                observation_date="2026-09-30",
                record_identity="abcdef1234567890",
            ),
        ),
    )
    run_paper_lifecycle.main()

    assert "trailing:True" in calls
