# D4 market-writer migration plan

Date: 2026-10-04. Baseline: `main`, HEAD `ca0a2b200aeb7ade1ba139bd00ad8bb35af9b3de`; clean tracked tree/index, 18 unrelated untracked artifacts. Status: **DESIGN_ONLY; production and real ten-session shadow BLOCKED**. This document changes no policy, schema, source qualification or writer behavior.

Authority: [policy decision](DATA_INTEGRITY_POLICY_DECISION.md), [shadow plan](D4B2_SHADOW_ACTIVATION_PLAN.md). Current implementation evidence: [D4B1 review](D4B1_DETECTED_REVISION_BLOCKING_REVIEW.md), [revision staging review](D4_DETECTED_REVISION_STAGING_REVIEW.md), [synthetic clone rehearsal](D4_DETECTED_REVISION_CLONE_REHEARSAL.md), [CTR admission review](D4_CTR_PACKET_SHADOW_ADMISSION_REVIEW.md). Earlier documents are dated snapshots: attributed detected revisions no longer reach D4B1 upsert; the optional shadow generation allocator and symbol-session gate now exist. Their production wiring and complete source coverage still do not.

## Existing controls versus proposed work

| Boundary | CODE / existing focused evidence | Still DESIGN or BLOCKED |
|---|---|---|
| Preparation | `market_data_shadow_adapter.prepare_dataframe_price_batch`: rejects empty/malformed/duplicate candidates, explicit attribution; normalized content is not raw HTTP | Shared Update/Backfill caller, lawful reviewed transport and durable preparation-failure status |
| Admission | `completed_session.evaluate_completed_session` requires calendar, symbol-session and publication evidence; `operational_admission.evaluate_operational_admission` separates staging/append | Real source resolver, stable security/venue history, universe coverage and evidence authority validation |
| Revision | D4B1 and D4B2 block detected revisions even with attribution/Guard PASS; exact values/claims/references retained; D4B1 review reuses 34 passing focused cases | Wider/rotating watch, missing expected anchors and durable basis/version binding; detection tolerance remains `1e-9` |
| Transaction | `commit_price_batch_shadow`: allocator (optional), receipt, manifest, append, links and staging in `BEGIN IMMEDIATE`; replay/rollback demonstrated on synthetic backup-API clone | Mandatory identity at shared caller, target capability, activation barrier and production rollback build |
| CTR evidence | `symbol_session_packet_adapter.load_symbol_session_packet`: reviewed packet pin + snapshot hashes, denial-only; 14 original cases plus two checkpoint regressions | Only CTR/HOSE/2022-02-17 supported; historical availability UNVERIFIED; no positive lifecycle resolver |
| Eligibility | Shadow outcomes/manifests/staging keep `research_eligible=false`, no automatic promotion | Incident exclusion/label-feature lineage and research qualification remain separate blockers |

These are reused results, not tests rerun for this design. No full D4 acceptance gate is declared complete.

## 1. Common candidate boundary for Update

Design one offline callable around the existing adapter and shadow transaction, with an explicit target handle, ingestion intent, retained provider observation, evidence bundle and identity request. It must not import `core.database` or fetch data. Future `scripts.update_data.update_symbol` supplies its already-returned candidate; `quantctl.operations` update and `main.py`/`scripts.run_daily.update_market_data` then reach the same boundary through `update_all_symbols`.

Flow: resolve target/security/series and retained evidence → validate/prepare the **whole** observation → `evaluate_completed_session` → `PreparedPriceBatch` → `commit_price_batch_shadow(..., operation_identity_request=...)`. D4A and operational admission remain inside the reserved write transaction; a caller's Guard PASS never authorizes SQL. Approved writes use only `_append_new_prices` and `_link_selected_prices`; unchanged anchors receive no replacement provenance. Receipt/manifest/identity allocation must commit with those writes or roll back together. All paths retain false research eligibility.

Make operation identity mandatory at this boundary; retain explicit namespaces for daily/manual update, backfill and bootstrap. Reuse durable request/generation allocation, but review a versioned contract binding stable security, series basis/method version and evidence identity before rollout. The current request schema lacks those series pins. Identical rows with changed evidence can currently reuse an allocation yet conflict on batch fingerprint: do not overwrite receipts or silently discard changed metadata. Same-request changed observations link generations; different requested ranges/target dates need honest explicit references, not fabricated `revision_of`.

Current admission allows only one genuinely new date, matching its completed-session target. Do not split a revision-containing observation to salvage its new day, trim failed anchors, or feed several catch-up dates through one completion result. Multi-date catch-up remains blocked/staged until a separate per-date evidence/whole-observation review contract exists. Implement the policy's bounded watch in preparation; its proposed 120-day/cohort parameters still need approval. All returned overlaps use the existing detector; preserve exact retained values and its `1e-9` tolerance.

The current completion result covers its target date, not every returned historical row/anchor. Preparation must additionally check calendar/lifecycle validity for candidate and anchor dates against reviewed date-specific claims or covered intervals; invalid or unresolved rows cannot be treated as observed sessions. This coverage check is DESIGN, not a capability supplied by the current target-session fixture.

## 2. Backfill and bootstrap are staging-only

Replace the future `scripts.backfill_market_data.backfill_symbol` save with the same boundary and `IngestionIntent.BACKFILL`; an empty-history Update produces a bootstrap review outcome. Existing admission precedence is honest: no history → `BOOTSTRAP_PENDING`, historical gaps → gap review, otherwise explicit BACKFILL/BOOTSTRAP → backfill staging. Every such outcome has zero price/provenance writes, no admission manifest and no automatic promotion, even if all supplied claims are verified.

Retain the entire normalized candidate, coverage limitations, old/new revisions, exact claims and immutable references in receipt/staging. Missing historical lifecycle/completion/basis evidence may be retained as UNKNOWN in staging, never filled with current-session claims. The current identity request requires a calendar/target: design an explicitly versioned unresolved staging identity for missing evidence; do not fabricate a calendar or silently omit production idempotency. Stage replay remains deterministic. Historical repair/import needs a separate reviewed migration; this release grants no repair capability and does not requalify old receipts.

## 3. Target/capability boundary and legacy refusal

DESIGN: extend the existing `_connection` boundary with a factory-issued, opaque capability bound to the actual connection, verified database identity, resolved file identity, environment, policy/schema/build version and operation scope. Scopes: admission append, staging-only, read-only; no general upsert/repair scope in this release. Caller strings, a path named “clone”, `OperationCapability.MARKET_DATA_WRITE` in the CLI, or an env flag are not write authority. Resolve relative paths/aliases using `core.paths`; verify the opened database identity, not just the requested spelling. Reject mismatched handles, replaced targets, default/unknown targets and unknown schema/activation state before DML. Enable foreign keys on every managed connection.

A backup can inherit the source's database/activation metadata: assign and bind a distinct disposable target/environment identity through the controlled clone registry, retaining the source identity/hash as lineage. Copied metadata alone must never grant canonical capability to a clone or clone capability to the source. Check file identity for hard-link/path aliases as well as normalized paths.

Persist an activation record and check it inside the reserved transaction. Once activated, `save_price_data` refuses **all** calls; a capability routes to the append transaction rather than legitimizing `INSERT OR REPLACE`. `cleanup_price_duplicates`, quarantine `--apply`, D4B1 upsert and exposed SQL helpers also refuse activated targets outside their permitted boundary. Quarantine dry-run opens read-only. Keep legacy cleanup/delete disabled pending separately reviewed maintenance preserving row IDs, quarantine evidence, receipts and provenance; non-mutating incident exclusion is preferred to deletion.

Application guards cannot stop an older binary or arbitrary SQLite tooling with file write access. Proposed additive database DML fences on prices/provenance must reject missing/wrong connection-bound transaction authorization, reject historical UPDATE/DELETE, and bind admitted INSERTs to the authorized batch/receipt; test raw SQL, legacy SQLAlchemy connections and older-build attempts on clones. Fence implementation/DDL is **not present** and needs review, including trigger/connection behavior. Restrict canonical write permissions and process launch paths to the controlled writer; a file owner able to alter schema is outside this protection. No “unforgeable” Python token alone closes that boundary. Activation is blocked until both managed bypass denial and the older/raw-writer boundary are demonstrated.

## 4. Required source contracts

| Input | Required retained authority and binding | Failure behavior / current gap |
|---|---|---|
| Calendar | Official venue calendar plus exceptional-closure notices; version, covered dates, publication/effective time, collection time, URL/snapshot/hash | Missing/ambiguous venue session → UNRESOLVED; no weekday/clock or local VNINDEX substitute. Live resolver unqualified |
| Symbol-session | Exchange listing/first-trading, transfer, suspension/resumption/delisting records; stable security identity via VSDC/issuer records; reviewed exact security/venue/date claim | Missing, mismatch, unverifiable snapshot → UNKNOWN; NOT_TRADING blocks regardless of publication. Calendar opening is not permission to trade |
| Publication | Attributable provider completion watermark, or retained complete observations/fingerprints with a measured approved delay policy; target symbol/session/provider must match; retain/check the shadow plan's required market-calendar corroborator | Missing/still changing/partial response → unresolved/rejected; after-close response, empty payload or repeated fabricated row is not completion. KBS rule and real corroborator integration unqualified |
| Basis/version/unit | Attributable provider endpoint semantics, unit, raw/adjusted method/version and effective changes; archive responses honestly | No invented RAW/ADJUSTED/version claims. UNKNOWN carve-out remains operational-only under policy, with separately reviewed explicit UNKNOWN series identity; absent required binding/semantics blocks append. Durable series pin unimplemented |

Integrity hashes prove retained bytes, not authority or approval. Resolver review must bind an independently approved claim to the exact snapshot and applicable interval; source-reference presence alone in existing dataclasses is insufficient qualification. Keep original HTTP hashes NULL when unavailable; never relabel normalized hashes. Provider access/retention and the [reviewed transport constraints](D4B2_7B_STATIC_SECURITY_REVIEW.md) remain prerequisites; no quarantined package execution for real shadow.

CTR/HOSE/2022-02-17 is NOT_TRADING by comparing 17 February with the HOSE notice's first-trading date 23 February, not a sentence directly naming 17 February. Security/ISIN comes from VSDC; depository transfer effectiveness is distinct from trading permission. This retrospective packet cannot establish later sessions, UPCoM status, venue calendar, completion or basis. Its current snapshot does not establish historical byte availability: keep UNVERIFIED and `research_eligible=false`.

## 5. One release barrier, no mixed writer rollout

Build/review the common boundary, both caller migrations, direct/maintenance refusals, database fences, status mapping and a compatible no-write rollback build as **one release unit**. Deploy with writers stopped: disable scheduled/manual launch paths, drain processes/connections, hold exclusive operational write exclusion, verify the target and backup, then apply reviewed additive migrations/fences/activation atomically. Check all binaries/entrypoints against the same database activation identity before enabling any admission append. A crash, partial deployment or failed check leaves writers disabled. Do not enable migrated Update while Backfill/legacy writers remain runnable against that target.

`core.initialize_market_database` (Daily bootstrap and `scripts.init_db`) must recognize the activated schema and use reviewed migration commands instead of automatic legacy DDL/cleanup. Close the module-global SQLAlchemy engine during cutover; changing an env flag cannot rebind it. Scheduler/manual/CLI launch restrictions and file permissions are part of the release acceptance, not an alternate optional feature flag.

## 6. Rejection, backup and rollback

Preserve `update_all_symbols` → `run_daily.update_market_data` → `DailyPipeline._run_data_stage`'s `(success_count, issues)` interface using structured per-symbol outcomes behind it. Only approved/replayed approval with matching target evidence counts as success; historical replay counts are not fresh writes. Rejected/UNKNOWN, staged, incomplete, identity conflict and exhausted infrastructure retries appear in issues with receipt/reason. Empty/malformed candidates need a durable preparation-failure record, not a fabricated valid batch or SUCCESS. No fallback to `save_price_data`, backfill or data-error network retry; only bounded transport/lock retry. Required current-session failures must block dependent stages even when legacy `stop_on_data_errors=false` would continue; wiring is DESIGN only and no downstream stage is run here.

Backup procedure: source URI `mode=ro`, separate destination, SQLite backup API while write exclusion is held; record source DB hash/size and WAL/journal state, then independently check backup integrity, foreign keys, schema and critical row counts. Never copy an open DB file. A backup-API clone need not have the source's exact file hash; compare contents/integrity and record each identity honestly. Rehearse only on disposable targets; canonical hash preservation under write exclusion is a safety check, not data qualification.

On migration/release failure keep no-write mode and retained evidence. After any admitted rows, prefer forward repair; a full restore needs an explicit decision to discard subsequent prices **and** their matching receipts/provenance/identity state together, plus an evidence backup. Never splice tables, reset the activation marker to permit legacy access, or deploy a pre-D4 writer. Even restoring pre-activation bytes must reinstall/verify the no-write barrier before any process is resumed. Rollback may use only the compatible build, keeping D4 tables/readability and legacy mutations disabled.

## Writers still outside complete coverage at this HEAD

| Actual entrypoint / call path | SQL action / present gap | Required closure (DESIGN) |
|---|---|---|
| `quantctl.operations` update; `scripts.update_data.update_symbol` → `core.database.save_price_data` | `INSERT OR REPLACE prices`; no admission or provenance. Empty response currently SUCCESS | Common candidate boundary and truthful status |
| `main.py` → `scripts.run_daily.main` → `update_market_data` → `update_all_symbols` | Same Update bypass; post-write pipeline integrity cannot undo it | Same release; fail dependent stages on unresolved required data |
| `scripts.backfill_market_data.backfill_symbol` → `save_price_data` | Historical `INSERT OR REPLACE`; Boolean success | Staging-only, durable review outcome |
| Direct `core.database.save_price_data` callers | Module-global engine can replace linked rows without receipts | Unconditional activated-target refusal; controlled connection/DML fence |
| `scripts.update_data --cleanup-only` → `core.database.cleanup_price_duplicates` | Delete/reinsert all prices; row IDs/links endangered, FK enforcement not guaranteed | Refuse activated target; separate maintenance design |
| `scripts.quarantine_invalid_ohlc.main --apply` | Quarantine insert + price delete, direct SQLite path, no admission | Refuse activated/linked rows; read-only inspection; no deletion release capability |
| `transactional_market_data.commit_price_batch` → `_upsert_prices` / `_link_prices` | Detected revisions blocked, but other Guard PASS batches still INSERT/ON CONFLICT UPDATE prices/links; no full session gate | Deny activated ingestion; migrate callers to full append admission |
| `commit_price_batch_shadow` → `_append_new_prices` / `_link_selected_prices` | Admission exists, but explicit arbitrary target and optional request; private helpers have no capability barrier | Verified target/transaction capability and mandatory request |
| `cafef_manual_eod.run_manual_shadow` → backup clone → `apply_archive_to_shadow` | Uses shadow admission; low-level apply accepts arbitrary path and performs schema setup, no target fence | Clone-issued capability also required at low-level apply/schema setup; current evidence remains denial/staging only |
| `research/rehearse_d4_detected_revision_clone.py::rehearse` synthetic seed; `tests/` fixture DML | Intentional direct price/link inserts confined by convention to disposable targets | Explicit fixture-only target boundary; never grant canonical capability |

Static tracked-Python search found these price/provenance paths. `scripts.init_db` and Daily bootstrap additionally perform schema DDL, not price ingestion; they need the release/migration boundary above. Arbitrary external SQLite access is unenumerable and remains prohibited/fenced operationally. Paper-position writers are separate and unchanged. No listed path is activated by this plan.

## 7. Clone order and acceptance before ten-session shadow

| Order | Required acceptance evidence | Status now |
|---|---|---|
| 1. Target boundary prototype | Explicit disposable target/capability; canonical aliases, missing/wrong token, connection substitution and env-only selection refuse before DML | DESIGN |
| 2. Offline common caller/status adapter | Approved one-session append; missing/mismatched evidence zero price/link attempts; bootstrap/backfill/gap stage; revision blocks whole batch; no false success/fallback | Existing writer fixtures support parts; caller integration DESIGN |
| 3. Source/series/watch binding | Reviewed live calendar/security/publication/basis contracts; missing anchors and basis/version change block; incident rows cannot qualify anchors; UNKNOWN never becomes verified | BLOCKED; no resolver/source expansion this turn |
| 4. Unified cutover/fences on clone | Update, Backfill, direct save, D4B1, cleanup, quarantine, manual lower-level target and raw/older writer attempts cannot bypass; all FK checks pass | DESIGN |
| 5. Recovery/compatibility on clone | Additive migration replay, identity conflict, lock timeout, faults after actual writes and stage, restore after append; rollback build still refuses legacy writes | Prior synthetic transaction/rehearsal supports parts; release recovery DESIGN |
| 6. Begin real ten-session observation | All preceding gates reviewed/passed; lawful separately approved read-only transport; coverage for every configured symbol/date; honest archival/NULL raw provenance; deterministic classification and bounded watch | BLOCKED |

After gate 6, observe ten consecutive **official completed sessions** including a weekend/non-session boundary on disposable backup-API clones. Retain every symbol outcome/receipt and source snapshot; reconcile zero historical updates, zero automatic promotion/research eligibility, replay stability, no unexplained conflicts or FK/integrity errors, canonical hash unchanged every cycle. Synthetic fixtures and CTR retrospective evidence do not count as sessions. These criteria allow shadow assessment only; research incident exclusion/lineage and all policy gates remain required for their own qualification, and production activation needs a separate decision.

**Exactly one next implementation:** a clone-only target-handle/capability factory prototype with focused synthetic SQLite checks for canonical/alias rejection, absent/wrong capability and substituted connection. Require explicit newly created disposable targets, return no default canonical target, perform no production imports or wiring and no activation/schema migration. This establishes gate 1's managed-call boundary; it does not claim to close legacy/raw writer bypasses yet.

## This design turn's verification

Read-only code/call-path review and existing focused results above; no new tests, rehearsal, provider fetch or SQLite open of canonical. Only this design document is added, unstaged. Canonical file SHA-256 before and after: `f771b314ef2094373c3753def45223dd6e61aec84e15a369fcd52da585265fb6`; all 18 outside artifact hashes remain identical. HEAD/index/tracked status unchanged. Existing code, evidence packets, reports and policy are preserved. Document links/whitespace are checked without executing application modules.
