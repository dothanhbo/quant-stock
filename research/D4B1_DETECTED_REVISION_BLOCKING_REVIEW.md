# D4B1 detected historical revision blocking

Date: 2026-10-04. Pre-change HEAD: `222abeada3069de6e73974e36332b03dd52a9415`, branch `main`, clean tracked worktree/index and 18 outside untracked artifacts. Scope: exactly one bypass, detected historical revisions in `commit_price_batch()`. This does not make D4B1 full operational admission or enable production.

## Before / after

Before, D4B1 accepted any D4A PASS and called `_upsert_prices()` and `_link_prices()`. Exact provider-correction or corporate-action-restatement attribution could therefore update historical values/provenance and write a new date in the same batch. Unattributed revisions already received Guard BLOCK.

After, any nonempty `guard_result.revisions` rejects the entire new batch before either SQL helper, regardless of attribution or Guard PASS. D4A result/reasons and numeric detection (`rel_tol=abs_tol=1e-9`) stay unchanged. Result is `ReceiptStatus.REJECTED`, `rows_written=0`, `revision_review_required=true`; the original six result fields remain compatible and the appended flag defaults false.

`historical_revision_review()` shares the existing denial-only D4B2 classification. D4B2 keeps its prior completion/coverage checks and decision ordering. The helper itself performs no positive admission decision.

## Durable review without automatic migration

Every new D4B1 revision rejection retains a `revision_review` object in the receipt's guard-result JSON: the review decision, false research eligibility, full normalized candidate rows (including the blocked new day), exact submitted revision claims/references, and provider source references. The original guard revisions retain exact detected old/new values, even when no attribution was supplied. The batch fingerprint continues to bind its submitted metadata. D4A Guard PASS is retained honestly beside a REJECTED receipt.

When all existing D4B2 receipt/staging columns are present, the same transaction attaches operational review fields and calls the existing `_insert_staging_candidate()`: one complete staged candidate with exact claims/references and `research_eligible=false`. Otherwise the base schema uses receipt-only review, explicitly OPERATIONAL_REJECTED with `D4B1_RECEIPT_ONLY_REVISION_REVIEW` and `D4B1_REVISION_ONLY_NOT_FULL_OPERATIONAL_ADMISSION` limitations. No table/column/migration is created by the writer; a future migration does not promote or automatically stage the earlier receipt.

Receipt, optional staging and review fields remain inside the existing `BEGIN IMMEDIATE`; either the transaction commits the rejection evidence or rollback removes all new evidence. Prices, row IDs, manifests, old receipts and provenance remain unchanged on rejection.

## Replay and caller compatibility

Read-only search of application/research call sites found D4B1 callers only in `tests/test_transactional_market_data.py`; production update/backfill still use `save_price_data()`. Existing callers' result type and first six constructor fields are unchanged. Callers must use `status`/`rows_written`/`idempotent_replay` for write outcomes, not Guard PASS alone. The optional `revision_review_required` flag replays from the new marker; old marker-free receipts retain their existing result and default false flag.

The directly affected cross-writer reader `_existing_shadow_receipt()` now recognizes new receipt-only review metadata. A D4B1 REJECTED/PASS receipt cannot be mapped to `GUARD_PASS_APPROVED` on shadow replay. It remains rejected if no staging existed at rejection, or revision-staged if the original transaction actually staged it. Replay creates no receipt/staging duplicates, writes, admission or promotion.

Older receipts are never updated or requalified by this change. A synthetic old-format APPROVED/PASS revision receipt confirms replay retains the original status/count and bytes, with zero new price/provenance SQL attempts. Its returned rows_written is the historical receipt count, not a count of fresh writes. This does not certify an old approval or its underlying market state.

## Focused verification

First selection: **33 passed in 1.60s**:

- `tests/test_transactional_market_data.py`: 13 existing cases plus 14 new cases. New cases cover provider correction, corporate-action restatement and missing attribution on base and migrated schemas; immutable prices/provenance/old receipts; deterministic replay; unchanged non-revision upsert behavior; cross-writer replay; failure after receipt and after optional staging.
- `tests/test_operational_market_data_shadow.py::test_attributed_guard_pass_revision_stages_entire_batch_without_append`: four cases affected by the shared classification helper.
- `tests/test_operational_market_data_shadow.py::test_historical_revision_is_rejected`: one shared-helper rejection case.
- `tests/test_completed_session_operation_identity.py::test_linked_revision_observation_does_not_authorize_price_rewrite`: one directly affected shadow replay case.

One subsequently added old-format approved-revision receipt replay regression: **1 passed in 0.18s**; only that new case was executed. Total distinct focused cases passing: **34**. No full suite, prior rehearsal, CTR checker, provider fetch or expensive audit was run.

For rejected, replayed and faulted cases, SQLite authorizers deny/record attempted INSERT/UPDATE/DELETE on both `prices` and `market_price_provenance`: **zero attempts**. All copied preexisting table rows remain unchanged, including original price IDs and stored provenance. Faults fire after actual receipt/staging mutations; rollback matches every pre-fault table and closes the transaction. Retry produces one durable rejection. Non-revision control retains the old three-row upsert/link count and does not acquire a full D4B2 decision.

All executed SQL used synthetic SQLite fixtures under new disposable visualization test directories. Canonical database was not opened as SQLite; file SHA-256 before/after is:

`f771b314ef2094373c3753def45223dd6e61aec84e15a369fcd52da585265fb6`

The 18 outside artifacts remain byte-identical. No Update/Daily/Scan, paper/Forward/Telegram/broker, backtest, WFO or production activation. Bundled Python 3.12 with repository pytest dependencies was used; bytecode, plugin autoload and cache were disabled.

## Remaining boundaries

- D4B1 non-revision batches still use the existing Guard PASS/upsert contract, without required completed-session or lifecycle admission. Do not route production to this API as a complete D4 gate.
- Sub-tolerance changes and revisions outside returned overlap remain undetected; tolerance, provider watch, missing-anchor handling and basis/version pinning were not changed.
- Production update/backfill/direct `save_price_data()`, cleanup rebuild and quarantine deletion remain bypasses. SQL helpers and explicit writer targets have no production capability/activation barrier.
- Historical incident exclusion, real source/calendar/completion coverage and research lineage remain unresolved. No old history, receipt or artifact is repaired or promoted.
- Receipt-only review on an unmigrated schema is durable evidence, not a row in the staging table. Consumers and older writer-capable builds need the reviewed rejection semantics before any rollout; no compatible production rollback is certified here.

Files in this separate checkpoint: `quantlab/operational_admission.py`, `quantlab/transactional_market_data.py`, `tests/test_transactional_market_data.py`, and this report. No policy, detector, canonical data, CTR packet or outside artifact is changed. No push.
