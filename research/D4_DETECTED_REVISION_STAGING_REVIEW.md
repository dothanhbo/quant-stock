# D4: detected historical revisions must stage before shadow append

Review date: 2026-10-04 (Asia/Bangkok). Status: **one shadow-policy blocker narrowed; production and real EOD shadow remain BLOCKED**.

This review compares the working code with `DATA_INTEGRITY_POLICY_DECISION.md` and `D4B2_SHADOW_ACTIVATION_PLAN.md`. The documents contain design statements that are older than parts of the implementation. No proposal is treated as deployed, and no real source is qualified by synthetic fixtures or short CafeF parity.

## Git and data boundary

- Initial branch: `main`.
- Initial HEAD: `ee44eb867aebacdb6ae90d2a7502179fc8796afb`.
- Initially no tracked unstaged or staged changes. There were 18 untracked user files, listed below. They were read-only to this task; initial/final SHA-256 comparisons verify preservation.
- Final branch/HEAD: unchanged. No stage, commit or push.
- Final new changes: three tracked files modified, this report untracked; all initial user files remain untracked and byte-identical.
- Canonical `data/market.db` SHA-256 before/after: `F771B314EF2094373C3753DEF45223DD6E61AEC84E15A369FCD52DA585265FB6`.
- Canonical DB was hashed as a file only, never opened for ingestion or migration. All executed SQL used synthetic databases under new disposable test directories in the permitted visualization workspace.
- No Update/Daily/Scan, backtest, WFO, provider fetch, expensive market audit, paper/Forward mutation, Telegram, broker, production writer or ten-session shadow was run.

Initial user-owned untracked status (all preserved):

```text
?? research/data_integrity_audit/run_20261002T075232Z_f771b314/audit_summary.json
?? research/data_integrity_audit/run_20261002T075232Z_f771b314/flagged_discontinuities.csv
?? research/data_integrity_audit/run_20261002T075232Z_f771b314/symbol_summary.csv
?? research/data_integrity_audit/run_20261002T075232Z_f771b314/vhm_incident.csv
?? research/data_integrity_audit/run_20261002T080507Z_d2_f771b314/calendar_exceptions.csv
?? research/data_integrity_audit/run_20261002T080507Z_d2_f771b314/d2_summary.json
?? research/data_integrity_audit/run_20261002T080507Z_d2_f771b314/high_severity_triage.csv
?? research/data_integrity_audit/run_20261002T080507Z_d2_f771b314/research_impact_matrix.csv
?? research/free_data_pilot/README.md
?? research/free_data_pilot/events.csv
?? research/free_data_pilot/pilot_results.csv
?? research/free_data_pilot/read_only_pilot.py
?? research/free_data_pilot_v1_1/README.md
?? research/free_data_pilot_v1_1/events_v1_1.csv
?? research/free_data_pilot_v1_1/output_v1_1/adjacent_closes_v1_1.csv
?? research/free_data_pilot_v1_1/output_v1_1/event_brackets_v1_1.csv
?? research/free_data_pilot_v1_1/output_v1_1/summary_v1_1.json
?? research/free_data_pilot_v1_1/pilot_v1_1.py
```

## Exactly one selected blocker and its evidence

Selected: **a detected historical revision can receive D4A PASS through exact attribution, then the D4B2 shadow writer approves the batch and appends its new session instead of retaining the entire batch for review**.

Direct evidence:

1. `evaluate_preupdate_market_data()` adds `ATTRIBUTED_HISTORICAL_REVISION` without blocking when all detected changes have matching `RevisionEvidence`; a verified-basis batch can therefore PASS.
2. Before this change, `evaluate_operational_admission()` returned `NOT_REQUIRED_GUARD_PASS` for that batch.
3. `commit_price_batch_shadow()` already used append-only SQL, so it did **not** overwrite the revised overlap, but it appended the genuinely new date and stored an approved manifest.
4. Four new synthetic cases reproduce this approval: small close correction or half-scale historical overlap, each attributed as provider correction or corporate-action restatement. All four failed the desired rejection assertion before the code change, returning `APPROVED`.

This is sufficient evidence to prohibit a write. It is not evidence to repair VHM/CTR/PHR/SIP, qualify a real basis, or classify a real corporate action. The fixture explicitly manufactures verified basis and exact claims only inside synthetic test data.

## Minimal implementation and behavior

| File | Change |
|---|---|
| `quantlab/operational_admission.py` | After completed-session/coverage checks and before the Guard PASS branch, detected `guard.revisions` yields `HISTORICAL_REVISION_REQUIRES_REVIEW`, reason `HISTORICAL_REVISION_NOT_APPEND_ONLY`. Adds staging membership and `HISTORICAL_REVISION_STAGED` shadow status. D4A stays unchanged. |
| `quantlab/transactional_market_data.py` | Existing staging metadata now retains serialized revision kind, symbol, exact old/new claims and source references. Receipt already retains guard changes; staged rows retain candidate values/fingerprint. Uses the existing transaction and schema. |
| `tests/test_operational_market_data_shadow.py` | Adds four attributed PASS cases, replay checks, rollback, unchanged verified PASS control; updates existing mixed-basis/unattributed revision expectations to durable staging. |
| `research/D4_DETECTED_REVISION_STAGING_REVIEW.md` | This evidence, gate assessment and next clone step. |

After: eligible-session batches with detected revisions stage in full, whether D4A PASS or BLOCK. Receipt status is `REJECTED`, rows written are zero, no new manifest or provenance link is created, old prices/row IDs/links remain unchanged, and staged data remains `research_eligible=false`. Missing/rejected completion still rejects before this branch; bootstrap/backfill/historical gaps retain their existing staging classification. There is no promotion action.

## Focused verification

Final selection: **29 passed in 1.88s**.

```text
tests/test_operational_market_data_shadow.py (25 cases)
tests/test_completed_session_operation_identity.py::test_linked_revision_observation_does_not_authorize_price_rewrite
tests/test_completed_session_operation_identity.py::test_shadow_failure_after_identity_allocation_rolls_back_generation
tests/test_preupdate_market_data_guard.py::test_small_exactly_attributed_correction_passes_and_preserves_values
tests/test_preupdate_market_data_guard.py::test_exact_boundary_event_evidence_can_attribute_but_does_not_adjust_values
```

Coverage includes zero price writes for attributed small/scale revisions, durable old/new claims and source identity, unchanged prices and provenance, no manifest creation, false research eligibility, one receipt/staging record on replay, receipt/staging rollback, linked revision generation, and unchanged verified PASS append.

Runtime caveat: `.venv/Scripts/python.exe` and its base interpreter could not execute (OS access denied). Bundled Python 3.12.14 / pandas 3.0.1 / numpy 2.3.5 was used with the repository's pytest 9.1.1 / pluggy appended to `sys.path`; no packages were installed. Plugin autoload and pytest cache were disabled. Default pytest temp access also failed, so each subsequent run used a new UUID directory under the writable visualization workspace. The first green run had two test-only ordering failures (guard OHLC order versus sorted claims); comparisons were corrected before the final pass. The normal Python 3.14 venv was not validated. `git diff --check` also passed.

## Policy decision: every numbered acceptance criterion

`FIXTURE` means code/test support only, not real-source qualification. Existing symbol-session fixtures were inspected rather than rerun because that boundary was not changed. No complete production/research gate set is declared passed.

| Gate | Actual state after this turn | Evidence / remaining blocker |
|---|---|---|
| 1. CTR Feb 16-22 rejection with open venue and stable observations | Existing FIXTURE control | `test_completed_session_operation_identity.py` covers five weekday gap dates as NOT_TRADING before publication stability; synthetic venue evidence and local incident reference. No live lifecycle source is integrated. |
| 2. Missing/unattributable lifecycle and zero writes | Existing FIXTURE control; real coverage BLOCKED | `SymbolSessionEvidence`, evaluator missing/attribution/mismatch reasons, admission rejection, batch fingerprint binding and multi-date coverage rejection exist. Stable security identity, lifecycle intervals and authoritative snapshots for the universe remain absent. |
| 3. VHM-scale switch blocked/staged without overwrite | Focused shadow FIXTURE passed | Existing unattributed mixed-adjustment fixture now stages; new half-scale attributed PASS fixture also stages, with exact old/new receipt values and zero append. This is not a real VHM correction. |
| 4. RAW/ADJUSTED/UNKNOWN/version cannot share writable series | BLOCKED | Guard compares verified unit/basis. UNKNOWN carve-out remains; no durable provider/endpoint/security/venue/basis/method-version pin. CafeF clone binding is specific to that manual adapter. |
| 5. All overlap/configured anchors; any change/missing/insertion durably reviews | PARTIAL; BLOCKED | Returned overlap is compared; detected revisions now stage, gaps already stage. Comparator still uses `1e-9` tolerances, not literal equality. No provider 120-day/rotating-anchor request integration or missing-anchor contract; not every possible difference is detected. |
| 6. Attributed action cannot automatically authorize revision, retain exact claims | Selected shadow subgate passed; global gate BLOCKED | Restatement attribution cannot approve shadow append; kind/reference/claims retained. D4B1 `commit_price_batch()` still upserts a PASS revision. No authoritative event ledger; `RevisionEvidence` is not a full event-terms ledger. |
| 7. Versioned incident exclusion in new labels/features; preserve old bytes | BLOCKED | `historical_qualification.py` appends limitations; label provenance contracts exist but there is no incident row/window overlay at historical feature/outcome boundaries. Canonical bytes preserved this turn. |
| 8. PHR/SIP quarantined until evidence requirements met | BLOCKED for machine enforcement | Policy dispositions remain unchanged; no row/window overlay. PHR needs exact official event and raw/adjusted semantics; SIP needs lifecycle/venue/status/limits/calendar/provider/action evidence. No cause inferred or price changed. |
| 9. Identity/receipt/manifest/staging/rejection/append/provenance atomic, deterministic retries | Focused shadow FIXTURE passed | Existing `BEGIN IMMEDIATE` and optional integrated allocator; replay/concurrency/conflict/rollback tests in selected shadow suite, staging rollback and linked-generation focused tests pass. Real caller wiring is not present. |
| 10. All real shadow on backup-API clones, canonical hash unchanged | Hash preservation passed for this turn; real exercise BLOCKED | Only synthetic SQL tests were executed. `cafef_manual_eod.py` has read-only SQLite backup-API cloning; migration rehearsal alone uses file bytes. No real provider shadow or canonical-clone run performed. |
| 11. Operational never research eligible; staging never automatically promoted | Focused shadow FIXTURE passed | Admission always false, manifest/staging preserve operational-only classification; no promotion implementation invoked. Historical research source qualification is still blocked. |
| 12. Update/backfill/direct/cleanup/quarantine bypasses closed, rollback retains evidence | BLOCKED | Production update/backfill still call `save_price_data`; direct replace, cleanup rebuild, quarantine apply remain bypasses. No activation capability/maintenance boundary or deployed compatible rollback. |

## Shadow plan: D4B3 activation prerequisites 1-9

| Prerequisite | Actual state |
|---|---|
| 1. Approved allowlist and completed-session source | Allowlist implemented in shadow; source qualification/approval BLOCKED. Symbol-session checks now exist, unlike the older plan wording. |
| 2. Production operation ID across retries | Deterministic request/generation/payload/revision linkage implemented in `market_data_operation_identity.py` and optional shadow transaction argument; production scheduled/manual integration BLOCKED. The plan's claim that allocator/linkage are absent is stale. |
| 3. Reviewed migration command, backup/recovery | Additive migration functions, rehearsal and failure fixtures exist; production command, activation marker, backup verification and operational recovery acceptance BLOCKED. |
| 4. Real-universe multiple EOD cycles on clones | BLOCKED; no such cycle run. Offline recorder is synthetic observation-only and makes no completion/admission decision. |
| 5. Daily structured status handling | Shadow enum mapping exists (including new revision-staged status); Daily/deferred/conflict/lock/error integration BLOCKED. `COMPLETED_SESSION_UNRESOLVED` is still mapped to generic shadow rejection rather than the plan's distinct deferred runtime outcome. |
| 6. Update/backfill migration together | BLOCKED; neither imports the D4 shadow boundary. |
| 7. Direct writer/maintenance restrictions | BLOCKED; no bypass closure implemented in this turn. |
| 8. Honest NULL or archive-first policy | Normalized-only batch raw provenance remains NULL. Policy/source permissions and an original KBS archive-first transport remain BLOCKED; no normalized hash relabeled raw. |
| 9. Safe rollback after operational rows | Fixture transaction/migration recovery exists; writer-stop/provenance-retaining compatible rollback release is BLOCKED. |

Additional explicit contracts in the plan:

| Contract / gate | Implementation and unmet conditions |
|---|---|
| Completed-session 1-2: versioned venue calendar, closed session, no local-VNINDEX authority | Source-agnostic calendar model exists; real snapshot process and exception checks not wired. `calendar_sources.json` still marks HOSE baseline notice 2294 original bytes unresolved; source index is not a complete admitted daily calendar. |
| Completed-session 3: watermark or measured approved delay | Evaluator and synthetic watermark/stability tests exist; real watermark or measured approved delay BLOCKED. Stability cannot override inactive/missing symbol evidence. |
| Completed-session 4: requested row and required market corroborator | Batch checks exact target fingerprint/one row; real response/corroborator and per-symbol source integration BLOCKED. Venue/security lifecycle cannot be replaced by a corroborator price row. |
| Completed-session 5: attributable receipt of calendar/publication decision | Calendar/symbol snapshot identifiers, timestamps, fingerprints and evidence hash are in results/receipts. Full independently retained source evidence and real-source resolver BLOCKED. No caller clock or stale update substituted for completion. |
| Weekend/holiday previous completed session and unresolved-not-success | Evaluator rejects supplied non-session/closure/unknown; no real previous-session resolver or Daily deferred handling. BLOCKED. |
| Bounded revision watch: trailing 120d, daily cohort, long horizon, retained anchors, approved pacing/horizons | DESIGN ONLY. Exact claims staging now exists for detected returned changes, but no widening/rotation/missing-anchor execution; no quota behavior measured. BLOCKED. |
| Identity: canonical JSON, namespaces, persisted generation/revision, conflict, integrated transaction | Implemented for temporary SQLite/shadow callers; fixtures support it. Not required by production paths and no basis/version pin in request identity. Production gate BLOCKED. |
| Runtime table: operational accepted, guard PASS, blocked/evidence, backfill/bootstrap/gap, conflict/lock, deferred completion | Shadow outcomes partly exist, and revision now has staging status. Production summary/dependent-stage handling and bounded infrastructure retries BLOCKED. |
| Legacy closure 1: update and backfill together | BLOCKED: both still use legacy save. |
| Legacy closure 2: marker/capability on direct save | BLOCKED: no guarded activation boundary in `save_price_data()`. |
| Legacy closure 3: cleanup refusal/audited replacement | BLOCKED: deletes/rebuilds prices. |
| Legacy closure 4: quarantine apply refusal/audited replacement | BLOCKED: legacy delete outside receipt/provenance transaction. |
| Legacy closure 5: all managed connections enforce foreign keys | D4 connections do enforce them; universal production coverage/operational controls are not proven. BLOCKED. |
| Runbook 1-3: canonical preflight, consistent backup, activated migration | Not executed; no production activation/migration permitted. Backup-API helper exists but runbook remains BLOCKED. |
| Runbook 4: disposable-clone rehearsal | Synthetic migration/replay/conflict/concurrency/failure fixtures exist. Complete backup-API canonical-clone rehearsal and lock-timeout/recovery acceptance not performed in this turn. |
| Runbook 5: authorized real EOD | BLOCKED by lawful KBS access/retention, reviewed isolated HTTP-only transport, calendar/lifecycle, completion, basis pin and watch. Package-path static review remains partial. |
| Runbook 6-7: recovery and compatible rollback retaining prices plus evidence | DESIGN ONLY for deployment; no rollout/restore performed. Older writer-capable build remains unsafe. |
| Minimum shadow: >=10 official consecutive sessions crossing a non-session boundary; every symbol classified | NOT RUN / BLOCKED. Synthetic tests do not count as official sessions. |
| Minimum shadow: idempotency, linked changed payload, no history writes/promotions/research/FK faults/conflicts | Focused fixture support only; ten-cycle/universe reconciliation and per-cycle canonical hashes still required. |
| Minimum shadow: bootstrap/backfill/revision/incomplete publication/lock timeout/post-accept rollback fixtures | Bootstrap/backfill/revision/rollback tests exist; this turn verifies the changed revision path and selected identity regressions. Full controlled clone scenario acceptance, including lock timeout and recovery after accepted rows, remains outstanding. |

## Next small step toward ten-session shadow

Prepare one **offline backup-API clone rehearsal case** that accepts an operational-only synthetic row, then submits an attributed revision under the same request identity and verifies linked generation, staging, replay and injected rollback after staging in one transaction. Use the existing allocator and writer; retain request JSON, receipts, staged bytes, clone integrity/foreign-key checks and before/after canonical hashes. Never feed synthetic attribution into the canonical DB or count it as a real session. This combined scenario is a follow-up, not executed in this turn.

Before selecting any real ten-session window, acquire the missing authoritative calendar/lifecycle/security snapshot, provider lawful-access/retention and completion evidence; integrate them at `evaluate_completed_session()`/the adapter, pin durable basis/version in manifests/request identity, and implement the bounded watch. Then classify every configured symbol on new backup-API clones for ten official sessions crossing a non-session boundary, with zero historical writes/promotions/research eligibility and unchanged canonical hashes. None of these future integrations is authorized production activation.

## Residual risk and compatibility

- Legacy prices, row IDs and old artifacts were not repaired or requalified. Historical incident descendants can still contaminate research until the versioned exclusion overlay is implemented.
- D4B1 `commit_price_batch()` remains an upsert path for attributed PASS revisions. Direct/production/maintenance writers remain unprotected. This patch protects only the existing D4B2 shadow admission path.
- Detection still uses the existing numeric tolerance and returned request range. A tiny change or revision outside returned overlap is not addressed here.
- Existing durable receipts retain their original decisions on replay; old approvals are not retrospectively invalidated. Old receipts/archives do not acquire missing revision claims. Replaying is not a new price write.
- The additional TEXT enum values need consumer support. Shadow mapping/replay supports them; an older build reading new staged receipts may reject the unknown enum. Do not treat this as compatible deployed rollback.
- Staging metadata adds claims without a schema change; it never changes the price series, raw-payload identity, D4A decision or research eligibility.
- Low-level writer accepts an explicit target; it is not itself a production-path safety barrier. Keep all future calls on explicit disposable targets until bypass gates are closed.
