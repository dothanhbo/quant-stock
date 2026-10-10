# Research synthesis V1 — ADX14, MOM20 and VOL20 versus H10

2026-10-10 · **EXPLORATORY_ONLY / LEGACY_UNVERIFIED** · STATUS: COMPLETED

The saved results describe weak, temporally unstable associations. Retain the positive MOM20 hypothesis for prospective consideration, conditional on qualified inputs and a design frozen before new outcomes. None of these studies establishes significance, alpha, profitability or independent confirmation. Controlled ADX V1 remains **DATA_BLOCKED**.

## Studies and methodology

All studies use daily cross-sectional Spearman Rank IC: Pearson correlation of ascending average-tie ranks among available finite feature/H10 pairs, minimum five pairs, followed by an equal-weight mean over defined dates. Undefined dates remain recorded. H10 is `close(t+10)/close(t)-1`, targeting exactly the tenth subsequent **stored VNINDEX session**, without substitution. Percentage scaling in existing engine fields preserves ranks. The minimum-five software convention does not qualify a scientific sample.

| Study | Directional hypothesis and feature | Saved mean IC | Eligible / expected cohort-date pairs | Defined / scheduled dates |
| --- | --- | ---: | ---: | ---: |
| VCI ADX pilot | Higher ADX14 trend strength → higher signed H10 return; unchanged `adx@v1(14)`, Wilder EWM initialization. ADX itself has no upward/downward direction. | +0.03793022 | 6,330 / 6,810 (92.9515%) | 645 / 681 |
| MOM20 | Higher `close(t)/close(t−20)−1` → higher H10 return; exact-session endpoints, no filling. | +0.01420234 | 183,710 / 203,400 (90.3196%) | 2,004 / 2,034 |
| VOL20 | Lower volatility → higher H10 return; score `−VOL20`, sample standard deviation (`ddof=1`) of 20 simple returns ending at t; requires 21 consecutive exact-session closes. | −0.00607367 | 182,729 / 203,400 (89.8373%) | 2,004 / 2,034 |

VCI ADX uses **VHM, PHR, CTR, TPB, DGW, MSB, TCH, VIB, VPB, DSE**, captured 2026-10-09 for 2024-01-01–2026-09-30. Defined dates are **2024-02-07–2026-09-16**, with 9–10 eligible stocks. Missingness includes 120 DSE pre-history rows, 260 ADX warm-up exclusions and 100 immature H10 labels; 36 dates have undefined IC.

MOM20/VOL20 use the frozen **FIXED_COHORT_V1 / EXISTING_TRACKED_UNIVERSE** of 100 tickers, not verified historical VN100 membership. Their shared private market.db snapshot was captured **2026-10-10T05:23:57.942372+00:00**, covering **2018-08-07–2026-10-06**. Defined dates are **2018-09-05–2026-09-22**; daily eligible counts are 82–100 and 81–100 respectively. Each retains 20 initial and 10 final undefined dates. Both have 16,209 unavailable market rows; tighter complete-window requirements reduce VOL20 eligibility. Positive IC dates: **MOM20 1,092/2,004 (54.4910%)**, **VOL20 981/2,004 (48.9521%)**. Neither series has zero IC dates.

## Compatibility and chronology

**Do not rank the VCI ADX result against MOM20/VOL20.** Input bytes, providers/provenance, cohort sizes, histories and eligibility differ. Sharing the H10 formula does not make those samples comparable. Earlier legacy ADX exposure also prevents an independent-replication interpretation.

MOM20/VOL20 have identical input bytes, fixed membership, scheduled dates and exact H10 target dates. Their eligible symbol sets can differ. Pearson time-series correlation of their saved daily Spearman ICs is **−0.05571482**, over **2,004** matched defined dates, **2018-09-05–2026-09-22**. There are no unmatched scheduled/defined dates and 30 jointly undefined dates; no filling or zero substitution was used. This is small linear co-movement of two statistic series, not feature independence, a combined-signal result or evidence of diversification. Shared outcomes, dependence and differing eligible pairs limit its interpretation.

Calendar-year means below are descriptive; 2018 and 2026 are partial years. MOM20/VOL20 entries reconcile with their saved annual artifacts. VCI ADX annual entries are newly aggregated from its **saved daily ICs**, without recalculating features or returns, and are shown separately.

| Year | MOM20 defined dates | MOM20 mean IC | VOL20 defined dates | VOL20 mean IC |
| --- | ---: | ---: | ---: | ---: |
| 2018 partial | 83 | +0.08182792 | 83 | +0.03496581 |
| 2019 | 247 | +0.01390083 | 247 | +0.00720737 |
| 2020 | 252 | +0.05583582 | 252 | −0.04745170 |
| 2021 | 249 | +0.04868348 | 249 | −0.08755708 |
| 2022 | 249 | −0.02819500 | 249 | +0.02887038 |
| 2023 | 249 | +0.02360073 | 249 | −0.09573938 |
| 2024 | 250 | −0.01949556 | 250 | +0.04190375 |
| 2025 | 249 | +0.01216754 | 249 | +0.01777212 |
| 2026 partial | 176 | −0.02822956 | 176 | +0.10599271 |

MOM20 changes annual sign **five times** across eight transitions, VOL20 **four times**; each has six positive and three negative annual means. MOM20 is negative in 2022, 2024 and partial 2026. VOL20 is negative in 2020, 2021 and 2023, then positive in 2024–partial 2026. Favorable subperiods cannot establish a stable association or justify reversing the predefined VOL20 score.

| VCI ADX year | Defined dates | Mean IC |
| --- | ---: | ---: |
| 2024 partial | 224 | −0.04590529 |
| 2025 | 249 | +0.10812999 |
| 2026 partial | 172 | +0.04548495 |

The ADX pilot changes annual sign once; its aggregate positive result includes an initially negative subperiod. These rows do not support a comparison with the larger cohort.

## Decision table

These are bounded prioritization judgments following exposure to all saved results, not hypothesis acceptance or inferential rejection.

| Hypothesis | Decision | Basis |
| --- | --- | --- |
| Higher ADX14 → higher H10 | **INSUFFICIENT_EVIDENCE** | Small purposive, incompatible pilot; annual reversal and unresolved input qualification. Preserve evidence and existing governance. |
| Higher MOM20 → higher H10 | **RETAIN_FOR_PROSPECTIVE_CONSIDERATION** | Weak positive overall association in the fixed 100-ticker sample; frequent annual reversals demand independent validation. |
| Lower VOL20 → higher H10 | **DO_NOT_PRIORITIZE** | Overall sign opposes the predefined direction and annual signs reverse repeatedly. Preserve the negative finding; do not flip direction or tune parameters. |

**One recommended prospective hypothesis:** higher fixed **MOM20** is associated with higher exact-session **H10** returns, evaluated by equal-date cross-sectional Spearman IC on unseen prospective observations. This is a candidate, not an activated experiment or a change to the approved ADX program. Source/price-basis qualification, a fixed cohort/calendar/missingness policy, enrollment and dependence-aware reporting must be approved before collection/evaluation. No numerical precision or power target is inferred here.

## Limits, identity and reproduction

All price-adjustment bases and authoritative corporate-action/lifecycle coverage remain unresolved. Labels are observed-basis price returns, not verified RAW or total economic returns. VCI has attributable acquisition metadata but no preserved original provider bodies. The market.db snapshot lacks a source provenance ledger; its historical provider must not be assumed from today's updater. Current-vintage revisions/publication timing, ticker continuity and official session completeness remain unverified. Retrospective current-cohort selection creates survivorship limitations; overlapping H10 labels and dependent dates preclude treating the date count as independent information. Software checks and hash matches establish artifact consistency, not scientific validity. Redistribution rights remain unverified; inputs stay private.

| Preserved input | SHA256 | Snapshot identity |
| --- | --- | --- |
| VCI `isolated_vci_market.sqlite` | `69281165b1cac4a0d05e93fc2f9235a9f44172b7d52ad363e670c703d6999fdb` | `b2ee38f8490368ad918f1d8de8e298a1e1a774e2731462ef8a85792e9d64fcb1` |
| Shared MOM20/VOL20 `inputs/market.sqlite` | `75704e29fe5a3bcf44dba754b42264664523f3270b2eaeefcc7c6812f4055d01` | `28a7609b53682484fecf1c802db6f11e295b9445460e99097720fae1015557ba` |

Exact cohort membership SHA256: `a56872b859b018d8a8dfb7f034b15ceb9a68ea2805aeb5c7150fa53204d14faf`. Baseline HEAD is `b76e1d208ed2f18364ca9d79bd2127af15d7544b`; frozen module/runner hashes and each configuration identify the actual study code, including prior dirty-tree dependencies. HEAD alone is insufficient.

Source notes: [ADX](C:/Users/hello/Desktop/quant-stock/research/alpha_hypotheses/vci_adx14_h10_exploratory_20261009/RESEARCH_NOTE.md), [MOM20](C:/Users/hello/Desktop/quant-stock/research/alpha_hypotheses/mom20_h10_exploratory_20261010/outputs/RESEARCH_NOTE.md), [VOL20](C:/Users/hello/Desktop/quant-stock/research/alpha_hypotheses/vol20_h10_exploratory_20261010/outputs/RESEARCH_NOTE.md).

[source_manifest.json](C:/Users/hello/Desktop/quant-stock/research/alpha_hypotheses/research_synthesis_v1_20261010/outputs/source_manifest.json) records **91** verified source files, including exact input, configuration, frozen-code, cohort, summary, daily-series, annual-series and verification hashes. [synthesis.json](C:/Users/hello/Desktop/quant-stock/research/alpha_hypotheses/research_synthesis_v1_20261010/outputs/synthesis.json) preserves full precision and validation counts. All current checksum entries match (ADX 5, MOM20 41, VOL20 10), as do the 26 frozen modules per study.

Bookkeeping caveat: VOL20's older verification records checksum-manifest digest `f31ec78027308ba8f2798f407c3e4cc6a2e3fb8e44b3cf5eba53073480f79407`; current digest is `18f1bd65b8cbce42317c737634b446ea715b2679a051e584b8c6a60a82600c10`. Removing only the later `verification.json` entry reproduces the older digest exactly. All result hashes match; no existing manifest was rewritten.

From the repository root, replay **only the saved-artifact synthesis** into a nonexistent directory:

```powershell
& .\.venv\Scripts\python.exe -B .\research\alpha_hypotheses\research_synthesis_v1_20261010\synthesize.py --output C:\Temp\quant-stock-synthesis-v1-replay
```

The standard-library script verifies identities, reconciles saved means/counts/annual slices and computes the declared correlation. It blocks SQLite, sockets, subprocesses and URL access; preserved SQLite files are read only as bytes for SHA256, with **zero OHLCV queries**. It recreates the numeric synthesis and source manifest; this note is the interpretation of those outputs. No study replay, new factor experiment, provider call, production edit, commit, push, merge or activation occurred.
