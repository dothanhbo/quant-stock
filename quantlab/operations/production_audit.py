from __future__ import annotations

"""Immutable evidence-based audit model for the tracked production surface.

The builder is pure: it records source-inspection conclusions and sanitized,
read-only ledger counts supplied by the separate runner. It never imports
operational services, opens databases, reads environment values, or performs
network/process actions.
"""

from dataclasses import asdict, dataclass
from enum import Enum
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Mapping

from quantlab.identity import canonical_identity_value, canonical_json


CONTRACT = "quantlab.production_surface_audit"
VERSION = "v1"


class RetrySafety(str, Enum):
    IDEMPOTENT = "IDEMPOTENT"
    RETRY_SAFE = "RETRY_SAFE"
    CONDITIONALLY_RETRY_SAFE = "CONDITIONALLY_RETRY_SAFE"
    NOT_RETRY_SAFE = "NOT_RETRY_SAFE"
    UNKNOWN = "UNKNOWN"


class AuditSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    MATERIAL = "MATERIAL"
    MINOR = "MINOR"
    INFORMATIONAL = "INFORMATIONAL"


@dataclass(frozen=True, slots=True)
class OperationalEntryPoint:
    name: str
    path: str
    classification: str
    command_or_trigger: str
    stage_or_purpose: str
    persistent_state: tuple[str, ...]
    side_effects: tuple[str, ...]
    retry_safety: RetrySafety
    evidence: tuple[str, ...]
    notes: str = ""


@dataclass(frozen=True, slots=True)
class PersistentStateStore:
    name: str
    default_location: str
    classification: str
    owners: tuple[str, ...]
    readers: tuple[str, ...]
    mutation_semantics: str
    recovery_notes: str
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SideEffectBoundary:
    stage: str
    effect: str
    possible: bool
    evidence: tuple[str, ...]
    boundary_note: str


@dataclass(frozen=True, slots=True)
class FailureScenario:
    scenario: str
    already_committed: str
    remains_uncommitted: str
    later_stages_run: str
    state_consistency: str
    retry_safety: RetrySafety
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecoveryGap:
    severity: AuditSeverity
    title: str
    consequence: str
    evidence: tuple[str, ...]
    affected_surface: str


@dataclass(frozen=True, slots=True)
class ConfigContract:
    name: str
    classification: str
    scope: str
    evidence: tuple[str, ...]
    note: str = ""


@dataclass(frozen=True, slots=True)
class ImportSafety:
    module: str
    classification: str
    possible_effect: str
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProductionAuditSpec:
    contract: str = CONTRACT
    version: str = VERSION
    retry_taxonomy: tuple[str, ...] = tuple(item.value for item in RetrySafety)
    severity_taxonomy: tuple[str, ...] = tuple(item.value for item in AuditSeverity)
    read_only: bool = True
    network_disabled: bool = True
    no_production_changes: bool = True
    fingerprint: str = ""

    def __post_init__(self) -> None:
        payload = {
            "contract": self.contract, "version": self.version,
            "retry_taxonomy": self.retry_taxonomy, "severity_taxonomy": self.severity_taxonomy,
            "semantics": "classify from tracked source and explicitly observed state only; UNKNOWN fails closed",
            "restrictions": ("no_network", "no_state_mutation", "no_production_changes", "no_secret_values"),
        }
        object.__setattr__(self, "fingerprint", _digest(payload))


@dataclass(frozen=True, slots=True)
class ProductionAuditResult:
    repository_commit_identity: str
    canonical_daily_entry_point: str
    daily_stage_order: tuple[str, ...]
    entry_points: tuple[OperationalEntryPoint, ...]
    state_stores: tuple[PersistentStateStore, ...]
    side_effects: tuple[SideEffectBoundary, ...]
    failures: tuple[FailureScenario, ...]
    recovery_gaps: tuple[RecoveryGap, ...]
    configuration: tuple[ConfigContract, ...]
    import_safety: tuple[ImportSafety, ...]
    forward_recovery_conclusion: str
    paper_recovery_conclusion: str
    market_update_recovery_conclusion: str
    deployment_provenance: str
    tracked_deployment_evidence: tuple[str, ...]
    forward_ledger_observation: Mapping[str, int | bool | str]
    no_mutation_no_network: bool
    specification_fingerprint: str
    identity: str

    def __post_init__(self) -> None:
        for name in ("daily_stage_order", "entry_points", "state_stores", "side_effects", "failures", "recovery_gaps", "configuration", "import_safety", "tracked_deployment_evidence"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, "forward_ledger_observation", MappingProxyType(dict(sorted(self.forward_ledger_observation.items()))))


def _digest(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _validate_ledger_observation(observation: Mapping[str, Any]) -> Mapping[str, int | bool | str]:
    allowed = {
        "present", "schema_complete", "protocol_count", "activation_count", "formation_count",
        "pending_maturity_count", "matured_maturity_count", "unavailable_maturity_count",
        "outcome_count", "missing_formation_event_count", "identity_match",
    }
    if set(observation) - allowed:
        raise ValueError("ledger observation contains fields outside the sanitized allowlist")
    result: dict[str, int | bool | str] = {}
    for key, value in observation.items():
        if key in {"present", "schema_complete", "identity_match"}:
            if not isinstance(value, bool):
                raise TypeError(f"{key} must be boolean")
            result[key] = value
        else:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{key} must be a non-negative integer")
            result[key] = value
    if not result:
        raise ValueError("ledger observation must explicitly state whether the ledger exists")
    return MappingProxyType(dict(sorted(result.items())))


def _entry_points() -> tuple[OperationalEntryPoint, ...]:
    rows = (
        OperationalEntryPoint("daily_pipeline", "main.py -> scripts/run_daily.py", "CANONICAL_OPERATIONAL", "python main.py | python -m scripts.run_daily", "market update -> Forward V1 -> session guard -> paper lifecycle -> scanner/report", ("market.db", "forward_validation.db", "paper V1/V2/V3 DBs", "logs/update_data_failed.txt"), ("NETWORK_PROVIDER_READ", "DATABASE_WRITE", "FILE_WRITE", "TELEGRAM_SEND"), RetrySafety.CONDITIONALLY_RETRY_SAFE, ("main.py:1-5", "scripts/run_daily.py:103-201", "app/daily_pipeline.py:95-196"), "Workflow and CLI are tracked; stage state is committed independently."),
        OperationalEntryPoint("github_daily_workflow", ".github/workflows/daily_scan.yml", "CANONICAL_OPERATIONAL", "Weekday cron and workflow_dispatch", "checkout/install/run main.py/attempt market database commit and push", ("GitHub repository history", "market.db artifact"), ("PROCESS/SERVICE_ACTION", "NETWORK_PROVIDER_READ", "DATABASE_WRITE", "EXTERNAL_REPOSITORY_WRITE"), RetrySafety.CONDITIONALLY_RETRY_SAFE, (".github/workflows/daily_scan.yml:1-54",), "Workflow stages a root market.db although core.paths defaults to data/market.db."),
        OperationalEntryPoint("market_incremental_update", "scripts/update_data.py", "SUPPORTING_OPERATIONAL", "python -m scripts.update_data [--symbols ...]", "KBS incremental per-symbol refresh with overlap and retries", ("market.db", "logs/update_data_failed.txt"), ("NETWORK_PROVIDER_READ", "DATABASE_WRITE", "FILE_WRITE"), RetrySafety.CONDITIONALLY_RETRY_SAFE, ("scripts/update_data.py:17-20", "scripts/update_data.py:61-142", "scripts/update_data.py:298-475", "core/database.py:226-346"), "Each symbol batch commits independently; provider revisions may change retry results."),
        OperationalEntryPoint("scanner_and_broadcast", "strategy/scanner.py", "SUPPORTING_OPERATIONAL", "python -m strategy.scanner; also daily pipeline", "current VN100 scan, telemetry/signals, pending paper queue, Telegram presentation", ("market.db", "selected paper DB"), ("NETWORK_PROVIDER_READ", "DATABASE_WRITE", "TELEGRAM_SEND"), RetrySafety.CONDITIONALLY_RETRY_SAFE, ("strategy/scanner.py:42-50", "strategy/scanner.py:443-622", "core/signal_database.py:10-70"), "Import initializes market schema and instantiates configured paper executor/Telegram client."),
        OperationalEntryPoint("paper_lifecycle", "scripts/run_paper_lifecycle.py", "SUPPORTING_OPERATIONAL", "Daily V2/V3 wrapper calls main()", "pending next-open fills then existing-position lifecycle/exits", ("market.db", "selected paper DB"), ("DATABASE_WRITE",), RetrySafety.CONDITIONALLY_RETRY_SAFE, ("scripts/run_paper_lifecycle.py:58-157", "execution/lifecycle_manager.py:95-330"), "Separate database commits span fills, portfolio state, closed-trade and lifecycle records."),
        OperationalEntryPoint("paper_v2_wrapper", "scripts/run_paper_v2_lifecycle.py", "SUPPORTING_OPERATIONAL", "Imported by run_daily when V3 is not selected", "forces V2 settings and delegates common lifecycle", ("V2 paper DB", "market.db"), ("DATABASE_WRITE",), RetrySafety.CONDITIONALLY_RETRY_SAFE, ("scripts/run_paper_v2_lifecycle.py:8-23",), "Sets PAPER_DATABASE_PATH from PAPER_V2_DATABASE_PATH or data/paper_trading_v2.db."),
        OperationalEntryPoint("paper_v3_wrapper", "scripts/run_paper_v3_lifecycle.py", "SUPPORTING_OPERATIONAL", "Imported by run_daily for PAPER_STRATEGY_VERSION=V3_BREADTH_40_60", "sets frozen V3 environment and delegates common lifecycle", ("V3 paper DB", "market.db"), ("DATABASE_WRITE",), RetrySafety.CONDITIONALLY_RETRY_SAFE, ("scripts/run_paper_v3_lifecycle.py:8-32",), "Sets PAPER_DATABASE_PATH from PAPER_V3_DATABASE_PATH or data/paper_trading_v3.db."),
        OperationalEntryPoint("telegram_query_service", "services/telegram_bot/app.py", "SUPPORTING_OPERATIONAL", "python -m services.telegram_bot.app", "long-poll queries, scans a symbol, optional Gemini interpretation, reply", ("market.db",), ("NETWORK_PROVIDER_READ", "TELEGRAM_SEND", "OPTIONAL_AI_API"), RetrySafety.CONDITIONALLY_RETRY_SAFE, ("services/telegram_bot/app.py:7-17", "services/telegram_bot/bot.py:39-146", "services/telegram_bot/query.py:26-91"), "Read-only with respect to portfolio/market records apart from import-time initialization; update offset advances before handling."),
        OperationalEntryPoint("market_backfill", "scripts/backfill_market_data.py", "MAINTENANCE", "python -m scripts.backfill_market_data [--symbols ...]", "provider history backfill with per-symbol writes", ("market.db",), ("NETWORK_PROVIDER_READ", "DATABASE_WRITE"), RetrySafety.CONDITIONALLY_RETRY_SAFE, ("scripts/backfill_market_data.py:27-99", "scripts/backfill_market_data.py:112-215")),
        OperationalEntryPoint("forward_activation_and_daily", "quantlab/forward/protocol.py; quantlab/forward/daily.py", "SUPPORTING_OPERATIONAL", "Activation is an explicit separate operation; daily called by canonical pipeline", "append-only prospective formation/maturity/outcome/gap recording", ("forward_validation.db", "market.db", "tracked Phase 8 protocol sources"), ("DATABASE_WRITE",), RetrySafety.CONDITIONALLY_RETRY_SAFE, ("quantlab/forward/protocol.py:48-111", "quantlab/forward/daily.py:155-305", "quantlab/forward/ledger.py:20-135"), "Daily refuses an unactivated/mismatched protocol; no backfill of missed formation dates."),
        OperationalEntryPoint("telegram_client", "services/telegram_client.py", "SUPPORTING_OPERATIONAL", "Called by scanner or query bot", "chunked sendMessage with bounded retry", (), ("TELEGRAM_SEND",), RetrySafety.CONDITIONALLY_RETRY_SAFE, ("services/telegram_client.py:69-181",), "Timeout after remote acceptance can make retry delivery ambiguous."),
        OperationalEntryPoint("maintenance_utilities", "scripts/init_db.py; scripts/verify_market_data_integrity.py; scripts/quarantine_invalid_ohlc.py; scripts/register_paper_lifecycle.py; scripts/backfill_position_lifecycle.py; scripts/migrate_open_positions_policy.py", "MAINTENANCE", "Explicit operator CLI invocation", "schema initialization, checks, quarantine, registration/backfill/migration", ("market.db", "paper DB"), ("DATABASE_WRITE",), RetrySafety.UNKNOWN, ("scripts/init_db.py:1-5", "scripts/quarantine_invalid_ohlc.py:20-57", "scripts/register_paper_lifecycle.py", "scripts/backfill_position_lifecycle.py", "scripts/migrate_open_positions_policy.py"), "Quarantine has an explicit --apply mode; lifecycle migration tools can mutate existing state."),
        OperationalEntryPoint("research_runners", "research/*.py", "RESEARCH_ONLY", "Explicit research invocation", "historical analysis, backtest, artifact generation", ("market.db read-only for supported runners", "research_results"), ("DATABASE_READ", "FILE_WRITE"), RetrySafety.UNKNOWN, ("research/run_quantlab_monitoring_snapshot.py", "research/run_quantlab_monitoring_history.py"), "Not a normal production scheduler; each runner must be individually audited before use."),
    )
    return tuple(sorted(rows, key=lambda item: (item.classification, item.name)))


def _state_stores() -> tuple[PersistentStateStore, ...]:
    return (
        PersistentStateStore("canonical market database", "core.paths: project-root/data/market.db; MARKET_DATABASE_PATH override", "MUTABLE", ("scripts.update_data", "scripts.backfill_market_data", "strategy.scanner telemetry/signals", "schema initialization"), ("scanner", "paper execution/lifecycle", "forward daily", "reports/maintenance"), "Prices upsert per symbol transaction; scanner writes telemetry/signals; schema is created by core.database import.", "A partially completed universe refresh remains partly committed; restore/rebuild provenance is provider-dependent.", ("core/paths.py", "core/database.py", "scripts/update_data.py", "strategy/scanner.py")),
        PersistentStateStore("paper V1 state", "PAPER_DATABASE_PATH or common data/paper_trading.db default", "MUTABLE", ("PaperSignalExecutor", "PaperBroker", "PaperLifecycleManager"), ("paper lifecycle", "scanner queue/fill", "reporting"), "WAL SQLite; individual order, fill, portfolio, lifecycle, closed-trade, pending rows commit in separate transactions.", "No transaction spans a complete lifecycle or the fill plus pending-row completion; restart can observe an intermediate state.", ("execution/persistence.py", "execution/paper_broker.py", "scripts/run_paper_lifecycle.py")),
        PersistentStateStore("paper V2 state", "PAPER_V2_DATABASE_PATH or data/paper_trading_v2.db; passed via PAPER_DATABASE_PATH", "MUTABLE", ("V2 lifecycle wrapper/common lifecycle", "V2 scanner pending queue"), ("V2 lifecycle", "V2 scanner", "reports"), "Same SQLite store and transaction boundaries as paper V1, isolated by wrapper-selected path unless environment paths overlap.", "Relative paper paths resolve against process CWD; environment overrides can defeat intended isolation if pointed to same file.", ("scripts/run_paper_v2_lifecycle.py", "execution/signal_executor.py")),
        PersistentStateStore("paper V3 state", "PAPER_V3_DATABASE_PATH or data/paper_trading_v3.db; passed via PAPER_DATABASE_PATH", "MUTABLE", ("V3 lifecycle wrapper/common lifecycle", "V3 scanner pending queue"), ("V3 lifecycle", "V3 scanner", "reports"), "Same SQLite store and transaction boundaries as paper V1, isolated by wrapper-selected path unless environment paths overlap.", "Relative paper paths resolve against process CWD; environment overrides can defeat intended isolation if pointed to same file.", ("scripts/run_paper_v3_lifecycle.py", "execution/signal_executor.py")),
        PersistentStateStore("Forward V1 evidence ledger", "data/forward_validation.db", "APPEND_ONLY", ("ForwardValidationLedger activation/daily operations"), ("quantlab.forward.daily", "monitoring reads"), "Unique keys plus no-update/no-delete triggers; each ledger method commits independently; explicit initialize/activate mutates schema/protocol.", "Formation is atomic across formation/positions/pending maturities; later maturity/outcome methods are separate and daily replay can resume missing later rows.", ("quantlab/forward/ledger.py", "quantlab/forward/daily.py")),
        PersistentStateStore("local environment and strategy configuration", ".env (ignored/local); config/strategy.yaml; research/forward_validation/protocol_v1.json", "EXTERNAL", ("operator/deployment configuration"), ("daily pipeline", "TradingPolicy", "Telegram/AI/provider adapters", "Forward protocol loader"), "Secrets/config are read at runtime; code has defaults and wrappers mutate process environment.", "No single validated deployment config contract; `.env` search/CWD and relative paths can drift.", ("scripts/run_daily.py", "config/strategy_loader.py", "config/trading_policy.py", "quantlab/forward/protocol.py")),
        PersistentStateStore("failed-update log", "logs/update_data_failed.txt (CWD-relative)", "MUTABLE", ("scripts.update_data.write_failed_log"), ("operator/manual retry"), "Rewritten with current unresolved symbols and timestamp; deleted on full success.", "A changed working directory relocates log; a successful run erases prior failure list.", ("scripts/update_data.py:329-365",)),
        PersistentStateStore("GitHub workflow/repository state", "GitHub Actions job workspace and remote branch", "EXTERNAL", (".github/workflows/daily_scan.yml"), ("scheduled workflow"), "On success workflow stages/commits/pulls/pushes a database file.", "Tracked workflow targets root market.db while canonical application default is data/market.db; no tracked VPS service unit establishes runtime parity.", (".github/workflows/daily_scan.yml", "core/paths.py")),
    )


def _side_effects() -> tuple[SideEffectBoundary, ...]:
    return (
        SideEffectBoundary("update market", "NETWORK_PROVIDER_READ", True, ("scripts/update_data.py", "vnstock.api.quote.Quote.history"), "Provider read; one symbol's rows saved after validation."),
        SideEffectBoundary("update market", "DATABASE_WRITE", True, ("core/database.py:save_price_data",), "INSERT OR REPLACE, one transaction per symbol call."),
        SideEffectBoundary("import core.database", "DATABASE_WRITE/SCHEMA_CREATE", True, ("core/database.py:467-468",), "Import creates prices/signals tables and parent directory/engine; not a read-only import."),
        SideEffectBoundary("scanner", "NETWORK_PROVIDER_READ", True, ("core/universe.py:get_vn100_symbols", "strategy/scanner.py:scan_all_symbols"), "Current VN100 universe resolution can contact provider; exact call behavior depends on core.universe cache/fallback."),
        SideEffectBoundary("scanner", "DATABASE_WRITE", True, ("core/scan_telemetry.py:persist_scan_telemetry", "core/signal_database.py:save_signal", "execution/signal_executor.py:queue_signals"), "Scan telemetry, signal history, pending signal and paper state writes."),
        SideEffectBoundary("paper lifecycle", "DATABASE_WRITE", True, ("execution/persistence.py", "execution/lifecycle_manager.py"), "Fills, position/cash snapshots, lifecycle state, closed-trade rows."),
        SideEffectBoundary("Forward V1 daily", "DATABASE_WRITE", True, ("quantlab/forward/daily.py", "quantlab/forward/ledger.py"), "Append-only formation, maturity, outcome and missing-session events."),
        SideEffectBoundary("scanner/query bot", "TELEGRAM_SEND", True, ("services/telegram_client.py",), "External message side effect; scanner catches presentation failures."),
        SideEffectBoundary("Telegram query", "OPTIONAL_AI_API", True, ("services/telegram_bot/analyst.py:GeminiAnalyst.analyze",), "Only enabled with configuration and queried symbol; result is presentation-only."),
        SideEffectBoundary("tracked production surface", "BROKER_ACTION", False, ("execution/paper_broker.py",), "Only simulated PaperBroker exists in inspected path; no broker order API call found."),
        SideEffectBoundary("GitHub Actions", "PROCESS/SERVICE_ACTION + EXTERNAL_REPOSITORY_WRITE", True, (".github/workflows/daily_scan.yml",), "Scheduled workflow runs Python and conditionally commits/pushes a database artifact."),
    )


def _failures() -> tuple[FailureScenario, ...]:
    rows = (
        FailureScenario("provider/update raises before any symbol is saved", "Earlier symbol commits, if any, remain; failing symbol write is not committed.", "Current/later symbols and later stages.", "Daily pipeline stops at update stage.", "Consistent per committed symbol; incomplete coverage until retry.", RetrySafety.CONDITIONALLY_RETRY_SAFE, ("scripts/update_data.py:update_symbol", "app/daily_pipeline.py:_run_data_stage")),
        FailureScenario("partial symbol update with unresolved symbols", "Successful symbols and their rows remain committed; failed log is rewritten.", "Forward/lifecycle/scanner are blocked because partial-update warning becomes Forward Validation failure.", "No later stage runs (regardless of stop_on_data_errors flag in normal daily composition).", "Market DB is intentionally partial; rerun can refresh seven-day overlap.", RetrySafety.CONDITIONALLY_RETRY_SAFE, ("scripts/update_data.py:update_all_symbols", "app/daily_pipeline.py:run")),
        FailureScenario("invalid OHLC/time for one provider batch", "Earlier symbols remain committed; the current save batch is rejected before SQL write.", "Current symbol rows and later stages.", "Updater records symbol failure; Forward stage blocks downstream.", "Per-symbol validation is atomic before insert, but no complete post-update integrity gate runs in daily path.", RetrySafety.CONDITIONALLY_RETRY_SAFE, ("core/database.py:save_price_data", "scripts/update_data.py:update_symbol")),
        FailureScenario("Forward formation recording interruption", "No rows before transaction commit, or complete formation/positions/pending maturities after commit.", "Later maturity/outcome processing if interrupted after formation commit.", "Daily run stops; rerun sees unique protocol/session and same immutable formation identity.", "Formation bundle is transactional and duplicate replay is rejected/treated existing.", RetrySafety.IDEMPOTENT, ("quantlab/forward/ledger.py:record_formation", "quantlab/forward/daily.py:run_forward_validation_daily")),
        FailureScenario("Forward maturity/outcome processing interruption", "Earlier method-level maturity/outcome transactions remain; current method transaction rolls back on exception.", "Later horizons/outcomes/gap events.", "Daily run stops at Forward stage.", "Rerun reads latest maturity and existing outcome keys; unique identities permit continuation; no historical formation backfill.", RetrySafety.CONDITIONALLY_RETRY_SAFE, ("quantlab/forward/ledger.py:record_maturities", "record_outcomes", "record_audit_events", "quantlab/forward/daily.py")),
        FailureScenario("paper next-open fill before pending row completion", "Order/fill/portfolio state may commit while queue row remains PENDING.", "Pending completion status and later lifecycle/scanner.", "Lifecycle call errors or process interruption can stop daily flow.", "Not atomic across order fill and pending status; UUID order IDs do not deduplicate replay by pending id.", RetrySafety.NOT_RETRY_SAFE, ("execution/signal_executor.py:execute_pending_signals", "execution/models.py:Order", "execution/persistence.py:complete_pending_signal")),
        FailureScenario("paper lifecycle interruption during exit", "Individual mark/exit order/fill/portfolio writes may already be committed.", "Closed-trade/lifecycle cleanup or later positions.", "Daily run stops before scanner if lifecycle raises.", "Manager is not one transaction; closed-trade insert, lifecycle deletion and broker fill are separate; restart may retain incomplete audit/lifecycle state.", RetrySafety.CONDITIONALLY_RETRY_SAFE, ("execution/lifecycle_manager.py:run", "execution/paper_broker.py:submit_order", "execution/persistence.py")),
        FailureScenario("scanner failure before/among persistence or queueing", "Telemetry/signals and already queued candidates are committed independently.", "Remaining signal rows/queues and Telegram message.", "Scanner stage fails and daily pipeline ends.", "Signal and pending rows have date/symbol uniqueness, but batch is not atomic; rerunning is conditionally deduplicated.", RetrySafety.CONDITIONALLY_RETRY_SAFE, ("strategy/scanner.py:run_scan", "core/signal_database.py:save_signal", "execution/persistence.py:queue_signal")),
        FailureScenario("Telegram broadcast/query failure", "Any preceding market/forward/paper/signal writes remain; scanner wraps broadcast errors and continues.", "Message delivery only; query reply may be lost.", "Broadcast presentation exception does not fail scanner; query poll loop catches and retries.", "Core state is separate; remote timeout/retry can lose or duplicate presentation.", RetrySafety.CONDITIONALLY_RETRY_SAFE, ("strategy/scanner.py:run_scan", "services/telegram_client.py", "services/telegram_bot/bot.py:run_forever")),
        FailureScenario("missing/invalid configuration or locked/unavailable database", "Prior committed stages remain; failed transaction rolls back locally where connection context supports it.", "Current stage and all later pipeline stages.", "Exception captured as failed stage; daily returns nonzero; KeyboardInterrupt returns 130.", "No cross-stage rollback; import-time schema initialization may already have created tables.", RetrySafety.UNKNOWN, ("scripts/run_daily.py:main", "app/daily_pipeline.py:_run_stage", "execution/persistence.py:_connection")),
        FailureScenario("unexpected process interruption between stages", "All already committed per-method/per-symbol state remains.", "Unstarted stages and uncommitted current transaction.", "Process stops immediately.", "No durable run journal/checkpoint; safe recovery is stage-dependent, not whole-pipeline atomic.", RetrySafety.UNKNOWN, ("app/daily_pipeline.py:run", "market/forward/paper persistence boundaries")),
    )
    return tuple(rows)


def _gaps() -> tuple[RecoveryGap, ...]:
    rows = (
        RecoveryGap(AuditSeverity.MATERIAL, "Import-time database initialization", "Importing core.database creates database parent/schema and signals table; scanner and some maintenance imports can mutate persistent state before an explicit command.", ("core/database.py:201-226", "core/database.py:467-468", "strategy/scanner.py:13-17"), "IMPORT_SAFETY"),
        RecoveryGap(AuditSeverity.MATERIAL, "Pending-fill replay is not idempotent across a crash window", "A paper fill may persist while its pending signal remains PENDING; retry uses a newly generated UUID order and may apply the same candidate again.", ("execution/signal_executor.py:execute_pending_signals", "execution/models.py:Order.client_order_id", "execution/persistence.py:queue_signal/complete_pending_signal"), "PAPER_RECOVERY"),
        RecoveryGap(AuditSeverity.MATERIAL, "Paper lifecycle updates are not transactionally bundled", "Cash/position/fill, closed-trade history and lifecycle cleanup can persist at different boundaries, leaving recoverable but divergent virtual-account records after interruption.", ("execution/paper_broker.py:submit_order", "execution/lifecycle_manager.py:run", "execution/persistence.py"), "PAPER_RECOVERY"),
        RecoveryGap(AuditSeverity.MATERIAL, "Scheduled workflow database path mismatch", "Workflow force-adds root market.db while canonical resolver defaults to data/market.db; intended market refresh may not be the file committed, or the job may fail when root file is absent.", (".github/workflows/daily_scan.yml:43-54", "core/paths.py:8-31"), "DEPLOYMENT"),
        RecoveryGap(AuditSeverity.MATERIAL, "No complete post-update integrity gate in canonical daily flow", "Per-symbol input validation exists, but cross-symbol freshness/schema/duplicate audit is not invoked before Forward/position/scanner consumers; one anomalous but accepted provider result can flow downstream.", ("scripts/run_daily.py", "app/daily_pipeline.py", "core/database.py:save_price_data", "scripts/verify_market_data_integrity.py"), "MARKET_DATA"),
        RecoveryGap(AuditSeverity.MINOR, "Partial-update continuation wording/behavior diverges", "DailyPipeline says scanner may continue on stale rows, but its always-configured Forward stage converts any update warning into failure and exits before lifecycle/scanner.", ("app/daily_pipeline.py:_run_data_stage", "app/daily_pipeline.py:run"), "PIPELINE_FAILURE"),
        RecoveryGap(AuditSeverity.MINOR, "Telegram query offset advances before successful processing", "If query handling fails after update_id offset advances, long polling may not redeliver the update; the user can lose a query reply without core-state impact.", ("services/telegram_bot/bot.py:run_forever", "services/telegram_bot/bot.py:_handle_update"), "PRESENTATION"),
        RecoveryGap(AuditSeverity.MINOR, "Send retry can duplicate a delivered Telegram message", "A timeout after Telegram accepted a chunk is ambiguous; bounded retry has no provider idempotency key.", ("services/telegram_client.py:send_message", "services/telegram_client.py:_send_chunk"), "PRESENTATION"),
        RecoveryGap(AuditSeverity.INFORMATIONAL, "VPS runtime provenance is partial", "Repository has a scheduled GitHub Actions workflow and prose for a separate Telegram process, but no tracked VPS systemd unit/timer or process supervisor state.", (".github/workflows/daily_scan.yml", "services/telegram_bot/README.md"), "DEPLOYMENT"),
        RecoveryGap(AuditSeverity.INFORMATIONAL, "Paper relative paths are CWD-sensitive", "Market DB is project-root anchored, while paper DB settings use raw relative Path values; alternate launch directories can select a different paper state file.", ("core/paths.py", "execution/signal_executor.py:PaperExecutionConfig.from_env", "scripts/run_paper_lifecycle.py"), "CONFIGURATION"),
    )
    return tuple(sorted(rows, key=lambda item: (item.severity.value, item.title)))


def _configuration() -> tuple[ConfigContract, ...]:
    rows = (
        ConfigContract("TELEGRAM_TOKEN", "REQUIRED", "Scanner/query Telegram client construction; secret value not recorded", ("services/telegram_client.py:TelegramConfig.from_env", "strategy/scanner.py:47")),
        ConfigContract("CHAT_ID", "REQUIRED", "Broadcast/query target and bot authorization; value not recorded", ("services/telegram_client.py:TelegramConfig.from_env", "services/telegram_bot/bot.py")),
        ConfigContract("MARKET_DATABASE_PATH", "OPTIONAL", "Canonical market DB override; blank/unset defaults to project-root data/market.db", ("core/paths.py:resolve_market_database_path",)),
        ConfigContract("PAPER_DATABASE_PATH", "DEFAULTED", "Common paper DB path; wrappers replace it with V2/V3 path; relative value is CWD-relative", ("execution/signal_executor.py:PaperExecutionConfig.from_env", "scripts/run_paper_lifecycle.py")),
        ConfigContract("PAPER_V2_DATABASE_PATH", "DEFAULTED", "V2 isolation path defaults to data/paper_trading_v2.db", ("scripts/run_paper_v2_lifecycle.py",)),
        ConfigContract("PAPER_V3_DATABASE_PATH", "DEFAULTED", "V3 isolation path defaults to data/paper_trading_v3.db", ("scripts/run_paper_v3_lifecycle.py",)),
        ConfigContract("PAPER_STRATEGY_VERSION", "DEFAULTED", "Unset defaults to Q70_FROZEN; exact V3 selector is V3_BREADTH_40_60", ("scripts/run_daily.py:_use_v3", "scripts/run_paper_v3_lifecycle.py")),
        ConfigContract("PAPER_TRADING_ENABLED", "DEFAULTED", "Executor default false, but V2/V3 lifecycle wrappers force true", ("execution/signal_executor.py:PaperExecutionConfig.from_env", "scripts/run_paper_v2_lifecycle.py", "scripts/run_paper_v3_lifecycle.py")),
        ConfigContract("TRADING_ENTRY_MODEL", "DEFAULTED", "hybrid by default; V3 wrapper explicitly sets hybrid", ("config/trading_policy.py:TradingPolicy.from_env", "scripts/run_paper_v3_lifecycle.py")),
        ConfigContract("TRADING_EXIT_MODEL", "DEFAULTED", "atr by default and only supported policy", ("config/trading_policy.py",)),
        ConfigContract("TRADING_EXECUTION_TIMING", "DEFAULTED", "next_open default and only supported production timing", ("config/trading_policy.py",)),
        ConfigContract("TRADING_STOP_ATR_MULTIPLIER / TRADING_TARGET_ATR_MULTIPLIER", "DEFAULTED", "Defaults 2.0 / 5.0; wrappers freeze V2/V3 values", ("config/trading_policy.py", "scripts/run_paper_v2_lifecycle.py", "scripts/run_paper_v3_lifecycle.py")),
        ConfigContract("PAPER_INITIAL_CASH / PAPER_COMMISSION_RATE / PAPER_SLIPPAGE_BPS / PAPER_SELL_TAX_RATE", "DEFAULTED", "Paper account and cost settings; values are not serialized", ("scripts/run_paper_lifecycle.py", "execution/signal_executor.py:PaperExecutionConfig.from_env")),
        ConfigContract("PAPER_POSITION_SIZER / PAPER_RISK_PER_TRADE_PCT / PAPER_MAX_POSITION_PCT / PAPER_MAX_EXPOSURE_PCT / PAPER_MAX_OPEN_POSITIONS / PAPER_MAX_DAILY_LOSS_PCT / PAPER_MIN_CASH_BUFFER_PCT / PAPER_MAX_ORDERS_PER_SCAN / PAPER_LOT_SIZE / PAPER_MAX_ORDER_ADTV20_PCT", "DEFAULTED", "Position size and paper risk/execution limits", ("execution/signal_executor.py:PaperExecutionConfig.from_env", "scripts/run_paper_lifecycle.py")),
        ConfigContract("AI_ANALYSIS_ENABLED / GEMINI_API_KEY", "OPTIONAL", "AI query interpretation; disabled by default, key needed only when enabled", ("services/telegram_bot/analyst.py:AIAnalystConfig.from_env",)),
        ConfigContract("GEMINI_MODEL / GEMINI_API_BASE_URL / AI_ANALYSIS_TIMEOUT_SECONDS / AI_ANALYSIS_MAX_OUTPUT_TOKENS", "DEFAULTED", "AI-only endpoint/model/time/token behavior", ("services/telegram_bot/analyst.py:AIAnalystConfig.from_env",)),
        ConfigContract("config/strategy.yaml", "REQUIRED", "Tracked regime and scanner thresholds loaded at import", ("config/strategy_loader.py",)),
        ConfigContract("research/forward_validation/protocol_v1.json", "REQUIRED_WHEN_FORWARD_ACTIVE", "Tracked protocol whose fingerprint and Phase 8 authorization are checked before daily evidence", ("quantlab/forward/daily.py", "quantlab/forward/protocol.py")),
        ConfigContract("PAPER_V2_QUALITY_THRESHOLD / PAPER_V2_ENABLED / PAPER_DISABLE_TRAILING / PAPER_ATR_TARGET_MULTIPLIER", "LEGACY_OR_UNCLEAR", "Several compatibility flags are set by wrappers but scanner threshold is also hardcoded in run_daily; consumers differ", ("scripts/run_paper_v2.py", "scripts/run_paper_v3_lifecycle.py", "scripts/run_daily.py")),
        ConfigContract("VNSTOCK_API_KEY", "LEGACY_OR_UNCLEAR", "Injected by workflow, not directly read by tracked Python call sites found; provider SDK behavior may consume external environment", (".github/workflows/daily_scan.yml", "scripts/update_data.py")),
    )
    return tuple(sorted(rows, key=lambda item: item.name))


def _import_safety() -> tuple[ImportSafety, ...]:
    return tuple(sorted((
        ImportSafety("quantlab.operations.production_audit", "SAFE_STATIC", "Pure records/identity only; no database, network, scheduler, or operational imports", ("quantlab/operations/production_audit.py imports",)),
        ImportSafety("core.paths", "SAFE_STATIC", "Resolves a Path only; no database open", ("core/paths.py",)),
        ImportSafety("core.database", "UNSAFE_MUTATING_IMPORT", "Creates parent directory, SQLAlchemy engine, prices table, and signals table on import", ("core/database.py:12-18", "core/database.py:467-468")),
        ImportSafety("strategy.scanner", "UNSAFE_CONFIG_AND_DATABASE_IMPORT", "Imports core.database; load_dotenv; creates TelegramClient and PaperSignalExecutor at module import; latter constructs PaperBroker/initializes configured paper DB", ("strategy/scanner.py:13-50", "execution/signal_executor.py:376-442", "execution/persistence.py:30-67")),
        ImportSafety("services.telegram_bot.app", "ENTRYPOINT_ONLY", "Starts long-running poll loop only under main guard; importing bot dependencies may instantiate no polling loop", ("services/telegram_bot/app.py:7-17",)),
        ImportSafety("scripts.run_daily", "ENTRYPOINT_LAZY_STAGES", "Loads dotenv/parser and DailyPipeline; provider/scanner/lifecycle imports are deferred to stage functions", ("scripts/run_daily.py:1-17", "scripts/run_daily.py:35-114")),
        ImportSafety("scripts.init_db", "UNSAFE_MUTATING_IMPORT", "Calls init_database immediately at module import", ("scripts/init_db.py:1-5",)),
    ), key=lambda item: item.module))


def _side_effect_free_report_state(observation: Mapping[str, int | bool | str]) -> str:
    if not observation.get("present", False):
        return "ledger absent at audit time; no activation/formation state observed"
    if not observation.get("schema_complete", False):
        return "ledger present but schema incomplete; state cannot be classified"
    return "ledger inspected read-only; counts only, no record/mature operation invoked"


def build_production_audit(
    repository_commit_identity: str,
    *,
    forward_ledger_observation: Mapping[str, Any],
    tracked_deployment_evidence: tuple[str, ...] = (),
) -> ProductionAuditResult:
    """Build immutable audit findings from source-audit conclusions and sanitized read-only observations."""
    commit = str(repository_commit_identity).strip()
    if len(commit) < 7 or any(character not in "0123456789abcdefABCDEF" for character in commit):
        raise ValueError("repository commit identity must be a hexadecimal Git object id")
    observation = _validate_ledger_observation(forward_ledger_observation)
    deployment_files = tuple(sorted(set(tracked_deployment_evidence)))
    deployment = "DEPLOYMENT_RUNTIME_PROVENANCE_PARTIAL" if not any("systemd" in item.lower() or "docker" in item.lower() for item in deployment_files) else "TRACKED_SERVICE_CONFIG_PRESENT_RUNTIME_UNVERIFIED"
    entries, stores, effects, failures, gaps, configuration, imports = (
        _entry_points(), _state_stores(), _side_effects(), _failures(), _gaps(), _configuration(), _import_safety()
    )
    stages = (
        "dotenv/config load",
        "market update (unless --skip-update)",
        "Forward V1 daily evidence (skipped when update skipped; blocks later stages on update warning)",
        "trading-session guard using get_reference_market_date == system date (unless update skipped)",
        "paper lifecycle: pending next-open execution then position exits (unless --skip-lifecycle)",
        "strategy scan -> telemetry/signals -> pending paper queue -> Telegram/report (unless --skip-scan)",
    )
    static_spec = ProductionAuditSpec()
    forward_conclusion = (
        "Formation replay is guarded by protocol+session uniqueness and immutable identity; formation write is transactional across its dependent rows. Matured-event/outcome/gap calls commit separately and rerun can continue using latest rows/unique keys. Missed formation sessions are append-only recorded and not backfilled. Activation requires exact protocol fingerprint and Phase 8 manifest/decision authorization. "
        + _side_effect_free_report_state(observation)
    )
    paper_conclusion = "CONDITIONALLY_RETRY_SAFE overall; NOT_RETRY_SAFE for interruption after fill commit and before pending status completion. Pending unique(signal_date,symbol) prevents duplicate queue rows, but fill has random order UUID and no pending-idempotency linkage. Lifecycle and fill/history updates span independent transactions; exact whole-run resume is UNKNOWN. PaperBroker is simulated; no live broker order endpoint is present in audited call path."
    market_conclusion = "CONDITIONALLY_RETRY_SAFE by symbol: provider batches validated before save; each symbol save uses INSERT OR REPLACE and commits separately; updater refreshes a seven-calendar-day overlap and retries unresolved symbols twice. Earlier symbol commits remain after later failures. Repeated results may differ if provider revises history. No complete integrity verifier is called in the daily path."
    payload = {
        "contract": CONTRACT, "version": VERSION, "repository_commit_identity": commit,
        "canonical_daily_entry_point": "main.py -> scripts.run_daily.main",
        "daily_stage_order": stages, "entry_points": tuple(asdict(row) for row in entries),
        "state_stores": tuple(asdict(row) for row in stores),
        "side_effects": tuple(asdict(row) for row in effects),
        "failures": tuple(asdict(row) for row in failures),
        "recovery_gaps": tuple(asdict(row) for row in gaps),
        "configuration_names_and_classes": tuple(asdict(row) for row in configuration),
        "import_safety": tuple(asdict(row) for row in imports),
        "forward_recovery_conclusion": forward_conclusion, "paper_recovery_conclusion": paper_conclusion,
        "market_update_recovery_conclusion": market_conclusion, "deployment_provenance": deployment,
        "tracked_deployment_evidence": deployment_files, "forward_ledger_observation": dict(observation),
        "no_mutation_no_network": True, "specification_fingerprint": static_spec.fingerprint,
    }
    return ProductionAuditResult(
        repository_commit_identity=commit,
        canonical_daily_entry_point="main.py -> scripts.run_daily.main",
        daily_stage_order=stages, entry_points=entries, state_stores=stores,
        side_effects=effects, failures=failures, recovery_gaps=gaps,
        configuration=configuration, import_safety=imports,
        forward_recovery_conclusion=forward_conclusion, paper_recovery_conclusion=paper_conclusion,
        market_update_recovery_conclusion=market_conclusion, deployment_provenance=deployment,
        tracked_deployment_evidence=deployment_files, forward_ledger_observation=observation,
        no_mutation_no_network=True, specification_fingerprint=static_spec.fingerprint,
        identity=_digest(payload),
    )


def write_production_audit_artifacts(result: ProductionAuditResult, output_directory: str) -> Mapping[str, str]:
    """Write compact UTF-8 CSV/JSON/Markdown audit artifacts to one new directory."""
    import csv
    import json
    from pathlib import Path

    target = Path(output_directory).resolve()
    if target.exists():
        raise FileExistsError(f"production audit output directory already exists: {target}")
    target.mkdir(parents=True, exist_ok=False)

    def write_rows(filename: str, rows: tuple[Mapping[str, Any], ...], columns: tuple[str, ...]) -> None:
        with (target / filename).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    entry_rows = tuple({
        "name": row.name, "path": row.path, "classification": row.classification,
        "command_or_trigger": row.command_or_trigger, "stage_or_purpose": row.stage_or_purpose,
        "persistent_state": " | ".join(row.persistent_state), "side_effects": " | ".join(row.side_effects),
        "retry_safety": row.retry_safety.value, "evidence": " | ".join(row.evidence), "notes": row.notes,
    } for row in result.entry_points)
    state_rows = tuple({
        "name": row.name, "default_location": row.default_location, "classification": row.classification,
        "owners": " | ".join(row.owners), "readers": " | ".join(row.readers),
        "mutation_semantics": row.mutation_semantics, "recovery_notes": row.recovery_notes,
        "evidence": " | ".join(row.evidence),
    } for row in result.state_stores)
    failure_rows = tuple({
        "scenario": row.scenario, "already_committed": row.already_committed,
        "remains_uncommitted": row.remains_uncommitted, "later_stages_run": row.later_stages_run,
        "state_consistency": row.state_consistency, "retry_safety": row.retry_safety.value,
        "evidence": " | ".join(row.evidence),
    } for row in result.failures)
    gap_rows = tuple({
        "severity": row.severity.value, "title": row.title, "consequence": row.consequence,
        "affected_surface": row.affected_surface, "evidence": " | ".join(row.evidence),
    } for row in result.recovery_gaps)
    write_rows("production_entrypoints.csv", entry_rows, tuple(entry_rows[0]))
    write_rows("production_state_inventory.csv", state_rows, tuple(state_rows[0]))
    write_rows("production_failure_matrix.csv", failure_rows, tuple(failure_rows[0]))
    write_rows("production_recovery_gaps.csv", gap_rows, tuple(gap_rows[0]))
    hashes = {path.name: sha256(path.read_bytes()).hexdigest() for path in target.glob("*.csv")}
    severity_counts = {severity.value: sum(item.severity is severity for item in result.recovery_gaps) for severity in AuditSeverity}
    report = [
        "# Production surface and failure-recovery audit", "",
        f"- Repository commit: `{result.repository_commit_identity}`",
        f"- Result identity: `{result.identity}`",
        f"- Canonical daily entry: `{result.canonical_daily_entry_point}`",
        f"- Deployment provenance: **{result.deployment_provenance}**", "",
        "## Daily stage order", "", *(f"{index}. {stage}" for index, stage in enumerate(result.daily_stage_order, 1)), "",
        "## Recovery conclusions", "", f"- Forward V1: {result.forward_recovery_conclusion}",
        f"- Paper: {result.paper_recovery_conclusion}", f"- Market update: {result.market_update_recovery_conclusion}", "",
        "## Recovery gaps", "", *(f"- **{item.severity.value} — {item.title}:** {item.consequence}" for item in result.recovery_gaps), "",
        "No performance monitoring, alerting, automated strategy control, live broker action, or production hardening is inferred.",
    ]
    report_path = target / "production_audit_report.md"
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8", newline="\n")
    hashes[report_path.name] = sha256(report_path.read_bytes()).hexdigest()
    manifest = {
        "contract": CONTRACT, "version": VERSION, "repository_commit_identity": result.repository_commit_identity,
        "result_identity": result.identity, "specification_fingerprint": result.specification_fingerprint,
        "canonical_daily_entry_point": result.canonical_daily_entry_point,
        "daily_stage_order": result.daily_stage_order,
        "persistent_state_stores": tuple(item.name for item in result.state_stores),
        "side_effect_taxonomy": tuple(sorted({item.effect for item in result.side_effects if item.possible})),
        "retry_safety_taxonomy": tuple(item.value for item in RetrySafety),
        "forward_recovery_conclusion": result.forward_recovery_conclusion,
        "paper_recovery_conclusion": result.paper_recovery_conclusion,
        "market_update_recovery_conclusion": result.market_update_recovery_conclusion,
        "deployment_provenance": result.deployment_provenance,
        "configuration_contract": tuple((item.name, item.classification) for item in result.configuration),
        "import_safety_state": tuple((item.module, item.classification) for item in result.import_safety),
        "forward_ledger_observation": dict(result.forward_ledger_observation),
        "recovery_gap_counts_by_severity": severity_counts,
        "no_mutation_no_network": result.no_mutation_no_network,
        "performance_monitoring": False, "alerting": False, "automated_control": False,
        "artifacts": dict(sorted(hashes.items())),
    }
    manifest_path = target / "production_audit_manifest.json"
    manifest_path.write_bytes(canonical_json(canonical_identity_value(manifest)) + b"\n")
    hashes[manifest_path.name] = sha256(manifest_path.read_bytes()).hexdigest()
    return MappingProxyType(dict(sorted(hashes.items())))


__all__ = (
    "AuditSeverity", "ConfigContract", "FailureScenario", "ImportSafety",
    "OperationalEntryPoint", "PersistentStateStore", "ProductionAuditResult",
    "ProductionAuditSpec", "RecoveryGap", "RetrySafety", "SideEffectBoundary",
    "build_production_audit", "write_production_audit_artifacts",
)
