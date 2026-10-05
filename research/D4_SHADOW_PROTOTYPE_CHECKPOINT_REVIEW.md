# D4 disposable shadow prototype checkpoint

Date: 2026-10-05 (Asia/Bangkok). Baseline: `main`, HEAD `ca0a2b200aeb7ade1ba139bd00ad8bb35af9b3de`, empty index; one tracked prototype modification and 23 untracked files (18 outside artifacts, migration plan, two previous prototype reports and two test files). Scope: review/checkpoint the existing clone-only factory and DML scope, plus one controlled shadow entrypoint. **Production/research qualification, universal writer closure and real ten-session shadow remain BLOCKED.**

## Controlled entrypoint and before/after

Before, the only shadow commit API accepted factory handles, arbitrary paths and raw SQLite connections. Now `quantlab.transactional_market_data.commit_disposable_price_batch_shadow(target, batch, target_capability=...)` accepts only the exact live factory handle and its registered capability. `_verify_clone_target` checks the actual registered connection/file/schema before any delegation. Missing/fabricated/foreign capability, unregistered/expired handle, closed connection, strings/paths and both protected/unmanaged raw connections raise `ShadowTargetError` before price/provenance SQL attempts. No substitute path/connection keyword is exposed.

The wrapper delegates exactly once, with the same handle/token, to the existing `commit_price_batch_shadow` transaction. It has no exception handler, retry, target coercion or fallback. Admission denial is returned as denial; identity conflicts and exceptions propagate. It changes neither production callers nor the existing overloads. Unmanaged path/raw overloads remain an **activation blocker**, even though raw access through an intact factory connection cannot acquire an admission DML grant.

Target binding, transaction generation, admission, receipt/manifest/staging, append/link, replay and rollback remain owned by the existing implementation. No additional schema, allocator or ingestion framework is introduced. Operation identity requests remain optional in this prototype; the migration plan's mandatory common-caller identity and series pins are still design work.

## Review of the complete prototype

- Factory identity comes from its own actual SQLite connection, `database_list`, resolved file identity/held descriptor, single-link check and schema cookie. Default canonical and configured protected targets/aliases are refused before backup mutation. Source uses `mode=ro`, `query_only=ON` and SQLite backup API; no direct open-file copying or schema migration. This turn used only synthetic sources/clones, never a canonical SQL connection.
- Default denial covers price/provenance INSERT/UPDATE/DELETE on factory connections. A positive admission opens an INSERT-only main-table scope tied to the exact connection and live transaction object/generation. It closes in `finally` before the post-price checkpoint/COMMIT; the writer's outer `finally` also clears transaction/grant/cursors on success, replay, rejection, staging, exception and rollback. Factory close clears remaining state. Pre-entry failure never opens a scope.
- Reinstalled authorizers expire statements; managed cursors executed inside the scope are closed on exit, including active RETURNING statements. Existing cache 0/128, trigger/schema, ATTACH/temp, transaction-restart and exception results are reused. The new wrapper adds no cursor or authorization path.
- Detected revisions still block the entire batch, including its new day, before price/provenance writes. Existing staging retains normalized rows, exact old/new detected changes, claims and source references; replay creates no duplicate or promotion, rollback restores prices and provenance. This checkpoint does not change detection tolerance `1e-9` or old receipts.
- Target/file/snapshot integrity does **not** grant source authority or claim approval. The unchanged packet adapter requires an independently reviewed packet pin and exact symbol/venue/session mapping; hashes alone do not approve a new packet. Its denial-only mapping cannot produce TRADING_CONFIRMED. Synthetic admission fixtures qualify no real session. CTR/HOSE/2022-02-17 remains a comparison with the first-trading date 23 February, not a directly stated 17 February prohibition; identity/ISIN is from VSDC. Historical availability remains UNVERIFIED; no later session, venue calendar, publication completion or basis inference is added.
- Operational outcomes/manifests/staging retain `research_eligible=false`. A target capability is permission to use this managed disposable path, not a statement that its inputs are research eligible.

The [factory review](D4_DISPOSABLE_TARGET_CAPABILITY_REVIEW.md), [DML scope review](D4_SHADOW_ADMISSION_DML_SCOPE_REVIEW.md) and [migration plan](D4_MARKET_WRITER_MIGRATION_PLAN.md) are preserved byte-for-byte as dated snapshots. The factory review's earlier ordinary raw-DML gap is superseded by the DML scope; its privileged/unmanaged limitations remain. Their former next steps and unstaged state describe those earlier turns. This checkpoint supplies the current state; design-only release gates are not silently marked implemented.

## Focused checks used

Reused, **not rerun**: the DML scope report's **59 distinct passing focused cases** (40 initial affected DML cases, 13 affected factory cases, five later outcome/cursor cases and one AUTOINCREMENT case). The earlier factory report also records physical-alias checks and one Windows symlink privilege skip; that skipped case is not a pass.

New `tests/test_disposable_shadow_entrypoint.py`: **25 passed in 1.99s**. Bundled Python 3.12/repository pytest dependencies; bytecode, plugin autoload and pytest cache disabled. Only this new file was executed, using fresh synthetic fixture sources and factory backup-API clones in a disposable visualization directory.

| New entrypoint check | Evidence |
|---|---|
| Append and replay | One new price/link, one approved manifest, false research eligibility; replay leaves the full persisted snapshot unchanged and has zero fresh price/link attempts |
| Backfill/revision staging and replay | Rejected, zero price/link attempts, old prices/provenance unchanged, exactly one staging row with research eligibility 0; replay adds no duplicate or promotion |
| Missing completion, NOT_TRADING, UNKNOWN | Rejected despite other synthetic fixture claims; no price/link attempts, no admitted manifest, no fallback |
| Eleven invalid input/capability combinations | Zero delegate calls and zero authorizer-observed price/provenance INSERT/UPDATE/DELETE attempts; full clone snapshot unchanged |
| Closed/expired handles | Zero delegate calls and zero price/link attempts; clone remains equal to the synthetic source |
| Three fault checkpoints | Fault before price DML, after actual price/link DML and after staging; exactly one delegate call; all five tables rolled back, grants/cursors/transaction absent; following raw write denied |
| Identity conflict and delegate validation error | Exception propagates without fallback/retry; no price/link attempts or changed snapshots |

All tested outcomes retain false research eligibility. Rejection results are distinct from infrastructure exceptions and successful replay; no rejection is counted as an accepted append. Read-only syntax/whitespace, staged allowlist/content review and preservation checks accompany the checkpoint. No full suite, repeated prior rehearsal, provider fetch, Update/Daily/Scan, paper/Forward/Telegram, backtest, WFO or production activation.

## Protection boundary and activation blockers

Protection covers ordinary SQL through the factory-issued connection/cursors and this controlled entrypoint while the installed guard is intact. Instance authorizer replacement is denied, but explicitly calling the **base** `sqlite3.Connection.set_authorizer` can disable it. Existing tests positively demonstrate base-API and fresh-connection writes on disposable fixtures. Private Python state, base cursor APIs/native mutation APIs, hostile callbacks and filesystem privileges remain outside protection. File/path/held-descriptor checks do not prove resistance to hostile VFS/filesystem replacement races. General receipt/metadata DML is not default-denied, and the SQL scope does not bind every value against hostile internal helper replacement.

Still blocked: legacy shadow path/raw overloads and manual lower-level apply/schema APIs; nonrevision D4B1 upsert; Update/Daily/Backfill/direct save; cleanup/quarantine/bootstrap maintenance; unmanaged/older connections. No durable activation identity, cross-connection database fence, controlled production filesystem/process permissions, unified cutover or compatible no-write rollback release has been implemented. Real calendar/security/lifecycle/publication/basis/version/watch coverage, incident exclusion and point-in-time research qualification remain separate source/policy blockers. This prototype does not close those writers or authorize ten-session observation.

## Checkpoint files and preserved state

Commit allowlist: `quantlab/transactional_market_data.py`; `tests/test_disposable_shadow_target.py`; `tests/test_shadow_admission_dml_scope.py`; `tests/test_disposable_shadow_entrypoint.py`; `research/D4_MARKET_WRITER_MIGRATION_PLAN.md`; `research/D4_DISPOSABLE_TARGET_CAPABILITY_REVIEW.md`; `research/D4_SHADOW_ADMISSION_DML_SCOPE_REVIEW.md`; this report. The migration plan and previous reports are included unchanged. No DB, cache, disposable output, original CTR snapshot or outside artifact belongs to the allowlist. No push.

Canonical `data/market.db` SHA-256 before/after (file hashing only):

`f771b314ef2094373c3753def45223dd6e61aec84e15a369fcd52da585265fb6`

All 18 outside artifact hashes and the three previous document hashes remain identical. The final commit identity is reported from Git after checkpoint creation rather than embedded recursively in this file.

**Exactly one next step:** prototype the migration plan's offline common candidate/status adapter on disposable factory targets, routing through this strict entrypoint and proving rejected/staged/UNKNOWN outcomes cannot become caller success or fallback writes. Keep production entrypoints and unified-release activation disabled.
