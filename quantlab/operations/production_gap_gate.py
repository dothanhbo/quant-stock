from __future__ import annotations

"""Compact, evidence-bound Phase 13E production hardening decision gate.

This module records audited conclusions only. It never inspects the runtime,
loads configuration values, opens databases, or imports operational code.
"""

from dataclasses import asdict, dataclass
from enum import Enum
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Any

from quantlab.identity import canonical_identity_value, canonical_json


CONTRACT = "quantlab.production_hardening_gate"
VERSION = "v1"
PHASE_13A_COMMIT = "b4926971837eb6cd20b1407f87a72cbbd307125d"
PHASE_13A_AUDIT_COMMIT = "33d4ee09fb65117621b28784e00eb44f6a8922c1"
PHASE_13A_RESULT_IDENTITY = "7cb159bad26418adb02be5343800ac34c6d2f59062670a73af85a3fd452e9bad"
PHASE_13A_SOURCE_ARTIFACTS = (
    ("production_audit_manifest.json", "c943fb57d1ec39a21deeadd5f67a443ba65b7c4db2fbf732ecd47fc9619f38ac4"),
    ("production_recovery_gaps.csv", "9f31ff4cba646fff57ac658a524dc9595b230cb7a4a0c96ed23c47ee867c3d9f"),
    ("production_audit_report.md", "b79318650d392fffb3104638e60c422ac170df624b37cc70f071f5e5319351a7"),
)
PHASE_13B_COMMIT = "edaf78a05dbd05ab030fc8ea01f1abf57cbe6547"
PHASE_13C_COMMIT = "7e0eff38eb6850dc506ef1642d86bbaf07d46555"
PHASE_13D_COMMIT = "ee96270ef7ec0b066d8633f630acbdeea68e4d37"


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    MATERIAL = "MATERIAL"
    MINOR = "MINOR"
    INFORMATIONAL = "INFORMATIONAL"


class LifecycleState(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
    SUPERSEDED = "SUPERSEDED"
    DEFERRED = "DEFERRED"


class Decision(str, Enum):
    FIX_BEFORE_V1_CONSOLIDATION = "FIX_BEFORE_V1_CONSOLIDATION"
    DEFER_WITH_DOCUMENTATION = "DEFER_WITH_DOCUMENTATION"
    NO_ACTION_REQUIRED = "NO_ACTION_REQUIRED"


class BundleDecision(str, Enum):
    ONE_BOUNDED_HARDENING_PACKAGE = "ONE_BOUNDED_HARDENING_PACKAGE"
    MULTIPLE_PACKAGES_REQUIRED = "MULTIPLE_PACKAGES_REQUIRED"
    NO_FURTHER_HARDENING_REQUIRED = "NO_FURTHER_HARDENING_REQUIRED"


class Readiness(str, Enum):
    READY_FOR_FINAL_CONSOLIDATION = "READY_FOR_FINAL_CONSOLIDATION"
    ONE_HARDENING_PACKAGE_REMAINS = "ONE_HARDENING_PACKAGE_REMAINS"
    MULTIPLE_HARDENING_PACKAGES_REMAIN = "MULTIPLE_HARDENING_PACKAGES_REMAIN"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class ProductionGap:
    identifier: str
    original_severity: Severity
    current_severity: Severity
    state: LifecycleState
    component: str
    consequence: str
    evidence: tuple[str, ...]
    original_recommendation: str
    decision: Decision
    decision_reason: str
    closure_evidence: tuple[str, ...] = ()
    reopening_evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("evidence", "closure_evidence", "reopening_evidence"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if not self.identifier.strip() or not self.evidence:
            raise ValueError("gap identifier and evidence are required")
        if self.state is LifecycleState.CLOSED and not self.closure_evidence:
            raise ValueError("CLOSED gaps require closure evidence")
        if self.state is LifecycleState.OPEN and self.closure_evidence and not self.reopening_evidence:
            raise ValueError("reopening a previously closed gap requires concrete reopening evidence")
        if self.decision is Decision.FIX_BEFORE_V1_CONSOLIDATION:
            if self.state not in {LifecycleState.OPEN, LifecycleState.PARTIALLY_CLOSED}:
                raise ValueError("FIX_BEFORE requires an open or partially closed gap")
            if not self.consequence.strip() or not self.evidence:
                raise ValueError("FIX_BEFORE requires a concrete consequence and evidence")
        if self.current_severity is Severity.INFORMATIONAL and self.decision is Decision.FIX_BEFORE_V1_CONSOLIDATION:
            raise ValueError("informational gaps cannot force implementation")


@dataclass(frozen=True, slots=True)
class ProductionHardeningGateResult:
    repository_commit_identity: str
    source_phase13a_commit: str
    source_phase13a_audit_commit: str
    source_phase13a_result_identity: str
    phase_commit_provenance: tuple[tuple[str, str], ...]
    gaps: tuple[ProductionGap, ...]
    new_concrete_regressions: tuple[str, ...]
    bundling_decision: BundleDecision
    bundling_evidence: tuple[str, ...]
    production_v1_boundary: tuple[str, ...]
    readiness: Readiness
    audit_complete: bool
    no_production_mutation: bool
    no_network_or_provider_calls: bool
    identity: str

    def __post_init__(self) -> None:
        for name in ("phase_commit_provenance", "gaps", "new_concrete_regressions", "bundling_evidence", "production_v1_boundary"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if len({gap.identifier for gap in self.gaps}) != len(self.gaps):
            raise ValueError("gap identifiers must be unique")
        expected = _result_identity(self)
        if self.identity and self.identity != expected:
            raise ValueError("result identity does not match immutable gate content")


def _gap(
    identifier: str,
    original: Severity,
    current: Severity,
    state: LifecycleState,
    component: str,
    consequence: str,
    evidence: tuple[str, ...],
    recommendation: str,
    decision: Decision,
    reason: str,
    closure: tuple[str, ...] = (),
) -> ProductionGap:
    return ProductionGap(identifier, original, current, state, component, consequence, evidence, recommendation, decision, reason, closure)


# Phase 13A's report and recovery_gaps.csv are canonical. They did not store a
# separate recommendation field; the value below states that provenance gap
# rather than inventing historical wording.
PHASE_13E_GAPS = (
    _gap("13A-M1-import-time-database-initialization", Severity.MATERIAL, Severity.MATERIAL, LifecycleState.OPEN, "IMPORT_SAFETY", "Importing core.database creates the parent directory and initializes market schema; importing strategy.scanner can also initialize paper state before an explicit operational command.", ("core/database.py:12-18", "core/database.py:467-468", "strategy/scanner.py:13-50", "execution/persistence.py:30-67"), "Original action not recorded as a distinct field in Phase 13A artifacts; gap retained for a bounded import-safety fix.", Decision.FIX_BEFORE_V1_CONSOLIDATION, "Read-only tooling/imports can create or migrate persistent state unexpectedly."),
    _gap("13A-M2-post-update-integrity-gate", Severity.MATERIAL, Severity.MATERIAL, LifecycleState.OPEN, "MARKET_DATA", "Daily update completion is not an all-symbol data-integrity result. The session guard uses get_reference_market_date(), which excludes VNINDEX; it does not prove the benchmark has a valid current close or verify duplicate keys/OHLCV/session coverage before stateful consumers.", ("app/daily_pipeline.py:_run_data_stage", "app/daily_pipeline.py:run", "core/database.py:get_reference_market_date", "scripts/run_paper_lifecycle.py:main", "scripts/run_daily.py:update_market_data", "scripts/verify_market_data_integrity.py:main"), "Original action not recorded as a distinct field in Phase 13A artifacts; add a bounded, read-only pre-consumer integrity gate.", Decision.FIX_BEFORE_V1_CONSOLIDATION, "Other equities can have the current date while VNINDEX is stale or invalid; stateful consumers can proceed without proving canonical data integrity."),
    _gap("13A-M3-paper-lifecycle-transaction-boundaries", Severity.MATERIAL, Severity.MATERIAL, LifecycleState.CLOSED, "PAPER_RECOVERY", "Historical report found entry/exit state persisted across independent transaction boundaries, risking divergent paper account records after interruption.", ("execution/paper_broker.py:submit_order", "execution/lifecycle_manager.py:run", "execution/persistence.py"), "Original action not recorded as a distinct field in Phase 13A artifacts; paper execution/lifecycle bundle hardening.", Decision.NO_ACTION_REQUIRED, "Protected shared entry and exit paths now persist economic and lifecycle state atomically; legacy rows remain manual-review cases.", ("Phase 13B commit edaf78a: source-linked entry transaction", "Phase 13D commit ee96270: atomic exit bundle and lifecycle deletion", "Phase 13D focused validation: 65 passed")),
    _gap("13A-M4-pending-fill-replay-idempotency", Severity.MATERIAL, Severity.MATERIAL, LifecycleState.CLOSED, "PAPER_RECOVERY", "A retry after a durable fill but before queue completion could previously create a second economic entry.", ("execution/signal_executor.py:execute_pending_signals", "execution/models.py:Order.client_order_id", "execution/persistence.py:queue_signal/complete_pending_signal"), "Original action not recorded as a distinct field in Phase 13A artifacts; durable idempotent source intent for pending execution.", Decision.NO_ACTION_REQUIRED, "Phase 13B source intent and Phase 13D recovery protect the shared operational path; do not reopen absent a concrete regression.", ("Phase 13B commit edaf78a: pending_signal source intent and atomic entry bundle", "Phase 13D commit ee96270: recovery regression suite")),
    _gap("13A-M5-scheduled-market-database-path-mismatch", Severity.MATERIAL, Severity.MATERIAL, LifecycleState.OPEN, "DEPLOYMENT", "The workflow neither restores the ignored historical market DB nor bootstraps one: on a clean checkout the updater has no per-symbol latest dates and returns NEEDS_BACKFILL, so the Forward stage stops the run. If a DB is provisioned externally, the application updates data/market.db but the workflow force-adds root market.db.", (".github/workflows/daily_scan.yml:Checkout repository through Run Quant Bot (no DB restore/cache)", ".gitignore:*.db", "git ls-files -- market.db data/market.db (neither tracked)", "core.paths:resolve_market_database_path", "core.universe:get_all_symbols", "scripts/update_data.py:calculate_start_date", "scripts/update_data.py:update_all_symbols", "scripts/run_daily.py:update_market_data", ".github/workflows/daily_scan.yml:Save updated database"), "Define how the canonical historical DB is provisioned in Actions, then use that exact canonical path for update and persistence.", Decision.FIX_BEFORE_V1_CONSOLIDATION, "The scheduled workflow cannot incrementally update without a seeded database; a manually seeded run would still persist the wrong path."),
    _gap("13A-m1-partial-update-continuation-wording", Severity.MINOR, Severity.MINOR, LifecycleState.OPEN, "PIPELINE_FAILURE", "The update stage prints that the pipeline continues on failed symbols, but the configured Forward stage sees the warning and returns failure before lifecycle/scanner.", ("app/daily_pipeline.py:_run_data_stage", "app/daily_pipeline.py:run"), "Correct the operator-facing message or make behavior explicit.", Decision.DEFER_WITH_DOCUMENTATION, "Current downstream behavior fails closed; the mismatch is confusing but not an unsafe continuation."),
    _gap("13A-m2-telegram-send-retry-ambiguity", Severity.MINOR, Severity.MINOR, LifecycleState.OPEN, "PRESENTATION", "A send timeout after provider acceptance may cause a bounded retry to duplicate a delivered Telegram chunk.", ("services/telegram_client.py:send_message", "services/telegram_client.py:_send_chunk"), "Document ambiguous delivery; provider-side idempotency is unavailable in current contract.", Decision.DEFER_WITH_DOCUMENTATION, "Presentation duplication does not mutate canonical trading state and cannot be made exactly-once with the current provider contract."),
    _gap("13A-m3-telegram-query-offset-ordering", Severity.MINOR, Severity.MINOR, LifecycleState.OPEN, "PRESENTATION", "Advancing update_id before successful handling can lose a query reply if processing fails.", ("services/telegram_bot/bot.py:run_forever", "services/telegram_bot/bot.py:_handle_update"), "Document recoverable query-response loss; defer offset redesign.", Decision.DEFER_WITH_DOCUMENTATION, "Failure affects a user response, not paper/market/Forward state."),
    _gap("13A-i1-deployment-runtime-provenance", Severity.INFORMATIONAL, Severity.INFORMATIONAL, LifecycleState.OPEN, "DEPLOYMENT", "Tracked GitHub Actions workflow exists, but no tracked VPS service/timer or runtime supervisor state proves separate deployment behavior.", (".github/workflows/daily_scan.yml", "services/telegram_bot/README.md", "Phase 13A deployment provenance partial"), "Keep runtime deployment provenance explicit; no unverified host configuration is inferred.", Decision.DEFER_WITH_DOCUMENTATION, "No immediate code-safety consequence is evidenced by missing host-local deployment state."),
    _gap("13A-i2-paper-database-cwd-relative-paths", Severity.INFORMATIONAL, Severity.INFORMATIONAL, LifecycleState.OPEN, "CONFIGURATION", "Paper database defaults and relative overrides are interpreted against process CWD, unlike repository-anchored market DB resolution.", ("execution/signal_executor.py:PaperExecutionConfig.from_env", "scripts/run_paper_lifecycle.py:main", "scripts/run_paper_v2_lifecycle.py:main", "scripts/run_paper_v3_lifecycle.py:main"), "Document repository-root launch requirement and explicit absolute-path override.", Decision.DEFER_WITH_DOCUMENTATION, "Tracked operational workflow launches from repository root; no observed cross-CWD production invocation."),
)


_BOUNDARY = (
    "Market and paper database paths are explicit and consistent across supported launchers.",
    "Read-only imports do not create, migrate, or mutate persistent state.",
    "Incomplete or invalid canonical market data cannot silently reach stateful daily consumers.",
    "Protected paper entry/exit execution and Forward V1 identity/state remain retry-safe.",
    "Operational failures stop safely; known legacy and deployment limitations are explicit.",
    "V1 does not claim institutional execution, broker connectivity, HA, or zero technical debt.",
)


def _gap_payload(gap: ProductionGap) -> dict[str, Any]:
    payload = asdict(gap)
    for key in ("original_severity", "current_severity", "state", "decision"):
        payload[key] = getattr(gap, key).value
    return payload


def _result_identity(result: ProductionHardeningGateResult) -> str:
    payload = {
        "contract": CONTRACT,
        "version": VERSION,
        "repository_commit_identity": result.repository_commit_identity,
        "source_phase13a_commit": result.source_phase13a_commit,
        "source_phase13a_audit_commit": result.source_phase13a_audit_commit,
        "source_phase13a_result_identity": result.source_phase13a_result_identity,
        "phase_commit_provenance": result.phase_commit_provenance,
        "gaps": tuple(_gap_payload(gap) for gap in result.gaps),
        "new_concrete_regressions": result.new_concrete_regressions,
        "bundling_decision": result.bundling_decision.value,
        "bundling_evidence": result.bundling_evidence,
        "production_v1_boundary": result.production_v1_boundary,
        "readiness": result.readiness.value,
        "audit_complete": result.audit_complete,
        "no_production_mutation": result.no_production_mutation,
        "no_network_or_provider_calls": result.no_network_or_provider_calls,
    }
    return sha256(canonical_json(canonical_identity_value(payload))).hexdigest()


def build_production_hardening_gate(repository_commit_identity: str) -> ProductionHardeningGateResult:
    commit = repository_commit_identity.strip().lower()
    if len(commit) < 7 or any(character not in "0123456789abcdef" for character in commit):
        raise ValueError("repository commit identity must be a hexadecimal Git object id")
    provenance = (("13B", PHASE_13B_COMMIT), ("13C", PHASE_13C_COMMIT), ("13D", PHASE_13D_COMMIT))
    fix_gaps = tuple(gap.identifier for gap in PHASE_13E_GAPS if gap.decision is Decision.FIX_BEFORE_V1_CONSOLIDATION)
    bundling = BundleDecision.ONE_BOUNDED_HARDENING_PACKAGE if len(fix_gaps) > 1 else BundleDecision.NO_FURTHER_HARDENING_REQUIRED
    readiness = Readiness.ONE_HARDENING_PACKAGE_REMAINS if bundling is BundleDecision.ONE_BOUNDED_HARDENING_PACKAGE else Readiness.READY_FOR_FINAL_CONSOLIDATION
    result = ProductionHardeningGateResult(
        repository_commit_identity=commit,
        source_phase13a_commit=PHASE_13A_COMMIT,
        source_phase13a_audit_commit=PHASE_13A_AUDIT_COMMIT,
        source_phase13a_result_identity=PHASE_13A_RESULT_IDENTITY,
        phase_commit_provenance=provenance,
        gaps=PHASE_13E_GAPS,
        new_concrete_regressions=(),
        bundling_decision=bundling,
        bundling_evidence=(
            "Three bounded operational reliability changes share the canonical daily boundary: make imports side-effect-safe, validate the database actually updated, and persist that canonical path in scheduled workflow.",
            "The paper recovery path is closed and excluded from the proposed package.",
            "No production fix is implemented by this gate.",
        ),
        production_v1_boundary=_BOUNDARY,
        readiness=readiness,
        audit_complete=True,
        no_production_mutation=True,
        no_network_or_provider_calls=True,
        identity="",
    )
    object.__setattr__(result, "identity", _result_identity(result))
    return result


def write_production_hardening_gate_artifacts(result: ProductionHardeningGateResult, output_directory: str | Path) -> MappingProxyType:
    import csv
    import json

    target = Path(output_directory).resolve()
    if target.exists():
        raise FileExistsError(f"production hardening output already exists: {target}")
    target.mkdir(parents=True, exist_ok=False)

    def write_csv(path: Path, fields: tuple[str, ...], rows: tuple[dict[str, str], ...]) -> None:
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    gap_rows = tuple({
        "identifier": gap.identifier,
        "original_severity": gap.original_severity.value,
        "current_severity": gap.current_severity.value,
        "state": gap.state.value,
        "component": gap.component,
        "consequence": gap.consequence,
        "evidence": " | ".join(gap.evidence),
        "original_recommendation": gap.original_recommendation,
        "decision": gap.decision.value,
        "decision_reason": gap.decision_reason,
        "closure_evidence": " | ".join(gap.closure_evidence),
        "reopening_evidence": " | ".join(gap.reopening_evidence),
    } for gap in result.gaps)
    write_csv(target / "production_gap_register.csv", tuple(gap_rows[0]), gap_rows)
    decisions = tuple({"identifier": gap.identifier, "decision": gap.decision.value, "reason": gap.decision_reason} for gap in result.gaps)
    write_csv(target / "production_hardening_decisions.csv", tuple(decisions[0]), decisions)
    report = [
        "# Phase 13E — Production hardening decision gate", "",
        f"- Commit: `{result.repository_commit_identity}`",
        f"- Result identity: `{result.identity}`",
        f"- Readiness: **{result.readiness.value}**",
        f"- Bundling: **{result.bundling_decision.value}**", "",
        "## Gap decisions", "",
        *(f"- **{gap.identifier} — {gap.current_severity.value}/{gap.state.value}: {gap.decision.value}.** {gap.decision_reason}" for gap in result.gaps), "",
        "## Production V1 boundary", "", *(f"- {item}" for item in result.production_v1_boundary), "",
        "No production behavior, canonical database, workflow, or provider was changed or invoked.",
    ]
    (target / "production_hardening_gate_report.md").write_text("\n".join(report) + "\n", encoding="utf-8", newline="\n")
    artifact_names = ("production_gap_register.csv", "production_hardening_decisions.csv", "production_hardening_gate_report.md")
    hashes = {name: sha256((target / name).read_bytes()).hexdigest() for name in artifact_names}
    manifest = {
        "contract": CONTRACT,
        "version": VERSION,
        "phase": "13E",
        "source_phase13a": {"implementation_commit": result.source_phase13a_commit, "audit_run_commit": result.source_phase13a_audit_commit, "result_identity": result.source_phase13a_result_identity},
        "source_phase13a_artifact_sha256": dict(PHASE_13A_SOURCE_ARTIFACTS),
        "phase_commit_provenance": dict(result.phase_commit_provenance),
        "repository_commit_identity": result.repository_commit_identity,
        "result_identity": result.identity,
        "gap_count": len(result.gaps),
        "gaps": tuple({"identifier": gap.identifier, "original_severity": gap.original_severity.value, "current_severity": gap.current_severity.value, "state": gap.state.value, "decision": gap.decision.value, "evidence": gap.evidence, "consequence": gap.consequence} for gap in result.gaps),
        "new_concrete_regressions": result.new_concrete_regressions,
        "bundling_decision": result.bundling_decision.value,
        "bundling_evidence": result.bundling_evidence,
        "production_v1_boundary": result.production_v1_boundary,
        "readiness": result.readiness.value,
        "audit_complete": result.audit_complete,
        "no_production_mutation": result.no_production_mutation,
        "no_network_or_provider_calls": result.no_network_or_provider_calls,
        "artifacts": dict(sorted(hashes.items())),
    }
    manifest_path = target / "production_hardening_gate_manifest.json"
    manifest_path.write_bytes(canonical_json(canonical_identity_value(manifest)) + b"\n")
    hashes[manifest_path.name] = sha256(manifest_path.read_bytes()).hexdigest()
    return MappingProxyType(dict(sorted(hashes.items())))


__all__ = (
    "BundleDecision", "Decision", "LifecycleState", "ProductionGap", "ProductionHardeningGateResult",
    "Readiness", "Severity", "build_production_hardening_gate", "write_production_hardening_gate_artifacts",
)
