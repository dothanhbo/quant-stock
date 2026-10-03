# Data integrity policy decision

Decision status: `DESIGN_ONLY`; production activation remains `BLOCKED`.

Review date: 2026-10-03 (Asia/Bangkok). Canonical `data/market.db` SHA256 at review start: `F771B314EF2094373C3753DEF45223DD6E61AEC84E15A369FCD52DA585265FB6`.

## Decision summary

VHM and CTR expose related but distinct failure modes:

- **Price basis:** VHM switches from RAW-equivalent to ADJUSTED-equivalent values across consecutive actual sessions. CTR combines adjusted-equivalent actual-session rows with raw-equivalent rows during a venue-transfer non-trading gap. A batch may therefore be internally valid OHLCV while belonging to the wrong series basis.
- **Session validity:** CTR proves that a venue being open is insufficient. A symbol must also be actively tradable on that venue for the specific date. Listing, delisting, transfer, and suspension state are part of session evidence. Forward-filled OHLCV is never an observed session.
- **Historical revision:** Providers can revise prior values or apply a new adjustment basis beyond the short daily overlap. Unchanged recent anchors do not certify older history. Changed history requires exact attribution and review; it must not be partially rewritten automatically.

Large returns remain diagnostic only. They do not prove a corporate action or corruption.

## General policy requirements

### A. Price basis

1. Every admitted series must have a stable identity containing provider, endpoint, symbol/security identity, venue, price unit, adjustment basis, adjustment-method/version identity, and attributable evidence.
2. RAW, ADJUSTED, and UNKNOWN series must never be merged under one price-history identity.
3. UNKNOWN may support a narrowly reviewed operational-only append policy, but never research eligibility or historical certification.
4. A changed scaling factor, adjustment version, or historical value creates a linked revision observation and review outcome. It does not authorize a partial rewrite.
5. Corporate-action evidence may explain a boundary or revision only when it identifies the exact symbol/security, dates, terms, values, and source. A large movement alone is insufficient.

### B. Session validity

Every candidate row must satisfy both:

1. **Venue session validity:** the exchange/venue was open for a completed session under an attributable calendar snapshot; and
2. **Symbol-session eligibility:** the same security was listed and active on that venue, was not in a transfer gap, suspended, or delisted, and the row is an actual observation rather than a carry-forward.

Missing symbol-session evidence fails closed. A missing observation on an eligible session is distinct from a non-session. No component may create OHLCV rows by forward filling, calendar expansion, or last-observation carry-forward.

### C. Historical revision

1. Compare all returned overlap rows exactly, not only the two admission anchors.
2. Add a bounded rotating anchor watch outside the ordinary overlap; retain incident, month-end, quarter-end, and corporate-action-adjacent anchors.
3. Store exact old/new values, fingerprints, request identity, generation, source references, and `revision_of` linkage.
4. Any changed or missing expected anchor, scale-like revision, basis/version change, or newly returned historical date is staged for review with zero historical mutation.
5. Attributed evidence permits classification, not automatic repair. Historical repair requires a separate reviewed migration from immutable source bytes.

## Existing D4 coverage matrix

| Requirement | Existing control | Evidence | Missing capability | Minimum correction |
|---|---|---|---|---|
| Validate normalized OHLCV and reject duplicate symbol/session rows | D4A row normalization and `PreparedPriceRow` | `quantlab/preupdate_market_data_guard.py::_rows`; `quantlab/transactional_market_data.py::PreparedPriceRow` | Semantically valid but fabricated forward-filled rows still pass shape checks | Keep validation; add symbol-session eligibility before completed-session admission |
| Prevent a declared RAW/ADJUSTED mismatch | D4A verifies unit and basis equality when both sides are verified | `PRICE_BASIS_MISMATCH`; `PreparedPriceBatch.guard_price_basis()` | Legacy rows generally have UNKNOWN basis; operational admission intentionally allowlists only `PRICE_BASIS_UNVERIFIED` under strict append conditions | Preserve operational-only carve-out, but pin a durable series-basis identity and reject/stage any later basis/version change |
| Detect mixed-basis overlap | Exact field comparison blocks unattributed revisions | D4A `UNATTRIBUTED_HISTORICAL_REVISION`; VHM-style regression tests | Only dates returned by the request are compared; a provider can revise older history outside overlap | Implement the existing D4B2 bounded revision-watch design by widening ordinary requests and rotating retained anchors |
| Prevent partial historical rewrites | D4B2 shadow writer appends only `genuinely_new_dates`; revisions are rejected | `commit_price_batch_shadow()` and operational tests | D4B1 `commit_price_batch()` still contains an upsert path for a Guard PASS with attributed revisions; production paths are not yet wired to the shadow policy | Production ingestion must use append-only operational admission; any revision, even attributed, must stage for a separate repair decision |
| Do not infer corporate actions from price movement | D4A boundary threshold is diagnostic and requires exact attributable `BoundaryEvidence` | `BOUNDARY_DISCONTINUITY_UNEXPLAINED`; corporate-action guard tests | No maintained authoritative corporate-action ledger is integrated | Add attributable event input to the existing batch/evidence model; missing or conflicting event evidence remains UNKNOWN |
| Establish an exchange session without relying on VNINDEX or wall clock | Completed-session contract requires attributable calendar evidence and publication evidence | `CalendarSessionEvidence`; watermark or two stable delayed observations | Calendar evidence is venue-wide only | Extend the existing completed-session contract with symbol-session lifecycle evidence |
| Reject weekends, holidays, and exceptional closures | Completed-session decisions reject `NON_SESSION` and `EXCEPTIONAL_CLOSURE` | `evaluate_completed_session()` | Does not represent listing start/end, transfer gaps, or suspensions | Add symbol-active status and venue/security identity for each candidate date |
| Prevent forward-filled non-trading rows | No control intentionally creates forward-filled rows | CafeF parser accepts only explicit source rows; CTR incident demonstrates the threat | Stable repeated provider observations can satisfy publication stability even when the symbol was not tradable | Evaluate symbol-session eligibility before publication stability; reject any row on an inactive date regardless of fingerprint stability |
| Require completed publication | Operational admission rejects missing, unresolved, or rejected completed-session evidence | `COMPLETED_SESSION_EVIDENCE_MISSING`, `COMPLETED_SESSION_UNRESOLVED`, `COMPLETED_SESSION_REJECTED` | Provider delay policy/watermark and authoritative live calendar remain unresolved for KBS | Qualify the real sources and measured delay before any genuine provider shadow |
| Preserve atomic receipts, manifests, rows, provenance, and retries | `BEGIN IMMEDIATE`, durable receipts, deterministic operation generation and rollback | `commit_price_batch_shadow()`; `market_data_operation_identity.py`; transactional tests | Active `update_symbol()`, `backfill_symbol()`, and legacy writers do not import these controls | Wire update and backfill together only after bypass closure; retain staging for bootstrap/backfill/history gaps |
| Distinguish original raw bytes from normalized content | Archive kind and hashes are explicit; `raw_payload_sha256` is populated only for original provider payload | `ImmutableArchiveIdentity`; manifest fields | A lawful original KBS payload archive is not available; honest NULL is therefore required | Keep raw provenance NULL unless original bytes are actually retained; never relabel normalized rows as raw |
| Keep manual CafeF evidence fail-closed | Explicit ZIP/member hashes, schema/date/venue mapping, source-claim-only adjustment label, disposable backup clone, zero price writes | `quantlab/cafef_manual_eod.py`; focused adapter tests | It validates the declared venue file, not symbol-specific listing/transfer eligibility; input has no completed-session evidence | Continue staging/blocking only; apply the same symbol-session evidence requirement before any future admission |
| Keep legacy history out of prospective research claims | Operational rows are `OPERATIONAL_ONLY`, `research_eligible=false`; historical UI/CLI surfaces append `LEGACY_UNVERIFIED` limitations | `quantlab/operational_admission.py`; `quantctl/historical_qualification.py` | Qualification is artifact-level warning, not an enforced row/window exclusion; existing artifacts remain numerically contaminated | Add an incident exclusion/quarantine overlay to existing qualification and label-building boundaries; do not mutate original artifacts |
| Protect production entrypoints and maintenance paths | Design documents specify restrictions | D4B plans and shadow contracts | No production module imports D4; `save_price_data()`, cleanup, and quarantine apply remain bypass risks | Activation remains blocked until all writers are routed or disabled and audited maintenance semantics exist |

## Exact remaining safety gaps

1. No symbol-specific listing/venue/suspension/delisting evidence exists in completed-session admission.
2. The two-observation completion fallback can accept a stable forward-filled row unless symbol-session eligibility is checked first.
3. The bounded historical revision watch is documented but not implemented in the real provider adapter/runner.
4. UNKNOWN adjustment basis is intentionally allowed for operational-only append, but there is no durable series-basis/version pin that detects a later semantic change outside returned overlap.
5. Attributed historical revisions can still reach the D4B1 upsert path; no production policy should use that path for automatic repair.
6. No machine-enforced row/window quarantine prevents historical research consumers from using confirmed incident rows.
7. No source-qualified corporate-action ledger is integrated.
8. Real KBS access, completion delay, original-payload retention, and HTTP-only client safety remain unresolved.
9. Production update/backfill and legacy maintenance writers are not protected by D4.

## Historical data policy

Canonical `market.db` remains immutable during qualification and remediation design.

| Historical state | Operational eligibility | Research eligibility | Required treatment |
|---|---|---|---|
| Confirmed invalid session row, such as CTR 2022-02-16 through 2022-02-22 | Never considered an observed session or an anchor; no new write may depend on it as valid session evidence | Ineligible | Preserve original row and row id; add a non-mutating quarantine/exclusion record; exclude from calendars, labels, and features in any newly qualified snapshot |
| Supported but unverified price-basis error, including CTR actual-session basis and the unresolved mechanism | Current append-only operation may proceed only after clean current overlap, symbol-session evidence, and no detected revision; it does not validate history | Ineligible for affected windows and descendants | Retain `LEGACY_UNVERIFIED`; acquire provider raw/adjusted bytes and event/version evidence before proposing correction |
| Confirmed price-basis inconsistency, VHM July 28–29 | Same narrow operational rule; incident rows cannot serve as proof of stable basis | Ineligible for incident-spanning returns, labels, and rolling state | Preserve rows; quarantine incident interval and downstream windows that consume it; no fabricated rescaling |
| Unknown corporate-action adjustment | May be operational-only only when no boundary discontinuity or revision is present; otherwise reject/stage | Ineligible | Keep basis and corporate-action state UNKNOWN; do not infer event terms from price movement |
| Historical data with missing provenance | Append-only operational anchor under D4B2 policy only; never whole-history certified | `LEGACY_UNVERIFIED` | Do not retroactively attach invented provider, basis, raw-payload, or event evidence |
| Existing research artifact depending on an incident | No bearing on operational writes | Existing artifact remains historical evidence but not source-qualified research evidence | Preserve bytes and identity; attach limitations/exclusion lineage; rerun only from a separately approved corrected snapshot |

Operational eligibility is permission to append a completed, attributable current observation without altering history. Research eligibility requires verified basis, corporate-action treatment, symbol-session validity, and lineage for the entire relevant input window. Operational acceptance never implies research promotion.

## PHR and SIP disposition

### PHR

- Status: `REVIEW_REQUIRED`, not confirmed corruption.
- Quarantine the 2026-08-27/28 transition and every newly produced return/label/rolling-feature window spanning it.
- Do not change the persisted 61.70 to 33.51 transition.
- Release requires an official issuer/exchange/VSDC event record with ex-date and terms, plus attributable provider RAW/ADJUSTED responses and adjustment semantics bracketing the transition.

### SIP

- Status: `REVIEW_REQUIRED`, not confirmed corruption and not a confirmed corporate action.
- Preserve the 2019 stepwise low-liquidity observations and 2021-01-28/29 move.
- The D1 June 21–27 pair must not be treated as adjacent: June 24–26 SIP rows exist and were hidden by missing local VNINDEX dates.
- Quarantine incident-spanning research labels, not the entire symbol history.
- Release requires authoritative listing/venue/trading-status history, applicable price-limit rules, venue calendar evidence, provider rows, and corporate-action history.

## Minimum implementation recommendation

Extend existing controls; do not create a parallel framework:

1. Add symbol-session lifecycle evidence to `completed_session.py` and require it for every candidate row. Represent listed venue, active interval, transfer gap, suspension/delisting status, stable security identifier, snapshot/version, and source references.
2. Make operational admission reject or stage any candidate whose venue calendar is open but symbol lifecycle is inactive or unresolved.
3. Implement the already-designed trailing-window and rotating-anchor watch in the existing batch adapter. Feed all returned overlaps through D4A exact comparison.
4. Change operational revision handling so every historical revision or insert stages for review, including an exactly attributed revision; reserve mutation for a separate reviewed migration.
5. Pin series basis/version identity in existing manifests and operation identity. A later change creates a linked generation and no price write.
6. Extend historical qualification with a deterministic incident exclusion overlay consumed by labels/features. Keep the canonical rows and old artifact bytes unchanged.
7. Add VHM and CTR fixtures to the existing focused suites: mixed basis across real sessions, venue-transfer gap, stable forward fill, and later provider revision.

## Acceptance criteria

The policy is implemented only when all of the following pass:

1. A CTR fixture rejects February 16–22 even when the venue calendar is open and two provider observations are identical.
2. A missing or unattributable symbol lifecycle produces unresolved/rejected admission and zero writes.
3. A VHM-scale basis switch blocks or stages with exact old/new values; it never rescales or overwrites history.
4. RAW, ADJUSTED, UNKNOWN, and adjustment-version changes cannot share a writable series identity.
5. Every returned overlap and configured historical anchor is compared; any difference, missing anchor, or historical insertion produces durable review evidence and zero historical writes.
6. An attributed corporate action does not automatically authorize a revision; event identity and exact claims are retained for manual review.
7. Confirmed incident rows are excluded from newly qualified labels/features through a versioned overlay, while canonical rows and pre-existing artifacts remain byte-identical.
8. PHR and SIP remain quarantined as specified until their evidence requirements are met.
9. Operation identity, receipt, manifest, staging, rejection, row append, and provenance remain within one `BEGIN IMMEDIATE` transaction with deterministic retry behavior.
10. All real-shadow activity runs on disposable SQLite backup-API clones; canonical SHA256 is unchanged before/after.
11. No operational row becomes research eligible and no staged candidate is automatically promoted.
12. Before production activation, update, backfill, direct writer, cleanup, and quarantine mutation bypasses are closed and rollback retains D4 receipts/provenance.

## Conditions to resume real EOD shadow

A genuine EOD shadow may resume only on disposable clones when:

- lawful provider access and request/retention permissions are documented;
- reviewed transport does not execute the quarantined package path;
- an attributable exchange calendar and symbol-lifecycle snapshot cover every configured symbol and candidate date;
- provider completion is established by an attributable watermark or a measured, approved two-observation delay policy;
- raw payload provenance is archived honestly or left NULL;
- operation identity is integrated, series basis/version is pinned, and the bounded revision watch is active;
- non-session, transfer-gap, suspension, revision, bootstrap, backfill, and missing-evidence outcomes all fail closed with durable status;
- observation runs append only to disposable clones, remain `research_eligible=false`, and leave canonical `market.db` byte-identical.

These conditions permit evidence collection and shadow assessment only. They do not authorize production activation or Phase 7 promotion.
