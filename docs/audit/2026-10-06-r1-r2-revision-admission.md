# R1 + R2: revision admission guard and append-only observation log (revision 3)

Date: 2026-10-06. Scope: the V1 data-provenance decision, items R1 and R2 only.
Frozen decisions are not reopened: current `market.db` history stays
LEGACY_UNVERIFIED / DESCRIPTIVE_ONLY, KBS via `vnstock.api` stays the operational
source, nothing is rebuilt, nothing is repaired automatically.

Revision 2 answers the Sol 6.1 review (P1-1 .. P1-6, P2 A..E). The log schema is
now `market-observation-log.v2`; revision 1 was never committed or run against a
live database, so there is no migration.

## Problem

`save_price_data` used `INSERT OR REPLACE` over a 7-day overlap. A provider that
retroactively adjusts history rewrote stored rows silently (TPB, DGW, VHM),
producing mixed-basis seams with no record.

## Admission (R1)

One implementation, `core/market_admission.py::admit_price_batch`, reached only
through `core.database.save_price_data(df, *, context, symbol)` (`context` is
mandatory). `scripts/update_data.py` and `scripts/backfill_market_data.py` use it.

| Candidate vs stored | Result | `market.db` |
|---|---|---|
| overlap identical, new sessions | `ADMITTED_APPEND` | new sessions inserted (plain `INSERT`) |
| overlap identical, nothing new | `ADMITTED_NO_NEW_SESSIONS` | untouched |
| symbol has no stored history | `ADMITTED_INITIAL_LOAD` | rows inserted |
| any overlapping tuple differs | `BLOCKED_REVISION_CHANGED` | untouched |
| stored session missing / no stored session in window / empty | `BLOCKED_OVERLAP_INCOMPLETE` | untouched |
| candidate session not stored inside or before stored history | `BLOCKED_HISTORY_EXTENSION` | untouched |
| stored history fails baseline/lineage verification, or an open intent is ambiguous | `BLOCKED_DATASET_MISMATCH` | untouched |
| stored history cannot be read as one row per session | `BLOCKED_STORED_HISTORY_INVALID` | untouched |

`REJECTED_INVALID_CANDIDATE` (no observation is created, retryable): duplicate
session keys, invalid OHLCV (checked before **and after** 4-decimal rounding),
weekend sessions, sessions outside the requested range, symbol mismatch, missing
columns, unparseable or mixed time zones.

Time rule: tz-naive timestamps are exchange-local session dates; tz-aware ones
are converted to Vietnam time (UTC+7) before the date is taken. Completed-session
cutoff is 15:00 ICT on weekdays; later sessions are excluded and recorded. No
holiday calendar is applied (not required for V1).

## Persistent state model (P1-1, P1-3)

An observation's status is derived from its append-only events:

| Status | Meaning | Gates consumers? | Terminal? |
|---|---|---|---|
| `PENDING_OBSERVED` | observed, not yet decided | no (market untouched) | no |
| `PENDING_APPLICATION` | decision admitted / intent open | **yes** (market may be half-applied) | no |
| `APPLIED` | market change (or no-op) recorded | no | **absorbing**: never regresses |
| `BLOCKED_UNRESOLVED` | blocked, nothing resolved it | **yes** | no |
| `BLOCKED_RESOLVED` | resolved by a later accepted observation or a reviewed event | no | resolution is append-only |
| `SUPERSEDED` | replaced by a newer admission for the symbol | no | no: identical data can be re-evaluated |
| `FAILED` | application error / abandoned intent | no | no: retryable |

Rules:

* A retry of an `APPLIED` observation returns the existing receipt and appends
  only a `RETRY_RECEIPT` annotation (state stays `applied`). It never appends
  admitted/pending state and never advances the dataset version.
* A repeated identical retry of a still-unresolved block appends only a
  `RETRY_RECEIPT`; it never stacks a second block to resolve.
* A block stays unresolved until (a) a later accepted decision that compared and
  confirmed every affected session identical applies (kind
  `SUPERSEDED_BY_ACCEPTED_OBSERVATION`), (b) the same observation is later
  accepted (`REEVALUATED_ACCEPTED`), or (c) a reviewed resolution is recorded
  (`scripts/resolve_market_block.py resolve --block-id N --reviewer R --reason T`).
  Ambiguity / dataset-mismatch blocks are not auto-resolvable: only (c) clears them.
  Resolution never modifies `market.db` and does not accept the revised data.
* Historical resolved blocks are not active; they remain in the log.

### Consumer gate

`core/market_provenance_gate.py::check_market_provenance(db, symbols)` is the
single read-only decision (log opened read-only, never created; no log means
`NOT_APPLICABLE`). It fails closed on unresolved blocks and open intents for the
requested symbols, on baseline-slice / version-content / unattributed-row drift
(`verify_dataset`), and on a log bound to another dataset. Wired into:

| Consumer | Gate |
|---|---|
| Daily validation (`scripts/run_daily.py`), standalone scanner (`strategy/scanner.py`), paper lifecycle (`scripts/run_paper_lifecycle.py`) | through `check_market_data_integrity` for the required universe only (an unrelated symbol's block does not poison them) |
| Forward validation (`quantlab/forward/daily.py`) | `require_market_provenance(database)` before the snapshot is built (whole dataset) |
| `scripts/update_signal_results.py`, `scripts/update_paper_positions.py` | `require_market_provenance` before any state is touched (whole dataset) |

## Decision binding and finalization order (revision 3)

Every admission captures, **under the market write lock**, an immutable binding:
the `ADMISSION_DECISION` event id (a total order over the log), the symbol's
dataset version at that point (`bound_version_id`; reconcile has already settled
any earlier application) and the sessions the decision compared and found
identical (`confirmed_sessions`).

* **No-op** (`ADMITTED_NO_NEW_SESSIONS`, nothing written to `market.db`): the
  receipt, version binding and eligible block resolutions are finalized *inside*
  the same lock that made the decision, so no other admission can run in between.
* **Append**: the market commit must precede the log record, so the application
  is recorded after the lock with the same binding; ownership still comes from
  the open intent, and the append is refused if the version moved since the
  decision.
* `record_application` is conditional on that binding even when called late:
  it refuses any finalization (including a zero-session one) unless the
  observation is currently `admitted` for exactly that decision; for a zero-session
  application `decision_event_id` is **mandatory** and the current decision is
  never discovered implicitly; a no-op carries
  `bound_version_id` and binds to it, never to the latest version; the receipt
  content hash is the one captured at the decision.
* Automatic block resolution is bound to the accepting decision: only blocks
  recorded **before** that decision, auto-resolvable, whose affected sessions are
  all in `confirmed_sessions`, with no newer blocking decision on those
  sessions. Request-range containment is no longer used. Crash recovery
  (reconcile) resolves nothing automatically.

## Ownership and concurrency (P1-2)

Two SQLite files cannot share a transaction, so ownership never comes from value
equality:

1. The observation and a fetch receipt are durable first.
2. One `BEGIN IMMEDIATE` on `market.db` serializes, per process boundary:
   reconcile open intents -> verify the stored slice against baseline/lineage ->
   classify -> record the decision -> record an **application intent** (the
   authoritative claim on the sessions, durable before the insert) -> insert -> commit.
3. After the lock: `record_application` (idempotent) attributes the sessions. It
   only accepts sessions held by *this observation's own open intent*.

Recovery inspects intents only: rows present with the intended values -> the
intent's owner is recorded as applied (`reconciled`); rows absent -> intent
abandoned (`FAILED`); anything else -> non-auto-resolvable
`BLOCKED_DATASET_MISMATCH`. A competing observation that proposes the same
session with equal values therefore finds it stored, appends nothing and owns
nothing. `applied_sessions` has a UNIQUE `(symbol, session)`; a conflict is an
error, not a silent re-attribution.

## Observation identity and fetch receipts (P1-4, P2-E)

`obs-<sha256>` over: source, endpoint, source mode, **package name and version**,
symbol, interval, request window, completed-session cutoff, normalization
version and the canonical batch hash. Fetch time and run id are excluded. A
different package version is a distinct observation even for identical data.

Every fetch (including repeats) writes a row in `fetch_receipts` (run id, fetched
at, request window, cutoff, package version, first-occurrence flag); the events
for that fetch reference its `receipt_id`. `source_observations` keeps
`first_run_id` / `first_fetched_at_utc` and is never overwritten. On identity
reuse every immutable field plus rows and excluded sessions are validated; a
mismatch is an `ObservationLogError`.

## Dataset binding (P1-5)

The baseline row binds the log to one dataset: canonical locator (resolved,
case-normalized path), baseline file SHA-256, logical content SHA-256, per-symbol
content hashes, registration time. Every open verifies the locator; a different
database, a log path equal to the market database, a copied/moved database, or a
missing database is rejected. The default log name is derived from the database
file name (`<stem>_observations.db`), so two databases in one directory cannot
collide; an explicit shared log is rejected on the second database. Registering
the same database again is idempotent (`scripts/register_market_baseline.py`,
`--verify` is read-only). Moving or renaming the repository changes the locator
and fails closed until the baseline is re-registered.

## Maintenance (P1-6)

Disabled for V1 rather than versioned: `cleanup_price_duplicates()` is
analysis-only (`apply=True` raises `MaintenanceDisabledError`),
`update_data --cleanup-only` is a dry run, and `quarantine_invalid_ohlc.py
--apply` refuses (the dry run opens the database read-only). Legacy rows are
therefore immutable, which is what makes baseline-slice verification sound. A
test inventories every non-test module for price-table writes.

## Dataset version rules

`symbol_versions` chains per-symbol versions; `dataset_version_id` hashes the
baseline id and the latest version per symbol.

* Blocked and pending observations do not advance it.
* A successful append advances it exactly once (one new `symbol_versions` row).
* A retry of an applied observation, and a no-op application, do not advance it.
* Maintenance is disabled, so it cannot change data without a version.
* Per-symbol outcomes of one run are visible through `run_summary(run_id)`;
  `dataset_version_identity()` lists unresolved-block and pending symbols and a
  `consumable` flag.

`session_provenance(symbol, session)` returns `BASELINE_LEGACY`, `OBSERVATION`,
`PENDING_APPLICATION`, `UNATTRIBUTED`, `REMOVED` or `ABSENT`; an absent session
is never reported as legacy.

## Append-only protection: what is and is not claimed

Every log table rejects UPDATE, DELETE and `INSERT OR REPLACE` (a BEFORE INSERT
trigger aborts an insert that would collide, independent of
`recursive_triggers`). Production APIs expose no mutating method. A privileged
writer that drops the triggers or replaces the file is **not** prevented; the
baseline hash and per-symbol verification make such tampering of `market.db`
detectable, not the log file itself.

## Not done (by design)

R3 forward/paper quarantine, R4 rebaseline, corporate-action ledger, automatic
repair, historical constituents, second-source automation, raw response
archival, UI.

## Known limits

* A provider that keeps serving revised history (TPB-like) re-creates a block on
  every Daily run (a new window is a new observation) until R3/R4; a reviewed
  resolution applies to one block, not to future observations.
* A deleted observation-log file reverts the dataset to `NOT_APPLICABLE`.
* Forward evidence rows do not yet store a dataset version (R3).
