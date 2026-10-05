# D4 disposable SQLite target/capability prototype

Date: 2026-10-05 (Asia/Bangkok). Initial/final HEAD: `ca0a2b200aeb7ade1ba139bd00ad8bb35af9b3de`, branch `main`. Initial tracked tree/index clean; the migration plan and 18 outside artifacts were already untracked. Scope: one clone-only factory integrated into the existing D4B2 shadow connection/transaction boundary. **Prototype checks pass; production, direct-writer closure and real ten-session shadow remain BLOCKED.**

## Binding and actual target verification

`quantlab.transactional_market_data.create_disposable_shadow_target(source_database, clone_database)` is a context manager yielding `(DisposableShadowTarget, ShadowTargetCapability)`. It does not adopt an existing caller-supplied database or use a disposable env flag. It exclusively creates a new file, holds its file descriptor, opens its own SQLite connection with `mode=rw` and statement caching disabled, and registers the exact handle/token/connection objects privately. The handle has no caller-settable fields; independently constructing one grants no authority. Subclass hash/equality impersonation is rejected.

Before any backup mutation, the factory checks the **actual connection's** `PRAGMA database_list`, its opened main filename, resolved path, regular-file `(st_dev, st_ino)` identity against both the path and held descriptor, link count, and protected targets. The default canonical path is always protected; current configuration may add another protected path but cannot remove that default. Physical identity catches hardlinks; path resolution catches normal/symlink aliases. Unknown/missing file identity, wrong actual connection or a replaced/aliased target fails closed. These are physical/context identities, not a persisted database UUID or source-authority claim.

Only after verification does it open the source with URI `mode=ro`, set `query_only=ON`, and use `Connection.backup()` into the verified clone. Source is explicitly closed. It records the clone's resulting schema cookie, enables foreign keys, performs clone `quick_check`, installs a connection authorizer and yields the handle/token. No schema migration or schema upgrade is performed. Context exit revokes registration and closes the connection/descriptor; files remain disposable evidence, not automatically deleted.

`commit_price_batch_shadow(handle, batch, target_capability=token)` reuses `_connection` and the existing `BEGIN IMMEDIATE` admission transaction. Missing, fabricated, expired or another handle's token raises `ShadowTargetError`; a token accompanying a path/raw connection is also rejected. Validation checks exact connection object identity again after BEGIN and after each existing fault-injection checkpoint, including immediately before price/provenance insertion and before commit. Closed connections fail verification; SQLite close rolls back their active transaction. Existing receipts, admission, manifests, staging, replay/conflict and false research eligibility are unchanged.

## ATTACH, temp shadowing and escapes

The factory connection authorizer rejects ATTACH/DETACH, schema DDL, temp-object creation, unsafe PRAGMA assignment and access to another database. Validation independently rejects attached databases and any temp objects that could shadow unqualified table names. SQLite `quick_check` can materialize an **empty** temp schema: that is allowed, with only temp-schema metadata inspection permitted. The first valid-append test exposed an overly strict empty-temp check; the check was corrected and all affected new-file cases rerun.

Tests deliberately remove the authorizer through private raw access, attach a read-only synthetic database or create a temp `prices` table, then invoke the real writer. Both pre-entry and after-manifest cases reject before any price/provenance SQL attempts; partial receipt/manifest work rolls back. An injected substituted connection is likewise rejected after BEGIN with no price/provenance attempt and both fixture databases unchanged.

**Open blocker: raw DML still bypasses admission.** A dedicated synthetic test retrieves the registered raw connection through Python internals, inserts a price row and commits with zero receipts. The authorizer currently fences redirection/schema, not price/provenance DML outside admission. Existing `commit_price_batch_shadow(path/raw_connection)` overloads still work without a capability and remain explicit bypasses. D4B1, direct save, Update/Backfill, cleanup/quarantine and manual lower-level targets have not been fenced or wired.

This in-process registry/opaque token is a managed-call boundary, not security against arbitrary Python introspection, registry/authorizer replacement, hostile fault callbacks or a file owner changing files/schema. The standard sqlite3 API exposes the VFS filename, not its native open-file identity; path/stat/held-descriptor checks do not prove resistance to hostile concurrent filesystem replacement races. No durable activation marker, database DML trigger fence, persistent target UUID or OS permission boundary is implemented. Fault injection is trusted test instrumentation; arbitrary callbacks committing early are not certified rollback behavior. Do not describe this prototype as closing every direct SQL bypass.

## Focused evidence

All SQL executed for this turn used fresh synthetic SQLite sources/clones under a new disposable visualization test directory. Fixture setup creates an already-shaped schema directly; no migration helpers/ALTER, canonical clone rehearsal or existing suite were executed. Existing `_batch`/OHLCV fixture helpers are reused; their fabricated calendar/publication/lifecycle claims qualify no real symbol-session.

`tests/test_disposable_shadow_target.py`: final affected-file run **31 passed, 1 skipped in 2.49s**. Two subsequently added regressions (handle subclass impersonation and revision staging rollback/replay) ran alone: **2 passed in 1.26s**. Total distinct passing cases: **33**; one symlink case skipped because Windows returned `WinError 1314` (missing symlink creation privilege). Hardlink cases passed. The first collection attempt used the wrong test-module import; it was corrected to the repository's `tests` package before execution. No unrelated previously passing suite/rehearsal was repeated.

| Case | Evidence |
|---|---|
| Valid factory clone append | One genuinely new price/provenance link, APPROVED operational-only receipt/manifest; false research eligibility; foreign-key check clean; synthetic source rows and file hash unchanged |
| Identical replay / conflicting fingerprint | Replay has zero fresh price/provenance attempts and identical persisted snapshot; conflict has zero attempts and no changed rows/receipts |
| Missing/fake/foreign token, unregistered/impersonated handle, wrong overload | SQLite authorizer recorder logs **zero INSERT/UPDATE/DELETE attempts** on `prices` and `market_price_provenance`; all fixture tables unchanged |
| Closed/revoked/substituted connection | Same zero-attempt checks; closing after approval receipt and substitution after BEGIN leave no partial durable evidence |
| Canonical/default/configured/normal alias/hardlink | Rejected before opening SQLite; tests replace `sqlite3.connect` with a failure spy. Alias tests use synthetic protected files; actual default canonical destination also rejected without SQLite open |
| Wrong actual main / memory connection | Factory refuses before opening the backup source, hence before any backup; source snapshot unchanged |
| Added target hardlink; ATTACH/temp shadowing | Binding invalidation/connection denial; escaped redirection at entry or after manifest still gives zero price/provenance attempts and full rollback |
| Fault after actual price/link writes | Recorder proves both clone DML actions occurred; rollback restores every fixture table, closes transaction; retry appends once |
| Historical revision staging fault/replay | Whole batch rejected; original prices/provenance unchanged; staging fault rolls back, retry stages once, replay creates no duplicate; zero price/provenance attempts, research eligibility false |
| Legacy path/raw writer and private raw DML | Bypasses positively demonstrated only on synthetic targets, retained as blockers rather than counted as capability closure |

Bundled Python 3.12 with repository pytest dependencies; bytecode, plugin autoload and pytest cache disabled. `git diff --check` passes; final source syntax/whitespace and workspace preservation checks are separate read-only checks. Only the side-effect-free `core.paths` utility is reused; no provider package, production writer/entrypoint or `core.database` engine is imported/executed. No canonical SQL connection, Update/Daily/Scan, paper/Forward/Telegram, backtest or production activation.

## Files and preserved state

- Modified: `quantlab/transactional_market_data.py` (factory, handle/token validation, existing connection/shadow integration only).
- Added: `tests/test_disposable_shadow_target.py` and this report.
- Preserved byte-for-byte: `research/D4_MARKET_WRITER_MIGRATION_PLAN.md` and all 18 outside artifacts; old evidence packets, policies, receipts and reports untouched. Index empty; no stage/commit/push.

Canonical `data/market.db` SHA-256 before/after (file hashing only):

`f771b314ef2094373c3753def45223dd6e61aec84e15a369fcd52da585265fb6`

## Exactly one next step

On disposable fixtures, add default-deny price/provenance DML authorization scoped to the existing shadow admission transaction, then prove the demonstrated private raw-DML escape is refused while admitted append/replay/rollback still work. Limit that step to factory-owned connections; it must not claim to close legacy overloads, hostile Python or production writers.
