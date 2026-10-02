# D4B2 operational admission and shadow-integration plan

## Status

`OPERATIONAL_APPEND_ONLY_V1` is implemented for explicit temporary SQLite
targets only. Production update, backfill, cleanup and quarantine entrypoints do
not import the adapter or shadow writer. Canonical activation remains prohibited.

## Decision separation

The D4A `guard_decision` remains authoritative and unchanged. D4B2 records a
second `operational_admission` decision. An operational append may therefore
have:

- `guard_decision = INSUFFICIENT_EVIDENCE`
- `operational_admission = OPERATIONAL_APPEND_ONLY_ACCEPTED`
- `data_class = OPERATIONAL_ONLY`
- `research_eligible = false`
- `adjustment_mode = UNKNOWN`
- `corporate_action_verification = UNKNOWN`

This combination permits operational storage only. It is not a D4A PASS and is
not an ingestion manifest eligible for prospective research labels.

## Admission matrix

| Candidate condition | Operational outcome | Canonical-style price action in shadow |
|---|---|---|
| D4A PASS | `NOT_REQUIRED_GUARD_PASS` | D4B1 transaction controls the write |
| Incremental update; existing history; at least two exact overlap anchors; only `PRICE_BASIS_UNVERIFIED`; strictly newer rows | `OPERATIONAL_APPEND_ONLY_ACCEPTED` | Insert only genuinely new sessions |
| No existing history | `BOOTSTRAP_PENDING` | Stage candidate; no price write |
| Explicit backfill | `BACKFILL_REQUIRES_STAGING` | Stage candidate; no price write |
| Historical missing-session insertion | `HISTORICAL_GAP_REQUIRES_REVIEW` | Stage candidate; no price write |
| Revision, mixed adjustment, missing overlap, boundary discontinuity, unsafe identity, incomplete attribution, incomplete sessions, or any unexpected guard reason | `OPERATIONAL_REJECTED` | Receipt only; no price write |

The allowlisted D4A result contains exactly `IDENTICAL_OVERLAP`,
`PRICE_BASIS_UNVERIFIED`, and `WHOLE_HISTORY_NOT_CERTIFIED`. Any additional
reason fails closed.

## Transaction invariants

Within one `BEGIN IMMEDIATE` transaction the shadow writer:

1. Resolves operation-id replay/conflict.
2. Reads persisted prices and current provenance links.
3. Evaluates D4A without modification.
4. Evaluates `OPERATIONAL_APPEND_ONLY_V1` separately.
5. Stores both complete decisions in the receipt.
6. For operational admission, stores an honest normalized-only manifest and
   inserts and links only the sessions later than the persisted maximum.
7. For bootstrap/backfill/gap outcomes, stores a non-canonical staging record
   with no automatic promotion.
8. Rolls back receipt, manifest, staging, price and links on any pre-commit
   failure.

Unchanged overlap rows are comparison anchors only. They are neither updated nor
linked to the new manifest, so legacy provenance is not upgraded.

## Additive migration and rollback

The D4B2 migration requires the unique `prices(symbol,time)` index, adds nullable
operational columns to D4B1 receipts/manifests, creates a staging table and
records one migration identity. It is idempotent and validates foreign keys.

Rehearsal is performed on a byte-for-byte database clone. Rollback is restore of
the pre-migration backup, not a destructive down-migration. Old D4B1 code ignores
the nullable added columns, providing code rollback compatibility before any
D4B2 operational rows exist.

After operational rows exist, rollback must stop all writers and retain the
provenance tables. Re-enabling `save_price_data()` would permit unlinked
overwrites and is not a safe rollback.

## Raw provenance

The DataFrame adapter treats its input as normalized content. With no preserved
original HTTP payload, archive reference and `raw_payload_sha256` remain NULL.
No normalized DataFrame hash is copied into a raw-payload field.

## Production bypasses and required restrictions

- `scripts/update_data.py::update_symbol()` still calls `save_price_data()`.
- `scripts/backfill_market_data.py::backfill_symbol()` still calls
  `save_price_data()`.
- `core.database.save_price_data()` can overwrite linked rows without a receipt.
- `cleanup_price_duplicates()` deletes and rebuilds all prices; with provenance
  links it can fail foreign keys or leave stale links if enforcement is off.
- `quarantine_invalid_ohlc.py --apply` deletes price rows outside the ingestion
  transaction and can invalidate current provenance.

Before activation, update and backfill must migrate together or the legacy
writer must reject guarded symbols. Cleanup and quarantine require distinct,
audited maintenance transactions and must not emit ingestion manifests.

## D4B3 activation prerequisites

1. Review and approve the exact append-only allowlist and completed-session
   source.
2. Define production operation-id construction across scheduled and manual
   retries.
3. Add a reviewed schema migration command, backup verification and recovery
   runbook.
4. Shadow the real update universe on disposable canonical clones for multiple
   EOD cycles and reconcile decisions without provider-side writes.
5. Define Daily status handling for accepted, rejected, staged, conflict and
   lock-timeout outcomes.
6. Migrate `update_symbol()` and `backfill_symbol()` together while retaining
   distinct append, staging and bootstrap policies.
7. Restrict direct `save_price_data()` use and replace cleanup/quarantine with
   audited maintenance boundaries.
8. Decide whether production operates with honest NULL raw provenance or add an
   archive-first adapter with orphan recovery.
9. Demonstrate rollback after some operational-only rows exist without allowing
   the legacy writer to overwrite them.

Phase 7 and research-label eligibility remain unchanged and blocked by their own
evidence requirements.

## D4B2.5 readiness decision

The D4B2 implementation is checkpoint-ready, but production shadow activation
is `DESIGN_ONLY`. The caller-supplied `completed_through` value is not yet an
authoritative completed-session determination. Production shadow observation
must not begin until the session source, provider-publication rule and durable
operation-identity fields below are implemented and fixture-tested.

Operational write integrity and prospective research-label eligibility remain
separate decisions. An operational append can preserve existing rows and add a
new completed session while remaining permanently ineligible for prospective
research labels. No operational outcome may manufacture adjustment,
corporate-action, raw-payload or whole-history verification evidence.

## Completed-session contract for Vietnamese equities

A session is eligible for operational admission only when all of the following
are true:

1. Session membership comes from a versioned Vietnam exchange calendar covering
   the symbol's venue (HOSE, HNX or UPCoM), including published exceptional
   closures. Weekends are excluded by that calendar, not by the ingestion host's
   date alone.
2. The calendar snapshot identifies the session as scheduled and closed. The
   local VNINDEX price table is a corroborating observation only. In particular,
   the four known missing local VNINDEX dates remain genuine exchange sessions
   when the authoritative calendar says they were open.
3. The provider has published a complete observation for the candidate session.
   This requires a provider completion watermark or two identical observations
   separated by a reviewed publication-delay interval. A response produced
   merely after local market close is insufficient.
4. The response contains the candidate session for the requested symbol and its
   required market-calendar corroborator. A missing, partial, duplicated or
   still-changing current-session row is not complete.
5. The receipt stores the calendar source and snapshot identity, venue, resolved
   completed-through session, provider observation timestamps and payload
   fingerprints used to make the determination.

On a weekend or holiday, the candidate is the most recent prior session that
satisfies all five conditions. Calendar absence, disagreement, an unpublished
provider session, or missing corroboration produces
`COMPLETED_SESSION_UNRESOLVED` and no price write. It must not be converted to a
successful stale-data update.

The exact authoritative calendar feed/snapshot process and the provider
completion watermark (or the duration and mechanics of the two-observation
fallback) are unresolved activation decisions. Wall-clock time, weekday logic,
and local VNINDEX gaps are explicitly prohibited as sole authority.

## Bounded historical revision watch

The two-session rule remains the minimum admission anchor, not the complete
revision-surveillance policy. The minimum practical production proposal is:

- Each ordinary update makes its existing per-symbol provider request cover the
  trailing 120 calendar days. Every returned date that overlaps persisted data
  is compared exactly; the two latest completed overlapping sessions must be
  present and unchanged.
- A deterministic daily cohort (for example, five percent of symbols selected
  by a stable hash of symbol and completed session) expands the same request to
  a reviewed longer horizon. This changes payload size rather than request
  count. Over a 20-session rotation every symbol receives the longer check.
- The longer check records exact prior and candidate values at deterministic
  month-end and quarter-end exchange sessions, the boundary before the ordinary
  window, and any retained incident or corporate-action-adjacent anchors.
- Symbols with unresolved D1/D2 discontinuities or a detected corporate-action
  signal remain on an expanded/manual-review list rather than being silently
  repaired.
- Request pacing retains the configured Community API rate limit and jitter.
  If a provider cannot serve the wider range in the same request, that cohort is
  deferred; the system must not multiply requests past the reviewed quota.

Any changed historical anchor, missing expected anchor, scale-like price change,
boundary discontinuity, or inconsistent adjustment behavior yields
`HISTORICAL_REVISION_REQUIRES_REVIEW`, stores both exact values and fingerprints,
and performs no canonical write. Full-history comparison is an event-triggered
or offline audit, not a daily repair mechanism. Unchanged bounded anchors are
evidence only for those dates and never certify the rest of history. Provider
data can omit or mis-date corporate actions, and price-only anchors cannot prove
the cause or adjustment basis; those limitations require manual evidence review.

The 120-day window, longer horizon, rotation percentage and retained anchor set
must be approved from measured provider behavior before activation.

## Deterministic production operation identity

Production uses canonical JSON with sorted keys, UTF-8 encoding, ISO session
dates, normalized provider/package strings and full SHA-256 digests. Two related
identities are required:

- `request_identity` hashes schema version, environment/database identity,
  entrypoint namespace (`daily_update`, `manual_update`, `backfill_stage` or
  `bootstrap_stage`), symbol, provider, endpoint, package version, requested
  session range, completed-session/calendar snapshot and policy version.
- `operation_id` hashes `market-data-operation/v1`, `request_identity`, the
  normalized payload fingerprint and a durable observation generation.

Update, backfill, bootstrap and staged operations therefore cannot collide even
for the same symbol and range. A retry of the same observation reuses the stored
request identity, generation and payload fingerprint and therefore replays the
same operation ID. If refetching under the same request identity produces a
different payload, it is not a retry: the old receipt is retained, a new
generation and operation ID are created with `revision_of`, and historical
differences are staged for review. An attempt to reuse an operation ID with any
different identity component yields `OPERATION_IDENTITY_CONFLICT` and no write.
Neither timestamps nor random UUIDs are the primary idempotency key.

The observation-generation allocator and `revision_of` link are not yet present
in D4B2 and are required before production shadowing.

## Runtime status mapping

The Daily/update compatibility boundary must preserve the existing summary
interface while returning a structured status and reason for every symbol:

| Transaction outcome | Runtime classification | Daily behavior |
|---|---|---|
| `OPERATIONAL_APPEND_ONLY_ACCEPTED` | success, operational-only | Count success; expose rows appended and receipt ID |
| `GUARD_PASS` | success, guard-qualified | Count success; expose guard receipt ID |
| `BLOCKED` | hard data-integrity failure | No write; report issue; stop dependent stages when data errors are fatal |
| `INSUFFICIENT_EVIDENCE` | evidence failure | No write; report issue; no blind network retry |
| `BACKFILL_REQUIRES_STAGING` | staged review required | No canonical write; report staged receipt, not success |
| `BOOTSTRAP_PENDING` | bootstrap review required | No canonical write; report pending, not success |
| `HISTORICAL_GAP_REQUIRES_REVIEW` | manual review required | No canonical write; report issue |
| `OPERATION_IDENTITY_CONFLICT` | hard idempotency failure | No write; page/stop for operator review |
| `DATABASE_LOCK_TIMEOUT` | transient infrastructure failure | Bounded jittered retry; report failure after exhaustion |

`COMPLETED_SESSION_UNRESOLVED` is a deferred-not-ready outcome, not success and
not `INSUFFICIENT_EVIDENCE`; update-dependent Daily stages must not proceed on the
assumption that the current EOD session was ingested. Transport errors retain
bounded retry behavior, but structural evidence failures do not.

## Legacy bypass closure

Before any canonical operational write:

1. `update_symbol()` and `backfill_symbol()` must move in the same release to the
   transactional boundary. Update may append under the reviewed policy;
   backfill and bootstrap must remain staging-only until separately approved.
2. `save_price_data()` must reject writes once a durable database activation
   marker exists unless called through an unforgeable in-process transactional
   capability. A feature rollback may choose guarded-write or no-write shadow
   mode, never the legacy writer.
3. `cleanup_price_duplicates()` must refuse to operate after activation. Its
   replacement must be an audited maintenance transaction that preserves or
   explicitly rebinds provenance and verifies foreign keys before commit.
4. `quarantine_invalid_ohlc.py --apply` must refuse linked/activated price rows
   unless an approved maintenance transaction atomically records the quarantine
   and updates provenance. Dry-run inspection can remain read-only.
5. Every managed SQLite connection must enable foreign-key enforcement. Direct
   ad-hoc database access remains operationally prohibited; application checks
   alone do not protect a file opened by arbitrary SQLite tooling.

The rollback build itself must understand the activation marker and refuse the
legacy writer. Deploying the pre-D4 binary after operational rows exist is not a
safe rollback.

## Production activation runbook

1. **Canonical preflight.** Stop and verify all writers, resolve the exact
   canonical path, record its SHA-256 and size, inspect journal mode and active
   connections, run `PRAGMA quick_check`, `PRAGMA integrity_check`, foreign-key
   checks, schema/index checks and duplicate-key checks, and verify free disk.
2. **Consistent backup.** Use the SQLite backup API or reviewed `VACUUM INTO`
   while write exclusion is held. A filesystem copy of an active database is
   not accepted. Open the backup independently; repeat integrity/foreign-key and
   critical row-count checks and record its SHA-256 before migration.
3. **Additive migration.** Under `BEGIN IMMEDIATE`, apply the idempotent schema,
   indexes and activation marker; verify the migration identity and invariants;
   commit only when all checks pass.
4. **Disposable-clone rehearsal.** Restore the verified backup to a new path,
   rehearse migration twice, idempotent replay, conflict, lock timeout, injected
   rollback and backup restoration. Never point rehearsal code at the canonical
   path.
5. **Real EOD shadow observation.** Use real provider reads only after separate
   approval, write solely to disposable clones, resolve sessions with the
   approved calendar/publication contract, preserve request/payload evidence,
   and reconcile every symbol's guard, admission and runtime status. Record and
   compare the canonical database hash before and after every cycle.
6. **Recovery after transactional writes.** Stop writers and take a new
   consistent evidence backup. Prefer forward repair. Restoring the pre-activation
   backup is allowed only with an explicit decision to discard all subsequent
   price rows and their receipts together; never splice price and provenance
   tables from different points in time.
7. **Rollback compatibility.** Roll back only to a compatibility build that
   recognizes the activation marker, keeps `save_price_data()`, cleanup and
   quarantine apply disabled, and can read the additive columns. Retain the D4
   tables and receipts even when writes are placed in no-write shadow mode.

## Minimum real-shadow acceptance criteria

D4B3 requires at least ten consecutive official completed sessions across a
weekend or exchange non-session boundary on disposable canonical clones. Every
configured symbol must have a classified outcome; no result may be silently
dropped or translated to success. Repeating an identical observation must be
idempotent, and a changed payload under the same request identity must create a
linked revision observation rather than overwrite a receipt.

Across the observation window there must be zero historical price updates,
zero automatic staged-data promotions, zero research-eligible operational rows,
no foreign-key/integrity failures, and no unexplained identity conflicts. The
exercise must include controlled fixtures for bootstrap, backfill, historical
revision, incomplete publication, lock timeout and rollback after accepted
operational rows. The canonical database SHA-256 must remain unchanged after
every shadow cycle.

Until the unresolved session authority, identity generation, status wiring and
bypass closures are implemented and these criteria pass, D4B3 is not ready for
production shadow activation.
