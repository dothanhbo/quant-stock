# Phase D2 — Root-cause triage and research impact mapping

- Run: `run_20261002T080507Z_d2_f771b314`
- D1 source: `C:/Users/hello/Desktop/quant-stock/research/data_integrity_audit/run_20261002T075232Z_f771b314`
- Canonical database: `C:/Users/hello/Desktop/quant-stock/data/market.db`
- SHA-256 before/after: `F771B314EF2094373C3753DEF45223DD6E61AEC84E15A369FCD52DA585265FB6` / `F771B314EF2094373C3753DEF45223DD6E61AEC84E15A369FCD52DA585265FB6`
- SQLite: `URI mode=ro; PRAGMA query_only=ON`

## Result

**PASS.** Six investigative triggers were triaged, all 348 calendar exceptions were preserved, and research lineage was mapped without changing the database or rerunning research. These findings do not verify corruption and do not promote Phase 7.

## High-severity incidents

| Priority | Symbol | Pair | Change | D1 class | D2 adjacency finding |
|---|---|---|---:|---|---|
| P1 | SIP | 2019-06-12 → 2019-06-13 | +39.386189% | UNRESOLVED | ADJACENT_RAW_AND_VNINDEX_SESSIONS |
| P1 | SIP | 2019-06-21 → 2019-06-27 | +30.063796% | POTENTIAL_CORPORATE_ACTION | NOT_ADJACENT_RAW_SYMBOL_OBSERVATIONS_VNINDEX_CALENDAR_FILTER_ARTIFACT |
| P0 | CTR | 2022-02-14 → 2022-02-16 | +54.009559% | UNRESOLVED | ADJACENT_RAW_SYMBOL_OBSERVATIONS_WITH_MISSING_SYMBOL_SESSION |
| P0 | CTR | 2022-02-22 → 2022-02-23 | -31.781609% | POTENTIAL_CORPORATE_ACTION | ADJACENT_RAW_AND_VNINDEX_SESSIONS |
| P0 | VHM | 2026-07-28 → 2026-07-29 | -46.929492% | MIXED_ADJUSTMENT_SUSPECTED | ADJACENT_RAW_AND_VNINDEX_SESSIONS |
| P0 | PHR | 2026-08-27 → 2026-08-28 | -45.688817% | POTENTIAL_CORPORATE_ACTION | ADJACENT_RAW_AND_VNINDEX_SESSIONS |

VHM remains **MIXED_ADJUSTMENT_SUSPECTED**, not verified. SIP 2019-06-21→27 is not adjacent in raw SIP history: 2019-06-24, 25 and 26 observations exist and were excluded only because local VNINDEX rows are absent. CTR 2022-02-14→16 is raw-adjacent but spans one valid VNINDEX session with no CTR row.

## Calendar exceptions

The 348 rows cover 91 symbols on four weekdays: `2019-06-24` (86), `2019-06-25` (85), `2019-06-26` (86), `2021-08-23` (91). No exchange/listing segment or authoritative exchange calendar is stored. Broad cross-sectional coverage with no VNINDEX row supports **BENCHMARK_CALENDAR_GAP_SUSPECTED**, not an automatic claim that the equity rows are invalid.

## Research lineage

The persisted PIT outcome panel has 111 unique symbol/date/horizon label cells whose intervals contain a >=25% transition. Of these, 108 reproduce the persisted return from current local closes; two CTR cells are explicitly unavailable because 2022-02-15 has no CTR close; and one PHR 20-session cell differs (persisted return implies a 31.75 close on 2026-09-17, current DB stores 31.70). The panel also has 6,189 unique label cells spanning at least one absent-VNINDEX date. Exact counts establish dependency and one row-version discrepancy, not numerical bias or corrected performance.

| Family | Status | Qualification |
|---|---|---|
| ADX_ATR_RSI_EMA_DONCHIAN | CONFIRMED_DEPENDENCY | Qualify affected-symbol windows as LEGACY_UNVERIFIED until source attribution; do not infer alpha impact. |
| RELATIVE_STRENGTH_20D | CONFIRMED_DEPENDENCY | Preserve source/date qualifications and missing-session counts. |
| BREADTH_AND_MARKET_REGIME | CONFIRMED_DEPENDENCY | Treat regime/calendar and affected-member breadth lineage as qualified, not invalidated wholesale. |
| HISTORICAL_CANDIDATES_Q70_WFO | CONFIRMED_DEPENDENCY | Add LEGACY_UNVERIFIED price/CA warning before reuse and retain existing survivorship warning. |
| POINT_IN_TIME_FORWARD_RETURN_LABELS | CONFIRMED_DEPENDENCY | Affected labels require provider/event qualification; do not treat UNKNOWN adjustment semantics as valid raw-return evidence. |
| NEUTRAL_FACTOR_EVALUATION | CONFIRMED_DEPENDENCY | Descriptive use only with explicit price-adjustment/corporate-action and calendar-gap warning. |
| PORTFOLIO_AND_RISK_RESEARCH | CONFIRMED_DEPENDENCY | Carry the upstream LEGACY_UNVERIFIED qualification through portfolio/risk summaries. |
| OLDER_CUSTOM_WFO_AND_ABLATION_ARTIFACTS | INSUFFICIENT_LINEAGE | Do not present as source-verified; require artifact-specific lineage audit. |
| ACTIVE_SCANNER_AND_PAPER_SIGNAL_PREPARATION | POTENTIAL_DEPENDENCY | Operationally surface the incident warning; do not mutate trading state in this audit. |
| PAPER_PROSPECTIVE_AND_FORWARD_PROTOCOL_EVIDENCE | NOT_IDENTIFIED | Keep populations separate and inspect protocol-specific source manifests if an incident-overlap question arises. |
| RESEARCH_FACING_WARNINGS | CONFIRMED_DEPENDENCY | Smallest correction is a non-mutating reuse/presentation warning bound to affected artifacts; do not rewrite old artifacts. |

## Priority source-verification questions

1. For VHM 2026-07-29, PHR 2026-08-28 and both CTR February 2022 transitions, what official event terms, ex-dates and security identifiers apply?
2. Which exact provider endpoint/version/batch produced each side of every transition, and were raw and adjusted conventions mixed?
3. What upstream price unit was supplied, and which transformations were applied before persistence?
4. Why are local VNINDEX rows absent on 2019-06-24/25/26 and 2021-08-23 while 85–91 equity rows exist? Are attributable benchmark rows available?
5. For SIP June 2019, do listing phase, illiquidity, price limits or corporate actions explain the stepwise sequence?
6. Can each ticker be bound to a stable security identity across the incident?

## Smallest justified corrective action

Acquire and preserve attributable, versioned provider/event evidence for the P0 incidents and the four VNINDEX calendar dates. Until then, add a non-mutating `LEGACY_UNVERIFIED` qualification at historical artifact reuse/presentation boundaries, with the incident run identity and affected dates. Do not rewrite prices, quarantine symbols, or rerun performance automatically.

## Limits
- No provider, exchange calendar, or corporate-action source was queried.
- D1/D2 thresholds do not prove corruption.
- Current database hash differs from historical artifact database hash; exact current-row formula matches do not establish complete prefix immutability.
- No feature, signal, Q70, WFO, portfolio, or performance result was recomputed.
- Database schema has no exchange segment, stable security identifier, adjustment mode, provider batch, or corporate-action identity.

**Phase 7 remains BLOCKED.**
