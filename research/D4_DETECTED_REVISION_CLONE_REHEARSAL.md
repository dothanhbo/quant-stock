# D4 detected-revision staging: canonical-backup clone rehearsal

Date: 2026-10-04 (Asia/Bangkok). Result: **PASS for synthetic rehearsal only**. Production, real shadow and research qualification remain BLOCKED.

## Review and preserved workspace

Reviewed the three existing diffs, `D4_DETECTED_REVISION_STAGING_REVIEW.md`, `DATA_INTEGRITY_POLICY_DECISION.md` and `D4B2_SHADOW_ACTIVATION_PLAN.md`, together with admission, receipt replay, allocator, staging and rollback paths. No direct invariant defect was found, so the existing three modified files and prior review were left byte-identical.

Initial/final branch `main`, HEAD `ee44eb867aebacdb6ae90d2a7502179fc8796afb`; index empty. All 22 existing changed/untracked files were verified byte-identical: the three modified files, prior review and 18 user artifacts. No stage, commit, push or activation.

New repository files this turn:

- `research/rehearse_d4_detected_revision_clone.py`: explicit-source, new-output-directory-only synthetic rehearsal using the existing D4B2 writer and allocator.
- `research/D4_DETECTED_REVISION_CLONE_REHEARSAL.md`: this report.

## Backup and input boundary

Canonical `data/market.db` was opened with URI `mode=ro` and `PRAGMA query_only=ON`, then copied through `Connection.backup()` into a new disposable SQLite file. Source and destination connections were distinct; the source was closed explicitly. No filesystem DB copy was used. Clone row count and `quick_check` were verified before mutations. Only the clone received additive D4 migrations and synthetic fixtures.

Canonical SHA-256 **before, after backup and after all rehearsal mutations**:

```text
f771b314ef2094373c3753def45223dd6e61aec84e15a369fcd52da585265fb6
```

The fixture symbol `ZZD4TEST` was confirmed absent in the canonical-derived clone. Two synthetic seed rows, 2026-09-01/02, were attributed only on the clone. Their manufactured RAW/unit/calendar/lifecycle/publication evidence is explicitly `fixture://` evidence with provider `SYNTHETIC_D4_REHEARSAL`; it does not qualify any real row. No original price row was used as a synthetic verified anchor or assigned invented provenance.

A appends 2026-09-03 with close 102.5. B changes that exact accepted row's close to 102.6 and also includes the genuinely new 2026-09-04 row. Both changes exceed the unchanged guard tolerance. The revision claim references A's ingestion operation; A's existing provenance link remains unchanged. Fault staging input uses close 102.7; fault append input uses the unchanged 102.5. Inputs are deterministic and retained in `synthetic_inputs.json`.

Important linkage distinction: request identity includes requested range and completed target date. B with a new date therefore has a different request identity from A and honestly has `revision_of=null`. Its exact claim/source reference links the corrected row to A. A separate same-request revision observation checks the allocator's real `revision_of=A`, generation 1; it has no additional new date. No cross-request linkage was fabricated.

## A-E results

| Step | Result and evidence |
|---|---|
| A: valid append without revision | Guard PASS; `NOT_REQUIRED_GUARD_PASS`; APPROVED, one price row and provenance link inserted. Synthetic-only, `research_eligible=false`. |
| B same-request linkage check | Guard PASS; generation 1, `revision_of` names A; `HISTORICAL_REVISION_REQUIRES_REVIEW`, zero price writes. |
| B: correction to A plus new day | Guard PASS with complete synthetic attribution; REJECTED/staged, zero price writes for the entire batch. |
| C: verify retained evidence | Receipt retains exact old/new 102.5/102.6; staging retains exact claim, both source references and all four candidate rows. Prices, provenance and manifests unchanged; September 4 absent; staging and decision research eligibility false. |
| D: identical B replay | Same operation ID and rejected/staged outcome, `idempotent_replay=true`; all seven table count/content hashes unchanged. No duplicate receipt, staging record or observation; no promotion. |
| E1: fault after rejection/staging | Exception at `after_rejection_or_staging`, after receipt/staging/generation exist inside transaction. After rollback all seven tables match their pre-fault count/content hashes and no transaction remains open. |
| E2: fault after actual append/link insertion | Exception at `after_prices_and_links`, after one price, one provenance link, manifest, receipt and generation have actually been inserted inside transaction. Rollback restores all seven tables, including prices and provenance. |

For B/C, D and E1, SQLite authorizer denies and records any INSERT/UPDATE/DELETE attempt on `prices` or `market_price_provenance`. The completed checks recorded **zero attempts**, in addition to count/content equality. E2 intentionally permits clone-only writes so rollback verifies undo of actual mutations, not just unchanged rows on a rejection path.

| Table | Before B | After B | Inside E1 | Inside E2 | After either rollback |
|---|---:|---:|---:|---:|---:|
| prices | 189273 | 189273 | 189273 | 189274 | 189273 |
| market_price_provenance | 3 | 3 | 3 | 4 | 3 |
| market_ingestion_receipts | 3 | 4 | 5 | 5 | 4 |
| market_ingestion_manifests | 2 | 2 | 2 | 3 | 2 |
| market_ingestion_staging | 1 | 2 | 3 | 2 | 2 |
| market_operation_requests | 1 | 2 | 2 | 2 | 2 |
| market_operation_observations | 2 | 3 | 4 | 4 | 3 |

All **189270 original copied price rows**, including IDs, remain unchanged on the clone. Their normalized row-content SHA-256 is `fea09ac93b232058ea3689a0b2e5e7d63028ef022cc77d3c0beafd37a2c27daa`. This digest proves preservation, not market-data correctness. Final clone `quick_check=ok`, `foreign_key_check=[]`.

Only this new executable rehearsal and focused preservation checks were run. Prior 29 passing tests were not rerun; no production code was changed. Python 3.12.14 bundled runtime was used, with bytecode writes disabled. No provider fetch, Update/Daily/Scan, paper/Forward/Telegram/broker, backtest or WFO.

## Evidence artifacts

All paths below are in the permitted disposable visualization workspace, outside the canonical data directory:

- [Full result, per-table hashes and inside-transaction snapshots](C:/Users/hello/.codex/visualizations/2026/10/04/01a10720-75cf-7d50-9214-075861ee51ec/d4-clone-rehearsal-20261004-v1/rehearsal_summary.json)
- [Deterministic synthetic inputs and request identities](C:/Users/hello/.codex/visualizations/2026/10/04/01a10720-75cf-7d50-9214-075861ee51ec/d4-clone-rehearsal-20261004-v1/synthetic_inputs.json)
- [Existing workspace file hashes before/after](C:/Users/hello/.codex/visualizations/2026/10/04/01a10720-75cf-7d50-9214-075861ee51ec/d4-clone-rehearsal-20261004-v1/workspace_preservation.json)
- [Disposable SQLite clone](C:/Users/hello/.codex/visualizations/2026/10/04/01a10720-75cf-7d50-9214-075861ee51ec/d4-clone-rehearsal-20261004-v1/disposable_market.sqlite)

The script requires a fresh output directory and refuses an existing one. Reuse requires a new path; do not point any production consumer at this clone containing synthetic prices.

## Inside/outside the checked mechanism and remaining bypasses

| Boundary | Assessment |
|---|---|
| `commit_price_batch_shadow()` -> operational admission -> staging/replay/rollback | **Inside**: a detected revision prevents the approval branch and all price/provenance writes; entire batch is staged when completion/coverage permit that classification. Other missing-evidence paths still reject with zero price writes. |
| `evaluate_preupdate_market_data()` numeric comparison | **Inside upstream detection**, using relative and absolute tolerance `1e-9`. Staging only sees differences the guard reports. Small differences within tolerance and changes outside returned overlap remain outside the protection demonstrated here. Tolerance was neither changed nor used to claim literal exact-equality surveillance. Exact fixture claims match actual old/new values. |
| D4B1 `commit_price_batch()` -> `_upsert_prices()` | **Outside D4B2 admission**. Uses the same guard but can upsert an attributed PASS revision; not called by this rehearsal, not patched. |
| `scripts/update_data.py::update_symbol()` and `scripts/backfill_market_data.py::backfill_symbol()` | **Outside**: still call legacy `save_price_data()`. No wiring changed. |
| `core.database.save_price_data()` | **Outside**: direct `INSERT OR REPLACE` without D4 receipt/provenance boundary. |
| `core.database.cleanup_price_duplicates()` (including updater cleanup path) | **Outside**: deletes/rebuilds prices without the D4 maintenance boundary. |
| `scripts/quarantine_invalid_ohlc.py --apply` | **Outside**: quarantine/delete outside D4 transaction and can invalidate links. |
| Existing durable receipt replay | Original decisions remain immutable on replay. No retroactive receipt invalidation or promotion was added. Old approvals and pre-D4B2 fallback semantics are not certified by this new staged-receipt replay check. |

Authoritative lifecycle/security/calendar and provider-completion evidence, durable basis/version pin, revision watch/missing-anchor handling, incident window exclusion, production bypass closure and ten official shadow sessions remain BLOCKED. This is one clone rehearsal, not an official session and not production/research eligibility evidence.

## Exactly one next step

Qualify **one attributable lifecycle/security/venue snapshot for one intended real symbol-session**, retaining source references and content hash for the existing `SymbolSessionEvidence` integration boundary. If the authoritative snapshot cannot be obtained or does not establish tradability, keep that session UNRESOLVED; do not substitute synthetic status or CafeF parity. This next evidence step does not authorize provider fetch or production wiring in this turn.

## Subsequent offline checkpoint review

The completed rehearsal was reviewed without rerunning it. Byte-identical copies of `rehearsal_summary.json` and `synthetic_inputs.json` are checkpointed under `research/d4_detected_revision_rehearsal/`; absolute paths inside them identify the historical execution only. The disposable SQLite database and workspace-preservation/debug outputs stay outside Git. No production code or rehearsal input changed in this review. The earlier branch/index/no-commit statements above describe the rehearsal turn, before this checkpoint.
