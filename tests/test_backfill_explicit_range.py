"""V1 P1-OPS-1 acquisition: explicit-range, single-symbol backfill for reviewed-rebase preparation.

Offline and deterministic: a fake KBS provider and temporary SQLite databases
only (no provider call, no live ``market.db``, no Daily, no Telegram). The fake
provider honours the requested window exactly like the KBS adapter (it trims to
``start..end``) and can simulate a provider-side history limit.
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from core import market_admission
from core.market_admission import AdmissionResult
from core.market_observation_log import open_observation_log
from core.market_provenance_gate import check_market_provenance
from core.market_rebase import RebaseRefused, preview_rebase
from tests.test_market_rebase import (  # noqa: F401 - isolated_paper_routing is an autouse fixture
    EX_INDEX,
    LAST_STORED,
    NOW,
    PAPER,
    SESS,
    TODAY,
    adjusted,
    admit,
    bar,
    do_rebase,
    file_sha,
    isolated_paper_routing,
    log_fingerprint,
    make_market,
    prices,
)

COLUMNS = ["time", "open", "high", "low", "close", "volume"]


class FakeKBS:
    """Stands in for ``vnstock.api.quote.Quote(source='KBS')``."""

    series: dict[str, list[tuple]] = {}
    earliest_available: dict[str, str] = {}  # simulated provider history limit
    calls: list[tuple[str, str, str, str, str]] = []

    def __init__(self, symbol: str, source: str) -> None:
        self.symbol, self.source = symbol, source

    def history(self, start: str, end: str, interval: str) -> pd.DataFrame:
        FakeKBS.calls.append((self.symbol, self.source, start, end, interval))
        floor = FakeKBS.earliest_available.get(self.symbol, "0000-00-00")
        rows = [r for r in FakeKBS.series[self.symbol] if start <= r[0] <= end and r[0] >= floor]
        return pd.DataFrame(rows, columns=COLUMNS)


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls(2026, 10, 5, 16, 30) if tz is None else NOW.astimezone(tz)


@pytest.fixture()
def wired(tmp_path: Path, monkeypatch):
    import core.database as database
    import scripts.backfill_market_data as backfill
    import scripts.update_data as updater
    from sqlalchemy import create_engine

    market = make_market(tmp_path)
    monkeypatch.setattr(database, "DATABASE_PATH", market)
    monkeypatch.setattr(database, "engine", create_engine(f"sqlite:///{market.as_posix()}"))
    monkeypatch.setattr(market_admission, "utc_now", lambda: NOW)
    for module in (backfill, updater):
        monkeypatch.setattr(module, "Quote", FakeKBS)
        monkeypatch.setattr(module, "datetime", FixedDatetime)
        monkeypatch.setattr(module.time, "sleep", lambda *_: None)
    monkeypatch.setattr(backfill, "REQUEST_DELAY_SECONDS", 0)
    monkeypatch.setattr(updater, "REQUEST_DELAY_SECONDS", 0)
    monkeypatch.setattr(updater, "FAILED_LOG_PATH", str(tmp_path / "failed.txt"))
    # the provider now serves the back-adjusted basis (corporate action at EX_INDEX)
    FakeKBS.series = {s: [adjusted(s, i) if s == "AAA" else bar(s, i) for i in range(TODAY + 1)]
                      for s in ("AAA", "BBB", "VNINDEX")}
    FakeKBS.earliest_available, FakeKBS.calls = {}, []
    return market, backfill, updater


def run(backfill, capsys, *argv: str) -> tuple[int | None, dict]:
    code = backfill.main(list(argv))
    out = capsys.readouterr().out.strip()
    return code, (json.loads(out) if out.startswith("{") else {})


def explicit(backfill, capsys, start: str = SESS[0], end: str = SESS[TODAY], symbol: str = "AAA"):
    return run(backfill, capsys, "--symbols", symbol, "--start", start, "--end", end)


# --------------------------------------------------------------------- 1, 2
def test_01_explicit_single_symbol_window_is_honoured_exactly(wired, capsys) -> None:
    market, backfill, _ = wired
    code, report = explicit(backfill, capsys)

    assert FakeKBS.calls == [("AAA", "KBS", SESS[0], SESS[TODAY], "1D")]
    assert code == 0 and report["requested_window"] == [SESS[0], SESS[TODAY]]
    observation = [o for o in open_observation_log(market).list_observations("AAA")
                   if o["source_mode"] == "BACKFILL_EXPLICIT_RANGE"][0]
    assert (observation["request_start"], observation["request_end"]) == (SESS[0], SESS[TODAY])
    assert (observation["first_session"], observation["last_session"]) == (SESS[0], SESS[TODAY])
    assert report["returned"] == {"earliest_session": SESS[0], "latest_session": SESS[TODAY], "sessions": TODAY + 1}


def test_02_explicit_request_identity_differs_from_rolling_requests(wired, capsys) -> None:
    market, backfill, _ = wired
    _, report = explicit(backfill, capsys)
    # an ordinary BACKFILL of the very same window and content is a different observation
    backfill.backfill_symbol("AAA", start_date=datetime.fromisoformat(SESS[0]),
                             end_date=datetime.fromisoformat(SESS[TODAY]))
    log = open_observation_log(market)
    modes = {o["observation_id"]: o["source_mode"] for o in log.list_observations("AAA")}
    explicit_id = report["admission"]["observation_id"]
    assert modes[explicit_id] == "BACKFILL_EXPLICIT_RANGE"
    assert sorted(set(modes.values())) == ["BACKFILL", "BACKFILL_EXPLICIT_RANGE", "UPDATE"]
    assert len(modes) == 3  # rolling UPDATE, ordinary BACKFILL, explicit range: three identities


# --------------------------------------------------------------------- 3-5
def test_03_full_back_adjusted_history_is_persisted_and_blocked_without_overwrite(wired, capsys) -> None:
    market, backfill, _ = wired
    before = file_sha(market)
    code, report = explicit(backfill, capsys)

    assert code == 0 and report["status"] == "OBSERVATION_RECORDED_BLOCKED"
    assert report["coverage"] == "FULL_STORED_HISTORY_COVERED"
    assert report["admission"]["result"] == "BLOCKED_REVISION_CHANGED" and report["admission"]["blocked"]
    assert file_sha(market) == before  # no historical overwrite, no append either
    log = open_observation_log(market, readonly=True)
    (block,) = log.unresolved_blocks(["AAA"])
    assert block["observation_id"] == report["admission"]["observation_id"]
    assert block["affected_sessions"] == list(SESS[:EX_INDEX])
    assert len(log.get_observation(block["observation_id"])["rows"]) == TODAY + 1


def test_04_05_insufficient_provider_history_fails_closed_without_any_write(wired, capsys) -> None:
    market, backfill, _ = wired
    FakeKBS.earliest_available["AAA"] = SESS[3]  # the provider cannot reach the first stored session
    before_market, before_log = file_sha(market), log_fingerprint(market)

    code, report = explicit(backfill, capsys)

    assert code == 2 and report["status"] == "PROVIDER_HISTORY_COVERAGE_INSUFFICIENT"
    assert report["requested_window"][0] == SESS[0]  # the requested start was not moved
    assert report["stored_history"]["first_session"] == SESS[0]
    assert report["returned"]["earliest_session"] == SESS[3]
    assert report["missing_range"] == [SESS[0], (datetime.fromisoformat(SESS[3]) - timedelta(days=1)).date().isoformat()]
    assert report["admission"] == {}
    assert file_sha(market) == before_market and log_fingerprint(market) == before_log  # no observation, no block
    # an empty provider answer is insufficient as well
    FakeKBS.earliest_available["AAA"] = "9999-12-31"
    code, report = explicit(backfill, capsys)
    assert code == 2 and report["status"] == "PROVIDER_HISTORY_COVERAGE_INSUFFICIENT"
    assert file_sha(market) == before_market and log_fingerprint(market) == before_log


# --------------------------------------------------------------------- 6, 7
@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (["--symbols", "AAA", "--start", "2026-9-1"], "INVALID_DATE"),
        (["--symbols", "AAA", "--start", "01-09-2026"], "INVALID_DATE"),
        (["--symbols", "AAA", "--start", "2026-09-01", "--end", "2026-02-30"], "INVALID_DATE"),
        (["--symbols", "AAA", "--start", "2026-10-02", "--end", "2026-09-01"], "START_AFTER_END"),
        (["--symbols", "AAA", "--start", "2026-09-01", "--end", "2026-10-06"], "END_IN_FUTURE"),
        (["--symbols", "AAA", "--start", SESS[1]], "EXPLICIT_START_AFTER_FIRST_STORED_SESSION"),
        (["--symbols", "AAA", "--start", "2018-08-07"], "EXPLICIT_START_BEFORE_FIRST_STORED_SESSION"),
        (["--symbols", "AAA", "--start", SESS[0], "--end", SESS[20]], "EXPLICIT_END_BEFORE_LAST_STORED_SESSION"),
        (["--symbols", "NEWCO", "--start", SESS[0]], "SYMBOL_HAS_NO_STORED_HISTORY"),
        (["--start", SESS[0]], "EXPLICIT_START_REQUIRES_EXPLICIT_SYMBOL"),
        (["--symbols", "--start", SESS[0]], "EXPLICIT_START_REQUIRES_EXPLICIT_SYMBOL"),
        (["--symbols", "AAA", "BBB", "--start", SESS[0]], "EXPLICIT_START_REQUIRES_EXACTLY_ONE_SYMBOL"),
        (["--symbols", "AAA", "--end", SESS[TODAY]], "EXPLICIT_RANGE_OPTION_WITHOUT_START"),
        (["--dry-run"], "EXPLICIT_RANGE_OPTION_WITHOUT_START"),
    ],
)
def test_06_07_invalid_or_broad_requests_are_refused_before_any_fetch(wired, capsys, argv, code) -> None:
    market, backfill, _ = wired
    before_market, before_log = file_sha(market), log_fingerprint(market)
    exit_code, report = run(backfill, capsys, *argv)
    assert exit_code == 2 and report["status"] == "REFUSED" and report["code"] == code
    assert FakeKBS.calls == []  # never an all-universe (or any) provider call
    assert file_sha(market) == before_market and log_fingerprint(market) == before_log


def test_06b_dry_run_prints_the_plan_without_a_provider_call(wired, capsys) -> None:
    market, backfill, _ = wired
    before_market, before_log = file_sha(market), log_fingerprint(market)
    code, report = run(backfill, capsys, "--symbols", "aaa", "--start", SESS[0], "--dry-run")
    assert code == 0 and report["provider_called"] is False and report["status"] == "PLANNED"
    assert report["requested_window"] == [SESS[0], "2026-10-05"]
    assert report["stored_history"] == {"first_session": SESS[0], "last_session": SESS[LAST_STORED],
                                        "sessions": LAST_STORED + 1}
    assert FakeKBS.calls == [] and file_sha(market) == before_market and log_fingerprint(market) == before_log
    # the read-only way to learn the exact start: any start prints the stored range
    code, report = run(backfill, capsys, "--symbols", "AAA", "--start", "2000-01-01", "--dry-run")
    assert code == 2 and report["code"] == "EXPLICIT_START_BEFORE_FIRST_STORED_SESSION"
    assert report["stored_history"]["first_session"] == SESS[0] and SESS[0] in report["message"]
    assert FakeKBS.calls == [] and file_sha(market) == before_market and log_fingerprint(market) == before_log


# --------------------------------------------------------------------- 8, 9
def test_08_ordinary_backfill_behaviour_is_unchanged(wired, capsys, monkeypatch) -> None:
    market, backfill, _ = wired
    monkeypatch.setattr(backfill, "get_all_symbols", lambda: (_ for _ in ()).throw(AssertionError("no universe")))
    assert backfill.main(["--symbols", "AAA"]) is None
    rolling_start = (FixedDatetime(2026, 10, 5, 16, 30) - timedelta(days=365 * 8)).strftime("%Y-%m-%d")
    assert FakeKBS.calls == [("AAA", "KBS", rolling_start, "2026-10-05", "1D")]
    modes = {o["source_mode"] for o in open_observation_log(market).list_observations("AAA")}
    assert "BACKFILL" in modes and "BACKFILL_EXPLICIT_RANGE" not in modes
    # the whole-universe default still requires a real universe and never takes --start
    calls: list[list[str]] = []
    monkeypatch.setattr(backfill, "get_all_symbols", lambda: [f"S{i:03d}" for i in range(120)])
    monkeypatch.setattr(backfill, "backfill_all_symbols", lambda symbols: calls.append(list(symbols)))
    assert backfill.main([]) is None and len(calls[0]) == 120


def test_09_ordinary_daily_update_window_is_unchanged(wired) -> None:
    market, backfill, updater = wired
    FakeKBS.series["BBB"] = [bar("BBB", i) for i in range(TODAY + 1)]
    status = updater.update_symbol("BBB")
    assert status is updater.UpdateStatus.SUCCESS
    expected_start = (datetime.fromisoformat(SESS[LAST_STORED]) - timedelta(days=updater.REFRESH_OVERLAP_DAYS))
    assert FakeKBS.calls == [("BBB", "KBS", expected_start.strftime("%Y-%m-%d"), "2026-10-05", "1D")]
    modes = {o["source_mode"] for o in open_observation_log(market).list_observations("BBB")}
    assert modes == {"UPDATE"}
    for entrypoint in ("scripts/update_data.py", "scripts/run_daily.py"):
        source = (Path(__file__).resolve().parents[1] / entrypoint).read_text(encoding="utf-8")
        assert "BACKFILL_EXPLICIT_RANGE" not in source and "backfill_explicit_range" not in source


# --------------------------------------------------------------------- 10, 11
def test_10_full_history_observation_makes_the_reviewed_rebase_eligible(wired, capsys) -> None:
    market, backfill, _ = wired
    _, report = explicit(backfill, capsys)
    log = open_observation_log(market, readonly=True)
    (block,) = log.unresolved_blocks(["AAA"])
    plan = preview_rebase(market, symbol="AAA", block_id=block["block_id"], observation_id=block["observation_id"],
                          paper_environ={}, paper_root=PAPER["root"])
    assert plan.summary()["history_coverage"] == "FULL_STORED_HISTORY"
    assert plan.sessions == tuple(SESS[:EX_INDEX])

    assert do_rebase(market, block).applied
    # next ordinary Daily update on the same provider basis: no re-block, the new session appends
    nxt = admit(market, "AAA", [adjusted("AAA", i) for i in range(25, TODAY + 1)], SESS[25], SESS[TODAY])
    assert nxt.result is AdmissionResult.ADMITTED_APPEND
    assert check_market_provenance(market, ["AAA", "BBB", "VNINDEX"]).state == "PASS"
    # and an identical explicit re-acquisition is now an accepted no-op, not a new block
    code, again = explicit(backfill, capsys)
    assert code == 0 and not again["admission"]["blocked"]
    assert open_observation_log(market, readonly=True).unresolved_blocks() == []


def test_11_short_historical_observation_is_still_incomplete_coverage(wired) -> None:
    market, backfill, _ = wired
    ok = backfill.backfill_symbol("AAA", start_date=datetime.fromisoformat(SESS[10]),
                                  end_date=datetime.fromisoformat(SESS[TODAY]))
    assert ok is False  # ordinary backfill: blocked, nothing written
    (block,) = open_observation_log(market, readonly=True).unresolved_blocks(["AAA"])
    with pytest.raises(RebaseRefused, match="INCOMPLETE_REBASE_HISTORY_COVERAGE"):
        preview_rebase(market, symbol="AAA", block_id=block["block_id"], observation_id=block["observation_id"],
                       paper_environ={}, paper_root=PAPER["root"])


# --------------------------------------------------------------------- 12, 13
def test_12_identical_explicit_retry_is_idempotent(wired, capsys) -> None:
    market, backfill, _ = wired
    _, first = explicit(backfill, capsys)
    snapshot = file_sha(market)
    _, second = explicit(backfill, capsys)

    assert second["admission"]["observation_id"] == first["admission"]["observation_id"]
    assert second["status"] == "OBSERVATION_RECORDED_BLOCKED" and file_sha(market) == snapshot
    log = open_observation_log(market, readonly=True)
    assert len(log.unresolved_blocks(["AAA"])) == 1  # one block, a second fetch receipt only
    explicit_obs = [o for o in log.list_observations("AAA") if o["source_mode"] == "BACKFILL_EXPLICIT_RANGE"]
    assert len(explicit_obs) == 1


def test_12b_identical_unrevised_history_is_admitted_idempotently(wired, capsys) -> None:
    market, backfill, _ = wired
    FakeKBS.series["BBB"] = [bar("BBB", i) for i in range(TODAY + 1)]
    code, first = explicit(backfill, capsys, symbol="BBB")
    assert code == 0 and first["status"] == "OBSERVATION_RECORDED_ADMITTED"
    assert first["admission"]["appended_sessions"] == [SESS[TODAY]]  # new sessions: ordinary semantics
    snapshot = file_sha(market)
    code, second = explicit(backfill, capsys, symbol="BBB")
    assert code == 0 and second["admission"]["already_applied"] and file_sha(market) == snapshot


def test_13_explicit_range_has_no_writer_bypass(wired, capsys, monkeypatch) -> None:
    market, backfill, _ = wired
    source = inspect.getsource(backfill)
    for token in ("INSERT INTO prices", "UPDATE prices", "DELETE FROM prices", "REPLACE INTO", "executemany"):
        assert token not in source
    assert "mode=ro" in inspect.getsource(backfill.stored_session_range)
    handed: list[str] = []
    real = backfill.save_price_data

    def spy(frame, *, context, symbol=None):
        handed.append(context.source_mode)
        return real(frame, context=context, symbol=symbol)

    monkeypatch.setattr(backfill, "save_price_data", spy)
    explicit(backfill, capsys)
    assert handed == ["BACKFILL_EXPLICIT_RANGE"]  # the only write path is the admission guard
    assert [r[2][:10] for r in prices(market, "AAA")][:1] == [SESS[0]]
