"""R3: market-data version / session provenance bound into forward and paper evidence.

Offline and deterministic: temporary SQLite databases only, no provider calls, no
live ``market.db``, no Telegram.  The observation log is produced through the real
R1/R2 admission path so the bindings are validated against genuine log state.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from core.evidence_market_binding import (
    EXCLUDED_STATES,
    LOG_BOUND,
    LOG_MISSING,
    QUALIFIED_STATES,
    EvidenceQualifier,
    MarketBindingError,
    MarketBindingSession,
    ProvenanceQuarantineError,
    Qualification,
    StaleDatasetVersionError,
    binding_identity,
    ensure_binding_schema,
    insert_binding,
    record_review,
)
from core.market_admission import AdmissionContext, AdmissionResult, admit_price_batch
from core.market_observation_log import open_observation_log
from quantlab.forward import (
    ForwardValidationLedger,
    build_forward_formation,
    build_matured_outcomes,
    create_activation,
    evaluate_formation_maturities,
    load_protocol_spec,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "research/forward_validation/protocol_v1.json"
PHASE8_ROOT = ROOT / "research_results/quantlab_portfolio_research_synthesis_2018-08-07_2026-09-17"

NOW = datetime(2026, 10, 5, 9, 30, tzinfo=timezone.utc)
SCHEMA = """
CREATE TABLE prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT, time TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER
);
CREATE UNIQUE INDEX ux_prices_symbol_time ON prices(symbol, time);
"""

#: weekday sessions only (the admission guard rejects weekend sessions).
SESS = (
    "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25",
    "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05",
)
SYMBOLS = ("VNINDEX", "AAA", "BBB")


class SimulatedCrash(BaseException):
    """Stands in for a process kill: bypasses ``except Exception`` handlers."""


def bar(symbol: str, sessions: tuple[str, ...], index: int) -> tuple:
    rank = SYMBOLS.index(symbol) if symbol in SYMBOLS else 3
    close = round(100.0 + rank * 10.0 + index * (1.0 + rank / 10.0), 4)
    return (sessions[index], round(close - 0.5, 4), round(close + 1.0, 4), round(close - 1.0, 4), close, 1000 + index)


def _context(start: str, end: str) -> AdmissionContext:
    return AdmissionContext(
        source="KBS",
        endpoint="vnstock.api.quote.Quote(source='KBS').history",
        source_mode="UPDATE",
        package_name="vnstock",
        package_version="4.0.2",
        request_start=start,
        request_end=end,
    )


def _frame(symbol: str, rows: list[tuple]) -> pd.DataFrame:
    data = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    data["symbol"] = symbol
    return data


def make_market(
    directory: Path,
    *,
    sessions: tuple[str, ...] = SESS,
    symbols: tuple[str, ...] = SYMBOLS,
    baseline_count: int = 4,
    register: bool = True,
    name: str = "market.db",
) -> Path:
    path = directory / name
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.executemany(
        "INSERT INTO prices(symbol,time,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)",
        [(s, *bar(s, sessions, i)) for s in symbols for i in range(baseline_count)],
    )
    connection.commit()
    connection.close()
    if register:
        open_observation_log(path).ensure_initial_baseline(path, now=NOW)
    return path


def append_through(
    market: Path,
    last_index: int,
    *,
    sessions: tuple[str, ...] = SESS,
    symbols: tuple[str, ...] = SYMBOLS,
    first_index: int = 0,
) -> None:
    """Admit every symbol's bars up to ``last_index`` through the real admission path."""
    for symbol in symbols:
        rows = [bar(symbol, sessions, i) for i in range(first_index, last_index + 1)]
        outcome = admit_price_batch(
            _frame(symbol, rows),
            symbol=symbol,
            context=_context(sessions[first_index], sessions[last_index]),
            market_db_path=market,
            now=NOW,
        )
        assert outcome.result in {
            AdmissionResult.ADMITTED_APPEND,
            AdmissionResult.ADMITTED_NO_NEW_SESSIONS,
        }, outcome


def revise(
    market: Path,
    symbol: str,
    revised_index: int,
    last_index: int,
    *,
    sessions: tuple[str, ...] = SESS,
    first_index: int = 0,
):
    """Offer a provider revision of one stored session: the admission guard must block it."""
    rows = [bar(symbol, sessions, i) for i in range(first_index, last_index + 1)]
    day, o, h, l, c, v = rows[revised_index - first_index]
    rows[revised_index - first_index] = (day, *(round(x * 0.8, 4) for x in (o, h, l, c)), v)
    outcome = admit_price_batch(
        _frame(symbol, rows),
        symbol=symbol,
        context=_context(sessions[first_index], sessions[last_index]),
        market_db_path=market,
        now=NOW,
    )
    assert outcome.result is AdmissionResult.BLOCKED_REVISION_CHANGED
    return outcome


def version_id(market: Path) -> str:
    return str(open_observation_log(market, readonly=True).dataset_version_identity()["dataset_version_id"])


def prices_snapshot(market: Path) -> list[tuple]:
    with sqlite3.connect(market) as connection:
        return connection.execute("SELECT * FROM prices ORDER BY id").fetchall()


# ------------------------------------------------------------------ forward fixtures
@pytest.fixture()
def protocol():
    return load_protocol_spec(SPEC_PATH)


@pytest.fixture()
def activation(protocol):
    return create_activation(
        protocol,
        activated_at_utc="2026-09-27T12:00:00Z",
        confirmed_latest_completed_session="2026-09-17",
    )


def make_formation(protocol, activation, session_index: int = 4, symbols=("AAA", "BBB")):
    session = SESS[session_index]
    return build_forward_formation(
        protocol,
        activation,
        formation_session=session,
        recorded_at_utc="2026-09-27T12:01:00Z",
        completed_market_sessions=SESS[: session_index + 1],
        ordered_symbols=symbols,
        formation_closes={s: bar(s, SESS, session_index)[4] for s in symbols},
        benchmark_formation_close=bar("VNINDEX", SESS, session_index)[4],
        source_snapshot_identity="snapshot-r3",
        selection_policy_identity=protocol.selection_policy_identity,
        eligible_universe_identity="universe-r3",
        selection_identity="selection-r3",
    )


def formation_binding(bind_session: MarketBindingSession, formation, protocol):
    session = formation.formation_session
    return bind_session.bind(
        "FORMATION",
        formation.formation_identity,
        (
            *(("FORMATION_CLOSE", p.symbol, session) for p in formation.positions),
            ("BENCHMARK_FORMATION_CLOSE", protocol.benchmark, session),
        ),
        context={"protocol_id": protocol.protocol_id},
    )


def mature_outcomes(protocol, formation, last_index: int):
    maturity = evaluate_formation_maturities(
        protocol, formation, SESS[: last_index + 1], recorded_at_utc="t"
    )[0]
    assert maturity.target_session is not None
    target = SESS.index(maturity.target_session)
    outcomes = build_matured_outcomes(
        formation,
        maturity,
        target_stock_closes={p.symbol: bar(p.symbol, SESS, target)[4] for p in formation.positions},
        target_benchmark_close=bar("VNINDEX", SESS, target)[4],
        recorded_at_utc="t",
    )
    return maturity, outcomes


def outcome_bindings(bind_session: MarketBindingSession, formation, maturity, outcomes, protocol):
    return {
        item.outcome_identity: bind_session.bind(
            "OUTCOME",
            item.outcome_identity,
            (
                ("STOCK_TARGET_CLOSE", item.symbol, maturity.target_session, formation.formation_session),
                ("BENCHMARK_TARGET_CLOSE", protocol.benchmark, maturity.target_session, formation.formation_session),
            ),
            context={"formation_identity": formation.formation_identity},
        )
        for item in outcomes
    }


@pytest.fixture()
def forward_env(tmp_path: Path, protocol, activation):
    """Formation on the first observed session (9/25), recorded at dataset version V1."""
    market = make_market(tmp_path)
    append_through(market, 4)
    v1 = version_id(market)
    ledger = ForwardValidationLedger(tmp_path / "forward.db")
    ledger.activate(activation)
    formation = make_formation(protocol, activation)
    binding = formation_binding(MarketBindingSession(market), formation, protocol)
    assert ledger.record_formation(formation, protocol.tracked_horizons, market_binding=binding) is True
    return {
        "market": market, "ledger": ledger, "formation": formation, "binding": binding,
        "v1": v1, "protocol": protocol, "activation": activation,
    }


def record_matured_outcomes(env, *, last_index: int = 9):
    """Append sessions through ``last_index`` and record + bind the h5 outcomes."""
    append_through(env["market"], last_index)
    maturity, outcomes = mature_outcomes(env["protocol"], env["formation"], last_index)
    env["ledger"].record_maturities((maturity,))
    session = MarketBindingSession(env["market"])
    bindings = outcome_bindings(session, env["formation"], maturity, outcomes, env["protocol"])
    assert env["ledger"].record_outcomes(outcomes, market_bindings=bindings) == len(outcomes)
    return maturity, outcomes, bindings, session


def forward_qualification(env, outcome, qualifier: EvidenceQualifier | None = None):
    qualifier = qualifier or EvidenceQualifier(env["market"])
    stored = env["ledger"].market_bindings()
    return qualifier.qualify(
        (
            (stored.get(("FORMATION", env["formation"].formation_identity)), {outcome.symbol, "VNINDEX"}),
            (stored.get(("OUTCOME", outcome.outcome_identity)), None),
        )
    )


# ===================================================================== 1
def test_new_forward_snapshot_binds_the_exact_dataset_version(forward_env) -> None:
    env = forward_env
    stored = env["ledger"].market_bindings()[("FORMATION", env["formation"].formation_identity)]

    assert stored["log_state"] == LOG_BOUND
    assert stored["dataset"]["dataset_version_id"] == env["v1"]
    assert stored["dataset"]["baseline_id"] == open_observation_log(env["market"]).get_baseline()["baseline_id"]
    assert stored["binding_identity"] == binding_identity(stored)
    roles = {(d["role"], d["symbol"], d["session"]) for d in stored["dependencies"]}
    assert roles == {
        ("FORMATION_CLOSE", "AAA", "2026-09-25"),
        ("FORMATION_CLOSE", "BBB", "2026-09-25"),
        ("BENCHMARK_FORMATION_CLOSE", "VNINDEX", "2026-09-25"),
    }
    log = open_observation_log(env["market"], readonly=True)
    for dep in stored["dependencies"]:
        live = log.session_provenance(dep["symbol"], dep["session"])
        assert dep["origin"] == "OBSERVATION" == live["origin"]
        assert dep["observation_id"] == live["observation_id"]
        assert dep["version_id"] == live["version_id"] and dep["version_id"]
    verdict = EvidenceQualifier(env["market"]).qualify(((stored, None),))
    assert verdict.state == Qualification.PROVENANCE_VERIFIED.value and verdict.qualified


# ===================================================================== 2
def test_a_later_dataset_version_does_not_rebind_an_old_snapshot(forward_env) -> None:
    env = forward_env
    key = ("FORMATION", env["formation"].formation_identity)
    before = env["ledger"].market_bindings()[key]

    append_through(env["market"], 7)
    v2 = version_id(env["market"])
    assert v2 != env["v1"]
    later = formation_binding(MarketBindingSession(env["market"]), env["formation"], env["protocol"])
    assert later["dataset"]["dataset_version_id"] == v2

    # the formation exists: no rewrite, no rebind, and a direct conflicting insert is refused
    assert env["ledger"].record_formation(env["formation"], env["protocol"].tracked_horizons, market_binding=later) is False
    assert env["ledger"].market_bindings()[key] == before
    with sqlite3.connect(env["ledger"].path) as connection:
        with pytest.raises(MarketBindingError, match="conflicting immutable"):
            insert_binding(connection, "forward", later)
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute("UPDATE forward_market_bindings SET dataset_version_id='x'")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute("DELETE FROM forward_market_bindings")
    # later, unrelated sessions did not disturb the original session provenance
    assert EvidenceQualifier(env["market"]).qualify(((before, None),)).state == Qualification.PROVENANCE_VERIFIED.value


# ===================================================================== 3
def test_forward_outcome_binds_the_exact_target_session_and_version(forward_env) -> None:
    env = forward_env
    maturity, outcomes, bindings, _session = record_matured_outcomes(env)
    v3 = version_id(env["market"])
    assert maturity.target_session == "2026-10-02" and v3 != env["v1"]

    stored = env["ledger"].market_bindings()
    for item in outcomes:
        binding = stored[("OUTCOME", item.outcome_identity)]
        assert binding["dataset"]["dataset_version_id"] == v3
        by_role = {d["role"]: d for d in binding["dependencies"]}
        assert by_role["STOCK_TARGET_CLOSE"]["session"] == "2026-10-02"
        assert by_role["STOCK_TARGET_CLOSE"]["window_start"] == env["formation"].formation_session
        assert by_role["STOCK_TARGET_CLOSE"]["origin"] == "OBSERVATION"
        assert by_role["BENCHMARK_TARGET_CLOSE"]["symbol"] == "VNINDEX"
        assert forward_qualification(env, item).state == Qualification.PROVENANCE_VERIFIED.value
    # the formation binding stayed at the version it was decided on
    assert stored[("FORMATION", env["formation"].formation_identity)]["dataset"]["dataset_version_id"] == env["v1"]


# ===================================================================== 4
def test_unresolved_target_revision_quarantines_the_outcome_and_preserves_it(forward_env) -> None:
    env = forward_env
    _maturity, outcomes, _bindings, _session = record_matured_outcomes(env)
    rows_before = env["ledger"].outcomes(env["protocol"].protocol_id)

    revise(env["market"], "AAA", 8, 9)  # 2026-10-01 lies inside [formation, target]

    verdicts = {item.symbol: forward_qualification(env, item) for item in outcomes}
    assert verdicts["AAA"].state == Qualification.QUARANTINED_UNRESOLVED_REVISION.value
    assert verdicts["AAA"].excluded and not verdicts["AAA"].qualified
    assert any(reason.startswith("UNRESOLVED_BLOCK:") for reason in verdicts["AAA"].reasons)
    # evidence is never deleted or rewritten
    assert env["ledger"].outcomes(env["protocol"].protocol_id) == rows_before
    # the unrelated symbol's outcome is untouched by AAA's block
    assert verdicts["BBB"].state == Qualification.PROVENANCE_VERIFIED.value


# ===================================================================== 5
def test_legacy_forward_evidence_remains_unbound_and_is_never_verified(tmp_path: Path, protocol, activation) -> None:
    market = make_market(tmp_path)
    append_through(market, 9)
    ledger = ForwardValidationLedger(tmp_path / "forward.db")
    ledger.activate(activation)
    formation = make_formation(protocol, activation)
    ledger.record_formation(formation, protocol.tracked_horizons)  # pre-R3 style: no binding
    maturity, outcomes = mature_outcomes(protocol, formation, 9)
    ledger.record_maturities((maturity,))
    ledger.record_outcomes(outcomes)

    assert ledger.market_bindings() == {}
    verdict = EvidenceQualifier(market).qualify(((None, None), (None, None)))
    assert verdict.state == Qualification.LEGACY_UNBOUND.value
    assert verdict.legacy_label == "LEGACY_UNVERIFIED"
    assert verdict.descriptive_only and not verdict.qualified and not verdict.excluded
    # even a fully clean current log cannot upgrade legacy evidence
    assert verdict.state not in QUALIFIED_STATES and verdict.state not in EXCLUDED_STATES


def test_old_database_gets_an_additive_idempotent_schema_and_legacy_rows_are_untouched(
    tmp_path: Path, protocol, activation
) -> None:
    ledger = ForwardValidationLedger(tmp_path / "forward.db")
    ledger.activate(activation)
    formation = make_formation(protocol, activation)
    ledger.record_formation(formation, protocol.tracked_horizons)
    with sqlite3.connect(ledger.path) as connection:  # simulate a database created before R3
        connection.execute("DROP TABLE forward_market_bindings")
        connection.execute("DROP TABLE forward_qualification_events")
        before = {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall()
            for table in ("forward_formations", "forward_positions", "forward_maturities")
        }
    old = ForwardValidationLedger(ledger.path)
    assert old.market_bindings() == {} and old.qualification_events() == {}
    old.initialize()
    old.initialize()  # idempotent
    with sqlite3.connect(ledger.path) as connection:
        for table, rows in before.items():
            assert connection.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall() == rows
        assert connection.execute("SELECT COUNT(*) FROM forward_market_bindings").fetchone()[0] == 0
    assert old.market_bindings() == {}


# ===================================================================== 8 / 9
def test_unrelated_symbol_and_unrelated_session_are_not_quarantined(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    append_through(market, 9)
    session = MarketBindingSession(market)
    aaa = session.bind("PAPER_OBSERVATION", "aaa", (("MARK_CLOSE", "AAA", "2026-10-02", "2026-09-25"),))
    bbb_old = session.bind("PAPER_OBSERVATION", "bbb-old", (("MARK_CLOSE", "BBB", "2026-09-28", "2026-09-28"),))
    bbb_window = session.bind("PAPER_OBSERVATION", "bbb-window", (("MARK_CLOSE", "BBB", "2026-10-02", "2026-09-25"),))

    block = revise(market, "BBB", 8, 9)  # BBB 2026-10-01 only
    qualifier = EvidenceQualifier(market)
    assert [b["symbol"] for b in qualifier.blocks] == ["BBB"]
    assert block.blocked

    assert qualifier.qualify(((aaa, None),)).state == Qualification.PROVENANCE_VERIFIED.value
    assert qualifier.qualify(((bbb_old, None),)).state == Qualification.PROVENANCE_VERIFIED.value
    # a block on BBB does not touch evidence that only depends on AAA, even when the
    # evidence also lists BBB but is restricted to its own symbol
    assert qualifier.qualify(((bbb_window, {"AAA"}),)).state == Qualification.PROVENANCE_VERIFIED.value


def test_affected_symbol_and_session_quarantine_the_relevant_evidence(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    append_through(market, 9)
    session = MarketBindingSession(market)
    covering = session.bind("PAPER_OBSERVATION", "cover", (("MARK_CLOSE", "BBB", "2026-10-02", "2026-09-25"),))
    exact = session.bind("PAPER_EXIT", "exact", (("EXIT_PRICE", "BBB", "2026-10-01", "2026-09-25"),))
    revise(market, "BBB", 8, 9)

    qualifier = EvidenceQualifier(market)
    for binding in (covering, exact):
        verdict = qualifier.qualify(((binding, None),))
        assert verdict.state == Qualification.QUARANTINED_UNRESOLVED_REVISION.value
        assert verdict.excluded
    # mixed evidence is as weak as its weakest leg
    clean = session.bind("PAPER_ENTRY", "clean", (("ENTRY_OPEN", "AAA", "2026-10-02"),))
    assert qualifier.qualify(((clean, None), (covering, {"BBB"}))).state == (
        Qualification.QUARANTINED_UNRESOLVED_REVISION.value
    )


def test_review_is_append_only_basis_bound_and_never_lifts_a_new_reason(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    append_through(market, 9)
    binding = MarketBindingSession(market).bind(
        "PAPER_OBSERVATION", "r", (("MARK_CLOSE", "BBB", "2026-10-02", "2026-09-25"),)
    )
    revise(market, "BBB", 8, 9)
    qualifier = EvidenceQualifier(market)
    current = qualifier.qualify(((binding, None),))
    connection = sqlite3.connect(":memory:")
    ensure_binding_schema(connection, "forward")
    with pytest.raises(ValueError):
        record_review(connection, "forward", subject_kind="K", subject_ref="r", decision="ACCEPT",
                      reviewer="", reason="x", current=current)
    assert record_review(connection, "forward", subject_kind="K", subject_ref="r", decision="ACCEPT",
                         reviewer="rev", reason="checked against source", current=current)
    events = [
        dict(zip(("state", "basis", "source"), row))
        for row in connection.execute("SELECT state,basis_identity,source FROM forward_qualification_events")
    ]
    accepted = qualifier.qualify(((binding, None),), reviews=[events[-1] | {"state": events[-1]["state"], "basis_identity": events[-1]["basis"]}])
    assert accepted.state == Qualification.REVIEWED_ACCEPTED.value and accepted.qualified
    # a second, different block changes the basis: the earlier acceptance no longer applies
    revise(market, "BBB", 7, 9)
    again = EvidenceQualifier(market).qualify(
        ((binding, None),), reviews=[{"state": events[-1]["state"], "basis_identity": events[-1]["basis"]}]
    )
    assert again.state == Qualification.QUARANTINED_UNRESOLVED_REVISION.value
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        connection.execute("DELETE FROM forward_qualification_events")
    # a rejection is sticky
    rejected = EvidenceQualifier(market).qualify(
        ((binding, None),), reviews=[{"state": Qualification.REVIEWED_REJECTED.value, "basis_identity": "x"}]
    )
    assert rejected.state == Qualification.REVIEWED_REJECTED.value and rejected.excluded


# ===================================================================== 10
def test_retry_is_idempotent_for_formation_and_outcomes(forward_env) -> None:
    env = forward_env
    key = ("FORMATION", env["formation"].formation_identity)
    first = env["ledger"].market_bindings()[key]
    # identical retry with a fresh binding of the same dataset: no write, original bound_at kept
    retry = formation_binding(MarketBindingSession(env["market"]), env["formation"], env["protocol"])
    assert retry["binding_identity"] == first["binding_identity"]
    assert env["ledger"].record_formation(env["formation"], env["protocol"].tracked_horizons, market_binding=retry) is False
    assert env["ledger"].market_bindings()[key] == first

    _maturity, outcomes, bindings, _session = record_matured_outcomes(env)
    snapshot = env["ledger"].market_bindings()
    assert env["ledger"].record_outcomes(outcomes, market_bindings=bindings) == 0
    assert env["ledger"].market_bindings() == snapshot
    assert len(snapshot) == 1 + len(outcomes)


# ===================================================================== 11
def test_stale_or_latest_version_substitution_is_rejected(forward_env) -> None:
    env = forward_env
    pinned = MarketBindingSession(env["market"])
    assert pinned.dataset_version_id == env["v1"]
    append_through(env["market"], 6)  # the dataset advances after the decision point

    with pytest.raises(StaleDatasetVersionError):
        pinned.bind("OUTCOME", "x", (("STOCK_TARGET_CLOSE", "AAA", "2026-09-29", "2026-09-25"),))
    with pytest.raises(StaleDatasetVersionError):
        MarketBindingSession(env["market"], expected_dataset_version_id=env["v1"])
    # the caller may rebind only by making a new decision at the new version
    fresh = MarketBindingSession(env["market"], expected_dataset_version_id=version_id(env["market"]))
    assert fresh.dataset_version_id != env["v1"]
    assert not any(k[0] == "OUTCOME" for k in env["ledger"].market_bindings())
    with pytest.raises(MarketBindingError):
        pinned.bind("", "x", ())
    with pytest.raises(MarketBindingError):
        fresh.bind("OUTCOME", "x", (("STOCK_TARGET_CLOSE", "AAA", "2026-09-29", "2026-09-30"),))


# ===================================================================== 12
@pytest.mark.parametrize("crash", (RuntimeError("provenance write failed"), SimulatedCrash()))
def test_crash_between_evidence_and_provenance_leaves_neither_and_retry_binds_truthfully(
    tmp_path: Path, protocol, activation, monkeypatch: pytest.MonkeyPatch, crash
) -> None:
    market = make_market(tmp_path)
    append_through(market, 4)
    ledger = ForwardValidationLedger(tmp_path / "forward.db")
    ledger.activate(activation)
    formation = make_formation(protocol, activation)
    binding = formation_binding(MarketBindingSession(market), formation, protocol)

    import quantlab.forward.ledger as ledger_module

    real = ledger_module.insert_binding

    def failing(*args, **kwargs):  # evidence rows are already inserted in this transaction
        raise crash

    monkeypatch.setattr(ledger_module, "insert_binding", failing)
    with pytest.raises(type(crash)):
        ledger.record_formation(formation, protocol.tracked_horizons, market_binding=binding)
    monkeypatch.setattr(ledger_module, "insert_binding", real)

    assert ledger.formations(protocol.protocol_id) == ()  # no evidence without provenance
    assert ledger.market_bindings() == {}
    assert ledger.latest_maturities(protocol.protocol_id) == ()

    # the dataset moves on; the retry is a *new decision* and binds what it actually consumes
    append_through(market, 5)
    retry = formation_binding(MarketBindingSession(market), formation, protocol)
    assert ledger.record_formation(formation, protocol.tracked_horizons, market_binding=retry) is True
    stored = ledger.market_bindings()[("FORMATION", formation.formation_identity)]
    assert stored["dataset"]["dataset_version_id"] == version_id(market)


def test_crash_after_provenance_bound_but_before_evidence_commit_rolls_both_back(
    tmp_path: Path, protocol, activation, monkeypatch: pytest.MonkeyPatch
) -> None:
    market = make_market(tmp_path)
    append_through(market, 4)
    ledger = ForwardValidationLedger(tmp_path / "forward.db")
    ledger.activate(activation)
    formation = make_formation(protocol, activation)
    binding = formation_binding(MarketBindingSession(market), formation, protocol)

    import quantlab.forward.ledger as ledger_module

    real = ledger_module.insert_binding

    def bound_then_crash(connection, prefix, value):
        real(connection, prefix, value)  # the binding row is written ...
        raise SimulatedCrash()  # ... and the process dies before the commit

    monkeypatch.setattr(ledger_module, "insert_binding", bound_then_crash)
    with pytest.raises(SimulatedCrash):
        ledger.record_formation(formation, protocol.tracked_horizons, market_binding=binding)
    monkeypatch.setattr(ledger_module, "insert_binding", real)

    assert ledger.market_bindings() == {} and ledger.formations(protocol.protocol_id) == ()
    assert ledger.record_formation(formation, protocol.tracked_horizons, market_binding=binding) is True
    assert len(ledger.market_bindings()) == 1


def test_outcome_crash_binds_nothing_and_a_late_update_binds_its_own_version(
    forward_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = forward_env
    append_through(env["market"], 9)
    maturity, outcomes = mature_outcomes(env["protocol"], env["formation"], 9)
    env["ledger"].record_maturities((maturity,))
    first_session = MarketBindingSession(env["market"])
    bindings = outcome_bindings(first_session, env["formation"], maturity, outcomes, env["protocol"])

    import quantlab.forward.ledger as ledger_module

    real = ledger_module.insert_binding
    calls = {"n": 0}

    def crash_on_second(connection, prefix, value):
        calls["n"] += 1
        if calls["n"] == 2:
            raise SimulatedCrash()
        return real(connection, prefix, value)

    monkeypatch.setattr(ledger_module, "insert_binding", crash_on_second)
    with pytest.raises(SimulatedCrash):
        env["ledger"].record_outcomes(outcomes, market_bindings=bindings)
    monkeypatch.setattr(ledger_module, "insert_binding", real)
    assert env["ledger"].outcomes(env["protocol"].protocol_id) == ()
    assert [k for k in env["ledger"].market_bindings() if k[0] == "OUTCOME"] == []

    # an outcome update arriving after a later dataset version exists: the stale session is refused
    append_through(env["market"], 10)
    with pytest.raises(StaleDatasetVersionError):
        outcome_bindings(first_session, env["formation"], maturity, outcomes, env["protocol"])
    late = MarketBindingSession(env["market"])
    late_bindings = outcome_bindings(late, env["formation"], maturity, outcomes, env["protocol"])
    assert env["ledger"].record_outcomes(outcomes, market_bindings=late_bindings) == len(outcomes)
    for item in outcomes:
        stored = env["ledger"].market_bindings()[("OUTCOME", item.outcome_identity)]
        assert stored["dataset"]["dataset_version_id"] == late.dataset_version_id != env["v1"]
        assert forward_qualification(env, item).state == Qualification.PROVENANCE_VERIFIED.value


def test_a_block_appearing_between_decision_and_evidence_write_is_caught_at_qualification(forward_env) -> None:
    env = forward_env
    append_through(env["market"], 9)
    maturity, outcomes = mature_outcomes(env["protocol"], env["formation"], 9)
    env["ledger"].record_maturities((maturity,))
    session = MarketBindingSession(env["market"])  # decision point
    bindings = outcome_bindings(session, env["formation"], maturity, outcomes, env["protocol"])
    version_at_decision = session.dataset_version_id

    revise(env["market"], "AAA", 9, 9)  # a revision of the target session is blocked meanwhile
    assert version_id(env["market"]) == version_at_decision  # blocks never advance the version
    verdict = EvidenceQualifier(env["market"]).qualify(((bindings[outcomes[0].outcome_identity], None),))
    assert verdict.state == Qualification.QUARANTINED_UNRESOLVED_REVISION.value
    # the binding itself is unchanged: it records what was actually consumed
    assert bindings[outcomes[0].outcome_identity]["dataset"]["dataset_version_id"] == version_at_decision


def test_no_observation_log_environment_records_missing_provenance_not_a_false_binding(tmp_path: Path) -> None:
    market = make_market(tmp_path, register=False)
    session = MarketBindingSession(market)
    binding = session.bind("FORMATION", "f", (("FORMATION_CLOSE", "AAA", SESS[3]),))
    assert session.log_state == LOG_MISSING and binding["log_state"] == LOG_MISSING
    assert binding["dataset"] is None and binding["dependencies"][0]["origin"] == "NO_LOG"
    assert not (tmp_path / "market_observations.db").exists()  # binding never creates a log
    verdict = EvidenceQualifier(market).qualify(((binding, None),))
    assert verdict.state == Qualification.QUARANTINED_MISSING_PROVENANCE.value


# ===================================================================== legacy basis
def test_evidence_that_depends_on_a_legacy_baseline_session_is_descriptive_only(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    append_through(market, 5)
    binding = MarketBindingSession(market).bind(
        "FORMATION", "legacy-basis", (("FORMATION_CLOSE", "AAA", SESS[2]),)
    )
    assert binding["dependencies"][0]["origin"] == "BASELINE_LEGACY"
    verdict = EvidenceQualifier(market).qualify(((binding, None),))
    assert verdict.state == Qualification.BOUND_LEGACY_INPUT.value
    assert verdict.descriptive_only and not verdict.qualified and not verdict.excluded
    assert verdict.legacy_label == "LEGACY_UNVERIFIED"


# ===================================================================== forward daily
def _weekdays_before(end: date, count: int) -> list[str]:
    days, cursor = [], end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return sorted(days)


def _weekdays_from(start: date, count: int) -> tuple[str, ...]:
    days, cursor = [], start
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return tuple(days)


#: index 116 is 2026-09-21 (the activation cutoff); indexes 117..122 are 2026-09-22..09-29.
DSESS = tuple(_weekdays_before(date(2026, 9, 21), 117)) + _weekdays_from(date(2026, 9, 22), 13)
assert DSESS[116] == "2026-09-21" and len(DSESS) == 130
DSYMS = ("VNINDEX", "AAA", "BBB", "CCC")


def _daily_run(market: Path, ledger_path: Path, timestamp: str = "2026-10-01T12:00:00Z"):
    from quantlab.forward import run_forward_validation_daily

    return run_forward_validation_daily(
        database_path=market,
        ledger_path=ledger_path,
        protocol_spec=SPEC_PATH,
        phase8_root=PHASE8_ROOT,
        recorded_at_utc=timestamp,
    )


@pytest.fixture()
def daily_env(tmp_path: Path, protocol, monkeypatch: pytest.MonkeyPatch):
    if not PHASE8_ROOT.exists():
        pytest.skip("Phase 8 synthesis artifacts are not present in this checkout")
    monkeypatch.setattr(
        "core.universe.get_vn100_symbols",
        lambda: (_ for _ in ()).throw(AssertionError("network universe path used")),
    )
    market = make_market(tmp_path, sessions=DSESS, symbols=DSYMS, baseline_count=117)
    ledger_path = tmp_path / "forward.db"
    activation = create_activation(
        protocol,
        activated_at_utc="2026-09-27T12:00:00Z",
        confirmed_latest_completed_session=DSESS[116],
    )
    ForwardValidationLedger(ledger_path).activate(activation)
    return {"market": market, "ledger_path": ledger_path, "protocol": protocol}


def _append_daily(market: Path, last_index: int) -> None:
    append_through(market, last_index, sessions=DSESS, symbols=DSYMS, first_index=116)


def test_daily_forward_run_binds_formation_and_outcomes_to_the_consumed_versions(daily_env) -> None:
    market, ledger_path, protocol = daily_env["market"], daily_env["ledger_path"], daily_env["protocol"]
    _append_daily(market, 117)
    v_formation = version_id(market)
    created = _daily_run(market, ledger_path)
    assert created.formation_created
    ledger = ForwardValidationLedger(ledger_path)
    formation = ledger.formations(protocol.protocol_id)[0]
    binding = ledger.market_bindings()[("FORMATION", formation.formation_identity)]
    assert binding["dataset"]["dataset_version_id"] == v_formation
    assert {d["session"] for d in binding["dependencies"]} == {DSESS[117]}

    replay = _daily_run(market, ledger_path, "2026-10-01T13:00:00Z")
    assert replay.status_identity == created.status_identity
    assert ledger.market_bindings() == {("FORMATION", formation.formation_identity): binding}

    _append_daily(market, 122)
    v_outcome = version_id(market)
    assert v_outcome != v_formation
    matured = _daily_run(market, ledger_path, "2026-10-02T12:00:00Z")
    outcomes = ledger.outcomes(protocol.protocol_id)
    assert matured.outcomes_created == len(outcomes) > 0
    stored = ledger.market_bindings()
    qualifier = EvidenceQualifier(market)
    for item in outcomes:
        outcome_binding = stored[("OUTCOME", item.outcome_identity)]
        assert outcome_binding["dataset"]["dataset_version_id"] == v_outcome
        assert {d["session"] for d in outcome_binding["dependencies"]} == {DSESS[122]}
        assert qualifier.qualify(((outcome_binding, None),)).state == Qualification.PROVENANCE_VERIFIED.value
        verdict = qualifier.qualify(
            (
                (stored[("FORMATION", item.formation_identity)], {item.symbol, "VNINDEX"}),
                (outcome_binding, None),
            )
        )
        # the selected symbol's indicator history is the legacy baseline: descriptive only
        assert verdict.state == Qualification.BOUND_LEGACY_INPUT.value
    # the formation binding was not touched by the later run
    assert stored[("FORMATION", formation.formation_identity)] == binding
    snapshot = ledger.market_bindings()
    again = _daily_run(market, ledger_path, "2026-10-02T13:00:00Z")
    assert again.outcomes_created == 0 and ledger.market_bindings() == snapshot


def test_daily_refuses_outcome_whose_target_is_blocked_between_decision_and_write(
    daily_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    market, ledger_path, protocol = daily_env["market"], daily_env["ledger_path"], daily_env["protocol"]
    _append_daily(market, 117)
    _daily_run(market, ledger_path)
    ledger = ForwardValidationLedger(ledger_path)
    formation = ledger.formations(protocol.protocol_id)[0]
    victim = formation.positions[0].symbol
    _append_daily(market, 122)
    bindings_before = ledger.market_bindings()

    import quantlab.forward.daily as daily_module

    real_qualifier = daily_module.EvidenceQualifier

    class BlockAppearsFirst(real_qualifier):
        def __init__(self, *args, **kwargs):
            # the provider revises the target session after the version was pinned
            revise(market, victim, 120, 122, sessions=DSESS, first_index=116)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(daily_module, "EvidenceQualifier", BlockAppearsFirst)
    with pytest.raises(ProvenanceQuarantineError, match="QUARANTINED_UNRESOLVED_REVISION"):
        _daily_run(market, ledger_path, "2026-10-02T12:00:00Z")

    assert ledger.outcomes(protocol.protocol_id) == ()
    after = ledger.market_bindings()
    assert not [key for key in after if key[0] == "OUTCOME"]  # no false or partial outcome binding
    assert all(after[key] == value for key, value in bindings_before.items())  # nothing rebound
    # the formation of the newest session was already committed, with its own binding, before the refusal
    assert len(ledger.formations(protocol.protocol_id)) == 2
    assert len(after) == len(bindings_before) + 1
    monkeypatch.setattr(daily_module, "EvidenceQualifier", real_qualifier)
    # the operational gate stays global and fails closed until the block is resolved
    with pytest.raises(Exception, match=r"(?i)block|provenance|unresolved"):
        _daily_run(market, ledger_path, "2026-10-02T13:00:00Z")
    assert ledger.outcomes(protocol.protocol_id) == ()


def test_daily_runs_in_a_dataset_without_an_observation_log_and_records_that_fact(
    tmp_path: Path, protocol
) -> None:
    if not PHASE8_ROOT.exists():
        pytest.skip("Phase 8 synthesis artifacts are not present in this checkout")
    market = make_market(tmp_path, sessions=DSESS, symbols=DSYMS, baseline_count=118, register=False)
    ledger_path = tmp_path / "forward.db"
    activation = create_activation(
        protocol, activated_at_utc="2026-09-27T12:00:00Z", confirmed_latest_completed_session=DSESS[116]
    )
    ForwardValidationLedger(ledger_path).activate(activation)
    result = _daily_run(market, ledger_path)
    assert result.formation_created
    ledger = ForwardValidationLedger(ledger_path)
    (binding,) = ledger.market_bindings().values()
    assert binding["log_state"] == LOG_MISSING and binding["dataset"] is None
    assert not (tmp_path / "market_observations.db").exists()
    assert EvidenceQualifier(market).qualify(((binding, None),)).state == (
        Qualification.QUARANTINED_MISSING_PROVENANCE.value
    )


def test_daily_fails_closed_before_writing_when_a_block_already_exists(daily_env) -> None:
    market, ledger_path, protocol = daily_env["market"], daily_env["ledger_path"], daily_env["protocol"]
    _append_daily(market, 117)
    revise(market, "AAA", 117, 117, sessions=DSESS, first_index=116)
    with pytest.raises(Exception, match=r"(?i)block|provenance|unresolved"):
        _daily_run(market, ledger_path)
    ledger = ForwardValidationLedger(ledger_path)
    assert ledger.formations(protocol.protocol_id) == () and ledger.market_bindings() == {}


# ===================================================================== paper: entry / exit
from execution.paper_provenance import order_bindings, qualify_closed_trades  # noqa: E402
from tests.test_paper_execution_idempotency import (  # noqa: E402
    EXECUTION_DATE,
    SIGNAL_DATE,
    _executor,
    _queue,
)


def _paper_market(
    directory: Path,
    *,
    with_log: bool,
    name: str = "market.db",
    history: str = "legacy",
    index_history: str | None = None,
) -> Path:
    """AAA and VNINDEX history to the signal date, then the signal and execution sessions.

    ``history`` / ``index_history`` (default: same as ``history``) choose, per symbol,
    whether the pre-signal history is the registered LEGACY baseline (``"legacy"``) or arrives
    session by session through the admission guard (``"observed"``; a dummy ``ZZZ`` symbol
    then carries the baseline when nothing else does).  VNINDEX is the benchmark whose full
    history the signal path (market regime, relative strength) consumes.
    """
    index_history = history if index_history is None else index_history
    path = directory / name
    hist = _weekdays_before(SIGNAL_DATE - timedelta(days=1), 19)
    flat = lambda day: (day, 10.0, 10.5, 9.5, 10.0, 1_000_000)  # noqa: E731
    idx = lambda day: (day, 1300.0, 1310.0, 1290.0, 1300.0, 1_000_000)  # noqa: E731
    execution = (EXECUTION_DATE.isoformat(), 51.0, 52.0, 50.0, 51.0, 1_000_000)
    index_exec = (EXECUTION_DATE.isoformat(), 1300.0, 1310.0, 1290.0, 1300.0, 1_000_000)
    aaa_all = [flat(day) for day in hist] + [flat(SIGNAL_DATE.isoformat()), execution]
    idx_all = [idx(day) for day in hist] + [idx(SIGNAL_DATE.isoformat()), index_exec]
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    if not with_log:
        rows = [("AAA", *bar_) for bar_ in aaa_all] + [("VNINDEX", *bar_) for bar_ in idx_all]
    else:
        rows = []
        if history == "legacy":
            rows += [("AAA", *flat(day)) for day in hist]
        if index_history == "legacy":
            rows += [("VNINDEX", *idx(day)) for day in hist]
        if not rows:
            rows = [("ZZZ", *flat(hist[0]))]
    connection.executemany("INSERT INTO prices(symbol,time,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)", rows)
    connection.commit()
    connection.close()
    if with_log:
        open_observation_log(path).ensure_initial_baseline(path, now=NOW)
        batches = [
            ("AAA", aaa_all if history == "observed" else [flat(hist[-1]), *aaa_all[19:]]),
            ("VNINDEX", idx_all if index_history == "observed" else [idx(hist[-1]), *idx_all[19:]]),
        ]
        for symbol, bars in batches:
            outcome = admit_price_batch(
                _frame(symbol, bars),
                symbol=symbol,
                context=_context(bars[0][0], bars[-1][0]),
                market_db_path=path,
                now=NOW,
            )
            assert outcome.applied, outcome
    return path


def _paper_orders(paper_db: Path) -> list[dict]:
    with sqlite3.connect(paper_db) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(r) for r in connection.execute("SELECT * FROM paper_orders ORDER BY created_at, client_order_id")]


@pytest.mark.parametrize(
    ("history", "expected"),
    (("legacy", Qualification.BOUND_LEGACY_INPUT.value), ("observed", Qualification.PROVENANCE_VERIFIED.value)),
)
def test_new_paper_entry_fill_binds_entry_session_signal_and_consumed_windows(
    tmp_path: Path, history: str, expected: str
) -> None:
    paper_db, market = tmp_path / "paper.db", _paper_market(tmp_path, with_log=True, history=history)
    executor = _executor(paper_db)
    pending_id = _queue(executor)
    version = version_id(market)

    result = executor.execute_pending_signals(valuation_date=EXECUTION_DATE, market_database_path=market)
    assert result.executions[0].status == "FILLED"

    (order,) = _paper_orders(paper_db)
    binding = json.loads(order["execution_context"])["market_binding"]
    assert order["source_intent_id"] == f"pending_signal:{pending_id}"
    assert binding["subject"] == {"kind": "PAPER_ENTRY", "ref": f"pending_signal:{pending_id}"}
    assert binding["dataset"]["dataset_version_id"] == version
    deps = {d["role"]: d for d in binding["dependencies"]}
    assert set(deps) == {"ENTRY_OPEN", "SIGNAL_REFERENCE", "ADTV20_WINDOW", "SIGNAL_HISTORY", "REGIME_RS_HISTORY"}
    assert deps["ENTRY_OPEN"]["session"] == EXECUTION_DATE.isoformat()
    assert deps["SIGNAL_REFERENCE"]["session"] == SIGNAL_DATE.isoformat()
    # the ADTV20 window is exactly the 20 sessions the sizing read
    adtv = deps["ADTV20_WINDOW"]
    assert (adtv["coverage"], adtv["session"]) == ("WINDOW", SIGNAL_DATE.isoformat())
    assert adtv["window_start"] == _weekdays_before(SIGNAL_DATE - timedelta(days=1), 19)[0]
    assert adtv["lineage"]["session_count"] == 20
    assert deps["SIGNAL_HISTORY"]["window_start"] <= adtv["window_start"]
    legacy_sessions = adtv["lineage"]["origin_counts"].get("BASELINE_LEGACY", 0)
    assert (legacy_sessions > 0) == (history == "legacy")
    assert order_bindings(paper_db) == {order["client_order_id"]: binding}
    verdict = EvidenceQualifier(market).qualify(((binding, {"AAA"}),))
    assert verdict.state == expected
    assert verdict.qualified == (expected == Qualification.PROVENANCE_VERIFIED.value)
    if history == "legacy":  # the endpoints alone are observed: only the consumed window makes it legacy
        endpoints = dict(binding, dependencies=[d for d in binding["dependencies"] if d["coverage"] == "POINT"])
        endpoints.pop("binding_identity")
        assert EvidenceQualifier(market).qualify(((endpoints, {"AAA"}),)).state == (
            Qualification.PROVENANCE_VERIFIED.value
        )

    # retry of the same operation: nothing new, binding unchanged
    _executor(paper_db).execute_pending_signals(valuation_date=EXECUTION_DATE, market_database_path=market)
    assert [json.loads(o["execution_context"])["market_binding"] for o in _paper_orders(paper_db)] == [binding]


def test_revision_inside_the_adtv20_window_quarantines_the_entry(tmp_path: Path) -> None:
    paper_db, market = tmp_path / "paper.db", _paper_market(tmp_path, with_log=True, history="observed")
    executor = _executor(paper_db)
    _queue(executor)
    executor.execute_pending_signals(valuation_date=EXECUTION_DATE, market_database_path=market)
    (order,) = _paper_orders(paper_db)
    binding = json.loads(order["execution_context"])["market_binding"]
    assert EvidenceQualifier(market).qualify(((binding, {"AAA"}),)).qualified

    hist = _weekdays_before(SIGNAL_DATE - timedelta(days=1), 19)
    bars = [(d, 10.0, 10.5, 9.5, 10.0, 1_000_000) for d in hist]
    bars[3] = (hist[3], 8.0, 8.4, 7.6, 8.0, 1_000_000)  # a provider revision of an in-window session
    bars += [(SIGNAL_DATE.isoformat(), 10.0, 10.5, 9.5, 10.0, 1_000_000)]
    outcome = admit_price_batch(
        _frame("AAA", bars), symbol="AAA", context=_context(hist[0], SIGNAL_DATE.isoformat()),
        market_db_path=market, now=NOW,
    )
    assert outcome.result is AdmissionResult.BLOCKED_REVISION_CHANGED
    verdict = EvidenceQualifier(market).qualify(((binding, {"AAA"}),))
    assert verdict.state == Qualification.QUARANTINED_UNRESOLVED_REVISION.value and verdict.excluded


def test_paper_execution_semantics_are_identical_with_and_without_a_log(tmp_path: Path) -> None:
    results = {}
    for label, with_log in (("log", True), ("nolog", False)):
        directory = tmp_path / label
        directory.mkdir()
        paper_db, market = directory / "paper.db", _paper_market(directory, with_log=with_log)
        executor = _executor(paper_db)
        _queue(executor)
        outcome = executor.execute_pending_signals(valuation_date=EXECUTION_DATE, market_database_path=market)
        with sqlite3.connect(paper_db) as connection:
            fills = connection.execute(
                "SELECT symbol,side,quantity,price,gross_value,commission,slippage_cost,net_cash_flow FROM paper_fills"
            ).fetchall()
            position = connection.execute("SELECT symbol,quantity FROM paper_positions").fetchall()
        orders = [
            {k: v for k, v in o.items() if k not in {"client_order_id", "execution_context", "created_at", "updated_at"}}
            for o in _paper_orders(paper_db)
        ]
        results[label] = (outcome.executions[0].status, outcome.executions[0].quantity, fills, position, orders)
    assert results["log"] == results["nolog"]
    # the absence of a log is recorded, never papered over
    nolog_order = _paper_orders(tmp_path / "nolog" / "paper.db")[0]
    assert json.loads(nolog_order["execution_context"])["market_binding"]["log_state"] == LOG_MISSING


def test_paper_entry_crash_leaves_no_binding_and_retry_binds_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paper_db, market = tmp_path / "paper.db", _paper_market(tmp_path, with_log=True)
    executor = _executor(paper_db)
    _queue(executor)
    real = executor.order_manager.buy_market

    def crash(**kwargs):
        raise SimulatedCrash()

    monkeypatch.setattr(executor.order_manager, "buy_market", crash)
    with pytest.raises(SimulatedCrash):
        executor.execute_pending_signals(valuation_date=EXECUTION_DATE, market_database_path=market)
    assert _paper_orders(paper_db) == [] and order_bindings(paper_db) == {}
    monkeypatch.setattr(executor.order_manager, "buy_market", real)
    _executor(paper_db).execute_pending_signals(valuation_date=EXECUTION_DATE, market_database_path=market)
    assert len(order_bindings(paper_db)) == 1


def _lifecycle_market(directory: Path) -> Path:
    path = directory / "market.db"
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.execute(
        "INSERT INTO prices(symbol,time,open,high,low,close,volume) VALUES ('VNM','2026-08-05',61.5,63.0,61.2,62.5,1000000)"
    )
    connection.commit()
    connection.close()
    open_observation_log(path).ensure_initial_baseline(path, now=NOW)
    outcome = admit_price_batch(
        _frame("VNM", [("2026-08-05", 61.5, 63.0, 61.2, 62.5, 1_000_000), ("2026-08-06", 61.5, 62.0, 60.5, 60.8, 1_100_000)]),
        symbol="VNM",
        context=_context("2026-08-05", "2026-08-06"),
        market_db_path=path,
        now=NOW,
    )
    assert outcome.applied
    return path


def _lifecycle_run(tmp_path: Path):
    from tests.test_lifecycle_manager import build_runtime
    from execution.lifecycle_models import PositionLifecycleState

    paper_db, market = tmp_path / "paper.db", _lifecycle_market(tmp_path)
    broker, order_manager, lifecycle = build_runtime(paper_db=paper_db, market_db=market)
    fill = order_manager.buy_market(symbol="VNM", quantity=300, price=59_500)
    broker.save_position_lifecycle(
        PositionLifecycleState(
            symbol="VNM", entry_date=date(2026, 8, 1), entry_price=fill.price, initial_quantity=300,
            stop_price=58_000, take_profit_price=70_000, highest_price=fill.price,
            trailing_stop_price=61_000, trailing_atr_multiplier=2.0,
        )
    )
    lifecycle.run(valuation_date="2026-08-05")
    second = lifecycle.run(valuation_date="2026-08-06")
    assert [item.reason for item in second.exited] == ["TRAILING_STOP"]
    return paper_db, market, lifecycle


def test_new_paper_exit_fill_binds_exit_session_and_entry_window(tmp_path: Path) -> None:
    paper_db, market, lifecycle = _lifecycle_run(tmp_path)
    version = version_id(market)
    orders_before = _paper_orders(paper_db)
    lifecycle.run(valuation_date="2026-08-06")  # replay of the same session: nothing new, nothing rebound
    assert _paper_orders(paper_db) == orders_before
    sells = [o for o in _paper_orders(paper_db) if o["side"] == "SELL"]
    assert len(sells) == 1
    binding = json.loads(sells[0]["execution_context"])["market_binding"]
    assert binding["subject"]["kind"] == "PAPER_EXIT"
    assert binding["subject"]["ref"] == sells[0]["source_intent_id"]
    assert binding["dataset"]["dataset_version_id"] == version
    deps = {d["role"]: d for d in binding["dependencies"]}
    dep = deps["EXIT_PRICE"]
    assert (dep["symbol"], dep["session"], dep["window_start"]) == ("VNM", "2026-08-06", "2026-08-01")
    assert dep["origin"] == "OBSERVATION" and dep["version_id"]
    history = deps["EXIT_HISTORY"]  # the Wilder ATR / trailing logic read the whole stored history
    assert (history["coverage"], history["window_start"]) == ("WINDOW", "2026-08-05")
    assert history["lineage"]["origin_counts"] == {"BASELINE_LEGACY": 1, "OBSERVATION": 1}
    assert history["origin"] == "BASELINE_LEGACY"
    # the entry leg pre-dates R3 (bought without a binding): the trade is legacy, never verified
    qualifier = EvidenceQualifier(market)
    (verdict,) = qualify_closed_trades(paper_db, qualifier).values()
    assert verdict.state == Qualification.LEGACY_UNBOUND.value and verdict.legacy_label == "LEGACY_UNVERIFIED"


def test_paper_consumers_label_legacy_and_exclude_quarantined_trades(tmp_path: Path) -> None:
    from analysis.paper_performance import calculate_paper_performance, load_closed_trades_frame

    paper_db, market, _lifecycle = _lifecycle_run(tmp_path)
    qualifier = EvidenceQualifier(market)

    plain = load_closed_trades_frame(paper_db)
    assert len(plain) == 1 and "provenance_qualification" not in plain.columns  # legacy frame unchanged
    labelled = load_closed_trades_frame(paper_db, qualifier=qualifier)
    assert labelled["provenance_qualification"].tolist() == [Qualification.LEGACY_UNBOUND.value]
    assert len(load_closed_trades_frame(paper_db, qualifier=qualifier, qualification_required=True)) == 0
    descriptive = calculate_paper_performance(paper_db)
    assert descriptive.provenance_scope == "UNQUALIFIED_DESCRIPTIVE"
    required = calculate_paper_performance(paper_db, qualifier=qualifier, qualification_required=True)
    assert required.provenance_scope.startswith("QUALIFIED_TRADES_ONLY")
    assert required.provenance_scope.endswith("ACCOUNT_LEVEL_METRICS_NOT_FILTERED")

    # an unresolved revision of the exit session quarantines the outcome; the row stays
    revise(market, "VNM", 1, 1, sessions=("2026-08-05", "2026-08-06"), first_index=0)
    quarantined = EvidenceQualifier(market)
    (verdict,) = qualify_closed_trades(paper_db, quarantined).values()
    assert verdict.state == Qualification.QUARANTINED_UNRESOLVED_REVISION.value
    kept = load_closed_trades_frame(paper_db, qualifier=quarantined)
    assert kept["provenance_qualification"].tolist() == [Qualification.QUARANTINED_UNRESOLVED_REVISION.value]
    assert len(load_closed_trades_frame(paper_db, qualifier=quarantined, qualification_required=True)) == 0


# ===================================================================== consumers: forward / paper views
def test_forward_view_separates_qualified_legacy_and_quarantined_outcomes(tmp_path: Path) -> None:
    from quantctl.forward_evidence import inspect_forward_evidence_catalog
    from quantlab.forward.contracts import (
        ForwardMaturity,
        ForwardOutcome,
        MaturityStatus,
        OutcomeAvailability,
    )
    from tests.test_forward_evidence_view import _activation, _formation, _root_with_evidence

    root, _paper, forward = _root_with_evidence(tmp_path)
    market = make_market(tmp_path)
    append_through(market, 9)
    session = MarketBindingSession(market)
    ledger = ForwardValidationLedger(forward)
    ledger.activate(_activation())
    formation = _formation()
    formation_bound = session.bind(
        "FORMATION", formation.formation_identity,
        (("FORMATION_CLOSE", "AAA", SESS[4]), ("BENCHMARK_FORMATION_CLOSE", "VNINDEX", SESS[4])),
    )
    ledger.record_formation(formation, (5, 10), market_binding=formation_bound)
    ledger.record_maturities((
        ForwardMaturity("protocol-a", formation.formation_identity, formation.formation_session, 5,
                        "2026-01-07", MaturityStatus.MATURED, "EXACT_SESSION", "2026-01-07T10:00:00Z", "mature-5"),
    ))

    def outcome(symbol: str, ident: str, stock: float) -> ForwardOutcome:
        return ForwardOutcome(
            "protocol-a", formation.formation_identity, 5, "2026-01-07", symbol, 1.0,
            OutcomeAvailability.AVAILABLE, stock, 2.0, stock - 2.0, "2026-01-07T10:00:00Z", ident,
        )

    bound, legacy = outcome("AAA", "outcome-aaa", 6.0), outcome("BBB", "outcome-bbb", 10.0)
    ledger.record_outcomes(
        (bound, legacy),
        market_bindings={
            "outcome-aaa": session.bind(
                "OUTCOME", "outcome-aaa", (("STOCK_TARGET_CLOSE", "AAA", SESS[9], SESS[4]),
                                           ("BENCHMARK_TARGET_CLOSE", "VNINDEX", SESS[9], SESS[4]))
            )
        },
    )

    def summary():
        view = inspect_forward_evidence_catalog(root=root, environ={}, market_database_path=market).protocols[0]
        return next(item for item in view.summaries if item.horizon_sessions == 5)

    before = summary()
    assert (before.qualified_outcome_count, before.legacy_unbound_outcome_count, before.quarantined_outcome_count) == (1, 1, 0)
    assert before.qualified_mean_excess_forward_return_pct_points == pytest.approx(4.0)
    assert before.mean_stock_forward_return_pct == pytest.approx(8.0)  # descriptive, includes labelled legacy

    revise(market, "AAA", 8, 9)
    after = summary()
    assert after.available_outcome_count == 2  # nothing deleted
    assert (after.qualified_outcome_count, after.legacy_unbound_outcome_count, after.quarantined_outcome_count) == (0, 1, 1)
    assert after.qualified_mean_excess_forward_return_pct_points is None
    assert after.mean_stock_forward_return_pct == pytest.approx(10.0)  # the quarantined outcome is not averaged in


def test_paper_observation_view_carries_the_qualification_of_its_market_basis(tmp_path: Path) -> None:
    from quantctl.forward_evidence import inspect_forward_evidence_catalog
    from quantlab.evidence import ProspectivePortfolioEvidenceLedger
    from tests.test_forward_evidence_view import _paper_record, _root_with_evidence

    root, paper, _forward = _root_with_evidence(tmp_path)
    market = make_market(tmp_path)
    append_through(market, 9)
    evidence = ProspectivePortfolioEvidenceLedger(root / "data" / "prospective_portfolio_evidence.db")
    legacy_record = _paper_record(paper_path=paper, session="2026-01-02", equity=100.0)
    bound_record = _paper_record(paper_path=paper, session="2026-01-03", equity=101.0)
    evidence.append(legacy_record)  # pre-R3 style
    binding = MarketBindingSession(market).bind(
        "PAPER_OBSERVATION", evidence.evidence_key(bound_record), (("BENCHMARK_CLOSE", "VNINDEX", SESS[9]),)
    )
    evidence.append(bound_record, market_binding=binding)
    with pytest.raises(ValueError, match="does not belong"):  # a binding for another subject is refused
        evidence.append(
            _paper_record(paper_path=paper, session="2026-01-04", equity=102.0), market_binding=binding
        )

    def points():
        catalog = inspect_forward_evidence_catalog(
            root=root, environ={"PAPER_V2_DATABASE_PATH": str(paper)}, market_database_path=market
        )
        return {p.session: p.market_data_qualification for p in catalog.paper.points}

    assert points() == {
        "2026-01-02": Qualification.LEGACY_UNBOUND.value,
        "2026-01-03": Qualification.PROVENANCE_VERIFIED.value,
    }
    revise(market, "VNINDEX", 9, 9)
    assert points()["2026-01-03"] == Qualification.QUARANTINED_UNRESOLVED_REVISION.value
    assert points()["2026-01-02"] == Qualification.LEGACY_UNBOUND.value


# =====================================================================================
# Sol 6.5 review fixes (P1-1 .. P1-6)
# =====================================================================================
from core import market_admission  # noqa: E402
from core.market_observation_log import ObservationLog  # noqa: E402
from core.evidence_market_binding import (  # noqa: E402
    COVERAGE_UNPROVEN,
    PendingApplicationError,
)


def _prospective_pair(directory: Path):
    from quantlab.evidence import ProspectivePortfolioEvidenceLedger

    return ProspectivePortfolioEvidenceLedger(directory / "evidence.db")


# ------------------------------------------------------------------ P1-1 coherent capture
def test_version_moving_between_the_guard_and_the_dependency_lookup_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, protocol, activation
) -> None:
    market = make_market(tmp_path)
    append_through(market, 4)
    ledger = ForwardValidationLedger(tmp_path / "forward.db")
    ledger.activate(activation)
    formation = make_formation(protocol, activation)
    session = MarketBindingSession(market)  # A pins V1
    v1 = session.dataset_version_id

    real = ObservationLog.session_provenance
    fired: list[bool] = []

    def racing(self, symbol, sess):
        if not fired:  # B finalizes the next session between A's guard and A's lookup
            fired.append(True)
            append_through(market, 5)
        return real(self, symbol, sess)

    monkeypatch.setattr(ObservationLog, "session_provenance", racing)
    with pytest.raises(StaleDatasetVersionError, match="while its provenance was being read"):
        formation_binding(session, formation, protocol)
    monkeypatch.setattr(ObservationLog, "session_provenance", real)

    assert version_id(market) != v1  # the race really happened
    # A wrote nothing and the caller re-runs its own decision: that binds the new version coherently
    assert ledger.formations(protocol.protocol_id) == () and ledger.market_bindings() == {}
    fresh = formation_binding(MarketBindingSession(market), formation, protocol)
    assert fresh["dataset"]["dataset_version_id"] == version_id(market)


def test_version_moving_during_a_window_lineage_read_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    market = make_market(tmp_path)
    append_through(market, 4)
    session = MarketBindingSession(market)
    real = ObservationLog.window_provenance
    fired: list[bool] = []

    def racing(self, symbol, start, end):
        if not fired:
            fired.append(True)
            append_through(market, 5)
        return real(self, symbol, start, end)

    monkeypatch.setattr(ObservationLog, "window_provenance", racing)
    with pytest.raises(StaleDatasetVersionError):
        session.bind("FORMATION", "w", (("FORMATION_FEATURE_HISTORY", "AAA", SESS[4], SESS[0], "WINDOW"),))


def test_pending_application_on_a_consumed_session_or_window_is_refused(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    append_through(market, 4)

    def crash(*_a, **_k):
        raise SimulatedCrash()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(market_admission, "_insert_rows", crash)
        with pytest.raises(SimulatedCrash):
            admit_price_batch(
                _frame("AAA", [bar("AAA", SESS, i) for i in range(0, 6)]),
                symbol="AAA", context=_context(SESS[0], SESS[5]), market_db_path=market, now=NOW,
            )
    assert open_observation_log(market).session_provenance("AAA", SESS[5])["origin"] == "PENDING_APPLICATION"
    session = MarketBindingSession(market)
    with pytest.raises(PendingApplicationError):
        session.bind("PAPER_ENTRY", "p1", (("ENTRY_OPEN", "AAA", SESS[5]),))
    with pytest.raises(PendingApplicationError):  # a consumed window that reaches the pending session
        session.bind("FORMATION", "p2", (("FORMATION_FEATURE_HISTORY", "AAA", SESS[5], SESS[0], "WINDOW"),))
    # sessions before the pending claim, and other symbols, are unaffected
    ok = session.bind(
        "FORMATION", "p3",
        (("FORMATION_FEATURE_HISTORY", "AAA", SESS[4], SESS[0], "WINDOW"), ("ENTRY_OPEN", "BBB", SESS[4])),
    )
    assert ok["dataset"]["dataset_version_id"] == session.dataset_version_id


# ------------------------------------------------------------------ P1-2 consumed windows
def test_legacy_indicator_window_is_not_verified_even_with_observed_endpoints(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    append_through(market, 9)
    session = MarketBindingSession(market)
    endpoint = ("FORMATION_CLOSE", "AAA", SESS[9])
    window = ("FORMATION_FEATURE_HISTORY", "AAA", SESS[9], session.first_session("AAA"), "WINDOW")
    qualifier = EvidenceQualifier(market)

    only_endpoint = session.bind("FORMATION", "e", (endpoint,))
    with_window = session.bind("FORMATION", "w", (endpoint, window))
    assert qualifier.qualify(((only_endpoint, None),)).state == Qualification.PROVENANCE_VERIFIED.value  # the flaw
    verdict = qualifier.qualify(((with_window, None),))
    assert verdict.state == Qualification.BOUND_LEGACY_INPUT.value and not verdict.qualified
    dep = next(d for d in with_window["dependencies"] if d["coverage"] == "WINDOW")
    assert dep["lineage"]["origin_counts"] == {"BASELINE_LEGACY": 4, "OBSERVATION": 6}
    assert dep["lineage"]["session_count"] == 10 and dep["origin"] == "BASELINE_LEGACY"
    assert len(json.dumps(dep)) < 1_000  # a lineage summary, never the bars


def test_fully_observed_consumed_window_may_verify(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    append_through(market, 9)
    outcome = admit_price_batch(  # a symbol that never existed in the legacy baseline
        _frame("NEW", [bar("NEW", SESS, i) for i in range(10)]),
        symbol="NEW", context=_context(SESS[0], SESS[9]), market_db_path=market, now=NOW,
    )
    assert outcome.result is AdmissionResult.ADMITTED_INITIAL_LOAD
    session = MarketBindingSession(market)
    binding = session.bind(
        "FORMATION", "n",
        (("FORMATION_CLOSE", "NEW", SESS[9]), ("FORMATION_FEATURE_HISTORY", "NEW", SESS[9], SESS[0], "WINDOW")),
    )
    window = next(d for d in binding["dependencies"] if d["coverage"] == "WINDOW")
    assert window["lineage"]["origin_counts"] == {"OBSERVATION": 10} and window["origin"] == "OBSERVATION"
    assert EvidenceQualifier(market).qualify(((binding, None),)).state == Qualification.PROVENANCE_VERIFIED.value


def test_revision_inside_the_consumed_window_quarantines_but_not_a_point_only_dependency(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    append_through(market, 9)
    session = MarketBindingSession(market)
    windowed = session.bind(
        "FORMATION", "w", (("FORMATION_FEATURE_HISTORY", "AAA", SESS[9], SESS[0], "WINDOW"),)
    )
    point = session.bind("FORMATION", "p", (("FORMATION_CLOSE", "AAA", SESS[9]),))
    revise(market, "AAA", 2, 9)  # an old, legacy session inside the consumed history
    qualifier = EvidenceQualifier(market)
    assert qualifier.qualify(((windowed, None),)).state == Qualification.QUARANTINED_UNRESOLVED_REVISION.value
    assert qualifier.qualify(((point, None),)).state == Qualification.PROVENANCE_VERIFIED.value


def test_window_lineage_drift_is_incompatible_basis(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    append_through(market, 9)
    binding = MarketBindingSession(market).bind(
        "FORMATION", "d", (("FORMATION_FEATURE_HISTORY", "AAA", SESS[9], SESS[0], "WINDOW"),)
    )
    forged = json.loads(json.dumps(binding))
    forged["dependencies"][0]["lineage"]["digest"] = "0" * 64
    forged["binding_identity"] = binding_identity(forged)
    verdict = EvidenceQualifier(market).qualify(((forged, None),))
    assert verdict.state == Qualification.QUARANTINED_INCOMPATIBLE_BASIS.value
    assert any(r.startswith("WINDOW_LINEAGE_DRIFT") for r in verdict.reasons)


# ------------------------------------------------------------------ P1-3 paper observations
OBS = SESS[9]


def _paper_with_evidence(
    path: Path,
    market: Path,
    *,
    mark: float | None,
    position_leg: bool = True,
    trade_legs: bool = False,
    legacy_trade: bool = False,
) -> None:
    from execution.lifecycle_models import PositionLifecycleState
    from execution.models import Position
    from execution.persistence import PaperTradingStore
    from execution.portfolio_state import PortfolioState

    store = PaperTradingStore(path)
    store.save_initial_cash(1_000_000.0)
    positions = {} if mark is None else {"AAA": Position("AAA", quantity=10, average_price=100_000.0, market_price=mark)}
    store.save_portfolio_state(
        PortfolioState(initial_cash=1_000_000.0, cash=800_000.0, realized_pnl=5.0, positions=positions)
    )
    session = MarketBindingSession(market)
    now = "2026-10-02T16:00:00+00:00"
    orders: list[tuple] = []
    if mark is not None:
        if position_leg:
            leg = session.bind("PAPER_ENTRY", "pending_signal:1", (("ENTRY_OPEN", "AAA", SESS[5]), ("SIGNAL_REFERENCE", "AAA", SESS[4])))
            ctx = json.dumps({"market_binding": leg})
        else:
            ctx = None
        orders.append(("entry-aaa", "AAA", "BUY", 10, "MARKET", None, 100.0, "FILLED", 10, 100.0, None, now, now, "pending_signal:1", ctx))
        store.save_position_lifecycle(
            PositionLifecycleState(
                symbol="AAA", entry_date=date.fromisoformat(SESS[5]), entry_price=100_000.0,
                initial_quantity=10, stop_price=90_000.0, entry_order_id="entry-aaa",
            )
        )
    if trade_legs or legacy_trade:
        entry = session.bind("PAPER_ENTRY", "pending_signal:2", (("ENTRY_OPEN", "BBB", SESS[5]), ("SIGNAL_REFERENCE", "BBB", SESS[4])))
        exit_ = session.bind("PAPER_EXIT", "paper_exit:bbb", (("EXIT_PRICE", "BBB", SESS[8], SESS[5]),))
        entry_ctx = json.dumps({"market_binding": entry}) if trade_legs else None
        exit_ctx = {"exit": {
            "entry_date": SESS[5], "entry_price": 10.2, "exit_date": SESS[8], "holding_days": 3,
            "exit_reason": "TAKE_PROFIT", "entry_order_id": "entry-bbb", "strategy_version": STRATEGY_ID,
            "policy_fingerprint": "policy-a",
        }}
        if trade_legs:
            exit_ctx["market_binding"] = exit_
        orders.append(("entry-bbb", "BBB", "BUY", 10, "MARKET", None, 10.1, "FILLED", 10, 10.2, None, now, now, "pending_signal:2", entry_ctx))
        orders.append(("exit-bbb", "BBB", "SELL", 10, "MARKET", None, 12.0, "FILLED", 10, 11.9, None, now, now, "paper_exit:bbb", json.dumps(exit_ctx)))
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO paper_orders(client_order_id,symbol,side,quantity,order_type,limit_price,reference_price,"
            "status,filled_quantity,average_fill_price,rejection_reason,created_at,updated_at,source_intent_id,"
            "execution_context) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            orders,
        )
        if trade_legs or legacy_trade:
            connection.executemany(
                "INSERT INTO paper_fills(order_id,symbol,side,quantity,price,gross_value,commission,slippage_cost,"
                "net_cash_flow,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    ("entry-bbb", "BBB", "BUY", 10, 10.2, 102.0, 1.0, 0.5, -103.0, now),
                    ("exit-bbb", "BBB", "SELL", 10, 11.9, 119.0, 1.5, 0.7, 117.5, now),
                ),
            )
            connection.execute(
                "INSERT INTO paper_closed_trades(symbol,entry_date,exit_date,quantity,entry_price,exit_price,"
                "gross_proceeds,commission,realized_pnl,return_pct,holding_days,exit_reason,order_id,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("BBB", SESS[5], SESS[8], 10, 10.2, 11.9, 119.0, 1.5, 15.5, 15.19, 3, "TAKE_PROFIT", "exit-bbb", now),
            )


STRATEGY_ID = "Q70_FROZEN"


def _capture_observation(paper: Path, market: Path, evidence: Path, *, session: str = OBS, baseline=None):
    from quantlab.evidence import PaperEventCursor, capture_prospective_portfolio_evidence

    return capture_prospective_portfolio_evidence(
        observation_date=session,
        paper_database_path=paper,
        source_store_id="q70-frozen",
        strategy_identity=STRATEGY_ID,
        runtime_configuration_fingerprint="config-r3",
        market_database_path=market,
        evidence_database_path=evidence,
        baseline_event_cursor=PaperEventCursor() if baseline is None else baseline,
    )


def _observation_state(market: Path, evidence: Path):
    from quantlab.evidence import ProspectivePortfolioEvidenceLedger

    ledger = ProspectivePortfolioEvidenceLedger(evidence)
    records = ledger.records(strategy_identity=STRATEGY_ID, source_store_identity=None) if False else None
    bindings = ledger.market_bindings()
    ordered = [bindings[key] for key in sorted(bindings, key=lambda k: bindings[k]["bound_at_utc"])]
    return ordered, EvidenceQualifier(market).qualify_observation_series(ordered)


def test_a_stored_mark_is_labelled_mark_close_only_when_it_equals_the_session_close(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    append_through(market, 10)
    proven_mark = bar("AAA", SESS, 9)[4] * 1000.0
    stale_mark = bar("AAA", SESS, 8)[4] * 1000.0
    assert proven_mark != stale_mark

    good, bad = tmp_path / "good", tmp_path / "bad"
    for directory, mark in ((good, proven_mark), (bad, stale_mark)):
        directory.mkdir()
        _paper_with_evidence(directory / "paper.db", market, mark=mark)
        _capture_observation(directory / "paper.db", market, directory / "evidence.db")

    (good_binding,), (good_verdict,) = _observation_state(market, good / "evidence.db")
    assert {d["role"] for d in good_binding["dependencies"]} == {"MARK_CLOSE", "BENCHMARK_CLOSE"}
    assert good_binding["lineage"]["mark_checks"][0]["status"] == "MATCHES_OBSERVATION_CLOSE"
    assert good_verdict.state == Qualification.PROVENANCE_VERIFIED.value

    (bad_binding,), (bad_verdict,) = _observation_state(market, bad / "evidence.db")
    roles = {d["role"]: d for d in bad_binding["dependencies"]}
    assert "MARK_CLOSE" not in roles  # never fabricate current-session provenance for a stale mark
    assert roles["STORED_MARK_UNVERIFIED"]["origin"] == "UNPROVEN_SOURCE"
    assert roles["STORED_MARK_UNVERIFIED"]["coverage"] == COVERAGE_UNPROVEN
    check = bad_binding["lineage"]["mark_checks"][0]
    assert (check["status"], check["matched_session"]) == ("STALE_OR_UNMATCHED", SESS[8])
    assert bad_verdict.state == Qualification.QUARANTINED_MISSING_PROVENANCE.value and bad_verdict.excluded


def test_a_mark_matching_no_market_close_is_never_verified(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    append_through(market, 10)
    _paper_with_evidence(tmp_path / "paper.db", market, mark=12_345.0)
    _capture_observation(tmp_path / "paper.db", market, tmp_path / "evidence.db")
    (binding,), (verdict,) = _observation_state(market, tmp_path / "evidence.db")
    assert binding["lineage"]["mark_checks"][0]["matched_session"] is None
    assert verdict.state == Qualification.QUARANTINED_MISSING_PROVENANCE.value


def test_revision_of_a_realized_pnl_trade_symbol_quarantines_the_observation(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    append_through(market, 10)
    _paper_with_evidence(tmp_path / "paper.db", market, mark=None, trade_legs=True)
    _capture_observation(tmp_path / "paper.db", market, tmp_path / "evidence.db")
    (binding,), (verdict,) = _observation_state(market, tmp_path / "evidence.db")
    (trade,) = binding["lineage"]["trades_added"]
    assert (trade["symbol"], trade["exit_order_id"], trade["entry_order_id"]) == ("BBB", "exit-bbb", "entry-bbb")
    assert {d["role"] for d in trade["exit"]["dependencies"]} == {"EXIT_PRICE"}
    assert {d["role"] for d in trade["entry"]["dependencies"]} == {"ENTRY_OPEN", "SIGNAL_REFERENCE"}
    assert binding["lineage"]["closed_trade_count"] == binding["lineage"]["tracked_trade_count"] == 1
    assert verdict.state == Qualification.PROVENANCE_VERIFIED.value

    revise(market, "AAA", 9, 10)  # a symbol that contributed nothing
    assert _observation_state(market, tmp_path / "evidence.db")[1][0].state == Qualification.PROVENANCE_VERIFIED.value
    revise(market, "BBB", 8, 10)  # the trade's exit session
    state = _observation_state(market, tmp_path / "evidence.db")[1][0]
    assert state.state == Qualification.QUARANTINED_UNRESOLVED_REVISION.value
    assert any(":BBB:" in reason for reason in state.reasons)


def test_realized_pnl_without_trade_provenance_is_legacy_never_verified(tmp_path: Path) -> None:
    from quantlab.evidence import PaperEventCursor

    market = make_market(tmp_path)
    append_through(market, 10)
    for label, kwargs, baseline in (
        ("legacy_legs", {"legacy_trade": True}, None),  # trade exists but its orders carry no binding
        ("pre_activation", {"trade_legs": True}, PaperEventCursor(fill_id=2, closed_trade_id=1)),
    ):
        directory = tmp_path / label
        directory.mkdir()
        _paper_with_evidence(directory / "paper.db", market, mark=None, **kwargs)
        _capture_observation(directory / "paper.db", market, directory / "evidence.db", baseline=baseline)
        (binding,), (verdict,) = _observation_state(market, directory / "evidence.db")
        assert verdict.state == Qualification.LEGACY_UNBOUND.value, label
        assert verdict.legacy_label == "LEGACY_UNVERIFIED"
    assert binding["lineage"]["closed_trade_count"] == 1 and binding["lineage"]["tracked_trade_count"] == 0


def test_legacy_open_position_entry_makes_the_observation_legacy(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    append_through(market, 10)
    _paper_with_evidence(tmp_path / "paper.db", market, mark=bar("AAA", SESS, 9)[4] * 1000.0, position_leg=False)
    _capture_observation(tmp_path / "paper.db", market, tmp_path / "evidence.db")
    assert _observation_state(market, tmp_path / "evidence.db")[1][0].state == Qualification.LEGACY_UNBOUND.value


def test_paper_reset_does_not_destroy_provenance_needed_by_surviving_evidence(tmp_path: Path) -> None:
    from execution.persistence import PaperTradingStore

    market = make_market(tmp_path)
    append_through(market, 10)
    _paper_with_evidence(tmp_path / "paper.db", market, mark=None, trade_legs=True)
    _capture_observation(tmp_path / "paper.db", market, tmp_path / "evidence.db")
    _capture_observation(tmp_path / "paper.db", market, tmp_path / "evidence.db", session=SESS[10])
    before = _observation_state(market, tmp_path / "evidence.db")[1]
    assert [r.state for r in before] == [Qualification.PROVENANCE_VERIFIED.value] * 2

    PaperTradingStore(tmp_path / "paper.db").reset()  # deletes every paper order (and its execution_context)
    with sqlite3.connect(tmp_path / "paper.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0

    ordered, after = _observation_state(market, tmp_path / "evidence.db")
    assert [r.state for r in after] == [Qualification.PROVENANCE_VERIFIED.value] * 2
    assert len(ordered[0]["lineage"]["trades_added"]) == 1 and ordered[1]["lineage"]["trades_added"] == []
    assert ordered[1]["lineage"]["tracked_trade_count"] == 1  # cumulative chain survives
    revise(market, "BBB", 8, 10)  # a later revision still reaches both surviving observations
    assert [r.state for r in _observation_state(market, tmp_path / "evidence.db")[1]] == [
        Qualification.QUARANTINED_UNRESOLVED_REVISION.value
    ] * 2


# ------------------------------------------------------------------ P1-4 review semantics
def _quarantined(tmp_path: Path):
    market = make_market(tmp_path)
    append_through(market, 9)
    binding = MarketBindingSession(market).bind(
        "PAPER_OBSERVATION", "r", (("MARK_CLOSE", "BBB", SESS[9], SESS[5]),)
    )
    revise(market, "BBB", 8, 9)
    qualifier = EvidenceQualifier(market)
    return market, binding, qualifier, qualifier.qualify(((binding, None),))


def _events(connection):
    return [
        {"state": r[0], "basis_identity": r[1]}
        for r in connection.execute("SELECT state,basis_identity FROM forward_qualification_events ORDER BY event_id")
    ]


def test_reject_then_accept_cannot_clear_a_sticky_rejection(tmp_path: Path) -> None:
    _market, binding, qualifier, current = _quarantined(tmp_path)
    connection = sqlite3.connect(":memory:")
    ensure_binding_schema(connection, "forward")
    kw = dict(subject_kind="K", subject_ref="r", reviewer="rev", current=current)
    record_review(connection, "forward", decision="REJECT", reason="bad data", **kw)
    record_review(connection, "forward", decision="ACCEPT", reason="second thoughts", **kw)
    assert qualifier.qualify(((binding, None),), reviews=_events(connection)).state == (
        Qualification.REVIEWED_REJECTED.value
    )
    with pytest.raises(MarketBindingError, match="no active rejection"):
        record_review(connection, "forward", decision="SUPERSEDE_REJECT", reason="x",
                      **dict(kw, subject_ref="never-rejected"))
    record_review(connection, "forward", decision="SUPERSEDE_REJECT", reason="rejection was mistaken", **kw)
    record_review(connection, "forward", decision="ACCEPT", reason="re-reviewed", **kw)
    assert qualifier.qualify(((binding, None),), reviews=_events(connection)).state == (
        Qualification.REVIEWED_ACCEPTED.value
    )
    record_review(connection, "forward", decision="REJECT", reason="rejected again", **kw)
    assert qualifier.qualify(((binding, None),), reviews=_events(connection)).state == (
        Qualification.REVIEWED_REJECTED.value
    )


def test_missing_provenance_can_never_be_accepted_into_qualified_evidence(tmp_path: Path) -> None:
    market = make_market(tmp_path, register=False)
    binding = MarketBindingSession(market).bind("FORMATION", "m", (("FORMATION_CLOSE", "AAA", SESS[3]),))
    assert binding["log_state"] == LOG_MISSING
    qualifier = EvidenceQualifier(market)
    current = qualifier.qualify(((binding, None),))
    assert current.state == Qualification.QUARANTINED_MISSING_PROVENANCE.value
    connection = sqlite3.connect(":memory:")
    ensure_binding_schema(connection, "forward")
    kw = dict(subject_kind="K", subject_ref="m", reviewer="rev", current=current)
    with pytest.raises(MarketBindingError, match="cannot be accepted"):
        record_review(connection, "forward", decision="ACCEPT", reason="trust me", **kw)
    assert record_review(connection, "forward", decision="ACKNOWLEDGE", reason="seen, still unproven", **kw)
    # even a forged / replayed ACCEPT event for the exact basis does not qualify it
    forged = [{"state": "REVIEWED_ACCEPTED", "basis_identity": current.basis_identity}]
    for reviews in (_events(connection), forged):
        verdict = qualifier.qualify(((binding, None),), reviews=reviews)
        assert verdict.state == Qualification.QUARANTINED_MISSING_PROVENANCE.value and not verdict.qualified
    legacy = qualifier.qualify(((None, None),))
    with pytest.raises(MarketBindingError):
        record_review(connection, "forward", decision="ACCEPT", reason="x", **dict(kw, current=legacy))


def test_accepted_basis_is_exact_and_a_later_revision_invalidates_it(tmp_path: Path) -> None:
    market, binding, qualifier, current = _quarantined(tmp_path)
    connection = sqlite3.connect(":memory:")
    ensure_binding_schema(connection, "forward")
    record_review(connection, "forward", subject_kind="K", subject_ref="r", decision="ACCEPT",
                  reviewer="rev", reason="checked the provider note", current=current)
    events = _events(connection)
    assert qualifier.qualify(((binding, None),), reviews=events).state == Qualification.REVIEWED_ACCEPTED.value
    # the acceptance names this exact binding: a different evidence binding does not inherit it
    other = MarketBindingSession(market).bind("PAPER_OBSERVATION", "other", (("MARK_CLOSE", "BBB", SESS[9], SESS[5]),))
    assert qualifier.qualify(((other, None),), reviews=events).state == Qualification.QUARANTINED_UNRESOLVED_REVISION.value
    revise(market, "BBB", 7, 9)  # a new, contradictory revision (same qualifier object, no manual rebuild)
    assert qualifier.qualify(((binding, None),), reviews=events).state == Qualification.QUARANTINED_UNRESOLVED_REVISION.value


# ------------------------------------------------------------------ P1-5 append-only hardening
def test_binding_and_event_tables_reject_replace_update_and_delete_with_default_sqlite(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    append_through(market, 5)
    session = MarketBindingSession(market)
    binding = session.bind("FORMATION", "f1", (("FORMATION_CLOSE", "AAA", SESS[5]),))
    other = session.bind("FORMATION", "f1", (("FORMATION_CLOSE", "BBB", SESS[5]),))
    connection = sqlite3.connect(tmp_path / "evidence.db")
    assert connection.execute("PRAGMA recursive_triggers").fetchone()[0] == 0  # default: not relied upon
    ensure_binding_schema(connection, "forward")
    ensure_binding_schema(connection, "forward")  # idempotent

    assert insert_binding(connection, "forward", binding) is True
    assert insert_binding(connection, "forward", binding) is False  # equivalent retry: no-op
    with pytest.raises(MarketBindingError, match="conflicting"):
        insert_binding(connection, "forward", other)

    def raw_replace(identity: str, subject_ref: str):
        return connection.execute(
            "INSERT OR REPLACE INTO forward_market_bindings(binding_identity,subject_kind,subject_ref,"
            "dataset_version_id,log_state,payload_json,bound_at_utc) VALUES (?,?,?,?,?,?,?)",
            (identity, "FORMATION", subject_ref, "x", "BOUND", "{}", "t"),
        )

    with pytest.raises(sqlite3.DatabaseError, match="no replace"):
        raw_replace(binding["binding_identity"], "f1")  # same key
    with pytest.raises(sqlite3.DatabaseError, match="no replace"):
        raw_replace("mbind-other", "f1")  # same subject, different identity
    with pytest.raises(sqlite3.DatabaseError, match="no replace"):
        connection.execute("REPLACE INTO forward_market_bindings SELECT * FROM forward_market_bindings")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        connection.execute("UPDATE forward_market_bindings SET log_state='X'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        connection.execute("DELETE FROM forward_market_bindings")
    assert read_stored(connection)["payload_json"] != "{}"

    verdict = EvidenceQualifier(market).qualify(((binding, None),))
    append_event = lambda: record_review(  # noqa: E731
        connection, "forward", subject_kind="FORMATION", subject_ref="f1", decision="ACKNOWLEDGE",
        reviewer="r", reason="seen", current=verdict,
    )
    assert append_event() is True
    with pytest.raises(sqlite3.DatabaseError, match="no replace"):
        connection.execute(
            "INSERT OR REPLACE INTO forward_qualification_events(event_id,subject_kind,subject_ref,state,"
            "reasons_json,basis_identity,source,recorded_at_utc) VALUES (1,'K','r','REVIEWED_ACCEPTED','[]','b','REVIEW','t')"
        )
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        connection.execute("UPDATE forward_qualification_events SET state='X'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        connection.execute("DELETE FROM forward_qualification_events")
    assert connection.execute("SELECT state FROM forward_qualification_events").fetchall() == [("REVIEW_ACKNOWLEDGED",)]


def read_stored(connection):
    cursor = connection.execute("SELECT payload_json FROM forward_market_bindings")
    return {"payload_json": cursor.fetchone()[0]}


def test_the_real_ledgers_inherit_the_no_replace_protection(tmp_path: Path, forward_env) -> None:
    key = ("FORMATION", forward_env["formation"].formation_identity)
    stored = forward_env["ledger"].market_bindings()[key]
    with sqlite3.connect(forward_env["ledger"].path) as connection:
        with pytest.raises(sqlite3.DatabaseError, match="no replace"):
            connection.execute(
                "INSERT OR REPLACE INTO forward_market_bindings(binding_identity,subject_kind,subject_ref,"
                "dataset_version_id,log_state,payload_json,bound_at_utc) VALUES (?,?,?,?,?,?,?)",
                (stored["binding_identity"], key[0], key[1], "forged", "BOUND", "{}", "t"),
            )
    assert forward_env["ledger"].market_bindings()[key] == stored
    evidence = _prospective_pair(tmp_path)
    evidence.initialize()
    with sqlite3.connect(evidence.database_path) as connection:
        triggers = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert {"prospective_market_bindings_no_replace", "prospective_qualification_events_no_replace"} <= triggers


# ------------------------------------------------------------------ P1-6 stale qualifier
def test_a_reused_qualifier_sees_a_block_created_after_it_was_built(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    append_through(market, 9)
    session = MarketBindingSession(market)
    bbb = session.bind("PAPER_OBSERVATION", "bbb", (("MARK_CLOSE", "BBB", SESS[9], SESS[5]),))
    aaa = session.bind("PAPER_OBSERVATION", "aaa", (("MARK_CLOSE", "AAA", SESS[9], SESS[5]),))
    qualifier = EvidenceQualifier(market)  # built once, reused below
    assert qualifier.qualify(((bbb, None),)).qualified and qualifier.blocks == []

    revise(market, "AAA", 8, 9)  # an UNRELATED symbol gets a block
    assert qualifier.qualify(((bbb, None),)).state == Qualification.PROVENANCE_VERIFIED.value
    revise(market, "BBB", 8, 9)  # the relevant block: no manual rebuild by the caller
    assert qualifier.qualify(((bbb, None),)).state == Qualification.QUARANTINED_UNRESOLVED_REVISION.value
    assert qualifier.qualify(((aaa, None),)).state == Qualification.QUARANTINED_UNRESOLVED_REVISION.value


def test_a_qualifier_built_before_the_log_existed_sees_it_later(tmp_path: Path) -> None:
    market = make_market(tmp_path, register=False)
    qualifier = EvidenceQualifier(market)
    assert not qualifier.available
    open_observation_log(market).ensure_initial_baseline(market, now=NOW)
    append_through(market, 5)
    binding = MarketBindingSession(market).bind("FORMATION", "b", (("FORMATION_CLOSE", "AAA", SESS[5]),))
    assert qualifier.qualify(((binding, None),)).state == Qualification.PROVENANCE_VERIFIED.value and qualifier.available


def test_report_consumers_revalidate_a_reused_qualifier_on_every_call(tmp_path: Path) -> None:
    from analysis.paper_performance import calculate_paper_performance, load_closed_trades_frame

    market = make_market(tmp_path)
    append_through(market, 10)
    paper = tmp_path / "paper.db"
    _paper_with_evidence(paper, market, mark=None, trade_legs=True)
    qualifier = EvidenceQualifier(market)  # the report keeps one qualifier object

    def qualified_trades() -> int:
        return len(load_closed_trades_frame(paper, qualifier=qualifier, qualification_required=True))

    assert qualified_trades() == 1
    assert calculate_paper_performance(paper, qualifier=qualifier, qualification_required=True).total_trades == 1
    revise(market, "AAA", 9, 10)  # unrelated symbol: the trade stays qualified
    assert qualified_trades() == 1
    revise(market, "BBB", 8, 10)  # the traded symbol's exit session
    assert qualified_trades() == 0
    assert calculate_paper_performance(paper, qualifier=qualifier, qualification_required=True).total_trades == 0
    labelled = load_closed_trades_frame(paper, qualifier=qualifier)
    assert labelled["provenance_qualification"].tolist() == [Qualification.QUARANTINED_UNRESOLVED_REVISION.value]


def test_daily_forward_with_a_fully_observed_history_may_verify(tmp_path: Path, protocol, monkeypatch) -> None:
    if not PHASE8_ROOT.exists():
        pytest.skip("Phase 8 synthesis artifacts are not present in this checkout")
    monkeypatch.setattr(
        "core.universe.get_vn100_symbols",
        lambda: (_ for _ in ()).throw(AssertionError("network universe path used")),
    )
    market = make_market(tmp_path, sessions=DSESS, symbols=("ZZZ",), baseline_count=1)  # legacy baseline: ZZZ only
    for symbol in DSYMS:  # every consumed session arrives through the admission guard
        outcome = admit_price_batch(
            _frame(symbol, [bar(symbol, DSESS, i) for i in range(118)]),
            symbol=symbol, context=_context(DSESS[0], DSESS[117]), market_db_path=market, now=NOW,
        )
        assert outcome.result is AdmissionResult.ADMITTED_INITIAL_LOAD
    ledger_path = tmp_path / "forward.db"
    activation = create_activation(
        protocol, activated_at_utc="2026-09-27T12:00:00Z", confirmed_latest_completed_session=DSESS[116]
    )
    ForwardValidationLedger(ledger_path).activate(activation)
    assert _daily_run(market, ledger_path).formation_created
    _append_daily(market, 122)
    _daily_run(market, ledger_path, "2026-10-02T12:00:00Z")

    ledger = ForwardValidationLedger(ledger_path)
    stored, qualifier = ledger.market_bindings(), EvidenceQualifier(market)
    outcomes = ledger.outcomes(protocol.protocol_id)
    assert outcomes
    formation = ledger.formations(protocol.protocol_id)[0]
    window = next(d for d in stored[("FORMATION", formation.formation_identity)]["dependencies"] if d["coverage"] == "WINDOW")
    assert set(window["lineage"]["origin_counts"]) == {"OBSERVATION"}
    for item in outcomes:
        verdict = qualifier.qualify(
            (
                (stored[("FORMATION", item.formation_identity)], {item.symbol, "VNINDEX"}),
                (stored[("OUTCOME", item.outcome_identity)], None),
            )
        )
        assert verdict.state == Qualification.PROVENANCE_VERIFIED.value
    # and a revision of an old session in a selected symbol's consumed history quarantines it
    victim = formation.positions[0].symbol
    revise(market, victim, 100, 122, sessions=DSESS, first_index=0)
    item = next(o for o in outcomes if o.symbol == victim)
    assert qualifier.qualify(
        ((stored[("FORMATION", item.formation_identity)], {item.symbol, "VNINDEX"}),)
    ).state == Qualification.QUARANTINED_UNRESOLVED_REVISION.value


# =====================================================================================
# Revision 3: the three remaining P1s (review laundering, full-chain, VNINDEX entry history)
# =====================================================================================
from core.evidence_market_binding import (  # noqa: E402
    ACCEPT_REQUIRED_BASE_STATE,
    INCOMPLETE_CHAIN_STATE,
    compact_leg,
    leg_binding,
    trade_leg_symbols,
)


def _review_connection():
    connection = sqlite3.connect(":memory:")
    ensure_binding_schema(connection, "forward")
    return connection


def _blocked_over(tmp_path: Path, extra: tuple, *, legacy_part: bool = False):
    """A binding with an unresolved BBB revision inside its window plus ``extra`` dependencies."""
    market = make_market(tmp_path)
    append_through(market, 9)
    session = MarketBindingSession(market)
    binding = session.bind(
        "PAPER_OBSERVATION", "r", (("MARK_CLOSE", "BBB", SESS[9], SESS[5]), *extra)
    )
    revise(market, "BBB", 8, 9)
    qualifier = EvidenceQualifier(market)
    parts = [(binding, None)] + ([(None, None)] if legacy_part else [])
    return market, qualifier, parts


def _assert_accept_cannot_launder(qualifier, parts, *, expected_base: str) -> None:
    current = qualifier.qualify(parts)
    assert current.state == Qualification.QUARANTINED_UNRESOLVED_REVISION.value
    assert current.underlying_base_state == expected_base != ACCEPT_REQUIRED_BASE_STATE
    connection = _review_connection()
    kw = dict(subject_kind="K", subject_ref="r", reviewer="rev", current=current)
    with pytest.raises(MarketBindingError, match="cannot be accepted"):
        record_review(connection, "forward", decision="ACCEPT", reason="looks fine", **kw)
    # a forged / replayed ACCEPT for the exact basis does not qualify it either
    forged = [{"state": "REVIEWED_ACCEPTED", "basis_identity": current.basis_identity}]
    verdict = qualifier.qualify(parts, reviews=forged)
    assert not verdict.qualified and verdict.excluded
    assert verdict.state == Qualification.QUARANTINED_UNRESOLVED_REVISION.value


def test_accept_cannot_launder_bound_legacy_input_with_an_unresolved_block(tmp_path: Path) -> None:
    legacy_dep = (("FORMATION_CLOSE", "BBB", SESS[3]),)  # a registered LEGACY baseline session
    _market, qualifier, parts = _blocked_over(tmp_path, legacy_dep)
    _assert_accept_cannot_launder(qualifier, parts, expected_base=Qualification.BOUND_LEGACY_INPUT.value)
    # without the block the same binding is merely BOUND_LEGACY_INPUT: the block never made it better
    assert qualifier.qualify(parts).reasons  # carries the block AND the legacy-baseline reason
    assert "DEPENDENCY_ON_LEGACY_BASELINE_SESSION" in qualifier.qualify(parts).reasons


def test_accept_cannot_launder_legacy_unbound_evidence_with_an_unresolved_block(tmp_path: Path) -> None:
    _market, qualifier, parts = _blocked_over(tmp_path, (), legacy_part=True)
    _assert_accept_cannot_launder(qualifier, parts, expected_base=Qualification.LEGACY_UNBOUND.value)


def test_accept_cannot_substitute_for_missing_or_incompatible_provenance_under_a_block(tmp_path: Path) -> None:
    market = make_market(tmp_path)
    append_through(market, 9)
    session = MarketBindingSession(market)
    binding = session.bind("PAPER_OBSERVATION", "i", (("MARK_CLOSE", "BBB", SESS[9], SESS[5]),
                                                       ("FORMATION_FEATURE_HISTORY", "AAA", SESS[9], SESS[0], "WINDOW")))
    forged = json.loads(json.dumps(binding))
    next(d for d in forged["dependencies"] if d["coverage"] == "WINDOW")["lineage"]["digest"] = "0" * 64
    forged["binding_identity"] = binding_identity(forged)
    missing = session.bind("PAPER_OBSERVATION", "m", (("MARK_CLOSE", "BBB", SESS[9], SESS[5]),
                                                       ("STORED_MARK_UNVERIFIED", "BBB", SESS[9], None, COVERAGE_UNPROVEN)))
    revise(market, "BBB", 8, 9)
    qualifier = EvidenceQualifier(market)
    for candidate, state in (
        (forged, Qualification.QUARANTINED_INCOMPATIBLE_BASIS.value),
        (missing, Qualification.QUARANTINED_MISSING_PROVENANCE.value),
    ):
        current = qualifier.qualify(((candidate, None),))
        assert current.state == state and current.underlying_base_state == state
        with pytest.raises(MarketBindingError, match="cannot be accepted"):
            record_review(_review_connection(), "forward", subject_kind="K", subject_ref="x",
                          decision="ACCEPT", reviewer="rev", reason="trust me", current=current)
        forged_accept = [{"state": "REVIEWED_ACCEPTED", "basis_identity": current.basis_identity}]
        assert qualifier.qualify(((candidate, None),), reviews=forged_accept).state == state


def test_accept_still_overrides_an_unresolved_block_over_provenance_verified_evidence(tmp_path: Path) -> None:
    _market, qualifier, parts = _blocked_over(tmp_path, ())
    current = qualifier.qualify(parts)
    assert current.state == Qualification.QUARANTINED_UNRESOLVED_REVISION.value
    assert current.underlying_base_state == Qualification.PROVENANCE_VERIFIED.value
    connection = _review_connection()
    record_review(connection, "forward", subject_kind="K", subject_ref="r", decision="ACCEPT",
                  reviewer="rev", reason="checked the provider note", current=current)
    verdict = qualifier.qualify(parts, reviews=_events(connection))
    assert verdict.state == Qualification.REVIEWED_ACCEPTED.value and verdict.qualified


def test_a_later_relevant_revision_invalidates_a_prior_accept_and_reject_stays_sticky(tmp_path: Path) -> None:
    market, qualifier, parts = _blocked_over(tmp_path, ())
    current = qualifier.qualify(parts)
    connection = _review_connection()
    kw = dict(subject_kind="K", subject_ref="r", reviewer="rev", current=current)
    record_review(connection, "forward", decision="ACCEPT", reason="checked", **kw)
    assert qualifier.qualify(parts, reviews=_events(connection)).state == Qualification.REVIEWED_ACCEPTED.value
    revise(market, "BBB", 7, 9)  # a new relevant revision: the accepted basis no longer holds
    after = qualifier.qualify(parts, reviews=_events(connection))
    assert after.state == Qualification.QUARANTINED_UNRESOLVED_REVISION.value and not after.qualified
    # REJECT remains sticky and applies even when the evidence is not acceptable at all
    record_review(connection, "forward", decision="REJECT", reason="bad data", **dict(kw, current=after))
    record_review(connection, "forward", decision="ACCEPT", reason="again", **dict(kw, current=after))
    assert qualifier.qualify(parts, reviews=_events(connection)).state == Qualification.REVIEWED_REJECTED.value
    legacy_current = qualifier.qualify(((None, None),))
    other = _review_connection()
    assert record_review(other, "forward", subject_kind="K", subject_ref="l", decision="REJECT",
                         reviewer="rev", reason="no", current=legacy_current)


# ------------------------------------------------------------------ P1-B full chain
def _chain_bindings(market: Path):
    """obs1 realizes a BBB trade (entry/exit lineage); obs2 carries it cumulatively."""
    session = MarketBindingSession(market)
    entry = session.bind("PAPER_ENTRY", "pending_signal:2",
                         (("ENTRY_OPEN", "BBB", SESS[5]), ("SIGNAL_REFERENCE", "BBB", SESS[4])))
    exit_ = session.bind("PAPER_EXIT", "paper_exit:bbb", (("EXIT_PRICE", "BBB", SESS[8], SESS[5]),))
    trade = {
        "closed_trade_id": 1, "symbol": "BBB", "exit_order_id": "exit-bbb", "entry_order_id": "entry-bbb",
        "entry": compact_leg(entry), "exit": compact_leg(exit_),
    }
    obs1 = session.bind(
        "PAPER_OBSERVATION", "obs1", (("BENCHMARK_CLOSE", "VNINDEX", SESS[8]),),
        lineage={"mark_checks": [], "closed_trade_count": 1, "tracked_trade_count": 1,
                 "trades_added": [trade], "open_entries": []},
    )
    obs2 = session.bind(
        "PAPER_OBSERVATION", "obs2", (("BENCHMARK_CLOSE", "VNINDEX", SESS[9]),),
        lineage={"mark_checks": [], "closed_trade_count": 1, "tracked_trade_count": 1,
                 "trades_added": [], "open_entries": []},
    )
    return obs1, obs2


def _chain_market(tmp_path: Path) -> Path:
    market = make_market(tmp_path)
    append_through(market, 9)
    return market


def test_full_chain_qualification_quarantines_a_later_observation_through_an_earlier_trade(tmp_path: Path) -> None:
    market = _chain_market(tmp_path)
    obs1, obs2 = _chain_bindings(market)
    qualifier = EvidenceQualifier(market)
    assert [r.state for r in qualifier.qualify_observation_series([obs1, obs2])] == [
        Qualification.PROVENANCE_VERIFIED.value
    ] * 2
    revise(market, "BBB", 8, 9)  # the realized trade's exit session
    states = [r.state for r in qualifier.qualify_observation_series([obs1, obs2])]
    assert states == [Qualification.QUARANTINED_UNRESOLVED_REVISION.value] * 2  # Case A


def test_a_truncated_series_fails_closed_instead_of_verifying_a_later_observation(tmp_path: Path) -> None:
    market = _chain_market(tmp_path)
    obs1, obs2 = _chain_bindings(market)
    revise(market, "BBB", 8, 9)
    qualifier = EvidenceQualifier(market)
    (alone,) = qualifier.qualify_observation_series([obs2])  # Case C: prior tracked trade is missing
    assert alone.state == Qualification.QUARANTINED_MISSING_PROVENANCE.value and not alone.qualified
    assert any(INCOMPLETE_CHAIN_STATE in reason for reason in alone.reasons)
    # even with NO revision, a truncated chain is never verified (the lineage is unproven)
    (tmp_path / "clean").mkdir()
    clean_market = _chain_market(tmp_path / "clean")
    c1, c2 = _chain_bindings(clean_market)
    (clean_alone,) = EvidenceQualifier(clean_market).qualify_observation_series([c2])
    assert not clean_alone.qualified and INCOMPLETE_CHAIN_STATE in " ".join(clean_alone.reasons)
    # and no review can launder an incomplete chain
    forged = [{"state": "REVIEWED_ACCEPTED", "basis_identity": alone.basis_identity}]
    assert not qualifier.qualify_observation_series([obs2], reviews=[forged])[0].qualified


def test_a_complete_single_observation_chain_still_qualifies_and_unrelated_revisions_do_not_quarantine(
    tmp_path: Path,
) -> None:
    market = _chain_market(tmp_path)
    session = MarketBindingSession(market)
    only = session.bind(
        "PAPER_OBSERVATION", "only", (("BENCHMARK_CLOSE", "VNINDEX", SESS[9]),),
        lineage={"mark_checks": [], "closed_trade_count": 0, "tracked_trade_count": 0,
                 "trades_added": [], "open_entries": []},
    )
    obs1, obs2 = _chain_bindings(market)
    qualifier = EvidenceQualifier(market)
    assert qualifier.qualify_observation_series([only])[0].state == Qualification.PROVENANCE_VERIFIED.value  # Case D
    revise(market, "AAA", 8, 9)  # a symbol no trade touched
    assert [r.state for r in qualifier.qualify_observation_series([obs1, obs2])] == [
        Qualification.PROVENANCE_VERIFIED.value
    ] * 2
    assert qualifier.qualify_observation_series([only])[0].state == Qualification.PROVENANCE_VERIFIED.value


def test_consumers_qualify_the_complete_chain_before_applying_the_date_window(tmp_path: Path) -> None:
    from quantctl.forward_evidence import inspect_forward_evidence_catalog
    from quantlab.evidence import ProspectivePortfolioEvidenceLedger
    from tests.test_forward_evidence_view import _paper_record, _root_with_evidence

    root, paper, _forward = _root_with_evidence(tmp_path)
    market = make_market(tmp_path)
    append_through(market, 9)
    evidence = ProspectivePortfolioEvidenceLedger(root / "data" / "prospective_portfolio_evidence.db")
    first = _paper_record(paper_path=paper, session="2026-01-02", equity=100.0)
    second = _paper_record(paper_path=paper, session="2026-01-03", equity=101.0)
    session = MarketBindingSession(market)
    entry = session.bind("PAPER_ENTRY", "pending_signal:2",
                         (("ENTRY_OPEN", "BBB", SESS[5]), ("SIGNAL_REFERENCE", "BBB", SESS[4])))
    exit_ = session.bind("PAPER_EXIT", "paper_exit:bbb", (("EXIT_PRICE", "BBB", SESS[8], SESS[5]),))
    trade = {"closed_trade_id": 1, "symbol": "BBB", "exit_order_id": "x", "entry_order_id": "e",
             "entry": compact_leg(entry), "exit": compact_leg(exit_)}
    lineage = lambda added: {  # noqa: E731
        "mark_checks": [], "closed_trade_count": 1, "tracked_trade_count": 1,
        "trades_added": added, "open_entries": [],
    }
    evidence.append(first, market_binding=session.bind(
        "PAPER_OBSERVATION", evidence.evidence_key(first), (("BENCHMARK_CLOSE", "VNINDEX", SESS[8]),),
        lineage=lineage([trade])))
    evidence.append(second, market_binding=session.bind(
        "PAPER_OBSERVATION", evidence.evidence_key(second), (("BENCHMARK_CLOSE", "VNINDEX", SESS[9]),),
        lineage=lineage([])))

    def points(**window):
        catalog = inspect_forward_evidence_catalog(
            root=root, environ={"PAPER_V2_DATABASE_PATH": str(paper)}, market_database_path=market, **window
        )
        return {p.session: p.market_data_qualification for p in catalog.paper.points}

    verified = Qualification.PROVENANCE_VERIFIED.value
    quarantined = Qualification.QUARANTINED_UNRESOLVED_REVISION.value
    assert points() == {"2026-01-02": verified, "2026-01-03": verified}
    revise(market, "BBB", 8, 9)  # the earlier observation's realized trade exit session
    assert points() == {"2026-01-02": quarantined, "2026-01-03": quarantined}
    # Case B: the date window only filters what is shown; it never shortens the qualified chain
    assert points(start_date="2026-01-03") == {"2026-01-03": quarantined}
    assert points(end_date="2026-01-02") == {"2026-01-02": quarantined}


# ------------------------------------------------------------------ P1-C VNINDEX entry history
def _entry_binding(tmp_path: Path, *, history: str, index_history: str):
    paper_db = tmp_path / "paper.db"
    market = _paper_market(tmp_path, with_log=True, history=history, index_history=index_history)
    executor = _executor(paper_db)
    _queue(executor)
    result = executor.execute_pending_signals(valuation_date=EXECUTION_DATE, market_database_path=market)
    assert result.executions[0].status == "FILLED"
    (order,) = _paper_orders(paper_db)
    return paper_db, market, json.loads(order["execution_context"])["market_binding"]


def _revise_vnindex_session(market: Path, day: str) -> None:
    hist = _weekdays_before(SIGNAL_DATE - timedelta(days=1), 19)
    bars = [(d, 1300.0, 1310.0, 1290.0, 1300.0, 1_000_000) for d in hist]
    bars.append((SIGNAL_DATE.isoformat(), 1300.0, 1310.0, 1290.0, 1300.0, 1_000_000))
    position = ([*hist, SIGNAL_DATE.isoformat()]).index(day)
    bars[position] = (day, 1000.0, 1010.0, 990.0, 1000.0, 1_000_000)
    outcome = admit_price_batch(
        _frame("VNINDEX", bars), symbol="VNINDEX", context=_context(bars[0][0], bars[-1][0]),
        market_db_path=market, now=NOW,
    )
    assert outcome.result is AdmissionResult.BLOCKED_REVISION_CHANGED


def test_entry_binding_carries_the_full_consumed_vnindex_history(tmp_path: Path) -> None:
    _paper, market, binding = _entry_binding(tmp_path, history="observed", index_history="observed")
    deps = {d["role"]: d for d in binding["dependencies"]}
    regime = deps["REGIME_RS_HISTORY"]
    assert (regime["symbol"], regime["coverage"], regime["session"]) == ("VNINDEX", "WINDOW", SIGNAL_DATE.isoformat())
    # get_market_regime reads ALL VNINDEX history up to the signal date: no shorter window is invented
    assert regime["window_start"] == MarketBindingSession(market).first_session("VNINDEX")
    assert regime["lineage"]["session_count"] == 20  # 19 history sessions + the signal session


@pytest.mark.parametrize(
    ("history", "index_history", "expected"),
    (
        ("observed", "legacy", Qualification.BOUND_LEGACY_INPUT.value),  # observed symbol + legacy VNINDEX
        ("legacy", "observed", Qualification.BOUND_LEGACY_INPUT.value),
        ("observed", "observed", Qualification.PROVENANCE_VERIFIED.value),  # both observed
    ),
)
def test_entry_qualification_depends_on_both_symbol_and_vnindex_history(
    tmp_path: Path, history: str, index_history: str, expected: str
) -> None:
    _paper, market, binding = _entry_binding(tmp_path, history=history, index_history=index_history)
    verdict = EvidenceQualifier(market).qualify(((binding, trade_leg_symbols("AAA")),))
    assert verdict.state == expected
    assert verdict.qualified == (expected == Qualification.PROVENANCE_VERIFIED.value)


def test_a_vnindex_revision_inside_the_consumed_window_quarantines_the_entry_but_outside_does_not(
    tmp_path: Path,
) -> None:
    _paper, market, binding = _entry_binding(tmp_path, history="observed", index_history="observed")
    qualifier = EvidenceQualifier(market)
    assert qualifier.qualify(((binding, trade_leg_symbols("AAA")),)).qualified
    _revise_vnindex_session(market, _weekdays_before(SIGNAL_DATE - timedelta(days=1), 19)[3])
    verdict = qualifier.qualify(((binding, trade_leg_symbols("AAA")),))
    assert verdict.state == Qualification.QUARANTINED_UNRESOLVED_REVISION.value and verdict.excluded
    assert any(":VNINDEX:" in reason and "REGIME_RS_HISTORY" in reason for reason in verdict.reasons)
    # the narrower traded-symbol-only filter (the defect) would have missed it
    assert qualifier.qualify(((binding, {"AAA"}),)).state == Qualification.PROVENANCE_VERIFIED.value


def test_a_vnindex_revision_outside_the_consumed_window_does_not_quarantine(tmp_path: Path) -> None:
    from core.evidence_market_binding import COVERAGE_WINDOW

    market = make_market(tmp_path)
    append_through(market, 9)
    session = MarketBindingSession(market)
    # a consumed VNINDEX window that ends at SESS[6]
    binding = session.bind("PAPER_ENTRY", "w", (("REGIME_RS_HISTORY", "VNINDEX", SESS[6], SESS[0], COVERAGE_WINDOW),))
    revise(market, "VNINDEX", 8, 9)  # after the window
    assert EvidenceQualifier(market).qualify(((binding, trade_leg_symbols("AAA")),)).state in {
        Qualification.BOUND_LEGACY_INPUT.value, Qualification.PROVENANCE_VERIFIED.value,
    }
    assert not EvidenceQualifier(market).qualify(((binding, trade_leg_symbols("AAA")),)).excluded


def test_closed_trade_and_observation_qualifiers_retain_the_vnindex_dependency(tmp_path: Path) -> None:
    from execution.paper_provenance import qualify_closed_trades

    market = make_market(tmp_path)
    append_through(market, 9)
    session = MarketBindingSession(market)
    # entry leg that consumed VNINDEX history; exit leg for the traded symbol only
    entry = session.bind(
        "PAPER_ENTRY", "pending_signal:2",
        (("ENTRY_OPEN", "BBB", SESS[5]), ("SIGNAL_REFERENCE", "BBB", SESS[4]),
         ("REGIME_RS_HISTORY", "VNINDEX", SESS[4], SESS[0], "WINDOW")),
    )
    exit_ = session.bind("PAPER_EXIT", "paper_exit:bbb", (("EXIT_PRICE", "BBB", SESS[8], SESS[5]),))

    # (5) qualify_closed_trades on a paper store whose entry order carries that binding
    paper = tmp_path / "closed.db"
    _paper_with_evidence(paper, market, mark=None, trade_legs=True)
    with sqlite3.connect(paper) as connection:
        ctx = json.loads(connection.execute("SELECT execution_context FROM paper_orders WHERE client_order_id='entry-bbb'").fetchone()[0])
        ctx["market_binding"] = entry
        connection.execute("UPDATE paper_orders SET execution_context=? WHERE client_order_id='entry-bbb'", (json.dumps(ctx),))
        xctx = json.loads(connection.execute("SELECT execution_context FROM paper_orders WHERE client_order_id='exit-bbb'").fetchone()[0])
        xctx["market_binding"] = exit_
        connection.execute("UPDATE paper_orders SET execution_context=? WHERE client_order_id='exit-bbb'", (json.dumps(xctx),))
    qualifier = EvidenceQualifier(market)
    assert qualify_closed_trades(paper, qualifier)["exit-bbb"].state == Qualification.BOUND_LEGACY_INPUT.value
    revise(market, "VNINDEX", 2, 9)  # inside the VNINDEX window the entry consumed
    verdict = qualify_closed_trades(paper, qualifier)["exit-bbb"]
    assert verdict.state == Qualification.QUARANTINED_UNRESOLVED_REVISION.value
    assert any(":VNINDEX:" in reason for reason in verdict.reasons)

    # (6) observation-series qualification retains the same dependency through the cumulative chain
    (tmp_path / "second").mkdir()
    market2 = _chain_market(tmp_path / "second")
    session2 = MarketBindingSession(market2)
    entry2 = session2.bind(
        "PAPER_ENTRY", "pending_signal:2",
        (("ENTRY_OPEN", "BBB", SESS[5]), ("REGIME_RS_HISTORY", "VNINDEX", SESS[4], SESS[0], "WINDOW")),
    )
    exit2 = session2.bind("PAPER_EXIT", "paper_exit:bbb", (("EXIT_PRICE", "BBB", SESS[8], SESS[5]),))
    obs = session2.bind(
        "PAPER_OBSERVATION", "o", (("BENCHMARK_CLOSE", "VNINDEX", SESS[9]),),
        lineage={"mark_checks": [], "closed_trade_count": 1, "tracked_trade_count": 1, "open_entries": [],
                 "trades_added": [{"closed_trade_id": 1, "symbol": "BBB", "exit_order_id": "x", "entry_order_id": "e",
                                   "entry": compact_leg(entry2), "exit": compact_leg(exit2)}]},
    )
    q2 = EvidenceQualifier(market2)
    assert q2.qualify_observation_series([obs])[0].state == Qualification.BOUND_LEGACY_INPUT.value
    revise(market2, "VNINDEX", 2, 9)
    state = q2.qualify_observation_series([obs])[0]
    assert state.state == Qualification.QUARANTINED_UNRESOLVED_REVISION.value
    assert any("REGIME_RS_HISTORY" in reason for reason in state.reasons)
