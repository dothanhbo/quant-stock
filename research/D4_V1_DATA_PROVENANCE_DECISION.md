# D4 — V1 data provenance decision

Status: `DECISION_MEMO` (documentation only, uncommitted). Review date 2026-10-06 (Asia/Bangkok).
Repository: `main` @ `c5d1a131f81858b5899e0ac712efb5070a9951b0` (= `origin/main`).
Canonical `data/market.db` reviewed from a read-only copy (`immutable=1`), SHA-256
`75704e29fe5a3bcf44dba754b42264664523f3270b2eaeefcc7c6812f4055d01` (the state left by the 2026-10-06 controlled Daily).
No provider call, no DB write, no Daily/scanner/backtest/WFO, no Telegram.

This memo **supersedes nothing**. It adds a practical V1 decision layer on top of
`DATA_INTEGRITY_POLICY_DECISION.md`, the VHM/CTR reviews, the CafeF parity review, the
KBS qualification and the personal-EOD memo. Those documents remain valid evidence.

## 1. Result

**CONDITIONAL.** Today's data supports current operations. Its history is not point-in-time, it is not adjustment-verified, and the live updater silently rewrites it.

No currently available source qualifies as historical-research-verified. The V1 freeze
needs four small items (§10). The first one stops the defect that is still adding new damage.

## 2. New evidence found in this review

Everything below comes from read-only queries on the canonical copy plus the retained CafeF archives.

### 2.1 The updater splices basis seams into history

Mechanism, from code:

* `scripts/update_data.py::update_symbol` requests `[latest_stored − 7 calendar days, now]` from `vnstock.api.quote.Quote(source="KBS")`.
* `core/database.py::save_price_data` then runs `INSERT OR REPLACE` on every returned row.
* No overlap comparison is made, and the prior values are not kept.

The provider returns history adjusted as of the fetch date, so the next update after an
ex-date rewrites only the overlap window into the new basis. Older rows stay in the old basis, and a permanent seam is created at the start of the overlap. `INSERT OR REPLACE` gives rewritten rows new row ids, so each seam is visible as a row-id batch boundary.

* From 2026-07-01 there are 11 daily close moves above 7.5% (HOSE's daily band is ±7%). **9 of the 11 sit exactly on a batch boundary**, against a 16% base rate of boundaries in that period:
  * VHM 07-29
  * MSB 08-19
  * TCH 08-19
  * PHR 08-28
  * TCX 08-28
  * VIB 08-28
  * VPI 08-28
  * VPB 09-10
  * TPB 09-24
* Before 2026-07, 9 of 529 large moves fall on a boundary (base rate 0.04%). Those boundaries are mostly deletion/quarantine gaps.
* All nine 2026 seam moves are drops. That fits a newer batch that was back-adjusted for a later event, but it does not prove it.

### 2.2 A previously verified row was silently revised by the accepted Daily

TPB 2026-10-01:

* Close was 14.40 in the 2026-10-03 parity review, with exact parity to CafeF RAW (DB `F771B314…`).
* Close is now **12.10**. Volume is unchanged at 16,555,800.
* Rows 09-24 to 10-01 (ids 586939–586944) were rewritten by the 2026-10-06 run. 09-23 (id 586072, 14.75) was not.
* Result: a false −16.3% seam at 09-23→09-24. The real RAW ex-date move on 10-02 (CafeF RAW 14.40→11.90) is gone from the series.

DGW 2026-10-02:

* Canonical close is now 44.60. CafeF RAW and CafeF regular file both show 45.60, and volume is identical.
* A ≈2.2% re-base consistent with a cash dividend; the seam sits at 09-23→09-24 (id 585645→586390).
* This shows that **small (cash-dividend) seams exist and are invisible to large-move thresholds**. The 9 seams listed above are a lower bound.

The other 99 of 100 equities still match CafeF RAW for 2026-10-02 exactly.

### 2.3 The provider adjusts prices but not volume

TPB and DGW volumes are unchanged after their price re-base. In the provider series as currently stored, price×volume (turnover) is therefore not basis-consistent.

### 2.4 The universe is live and survivorship-biased

`core/universe.py::get_vn100_symbols` uses the legacy `vnstock.Vnstock().stock(source="VCI").listing.symbols_by_group("VN100")`. That is the deprecated interface behind the Daily warning.

Today's 101 symbols (VN100 + VNINDEX) are the only ones that have history. There is no membership history.

### 2.5 Neither market history nor research results can be re-created

* `market.db` stores `symbol, time, OHLCV` only. Provider, fetch time, batch, basis and prior versions are absent.
* `MarketDataSnapshot.snapshot_id` is a content fingerprint, not a retained copy.
* Frozen-Q70/WFO artifacts do not bind a DB hash (D2 matrix).
* Every Daily changes the DB, so earlier research inputs can be identified at best, not re-created.

## 3. Source inventory

Legend: OP = operational, PROSP = prospective research, HIST = historical research.

| Source | OP | PROSP | HIST | Adjustment confidence | Revision risk |
|---|---|---|---|---|---|
| `vnstock.api.quote.Quote(source="KBS")` → `kbbuddywts.kbsec.com.vn/.../data_day` (vnstock 4.0.2) | **Canonical** (since commit `2a5d30d`, 2026-08-05); writes all of `prices` | Only after an observation log exists (§8) | No | LOW. Evidence (VHM vs CafeF ADJ; CTR 2022 ≈0.6493× published raw; TPB/DGW) shows **back-adjusted as of fetch date**, covering stock and cash events in observed cases. Method, version and event coverage are UNKNOWN. Volume is not adjusted. Prices are thousand VND (code: ÷1000, rounded to 2 dp). | **HIGH, demonstrated.** History is re-based after corporate actions and was forward-filled during the CTR 2022 non-trading gap. No snapshots are kept. Access, retention and quota terms are UNRESOLVED. |
| `vnstock.Vnstock` legacy listing, source VCI (VN100 membership) | Universe only. Fail-closed guards exist. | No | No (current constituents only) | n/a | Membership changes silently. No history. Deprecated. |
| `vnstock.api.quote.Quote(source="VCI")` | Former source (before 2026-08-05) | No | No | UNKNOWN | Per-row lineage is not recorded, so whether any VCI-era rows survive is UNKNOWN |
| CafeF manual RAW archives (`CafeF.SolieuGD.Raw.*.zip`, hashes retained) | No (manual, no completion evidence) | **Verification/shadow source.** Exact OHLCV parity 100/100 on 2026-10-01. | No (only a few sessions retained) | MEDIUM for RAW-ness on retained sessions, based on the file label and the ex-date move on TPB 10-02. The method is a source claim. | Low for retained bytes (hashed). Upstream revision behaviour is UNKNOWN. |
| CafeF regular (adjusted-labelled) files and the VHM screenshot | No | Cross-check only | No | UNKNOWN. The 10-02 regular file equals RAW for all equities. | UNKNOWN |
| KBS direct observation pilot | No | Not started (zero requests) | No | UNKNOWN | Blocked on access/retention terms (KBS qualification memo) |
| Official notices (VSDC/HOSE/issuer, broker mirrors) | No | Corporate-action event evidence | Descriptive | n/a | Ad hoc. No ledger. Most bytes not retained. |
| VNINDEX via the KBS index endpoint | Yes (regime/benchmark) | Same as KBS | Descriptive | Index level, no adjustment applied by the code path | Same transport risks. 4 known local calendar gaps (2019-06-24..26, 2021-08-23). |

## 4. Current `market.db` provenance — what is actually known

| Question | Answer |
|---|---|
| Source populating it | KBS via vnstock for writes since 2026-08-05. A whole-history (8-year) write for 70 symbols ends 2026-08-12. Per-row source is not recorded. |
| Overwrites recent history? | **Yes.** Every update replaces `[latest − 7 calendar days, today]`, usually 5–9 sessions (the 10-06 run rewrote 09-24..10-06). |
| Raw provenance per observation | **None** (no source, fetched_at, batch, basis or hash columns). The D4 provenance/receipt tables exist in code but are not wired to production. |
| Do original values survive revisions? | **No.** `INSERT OR REPLACE` deletes them. Since the 2018 start, about 400k row replacements have occurred: max id 587,127 against 189,573 rows. |
| Prospective vs revised/backfilled? | **Cannot be distinguished.** Row ids reveal batch boundaries only, not content history. |
| Can corporate actions change historical OHLC? | **Yes, demonstrated** (TPB, DGW, VHM). They change only inside the overlap window, which creates mixed-basis series. |
| Does the canonical strategy depend on adjustment-sensitive fields? | **Yes.** Donchian/trend entries, ATR stops and targets, ADX/RSI/EMA, RS20 and breadth EMA50 all use multi-session equity OHLC windows. VNINDEX regime is unaffected by equity adjustment. Seams therefore distort current indicators for affected symbols for as long as the seam stays inside a lookback window (EMA-based state indefinitely). |
| Prospective evidence exposure | Forward return = target close read from `market.db` ÷ formation close stored at formation. Paper P&L and stops use raw prices with **no corporate-action handling** anywhere in `execution/` or `quantlab/forward/`. An ex-date inside a holding or forward window produces a false raw price loss (e.g. TPB −17% on 10-02, a stock dividend not credited). |

## 5. Revision / discontinuity findings

| Incident | Classification | Basis |
|---|---|---|
| VHM 2026-07-28→07-29 (−46.9%) | **Source revision + partial overwrite** (mixed basis), confirmed | Persisted closes match CafeF RAW up to 07-28 and CafeF ADJ from 07-29. The seam is at a batch boundary (id 551198→552778); the 1:1 ex-date was 08-06. The CafeF values come from a screenshot, not bytes. |
| TPB 2026-09-23→09-24 (−16.3%) | **Source revision + partial overwrite**, confirmed | CafeF RAW bytes (10-01, 10-02) versus the current rows. Ex-date move on 10-02. Event terms are not retained. |
| DGW 2026-09-23→09-24 (≈2.2% artefact) | **Source revision + partial overwrite**, confirmed | CafeF RAW/regular 10-02 = 45.60, canonical 44.60, same volume. Event identity not retained. |
| PHR 08-28, TCX 08-28, VIB 08-28, VPI 08-28, MSB 08-19, TCH 08-19, VPB 09-10 | **Unresolved; source-revision seam suspected** | They sit on overwrite batch boundaries and exceed the ±7% band. No event or provider bytes are retained. |
| MBB 2026-08-03 (−11.2%), SSI 2026-08-07 (−19.5%) | **Unresolved** | Inside a single batch. These could be raw ex-date moves in a batch fetched before adjustment. No evidence. |
| CTR 2022-02-14..23 | **Data error (forward-filled non-trading rows) + supported basis inconsistency** | Five identical OHLCV tuples during the venue transfer (VSDC/HOSE timeline). Actual-session rows ≈0.6493× published raw. Mechanism UNRESOLVED. |
| SIP 2019 / 2021 | **Unresolved** (low liquidity, calendar-filter artefact) | D2 triage |
| VNINDEX calendar gaps (4 dates, 348 equity rows) | **Unavailable evidence** (benchmark gap suspected) | D2 |
| 86 rows in `prices_quarantine` (`INVALID_OHLC`, 2026-08-24) | **Data error**, removed from `prices` | Existing quarantine table |
| 529 pre-2026-07 moves >7.5% | **Unresolved / unavailable evidence** | Single-batch history adjusted to an unknown method. They may include listing, transfer or band-rule cases. No verdict is made. |

**Implication:** the historical series is a provider view adjusted as of mid-August 2026 with an UNKNOWN method. Since then it carries growing post-August seams. It is not a raw series, not a verified adjusted series, and not point-in-time.

## 6. Point-in-time risk

**Can we prove that a value downloaded today equals what was observable on that date? No.** For KBS history the evidence shows the opposite: values change after corporate actions (TPB 10-01: 14.40 → 12.10).

| Risk | Status |
|---|---|
| Retrospective adjustment | Demonstrated. Method UNKNOWN. |
| Vendor corrections | UNKNOWN beyond adjustment. Forward-fill of a non-trading period demonstrated (CTR). |
| Survivorship / universe | **Present.** Only current VN100 constituents have history. |
| Delayed corporate-action normalization | Demonstrated. The adjustment arrives on the first fetch after the ex-date and reaches only the overlap window. |
| Missing-session repair/backfill | Possible; nothing records it (SIP/VNINDEX gaps, CTR missing 02-15). |

**Reasonably robust (descriptive):**

* Same-session cross-sectional facts (rank, level and volume on one date).
* VNINDEX-level regime description (with the 4 gaps disclosed).
* Event identification against official notices.
* Liquidity screens, stated in shares.

**Not robust:**

* Multi-session returns and forward labels across any ex-date.
* Momentum/RS, breakout, ATR stop and target simulations.
* WFO and backtests.
* Rank-IC of forward returns.
* Turnover value across stock events.
* Any cross-section over history (survivorship).
* Exact reproduction of a past result.

## 7. V1 data eligibility matrix

| Data | Class | Notes |
|---|---|---|
| Latest completed session written by the current Daily (KBS) after integrity PASS | **OPERATIONAL_CURRENT** | Latest-bar values matched independent CafeF RAW 99–100/100 on checked sessions. |
| Lookback windows used by current Daily/scanner | **OPERATIONAL_CURRENT, conditional** | Known seam defect (§2.1). Blocker R1 and the one-time re-baseline R4. |
| Prospective observations in `market.db` | **Not PROSPECTIVE_RESEARCH_ELIGIBLE** | Rows are rewritten later and nothing records the observed value. |
| Values frozen in evidence ledgers (forward formation closes + `recorded_at_utc` + snapshot fingerprint; paper fills/stops; `prospective_portfolio_evidence` with contract identity, from 2026-10-06) | **PROSPECTIVE_RESEARCH_ELIGIBLE, label `RAW_PRICE_NO_CORPORATE_ACTION_TREATMENT`** | Outcomes spanning a re-based symbol must be flagged (R3) before they count as canonical V1 evidence. |
| `market.db` history 2018-08-07 → present | **LEGACY_UNVERIFIED** dataset; analysis **DESCRIPTIVE_ONLY** | Incident windows (§5) are excluded from any newly qualified use. |
| Historical research artifacts (Phase 5–8 panels, factor/Rank-IC studies, frozen-Q70 WFO, portfolio/risk syntheses, ADX exploration) | **LEGACY_UNVERIFIED**; adjustment-sensitive conclusions **DESCRIPTIVE_ONLY** | Keep bytes and identities. Never call them verified. |
| KBS via vnstock | OP canonical; PROSP after R2; HIST **not eligible** | |
| VCI listing | OP universe only; HIST not eligible (survivorship) | |
| CafeF RAW retained archives | Verification/shadow; HIST not eligible | |
| Any source for **HISTORICAL_RESEARCH_ELIGIBLE** | **None qualifies today.** | |

## 8. Minimum V1 EOD contract

**Mandatory (V1):**

1. **No partial rewrite.**
   * Compare every returned overlap row with the stored row.
   * Identical overlap → append new sessions only.
   * Any difference → treat as a series revision: **rebuild the whole symbol atomically** from one fresh full-history fetch, keep the superseded rows, or fail closed for that symbol.
   * Never splice.
2. **Append-only observation log** (separate SQLite file, e.g. `data/market_observations.db`; `market.db` schema untouched). One record per fetch batch:
   * `run_id` (operation_history), `symbol`, request start/end;
   * `source` = `vnstock.api.Quote/KBS` plus package version;
   * `fetched_at_utc`;
   * returned rows (`session, O, H, L, C, V`);
   * per-row hash and batch content hash;
   * `basis_label = PROVIDER_ADJUSTED_AS_OF_FETCH_UNVERIFIED`;
   * outcome (`APPENDED / IDENTICAL / REVISION_REBASED / REVISION_BLOCKED / ERROR`);
   * for revisions, the superseded rows (old/new) and their hash.
3. **Corporate-action/revision flag on prospective evidence.**
   * When a symbol is re-based (or a seam is detected), every open paper position and every forward formation/outcome window on that symbol is marked (e.g. `CORPORATE_ACTION_AFFECTED`).
   * The flag is recorded, not repaired.
4. **Explicit labels** for all historical use: `LEGACY_UNVERIFIED`, `PRICE_ADJUSTMENT_UNKNOWN`, existing limitations, plus `SURVIVORSHIP_CURRENT_CONSTITUENTS` and `NOT_POINT_IN_TIME`.

Mapping to the requested fields:

* `symbol`, `market_session`, OHLCV → present.
* source/provider, `fetched_at`, basis label, batch id/hash → in the observation log.
* Revision identity → superseded-version records.
* Content hash → per batch.

**Backlog (desirable, not V1):**

* Original response-body capture before parse.
* Publication watermark / completed-session evidence.
* Symbol lifecycle register.
* Corporate-action ledger.
* Rotating historical anchor watch.
* Independent automated second source.
* VN100 membership history.
* Separate RAW and ADJUSTED series identities.
* The D4B2 admission/receipt stack in production.

## 9. Canonical source policy and rules

* **Operational:** KBS via `vnstock.api.quote.Quote`, unchanged.
* **Shadow/verification:** CafeF RAW archives, manual and occasional, used for parity around suspected events.
* **Historical research:** **none.** No source is qualified.
* **Fallback:** none automatic. A source switch is a semantic change and needs a whole-history re-baseline plus a decision record.
* **Rejected for adjustment-sensitive research:** the existing `market.db` history and any KBS history fetch, until method, version and point-in-time behaviour are evidenced.

**Historical research policy:**

* Existing VNStock-based backtests and studies stay where they are, with their outputs unchanged. They may be cited only as `LEGACY_UNVERIFIED` (they can be identified, not re-created).
* They may not be called verified.
* Raw-return Rank-IC/factor studies may be kept as DESCRIPTIVE_ONLY. The sign of the adjustment bias is UNKNOWN because the input is a mixed basis.
* Before promotion, the following need adjustment-verified, point-in-time, survivorship-aware history:
  * return-based labels, IC, WFO and backtest performance claims;
  * stop/target simulations;
  * risk/portfolio syntheses;
  * any claim of alpha or robustness.

**Prospective policy:**

* Strong provenance starts with the first Daily after R1+R2: fetch → `fetched_at` + source identity → batch/row hash → (optional CafeF RAW parity) → immutable log record.
* Evidence ledgers already freeze formation inputs. R3 adds the corporate-action flag.
* Reuse the D4 contracts where it is cheap: `ingestion_provenance` field names, the D4A exact comparator, the `revision_of` semantics. Do not build a second platform or wire the full D4B2 admission stack for V1.

## 10. V1 closure

**Safe today:**

* Daily operation on current bars with fail-closed integrity, universe ≥100 and contract checks.
* Prospective ledgers, with the raw-price label.
* Preserving all historical artifacts as `LEGACY_UNVERIFIED`.

**Unverified:**

* Adjustment method.
* Point-in-time history.
* Pre-2026-07 discontinuities.
* Event terms.
* Universe history.

**Required before V1 freeze (only items preventing false claims, silent drift, unreproducible evidence or operational distortion):**

* **R1** Overlap revision guard with whole-symbol atomic rebase (or fail closed); no partial rewrite.
* **R2** Append-only market observation log, wired into update and backfill.
* **R3** Corporate-action/revision flag on paper and forward evidence for affected symbols.
* **R4** After R1+R2: a one-time, approved re-baseline of `market.db`.
  * Archive the current file byte-identical as `LEGACY_UNVERIFIED`.
  * Rebuild all 101 symbols from one dated fetch.
  * Produce a diff report.
  * This needs explicit approval for provider calls and the DB swap.

**Next implementation batch (one only):** R1 + R2. Touch `scripts/update_data.py`, `scripts/backfill_market_data.py`, a small new observation-log module, and `core/database.py` (atomic symbol replace). Add offline tests reproducing the TPB, DGW and VHM patterns:

* identical overlap → append only;
* changed overlap → whole-symbol rebase with superseded rows logged, never a splice;
* rebase fetch failure → symbol fails closed and Daily integrity reports it;
* the 8-year window boundary is handled explicitly (rows older than the rebuilt range are kept, labelled and excluded);
* no `market.db` schema change.

There are no strategy, Manager or threshold changes.

**VNStock deprecation:**

* Not an immediate blocker. Only the VN100 listing uses the legacy `Vnstock` class. Prices already use `vnstock.api`. Daily (≥100 + VNINDEX), scanner (non-empty) and standalone update (≥100) fail closed.
* Later: move to `vnstock.api.listing.Listing(source="vci").symbols_by_group(group="VN100")`. Validate with a recorded side-by-side set-equality check (same 100 tickers) before switching.
* Changing the listing source (KBS vs VCI) is a separate decision.
* The status of `vnstock.Reference` (sector) is UNKNOWN; it is not operationally critical.
