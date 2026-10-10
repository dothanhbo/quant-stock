# Exploratory MOM20–H10 research note

**EXPLORATORY_ONLY / LEGACY_UNVERIFIED — STATUS: COMPLETED.**

Question: Within the fixed current 100-ticker cohort, does higher trailing 20-session momentum associate with higher subsequent 10-session close returns? The descriptive directional hypothesis is positive Rank IC; no confirmatory claim is made.

Input SHA256: `75704e29fe5a3bcf44dba754b42264664523f3270b2eaeefcc7c6812f4055d01`. Snapshot ID: `28a7609b53682484fecf1c802db6f11e295b9445460e99097720fae1015557ba`. Capture/vintage: inputs/manifest.json. Cohort: FIXED_COHORT_V1 / EXISTING_TRACKED_UNIVERSE; exact members and hashes in inputs/cohort_v1_manifest.json and experiment.json.

All 2034 stored VNINDEX sessions from 2018-08-07 to 2026-10-06 are retained with 100 expected members per session (203,400 rows). No selection by momentum, returns or missingness; no replacements.

MOM20 = close(t)/close(t−20)−1 on the exact benchmark-session index, requiring finite positive endpoints. First 20 sessions lack the lag. H10 uses the existing outcome panel at the exact tenth subsequent stored benchmark session; last 10 sessions are censored. Feature attachment, dataset joins, availability rules and Rank IC use frozen existing engine code. The percent-scaled feature/outcome fields preserve the same ranks as fractional returns. Only this one feature/horizon is evaluated.

Spearman Rank IC uses ascending average ranks for ties and at least five pairwise available finite observations. Constants/insufficient pairs produce undefined IC. Summary weights every defined date equally. Positive share divides strictly positive dates by all defined dates, including zeros. Calendar-year slices were fixed before outcome evaluation.

Equal-date mean Rank IC: **0.0142023394**. Positive-IC share: **54.4910%** (1092/2004; zero: 0).

Defined ICs: **2004/2034** (98.53%); dates 2018-09-05 through 2026-09-22. Eligible pairs: **183,710/203,400** (90.32%); defined-date cross-sections range from 82 to 100.

| Calendar year | All dates | Defined ICs | Equal-date mean IC | Positive share |
|---|---:|---:|---:|---:|
| 2018 | 103 | 83 | 0.08182792 | 61.45% |
| 2019 | 247 | 247 | 0.01390083 | 53.04% |
| 2020 | 252 | 252 | 0.05583582 | 68.65% |
| 2021 | 249 | 249 | 0.04868348 | 60.64% |
| 2022 | 249 | 249 | -0.02819500 | 47.79% |
| 2023 | 249 | 249 | 0.02360073 | 54.22% |
| 2024 | 250 | 250 | -0.01949556 | 51.20% |
| 2025 | 249 | 249 | 0.01216754 | 52.61% |
| 2026 | 186 | 176 | -0.02822956 | 41.48% |

Missingness and daily counts are retained in summary.json, daily_ic.csv and observations.csv.gz. Detailed lag reasons distinguish warmup from absent exact endpoints. The input calendar and lineage limitations remain explicit.

Prior VCI ADX14–H10 mean IC +0.03793022 is separate descriptive context. Its dataset hash, 10-stock cohort, evaluation dates and vintage differ; this study makes no numerical superiority claim and runs no ADX comparison. Historical exposure is not an independent validation sample.

Limitations:
- Current tracked 100-ticker cohort applied retrospectively: survivorship/selection bias; no verified historical membership or ticker continuity.
- Unknown provider and price-adjustment basis; ratios describe stored closes, not verified RAW or total economic returns; corporate-action seams unverified.
- Current historical vintage, not original point-in-time data availability; revision history and source timestamps unavailable without a provenance ledger.
- Calendar uses only stored normalized VNINDEX sessions; official session completeness unverified.
- Missing exact endpoints are unavailable, never filled or substituted; intervening missing bars do not invalidate an otherwise available endpoint ratio.
- Overlapping H10 labels, cross-sectional/temporal dependence and variable coverage; no inferential significance, alpha or profitability confirmation.
- Minimum five eligible pairs is the existing software convention, not a scientific sample qualification gate.
- Private local input preserved; external provider retention/publication permissions remain unverified.

Reproduce from the repository root using Python 3.14.6, pandas 3.0.6 and NumPy as recorded in code_manifest.json:

```powershell
.\.venv\Scripts\python.exe -B research/alpha_hypotheses/mom20_h10_exploratory_20261010/run_study.py --output research/alpha_hypotheses/mom20_h10_exploratory_20261010/replay_outputs
```

Do not rerun freeze_inputs.py. Replay reads only inputs/market.sqlite and the preserved cohort/configuration and frozen code_reference; it rejects changed inputs, code and existing output directories. Network access and any other SQLite target are prohibited. Output files bind the input, config and executed code hashes; SHA256.json records their identities. Results are deterministic with the recorded runtime.

Conclusion: descriptive historical association only. EXPLORATORY_ONLY / LEGACY_UNVERIFIED; no p-values, confidence intervals, alpha, profitability, optimization or controlled-experiment activation.
