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
