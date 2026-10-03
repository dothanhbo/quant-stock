# VHM data reconciliation and incident triage

Status: `CONFIRMED_PRICE_BASIS_INCONSISTENCY` for the persisted VHM series. The mechanism that produced the transition remains `UNVERIFIED`.

## Scope and evidence controls

- Review date: 2026-10-03 (Asia/Bangkok).
- Repository HEAD at review start: `0e91e92e2028b14322e7618475c5ad7d6b2e7e7f`.
- Canonical database: `data/market.db`.
- Canonical SHA256 before review: `F771B314EF2094373C3753DEF45223DD6E61AEC84E15A369FCD52DA585265FB6`.
- Database access: SQLite URI `mode=ro` with `PRAGMA query_only=ON`.
- No database row, provider, ingestion, research result, or trading state was changed.

The CafeF RAW and ADJUSTED observations below were transcribed from a user-supplied browser screenshot accessed on 2026-10-03. They are **not** archived CafeF response bytes, an original raw provider payload, or a content-hashed source artifact. They can reconcile visible values but cannot establish the upstream KBS batch identity or retrieval history.

The review also reuses the existing read-only D1/D2 artifacts under:

- `research/data_integrity_audit/run_20261002T075232Z_f771b314/`
- `research/data_integrity_audit/run_20261002T080507Z_d2_f771b314/`
- `research/free_data_pilot/`
- `research/free_data_pilot_v1_1/`

## VHM reconciliation

### Persisted rows compared with supplied CafeF observations

| Date | Persisted close | CafeF RAW (screenshot) | CafeF ADJUSTED (screenshot) | Reconciliation |
|---|---:|---:|---:|---|
| 2026-07-27 | 131.00 | 131.00 | 65.50 | RAW-equivalent |
| 2026-07-28 | 131.90 | 131.90 | 65.95 | RAW-equivalent |
| 2026-07-29 | 70.00 | 140.00 | 70.00 | ADJUSTED-equivalent |
| 2026-07-30 | 73.95 | 147.90 | 73.95 | ADJUSTED-equivalent |
| 2026-08-05 | 76.50 | 153.00 | 76.50 | ADJUSTED-equivalent |
| 2026-08-06 | 77.10 | 77.10 | 77.10 | RAW and ADJUSTED converge in the supplied observation |

The original persisted VHM rows were read directly. Relevant row identities and full OHLCV are:

| Row id | Date | Open | High | Low | Close | Volume |
|---:|---|---:|---:|---:|---:|---:|
| 551197 | 2026-07-27 | 130.60 | 133.90 | 129.00 | 131.00 | 3,707,500 |
| 551198 | 2026-07-28 | 130.00 | 132.30 | 128.90 | 131.90 | 2,674,400 |
| 552778 | 2026-07-29 | 66.25 | 70.55 | 65.95 | 70.00 | 6,570,600 |
| 552779 | 2026-07-30 | 71.20 | 73.95 | 70.05 | 73.95 | 5,883,600 |
| 552783 | 2026-08-05 | 77.10 | 79.40 | 76.50 | 76.50 | 10,681,100 |
| 552784 | 2026-08-06 | 81.70 | 81.70 | 76.80 | 77.10 | 16,808,100 |

The stored series therefore changes from screenshot RAW-equivalent values on July 28 to screenshot ADJUSTED-equivalent values on July 29. The change predates the documented 1:1 stock-dividend ex-date of 2026-08-06 and is not an economic ex-date return.

### False return

- Persisted July 28 to July 29 close return: `(70.00 / 131.90 - 1) * 100 = -46.929492%`.
- Basis-consistent RAW return: `(140.00 / 131.90 - 1) * 100 = +6.141016%`.
- Basis-consistent ADJUSTED return: `(70.00 / 65.95 - 1) * 100 = +6.141016%`.
- False-return error: `-53.070508` percentage points relative to either consistent basis.

### Classification and limits

Classification: `CONFIRMED_PRICE_BASIS_INCONSISTENCY`.

This classification is supported by exact value agreement between the persisted closes and opposite columns of the supplied CafeF comparison on consecutive sessions. It does **not** verify that KBS changed batch type, that CafeF and KBS share adjustment semantics, or that a particular retrieval overwrote prior rows. Those mechanisms remain `UNVERIFIED` because the legacy database has no provider batch identity, retrieval timestamp, adjustment mode, raw response, or revision lineage.

Existing event evidence records a 1:1 VHM stock dividend, record date 2026-08-07 and ex-date 2026-08-06. The repository retains URLs and descriptive pilot evidence, but not immutable original notice bytes for this review. It establishes event context, not the provenance of the July 28/29 stored-row transition.

## Ranked incident investigation queue

Ranking is by urgency of missing evidence needed to distinguish source corruption, basis changes, and legitimate market events—not by expected alpha impact.

### 1. CTR — highest evidence urgency

Exact D1 abnormal transitions:

| Transition | Close change | Adjacency |
|---|---:|---|
| 2021-01-28 33.70 -> 2021-01-29 39.11 | +16.053412% | Consecutive local sessions |
| 2022-02-14 56.49 -> 2022-02-16 87.00 | +54.009559% | Missing CTR observation on local VNINDEX session 2022-02-15 |
| 2022-02-22 87.00 -> 2022-02-23 59.35 | -31.781609% | Consecutive local sessions |

Verified persisted facts:

- Normal-looking neighboring closes run from 55.39 on February 7 through 56.49 on February 14.
- February 16, 17, 18, 21, and 22 repeat the **same entire OHLCV tuple**: `88.60 / 88.60 / 86.20 / 87.00 / 729,829`.
- February 23 returns to 59.35, followed by 58.05 and 58.18.
- The February 14/16 transition spans a valid local VNINDEX session for which CTR has no row.
- No official CTR corporate-action document or provider batch payload is present in the reviewed D1/D2 evidence.

Hypotheses only: stale-row replication, a source/batch mapping defect, an unrecorded ticker-identity discontinuity, or a corporate-action/price-basis transition. The five-session identical OHLCV plateau makes source-lineage acquisition especially urgent, but does not by itself prove corruption.

Missing evidence: original provider responses and request metadata for February 14–23, 2022; provider revision/adjustment semantics; an official CTR event and ticker-identity history; and an explanation for both the absent February 15 row and the repeated OHLCV tuple.

### 2. PHR — clean persistent level shift, event unresolved

Exact D1 abnormal transition:

| Transition | Close change | Adjacency |
|---|---:|---|
| 2026-08-27 61.70 -> 2026-08-28 33.51 | -45.688817% | Consecutive raw symbol and local VNINDEX sessions |

Verified persisted facts:

- August 17–27 closes remain around 60–62; August 26 and 27 close at 61.50 and 61.70.
- August 28 changes to the 33.51 level, which persists at 33.29, 33.56, 33.62, and 33.67 on the next four stored observations.
- D1 found no symbol-history gap around the transition and no OHLCV row-invariant violation.
- No attributable PHR corporate-action notice or raw/adjusted provider evidence is stored in the reviewed evidence directories.

Hypotheses only: a legitimate corporate-action adjustment, a provider-wide historical adjustment revision, or a mixed-basis batch transition. Persistence supports investigation but does not identify the cause.

Missing evidence: official exchange/VSDC issuer event record with ex-date, ratio and cash terms; raw and adjusted provider payloads bracketing August 27–28; retrieval/batch identities; and provider adjustment/version semantics.

### 3. SIP — listing/liquidity and calendar context required first

Exact D1 threshold transitions:

| Transition | Close change | Qualification |
|---|---:|---|
| 2019-06-12 3.91 -> 2019-06-13 5.45 | +39.386189% | Consecutive local sessions; volume rises from 0 to 100 |
| 2019-06-13 5.45 -> 2019-06-14 6.27 | +15.045872% | Consecutive local sessions; volume 100 |
| 2019-06-20 10.90 -> 2019-06-21 12.54 | +15.045872% | Consecutive local sessions |
| 2019-06-21 12.54 -> 2019-06-27 16.31 | +30.063796% | **Not adjacent persisted SIP observations**; June 24–26 rows were excluded by missing local VNINDEX dates |
| 2019-07-03 16.13 -> 2019-07-05 18.94 | +17.420955% | Crosses one locally missing VNINDEX session |
| 2021-01-28 49.09 -> 2021-01-29 56.96 | +16.031778% | Consecutive local sessions |

Verified persisted facts:

- June 6–12, 2019 contains repeated 3.91 zero-volume rows.
- June 13–21 shows stepwise near-limit increases, commonly flat OHLC and volumes of only 100–200 until June 21.
- Persisted SIP rows exist on June 24 (14.40), June 25 (16.56), and June 26 (16.35). Therefore the D1 June 21/27 `POTENTIAL_CORPORATE_ACTION` pair is a VNINDEX-calendar-filter artifact, not a raw adjacent SIP transition.
- The 2021-01-29 move coincides with a separately flagged CTR move on the same date, which warrants market-context review rather than an automatic corruption label.
- No official SIP listing-phase, price-limit, venue-history, or corporate-action document is preserved in the reviewed evidence.

Hypotheses only: early listing/illiquidity and permitted daily price-limit behavior could explain much of the 2019 sequence. A corporate action is not established.

Missing evidence: authoritative SIP listing and venue history, applicable 2019 price-limit rules, official daily session calendar for the venue, provider payloads for the flagged dates, and attributable corporate-action history.

## Research impact

### Directly reproduced impact

The existing D2 reconciliation identifies 111 unique persisted point-in-time forward-return label cells whose intervals include a high-severity VHM, CTR, or PHR transition:

- VHM July 28/29: 35 cells.
- CTR February 14/16: 32 cells.
- CTR February 22/23: 35 cells.
- PHR August 27/28: 27 cells.

Across overlapping cells, 108 labels numerically match the current close-based formula, two CTR cells cannot be recomputed because the February 15 close is absent, and one PHR 20-session label differs because the historical artifact implies a 31.75 target close while the current database stores 31.70. The artifact and current database hashes differ, so the mismatch source is not attributable.

### Potential exposure

- `ADX_ATR_RSI_EMA_DONCHIAN`, `RELATIVE_STRENGTH_20D`, and `BREADTH_AND_MARKET_REGIME` structurally consume affected OHLC/close sequences.
- Frozen historical Q70/WFO candidate workflows cover the CTR, VHM, and PHR incident dates.
- Recent VHM/PHR rows may remain within active scanner or paper-signal feature warmups.
- SIP can affect rolling features and breadth when eligible, but no incident-specific persisted label-cell mapping was reproduced in D2.

These are dependency findings, not proof that a decision, trade, rank, or signal changed.

### Unknown quantitative impact

No feature panel, candidate set, WFO, factor IC/spread, portfolio return, risk metric, scanner output, or performance result was recomputed. The magnitude and direction of impact on neutral-factor evaluation, portfolio/risk research, older custom WFO/ablation artifacts, and prospective records remain unknown. Affected historical inputs should remain `LEGACY_UNVERIFIED` pending source and event qualification.

## Remediation evidence required

1. Immutable original provider payloads, request parameters, retrieval timestamps, package/provider versions, and batch identities bracketing each incident.
2. Explicit raw-versus-adjusted semantics and revision history from the data owner/provider.
3. Official exchange/VSDC/issuer corporate-action documents with original bytes, publication identity, ex-date, record date, ratio/cash terms, amendments, and SHA256.
4. Stable security identifiers and ticker/venue continuity records for CTR, PHR, and SIP.
5. Authoritative venue session calendars, particularly for SIP dates excluded by missing VNINDEX observations.
6. For SIP, official listing date, trading-status history, and applicable daily price-limit regime.

## Recommended next action

Acquire and content-hash the original provider responses plus authoritative event/ticker records for the CTR February 14–23, 2022 window first. Its bidirectional discontinuities, missing session, and five identical OHLCV rows make it the most urgent source-lineage case. Keep the database unchanged; reconcile the acquired bytes in a separate evidence record before proposing any repair.

