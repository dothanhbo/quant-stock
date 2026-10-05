# D4 factory-connection admission DML scope

Date: 2026-10-05 (Asia/Bangkok). Initial/final HEAD: `ca0a2b200aeb7ade1ba139bd00ad8bb35af9b3de`, branch `main`; index empty. This continues the uncommitted target prototype. The earlier [capability review](D4_DISPOSABLE_TARGET_CAPABILITY_REVIEW.md) is preserved as its previous-state evidence; the [migration plan](D4_MARKET_WRITER_MIGRATION_PLAN.md) and 18 outside artifacts are unchanged. **Managed factory-connection SQL checks pass; production and universal writer closure remain BLOCKED.**

## Before / after and binding

Before, the factory authorizer denied redirection/schema but allowed raw price/provenance DML. Now a connection-specific authorizer denies INSERT/UPDATE/DELETE on `main.prices` and `main.market_price_provenance` by default, through connection, cursor and executemany SQL APIs. Raw reads remain usable. This is not a default-deny policy for all metadata tables.

`commit_price_batch_shadow` still owns the existing `BEGIN IMMEDIATE`, admission, receipt/manifest/staging, append/link and commit/rollback transaction. Only the valid factory-handle path issues an internal transaction object after BEGIN. It binds the exact connection and authorizer-observed transaction generation; BEGIN/COMMIT/ROLLBACK/savepoint boundaries invalidate an old object. Rollback followed by a new BEGIN cannot reuse it. There is no caller-writable admission flag or env switch.

After positive admission and receipt/manifest preparation, `_admission_price_dml` revalidates target/capability and the live transaction, then opens a short scope around the existing `_append_new_prices` and `_link_selected_prices`. That scope permits **INSERT only**, on those two main tables, with the exact registered connection/transaction object. UPDATE/DELETE remain denied even inside it. Other tables/connections/databases receive no extended permission. No fault callback is called inside the granted window.

The scope revokes in `finally` before the after-price checkpoint and COMMIT. The writer also revokes its transaction registration in an outer `finally` on approval, replay, rejection, staging, exception or rollback. Factory close clears remaining registrations. The raw overload using the factory-owned connection cannot issue a grant; its attempted append is denied and rolled back. Legacy overloads using an unmanaged connection/path remain outside protection.

## SQLite statements, cursors and schema

Factory connections still use `cached_statements=0`. Scope entry/exit installs the connection-bound authorizer with the base SQLite method, expiring prepared statements. Focused tests additionally force a 128-statement cache: the identical authorized INSERT SQL cannot be executed again outside its scope with another connection.execute call.

Authorizer reinstallation alone does not terminate an already-active statement. Factory connection/cursor subclasses therefore route the ordinary execute/executemany/executescript APIs through managed cursors, tracking cursors executed while a scope is open. Revocation closes those cursors before resetting the authorizer. A retained `INSERT ... RETURNING` cursor is closed at scope exit, allowing the valid append/link transaction to finish while preventing any later fetch/re-execution of that cursor. A caller-supplied cursor factory is rejected. There is no SQL parser or second ingestion framework.

ATTACH/DETACH, unsafe PRAGMA assignments, schema DDL and temp objects remain denied. The structural authorizer now denies inherited trigger/view-origin actions too: neither an admitted price INSERT triggering another-table write nor an ordinary other-table write triggering a price UPDATE can execute. Empty SQLite temp-schema inspection remains allowed; nonempty temp shadowing is rejected. A native AUTOINCREMENT fixture still appends successfully without granting arbitrary writes to another table.

## Actual focused evidence

SQL ran only on fresh synthetic fixture databases and factory backup-API clones in a new disposable visualization directory. No canonical SQL connection, provider fetch or previous clone rehearsal. Fixture CREATE statements, including adversarial trigger definitions and a fresh AUTOINCREMENT schema variant, are test setup; no migration helpers/ALTER target migration or production schema change was run.

| Check | Result |
|---|---|
| Raw INSERT/UPDATE/DELETE, both tables, connection/cursor/executemany | 18 cases denied by SQLite; seeded provenance exists for UPDATE/DELETE; complete table snapshots and total_changes unchanged |
| Permission scope | Exact connection/transaction required; other table/connection, UPDATE/DELETE and ATTACH denied even during authorized append |
| Cached/done prepared statement | Cache sizes 0 and 128; captured scoped cursor closed; same SQL through connection.execute denied after revocation, no data changes |
| Active RETURNING statement | Admitted append/link commits correctly; retained cursor cannot be fetched afterward; subsequent raw INSERT denied and snapshots unchanged |
| Authorizer / cursor instance API | Removal, replacement with an allow-all callback and alternative cursor factory rejected; raw DML still denied |
| Triggers / schema / ATTACH / temp | Trigger DML and schema routes denied; affected earlier redirection regressions pass, including privileged ATTACH/temp injection detected before price/provenance attempts |
| Success, replay, staging, rejection | No surviving grant/transaction/cursor registration; following raw price and provenance DML denied; research eligibility false |
| Exceptions | Faults before price writes, inside granted DML, after actual price/link writes and after staging restore all fixture tables; transaction closed; following raw cursor INSERT denied |
| Changed transaction | Rollback + new BEGIN invalidates original transaction token; no price/provenance write and no partial durable evidence |
| Unmanaged / privileged escape controls | New connection and base-C authorizer removal/replacement demonstrably still write synthetic data; recorded as limits, not closure |

Executed focused checks:

- New `tests/test_shadow_admission_dml_scope.py`: **40 passed in 3.65s** after fixing the live-cursor regression. The initial new-file run found that authorizer reset alone did not prevent further stepping of an active RETURNING cursor; final cursor tracking/closure was tested by rerunning this affected file.
- Existing prototype file, selected affected cases only: **13 passed, 21 deselected in 1.86s** (append/source preservation, replay/rollback, revision staging, closed connections, ATTACH/temp and privileged base-authorizer escape).
- Five subsequently added outcome-revocation/cursor-factory cases: **5 passed in 1.50s**, run alone.
- One subsequently added native-AUTOINCREMENT compatibility case: **1 passed in 1.14s**, run alone.

Total: **59 distinct focused cases passed**; no unrelated old suite rerun. Bundled Python 3.12/repository pytest dependencies, bytecode/plugin autoload/cache disabled. Final syntax/whitespace and preservation checks are read-only.

## Protection boundary and remaining limits

The protected surface is ordinary SQL through **factory-issued connections/cursors while the installed guard is intact**. The instance `.set_authorizer` API is blocked, but Python permits explicitly calling `sqlite3.Connection.set_authorizer(connection, None)` or installing an allow-all callback through the base descriptor. Tests demonstrate that both bypass default denial and change the clone. Arbitrary private-state access, base-C method calls bypassing cursor tracking, hostile callbacks and filesystem privileges are not contained. This is not an unforgeable security boundary against a privileged caller.

A new SQLite connection to the same file has no authorizer; a focused control demonstrates its INSERT succeeds. Existing path/unmanaged-raw overloads, D4B1, Update/Backfill, direct save, maintenance and manual lower-level targets are not closed by this change. Native non-SQL database mutation APIs are not certified by this SQL-authorizer layer. The physical-target limitations from the earlier review still apply; no activation marker, persistent database UUID, DB trigger fence or OS access restriction is installed.

Admission decides candidate validity; the scope limits SQL connection/transaction/action/table, not the truth of source claims or every bound value against hostile replacement of internal helpers. Retained normalized/raw provenance semantics, tolerance `1e-9`, CTR historical availability, policy and old receipts are unchanged. Operational rows remain `research_eligible=false`; real ten-session shadow and production/research readiness are not established.

## Files and preservation

- Continued modification: `quantlab/transactional_market_data.py`.
- Updated within prototype scope: `tests/test_disposable_shadow_target.py` (explicit base-descriptor escape tests).
- Added: `tests/test_shadow_admission_dml_scope.py` and this report.
- Previous capability report, migration plan and 18 outside artifacts remain byte-identical. No stage/commit/push.

Canonical `data/market.db` SHA-256 before/after (file hashing only):

`f771b314ef2094373c3753def45223dd6e61aec84e15a369fcd52da585265fb6`

## Exactly one next step

Add a thin **clone-only controlled shadow entrypoint** requiring a factory handle/capability and rejecting path/unmanaged-raw arguments without fallback, delegating to the existing writer. Focus its clone tests on refusing unmanaged callers and preserving accepted/rejected outcomes. Keep legacy APIs explicitly separate and production untouched; this reduces accidental legacy routing and does not claim to contain privileged base-C access.
