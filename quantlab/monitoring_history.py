from __future__ import annotations

"""Immutable file-based catalog and descriptive comparison of Phase 12A snapshots.

This module consumes persisted monitoring artifacts only. It does not access the
market database, forward ledger, network, performance outcomes, or controls.
"""

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import csv
import json
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

from quantlab.identity import canonical_identity_value, canonical_json


SNAPSHOT_CONTRACT = "quantlab.monitoring_snapshot"
HISTORY_CONTRACT = "quantlab.monitoring_history"
HISTORY_VERSION = "v1"
SNAPSHOT_PREFIX = "quantlab_monitoring_snapshot_"
CATALOG_FILE = "monitoring_snapshot_catalog.csv"
TRANSITIONS_FILE = "monitoring_transitions.csv"
MANIFEST_FILE = "monitoring_longitudinal_manifest.json"
REPORT_FILE = "monitoring_longitudinal_report.md"

_TEXT_FIELDS = ("contract", "version", "result_identity", "specification_fingerprint", "observed_at_utc", "observed_market_session")
_SEMANTIC_FIELDS = ("feature_semantics", "selection_semantics", "portfolio_semantics", "forward_protocol_identity")
_REQUIRED_ARTIFACTS = ("monitoring_snapshot.csv", "monitoring_evidence.csv", "monitoring_report.md")
_SAFE_METRICS = (
    "database_coverage_universe", "stale_symbols_over_5_sessions", "missing_ohlcv_rows",
    "duplicate_symbol_date_groups", "invalid_date_rows", "latest_session_symbol_count",
    "latest_session_missing_ohlcv", "latest_vnindex_missing_ohlcv", "latest_vnindex_row_count",
    "adx_available_count", "rsi_available_count", "joint_feature_available_count",
    "adx_cross_section_median", "adx_cross_section_iqr",
    "selection_5_eligible_count", "selection_5_selected_count", "selection_5_previous_overlap_count",
    "selection_5_phase59_turnover", "selection_5_phase6_turnover",
    "selection_10_eligible_count", "selection_10_selected_count", "selection_10_previous_overlap_count",
    "selection_10_phase59_turnover", "selection_10_phase6_turnover",
    "selection_20_eligible_count", "selection_20_selected_count", "selection_20_previous_overlap_count",
    "selection_20_phase59_turnover", "selection_20_phase6_turnover",
    "portfolio_5_gross_exposure", "portfolio_5_cash_weight", "portfolio_5_max_position_weight", "portfolio_5_hhi", "portfolio_5_effective_n", "portfolio_5_phase6_turnover",
    "portfolio_10_gross_exposure", "portfolio_10_cash_weight", "portfolio_10_max_position_weight", "portfolio_10_hhi", "portfolio_10_effective_n", "portfolio_10_phase6_turnover",
    "portfolio_20_gross_exposure", "portfolio_20_cash_weight", "portfolio_20_max_position_weight", "portfolio_20_hhi", "portfolio_20_effective_n", "portfolio_20_phase6_turnover",
    "forward_formation_count", "forward_pending_maturities", "forward_matured_maturities",
    "forward_outcome_unavailable_count", "forward_missing_formation_count",
)
_FORBIDDEN_METRIC_WORDS = ("return", "profit", "pnl", "sharpe", "drawdown", "alpha", "win_rate", "outcome_mean")


@dataclass(frozen=True, slots=True)
class MonitoringHistorySpec:
    contract: str = HISTORY_CONTRACT
    version: str = HISTORY_VERSION
    ordering: str = "observed_market_session_then_observed_at_utc_then_snapshot_identity"
    compatibility: str = "same_specification_fingerprint_and_reference_semantics_required_for_numeric_deltas"
    same_session_policy: str = "distinct_identities_are_conflicting_and_not_silently_selected"
    duplicate_policy: str = "identical_snapshot_identity_collapses_to_first_sorted_directory"
    descriptive_only: bool = True
    performance_monitoring: bool = False
    alerting: bool = False
    control_actions: bool = False
    fingerprint: str = ""

    def __post_init__(self) -> None:
        payload = {
            "contract": self.contract, "version": self.version,
            "ordering": self.ordering, "compatibility": self.compatibility,
            "same_session_policy": self.same_session_policy,
            "duplicate_policy": self.duplicate_policy,
            "allowed_comparisons": _SAFE_METRICS,
            "selection_identity_limitation": "no selected symbols persisted; do not reconstruct or compute Jaccard",
            "restrictions": ("descriptive_only", "no_performance_monitoring", "no_alerting", "no_control_actions", "no_db_or_network"),
        }
        object.__setattr__(self, "fingerprint", _digest(payload))


@dataclass(frozen=True, slots=True)
class MonitoringSnapshotReference:
    directory: str
    result_identity: str
    specification_fingerprint: str
    observed_market_session: str
    observed_at_utc: str
    overall_status: str
    reference_identities: Mapping[str, str]
    semantic_identities: Mapping[str, str]
    evidence: tuple[tuple[str, str, str, str | None, str | None, str, str], ...]
    artifact_hashes: Mapping[str, str]
    classification: str = "VALID"
    invalid_reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference_identities", MappingProxyType(dict(sorted(self.reference_identities.items()))))
        object.__setattr__(self, "semantic_identities", MappingProxyType(dict(sorted(self.semantic_identities.items()))))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "artifact_hashes", MappingProxyType(dict(sorted(self.artifact_hashes.items()))))


@dataclass(frozen=True, slots=True)
class MonitoringSnapshotCatalog:
    snapshots: tuple[MonitoringSnapshotReference, ...]
    discovered_count: int
    valid_count: int
    invalid_count: int
    exact_duplicate_count: int
    conflicting_session_count: int


@dataclass(frozen=True, slots=True)
class MonitoringTransition:
    previous_identity: str
    current_identity: str
    previous_session: str
    current_session: str
    previous_status: str
    current_status: str
    transition_status: str
    metric_name: str
    previous_value: str | None
    current_value: str | None
    delta: float | None
    reason: str


@dataclass(frozen=True, slots=True)
class MonitoringHistoryResult:
    catalog: MonitoringSnapshotCatalog
    transitions: tuple[MonitoringTransition, ...]
    history_state: str
    specification_fingerprint: str
    identity: str
    limitations: tuple[str, ...]


def _digest(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iso_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("missing observation timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("observation timestamp must include a timezone")
    return parsed


def _load_snapshot(directory: Path, root: Path) -> MonitoringSnapshotReference:
    manifest_path = directory / "monitoring_manifest.json"
    if not manifest_path.is_file():
        raise ValueError("missing monitoring_manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("contract") != SNAPSHOT_CONTRACT or manifest.get("version") != "v1":
        raise ValueError("unsupported Phase 12A manifest contract/version")
    for field in _TEXT_FIELDS:
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise ValueError(f"missing or invalid manifest field: {field}")
    session = manifest["observed_market_session"]
    if date.fromisoformat(session).isoformat() != session:
        raise ValueError("observation session must be ISO YYYY-MM-DD")
    _iso_timestamp(manifest["observed_at_utc"])
    refs = manifest.get("reference_identities")
    if not isinstance(refs, dict) or not refs or any(not isinstance(k, str) or not isinstance(v, str) or not v for k, v in refs.items()):
        raise ValueError("missing or invalid reference identities")
    if manifest.get("forward_protocol_identity") != refs.get("phase9_forward_protocol_fingerprint"):
        raise ValueError("forward protocol identity is inconsistent")
    semantic = {field: manifest.get(field) for field in _SEMANTIC_FIELDS}
    if any(not isinstance(value, str) or not value for value in semantic.values()):
        raise ValueError("missing or invalid semantic identity")
    if manifest.get("performance_monitoring") is not False or manifest.get("alerting") is not False or manifest.get("control_actions") is not False:
        raise ValueError("Phase 12A restrictions are absent or incompatible")
    listed_hashes = manifest.get("artifacts")
    if not isinstance(listed_hashes, dict):
        raise ValueError("manifest artifact hashes are missing")
    artifact_hashes: dict[str, str] = {}
    for filename in _REQUIRED_ARTIFACTS:
        expected = listed_hashes.get(filename)
        path = directory / filename
        if path.is_symlink() or not isinstance(expected, str) or not path.is_file():
            raise ValueError(f"required artifact/hash missing: {filename}")
        actual = _file_hash(path)
        if actual != expected:
            raise ValueError(f"artifact hash mismatch: {filename}")
        artifact_hashes[filename] = actual
    with (directory / "monitoring_snapshot.csv").open("r", encoding="utf-8", newline="") as stream:
        rows = tuple(csv.DictReader(stream))
    if len(rows) != 1:
        raise ValueError("snapshot CSV must contain exactly one row")
    row = rows[0]
    comparisons = {
        "snapshot_identity": manifest["result_identity"],
        "observed_market_session": session,
        "observed_at_utc": manifest["observed_at_utc"],
        "specification_fingerprint": manifest["specification_fingerprint"],
        "market_snapshot_id": str(manifest.get("market_snapshot_id") or ""),
    }
    if any(row.get(field) != value for field, value in comparisons.items()):
        raise ValueError("snapshot row disagrees with manifest")
    if row.get("status") not in {"HEALTHY", "DEGRADED", "INSUFFICIENT_EVIDENCE", "NOT_APPLICABLE", "DESCRIPTIVE_DRIFT"}:
        raise ValueError("unknown Phase 12A status")
    with (directory / "monitoring_evidence.csv").open("r", encoding="utf-8", newline="") as stream:
        evidence_rows = tuple(csv.DictReader(stream))
    required_evidence_columns = {"dimension", "name", "status", "observed", "reference", "comparison_semantics", "reason"}
    if not evidence_rows or not required_evidence_columns.issubset(evidence_rows[0]):
        raise ValueError("evidence CSV is empty or lacks required columns")
    allowed_statuses = {"HEALTHY", "DEGRADED", "INSUFFICIENT_EVIDENCE", "NOT_APPLICABLE", "DESCRIPTIVE_DRIFT"}
    if any(not item.get("dimension") or not item.get("name") or item.get("status") not in allowed_statuses for item in evidence_rows):
        raise ValueError("evidence CSV contains an invalid dimension, name, or status")
    evidence = tuple(sorted((
        item["dimension"], item["name"], item["status"], item.get("observed") or None,
        item.get("reference") or None, item["comparison_semantics"], item["reason"],
    ) for item in evidence_rows))
    return MonitoringSnapshotReference(
        directory=str(directory.relative_to(root).as_posix()), result_identity=manifest["result_identity"],
        specification_fingerprint=manifest["specification_fingerprint"], observed_market_session=session,
        observed_at_utc=manifest["observed_at_utc"], overall_status=row["status"], reference_identities=refs,
        semantic_identities=semantic, evidence=evidence, artifact_hashes=artifact_hashes,
    )


def _invalid_snapshot(directory: Path, root: Path, reason: str) -> MonitoringSnapshotReference:
    return MonitoringSnapshotReference(
        directory=directory.relative_to(root).as_posix(), result_identity="", specification_fingerprint="",
        observed_market_session="", observed_at_utc="", overall_status="INVALID", reference_identities={},
        semantic_identities={}, evidence=(), artifact_hashes={}, classification="INVALID", invalid_reason=reason,
    )


def _ordering_key(snapshot: MonitoringSnapshotReference) -> tuple[str, str, str, str]:
    return (snapshot.observed_market_session, snapshot.observed_at_utc, snapshot.result_identity, snapshot.directory)


def discover_monitoring_snapshots(root: str | Path) -> MonitoringSnapshotCatalog:
    """Discover only direct-child Phase 12A snapshot directories; malformed entries remain visible as INVALID."""
    base = Path(root).resolve(strict=True)
    if not base.is_dir():
        raise NotADirectoryError(base)
    discovered: list[MonitoringSnapshotReference] = []
    for path in sorted(base.iterdir(), key=lambda item: item.name):
        if not path.name.startswith(SNAPSHOT_PREFIX):
            continue
        if path.is_symlink() or not path.is_dir():
            discovered.append(_invalid_snapshot(path, base, "snapshot path is not a regular directory"))
            continue
        try:
            snapshot = _load_snapshot(path, base)
            expected_name = f"{SNAPSHOT_PREFIX}{snapshot.observed_market_session}_{snapshot.observed_at_utc.replace(':', '').replace('-', '').removesuffix('Z')}"
            if path.name != expected_name:
                raise ValueError("directory name does not match Phase 12A snapshot naming contract")
            discovered.append(snapshot)
        except (OSError, ValueError, TypeError, csv.Error) as error:
            discovered.append(_invalid_snapshot(path, base, str(error)))
    discovered.sort(key=lambda item: (_ordering_key(item) if item.classification == "VALID" else ("", "", "", item.directory)))
    valid = [item for item in discovered if item.classification == "VALID"]
    deduplicated, duplicate_count, classified_valid = _deduplicate_snapshots(valid)
    sessions: dict[str, list[MonitoringSnapshotReference]] = {}
    for item in deduplicated:
        sessions.setdefault(item.observed_market_session, []).append(item)
    conflict_sessions = {session for session, items in sessions.items() if len(items) > 1}
    conflict_count = 0
    updated: list[MonitoringSnapshotReference] = []
    for item in discovered:
        if item.classification == "VALID":
            item = classified_valid[(item.result_identity, item.directory)]
        if item.classification == "VALID" and item.observed_market_session in conflict_sessions:
            conflict_count += 1
            item = MonitoringSnapshotReference(
                directory=item.directory, result_identity=item.result_identity,
                specification_fingerprint=item.specification_fingerprint,
                observed_market_session=item.observed_market_session, observed_at_utc=item.observed_at_utc,
                overall_status=item.overall_status, reference_identities=item.reference_identities,
                semantic_identities=item.semantic_identities, evidence=item.evidence, artifact_hashes=item.artifact_hashes,
                classification="CONFLICTING_SESSION", invalid_reason="distinct valid snapshot identities share one market session",
            )
        updated.append(item)
    valid_count = len(deduplicated)
    invalid_count = sum(item.classification == "INVALID" for item in updated)
    return MonitoringSnapshotCatalog(tuple(updated), len(updated), valid_count, invalid_count, duplicate_count, conflict_count)


def _deduplicate_snapshots(
    snapshots: list[MonitoringSnapshotReference] | tuple[MonitoringSnapshotReference, ...],
) -> tuple[list[MonitoringSnapshotReference], int, dict[tuple[str, str], MonitoringSnapshotReference]]:
    """Collapse byte/contract-identical result identities in deterministic path order."""
    identities_seen: dict[str, tuple[Any, ...]] = {}
    deduplicated: list[MonitoringSnapshotReference] = []
    classified: dict[tuple[str, str], MonitoringSnapshotReference] = {}
    duplicates = 0
    for item in sorted(snapshots, key=_ordering_key):
        key = (item.result_identity, item.directory)
        signature = (
            item.specification_fingerprint, item.observed_market_session, item.observed_at_utc,
            item.overall_status, tuple(sorted(item.reference_identities.items())),
            tuple(sorted(item.semantic_identities.items())), item.evidence,
            tuple(sorted(item.artifact_hashes.items())),
        )
        previous = identities_seen.get(item.result_identity)
        if previous is None:
            identities_seen[item.result_identity] = signature
            deduplicated.append(item)
            classified[key] = item
        elif previous == signature:
            duplicates += 1
            classified[key] = MonitoringSnapshotReference(
                directory=item.directory, result_identity=item.result_identity,
                specification_fingerprint=item.specification_fingerprint,
                observed_market_session=item.observed_market_session, observed_at_utc=item.observed_at_utc,
                overall_status=item.overall_status, reference_identities=item.reference_identities,
                semantic_identities=item.semantic_identities, evidence=item.evidence, artifact_hashes=item.artifact_hashes,
                classification="EXACT_DUPLICATE", invalid_reason="same immutable snapshot identity and content was already cataloged",
            )
        else:
            classified[key] = MonitoringSnapshotReference(
                directory=item.directory, result_identity=item.result_identity,
                specification_fingerprint=item.specification_fingerprint,
                observed_market_session=item.observed_market_session, observed_at_utc=item.observed_at_utc,
                overall_status=item.overall_status, reference_identities=item.reference_identities,
                semantic_identities=item.semantic_identities, evidence=item.evidence, artifact_hashes=item.artifact_hashes,
                classification="INVALID", invalid_reason="result identity collision: identity maps to different snapshot content",
            )
    return deduplicated, duplicates, classified


def _evidence_map(snapshot: MonitoringSnapshotReference) -> dict[tuple[str, str], tuple[str | None, str | None, str]]:
    return {(dimension, name): (observed, reference, status) for dimension, name, status, observed, reference, _semantics, _reason in snapshot.evidence}


def _finite_number(value: str | None) -> float | None:
    if value is None or not value.strip() or value.strip().lower() in {"none", "nan", "null"}:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _tokens(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    return {key.strip(): val.strip() for key, val in re.findall(r"([A-Za-z0-9_.]+)=([^;]+)", value)}


def _metric_values(snapshot: MonitoringSnapshotReference) -> dict[str, str]:
    evidence = _evidence_map(snapshot)
    result: dict[str, str] = {}
    direct = {
        "database_coverage_universe": ("DATA_HEALTH", "database_coverage_universe"),
        "duplicate_symbol_date_groups": ("DATA_HEALTH", "duplicate_symbol_date_groups"),
        "invalid_date_rows": ("DATA_HEALTH", "invalid_date_rows"),
        "missing_ohlcv_rows": ("DATA_HEALTH", "missing_ohlcv_rows"),
        "latest_session_symbol_count": ("DATA_HEALTH", "latest_session_symbol_count"),
        "latest_session_missing_ohlcv": ("DATA_HEALTH", "latest_session_missing_ohlcv"),
        "latest_vnindex_missing_ohlcv": ("DATA_HEALTH", "latest_vnindex_missing_ohlcv"),
        "latest_vnindex_row_count": ("DATA_HEALTH", "latest_vnindex_row_count"),
    }
    for output, key in direct.items():
        observed = evidence.get(key, (None, None, ""))[0]
        if observed is not None and _finite_number(observed) is not None:
            result[output] = observed
    stale = evidence.get(("DATA_HEALTH", "stale_symbol_count"), (None, None, ""))[0]
    if stale is not None and _finite_number(stale) is not None:
        result["stale_symbols_over_5_sessions"] = stale
    feature = evidence.get(("FEATURE_HEALTH", "latest_session_ADX_RSI_availability"), (None, None, ""))[0]
    feature_tokens = _tokens(feature)
    for token, output in (("adx", "adx_available_count"), ("rsi", "rsi_available_count"), ("joint", "joint_feature_available_count")):
        value = feature_tokens.get(token, "").split("/")[0]
        if _finite_number(value) is not None:
            result[output] = value
    adx = evidence.get(("FEATURE_DRIFT", "ADX14_distribution"), (None, None, ""))[0]
    adx_tokens = _tokens(adx)
    for token, output in (("median", "adx_cross_section_median"), ("IQR", "adx_cross_section_iqr")):
        if _finite_number(adx_tokens.get(token)) is not None:
            result[output] = adx_tokens[token]
    for budget in (5, 10, 20):
        sel = evidence.get(("SELECTION_HEALTH", f"ADX_ONLY_budget_{budget}"), (None, None, ""))[0]
        tokens = _tokens(sel)
        for token, suffix in (("eligible", "eligible_count"), ("selected", "selected_count"), ("previous_overlap", "previous_overlap_count"), ("Phase5.9_entry_turnover", "phase59_turnover"), ("Phase6_weight_turnover", "phase6_turnover")):
            if _finite_number(tokens.get(token)) is not None:
                result[f"selection_{budget}_{suffix}"] = tokens[token]
        port = evidence.get(("PORTFOLIO_HEALTH", f"equal_weight_structure_budget_{budget}"), (None, None, ""))[0]
        ptokens = _tokens(port)
        for token, suffix in (("gross", "gross_exposure"), ("cash", "cash_weight"), ("max_weight", "max_position_weight"), ("HHI", "hhi"), ("effective_N", "effective_n"), ("Phase6_weight_turnover", "phase6_turnover")):
            if _finite_number(ptokens.get(token)) is not None:
                result[f"portfolio_{budget}_{suffix}"] = ptokens[token]
    forward = evidence.get(("FORWARD_PROTOCOL", "formation_and_maturity_status"), (None, None, ""))[0]
    ftokens = _tokens(forward)
    for token, output in (("formations", "forward_formation_count"), ("pending", "forward_pending_maturities"), ("matured", "forward_matured_maturities"), ("unavailable", "forward_outcome_unavailable_count"), ("missing_sessions", "forward_missing_formation_count")):
        if _finite_number(ftokens.get(token)) is not None:
            result[output] = ftokens[token]
    return result


def _compatible(previous: MonitoringSnapshotReference, current: MonitoringSnapshotReference) -> bool:
    return (
        previous.specification_fingerprint == current.specification_fingerprint
        and dict(previous.reference_identities) == dict(current.reference_identities)
        and dict(previous.semantic_identities) == dict(current.semantic_identities)
    )


def _transition(previous: MonitoringSnapshotReference, current: MonitoringSnapshotReference) -> tuple[MonitoringTransition, ...]:
    fields = dict(
        previous_identity=previous.result_identity, current_identity=current.result_identity,
        previous_session=previous.observed_market_session, current_session=current.observed_market_session,
        previous_status=previous.overall_status, current_status=current.overall_status,
    )
    if previous.observed_market_session == current.observed_market_session:
        state = "CONFLICTING_SAME_SESSION"
        reason = "distinct snapshots share a market session; no snapshot was selected"
    elif "CONFLICTING_SESSION" in {previous.classification, current.classification}:
        state = "CONFLICTING_SESSION_BOUNDARY"
        reason = "one adjacent market session has conflicting snapshots; numeric deltas omitted"
    elif not _compatible(previous, current):
        state = "REFERENCE_BOUNDARY"
        reason = "monitoring specification, references, or semantics changed; numeric deltas omitted"
    else:
        state = "COMPARABLE"
        reason = "compatible Phase 12A evidence; descriptive deltas only"
    rows = [MonitoringTransition(**fields, transition_status=state, metric_name="__transition__", previous_value=previous.overall_status, current_value=current.overall_status, delta=None, reason=reason)]
    if state != "COMPARABLE":
        return tuple(rows)
    before, after = _metric_values(previous), _metric_values(current)
    for metric in _SAFE_METRICS:
        if any(word in metric.lower() for word in _FORBIDDEN_METRIC_WORDS):
            raise AssertionError(f"forbidden performance metric in allowlist: {metric}")
        old, new = before.get(metric), after.get(metric)
        if old is None and new is None:
            continue
        old_number, new_number = _finite_number(old), _finite_number(new)
        delta = None if old_number is None or new_number is None else new_number - old_number
        rows.append(MonitoringTransition(**fields, transition_status=state, metric_name=metric, previous_value=old, current_value=new, delta=delta, reason="persisted operational/descriptive evidence; not a quality judgment"))
    rows.append(MonitoringTransition(**fields, transition_status=state, metric_name="observed_market_session", previous_value=previous.observed_market_session, current_value=current.observed_market_session, delta=None, reason="completed market-session labels; session progression only, no inferred session count"))
    return tuple(rows)


def evaluate_monitoring_history(root: str | Path, spec: MonitoringHistorySpec = MonitoringHistorySpec()) -> MonitoringHistoryResult:
    catalog = discover_monitoring_snapshots(root)
    unique = [item for item in catalog.snapshots if item.classification in {"VALID", "CONFLICTING_SESSION"}]
    transitions: list[MonitoringTransition] = []
    for previous, current in zip(unique, unique[1:]):
        transitions.extend(_transition(previous, current))
    state = "INSUFFICIENT_HISTORY" if len(unique) < 2 else "REFERENCE_BOUNDARY_ONLY" if transitions and all(t.transition_status != "COMPARABLE" for t in transitions if t.metric_name == "__transition__") else "LONGITUDINAL_EVIDENCE_AVAILABLE"
    limitations = (
        "monitoring history is descriptive and is not performance monitoring, alerting, or strategy control",
        "selection identities were not persisted in Phase 12A; continuity/Jaccard is unavailable and no database reconstruction is performed",
        "Forward V1 counts are operational status only; matured outcomes are not evaluated",
        "full-sample research references overlap the historical dataset and are not independent confirmation",
        "database coverage is not necessarily historical VN100 membership",
        "no statistical thresholds, severity scores, p-values, profitability, tradability, or portfolio utility are inferred",
    )
    payload = {
        "contract": spec.contract, "version": spec.version, "specification_fingerprint": spec.fingerprint,
        "snapshot_identities": tuple(item.result_identity for item in unique),
        "catalog": tuple((item.directory, item.classification, item.invalid_reason, dict(item.artifact_hashes)) for item in catalog.snapshots),
        "transitions": tuple((item.previous_identity, item.current_identity, item.transition_status, item.metric_name, item.previous_value, item.current_value, item.delta, item.reason) for item in transitions),
        "history_state": state, "limitations": limitations,
    }
    return MonitoringHistoryResult(catalog, tuple(transitions), state, spec.fingerprint, _digest(payload), limitations)


def _write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_monitoring_history_artifacts(result: MonitoringHistoryResult, output_directory: str | Path) -> Mapping[str, str]:
    """Create compact catalog/transition artifacts under one new exact directory."""
    target = Path(output_directory).resolve()
    if target.exists():
        raise FileExistsError(f"monitoring history output directory already exists: {target}")
    target.mkdir(parents=True, exist_ok=False)
    catalog_fields = ("directory", "result_identity", "specification_fingerprint", "observed_market_session", "observed_at_utc", "overall_status", "classification", "invalid_reason", "artifact_hashes_json")
    catalog_rows = [{
        "directory": item.directory, "result_identity": item.result_identity,
        "specification_fingerprint": item.specification_fingerprint,
        "observed_market_session": item.observed_market_session, "observed_at_utc": item.observed_at_utc,
        "overall_status": item.overall_status, "classification": item.classification,
        "invalid_reason": item.invalid_reason,
        "artifact_hashes_json": canonical_json(dict(item.artifact_hashes)).decode("utf-8"),
    } for item in result.catalog.snapshots]
    transition_fields = tuple(MonitoringTransition.__dataclass_fields__)
    transition_rows = [{field: getattr(item, field) if getattr(item, field) is not None else "" for field in transition_fields} for item in result.transitions]
    _write_csv(target / CATALOG_FILE, catalog_fields, catalog_rows)
    _write_csv(target / TRANSITIONS_FILE, transition_fields, transition_rows)
    report = [
        "# Quant Lab monitoring history", "", f"- State: **{result.history_state}**",
        f"- Discovered: {result.catalog.discovered_count}; valid canonical identities: {result.catalog.valid_count}; invalid: {result.catalog.invalid_count}; exact duplicates: {result.catalog.exact_duplicate_count}; conflicting-session snapshots: {result.catalog.conflicting_session_count}",
        f"- Transition rows: {len(result.transitions)}", f"- Result identity: `{result.identity}`", "",
        "This is descriptive monitoring continuity only. It does not assess performance, alert, or control strategy behavior.", "",
        "## Limitations", "", *(f"- {item}" for item in result.limitations), "",
    ]
    (target / REPORT_FILE).write_text("\n".join(report), encoding="utf-8", newline="\n")
    hashes = {name: _file_hash(target / name) for name in (CATALOG_FILE, TRANSITIONS_FILE, REPORT_FILE)}
    valid = [item for item in result.catalog.snapshots if item.classification in {"VALID", "CONFLICTING_SESSION"}]
    reference_sets = [
        {"result_identity": item.result_identity, "reference_identities": dict(item.reference_identities)}
        for item in valid
    ]
    specs = sorted({item.specification_fingerprint for item in valid if item.specification_fingerprint})
    manifest = {
        "contract": HISTORY_CONTRACT, "version": HISTORY_VERSION, "result_identity": result.identity,
        "specification_fingerprint": result.specification_fingerprint, "history_state": result.history_state,
        "phase12a_contract": SNAPSHOT_CONTRACT, "phase12a_specification_fingerprints": specs,
        "phase12a_reference_identity_sets": reference_sets, "discovered_snapshot_count": result.catalog.discovered_count,
        "valid_snapshot_count": result.catalog.valid_count, "invalid_snapshot_count": result.catalog.invalid_count,
        "exact_duplicate_count": result.catalog.exact_duplicate_count,
        "conflicting_session_snapshot_count": result.catalog.conflicting_session_count,
        "ordering_semantics": MonitoringHistorySpec().ordering,
        "comparison_compatibility": MonitoringHistorySpec().compatibility,
        "reference_boundary_semantics": "no numeric delta crosses a spec/reference/semantic boundary",
        "performance_monitoring": False, "alerting": False, "control_actions": False,
        "limitations": result.limitations, "artifacts": dict(sorted(hashes.items())),
    }
    manifest_path = target / MANIFEST_FILE
    manifest_path.write_bytes(canonical_json(canonical_identity_value(manifest)) + b"\n")
    hashes[MANIFEST_FILE] = _file_hash(manifest_path)
    return MappingProxyType(dict(sorted(hashes.items())))

