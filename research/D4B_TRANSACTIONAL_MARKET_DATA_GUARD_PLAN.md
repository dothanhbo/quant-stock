# D4B transactional market-data guard integration plan

## D4A boundary

`quantlab.preupdate_market_data_guard` is a pure decision contract. It is not
called by the production update or backfill paths. A guard decision made before
`save_price_data()` would be vulnerable to a check-then-write race, and a
manifest written after `save_price_data()` would not be atomic with the price
rows. D4A therefore does not claim active production protection.

## Active mutation inventory

- Daily: `scripts/run_daily.py` -> `scripts.update_data.update_all_symbols()` ->
  `update_symbol()` -> KBS `Quote.history()` -> `core.database.save_price_data()`.
- Direct incremental update: `scripts/update_data.py` follows the same per-symbol
  path.
- Backfill: `scripts/backfill_market_data.py` -> `backfill_symbol()` -> KBS
  `Quote.history()` -> `core.database.save_price_data()`.
- Explicit maintenance: `cleanup_price_duplicates()` deletes and rebuilds the
  `prices` table; `scripts/quarantine_invalid_ohlc.py --apply` copies invalid
  rows to quarantine and deletes them. These are historical mutations even
  though they do not ingest provider candidates.

`save_price_data()` currently normalizes before opening a connection, then uses
`INSERT OR REPLACE` and commits. It does not read persisted rows in the same
transaction, accept price-basis/coverage evidence, or persist an ingestion
decision/manifest. `INSERT OR REPLACE` also expresses replacement as a delete
plus insert in SQLite, which is a poor basis for stable row identity and audit
linkage.

## Smallest safe production design

1. Introduce a `PreparedPriceBatch` boundary containing immutable normalized
   rows, symbol, requested range, coverage metadata, verified price-basis
   metadata, raw/normalized content hashes, provider/package identity, retrieval
   time, and attributable event/revision evidence. Preparation performs no
   database writes.
2. Replace the public ingestion use of `save_price_data(df)` with a single
   `commit_price_batch(prepared_batch)` operation. Incremental update and
   backfill must both use it; keep the old function private or make it reject
   production use once migration is complete.
3. On one database connection, start a SQLite write transaction that acquires
   the write reservation before comparison (`BEGIN IMMEDIATE`, with an explicit
   bounded busy timeout). Read the symbol's matching sessions plus the latest
   preceding anchor inside that transaction. This removes the check-then-write
   window between snapshot and mutation.
4. Run the pure D4A guard against that in-transaction snapshot. For `BLOCK` or
   `INSUFFICIENT_EVIDENCE`, insert a reason-coded `market_ingestion_attempt`
   record and commit the transaction without changing `prices`. The rejection
   is therefore durable and the affected price batch remains untouched.
5. For `PASS`, insert the attempt, the ingestion manifest/evidence references,
   and the normalized batch identity, then write all rows in the same
   transaction. Use `INSERT ... ON CONFLICT(symbol,time) DO UPDATE SET ...`
   rather than `INSERT OR REPLACE`. Link every affected row (or a normalized
   batch/session association) to the manifest within that same commit. Any
   failure rolls back prices and provenance together.
6. Bind retry identity to the normalized content hash plus symbol, requested
   range, provider identity, and normalization fingerprint. Repeating an
   already committed identity returns the prior committed result without a
   second mutation; repeating a rejected identity returns the durable rejection.
7. Map `BLOCK` and `INSUFFICIENT_EVIDENCE` to explicit updater/backfill statuses;
   neither may be counted as success. Preserve the existing retryable transport
   status separately from deterministic guard rejection.
8. Define a bootstrap policy before wiring backfill. With no persisted anchor,
   D4A correctly returns `INSUFFICIENT_EVIDENCE`; D4B must not silently bypass
   it. The minimal safe option is a separate, explicit bootstrap/quarantine
   workflow that requires verified price-basis and coverage evidence and does
   not promote legacy rows.
9. Put maintenance mutations behind audited operations. Duplicate cleanup must
   prove value-preserving canonicalization before one atomic rewrite; quarantine
   must record exact removed-row hashes and reasons in the same transaction.
   Neither path may create ingestion manifests or upgrade `LEGACY_UNVERIFIED`.
10. Add transaction-level tests with a temporary synthetic SQLite database for
    concurrent-writer serialization, rollback on every non-PASS decision,
    manifest/row atomicity, update and backfill coverage, maintenance audit, and
    idempotent retry. Do not validate this design against the canonical database.

## Activation criterion

Production protection is active only after every provider ingestion entry point
uses `commit_price_batch`, direct calls to the legacy writer are prevented, the
maintenance paths have explicit audited semantics, and transaction-level tests
prove that no non-PASS candidate can alter persisted prices.

## D4B1 foundation implemented (not activated)

`quantlab.transactional_market_data` now provides an isolated
`PreparedPriceBatch`, D4B1 schema initializer, and `commit_price_batch()` for an
explicit SQLite connection or path. No production module imports it.

The transaction invariants are:

- `BEGIN IMMEDIATE` precedes the receipt lookup, persisted-anchor read, guard
  evaluation, or mutation.
- An operation id may identify exactly one deterministic batch fingerprint.
  An identical retry returns its stored result; a different fingerprint raises
  an explicit conflict inside the reserved transaction.
- A non-PASS guard decision commits one durable reason-coded rejection receipt
  and writes no manifest, price, or price-provenance link.
- A PASS writes its receipt, manifest, complete price batch, and current-row
  provenance links in one transaction. Any exception before commit rolls all of
  them back.
- An existing price is treated as verified only when its current provenance
  link resolves to a manifest whose source, price unit, and adjustment basis are
  all explicitly verified. Rows without that link remain legacy/unverified.
- A claimed adjustment basis with unknown verification state is retained in the
  batch fingerprint but is presented to D4A as unknown. It cannot self-certify.
- A normalized candidate hash is always identified as normalized content. It is
  never stored as a raw-payload hash. `raw_payload_sha256` is populated only
  when an archive identity explicitly describes original provider payload.

PASS remains a batch-integrity/write decision. It does not assert prospective
label eligibility and does not retrofit verification onto historical rows.

## Archive and SQLite crash consistency

Filesystem archives and SQLite are not one atomic commit. A production archive
adapter must use this order:

1. Write provider bytes to a same-filesystem temporary archive object without
   labelling a normalized DataFrame as the raw response.
2. Flush the file and containing directory as supported, calculate SHA-256 from
   the staged bytes, verify it, and atomically rename to a content-addressed,
   immutable final name.
3. Only then construct `ImmutableArchiveIdentity` and `PreparedPriceBatch` and
   call `commit_price_batch()` using the verified final reference and hash.
4. If SQLite fails or rejects the candidate, the archive is an intentional
   orphan, not evidence of a committed ingestion. A retry with the same
   operation id and fingerprint reuses that exact archive identity.
5. An orphan collector may remove only archives absent from committed manifests
   and older than a configured retention period. Unknown, partially staged, or
   hash-mismatched objects are quarantined rather than attached or deleted.

SQLite never attempts to roll back, rename, or delete an external archive. A
manifest is authoritative only after its SQLite transaction commits.

## Minimum D4B2 activation gate

1. Add an archive adapter that implements and tests the archive-first protocol,
   or explicitly operate without a raw archive while leaving raw provenance
   null.
2. Add a reviewed schema migration and backup/restore rehearsal for the D4B1
   tables; do not initialize them opportunistically during ingestion.
3. Build one DataFrame-to-`PreparedPriceBatch` adapter that records the
   normalized DataFrame as normalized content only and leaves unsupported KBS
   source/adjustment claims unknown.
4. Migrate `update_symbol()` and `backfill_symbol()` together to that adapter and
   transactional writer, with explicit statuses for BLOCK,
   INSUFFICIENT_EVIDENCE, operation conflict, and retryable transport failure.
5. Disable direct production use of `save_price_data()` only after both paths
   migrate. Until then it remains a known bypass.
6. Put `cleanup_price_duplicates()` and `quarantine_invalid_ohlc.py --apply`
   behind separate audited, atomic maintenance contracts. They must never emit
   ingestion manifests or upgrade legacy provenance.
7. Run temporary-clone migration/rollback and concurrent-writer tests before any
   canonical-database activation. Canonical history stays `LEGACY_UNVERIFIED`.
