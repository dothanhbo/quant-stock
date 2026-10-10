# Exploratory VOL20–H10 research note

**EXPLORATORY_ONLY / LEGACY_UNVERIFIED — STATUS: COMPLETED.**

Predefined hypothesis: Lower realized volatility over the previous 20 daily simple returns ending at t is associated with higher subsequent exact-session H10 close returns.

Input SHA256: `75704e29fe5a3bcf44dba754b42264664523f3270b2eaeefcc7c6812f4055d01`; snapshot ID: `28a7609b53682484fecf1c802db6f11e295b9445460e99097720fae1015557ba`. Exact Cohort V1: experiment.json and the preserved MOM20 cohort manifest. All 2034 stored VNINDEX dates, 2018-08-07 through 2026-10-06, and all 100 fixed members are retained.

Daily simple returns are close(t)/close(t−1)−1 on the exact stored benchmark index. VOL20 is sample standard deviation (ddof=1) of the twenty returns r(t−19)..r(t), requiring 21 finite positive closes with no filling. Score is −VOL20; a positive IC describes the predefined lower-volatility direction. H10 uses the unchanged exact-session outcome panel; observed-basis labels are not verified RAW returns.

Generic existing observation/feature/outcome/dataset panels retain availability and missingness. Rank IC reuses the unchanged existing ascending average-tie ranking and Pearson primitives, minimum five eligible pairs, and constant/undefined rules. The built-in factor whitelist is unchanged; VOL20 is explicitly named. Defined daily ICs receive equal weight; positive frequency includes zero IC dates in the denominator. Calendar years were fixed before outcomes.

Mean Rank IC: **-0.0060736659**. Positive IC frequency: **48.9521%**, 981/2004 defined dates; zero: 0.

Eligible observations: **182,729/203,400** (89.84%). Defined IC dates: **2004/2034** (98.53%), 2018-09-05 through 2026-09-22. Undefined dates: 30. Defined-date pair counts: {'minimum': 81, 'maximum': 100}.

| Calendar year | All dates | Defined ICs | Mean IC | Positive share |
|---|---:|---:|---:|---:|
| 2018 | 103 | 83 | 0.03496581 | 71.08% |
| 2019 | 247 | 247 | 0.00720737 | 52.23% |
| 2020 | 252 | 252 | -0.04745170 | 38.10% |
| 2021 | 249 | 249 | -0.08755708 | 32.53% |
| 2022 | 249 | 249 | 0.02887038 | 52.21% |
| 2023 | 249 | 249 | -0.09573938 | 31.73% |
| 2024 | 250 | 250 | 0.04190375 | 58.80% |
| 2025 | 249 | 249 | 0.01777212 | 47.79% |
| 2026 | 186 | 176 | 0.10599271 | 80.11% |

Focused synthetic checks passed: independently calculated sample standard deviation, missing intermediate bar, causal prefix/future perturbation, tied ranks/constant/minimum-size behavior, and exact H10 target without sliding over a missing stock close. validation.json retains expected and actual arithmetic values.

No raw IC superiority comparison with MOM20 or ADX is made. Data and calendar match MOM20, but feature eligibility can differ; earlier exploratory exposure and dependence prohibit independent-confirmation claims.

Limitations:
- Fixed current tracked ticker cohort applied retrospectively; survivorship, selection and ticker-identity limitations.
- Unknown provider, adjustment basis and corporate-action treatment; observed-basis close returns, not verified RAW or total returns.
- Current historical vintage; original publication times, revisions and provenance ledger unavailable.
- Stored VNINDEX sessions are not an independently verified official calendar; missing bars never filled.
- Overlapping H10 outcomes and cross-sectional/temporal dependence; no significance, alpha or profitability claims.
- Prior MOM20/ADX outcomes already exposed; descriptive study is not independent confirmation.
- VOL20 requires all 20 exact-session returns; its eligible pairs can differ from MOM20 even on the same input.
- Private data retention only; redistribution rights remain unverified.

Reproduce with the recorded Python/pandas/NumPy runtime, after restoring both complete private studies as sibling directories:

```powershell
.\.venv\Scripts\python.exe -B research/alpha_hypotheses/vol20_h10_exploratory_20261010/run_study.py --output research/alpha_hypotheses/vol20_h10_exploratory_20261010/replay_outputs
```

Replay rejects altered input/cohort/engine files and existing output directories, reads only the frozen MOM20 SQLite snapshot, and prohibits networking. No canonical store, strategy, scanner or Daily code is used. code_manifest.json records runner/config/engine hashes and versions; SHA256.json records deterministic result identities. observations.csv.gz retains every expected row, score, label, window count, availability and reasons.

Conclusion: descriptive historical association only, EXPLORATORY_ONLY / LEGACY_UNVERIFIED. No parameter search, significance, alpha, profitability, provider collection or controlled experiment.
