# CTR February 2022 incident review

Status: `CONFIRMED_NON_TRADING_GAP_ROWS`; `SUPPORTED_PRICE_BASIS_INCONSISTENCY`; source-generation mechanism `UNRESOLVED`.

## Scope and controls

- Review date and external-source access date: 2026-10-03 (Asia/Bangkok).
- VHM checkpoint commit created before this review: `f04c658` (`document VHM price-basis reconciliation`).
- Canonical database: `data/market.db`.
- Canonical SHA256 at review start: `F771B314EF2094373C3753DEF45223DD6E61AEC84E15A369FCD52DA585265FB6`.
- Persisted data were queried using SQLite URI `mode=ro` and `PRAGMA query_only=ON`.
- No historical row, artifact, research result, or trading state was changed.

The review starts with the existing D1/D2 evidence:

- `research/data_integrity_audit/run_20261002T075232Z_f771b314/flagged_discontinuities.csv`
- `research/data_integrity_audit/run_20261002T080507Z_d2_f771b314/high_severity_triage.csv`
- `research/data_integrity_audit/run_20261002T080507Z_d2_f771b314/research_impact_matrix.csv`

## Persisted observations

The exact canonical rows covering the incident are:

| Row id | Date | Open | High | Low | Close | Volume | Review note |
|---:|---|---:|---:|---:|---:|---:|---|
| 418781 | 2022-02-14 | 57.53 | 57.53 | 55.97 | 56.49 | 729,829 | Last actual UPCoM trading date, but stored price level is not the published unadjusted level |
| — | 2022-02-15 | — | — | — | — | — | No persisted CTR row |
| 418782 | 2022-02-16 | 88.60 | 88.60 | 86.20 | 87.00 | 729,829 | Venue-transfer gap; tuple 1 of 5 |
| 418783 | 2022-02-17 | 88.60 | 88.60 | 86.20 | 87.00 | 729,829 | Venue-transfer gap; tuple 2 of 5 |
| 418784 | 2022-02-18 | 88.60 | 88.60 | 86.20 | 87.00 | 729,829 | Venue-transfer gap; tuple 3 of 5 |
| 418785 | 2022-02-21 | 88.60 | 88.60 | 86.20 | 87.00 | 729,829 | Venue-transfer gap; tuple 4 of 5 |
| 418786 | 2022-02-22 | 88.60 | 88.60 | 86.20 | 87.00 | 729,829 | Venue-transfer gap; tuple 5 of 5 |
| 418787 | 2022-02-23 | 58.44 | 59.41 | 57.14 | 59.35 | 1,029,000 | First actual HOSE trading date, stored price level is not the published unadjusted level |

Neighboring rows are internally ordinary before February 14 and after February 23: February 11 closed at 57.73; February 24 and 25 closed at 58.05 and 58.18. The five intervening rows repeat every OHLCV field, not merely the close.

The D1 transitions were:

- 2022-02-14 to 2022-02-16: `+54.009559%` from 56.49 to 87.00.
- 2022-02-22 to 2022-02-23: `-31.781609%` from 87.00 to 59.35.

## External evidence

All external pages below were accessed on 2026-10-03. They are live web evidence, not locally archived original bytes, and no content hash was captured.

| Source | Evidence | Limitations |
|---|---|---|
| [VSD/VSDC transfer notice](https://vsdc.vn/vi/ad/147589) | States that from 2022-02-17 VSD transferred CTR registration and depository data from UPCoM/HNX to listed trading on HOSE. It identifies the same ticker CTR and ISIN `VN000000CTR4`. | Live HTML/search-index evidence; original signed notice bytes were not retained in this review. |
| [State Securities Commission HOSE news archive](https://ssc.gov.vn/webcenter/portal/ubck/pages_r/m/thngtinthtrng/thngkthtrng/thngtinhose/tintchose?docType=TinBai&mucHienThi=188&selectedPage=20) | Records HOSE admitting 92,923,873 CTR shares to trading on 2022-02-23. | Archive page timed out when opened directly; the indexed official text was available. |
| [CafeF transfer report, published 2022-02-15](https://cafef.vn/viettel-construction-ctr-chuyen-niem-yet-hose-trong-thang-2-chot-gia-chao-san-85400-dong-co-phieu-20220215151004956.chn) | Reports final UPCoM trading date 2022-02-14, deregistration from 2022-02-15, first HOSE trading date 2022-02-23, first-day reference price VND 85,400, and February 14 close VND 87,000. | Secondary transcription of exchange information; not exchange EOD bytes. |
| [Thời báo Tài chính Việt Nam, published 2022-02-23](https://thoibaotaichinhvietnam.vn/co-phieu-ctr-tang-manh-trong-phien-chao-san-hose-100688.html) | Reports the first HOSE session closing at VND 91,400, up 6.9% from the VND 85,400 reference price. | Financial-news report, not an official exchange trade file. |
| [VSD 2021 dividend notice](https://vsd.vn/en/ad/151262) | Identifies a later record date, 2022-06-20, for CTR's 2021 cash and stock dividends, same ISIN `VN000000CTR4`. | Occurs months after this transfer window. It may matter to backward-adjusted historical series but is not an event inside February 14–23. |

The external venue timeline is therefore:

1. February 14: final UPCoM trading session; reported unadjusted close VND 87,000 and volume approximately 0.73 million shares.
2. February 15: UPCoM registration cancelled; CTR was not trading on an exchange.
3. February 17: depository/registration data transferred from UPCoM/HNX to HOSE.
4. February 23: first HOSE trading session; reference VND 85,400 and reported unadjusted close VND 91,400.

## Reconciliation

The canonical February 14 close of 56.49 is `0.6493103448` times the published unadjusted close of 87.00. The canonical February 23 close of 59.35 is `0.6493435449` times the published unadjusted close of 91.40. The two factors differ by only `0.0000332000`.

In contrast, the five February 16–22 rows close at 87.00 and repeat the same 729,829 volume as the final trading observation. Their close matches the published unadjusted February 14 close exactly. This establishes a basis discontinuity within the stored sequence, regardless of which later corporate actions were included in the adjustment factor.

## Classification

### Confirmed facts

- CTR moved from UPCoM to HOSE without a ticker change. VSD identifies the same ISIN `VN000000CTR4`; no security-identity or nominal-unit change was found.
- February 14 was the last UPCoM trading date and February 23 the first HOSE trading date.
- February 15–22 were therefore a venue-transfer non-trading gap for CTR, even though the broader market traded on some of those dates.
- The missing February 15 row is expected and is not, by itself, a data omission.
- The five persisted rows dated February 16, 17, 18, 21, and 22 cannot be genuine CTR exchange EOD observations for those dates.
- Those five rows repeat the exact same OHLCV tuple.
- Published unadjusted closes are VND 87,000 on February 14 and VND 91,400 on February 23. Canonical closes on those actual trading dates are at a nearly identical approximately 0.6493 scaling factor.
- No corporate-action ex-date inside February 14–23 was identified. The documented cash/stock dividend record date found is June 20, 2022.

### Supported hypotheses

- The five gap rows are consistent with a last-observation forward fill of the unadjusted February 14 tuple. Exact source behavior remains unverified because the originating payload and transformation code/version are absent.
- The actual trading-date rows are consistent with a backward-adjusted series, while the gap rows are raw-equivalent. The close ratios strongly support `PRICE_BASIS_INCONSISTENCY`, but the adjustment recipe and corporate-action chain are not attributable from the legacy database.
- The two D1 return spikes are artifacts of combining adjusted-equivalent actual-session rows with raw-equivalent non-trading gap rows; they are not economic shareholder returns.
- The venue transition likely exposed a provider normalization or calendar-fill defect. This is more consistent with the evidence than a corporate action or ticker/unit change, but the responsible provider stage is not proven.

### Unresolved items

- Which provider response introduced February 16–22 and whether it supplied rows, repeated a last quote, or was transformed locally.
- Whether the five rows were inserted in one batch or through multiple daily batches.
- The precise adjusted-price formula, event set, and revision version behind the approximately 0.6493 factor.
- Original official HNX deregistration and HOSE first-trading signed documents as immutable bytes.
- Original exchange/provider EOD records for February 14 and 23, including complete raw OHLCV and units.
- Whether any other venue-transition windows were normalized by the same mechanism.

## Alternative explanations

| Explanation | Assessment |
|---|---|
| Corporate action inside the incident window | Not supported. No February 14–23 event was found; the identified cash/stock dividend record date was June 20. A later event can affect retrospective adjusted prices but cannot create genuine trades during the transfer gap. |
| Trading venue transition | Confirmed and central. It explains the no-trading interval and reclassifies February 15 as expected absence. It does not justify persisted EOD rows dated February 16–22. |
| Price-adjustment inconsistency | Strongly supported by the raw-equivalent gap close and the common approximately 0.6493 scaling on the two actual trading dates. Exact adjustment lineage remains unresolved. |
| Missing-session artifact | Partly rejected. February 15 is correctly absent; the material defect is the presence of five rows on later non-trading transfer dates, not a missing February 15 observation. |
| Ticker or price-unit discontinuity | Not supported. Ticker and ISIN remain continuous, par-value evidence remains VND 10,000, and published prices use the same VND/share convention. |
| Source normalization defect | Best-supported mechanism class, specifically gap filling combined with inconsistent basis handling. Attribution to KBS, a package adapter, or local ingestion code remains unresolved. |

## D2 research exposure

D2 identified 67 unique point-in-time label cells whose intervals cross the two CTR transitions:

- February 14 to February 16: 32 cells.
- February 22 to February 23: 35 cells.

Two cells could not be recomputed because the February 15 CTR close is absent. External evidence now shows that the absence is appropriate for a non-trading transfer gap, so a label system must not invent a February 15 close. The remaining label matches only establish arithmetic agreement with the contaminated canonical series, not valid economic returns.

Inherited potential exposure includes rolling/recursive ADX, ATR, RSI, EMA and Donchian features; RS20; breadth; frozen Q70/WFO candidates; neutral-factor evaluation; and downstream portfolio/risk research. No feature, candidate, trade, IC, spread, return, or risk result was rerun, so quantitative impact remains unknown.

## Minimal additional evidence

1. Immutable original HNX deregistration and HOSE listing/first-trading notices, including document identifiers and SHA256.
2. Official exchange EOD records for CTR on February 14 and February 23, and an authoritative no-trade calendar for February 15–22.
3. Original provider raw responses, request parameters, retrieval timestamps, adapter/package versions, and response hashes for this window.
4. Provider documentation defining raw/adjusted fields, non-trading-date behavior, corporate-action revision policy, and venue-transition treatment.
5. Ingestion logs or database backups capable of identifying when row ids 418782–418786 were created.
6. A complete, attributable corporate-action ledger used by the provider to reproduce the approximately 0.6493 factor.

## Remediation decision

Decision: `NO_HISTORICAL_REWRITE`; `REMEDIATION_BLOCKED_PENDING_SOURCE_BYTES`.

Rows 418782–418786 should be treated as confirmed non-trading-gap evidence and excluded from claims of observed CTR EOD trading, but this review does not delete, replace, or manufacture them. The entire February 14–23 CTR window remains `LEGACY_UNVERIFIED` for research reuse. Any later correction must be performed as a separately reviewed, provenance-preserving migration from attributable exchange/provider bytes, with affected labels and derived artifacts requalified rather than silently recomputed.
