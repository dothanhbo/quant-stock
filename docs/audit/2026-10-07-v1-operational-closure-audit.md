# V1.1 Operational Closure Audit — quant-stock

Date: 2026-10-07. Mode: read-only audit of the live device repository plus offline sandbox probes on copies. No live DB, `.env`, provider, Telegram, Daily, commit or push. Contract: `docs/roadmap/QUANT_PROJECT_CANONICAL_ROADMAP_V1_V3.md`.

## RESULT

**BLOCKED** — one concrete P1 operational defect requires an architecture/ownership decision (not a small local fix), plus two P1 reporting/UI gaps for the V1 freeze. No code was changed.

> **Addendum 2026-10-07 (c) — acquisition coverage (uncommitted):** `scripts/backfill_market_data.py` gains explicit-range, single-symbol acquisition (`--symbols SYM --start <first stored session> [--end] [--dry-run]`, source mode `BACKFILL_EXPLICIT_RANGE`) through the unchanged admission guard, with an explicit `PROVIDER_HISTORY_COVERAGE_INSUFFICIENT` check that writes nothing. Runbook re-ordered below. KBS reach to 2018-08-07 is still to be confirmed by the operator's first real run.
>
> **Addendum 2026-10-07 (b) — adversarial review round 1 fixed (uncommitted):** APPLIED receipts are revalidated against the current basis; `--paper-db` is supplemental to the mandatory canonical stores; the 250-session partial-seam allowance is removed (full stored-history coverage required); older blocks auto-close only when their complete proposal is compatible; a pending rebase owns its block against ordinary review. Runbook updated below.
>
> **Addendum 2026-10-07 — P1-OPS-1 implemented (uncommitted).** Owner decision: REVIEWED PER-SYMBOL REBASE (option a). Implemented in `core/market_rebase.py` plus bounded changes to `core/market_observation_log.py`, `core/market_admission.py`, `core/evidence_market_binding.py` and `scripts/resolve_market_block.py`, with offline tests in `tests/test_market_rebase.py` (41 cases after review round 1) (`tests/test_market_rebase.py`). Ingestion stays fail-closed; recovery is an explicit operator command. See **CORPORATE ACTION / REBASE RUNBOOK** below. P1-REP-1 and P1-UI-1 remain open for the UI batch; V1 freeze stays NOT_READY until they are done and the acceptance test passes.

## CURRENT RELEASE TARGET

V1 — Trustworthy Quant MVP, milestone V1.1 Operational Closure. R1, R2, R3 closed; R3 = `f084260 bind evidence to market data provenance`. V2 not opened; R4 deferred.

## GIT / WORKTREE STATE

Method: no shell on the user's PC in this session. The device `.git/index`, refs and every non-research tracked file (523 files) were copied read-only and compared blob-by-blob (`git hash-object`) against the index. `git status` itself was not run.

- Branch `main`, HEAD `f0842609c89b…` (`f084260`), equal to `origin/main` (already pushed).
- Content-modified tracked files (5, all the uncommitted "Research Runs" Manager WIP): `manager/app.py`, `manager/theme.py`, `quantctl/cli.py`, `quantctl/registry.py`, `tests/test_quant_manager.py`.
- 55 further tracked files differ only by CRLF line endings (backtesting/*, strategy/*, several tests). Whether `git status` lists them depends on the user's global `core.autocrlf`; content is identical.
- Untracked, not ignored (examples, not exhaustive for `research/`): `docs/roadmap/QUANT_PROJECT_CANONICAL_ROADMAP_V1_V3.md` (the canonical roadmap itself), `manager/README.md`, `manager/research_ui.py`, `manager/pages/research_runs.py`, `manager/pages/run_results.py`, `quantctl/research_runs.py`, `quantlab/manager_factor_runner.py`, `tests/test_manager_research_runs.py`, `tests/test_research_ui_design.py`, `tests/ui/*`, `quant-ui-design/`, `QUANT_LAB_MVP_DESIGN/`, `Claude outputs/`, `research/MANAGER_*.md`, `research/D4_V1_DATA_PROVENANCE_DECISION.md`, `research/D4_PERSONAL_EOD_MINIMUM_DECISION_MEMO.md`, `research/D4_KBS_PILOT_SOURCE_QUALIFICATION.md`, `research/run_neutral_adx_h10_exploration.py`.
- Ignored data/WIP: `data/*`, `research_results/`, `logs/`, CafeF zips.
- Canonical committed runtime (app/, core/, execution/, quantlab/, strategy/, scripts/, quantctl except cli/registry, config/) matches HEAD content. All WIP is Manager/Quant Lab research UI, which is separate from the operational chain. Note: WIP `manager/theme.py` reads `quant-ui-design/design-tokens.json` at import time, so committing the theme without that untracked folder would break Manager import.
- The 222 tracked `research/` files were not hash-verified; the only one checked (`research/attestations/strategy_identity_v3_research.jsonl`) is clean.

## ACTIVE RUNTIME STORES

| Store | Canonical resolution (code) | Live value |
|---|---|---|
| Market DB | `core.paths.resolve_market_database_path`: explicit → `MARKET_DATABASE_PATH` → `data/market.db`, project-root relative | `.env` `MARKET_DATABASE_PATH=data/market.db` → `C:\Users\hello\Desktop\quant-stock\data\market.db` (17,448,960 B, SHA-256 `75704e29…`, 101 symbols, 189,573 rows, 2018-08-07 → 2026-10-06) |
| Observation log | `resolve_observation_log_path`: explicit → `MARKET_OBSERVATION_LOG_PATH` → `<stem>_observations.db` beside market DB | `data\market_observations.db` — **does not exist** (gitignored data file) |
| Active paper DB | `config.paper_store.resolve_active_paper_store`: `PAPER_STRATEGY_VERSION` (default Q70) → `PAPER_V2_DATABASE_PATH` → `data/paper_trading_v2.db` | Q70_FROZEN → `data\paper_trading_v2.db`: 0 orders, 0 positions, 0 closed trades, 17 snapshots, 7 pending rows (1 `PENDING`: NAB signal 2026-10-06) |
| Forward DB | `quantlab.forward` default `data/forward_validation.db` | protocol `QV-FWD-V1-8c60839fa29fc04e` active; 3 formations, 6 pending maturities, 0 outcomes; missing formation sessions 09-28, 09-29, 10-02, 10-05 |
| Prospective evidence | `data/prospective_portfolio_evidence.db` | 3 legacy observations (09-30, 10-01, 10-06), no R3 binding tables yet |
| Operation history | `quantctl.run_history` → `data/operation_history.db` | 7 runs |
| Telegram | `services.telegram_client.TelegramClient.from_env` (`TELEGRAM_TOKEN`, `CHAT_ID`) | configured in `.env` (values not read) |
| Strategy | `quantlab.strategy_contract` canonical contract `canonical-2026-10-06.v1`; runtime pinned by `configure_v2_environment` | Q70_FROZEN, REGIME_CAPS_V1 overlay |

Inactive/legacy stores (documented, not deleted): `data/paper_trading.db` (generic; `.env PAPER_DATABASE_PATH` points here but every operational path overrides it), `data/paper_trading_v3.db` (V3 store, selected only by `PAPER_STRATEGY_VERSION=V3_BREADTH_40_60`), `data/paper_archive/`, legacy read-only `dashboard/app.py`.

## OBSERVATION LOG ACTIVATION

Answers:

1. **Does the live log exist?** No. Without it the provenance gate returns `NOT_APPLICABLE` (legacy behaviour), and every new R3 binding records `NO_LOG` → `QUARANTINED_MISSING_PROVENANCE`.
2. **Initialization required:** one baseline registration that binds the log to the exact market DB locator and content. It happens **implicitly on the next `update_data` / Daily run** (`admit_price_batch` calls `ensure_initial_baseline` before the first admission) or explicitly via `scripts/register_market_baseline.py`.
3. **Live DB vs previously validated baseline:** no baseline was ever registered for the live DB. The current file hash `75704e29…` equals the copy used in the R3 isolated validation. Registering the current file yields content id `baseline-legacy-be567ba24776415e` (content SHA `be567ba2…`, 189,573 rows, 101 symbols). This was reproduced on a copy; the content id is path-independent. Any further update before registration changes it.
4. **Safe without rewriting history:** yes. Proven on a copy: `market.db` is opened read-only, its SHA-256 is unchanged after registration, and only the separate log file is written. Registration took about 1 s; `verify_dataset` returned no problems and the gate went NOT_APPLICABLE → PASS.
5. **Historical rows receive:** `BASELINE_LEGACY` per session, labelled `LEGACY_UNVERIFIED` / `NOT_POINT_IN_TIME` / `SURVIVORSHIP_CURRENT_CONSTITUENTS` / `PRICE_ADJUSTMENT_UNKNOWN`. Evidence consuming them is `BOUND_LEGACY_INPUT`, never verified.
6. **Future admitted sessions receive:** `OBSERVATION` provenance with a dataset version. Evidence consuming only those sessions can be `PROVENANCE_VERIFIED`; any window reaching legacy history stays `BOUND_LEGACY_INPUT`.
7. **If repeated:** idempotent, same baseline returned. A different/copied/moved DB path is rejected (`ObservationLogError … bound to a different market dataset`, reproduced). Moving or renaming the repository fails closed until a new log is registered. Deleting the log silently reverts to `NOT_APPLICABLE`, so the log must be backed up.

Activation procedure (prepared, **not run**):

```powershell
cd C:\Users\hello\Desktop\quant-stock
# 0. Nothing else running (no Daily, scanner, Manager operation).
# 1. Back up the four state DBs:
mkdir backups\2026-10-07-pre-log -Force
copy data\market.db, data\paper_trading_v2.db, data\forward_validation.db, data\prospective_portfolio_evidence.db backups\2026-10-07-pre-log\
Get-FileHash data\market.db   # expect 75704E29FE5A3BCF… if no update has run since
# 2. Register (writes only data\market_observations.db; market.db read-only):
.\.venv\Scripts\python.exe scripts\register_market_baseline.py
#    expect "baseline_id": "baseline-legacy-be567ba24776415e" if the hash matched in step 1
# 3. Verify read-only:
.\.venv\Scripts\python.exe scripts\register_market_baseline.py --verify     # {"verified": true, "problems": {}}
.\.venv\Scripts\python.exe scripts\resolve_market_block.py list             # []
# 4. Back up data\market_observations.db with the others from now on.
```

Activation is understood and mechanically safe. **But see P1-OPS-1:** once the log exists, the first provider back-adjustment of any VN100 symbol halts Daily; since 2026-10-07 the supported recovery is the reviewed per-symbol rebase (runbook below), except when a paper position or pending signal is open on that symbol. Activation happens automatically at the next Daily anyway, so the decision cannot be deferred by not running the script.

## CANONICAL DAILY PATH

`quantctl daily` / Manager "Run Daily" → subprocess `python -m scripts.run_daily` → `run_tracked_entrypoint("daily")`:

| # | Step | Authoritative function | DB / intent | Fail-closed boundary | Retry |
|---|---|---|---|---|---|
| 0 | Contract preflight | `app.strategy_scan.preflight_strategy_contract` | none | raises before any mutation on contract drift | pure |
| 1 | Market update + admission | `scripts.update_data.update_all_symbols` → `core.database.save_price_data` → `core.market_admission.admit_price_batch` | market.db append-only INSERT, log append | revision → `BLOCKED_*`, market untouched; any failed/blocked symbol → stage warning → Daily stops | identical retry is a no-op receipt |
| 2 | Version finalization | inside admission (`record_application`) | log | pending intent gates consumers | reconcile on next open |
| 3 | Integrity gate | `core.market_data_integrity.check_market_data_integrity` (+ `market_provenance_gate`) | read-only | FAIL_CLOSED → stop; NOT_APPLICABLE (session ≠ today) → stop with warning | read-only |
| 4 | Forward | `quantlab.forward.run_forward_validation_daily` | forward DB + R3 bindings (same txn) | `require_market_provenance`; blocked target → no outcome | formation/outcome idempotent |
| 5 | Paper lifecycle | `scripts.run_paper_v2_lifecycle` → `scripts.run_paper_lifecycle.main` | paper DB, prospective evidence | `require_market_data_integrity` (strict PASS) + contract enforcement before writes | pending execution and exits idempotent; evidence existing on retry |
| 6 | Scan / signals / pending | `app.strategy_scan.run_strategy_scan` → `strategy.scanner.run_scan` | market.db `signals`/telemetry, paper pending queue | Telegram config + integrity required before side effects | signal save de-dupes |
| 7 | Notification | `TelegramClient.send_message` after all writes | none | errors caught, printed, never raised into core state | — |
| 8 | Reporting | `quantctl.forward_evidence`, `analysis.paper_performance` (read-only) | read-only | R3 qualification at read time | — |

Daily records stage results in `operation_history.db`; exit code 0 means every stage succeeded, otherwise 1 (130 on interrupt).

## ENTRY-POINT CONSISTENCY

| Entry point | Reaches | Verdict |
|---|---|---|
| `quantctl daily`, Manager Run Daily | `scripts.run_daily` (subprocess) | canonical |
| `quantctl update`, Manager Update | `scripts.update_data` → admission | canonical |
| `quantctl scan`, Manager Run Scanner, `python -m strategy.scanner` | `app.strategy_scan.main` → `run_strategy_scan` | canonical |
| `scripts/run_paper_v2.py`, `run_paper_v3.py` | `run_strategy_scan(strategy_identity=…)` | canonical wrappers |
| `scripts/run_paper_v2_lifecycle.py` / `_v3_` | pin env → `run_paper_lifecycle.main` | canonical |
| `scripts/run_paper_lifecycle.py` run directly | same function, env not pinned | fails closed on contract (`trailing_enabled`), reproduced; not a semantic fork |
| `scripts/backfill_market_data.py` | `save_price_data` → admission | guarded |
| `scripts/update_paper_positions.py` | provenance gate only (no same-day session check) | P2 |
| `scripts/update_signal_results.py` | provenance gate | guarded |
| `scripts/migrate_open_positions_policy.py --apply` | refuses non-active store | guarded |
| `scripts/resolve_market_block.py`, `register_market_baseline.py` | log only | recovery tools |
| `research/run_quantlab_forward_validation.py record/mature` | writes **live** `data/forward_validation.db` from hand-supplied JSON, no R3 binding, no integrity gate | P2 (outputs are LEGACY_UNBOUND, but can pre-empt a canonical formation for a session) |
| `services/telegram_bot`, `dashboard/app.py`, Manager read pages | read-only | — |

No two active production paths implement different strategy or state semantics.

## STRATEGY CONTRACT CONSISTENCY

One processor selection (`build_scan_processor`: `PaperV2Scanner(0.70)` / `PaperV3Scanner(0.70)`), one runtime pinning (`configure_v2_environment` / `configure_v3_environment`), and one enforcement (`quantlab.strategy_contract.enforce_strategy_contract`). These are applied at Daily preflight, scan and lifecycle, and the retained-executor check refuses cross-strategy reuse. In the R3 sandbox the live-copy pending signal executed with `CONTRACT_MATCHED (canonical-2026-10-06.v1)`. Focused suites passed (`test_canonical_scan_path`, `test_strategy_contract_*`, `test_strategy_identity_v3`, `test_v3_configuration_integrity`). V3 is inactive and selectable only explicitly.

## PAPER OPERATIONS

Active Q70 store; isolation by resolver; the generic store cannot be reached by operational paths. Pending signals execute only at the first VNINDEX session after the signal date, otherwise `MISSED_EXECUTION` (fail-closed; 5 of 7 live rows expired this way). Entry binding includes ADTV20, symbol history, signal/execution and `REGIME_RS_HISTORY`. Exit binding includes the exit price and history. Retry is idempotent (sandbox re-run and suites). Evidence capture runs after the lifecycle; lineage survives paper reset (R3 tests). No live positions were touched.

## FORWARD OPERATIONS

Formation only for the latest completed benchmark session after activation; maturity from the session calendar; outcome only with an exact target session; R3 binding in the same transaction. A blocked target writes no outcome and a quarantined outcome is excluded from qualified means (sandbox + `test_evidence_market_binding`). The 3 pre-R3 formations remain unbound (`LEGACY_UNBOUND`). Missing Daily runs create permanent `CONTINUITY_GAP` sessions with no backfill (by design).

## PROVENANCE / QUALIFICATION

R3 states are computed at read time against the current log. Until the log exists, all new evidence is `QUARANTINED_MISSING_PROVENANCE`, legacy is `LEGACY_UNBOUND`, and nothing is qualified. After activation, evidence touching legacy history is `BOUND_LEGACY_INPUT`. Verification needs fully observed windows, and the full VNINDEX history window makes paper entries `BOUND_LEGACY_INPUT` for the foreseeable future (expected V1 limitation until R4).

## REPORTING SURFACES

- `quantctl.forward_evidence` catalog: per-point qualification and qualified/legacy/quarantined outcome counts and qualified mean — correct.
- `scripts/report_paper_performance.py`: prints a provenance scope; `--label-provenance`, `--qualified-only` — correct.
- **Manager Forward Evidence page: shows descriptive means and paper points without any R3 qualification** (P1-REP-1).
- **Manager Dashboard: no integrity/provenance block, observation-log, dataset-version, pending/position or qualification state** (P1-UI-1).
- QuantCtl `status`/`doctor`/`paper status`/`forward status`: no provenance/log/block state (P2).
- Daily summary: stage-level only. Telegram scan and paper messages make operational statements, not research claims — acceptable.

## FAILURE / RECOVERY MATRIX

| Failure | Symptom / state left | Safe diagnostic | Safe recovery | Must NOT |
|---|---|---|---|---|
| Update fails before admission (provider/network) | Daily stage warning "N mã vẫn lỗi", stops before integrity; market.db unchanged for those symbols | `quantctl history`, `logs/` failed list | re-run Daily same day (identical admissions are no-ops) | run `--skip-update` to force downstream |
| Revision detected | `🛑 REVISION BLOCKED`, unresolved block, symbol stale; integrity FAIL_CLOSED; forward/lifecycle/scan refuse | `scripts\resolve_market_block.py list` | corporate action: follow the **REBASE RUNBOOK** (`resolve … --action rebase`). Any other revision: review; `resolve --block-id N --reviewer R --reason T` records the review only and does not restore operation | edit market.db, delete the log, `INSERT OR REPLACE`, re-register a baseline to escape the block |
| Crash after market write, before finalization | open application intent → `PENDING_APPLICATION`, gate FAIL_CLOSED | `register_market_baseline.py --verify`; `resolve_market_block.py list` | re-run update/Daily: next admission reconciles the intent (applied / abandoned / non-auto-resolvable mismatch block) | hand-edit either DB |
| Crash during a reviewed rebase | before the market commit: nothing applied, open REBASE intent; after it: new rows, open intent, block still unresolved and owned by the intent (review refused: `BLOCK_OWNED_BY_PENDING_REBASE`); gate FAIL_CLOSED either way | `resolve_market_block.py list` / `rebases` | re-run the identical rebase command (`RECOVERED` or applies once), `recover-rebase --symbol SYM`, or the next Daily admission of that symbol settles it; `AMBIGUOUS_REBASE_STATE` → restore both DBs from the pre-rebase backup | hand-edit either DB; try to review the owned block |
| Integrity gate fails | Daily stops after "Market Data Integrity"; lifecycle/scan/forward not run; standalone lifecycle/scan raise | message names symbols/session | fix cause, re-run same day | run lifecycle/scan on another date |
| Integrity NOT_APPLICABLE | weekend/holiday or run after midnight; Daily exit 0 with warning, nothing downstream | summary text | run Daily same trading day after 15:00 ICT | change system date |
| Scanner fails | lifecycle/forward already committed; partial scan side effects possible only after the processor | traceback in history | re-run `quantctl scan` same day (signal de-dup, queue idempotent) | — |
| Paper execution fails midway | per-row transactions; idempotent intents | `quantctl paper status` | re-run Daily/lifecycle same day | reset the paper store |
| Forward batch fails midway | formation+binding atomic per row | `quantctl forward status` | re-run (idempotent) | use the research forward CLI on the live ledger |
| Telegram fails | message not sent; core state already committed | stdout "⚠️ … Telegram" | none required; missing config makes scan fail before side effects | — |
| Observation log missing | gate NOT_APPLICABLE; new R3 evidence MISSING_PROVENANCE | `Test-Path data\market_observations.db` | restore from backup; if none, re-register (history becomes a new legacy baseline; evidence bound to the old log becomes INCOMPATIBLE) | recreate silently without recording why |
| DB path/config error | integrity FAIL_CLOSED "market database does not exist"; `AmbiguousPaperStoreError`; log bound to another dataset | `quantctl doctor`, `quantctl status` | run from repo root; fix `.env` | point two strategies at one paper DB |

## DAILY RUNBOOK

Every trading day after 15:00 ICT, same calendar day, from repo root:

1. `.\.venv\Scripts\python.exe -m quantctl doctor` (read-only; WARN acceptable, FAIL stop).
2. `.\.venv\Scripts\python.exe -m quantctl daily` (or `scripts\run_daily.py` for live output).
3. **PASS:** exit 0; summary has ✅ for Update, Market Data Integrity (no warning), Forward Validation, Paper Lifecycle, Strategy Scanner; "Prospective evidence: created for <today>"; Telegram sent.
4. **FAIL:** exit 1, ❌ stage, `REVISION BLOCKED`, `FAIL_CLOSED`, or an integrity warning (nothing downstream ran) → follow the matrix; on a block run `scripts\resolve_market_block.py list` first.
5. Inspect `quantctl history --limit 5` and Manager Paper State.
6. Back up `data\*.db` including `market_observations.db` after a successful run (at least weekly).

## WEEKLY RUNBOOK

`register_market_baseline.py --verify`; `resolve_market_block.py list`; dataset version:

```powershell
python -c "from core.market_observation_log import open_observation_log as o; from core.paths import resolve_market_database_path as r; import json; print(json.dumps(o(r(),readonly=True,require_baseline=True).dataset_version_identity(),default=str,indent=1))"
```

Then: `quantctl status`, `quantctl paper status`, `quantctl forward status` (continuity gaps), `quantctl history --limit 10` (failed/stale runs), `scripts\report_paper_performance.py --label-provenance` (qualification counts), the strategy contract line in the last Daily output, and hash-checked copies of the five DBs plus the log in a dated backup folder.

## CORPORATE ACTION / REBASE RUNBOOK

Applies to one symbol whose provider history was back-adjusted after a corporate action (stock dividend, bonus shares, rights issue, split). **The system does not prove the corporate action is legitimate.** The operator asserts it (category, reviewer, reason, note) after reviewing external evidence; the rebase only guarantees that exactly the reviewed, already-persisted provider rows are applied, to exactly that symbol, once, with a full audit trail.

**1. Find the blocked symbol.** Daily stops with `🛑 <SYM>: REVISION BLOCKED (BLOCKED_REVISION_CHANGED …)`; integrity is FAIL_CLOSED and nothing downstream ran.

```powershell
.\.venv\Scripts\python.exe scripts\resolve_market_block.py list
```

A back-adjustment looks like this: every affected session lies **before** one date (the ex-date), sessions from the ex-date on are unchanged, and the ratio old/new is roughly constant across OHLC. If a block is anything else (`BLOCKED_OVERLAP_INCOMPLETE`, `BLOCKED_HISTORY_EXTENSION`, scattered or one-off changes), it is not a rebase case: review it and keep the stored history. A routine 7-day update block is never rebaseable itself (see step 5).

**2. Determine the first stored session (read-only, no provider call).**

```powershell
.\.venv\Scripts\python.exe -m scripts.backfill_market_data --symbols <SYM> --start 2000-01-01 --dry-run
```

It prints `stored_history.first_session` / `last_session` and refuses (exit 2) with `EXPLICIT_START_BEFORE_FIRST_STORED_SESSION`, naming the exact start to use. Re-run the dry run with `--start <first_session>`: `"status": "PLANNED"`, `"provider_called": false`.

**3. Back up** (nothing else running — no Daily, scanner or Manager operation): copy `data\market.db`, `data\market_observations.db` and the paper DBs to a dated folder and record their hashes.

**4. Acquire the full historical observation (a provider call; observation only).**

```powershell
.\.venv\Scripts\python.exe -m scripts.backfill_market_data --symbols <SYM> --start <first_session> --end <today>
```

Rules: exactly one symbol; `--start` must equal the symbol's first stored session (earlier or later is refused before any fetch); `--end` may not be after today nor before the last stored session; strict `YYYY-MM-DD`; `--start` without `--symbols`, with an empty or multi-symbol list, and `--end`/`--dry-run` without `--start` are refused (exit 2, no provider call). The whole-universe default never takes a date window. Source mode `BACKFILL_EXPLICIT_RANGE`, so the observation identity never collides with the rolling `UPDATE` (7-day) or ordinary `BACKFILL` (8-year) requests; the exact requested window, the actual first/last returned sessions, source, package version, normalization version and content digest are persisted by the R1/R2 log as for every observation. The batch goes through `save_price_data` → admission, exactly like Daily: **nothing is overwritten**. Expected for a back-adjusted symbol: exit 0, `"status": "OBSERVATION_RECORDED_BLOCKED"`, `"coverage": "FULL_STORED_HISTORY_COVERED"`, `admission.result = BLOCKED_REVISION_CHANGED`, `admission.observation_id`; `market.db` unchanged. An identical history is admitted normally (`OBSERVATION_RECORDED_ADMITTED`, new sessions appended by the ordinary rules) and a retry of the same request returns the same observation (one block, one more fetch receipt).

**5. Confirm coverage.** If the provider's earliest returned session is later than the first stored session, the command prints `"status": "PROVIDER_HISTORY_COVERAGE_INSUFFICIENT"` with `requested_window`, `stored_history`, `returned.earliest_session` and `missing_range`, exits 2 and admits **nothing** (no observation, no block, no market or log change). **Then V1 has no rebase path for that symbol:** do not trim history, do not partial-rebase, do not move the start; the symbol stays blocked (the gate stays closed) and is escalated to future data remediation (R4). `FULL_STORED_HISTORY_COVERED` only proves the provider reached the first stored session; the rebase dry run below is the authoritative eligibility check (full window, pure value revision, contiguous changed prefix).

**6. External evidence review (outside the system).** Confirm on the issuer/HOSE/HNX disclosure that a corporate action with that ex-date exists and that its ratio matches the observed factor. Write the reference into `--note`. If you cannot confirm it, stop: do not rebase.

*Paper exposure.* `.\.venv\Scripts\python.exe -m quantctl paper status`. If any paper store holds an **open position or a PENDING signal on the symbol, the rebase is refused** (`OPEN_POSITION_REQUIRES_CORPORATE_ACTION_RECONCILIATION` / `PENDING_SIGNAL_…`). V1 has no corporate-action portfolio engine: quantity, cost basis, stop and PnL would be wrong on the new basis. This case needs an owner decision; never edit the paper store to get past the check.

*Store coverage (always):* the rebase checks the **mandatory canonical stores** — generic, Q70 and V3, resolved exactly as the pipelines resolve them (process environment over `.env`) — **plus** any `--paper-db` you name. `--paper-db` is supplemental only; it can never replace or hide a canonical store. Missing paths: the **active** store or any store whose path variable is **configured** must exist and be readable (`PAPER_STORE_MISSING` / `PAPER_STORE_UNREADABLE` otherwise); an inactive, unconfigured default that does not exist cannot hold exposure and is recorded as `ABSENT_INACTIVE_UNCONFIGURED`; a `--paper-db` path that does not exist (typo) refuses, and a file that is not a paper store (no `paper_positions` table) refuses with `PAPER_STORE_NOT_A_PAPER_STORE`.

**7. Rebase dry run (changes nothing).** `list` again and take the **newest** block on `<SYM>` (the step-4 observation; older blocks are refused with `BLOCK_NOT_LATEST`).

```powershell
.\.venv\Scripts\python.exe scripts\resolve_market_block.py resolve --action rebase `
  --block-id <N> --observation-id <OBS> --confirm-symbol <SYM> `
  --category CORPORATE_ACTION_REBASE --reviewer <NAME> `
  --reason "<corporate action, ratio, ex-date>" --note "<disclosure reference>" --dry-run
```

Expected: `"status": "ELIGIBLE_DRY_RUN"`, `"market_db_modified": false`, `history_coverage: FULL_STORED_HISTORY` with `stored_history`, `rebased_session_count`, `first_rebased_session` / `last_rebased_session` (the last should be the session before the ex-date), `sample` stored vs rebased rows (check the factor), `supersedes_block_ids` (older blocks whose **complete** persisted proposal is compatible with the resulting basis; future, not-yet-stored sessions are ignored and never treated as applied; a conflicting proposal keeps its block for its own review) and `open_position_check.checked_stores`. Refusals print `"status": "REFUSED"` and a `code` (exit 2), e.g. `INCOMPLETE_REBASE_HISTORY_COVERAGE` (the observation does not cover every stored session: V1 accepts no partial basis seam and has no lookback threshold), `REBASE_REQUIRES_PURE_VALUE_REVISION`, `REBASE_NONCONTIGUOUS_BASIS_CHANGE`.

**8. Apply the rebase.** The same command without `--dry-run`. Expected: exit 0, `"status": "REBASED"` and a `rebase` record: `rebase_id`, `symbol`, `block_event_id`, `observation_id`, `pre_version_id` → `new_version_id`, `pre_dataset_version_id` → `new_dataset_version_id`, `first_session`..`last_session`, `session_count`, old/new content digests, `row_count`, `category`, `reviewer`, `reason`, `note`, `created_at_utc`, `detail.resulting_block_state = REBASED_BY_REVIEW`, `detail.old_rows` (kept for audit). New sessions contained in the observation are **not** appended by the rebase; the next normal update appends them.

*Applied receipts after a rebase:* an observation's APPLIED receipt is reused (idempotent `already_applied`) only while **every row of that observation is stored now with exactly the observed values**. A rebase can break that — it changes values an older applied observation confirmed, and it marks its own reviewed observation applied without inserting that observation's future sessions. When the receipt no longer holds, the same content is re-admitted under a basis-bound identity (`readmission_of` in its OBSERVED event) and goes through the normal overlap comparison: a conflicting old observation **blocks**, the reviewed observation **appends** its missing future sessions, a genuinely new revision **blocks**. Historical receipts are never deleted or reopened.

**9. Verify gate and version.**

```powershell
.\.venv\Scripts\python.exe scripts\resolve_market_block.py list          # the block is gone
.\.venv\Scripts\python.exe scripts\resolve_market_block.py rebases --symbol <SYM>
.\.venv\Scripts\python.exe scripts\register_market_baseline.py --verify
```

The dataset identity (weekly-runbook command) shows a new `dataset_version_id` and `consumable: true` if no other block or intent is open. Evidence effect: evidence that consumed a replaced session of `<SYM>` now qualifies as `QUARANTINED_INCOMPATIBLE_BASIS` (kept, never rewritten, never rebound); new evidence on the rebased sessions binds the new version as `BOUND_LEGACY_INPUT` (operator-asserted history is never `PROVENANCE_VERIFIED`). Other symbols are unaffected.

**10. Resume normal update.** Run Daily as usual. The rolling 7-day update for `<SYM>` is now an identical overlap on the rebased basis: it is admitted (appending the missing sessions) and does **not** re-block; integrity must PASS.

**Safe retry and ownership.** Re-running the identical command is always safe: `ALREADY_REBASED` (nothing changes) or `RECOVERED` (a crash after the market commit is finalized). A crash before the market commit leaves nothing applied; the retry abandons the stale intent and applies once. The next Daily admission of the symbol also finalizes or abandons an interrupted rebase automatically. Until then every gate stays FAIL_CLOSED and the block stays unresolved.

*Pending-rebase ownership:* the rebase intent claims its block atomically (in the same log transaction that re-checks the block is unresolved). While that intent is open, `resolve --action review` of the block is refused with `BLOCK_OWNED_BY_PENDING_REBASE` (exit 2), and automatic supersession skips it. Only the rebase's own finalization (re-run the identical command) or its explicit settlement may change it:

```powershell
.\.venv\Scripts\python.exe scripts\resolve_market_block.py recover-rebase --symbol <SYM>
```

`recover-rebase` finalizes the intent if the reviewed rows are in the market, abandons it if the old basis is fully intact (after which an ordinary review is allowed again), and otherwise reports `AMBIGUOUS_REBASE_STATE` and changes nothing. Unrelated blocks stay reviewable at all times.

**Rollback / recovery.** There is no "un-rebase" command, by design. If the rebase was wrong or `AMBIGUOUS_REBASE_STATE` is reported (the market holds neither the old nor the reviewed rows), stop everything and restore **both** `market.db` and `market_observations.db` (and the paper DBs) **together** from the step-3 backup, only if nothing ran after it. Restoring one without the other leaves the log and the market inconsistent (gates fail closed). Evidence written after the rebase and then rolled back would reference a version that no longer exists and would quarantine.

**Never do:**
- rebase without external evidence, or to "unstick" Daily;
- rebase a block that is not a clean corporate-action pattern;
- edit `market.db` or the log by hand, delete or re-register the log, or use `INSERT OR REPLACE` to escape a block;
- close, resize or re-price a paper position by hand to get past the open-position check;
- resolve a corporate-action block with `--action review` and expect operation to resume (the next fetch re-blocks);
- try to review a block that a pending rebase owns, or work around `BLOCK_OWNED_BY_PENDING_REBASE`;
- pass `--paper-db` expecting it to replace the canonical stores (it only adds);
- rebase from a short-window block or accept any old/new basis seam;
- trim stored history or start explicit acquisition at any date other than the first stored session;
- run explicit-range acquisition for several symbols or the whole universe;
- run the rebase while Daily, the scanner or a Manager operation is running;
- restore only one of the two databases.

## P0/P1 BLOCKERS

- **P1-OPS-1 Corporate-action revision deadlock — IMPLEMENTED 2026-10-07 (reviewed per-symbol rebase, uncommitted; see runbook).** Original finding: KBS back-adjusts history after corporate actions (R1/R2 doc known limit; D4 evidence). Reproduced on a live-DB copy: one back-adjusted symbol → `BLOCKED_REVISION_CHANGED`. The documented reviewed resolution then leaves the symbol stale (`FAIL_CLOSED … FPT=2026-10-06`). The next day's fetch re-blocks. Daily, lifecycle, scan and forward stay halted indefinitely, and no V1 recovery path exists short of R4. With about 100 symbols this will happen soon after activation, and activation is automatic at the next Daily. Options needing an owner decision: (a) a reviewed per-symbol rebase that accepts revised provider history as a new version, with evidence on the old basis becoming INCOMPATIBLE; (b) degraded-universe operation excluding blocked symbols, with explicit handling of open positions; (c) another provider/data route. Not fixed here.
- **P1-OPS-1 follow-up — acquisition coverage gap: ADDRESSED 2026-10-07 (uncommitted), provider reach unverified.** Finding: in a read-only copy of the live `market.db`, 86 of 101 symbols start on 2018-08-07, while ordinary backfill requests from today − 8×365 days (2018-10-09 on 2026-10-07), so no controlled path could record a full-coverage observation. Fix: explicit-range, single-symbol acquisition (`backfill_market_data --symbols SYM --start <first stored session> [--end today]`, runbook steps 2–5), through the unchanged admission guard. Whether KBS actually serves sessions back to 2018-08-07 has **not** been verified (no provider calls in this batch; the backfill code comment says the vnstock Community tier limits daily OHLCV to about 8 years, and the KBS adapter shows no client-side limit). The command detects it explicitly (`PROVIDER_HISTORY_COVERAGE_INSUFFICIENT`, nothing written). If KBS cannot reach it, those symbols still have no V1 rebase path (escalate to R4; never trim).
- **P1-REP-1 Manager Forward Evidence omits R3 qualification.** Means and paper points are shown with no qualified/legacy/quarantined distinction, against the roadmap V1.1 reporting acceptance. Small, local fix in the UI batch.
- **P1-UI-1 Dashboard cannot show the daily operational state.** Missing: blocks, log/dataset version, pending signals/open positions, latest Daily stage result, qualification. Fix in the UI batch.

## P2 BACKLOG

The research forward CLI writes the live ledger without binding/gates; `update_paper_positions.py` lacks the same-day session check; QuantCtl lacks a provenance/log status command; the canonical roadmap is untracked and still says R3 IN_PROGRESS; `.env PAPER_DATABASE_PATH` points to the inactive generic store; the universe comes from a VCI listing call each run (a new constituent without history fails Daily); the same-day rule depends on the local clock; log deletion reverts silently to NOT_APPLICABLE; the WIP theme depends on an untracked design folder; 55 CRLF-only diffs; Research Runs WIP needs an include-or-park decision for V1.

## V1 OPERATIONAL ACCEPTANCE TEST

Run on an isolated copy with the real code. Steps: register a baseline on the copy; drive `DailyPipeline` with the real integrity, forward, lifecycle and scan stages; replace only the update stage with deterministic synthetic admissions (no provider); stub Telegram to record and to fail once.

- **Day 1** synthetic session: integrity PASS; forward formation bound; pending signal fills with an entry binding including `REGIME_RS_HISTORY`; evidence observation created; Telegram failure leaves state identical.
- **Day 2**: exit/hold; second observation; the report shows qualification per point; the full-chain qualification equals the filtered views.
- **Retry** Day 2: byte-identical binding/evidence rows.
- **Failure branches:** (1) the revision on a traded symbol blocks and every downstream stage refuses; (2) the P1-OPS-1 reviewed rebase restores operation with correct evidence states (covered offline by `tests/test_market_rebase.py`; still to be run inside the end-to-end acceptance copy); (3) a missing log → MISSING_PROVENANCE; (4) a wrong DB path fails closed.

**PASS** = all assertions hold, original DB hashes unchanged, and no provider or Telegram call. Steps 1–3 and failure branches 1, 3 and 4 were already exercised in the R3 validation and this audit. Branch 2 is now implementable (P1-OPS-1 rebase).

## V1 FREEZE READINESS

**NOT_READY.**

## UI CLOSURE (static review)

Streamlit could not be installed in the cloud workspace (PyPI returns 403 under the org egress policy), so nothing was rendered and no width was inspected. Findings come from code and the prior QA memo.

- **P1 usability:** (1) the Dashboard lacks the operational answers listed above; (2) Forward Evidence lacks qualification; (3) two visual systems — the new research theme on 3 pages, the old theme elsewhere — plus Vietnamese labels ("Nghiên cứu", "Kết quả", "So sánh") mixed with English; (4) the daily workflow is spread over Dashboard / Operations / Run History / Paper State / Forward Evidence (under "Quant Lab"), with no single operational screen.
- **P2 polish:** brand split ("Quant Lab · Local research workspace" vs title "Quant Manager"); raw enum labels (`CONTINUITY_GAP`, `NOT_STARTED`) instead of human-readable ones; the research "Queue exploratory run" primary button has no capability badge; old pages unverified at 1024/768/390 (only research pages were QA'd on 2026-10-05).
- **V1 UI minimum:** one operational overview (health / blocked / legacy / qualified / quarantined hierarchy); qualification labels on every evidence surface; one consistent theme and language; state-changing controls badged with capabilities; a rendered check at 1440/1024/768/390.
- **Post-V1:** research-flow polish, charts, compare UX.

## NEXT STEP

1. **Is the live observation-log activation procedure safe and understood?** Yes, mechanically: it is read-only on market.db, idempotent, and binds to the exact path. Be aware it will also happen implicitly at the next Daily.
2. **Are all active operational entry points canonical?** Yes.
3. **Can any active path bypass the integrity/provenance gates?** No operational path. The research forward CLI and `update_paper_positions` are partial exceptions (P2).
4. **Are the paper and forward stores routed correctly?** Yes.
5. **Are retries and idempotence operationally safe?** Yes.
6. **Are legacy, quarantined and qualified evidence clearly separated?** In the engine and CLI reports, yes. In the Manager, no (P1-REP-1).
7. **Is there any remaining P0/P1 operational defect?** Yes: P1-OPS-1, plus P1-REP-1 and P1-UI-1. *(Update: P1-OPS-1 implemented 2026-10-07, uncommitted; P1-REP-1 and P1-UI-1 remain.)*
8. **What exact action remains before V1 acceptance/freeze?** (a) ~~Decide the P1-OPS-1 recovery design~~ — done: reviewed per-symbol rebase (commit it after adversarial review). (b) Activate the log via the prepared procedure and back it up. (c) Run the UI closure batch, including P1-REP-1, P1-UI-1 and a rendered width check. (d) Commit the roadmap with R3 marked CLOSED. (e) Run the acceptance test above.
9. **Should R4 remain deferred?** Yes, as a global rebuild. P1-OPS-1 may need a bounded per-symbol mechanism that is decided explicitly.
10. **Should V2 remain unopened?** Yes.
