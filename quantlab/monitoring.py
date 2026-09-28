from __future__ import annotations

"""Read-only monitoring measurements for the frozen Quant Lab environment.

This module stops at evidence and status.  It has no alerting, trading-control,
or persistence-to-database behavior.
"""

from dataclasses import dataclass
from bisect import bisect_right
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from statistics import median
from types import MappingProxyType
from typing import Any, Mapping

from core.paths import resolve_market_database_path
from core.database_coverage import build_database_coverage_index
from quantlab.catalog import build_market_data_snapshot
from quantlab.evaluation import NEUTRAL_ADX_RSI_SELECTION_DIAGNOSTICS_V1, rank_panel_policy_candidates
from quantlab.features import FeatureRegistry, FeatureRequest, builtin_definitions
from quantlab.forward.protocol import load_protocol_spec, verify_phase8_authorization
from quantlab.identity import canonical_identity_value, canonical_json


CONTRACT = "quantlab.monitoring_snapshot"
VERSION = "v1"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REFERENCE_ROOT = PROJECT_ROOT / "research_results"
DEFAULT_LEDGER_PATH = PROJECT_ROOT / "data" / "forward_validation.db"
DEFAULT_PROTOCOL_PATH = PROJECT_ROOT / "research" / "forward_validation" / "protocol_v1.json"
DEFAULT_PHASE8_ROOT = PROJECT_ROOT / "research_results" / "quantlab_portfolio_research_synthesis_2018-08-07_2026-09-17"
PHASE6_ROOT = PROJECT_ROOT / "research_results" / "quantlab_portfolio_construction_2018-08-07_2026-09-17"
PHASE10_ROOT = PROJECT_ROOT / "research_results" / "quantlab_portfolio_risk_2018-08-07_2026-09-17"
PHASE10D_ROOT = PROJECT_ROOT / "research_results" / "quantlab_risk_policy_decision_2018-08-07_2026-09-17"
PHASE11D_ROOT = PROJECT_ROOT / "research_results" / "quantlab_execution_provider_provenance_2018_2026"
PHASE11C_ROOT = PROJECT_ROOT / "research_results" / "quantlab_execution_price_provenance_2018_2026"
PHASE11A_ROOT = PROJECT_ROOT / "research_results" / "quantlab_execution_foundation"
PHASE11B_ROOT = PROJECT_ROOT / "research_results" / "quantlab_execution_timing_capacity_2018-08-07_2026-09-17"
PHASE11E_ROOT = PROJECT_ROOT / "research_results" / "quantlab_execution_friction_sensitivity_2018-08-07_2026-09-17"
PHASE5_ROOT = PROJECT_ROOT / "research_results" / "quantlab_neutral_panel_factor_evaluation_2018-08-07_2026-09-17"


class MonitoringStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    DESCRIPTIVE_DRIFT = "DESCRIPTIVE_DRIFT"


@dataclass(frozen=True, slots=True)
class MonitoringSpec:
    minimum_history_sessions: int = 50
    maximum_staleness_sessions: int = 5
    feature_request: str = "historical_candidate_per_symbol_subset@v2"
    feature_reference_semantics: str = "latest cross-section described; no frozen raw-feature distribution available"
    selection_policy: str = "ADX_ONLY"
    budgets: tuple[int, ...] = (5, 10, 20)
    portfolio_weighting: str = "EQUAL_WEIGHT"
    no_performance_monitoring: bool = True
    no_control_actions: bool = True
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if self.minimum_history_sessions < 0 or self.maximum_staleness_sessions < 0:
            raise ValueError("coverage thresholds must be non-negative")
        if self.budgets != (5, 10, 20) or self.selection_policy != "ADX_ONLY":
            raise ValueError("monitoring must reuse frozen ADX_ONLY budgets 5/10/20")
        payload = {
            "contract": CONTRACT, "version": VERSION,
            "coverage": (self.minimum_history_sessions, self.maximum_staleness_sessions),
            "feature_request": self.feature_request,
            "feature_reference_semantics": self.feature_reference_semantics,
            "selection_policy": self.selection_policy, "budgets": self.budgets,
            "weighting": self.portfolio_weighting,
            "comparison": "reference-periods explicit; distributional differences descriptive-only",
            "restrictions": ("no_profitability_judgment", "no_alert", "no_strategy_control", "read-only"),
        }
        object.__setattr__(self, "fingerprint", _hash(payload))


@dataclass(frozen=True, slots=True)
class MonitoringEvidence:
    dimension: str
    name: str
    status: MonitoringStatus
    observed: str | None
    reference: str | None
    comparison_semantics: str
    reason: str


@dataclass(frozen=True, slots=True)
class MonitoringSnapshot:
    contract: str
    version: str
    observed_at_utc: str
    observed_market_session: str | None
    market_database_path: str
    market_snapshot_id: str | None
    specification_fingerprint: str
    reference_identities: Mapping[str, str]
    evidence: tuple[MonitoringEvidence, ...]
    overall_status: MonitoringStatus
    limitations: tuple[str, ...]
    identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference_identities", MappingProxyType(dict(self.reference_identities)))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "limitations", tuple(self.limitations))


@dataclass(frozen=True, slots=True)
class MonitoringResult:
    snapshot: MonitoringSnapshot
    details: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))


def _hash(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"monitoring reference must be a JSON object: {path}")
    return value


def load_frozen_monitoring_references(reference_root: str | Path = DEFAULT_REFERENCE_ROOT) -> Mapping[str, str]:
    """Load and fail closed on the canonical Phase 6/8/9/10/11 reference chain."""
    root = Path(reference_root).resolve()
    phase6_path = PHASE6_ROOT / "portfolio_construction_manifest.json"
    phase8_path = DEFAULT_PHASE8_ROOT / "portfolio_research_synthesis_manifest.json"
    phase10_path = PHASE10_ROOT / "portfolio_risk_manifest.json"
    phase10d_path = PHASE10D_ROOT / "risk_policy_decision_manifest.json"
    phase11c_path = PHASE11C_ROOT / "execution_price_provenance_manifest.json"
    phase11d_path = PHASE11D_ROOT / "execution_provider_provenance_manifest.json"
    phase11a_path = PHASE11A_ROOT / "execution_contract_manifest.json"
    phase11b_path = PHASE11B_ROOT / "execution_timing_capacity_manifest.json"
    phase11e_path = PHASE11E_ROOT / "execution_friction_sensitivity_manifest.json"
    phase5_path = PHASE5_ROOT / "experiment_manifest.json"
    paths = {
        "phase5_neutral_factor": phase5_path,
        "phase6_portfolio_structure": phase6_path,
        "phase8_synthesis": phase8_path,
        "phase10_risk": phase10_path,
        "phase10d_risk_decision": phase10d_path,
        "phase11c_price_provenance": phase11c_path,
        "phase11d_provider_provenance": phase11d_path,
        "phase11a_execution_capabilities": phase11a_path,
        "phase11b_timing_capacity": phase11b_path,
        "phase11e_friction_sensitivity": phase11e_path,
    }
    # root is an explicit validation anchor; canonical files stay at their fixed locations.
    if root != DEFAULT_REFERENCE_ROOT.resolve():
        raise ValueError("reference_root must be the canonical research_results directory")
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise ValueError("canonical monitoring references missing: " + ", ".join(missing))
    manifests = {name: _read_json(path) for name, path in paths.items()}
    phase6, phase8 = manifests["phase6_portfolio_structure"], manifests["phase8_synthesis"]
    if phase6.get("result_identity") != "c23fa10a1b577509f1fa5d7a23d543dc63237b719c05269342ae5e7a6e660697":
        raise ValueError("canonical Phase 6 result identity mismatch")
    if phase8.get("result_identity") != "d433d139c27725421ab6a19fa28c62225040a816793bf63bfffee774b83f3ddc":
        raise ValueError("canonical Phase 8 result identity mismatch")
    phase8_sources = phase8.get("source_identities", {})
    if phase8_sources.get("phase6_result_identity") != phase6["result_identity"]:
        raise ValueError("Phase 8 does not reference the canonical Phase 6 result")
    if phase8_sources.get("phase6_manifest_sha256") != _file_sha256(phase6_path):
        raise ValueError("Phase 8 Phase 6 manifest checksum mismatch")
    phase10 = manifests["phase10_risk"]
    if phase10.get("source_phase6_result_identity") != phase6["result_identity"] or phase10.get("source_phase6_manifest_identity") != _file_sha256(phase6_path):
        raise ValueError("Phase 10 does not reference the canonical Phase 6 result/manifest")
    phase10d = manifests["phase10d_risk_decision"]
    if phase10d.get("source_identities", {}).get("phase10a_result_identity") != phase10.get("result_identity"):
        raise ValueError("Phase 10D does not reference the canonical Phase 10A result")
    protocol = load_protocol_spec(DEFAULT_PROTOCOL_PATH)
    verify_phase8_authorization(protocol, DEFAULT_PHASE8_ROOT)
    if protocol.activation_market_session_boundary != "2026-09-17":
        raise ValueError("Forward V1 activation boundary differs from frozen research boundary")
    phase11d = manifests["phase11d_provider_provenance"]
    if phase11d.get("result_identity") != "3bf2fd5184f2a02801ad4e6af17b5d7b2e5411bb0fd816fb67eec88e5551526e":
        raise ValueError("canonical Phase 11D result identity mismatch")
    if manifests["phase11c_price_provenance"].get("result_identity") != "4fb274958f452884fb6321245d3adaba011e052a8d05c34eb076f785cb7af9b0":
        raise ValueError("canonical Phase 11C result identity mismatch")
    if phase11d.get("phase11c", {}).get("result_identity") != manifests["phase11c_price_provenance"].get("result_identity"):
        raise ValueError("Phase 11D does not reference the canonical Phase 11C result")
    phase11a = manifests["phase11a_execution_capabilities"]
    phase11b = manifests["phase11b_timing_capacity"]
    phase11e = manifests["phase11e_friction_sensitivity"]
    if phase11a.get("result_identity") != "ae1ad7157c598172b335085ea60693bb3cb95ce1471a4a74982d0fdfed9737d7":
        raise ValueError("canonical Phase 11A result identity mismatch")
    if phase11b.get("result_identity") != "7363f62b313672b04fa5e2e83ab958bb103701d58ccd538ff500e3d8e093d669":
        raise ValueError("canonical Phase 11B result identity mismatch")
    if phase11e.get("result_identity") != "79d49d602146e6baab87edd32b6b96292ee096050f455cdcaddc0f9b7a9f1623":
        raise ValueError("canonical Phase 11E result identity mismatch")
    if phase11b.get("phase11a", {}).get("result_identity") != phase11a.get("result_identity"):
        raise ValueError("Phase 11B does not reference canonical Phase 11A")
    if phase11e.get("phase11", {}).get("phase11b", {}).get("phase11b_result_identity") != phase11b.get("result_identity"):
        raise ValueError("Phase 11E does not reference canonical Phase 11B")
    if phase11e.get("phase11", {}).get("phase11d", {}).get("phase11d_result_identity") != phase11d.get("result_identity"):
        raise ValueError("Phase 11E does not reference canonical Phase 11D")
    if manifests["phase10_risk"].get("completed") is not True or manifests["phase10d_risk_decision"].get("completed") is not True:
        raise ValueError("Phase 10 monitoring references are not complete")
    if manifests["phase5_neutral_factor"].get("completed") is not True:
        raise ValueError("canonical Phase 5 factor reference is not complete")
    result = {name: str(value.get("result_identity") or _file_sha256(paths[name])) for name, value in manifests.items()}
    result["phase9_forward_protocol_fingerprint"] = protocol.protocol_fingerprint
    result["phase6_specification_fingerprint"] = str(phase6.get("specification_fingerprint", ""))
    result["phase8_manifest_sha256"] = _file_sha256(phase8_path)
    result["phase11c_market_database_sha256"] = str(phase11d.get("phase11c", {}).get("canonical_market_database_sha256", ""))
    result["phase5_historical_market_snapshot_id"] = str(manifests["phase5_neutral_factor"].get("snapshot", {}).get("snapshot_id", ""))
    result["phase10_market_snapshot_id"] = str(phase10.get("market", {}).get("snapshot_id", ""))
    result["phase10_market_logical_content_fingerprint"] = str(phase10.get("market", {}).get("logical_content_fingerprint", ""))
    result["phase11a_capability_result_identity"] = str(phase11a.get("result_identity", ""))
    result["phase11b_timing_result_identity"] = str(phase11b.get("result_identity", ""))
    result["phase11e_friction_result_identity"] = str(phase11e.get("result_identity", ""))
    return MappingProxyType(result)


@contextmanager
def _read_only_connection(path: Path):
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        yield connection
    finally:
        connection.close()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _date_row(frame, date_text: str):
    if frame.empty:
        return None
    mask = frame["time"].dt.date.astype(str) == date_text
    rows = frame.loc[mask]
    return None if rows.empty else rows.iloc[-1]


def _overall(evidence: tuple[MonitoringEvidence, ...]) -> MonitoringStatus:
    if any(item.status is MonitoringStatus.DEGRADED for item in evidence):
        return MonitoringStatus.DEGRADED
    if any(item.status is MonitoringStatus.INSUFFICIENT_EVIDENCE for item in evidence):
        return MonitoringStatus.INSUFFICIENT_EVIDENCE
    if any(item.status is MonitoringStatus.DESCRIPTIVE_DRIFT for item in evidence):
        return MonitoringStatus.DESCRIPTIVE_DRIFT
    return MonitoringStatus.HEALTHY


def collect_monitoring_snapshot(
    *,
    database_path: str | Path | None = None,
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
    observed_at_utc: str | None = None,
    references: Mapping[str, str] | None = None,
    spec: MonitoringSpec = MonitoringSpec(),
) -> MonitoringResult:
    """Measure local market, latest feature, selection, portfolio and ledger state."""
    refs = dict(references or load_frozen_monitoring_references())
    if not refs or any(not key or not value for key, value in refs.items()):
        raise ValueError("frozen reference identities must be complete and non-empty")
    db = resolve_market_database_path(database_path)
    timestamp = observed_at_utc or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence: list[MonitoringEvidence] = []
    details: dict[str, str] = {}
    limitations = (
        "monitoring is descriptive and does not judge profitability or forward performance",
        "no alert, strategy disablement, risk change, order action, Telegram message, or ledger write is performed",
        "database coverage is not historical VN100 membership",
        "historical full-sample references overlap research history and are not independent future validation",
        "feature drift lacks a frozen raw-feature cross-sectional reference distribution; observed values are descriptive only",
        "market-volume data does not establish tradable liquidity or capacity",
    )

    try:
        with _read_only_connection(db) as connection:
            table = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='prices'").fetchone()
            schema_rows = tuple(connection.execute("PRAGMA table_info(prices)")) if table else ()
            columns = tuple(row[1] for row in schema_rows)
            required_columns = ("symbol", "time", "open", "high", "low", "close", "volume")
            missing_columns = tuple(name for name in required_columns if name not in columns)
            if table is None or missing_columns:
                evidence.append(MonitoringEvidence("DATA_HEALTH", "schema", MonitoringStatus.DEGRADED, ",".join(columns), ",".join(required_columns), "exact required prices columns", "prices table or required OHLCV columns are missing"))
                raise ValueError("canonical prices schema is incomplete")
            declared_types = {row[1]: str(row[2]).upper() for row in schema_rows}
            expected_types = {"symbol": "TEXT", "time": "TEXT", "open": "REAL", "high": "REAL", "low": "REAL", "close": "REAL", "volume": "INTEGER"}
            schema_mismatches = tuple(f"{name}:{declared_types.get(name)}!={expected}" for name, expected in expected_types.items() if declared_types.get(name) != expected)
            session_rows = tuple(row[0] for row in connection.execute("SELECT DISTINCT date(time) FROM prices WHERE upper(trim(symbol))='VNINDEX' AND date(time) IS NOT NULL ORDER BY date(time)"))
            latest = session_rows[-1] if session_rows else None
            earliest = session_rows[0] if session_rows else None
            if latest is None:
                evidence.append(MonitoringEvidence("DATA_HEALTH", "latest_vnindex_session", MonitoringStatus.DEGRADED, None, None, "latest valid VNINDEX date", "no valid VNINDEX session exists"))
                raise ValueError("canonical database contains no valid VNINDEX session")
            latest_benchmark = connection.execute("SELECT COUNT(*), SUM(CASE WHEN open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL OR volume IS NULL THEN 1 ELSE 0 END), SUM(CASE WHEN open<=0 OR high<=0 OR low<=0 OR close<=0 OR volume<0 THEN 1 ELSE 0 END), MAX(close) FROM prices WHERE upper(trim(symbol))='VNINDEX' AND date(time)=date(?)", (latest,)).fetchone()
            duplicate_groups = int(connection.execute("SELECT COUNT(*) FROM (SELECT upper(trim(symbol)),date(time) FROM prices WHERE date(time) IS NOT NULL GROUP BY upper(trim(symbol)),date(time) HAVING COUNT(*)>1)").fetchone()[0])
            duplicate_latest_benchmark = int(connection.execute("SELECT COUNT(*) FROM prices WHERE upper(trim(symbol))='VNINDEX' AND date(time)=date(?)", (latest,)).fetchone()[0])
            invalid_dates = int(connection.execute("SELECT COUNT(*) FROM prices WHERE date(time) IS NULL").fetchone()[0])
            future_dated_rows = int(connection.execute("SELECT COUNT(*) FROM prices WHERE date(time)>date(?)", (latest,)).fetchone()[0])
            nonpositive = int(connection.execute("SELECT COUNT(*) FROM prices WHERE open<=0 OR high<=0 OR low<=0 OR close<=0").fetchone()[0])
            negative_volume = int(connection.execute("SELECT COUNT(*) FROM prices WHERE volume<0").fetchone()[0])
            missing_ohlcv = int(connection.execute("SELECT COUNT(*) FROM prices WHERE open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL OR volume IS NULL").fetchone()[0])
            latest_rows = int(connection.execute("SELECT COUNT(*) FROM prices WHERE date(time)=date(?)", (latest,)).fetchone()[0])
            latest_missing = int(connection.execute("SELECT COUNT(*) FROM prices WHERE date(time)=date(?) AND (open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL OR volume IS NULL)", (latest,)).fetchone()[0])
            latest_symbols = int(connection.execute("SELECT COUNT(DISTINCT upper(trim(symbol))) FROM prices WHERE date(time)=date(?) AND trim(symbol)<>''", (latest,)).fetchone()[0])
            latest_valid_symbols = frozenset(row[0] for row in connection.execute("SELECT DISTINCT upper(trim(symbol)) FROM prices WHERE date(time)=date(?) AND trim(symbol)<>'' AND open>0 AND high>0 AND low>0 AND close>0 AND volume>=0 AND volume IS NOT NULL", (latest,)))
            symbol_dates = tuple(connection.execute("SELECT upper(trim(symbol)),MAX(date(time)) FROM prices WHERE trim(symbol)<>'' AND date(time) IS NOT NULL GROUP BY upper(trim(symbol)) ORDER BY upper(trim(symbol))"))
        details.update({"latest_market_session": str(latest), "earliest_vnindex_session": str(earliest), "latest_session_symbol_count": str(latest_symbols), "duplicate_symbol_date_groups": str(duplicate_groups), "invalid_date_rows": str(invalid_dates), "future_dated_rows_after_vnindex": str(future_dated_rows), "missing_ohlcv_rows": str(missing_ohlcv), "nonpositive_price_rows": str(nonpositive), "negative_volume_rows": str(negative_volume)})
        evidence.append(MonitoringEvidence("DATA_HEALTH", "latest_vnindex_session", MonitoringStatus.HEALTHY, str(latest), "canonical local VNINDEX series", "latest valid benchmark session", "session located by maximum valid VNINDEX date"))
        latest_close = _finite(latest_benchmark[3])
        close_status = MonitoringStatus.HEALTHY if duplicate_latest_benchmark == 1 and latest_close is not None and latest_close > 0 and int(latest_benchmark[1] or 0) == 0 else MonitoringStatus.DEGRADED
        evidence.append(MonitoringEvidence("DATA_HEALTH", "latest_vnindex_close", close_status, None if latest_close is None else str(latest_close), "exactly one finite positive close on latest VNINDEX session", "latest valid benchmark close", "valid close observed" if close_status is MonitoringStatus.HEALTHY else "latest benchmark close is missing, nonpositive, nonfinite, or duplicated"))
        evidence.append(MonitoringEvidence("DATA_HEALTH", "session_ordering", MonitoringStatus.HEALTHY if all(left < right for left, right in zip(session_rows, session_rows[1:])) else MonitoringStatus.DEGRADED, f"count={len(session_rows)};first={earliest};last={latest}", "strictly increasing distinct ISO dates", "SQLite DISTINCT dates sorted ascending", "VNINDEX date sequence is strictly ordered"))
        evidence.append(MonitoringEvidence("DATA_HEALTH", "schema", MonitoringStatus.DEGRADED if schema_mismatches else MonitoringStatus.HEALTHY, ",".join(f"{name}:{declared_types.get(name)}" for name in required_columns), ",".join(f"{name}:{kind}" for name, kind in expected_types.items()), "required OHLCV columns and declared SQLite affinities", "declared type mismatch: " + ",".join(schema_mismatches) if schema_mismatches else "all required columns and declared types match the canonical schema"))
        evidence.append(MonitoringEvidence("DATA_HEALTH", "latest_session_symbol_count", MonitoringStatus.HEALTHY, str(latest_symbols), "distinct normalized symbols on latest database date", "includes VNINDEX; equity database-coverage membership reported separately", "distinct-symbol count includes benchmark"))
        for name, value, reason in (
            ("duplicate_symbol_date_groups", duplicate_groups, "duplicate normalized symbol/date observations"),
            ("latest_vnindex_row_count", duplicate_latest_benchmark, "latest VNINDEX observation count must equal one"),
            ("invalid_date_rows", invalid_dates, "rows with unparseable SQLite date() value"),
            ("future_dated_rows_after_vnindex", future_dated_rows, "rows dated after latest completed VNINDEX session"),
            ("missing_ohlcv_rows", missing_ohlcv, "rows with one or more null OHLCV fields"),
            ("nonpositive_price_rows", nonpositive, "rows with nonpositive OHLC price"),
            ("negative_volume_rows", negative_volume, "rows with negative volume"),
            ("latest_vnindex_missing_ohlcv", int(latest_benchmark[1] or 0), "latest VNINDEX OHLCV completeness"),
            ("latest_session_missing_ohlcv", latest_missing, "latest database session OHLCV completeness"),
        ):
            if name == "latest_vnindex_row_count":
                status = MonitoringStatus.HEALTHY if value == 1 else MonitoringStatus.DEGRADED
                reference = "1"
                detail = "exactly one latest VNINDEX row observed" if value == 1 else reason
            else:
                status = MonitoringStatus.DEGRADED if value else MonitoringStatus.HEALTHY
                reference = "0"
                detail = reason if value else "no violations observed"
            evidence.append(MonitoringEvidence("DATA_HEALTH", name, status, str(value), reference, "exact integrity requirement", detail))
        all_market_sessions = tuple(row[0] for row in _read_session_rows(db))
        rank_by_session = {session: index for index, session in enumerate(all_market_sessions)}
        latest_rank = rank_by_session.get(latest, len(all_market_sessions) - 1)
        stale_over5 = sum(1 for _symbol, date_text in symbol_dates if latest_rank - (bisect_right(all_market_sessions, date_text)-1) > spec.maximum_staleness_sessions)
        details["stale_symbols_over_5_sessions"] = str(stale_over5)
        evidence.append(MonitoringEvidence("DATA_HEALTH", "stale_symbol_count", MonitoringStatus.HEALTHY, str(stale_over5), str(spec.maximum_staleness_sessions), "descriptive count beyond frozen database-coverage staleness tolerance; not a new degradation threshold", "staleness measured in observed VNINDEX market sessions"))
        for symbol, symbol_date in symbol_dates:
            symbol_rank = bisect_right(all_market_sessions, symbol_date)-1
            behind = latest_rank-symbol_rank
            future = symbol_date > latest
            evidence.append(MonitoringEvidence("DATA_HEALTH", f"symbol_latest_session_{symbol}", MonitoringStatus.DEGRADED if future or behind > spec.maximum_staleness_sessions else MonitoringStatus.HEALTHY, str(symbol_date), str(latest), "last stored observation mapped to preceding VNINDEX session; session-rank difference", f"{behind} observed market sessions behind latest; future_date={future}; 50/5 membership eligibility is not historical VN100"))
        coverage = build_database_coverage_index(earliest, latest, spec.minimum_history_sessions, spec.maximum_staleness_sessions, db)
        members = coverage.members_as_of(latest)
        valid_covered_equities = len(set(members) & set(latest_valid_symbols))
        evidence.append(MonitoringEvidence("DATA_HEALTH", "database_coverage_universe", MonitoringStatus.HEALTHY if members else MonitoringStatus.INSUFFICIENT_EVIDENCE, str(len(members)), f"minimum_history={spec.minimum_history_sessions};maximum_staleness={spec.maximum_staleness_sessions}", "existing point-in-time database coverage contract; VNINDEX excluded", "coverage membership is data availability, not historical index membership"))
        evidence.append(MonitoringEvidence("DATA_HEALTH", "latest_session_universe_coverage", MonitoringStatus.HEALTHY if valid_covered_equities == len(members) else MonitoringStatus.DEGRADED, f"{valid_covered_equities}/{len(members)}", "exact-date valid equity rows / eligible coverage members", "exact-date presence only; no forward fill", "reported as coverage, not as a quality or tradability judgment"))
        market_snapshot = build_market_data_snapshot(db)
        details["market_snapshot_id"] = market_snapshot.snapshot_id
        details["market_logical_content_fingerprint"] = market_snapshot.logical_content_fingerprint
        historical_snapshot_id = refs.get("phase5_historical_market_snapshot_id")
        environment_reference_id = refs.get("phase10_market_snapshot_id")
        if historical_snapshot_id or environment_reference_id:
            matches_environment = bool(environment_reference_id and market_snapshot.snapshot_id == environment_reference_id)
            evidence.append(MonitoringEvidence("DATA_HEALTH", "market_snapshot_reference", MonitoringStatus.HEALTHY if matches_environment else MonitoringStatus.DESCRIPTIVE_DRIFT, market_snapshot.snapshot_id, f"phase5_historical={historical_snapshot_id};phase10_operational={environment_reference_id}", "logical snapshot identity compared to named frozen research snapshots; current data extension is descriptive drift, not an integrity failure", "current logical content matches the Phase 10 reference snapshot" if matches_environment else "current logical content differs from frozen reference snapshot identities"))

        # Use the registered causal feature graph. It loads all pre-session history once,
        # preserving each definition's warmup, then exposes only latest-session rows.
        request = FeatureRequest("historical_candidate_per_symbol_subset", "v2")
        feature_symbols = tuple(sorted((*members, "VNINDEX")))
        feature_result = FeatureRegistry(builtin_definitions()).compute(request, market_snapshot, feature_symbols, start_date=all_market_sessions[-2] if len(all_market_sessions) > 1 else latest, through_date=latest)
        adx_values: list[float] = []
        rsi_values: list[float] = []
        eligible: list[tuple[str, float, float]] = []
        invalid_feature_reasons: dict[str, int] = {"missing_latest_row": 0, "nonfinite_adx": 0, "nonfinite_rsi": 0}
        previous_session = all_market_sessions[-2] if len(all_market_sessions) > 1 else None
        previous_eligible: list[tuple[str, float, float]] = []
        for symbol in sorted(members):
            frame = feature_result.frame_for(symbol)
            row = _date_row(frame, latest)
            if row is None:
                invalid_feature_reasons["missing_latest_row"] += 1
                continue
            adx, rsi = _finite(row.get("ADX14")), _finite(row.get("RSI"))
            if adx is not None:
                adx_values.append(adx)
            else:
                invalid_feature_reasons["nonfinite_adx"] += 1
            if rsi is not None:
                rsi_values.append(rsi)
            else:
                invalid_feature_reasons["nonfinite_rsi"] += 1
            if adx is not None and rsi is not None:
                eligible.append((symbol, adx, rsi))
            if previous_session is not None:
                prior = _date_row(frame, previous_session)
                if prior is not None:
                    prior_adx, prior_rsi = _finite(prior.get("ADX14")), _finite(prior.get("RSI"))
                    if prior_adx is not None and prior_rsi is not None and coverage.is_eligible(symbol, previous_session):
                        previous_eligible.append((symbol, prior_adx, prior_rsi))
        missing_feature_total = sum(invalid_feature_reasons.values())
        feature_status = MonitoringStatus.DEGRADED if missing_feature_total else MonitoringStatus.HEALTHY
        evidence.append(MonitoringEvidence("FEATURE_HEALTH", "latest_session_ADX_RSI_availability", feature_status if members else MonitoringStatus.INSUFFICIENT_EVIDENCE, f"adx={len(adx_values)};rsi={len(rsi_values)};joint={len(eligible)};eligible_universe={len(members)}", "finite exact-date ADX14 and RSI14 on coverage members", "registered historical_candidate_per_symbol_subset@v2; all history through latest date; no later rows", json.dumps(invalid_feature_reasons, sort_keys=True, separators=(",", ":"))))
        evidence.append(MonitoringEvidence("FEATURE_HEALTH", "resolved_feature_warmup", MonitoringStatus.HEALTHY, str(feature_result.metadata.get("resolved_warmup_sessions")), "FeatureRegistry resolved dependency warmup", "maximum dependency-graph warmup; computed from all history through latest date", "causal feature graph resolved without strategy scoring"))
        if adx_values:
            ordered = sorted(adx_values)
            q1, med, q3 = _quantile(ordered, .25), _quantile(ordered, .5), _quantile(ordered, .75)
            details.update({"adx_latest_median": str(med), "adx_latest_iqr": str(q3-q1), "adx_latest_q1": str(q1), "adx_latest_q3": str(q3), "adx_missing_rate": str((len(members)-len(adx_values))/len(members) if members else 1.0)})
            evidence.append(MonitoringEvidence("FEATURE_DRIFT", "ADX14_distribution", MonitoringStatus.DESCRIPTIVE_DRIFT, f"n={len(adx_values)};median={med:.8g};IQR={q3-q1:.8g};missing_rate={(len(members)-len(adx_values))/len(members) if members else 1.0:.8g}", None, spec.feature_reference_semantics, "descriptive latest cross-section; no arbitrary statistical threshold and no frozen raw ADX distribution to compare"))
        else:
            evidence.append(MonitoringEvidence("FEATURE_DRIFT", "ADX14_distribution", MonitoringStatus.INSUFFICIENT_EVIDENCE, None, None, spec.feature_reference_semantics, "no finite latest-session ADX values available"))

        adx_policy = next(item for item in NEUTRAL_ADX_RSI_SELECTION_DIAGNOSTICS_V1.policies if item.name == "ADX_ONLY")
        ordering = rank_panel_policy_candidates(tuple(eligible), adx_policy) if eligible else ()
        prior_ordering = rank_panel_policy_candidates(tuple(previous_eligible), adx_policy) if previous_eligible else ()
        selection_ids: dict[int, tuple[str, ...]] = {}
        for budget in spec.budgets:
            selected = tuple(symbol for symbol, _score in ordering[:budget])
            prior_selected = tuple(symbol for symbol, _score in prior_ordering[:budget])
            selection_ids[budget] = selected
            gross, cash = ((1.0, 0.0) if selected else (0.0, 1.0))
            max_weight = 1.0/len(selected) if selected else 0.0
            hhi = sum((1.0/len(selected))**2 for _ in selected) if selected else 0.0
            effective_n = 1.0/hhi if hhi else None
            entries = len(set(selected)-set(prior_selected))
            phase59_turnover = None if previous_session is None or not prior_selected else entries/len(prior_selected)
            if previous_session:
                old_weights={s:1/len(prior_selected) for s in prior_selected}; new_weights={s:1/len(selected) for s in selected}
                prior_cash = 1.0 if not prior_selected else 0.0
                phase6_turnover=.5*(sum(abs(new_weights.get(s,0)-old_weights.get(s,0)) for s in set(old_weights)|set(new_weights))+abs(cash-prior_cash))
            else:
                phase6_turnover=None
            rank_scores = tuple(score for _symbol, score in ordering)
            details.update({f"selection_{budget}_eligible": str(len(eligible)), f"selection_{budget}_selected": str(len(selected)), f"selection_{budget}_fill_ratio": str(len(selected)/budget), f"selection_{budget}_rank_score_min": str(min(rank_scores) if rank_scores else None), f"selection_{budget}_rank_score_max": str(max(rank_scores) if rank_scores else None), f"selection_{budget}_rank_score_median": str(_median(rank_scores)), f"selection_{budget}_ADX_only_policy_fingerprint": adx_policy.fingerprint, f"selection_{budget}_previous_available_session": str(previous_session), f"selection_{budget}_phase59_entries_over_previous_count": str(phase59_turnover), f"portfolio_{budget}_gross_exposure": str(gross), f"portfolio_{budget}_cash_weight": str(cash), f"portfolio_{budget}_max_position_weight": str(max_weight), f"portfolio_{budget}_hhi": str(hhi), f"portfolio_{budget}_effective_n": str(effective_n), f"portfolio_{budget}_phase6_one_way_weight_turnover": str(phase6_turnover)})
            reference_row = _load_phase6_summary(PHASE6_ROOT / "portfolio_construction_summary.csv", budget)
            evidence.append(MonitoringEvidence("SELECTION_HEALTH", f"ADX_ONLY_budget_{budget}", MonitoringStatus.HEALTHY if eligible else MonitoringStatus.INSUFFICIENT_EVIDENCE, f"eligible={len(eligible)};selected={len(selected)};fill_ratio={len(selected)/budget:.8g};rank_score_min={min(rank_scores) if rank_scores else None};rank_score_median={_median(rank_scores)};rank_score_max={max(rank_scores) if rank_scores else None};previous_overlap={len(set(selected)&set(prior_selected))};Phase5.9_entry_turnover={phase59_turnover};Phase6_weight_turnover={phase6_turnover}", f"Phase6 whole_period mean_selected_count={reference_row.get('mean_selected_count')};mean_fill_ratio={reference_row.get('mean_fill_ratio')}", "same Phase5.9 rank_panel_policy_candidates ADX_ONLY policy; current coverage 50/5; rank distribution has no persisted frozen reference; selection comparisons to Phase6 2018-08-07..2026-09-17 are descriptive", "selection/rank structure only; no outcome quality inference"))
            evidence.append(MonitoringEvidence("PORTFOLIO_HEALTH", f"equal_weight_structure_budget_{budget}", MonitoringStatus.HEALTHY if selected else MonitoringStatus.INSUFFICIENT_EVIDENCE, f"gross={gross};cash={cash};selected={len(selected)};requested={budget};underfilled={len(selected)<budget};max_weight={max_weight};HHI={hhi};effective_N={effective_n};empty={not bool(selected)};Phase6_weight_turnover={phase6_turnover}", f"Phase6 whole_period mean_effective_n={reference_row.get('mean_effective_n')};mean_max_single_name_weight={reference_row.get('mean_max_single_name_weight')}", "Phase6 equal-weight semantics; equal weight = 1/actual selected count; cash=1-gross; HHI=sum(weight^2); effective_N=1/HHI; historical comparison descriptive", "structure only; no portfolio outcome, risk decision, budget ranking, or optimization"))

        forward = _read_forward_status(Path(ledger_path), DEFAULT_PROTOCOL_PATH, latest)
        details.update(forward[1])
        evidence.extend(forward[0])
    except (sqlite3.Error, OSError, ValueError) as error:
        if not any(item.status is MonitoringStatus.DEGRADED and item.dimension == "DATA_HEALTH" for item in evidence):
            evidence.append(MonitoringEvidence("DATA_HEALTH", "monitoring_read", MonitoringStatus.DEGRADED, None, None, "read-only local inspection", str(error)))
    ordered_evidence = tuple(sorted(evidence, key=lambda item: (item.dimension, item.name)))
    status = _overall(ordered_evidence)
    payload = {
        "contract": CONTRACT, "version": VERSION, "observed_at_utc": timestamp,
        "observed_market_session": details.get("latest_market_session"),
        "market_database_path": str(db), "market_snapshot_id": details.get("market_snapshot_id"),
        "specification_fingerprint": spec.fingerprint, "reference_identities": refs,
        "evidence": tuple((item.dimension, item.name, item.status.value, item.observed, item.reference, item.comparison_semantics, item.reason) for item in ordered_evidence),
        "overall_status": status.value, "limitations": limitations,
    }
    snap = MonitoringSnapshot(CONTRACT, VERSION, timestamp, details.get("latest_market_session"), str(db), details.get("market_snapshot_id"), spec.fingerprint, refs, ordered_evidence, status, limitations, _hash(payload))
    return MonitoringResult(snap, MappingProxyType(dict(sorted(details.items()))))


def _read_session_rows(path: Path) -> tuple[tuple[str], ...]:
    with _read_only_connection(path) as connection:
        return tuple(connection.execute("SELECT DISTINCT date(time) FROM prices WHERE upper(trim(symbol))='VNINDEX' AND date(time) IS NOT NULL ORDER BY date(time)"))


def _quantile(values: list[float], probability: float) -> float:
    if len(values) == 1:
        return values[0]
    location = (len(values)-1)*probability
    lower = int(math.floor(location)); upper = int(math.ceil(location))
    return values[lower] + (values[upper]-values[lower])*(location-lower)


def _median(values: tuple[float, ...]) -> float | None:
    return None if not values else float(median(values))


def _load_phase6_summary(path: Path, budget: int) -> dict[str, str]:
    import csv
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle))
    matches = [row for row in rows if row.get("requested_budget") == str(budget) and row.get("scope_name") == "whole_period"]
    if len(matches) != 1:
        raise ValueError(f"expected one frozen Phase 6 whole-period summary for budget {budget}")
    return matches[0]


def _read_forward_status(ledger_path: Path, protocol_path: Path, latest_session: str) -> tuple[tuple[MonitoringEvidence, ...], dict[str, str]]:
    protocol = load_protocol_spec(protocol_path)
    evidence: list[MonitoringEvidence] = []
    details: dict[str, str] = {"forward_protocol_id": protocol.protocol_id, "forward_protocol_fingerprint": protocol.protocol_fingerprint, "forward_activation_boundary": protocol.activation_market_session_boundary}
    if not ledger_path.is_file():
        evidence.append(MonitoringEvidence("FORWARD_PROTOCOL", "ledger_status", MonitoringStatus.NOT_APPLICABLE, None, protocol.protocol_fingerprint, "read-only Forward V1 ledger inspection", "ledger does not exist; no activation or formations observed"))
        details.update({"forward_active": "false", "forward_formation_count": "0", "forward_pending_maturities": "0", "forward_outcome_unavailable_count": "0"})
        return tuple(evidence), details
    with _read_only_connection(ledger_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"forward_protocols", "forward_formations", "forward_maturities", "forward_outcomes", "forward_audit_events"}.issubset(tables):
            evidence.append(MonitoringEvidence("FORWARD_PROTOCOL", "ledger_schema", MonitoringStatus.DEGRADED, ",".join(sorted(tables)), "Forward V1 ledger tables", "read-only schema verification", "ledger schema is incomplete"))
            return tuple(evidence), details
        activation = connection.execute("SELECT protocol_fingerprint,activation_market_session_boundary,operational_start_after_session FROM forward_protocols WHERE protocol_id=?", (protocol.protocol_id,)).fetchone()
        if activation is None:
            evidence.append(MonitoringEvidence("FORWARD_PROTOCOL", "activation", MonitoringStatus.NOT_APPLICABLE, "not activated", protocol.protocol_fingerprint, "protocol activation row lookup", "no activation row exists for the canonical protocol; this is not a strategy failure"))
            details.update({"forward_active": "false", "forward_formation_count": "0", "forward_pending_maturities": "0", "forward_outcome_unavailable_count": "0", "forward_missing_formation_count": "0"})
            return tuple(evidence), details
        mismatch = activation[0] != protocol.protocol_fingerprint or activation[1] != protocol.activation_market_session_boundary
        formations = connection.execute("SELECT COUNT(*),MAX(formation_session) FROM forward_formations WHERE protocol_id=?", (protocol.protocol_id,)).fetchone()
        latest_maturity = connection.execute("SELECT m.status,COUNT(*) FROM forward_maturities m JOIN (SELECT formation_identity,horizon_sessions,MAX(event_sequence) seq FROM forward_maturities GROUP BY formation_identity,horizon_sessions) x ON x.seq=m.event_sequence WHERE m.protocol_id=? GROUP BY m.status", (protocol.protocol_id,)).fetchall()
        outcome_unavailable = int(connection.execute("SELECT COUNT(*) FROM forward_outcomes WHERE protocol_id=? AND availability<>'AVAILABLE'", (protocol.protocol_id,)).fetchone()[0])
        missing_formations = int(connection.execute("SELECT COUNT(*) FROM forward_audit_events WHERE protocol_id=? AND event_type='MISSING_FORMATION'", (protocol.protocol_id,)).fetchone()[0])
        outcome_count = int(connection.execute("SELECT COUNT(*) FROM forward_outcomes WHERE protocol_id=?", (protocol.protocol_id,)).fetchone()[0])
    state = MonitoringStatus.DEGRADED if mismatch else MonitoringStatus.HEALTHY if formations[0] else MonitoringStatus.NOT_APPLICABLE
    maturity_counts = {str(row[0]): int(row[1]) for row in latest_maturity}
    pending = maturity_counts.get("PENDING", 0)
    details.update({"forward_active": "true", "forward_formation_count": str(formations[0]), "forward_latest_formation": str(formations[1]), "forward_pending_maturities": str(pending), "forward_matured_maturities": str(maturity_counts.get("MATURED",0)), "forward_outcome_unavailable_count": str(outcome_unavailable), "forward_outcome_count": str(outcome_count), "forward_missing_formation_count": str(missing_formations)})
    evidence.append(MonitoringEvidence("FORWARD_PROTOCOL", "activation_identity", MonitoringStatus.DEGRADED if mismatch else MonitoringStatus.HEALTHY, f"{activation[0]}|{activation[1]}|{activation[2]}", f"{protocol.protocol_fingerprint}|{protocol.activation_market_session_boundary}", "exact protocol fingerprint and historical cutoff match", "protocol identity mismatch" if mismatch else "active ledger row matches the frozen protocol"))
    evidence.append(MonitoringEvidence("FORWARD_PROTOCOL", "formation_and_maturity_status", state, f"latest_market={latest_session};formations={formations[0]};latest_formation={formations[1]};pending={pending};matured={maturity_counts.get('MATURED',0)};unavailable={outcome_unavailable};missing_sessions={missing_formations}", "operational_start_after_session="+str(activation[2]), "read-only ledger status; exact VNINDEX session maturity semantics", "zero formations means no prospective evidence yet, not strategy failure" if not formations[0] else "formation count is operational coverage only"))
    return tuple(evidence), details


def write_monitoring_artifacts(result: MonitoringResult, output_directory: str | Path) -> Mapping[str, str]:
    """Write compact CSV/JSON/Markdown artifacts into one exact new directory."""
    import csv
    target = Path(output_directory).resolve()
    if target.exists():
        raise FileExistsError(f"monitoring output directory already exists: {target}")
    target.mkdir(parents=True, exist_ok=False)
    snapshot = result.snapshot
    snapshot_rows = [{"snapshot_identity": snapshot.identity, "status": snapshot.overall_status.value, "observed_at_utc": snapshot.observed_at_utc, "observed_market_session": snapshot.observed_market_session, "market_database_path": snapshot.market_database_path, "market_snapshot_id": snapshot.market_snapshot_id, "specification_fingerprint": snapshot.specification_fingerprint}]
    evidence_rows = [{"dimension": item.dimension, "name": item.name, "status": item.status.value, "observed": item.observed or "", "reference": item.reference or "", "comparison_semantics": item.comparison_semantics, "reason": item.reason} for item in snapshot.evidence]
    artifacts = {
        "monitoring_snapshot.csv": snapshot_rows,
        "monitoring_evidence.csv": evidence_rows,
    }
    hashes: dict[str, str] = {}
    for filename, rows in artifacts.items():
        path = target / filename
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader(); writer.writerows(rows)
        hashes[filename] = _file_sha256(path)
    report = [
        "# Quant Lab monitoring snapshot", "",
        f"- Status: **{snapshot.overall_status.value}**",
        f"- Observed at (UTC): {snapshot.observed_at_utc}",
        f"- Completed market session: {snapshot.observed_market_session or 'unavailable'}",
        f"- Snapshot identity: `{snapshot.identity}`", "",
        "This is operational and research-environment monitoring evidence only. It does not assess profitability, alert, or control trading.", "",
        "| Dimension | Evidence | Status | Observed | Reference / semantics |", "|---|---|---|---|---|",
    ]
    report.extend(f"| {item.dimension} | {item.name} | {item.status.value} | {item.observed or '—'} | {item.reference or item.comparison_semantics} |" for item in snapshot.evidence)
    report.extend(("", "## Limitations", "", *(f"- {value}" for value in snapshot.limitations), ""))
    report_path = target / "monitoring_report.md"
    report_path.write_text("\n".join(report), encoding="utf-8", newline="\n")
    hashes[report_path.name] = _file_sha256(report_path)
    manifest = {
        "contract": CONTRACT, "version": VERSION,
        "result_identity": snapshot.identity,
        "specification_fingerprint": snapshot.specification_fingerprint,
        "observed_at_utc": snapshot.observed_at_utc,
        "observed_market_session": snapshot.observed_market_session,
        "market_database_path": snapshot.market_database_path,
        "market_snapshot_id": snapshot.market_snapshot_id,
        "reference_identities": dict(snapshot.reference_identities),
        "data_health_contract": "read_only_prices_schema_duplicates_ohlcv_validity_coverage_50_5_session_staleness",
        "feature_semantics": "registered causal historical_candidate_per_symbol_subset@v2; ADX/RSI availability; descriptive ADX distribution only",
        "selection_semantics": "Phase5.9 ADX_ONLY ranking reused; budgets 5/10/20; no outcome scoring",
        "portfolio_semantics": "Phase6 equal-weight/cash/HHI/effective-N/one-way-weight-turnover definitions reused descriptively",
        "forward_protocol_identity": snapshot.reference_identities.get("phase9_forward_protocol_fingerprint"),
        "integrity_failure_separate_from_distributional_drift": True,
        "performance_monitoring": False,
        "control_actions": False,
        "alerting": False,
        "limitations": snapshot.limitations,
        "artifacts": dict(sorted(hashes.items())),
    }
    manifest_path = target / "monitoring_manifest.json"
    manifest_path.write_bytes(canonical_json(canonical_identity_value(manifest)) + b"\n")
    hashes[manifest_path.name] = _file_sha256(manifest_path)
    return MappingProxyType(dict(sorted(hashes.items())))

