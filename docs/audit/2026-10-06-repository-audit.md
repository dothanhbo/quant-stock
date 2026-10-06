# Repository audit — 2026-10-06

Scope: whole repository as found on the maintainer workstation on
2026-10-06 (Asia/Ho_Chi_Minh), including uncommitted work. Method: static
reading of the code paths named below, targeted tracing, and a sandboxed copy
of the test suite (no live databases, no provider/network, no Telegram).
This document records facts with evidence; where something could not be
verified it says so. It is an audit record, not a specification: later code
changes do not update it.

## 0. Factual starting state (recorded before any edit)

| Item | Value |
|---|---|
| Branch | `main` |
| Local HEAD | `ec3566ab` "Checkpoint clone-only D4 shadow target and scoped admission entrypoint" |
| `origin/main` (last fetch) | `ee44eb86` "add read-only CafeF EOD monitoring" — local `main` is ahead (unpushed D4 commit(s)) |
| Local tags not on remote clone | `v1.0.1-research`, `v1.1.0` |
| Tracked files | 708 |
| Uncommitted, content changes | `manager/app.py`, `manager/theme.py`, `quantctl/cli.py`, `quantctl/registry.py`, `tests/test_quant_manager.py` (WIP "Manager research runs") |
| Uncommitted, line-ending only | ~55 files (backtesting/*, strategy/*, several tests, research reports) converted LF→CRLF; no `.gitattributes` |
| Untracked | `manager/README.md`, `manager/pages/research_runs.py`, `manager/pages/run_results.py`, `manager/research_ui.py`, `quantctl/research_runs.py`, `quantlab/manager_factor_runner.py`, `research/D4_KBS_PILOT_SOURCE_QUALIFICATION.md`, `research/D4_PERSONAL_EOD_MINIMUM_DECISION_MEMO.md`, `research/MANAGER_RESEARCH_FLOW_CHECKPOINT.md`, `research/MANAGER_UI_DESIGN_QA.md`, `research/run_neutral_adx_h10_exploration.py`, `research/eod_source_evidence/`, `research/free_data_pilot*/`, `quant-ui-design/`, `tests/test_manager_research_runs.py`, `tests/test_research_ui_design.py`, `tests/ui/`, root `*.zip` (CafeF pilots) |
| Runtime databases (`data/`, gitignored) | `market.db` (17 MB), `paper_trading.db` (generic/V1), `paper_trading_v2.db` (Q70), `paper_trading_v3.db` (V3), `forward_validation.db`, `prospective_portfolio_evidence.db`, `operation_history.db`, `paper_archive/paper_trading_v1_20260824-234143.db`. None were opened by this audit. |
| Local Python env (`.venv`) | CPython 3.14 (cp314); pandas 3.0.6, numpy 2.5.3, SQLAlchemy 2.0.54, streamlit 1.64.0, plotly 7.1.0, altair 6.3.0, vnstock 4.0.2 (+vnai 2.6.0), pytest 9.1.1. `__pycache__` also contains cp312 files. README says "Python 3.12+". |

Discrepancies between the audit brief and the code:

* The label `DESCRIPTIVE_ONLY_NOT_ADJUSTMENT_VERIFIED` does not exist in
  code. The equivalent qualifications are
  `quantctl/historical_qualification.py::HISTORICAL_EVIDENCE_QUALIFICATIONS`
  (`LEGACY_UNVERIFIED`, `PRICE_ADJUSTMENT_UNKNOWN`,
  `CORPORATE_ACTION_PROVENANCE_UNAVAILABLE`,
  `TICKER_IDENTITY_HISTORY_UNAVAILABLE`, plus the D1/D2 incident reference),
  `EXPLORATORY_RETROSPECTIVE_ONLY` / `research_eligible=False` for Manager
  runs, and `*_DESCRIPTIVE_ONLY` reasons in the research decision gate.
* There is no explicit "V2 Q70" strategy class. Q70 is
  `HybridTrendDonchianEntryModel(mode="trend_context")` +
  `strategy/paper_v2_gate.py` (quality ≥ 0.70) + frozen ATR 2×/5×, fixed exit,
  trailing disabled, assembled by `scripts/run_paper_v2_lifecycle.py` and
  `scripts/run_daily.py`. V3 = the same + `strategy/breadth_exposure.py`.
* No scheduler definition is tracked (the GitHub workflow was deleted in
  `3b6670f`; VPS units/timers are not in the repository).

## 1. Executive assessment

Engineering health is well above a typical single-maintainer project. The
current code already has: a fail-closed market-data integrity gate before any
stateful stage, idempotent paper entry/exit intents (unique
`source_intent_id`), an append-only Forward ledger enforced by SQLite
triggers, capability-declared operations (`quantctl/operations.py`), an
operation-history ledger with secret redaction, identity hashing of research
artifacts, a pre-registration registry with a state machine, an archive
boundary, read-only Manager inspection, and ~1,780 tests.

Research-system maturity is *partial*: governance contracts exist and are
used, but (a) the evidence they govern lives in a gitignored directory, (b)
frozen strategy behaviour still depends on mutable inputs that are not part
of its identity, and (c) the market-data writer overwrites history in place
(the uncommitted D4 track is addressing this).

The highest risks are operational/provenance, not numerical: a leaked bot
token in public history, evidence durability, one CLI/UI path (`scan`) that
behaves differently from the daily path, and environment-driven strategy
semantics.

## 2. System map

| # | Area | Canonical implementation | Side effects | Persistence | Main risks |
|---|---|---|---|---|---|
| 1 | Data ingestion | `scripts/update_data.py` (vnstock `Quote(source="KBS")`, 7-day overlap refresh, retries) | network, `market.db` write, `logs/update_data_failed.txt` (CWD-relative) | `prices` upsert via `core.database.save_price_data` (`INSERT OR REPLACE`) | provider revisions overwrite history with no journal; `--cleanup-only` rewrites the whole table |
| 2 | Data persistence | `core/database.py` (SQLAlchemy engine bound at import), `core/paths.py` | schema creation only via `initialize_market_database()` | `data/market.db` | engine binds `MARKET_DATABASE_PATH` at import time |
| 3 | Features | `strategy/indicators.py`, `strategy/cache.py` (production); `quantlab/features/*`, `quantlab/panels/*` (research, PIT) | none | feature cache under `research_results/` (evictable) | two feature stacks; parity guarded by tests |
| 4 | Strategy | `strategy/hybrid_trend_donchian_entry.py` (+ `donchian_breakout_entry.py`, `trend_strategy_v1.py`, `scoring.py`, `filters.py`), `strategy/paper_v2_gate.py`, `strategy/breadth_exposure.py` | none (pure) | — | thresholds from mutable `config/strategy.yaml` and env |
| 5 | Market regime | `strategy/market_regime.py` (VNINDEX regime → `strategy.yaml` thresholds), `strategy/market_state.py` + `core/historical_breadth.py` (breadth) | reads market DB | — | regime thresholds not in runtime fingerprint |
| 6 | Scanner | `strategy/scanner.py::run_scan` with a processor from `scripts/run_daily.py::run_strategy_scanner` | integrity gate, scan telemetry + `signals` rows in `market.db`, paper queue, Telegram | market DB + active paper store | standalone `python -m strategy.scanner` has **no** Q70/V3 processor |
| 7 | Portfolio/risk | production: `execution/risk_guard.py`, `backtesting/position_sizers/*`; research: `quantlab/portfolio/*` | — | — | two lifecycle/executor defaults for max open positions (5 vs 10), deliberately fingerprinted |
| 8 | Paper trading | `scripts/run_paper_lifecycle.py` via V2/V3 wrappers; `execution/signal_executor.py`, `paper_broker.py`, `lifecycle_manager.py`, `persistence.py` | paper DB writes, prospective evidence ledger | `paper_trading_v2.db` / `_v3.db` (active by `PAPER_STRATEGY_VERSION`); generic `paper_trading.db` is legacy | multi-transaction lifecycle (recoverable, not atomic); schema auto-migrates on open |
| 9 | Forward validation | `quantlab/forward/*`, protocol `research/forward_validation/protocol_v1.json` | `forward_validation.db` append-only | triggers block UPDATE/DELETE | activation is manual maintenance |
| 10 | Research framework | `research/run_quantlab_*.py` (22 runners), `quantlab/evaluation/*`, `quantlab/hypotheses/*`, `research/alpha_hypotheses/registry_v1.json` | writes `research_results/<run>/` | **gitignored** | evidence not version-controlled; tests depend on it |
| 11 | Manager UI | `manager/app.py` + `manager/pages/*` → `quantctl/*` services | operations page launches subprocesses after confirmation | reads DBs read-only (`mode=ro&immutable=1`) | WIP files uncommitted; `theme.py` needs untracked `quant-ui-design/design-tokens.json` |
| 12 | CLI / QuantCtl | `quantctl/cli.py`, `quantctl/operations.py` (`update`, `scan`, `daily` as subprocesses) | as declared by `OperationCapability` | `data/operation_history.db` | `scan` = ungated scanner (see 6) |
| 13 | Telegram | broadcast: `services/telegram_client.py` (scanner); query bot: `services/telegram_bot/*` (separate process) | network | — | token leaked in history; retries can duplicate messages |
| 14 | Scheduling | not tracked (VPS) | — | — | no single-instance lock |
| 15 | Tests | `tests/` (≈1,786 incl. new) | some read real DBs; some need gitignored artifacts | — | env leakage between tests (fixed in batch 1) |
| 16 | Archive/legacy | `research/archive/**` (149 py), `dashboard/` (legacy paper dashboard), `reporting/`, `analysis/` | — | — | 24 tests import archive modules (protects frozen semantics) |

Daily order (verified in `app/daily_pipeline.py`): update → integrity gate →
Forward → paper lifecycle (pending next-open fills, exits) → scanner (queue,
Telegram). Integrity `NOT_APPLICABLE` (prior session) stops stateful stages.

## 3. What is already good — preserve, do not rewrite

* Fail-closed integrity gate (`core/market_data_integrity.py`) and its use in
  lifecycle and scanner entrypoints.
* Idempotent paper intents (`execution/persistence.py`, partial unique index
  on `source_intent_id`) and the prospective-evidence cursor design.
* Forward ledger immutability by triggers; formation bundles transactional.
* `quantctl/operations.py` capability model + `quantctl/run_history.py`
  (UTC timestamps, redaction, stale-run classification).
* Read-only inspection (`quantctl/registry.sqlite_read_only`).
* Research contracts with identity hashes; PIT observation/outcome panels with
  explicit censoring; EXPLORATORY labelling of Manager runs.
* `research/archive/README.md` and `research_results/README.md` retention
  policies; `research/CLEANUP_LIST.md`.
* The self-audit records in `quantlab/operations/production_audit.py` and
  `production_gap_gate.py` — keep them as historical evidence (see §9).

## 4. Critical risks (evidence-backed)

| ID | Sev | Finding | Evidence |
|---|---|---|---|
| S1 | **P0 → REMEDIATED / CLOSED** | Telegram bot token hardcoded in early commits; repository is publicly cloneable. | `git log -S` finds the literal in `9cfa6ae` (2026-07-28) and neighbours; anonymous clone succeeded. **Status 2026-10-06:** owner confirmed the old token was revoked and a new token is in use (outside Git, in the ignored `.env`). Kept as a historical finding; history intentionally not rewritten (the revoked literal remains visible in old commits and is inert). Ongoing control: secrets only in `.env` / `.env.example` holds names only. |
| R1 | P1 | Canonical research evidence (and many test fixtures) live only in gitignored `research_results/`. | `.gitignore`; `research_results/README.md` names the canonical checkpoint there; in a checkout without that directory 13 tests fail with `FileNotFoundError` under `research_results/` (Forward, monitoring, provenance tests). A single disk loss loses the evidence that justifies decisions. |
| O1 | P1 | `python -m strategy.scanner` (= `quantctl scan`, Manager "scan") runs `run_scan()` with **no** Q70/V3 processor and without the V2/V3 wrapper configuration, but routes to the *active* (Q70/V3) paper store. | `strategy/scanner.py` `__main__`; `quantctl/operations.py` `scan → strategy.scanner`; daily uses `PaperV2Scanner/PaperV3Scanner` (`scripts/run_daily.py`). If `.env` has `PAPER_TRADING_ENABLED=true`, ungated hybrid signals are queued into the frozen store; in any case Telegram broadcasts ungated signals. **Status 2026-10-06: FIXED in Phase 2** — see §13. |
| D1 | P1 | Market data is upserted with `INSERT OR REPLACE` over a 7-day overlap: provider revisions overwrite stored history with no record. | `core/database.py::save_price_data`, `scripts/update_data.py::REFRESH_OVERLAP_DAYS`. The uncommitted D4 work (`quantlab/transactional_market_data.py`, shadow admission, detected-revision blocking) targets exactly this; not touched. |
| C1 | P1 | Frozen strategy behaviour depends on inputs outside its identity: `config/strategy.yaml` (regime thresholds, RSI bounds, `min_data_rows`) and `TRADING_*`/`PAPER_*` env. The runtime fingerprint covers env-derived policy but **not** `strategy.yaml`. The V2 wrapper does not pin `TRADING_ENTRY_MODEL` (V3 does); `run_daily --skip-lifecycle` skips V2 pinning entirely. | `config/strategy_loader.py`, `quantctl/runtime_configuration.py`, `scripts/run_paper_v2_lifecycle.py`, `scripts/run_daily.py::main`. Not changed: adding inputs changes the fingerprint and breaks evidence continuity → needs a versioned contract decision. |
| O2 | P1 | Standalone updater exited 0 when symbols failed → ledger recorded SUCCESS. | `scripts/update_data.py::main` ignored `update_all_symbols` issues; `run_tracked_entrypoint` maps 0→SUCCESS. **Fixed in batch 1.** |
| O3 | P1 | Paper store *defaults* were CWD-relative while overrides, market DB and Manager/QuantCtl inspectors were root-anchored (split brain if launched elsewhere; SQLite would create an empty store). The gap gate record `13A-i2` is marked CLOSED but only overrides had been fixed. | `config/paper_store.py::resolve_store_definition`, `quantctl/state.py::_absolute_store_path`, `quantlab/operations/production_gap_gate.py`. **Fixed in batch 1** (anchored + ambiguity guard). |

## 5. Technical debt (prioritised)

**P1** — R1, D1, C1 above (O2, O3 fixed in batch 1; O1 fixed in Phase 2; S1 remediated by the owner).

**P2**
* No single-instance guard for mutating operations; two concurrent `daily`
  runs rely on SQLite uniqueness; Telegram messages can duplicate.
* Naive local time for session logic: `date.today()` in
  `core/market_data_integrity.py`, `datetime.now()` in `app/daily_pipeline.py`,
  `scripts/update_data.py`. Correct only while host TZ matches Vietnam or runs
  happen in the same calendar day.
* Path resolution duplicated with divergent override semantics:
  `quantctl/registry.inspect_system` and `quantctl/state` use
  `root/data/market.db` (ignore `MARKET_DATABASE_PATH`); `cafef-monitor
  --canonical-database data/market.db` and `update_data.FAILED_LOG_PATH` are
  CWD-relative; `backtesting/engine.DEFAULT_DB_PATH="market.db"` (fails closed);
  `PaperTradingStore` default `"paper_trading.db"`.
* Dependencies unpinned; `matplotlib`, `jinja2`, `markupsafe` used but not
  declared; Python 3.14 locally vs "3.12+" documented.
* CRLF churn in ~55 files; decide a `.gitattributes` policy before the next
  commit to avoid unreadable diffs.
* `manager/theme.py` (WIP) reads untracked `quant-ui-design/design-tokens.json`
  at import; commit them together.
* Test hygiene (fixed in batch 1): env leakage between tests, a test reading
  live DBs, Telegram secret required at test collection, a vacuous assertion.
  Remaining: tests depending on `research_results/`; `sys.modules`-based purity
  tests are order-sensitive.
* Research/backtest modules import the production scanner module
  (`backtesting/engine.py`, `quantlab/adapters/frozen_q70_candidate_decisions.py`),
  which still evaluates `TradingPolicy.from_env()` at import.

**P3**
* `config/trading_policy.py`: duplicated imports; `apply_strategy_config`
  mutates `os.environ` globally.
* Empty `NAME=` env values crash float/int parsing (no "empty = unset").
* `PaperBroker.reset_paper_account` hard-deletes all paper tables (unused in
  production; keep it that way).
* `core.database.cleanup_price_duplicates` (only via `update_data
  --cleanup-only`) silently drops rows whose `time` cannot be parsed.
* `scripts/migrate_open_positions_policy.py` defaulted to the legacy generic
  DB (hardened in batch 1).
* Incident windows are hardcoded in `quantlab/manager_factor_runner.py`
  (WIP) in addition to the audit CSV.

**P4** — `CHANGELOG.md` has escaped Markdown; mixed Vietnamese/English
messages (fine, but pick one per surface).

## 6. Research-integrity risks

* **Survivorship / universe**: current VN100 applied retrospectively is
  acknowledged in README and labelled as biased legacy comparisons. Keep.
* **Adjustment/provenance**: unknown adjustment basis, D1/D2 incidents,
  calendar holes — qualified via `HISTORICAL_EVIDENCE_QUALIFICATIONS`; keep the
  qualification fail-closed.
* **Look-ahead**: production uses next-open execution; signal-date causality
  and initial-level parity tests exist. No new look-ahead found in the traced
  paths.
* **Multiple testing**: the Manager can launch arbitrary retrospective factor
  runs. They are labelled `EXPLORATORY`/`research_eligible=False` (good), but
  the number of exploratory looks per factor is not recorded next to the
  pre-registrations. `research/run_neutral_adx_h10_exploration.py`
  (2026-10-05) explores ADX after `neutral-adx-*` were registered
  (2026-10-02): fine as exploration, but any protocol freeze must cite it.
* **Reproducibility**: artifacts carry identities, but the inputs that define
  them (market DB snapshot, `research_results/`) are not versioned, and
  dependencies are unpinned.
* **Strategy identity**: see C1.

## 7. Operational risks

* Paper stores: three live files + one archive snapshot. Active store is
  chosen only by `PAPER_STRATEGY_VERSION` (Q70 default). Inspection shows the
  generic store's role as `INACTIVE_WITH_STATE` when it holds positions —
  correct; never merge stores without an explicit, reversible migration.
* Mutation matrix (verified):

| Command | Market DB | Paper DB | Forward DB | Evidence DB | Telegram | Network |
|---|---|---|---|---|---|---|
| `python -m scripts.run_daily` | W (update, telemetry, signals) | W | W | W | send | provider |
| `python -m scripts.update_data` | W | – | – | – | – | provider |
| `python -m strategy.scanner` (= `quantctl scan`, Manager scan) | W (telemetry, signals) | W (active store; paper trading forced on as in daily since Phase 2; was: only if `PAPER_TRADING_ENABLED`) | – | – | send | universe lookup |
| `python -m scripts.run_paper_v2_lifecycle` / `_v3_` | R | W | – | W | – | – |
| `python -m quantctl status/doctor/data status/paper status/forward status/research list/status/history` | R | R | R | R | – | – |
| `python -m quantctl research run` | R | – | – | – | – | – (writes `research_results/`) |
| `python -m research.run_quantlab_forward_validation status` | – | – | R | – | – | – |
| `scripts/init_db`, `quarantine_invalid_ohlc --apply`, `migrate_open_positions_policy --apply`, `backfill_*`, `register_paper_lifecycle` | W | W (some) | – | – | – | some |

* Partial-run behaviour is documented in `production_audit.py` failure
  scenarios and remains accurate for transaction boundaries.

## 8. Architecture findings

* Dependency direction is mostly right: Manager pages → `quantctl` services →
  domain modules; no page talks to SQLite directly.
* Reverse dependency: `backtesting/` and `quantlab/adapters/` import
  `strategy.scanner` (an orchestration module) for `evaluate_prepared_row`.
  Extracting that pure function into `strategy/` would remove import-time
  configuration from research (behaviour-neutral if done as a move with a
  re-export); not done yet.
* Configuration is split across `.env`, `config/strategy.yaml`,
  `config/strategy_config.py` (frozen dataclasses, partially unused),
  `config/trading_policy.py` (env), wrappers that mutate `os.environ`.

## 9. Canonicality / legacy map

| Component | Implementation | Class | Safe action |
|---|---|---|---|
| Daily entrypoint | `scripts/run_daily.py` (`main.py` alias) | A canonical | keep |
| Update | `scripts/update_data.py` | A | keep; D4 writer replaces internals later |
| Scanner | `strategy/scanner.py::run_scan` + run_daily processor | A — single path `app/strategy_scan.py::run_strategy_scan` (Phase 2) | keep |
| Q70 policy | hybrid entry + `paper_v2_gate.py` + V2 wrapper | A, frozen | never edit semantics |
| V3 policy | Q70 + `breadth_exposure.py` + V3 wrapper | A, frozen | same |
| `strategy/paper_v2_scanner.py`, `paper_v3_scanner.py` | processors | A | keep |
| `scripts/run_paper_v2.py`, `run_paper_v3.py` | standalone scan wrappers | B compatibility | keep; document |
| `trend_strategy_v1.py`, `donchian_breakout_entry.py`, `scoring.py`, `filters.py`, `watchlist.py` | used by hybrid / `TRADING_ENTRY_MODEL=trend` | A/B | keep |
| `trend_pullback_retest_entry.py` | tests/archive only | D | keep for reproduction |
| `relative_strength.py` vs `relative_strength_v2.py` | scanner vs Q70 gate | both A | keep both; different contracts |
| Paper DB resolver | `config/paper_store.py` | A | keep (hardened) |
| Generic `paper_trading.db` | legacy store | C/D user data | never delete/merge |
| `data/paper_archive/*.db` | snapshot | C user data | keep |
| Forward | `quantlab/forward/*` | A | keep |
| Research runners | `research/run_quantlab_*.py` (22) | A | keep |
| `research/archive/**` | historical | C/D | keep; no style refactors |
| `backtesting/*` | historical + parity | B/C/D | keep; refactor only with parity tests |
| Manager | `manager/*` → `quantctl/*` | A (WIP uncommitted) | commit WIP together |
| `dashboard/` | legacy paper dashboard | B | keep, not canonical |
| `reporting/`, `analysis/` | presentation | B | keep |
| `quantlab/operations/production_audit.py`, `production_gap_gate.py` | frozen audit records | C evidence | do not "update"; supersede with new records |
| `.github` workflow | deleted (`3b6670f`) | — | — |
| CafeF `*.zip` at repo root | untracked pilot inputs | G unknown | move into `research/eod_source_evidence/` only with your confirmation |

## 10. Refactor roadmap (small, independent phases)

| Phase | Goal | Files | Behaviour change | Risk | Validation | Rollback |
|---|---|---|---|---|---|---|
| 0 (you) | Revoke token; decide WIP commit; `.gitattributes` | — | none | — | — | — |
| 1 (done) | Batch 1 below | see §11 | operational only | low | targeted + full sandbox suite | `git revert` / restore files |
| 2 | Resolve O1: route standalone `scan` through the same strategy-resolved processor and wrapper config as daily, or make it non-mutating | `strategy/scanner.py`, `scripts/run_daily.py`, `quantctl/operations.py` | standalone/QuantCtl/Manager scan now gated like daily | medium | `tests/test_canonical_scan_path.py` | revert — **done, see §13** |
| 3 | Evidence durability: separate evidence store/backup with manifests; tests use committed fixtures | `research_results/` policy, tests | none to results | low | fresh-clone test run | n/a |
| 4 | Strategy identity v3: hash `strategy.yaml` + entry model into the runtime fingerprint; V2 wrapper pins `TRADING_ENTRY_MODEL`; `--skip-lifecycle` applies the same pinning | `quantctl/runtime_configuration.py`, wrappers | fingerprint changes (versioned) | medium | parity on fingerprint inputs | revert |
| 5 | Single-instance lock for mutating ops in `run_tracked_entrypoint` | `quantctl/run_history.py` | second concurrent run refused | low | unit test | revert |
| 6 | Vietnam session date helper used by integrity gate and pipeline | `core/` | none on VN-time hosts | low | TZ-parametrised tests | revert |
| 7 | One path resolver for inspectors (honour `MARKET_DATABASE_PATH`, Forward path) | `quantctl/registry.py` (WIP file), `quantctl/state.py` | none by default | low | existing state tests | revert |
| 8 | Pin dependencies from the VPS environment; declare optional extras | `requirements*.txt` | none | low | fresh venv install | revert |
| 9 | Continue D4 market writer migration (your track) | `quantlab/transactional_market_data.py` … | by design | — | your D4 tests | — |
| 10 | Move pure `evaluate_prepared_row` out of `strategy.scanner` with re-export | `strategy/` | none | low | numerical parity on fixtures | revert |

## 11. Batch 1 (implemented 2026-10-06)

| Change | Files | Behavioural impact |
|---|---|---|
| Telegram client built at the start of `run_scan()` instead of at import | `strategy/scanner.py` | Importing the scanner (research, backtests, tests, query bot) no longer needs `TELEGRAM_TOKEN`. A production scan with a missing token still fails before any provider read, integrity check or write. |
| Standalone updater exit code reflects failed/backfill symbols | `scripts/update_data.py` | `python -m scripts.update_data` / `quantctl update` now exit 1 and are recorded FAILED when symbols remain incomplete. Written rows unchanged. Daily pipeline unaffected. |
| Paper store defaults anchored to repository root + ambiguity guard | `config/paper_store.py`, `quantctl/state.py`, `quantctl/research_status.py` | Same file when run from the repo root (documented production). From another CWD: previously a new empty DB; now the real store, or a hard `AmbiguousPaperStoreError` if a different CWD-relative store file exists. Runtime-configuration fingerprint verified identical before/after. |
| Policy migration tool shows its target and refuses implicit `--apply` on a non-active store | `scripts/migrate_open_positions_policy.py` | Dry-run unchanged; `--apply` needs `--database` unless the target is the active store. |
| Test isolation | `tests/conftest.py`, `tests/test_dashboard_data.py`, `tests/test_point_in_time_outcome_panel.py`, `tests/test_paper_store_routing.py`, new `tests/test_operational_exit_and_import_contracts.py` | `os.environ` restored after each test; live-DB test is opt-in (`QUANT_ALLOW_LIVE_DB_TESTS=1`); vacuous assertion removed; 8 new tests. |
| Docs | `.env.example`, `README.md` (path contract, updater exit code), this file | none |

Not changed on purpose: anything in the five WIP files, any strategy
threshold, any database, any archived research, the frozen audit records.

## 12. Validation performed

* Sandbox copy of the working tree (no `data/`, no network, Telegram token
  dummy, `vnstock` import stub, SQLAlchemy 2.0.54 copied from the local venv;
  PyPI is blocked in the sandbox, so `streamlit`/`altair` were unavailable).
* Baseline before edits: 1,778 tests, 47 failures — all environmental:
  13 need gitignored `research_results/`, 15 need the CTR evidence packet
  (not copied to the sandbox), 14 need streamlit/altair (unavailable), 3 need
  the live DBs, 1 needs vnstock in a subprocess, 1 `sys.modules` purity test
  is order-sensitive (passes alone).
* After batch 1: 1,786 tests, 46 failures (same environmental set; the
  live-DB test is now skipped), 8 new tests pass, no test changed from pass to
  fail. Order-variation runs of the previously polluting files are clean.
* Runtime-configuration fingerprint parity before/after (Q70 and V3).
* Recommended local check (your machine, real environment, read-only):
  `python -m pytest -q tests/test_paper_store_routing.py tests/test_operational_exit_and_import_contracts.py tests/test_operational_integrity_boundaries.py tests/test_v3_configuration_integrity.py tests/test_quantctl_state.py tests/test_daily_pipeline.py`
  then the full suite once.

## 13. Phase 2 — canonical scan path (2026-10-06)

**Root cause.** The strategy decision (Q70 gate `PaperV2Scanner`, V3
`PaperV3Scanner`) was attached only in `scripts/run_daily.py::run_strategy_scanner`,
and the strategy's frozen runtime (`PAPER_TRADING_ENABLED`, ATR 2×/5×, V3 entry
model, trailing flags) was pinned only as a side effect of the daily
*lifecycle* wrappers running first. `python -m strategy.scanner` — the module
`quantctl scan` and the Manager scan operation launch — called
`run_scan()` with no processor and with whatever `.env` held when the scanner
module froze its `TradingPolicy` at import. In a captured run with a hostile
environment it broadcast and recorded all four raw candidates with 3× ATR
stops, while daily produced two Q70-accepted signals with 2× ATR stops (and,
for V3 below 40 % breadth, none).

**Canonical path.** `app/strategy_scan.py::run_strategy_scan`: resolve strategy
→ pin its runtime via `configure_v2_environment` / `configure_v3_environment`
(the same functions the lifecycle uses) → build the Q70/V3 processor (version
checked against the strategy) → import the scanner and fail closed if its
frozen `TradingPolicy` differs → `strategy.scanner.run_scan(result_processor=…)`.
Used by the daily scanner stage, `python -m strategy.scanner` (QuantCtl,
Manager), `scripts/run_paper_v2.py`, `scripts/run_paper_v3.py`.
`run_scan` now raises without a processor.

**Parity evidence.** Golden traces of the pre-change daily stage
(`tests/fixtures/scan_parity/`) for Q70, V3 half exposure and V3 breadth block
are reproduced exactly by every entrypoint (`tests/test_canonical_scan_path.py`).

**Intentional behaviour changes.** (1) Standalone / QuantCtl / Manager scan now
queue and broadcast only strategy-accepted signals with the frozen levels, and
force paper trading on for the active store exactly like daily.
(2) `run_daily --skip-lifecycle` on Q70 now pins the Q70 runtime before scanning
(V3 already did). (3) The compatibility wrappers additionally pin
`PAPER_ATR_STOP_MULTIPLIER=2.0` / `PAPER_V2_DISABLE_TRAILING` as daily does.
Thresholds, gates, ordering, routing and stored data are unchanged.
