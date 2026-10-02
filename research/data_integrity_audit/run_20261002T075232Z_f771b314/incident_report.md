# Market Data Integrity Incident Audit

## Run

- Run ID: `run_20261002T075232Z_f771b314`
- Created: `2026-10-02T07:52:32.324549+00:00` UTC
- Database: `C:\Users\hello\Desktop\quant-stock\data\market.db`
- SHA-256 before: `F771B314EF2094373C3753DEF45223DD6E61AEC84E15A369FCD52DA585265FB6`
- SHA-256 after: `F771B314EF2094373C3753DEF45223DD6E61AEC84E15A369FCD52DA585265FB6`
- SQLite: URI `mode=ro`, `PRAGMA query_only=ON`

## Coverage and denominators

- VNINDEX calendar: 2018-08-07 through 2026-10-01 (2,031 sessions).
- Symbols: 101 total; 100 equities; VNINDEX audited separately.
- Raw price rows: 189,270; deduplicated rows: 189,270; duplicate symbol/date groups: 0.
- Eligible adjacent pairs: 188,821 (186,791 equity, 2,030 VNINDEX).
- Flagged: 33 at >=15%, 6 at >=25%, 3 at >=40%.
- Affected symbols at >=15%: 15.
- VNINDEX flags at >=15%: 0.

## VHM incident

The stored pair is reproducible: 2026-07-28 close 131.90 to 2026-07-29 close 70.00, or **-46.929492039424%**. The dates are consecutive VNINDEX sessions and the full source rows plus neighbors are preserved in `vhm_incident.csv`.

Classification: **MIXED_ADJUSTMENT_SUSPECTED**. This is not upgraded to verified because the database has no attributable upstream batch, adjustment mode, corporate-action record, or stable security identifier.

## Classification counts

- MIXED_ADJUSTMENT_SUSPECTED: 1
- POTENTIAL_CORPORATE_ACTION: 3
- UNRESOLVED: 29

`POTENTIAL_CORPORATE_ACTION` is only a diagnostic persistence pattern, not verified event attribution. All threshold values are investigation triggers, not economic-validity rules.

## Same-date clusters at the 15% trigger

- 2021-01-29: 2 symbols (CTR, SIP)
- 2026-08-28: 2 symbols (PHR, TCX)

## Research implications

- Price-derived features potentially affected: ADX14, ATR14, RSI14, EMA distances/trends, Donchian/breakout state, short/medium-horizon returns, relative strength, breadth and regime inputs.
- Evaluation families potentially affected: point-in-time factor panels, forward-return labels, candidate/Q70 historical evaluations, frozen-Q70 WFO artifacts, portfolio/risk studies and any prospective label using an unresolved interval.
- This audit does not quantify outcome changes and does not rerun or invalidate an experiment automatically.

## Limitations

- No provider, event service or network source was queried.
- Missing sessions were not imputed. 5 flagged pairs span at least one missing VNINDEX session and are explicitly marked.
- Large legitimate market movements remain possible.
- The local schema cannot distinguish raw, adjusted or mixed provider batches.
- No historical value or provenance status was changed.
