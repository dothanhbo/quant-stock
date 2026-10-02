# First Prospective Experiment: Researcher Decision Memo V1

**Status:** CANDIDATE APPROVED — REGISTRY RECORD REMAINS DRAFT — NOT FROZEN — NOT AUTHORIZED TO RUN

**Prepared:** 2026-10-02

**Historical discovery cutoff:** 2026-09-17

**Registry source:** `research/alpha_hypotheses/registry_v1.json`

**Purpose:** present a bounded design for human review before any registration, protocol freeze, experiment freeze, or eligible formation.

This memo does not select a production policy, validate alpha, authorize capital, reinterpret existing Paper or Forward observations, or change any research contract.

## 1. Decision-state legend

- **APPROVED** — explicitly accepted by the researcher; this does not change Registry lifecycle state.
- **PROPOSED** — a defensible baseline suitable for review, not yet approved.
- **PENDING_NUMERICAL_APPROVAL** — the method is scoped, but a human must approve its numerical value or deterministic calculation rule.
- **REQUIRES EVIDENCE** — a declaration needs a documented calculation or provenance improvement before freeze.
- **BLOCKED** — the experiment must not start until the stated dependency is resolved.

## 2. Worktree and evidence preservation

### Current checkpoint

- Branch: `main`
- HEAD inspected: `8ed159404e3a366eaf3fd134acfbd92340bd2fec`
- Phase 2–5 governance work is currently untracked under:
  - `quantlab/hypotheses/`
  - `research/alpha_hypotheses/registry_v1.json`
  - `tests/test_alpha_hypothesis_registry.py`
  - `tests/test_hypothesis_falsification_protocol.py`
  - `tests/test_controlled_experiment_design.py`
- Manager/QuantCtl changes already present in the working tree are separate work and must be preserved.
- Canonical databases (`data/*.db`) and `research_results/` are ignored runtime/evidence assets. They must not be included by a broad Git add.

### Safe checkpoint procedure — not executed

1. Review `git status --short` and the exact diff again.
2. Preserve ignored evidence independently before any cleanup. Record file hashes and copy the specific canonical artifact directories to controlled storage; do not move or rewrite the originals.
3. Stage governance work only with explicit paths, including this memo. Never use `git add .`.
4. Verify `git diff --cached --check`, `git diff --cached --stat`, and `git diff --cached` before committing.
5. Checkpoint unrelated Manager/QuantCtl work separately.
6. Never force-add `data/*.db`, `research_results/`, `.env`, `.venv`, or credentials.

**PROPOSED:** checkpoint the governance/memo work separately from the pre-existing Manager/QuantCtl work.

**BLOCKED:** no registry or protocol activation may rely solely on ignored artifacts without preserving their hashes and locations.

## 3. Candidate decision and alternatives

Candidate assessment is based on scientific and operational feasibility, not historical profitability.

### A. Neutral ADX directional association — APPROVED candidate

**APPROVED research question.** Does ADX exhibit a stable positive cross-sectional predictive association with future equity returns?

**APPROVED scope.** Predictive association only. Candidate approval does not register the hypothesis, freeze a protocol, authorize an experiment, validate alpha, or authorize production use.

**Available evidence.** Retrospective Phase 5 panel-factor and research-decision artifacts through 2026-09-17 motivated the draft. They are discovery evidence only and are not eligible prospective confirmation.

**Prospective requirement.** Persist the complete same-date point-in-time eligible cross-section, its ADX value, universe identity, exact formation session, and later exact-session outcome. One daily cross-sectional statistic must be produced only after all outcomes for the declared horizon mature.

**Feasibility.** The statistical target can be a daily cross-sectional rank association. It does not require a simulated portfolio, order sizing, or fill model. Same-date stocks are not treated as independent observations; the daily cross-section is the analysis unit.

**Corporate-action/execution dependency.** Execution modelling is not required for the primary association. Return validity still depends on known price-adjustment and corporate-action semantics. The current local history does not provide that provenance.

**Expected frequency and maturity.** At most one independent formation-date statistic per completed VNINDEX session; maturity follows the single horizon eventually approved before freeze. Overlapping forward-return windows induce serial dependence.

**Operational complexity.** Moderate: a new protocol-bound, full-cross-section prospective evidence stream is needed. Existing Forward V1 top-selection records are not sufficient and remain excluded.

**Unresolved assumptions.** Primary horizon, minimum meaningful rank association, precision/sample requirement, coverage rule, dependence method, information-exposure rule, and corporate-action treatment.

### B. Neutral ADX-only portfolio

**Research question.** Does the frozen ADX-ranked, equal-weight selection produce positive VNINDEX-relative forward outcomes under a newly frozen prospective protocol?

**Available evidence.** Retrospective portfolio synthesis informed the draft. Forward V1 already records ADX-only formations, but it was designed before this registry/protocol and cannot be relabeled as evidence for a later preregistration.

**Prospective requirement.** A new protocol/version must freeze selection, budget, weighting, universe, formation timing, horizon, benchmark, continuity, and outcome rules before eligible formations.

**Feasibility.** A close-to-close theoretical portfolio outcome is measurable, but formations overlap and selected securities within a formation are dependent. Executable net performance is not established.

**Corporate-action/execution dependency.** Price adjustment and corporate-action provenance affect returns. Any claim about realized capacity, fills, turnover cost, or executable PnL additionally requires evidence not currently available.

**Expected frequency and maturity.** At most one portfolio formation per completed market session; exact-session maturity at the approved horizon. The current Forward V1 observations remain outside the new experiment.

**Operational complexity.** Medium to high: new protocol-bound formations plus portfolio-level outcome aggregation and dependence-aware inference.

**Unresolved assumptions.** Budget, horizon, portfolio outcome aggregation, missing constituents, minimum economic effect, friction interpretation, and whether a theoretical rather than executable claim is acceptable.

### C. Frozen Q70 historical policy

**Research question.** Does the already frozen Q70 Hybrid Trend/Donchian policy produce positive net future portfolio performance under an independently registered prospective protocol?

**Available evidence.** Instrumented historical folds and policy-parity artifacts through 2026-09-17. Those results were observed during policy development and remain retrospective. Existing Paper snapshots are accounting evidence, not independent preregistered confirmation.

**Prospective requirement.** Bind the precise Q70 policy, entry/exit lifecycle, account epoch, capital, position sizing, transaction costs, universe, evidence ledger, and stopping rule before the first eligible signal.

**Feasibility.** Statistical power is likely limited by sparse executed trades and path dependence. A trade is not an independent unit when positions share dates, capital, and market conditions.

**Corporate-action/execution dependency.** Highest of the three candidates. Net-performance claims depend on reliable fills, friction, account reconciliation, adjustment provenance, and corporate-action-safe quantities.

**Expected frequency and maturity.** Irregular; evidence arrives only when accepted signals become executed and later closed. Calendar time required for a defensible sample is unknown.

**Operational complexity.** High. This is the least suitable first test while execution and provenance limitations remain unresolved.

**Unresolved assumptions.** Prospective universe, account and reset policy, eligible trade unit, minimum sample, duration, friction/capacity model, stopping rule, corporate actions, and separation from already exposed Paper evidence.

## 4. Approved scope and primary estimand

### Human decision recorded

- **APPROVED:** candidate `neutral-adx-directional-association-v1`.
- **APPROVED:** positive expected direction.
- **APPROVED:** predictive-association-only scope.
- **UNCHANGED:** the Registry record remains `DRAFT`; its registration timestamp remains unset.

The approved wording replaces the earlier candidate-selection proposal. No horizon, threshold, sample size, coverage level, decision boundary, registration, protocol, or experiment has been approved.

### Proposed primary statistic

For each eligible formation session `t` and approved horizon `H`:

1. Freeze the point-in-time eligible symbol cross-section after the completed session.
2. Record each symbol's same-date `adx_14` and formation close.
3. At the exact `H`-th later completed VNINDEX session `T(t,H)`, calculate raw stock forward return:

   `R(i,t,H) = close(i,T(t,H)) / close(i,t) - 1`.

4. Calculate one cross-sectional Spearman Rank IC:

   `IC(t,H) = Spearman(adx_14(i,t), R(i,t,H))`.

**PROPOSED primary metric:** the mean of the defined daily `IC(t,H)` values over the frozen prospective window. Each formation-date IC is one observation. Individual stock-date rows are not independent observations.

**PROPOSED primary outcome:** raw stock forward return.

**PROPOSED benchmark context:** exact-session VNINDEX price return, reported descriptively and not treated as a second confirmation.

For a formation date with exact VNINDEX return `B(t,H)`, define excess return as:

`E(i,t,H) = R(i,t,H) - B(t,H)`.

Because `B(t,H)` is the same scalar for every stock in that formation-date cross-section, subtracting it preserves ordering and ties:

`rank(E(i,t,H)) = rank(R(i,t,H))`.

Therefore:

`Spearman(adx_14(i,t), E(i,t,H)) = Spearman(adx_14(i,t), R(i,t,H))`.

Raw-return Rank IC and VNINDEX-relative Rank IC are algebraically equivalent for the same eligible rows. They are not independent evidence, must not be combined as two tests, and must not be described as replication. VNINDEX remains useful only as market-context metadata and for interpreting the level of realized returns.

### Two distinct positive-association claims

Let `[L, U]` be the approved dependence-aware confidence interval for the mean daily Rank IC and let `delta > 0` be the separately approved minimum research-relevance effect.

- **A. Positive association:** supported when `L > 0`.
- **B. Meaningful positive association:** supported only when `L > delta`.

Claim B implies Claim A, but Claim A does not imply Claim B. Statistical positivity must not be described as economic significance, portfolio utility, production readiness, or even research relevance above `delta`.

The exact approved hypothesis is the positive-association claim. Its proposed falsification region is therefore `U < 0`, not failure to exceed `delta`. A valid interval that crosses zero is inconclusive. A valid interval wholly above zero but not wholly above `delta` supports positive direction while leaving meaningful research relevance unestablished. Under the current V1 governance contract, that case remains an overall `INCONCLUSIVE` disposition because full `SUPPORTED` requires both statistical and relevance criteria; both component conclusions must be reported explicitly. Strict versus inclusive endpoint handling remains pending approval.

## 5. Approval matrix

| Decision | Status | Proposed baseline and reasoning | Alternatives / assumptions | Required input before freeze |
|---|---|---|---|---|
| Candidate | **APPROVED** | `neutral-adx-directional-association-v1`; association-only design avoids portfolio/fill assumptions | Approval is not Registry registration | None; retain DRAFT until explicit registration action |
| Research question and direction | **APPROVED** | Stable positive cross-sectional predictive association | Does not imply economic utility | None |
| Primary metric/outcome | **PROPOSED** | Daily Spearman Rank IC against raw exact-session stock forward return | Pearson correlation, bucket spreads, or excess Rank IC are not primary; excess Rank IC is algebraically redundant | Human approval of the estimand |
| VNINDEX role | **PROPOSED** | Descriptive exact-session price-return context only | It is non-tradable, excludes dividends/costs/tracking error, and creates no independent Rank IC evidence | Human approval of descriptive-only role |
| A. Primary horizon | **PENDING_NUMERICAL_APPROVAL** | Provisional `H=10` completed VNINDEX sessions because exact-session 10-session maturity already exists operationally and was not chosen from best ADX performance | `H=5` matures faster with less overlap; `H=20` is slower and more dependent. Scientific choice remains underdetermined | Human horizon choice and rationale independent of prospective outcomes |
| B. Universe eligibility | **PROPOSED** | Point-in-time database coverage with at least 50 observed sessions and no more than five VNINDEX sessions stale; exclude VNINDEX; require finite same-date ADX and formation close | Database coverage is local availability, not historical/current VN100; ticker identity is incomplete | Approve eligibility and persist membership identity/count each formation |
| C. Formation timing | **PROPOSED** | One formation after each newly completed VNINDEX session, after close-derived ADX is available; never backfill | Less frequent formation reduces dependence but changes the question | Approve completed-session and no-backfill rule |
| D. Outcome maturity | **PROPOSED** | Exact close on the `H`-th later completed VNINDEX session; no nearest-date substitution | Calendar-day maturity is rejected because it varies with closures | Approve exact-session rule and maturity tail |
| E. Minimum cross-sectional coverage | **REQUIRES_EVIDENCE** | A daily IC is defined only when both an approved minimum symbol count `n_min` and minimum fraction `c_min` of the frozen formation universe have valid mature labels | A count alone behaves differently as universe size changes; a fraction alone may permit unstable small samples | Calibrate `n_min` and `c_min` from structural missingness/null simulations, not historical ADX performance; then obtain numerical approval |
| F. Minimum independent information | **REQUIRES_EVIDENCE** | Require a frozen minimum number of defined formation-date ICs and a precision/power calculation adjusted for serial dependence | Raw stock count is not information count; nominal daily count overstates information under overlap | Provide approved meaningful effect, variance/dependence assumptions, target precision and power |
| G. Prospective window | **PENDING_NUMERICAL_APPROVAL** | Fixed enrollment start and fixed end, followed by `H` sessions for maturity; evaluate once; no extension to rescue sample/results | A fixed formation-count endpoint is operationally flexible but duration becomes uncertain | Approve start rule, end date/session and no-early-stop rule after sample calculation |
| H. Serial/cross-sectional dependence | **PROPOSED** | Collapse each same-date cross-section to one IC; preserve the complete chronological daily-IC series for inference | Stock rows are never treated as independent; daily ICs remain serially dependent | Approve daily IC as independent-analysis unit |
| I. Block bootstrap | **PROPOSED** | Circular moving-block bootstrap of the complete ordered defined-IC series; each resample preserves contiguous formation-date dependence and estimates the mean IC distribution | Stationary bootstrap or HAC inference are alternatives; changing method after exposure is forbidden | Approve bootstrap family and deterministic random-seed/replication policy |
| J. Block-length rule | **PENDING_NUMERICAL_APPROVAL** | Provisional deterministic rule `L = max(H, ceil(N^(1/3)))`, calculated once from final defined-IC count `N`; report a predeclared `2L` robustness interval as descriptive only | `H` protects overlap; cube-root term allows longer serial structure but is a heuristic. Automatic plug-in selection is an alternative | Method review plus human approval of rule; do not choose `L` by the narrowest/result-favorable interval |
| K. Confidence/error target | **PENDING_NUMERICAL_APPROVAL** | Provisional two-sided 95% percentile block-bootstrap interval for mean daily IC; primary directional decision uses its lower/upper endpoints | Studentized or BCa block intervals are alternatives but add assumptions/complexity | Approve confidence level, interval construction and bootstrap Monte Carlo precision |
| L. Multiple-testing family | **PROPOSED** | Exactly one primary factor, raw-return outcome and horizon; family size one, so multiplicity adjustment is the identity operation | Any extra horizon, factor, outcome or alternate inferential method is descriptive unless separately preregistered; stock/excess Rank IC are one test | Approve family definition and secondary-output restrictions |
| M. Minimum research-relevance effect | **REQUIRES_EVIDENCE** | Define `delta > 0` on mean daily Rank IC as the smallest effect worth further research; do not call it executable economic alpha | Costs cannot determine `delta` because no portfolio is defined; historical maximum cannot justify it | Human research-utility rationale plus documented precision calculation |
| N. Support/falsification boundaries | **BLOCKED** | Positive claim A: lower CI `> 0`. Meaningful claim B: lower CI `> delta`. Falsification of the exact positive hypothesis: upper CI `< 0`. Overall V1 `SUPPORTED` requires A and B; otherwise valid mature evidence between boundaries is `INCONCLUSIVE` | Positive-but-not-meaningful evidence is not falsified and must be reported separately. Insufficient sample is not falsification | Approve `delta`, CI method, component reporting and strict/inclusive operators before freezing criteria |
| O. Missingness, delisting and corporate actions | **BLOCKED** | Freeze formation membership; never impute, forward-fill, substitute nearest dates or convert missing to zero; reason-code every missing label; exclude a daily IC if coverage fails | Dropping suspended/delisted names may be informative. Unknown corporate actions can corrupt return labels | Establish corporate-action-safe label condition described in Section 9 |
| P. Information-exposure cutoff | **PROPOSED** | After freeze, expose counts, continuity and integrity metadata only; hide prospective ADX/outcome/IC summaries until fixed window closes and all primary labels mature | Operational diagnostics must not reveal outcome direction | Approve access roles, audit trail and one final unblinding event |

## 6. Structural feasibility evidence

This section uses read-only historical availability through the discovery cutoff solely for planning. It does not calculate returns, Rank IC, factor performance, or horizon profitability. It is not prospective evidence.

### Observed formation and cross-section structure

- Planning interval: 2018-08-07 through 2026-09-17.
- Observed VNINDEX sessions: 2,021, with no duplicate VNINDEX session dates.
- The 50-history/5-staleness database-coverage index contains a union of 100 non-benchmark symbols.
- The first 49 observed VNINDEX sessions are the coverage warmup; the first non-empty coverage and finite-ADX cross-section is 2018-10-16.
- Post-warmup formation sessions: 1,972.
- Post-warmup coverage-member counts: minimum 84, 10th percentile 86, median 93, 90th percentile 97, maximum 100.
- Finite exact-date `adx_14` and formation-close counts have the same reported quantiles: 84 / 86 / 93 / 97 / 100.
- Across 181,189 coverage-member/session pairs, 180,994 have finite exact-date ADX and close: 99.8924% structural availability.
- Missing exact-date ADX/close occurs in 195 pairs across 176 sessions. Per-session availability has a 96.7033% minimum, 100% 10th percentile and 100% median.

These counts demonstrate that a daily cross-sectional statistic is operationally plausible under the existing 50/5 database-coverage rule. They do not justify choosing `n_min`, `c_min`, a horizon, `delta`, or a sample size. Database coverage permits up to five-session staleness, so membership does not guarantee an exact-date feature or close.

### Exact target-close availability by candidate horizon

| Horizon | Mature formation dates within cutoff | Formation-eligible symbol/dates | Exact target closes | Structural availability | Dates with every exact label |
|---:|---:|---:|---:|---:|---:|
| 5 sessions | 1,967 | 180,494 | 180,304 | 99.8947% | 1,793 |
| 10 sessions | 1,962 | 179,994 | 179,759 | 99.8694% | 1,753 |
| 20 sessions | 1,952 | 178,994 | 178,747 | 99.8620% | 1,733 |

The final 5/10/20 formation sessions respectively cannot mature by the historical cutoff. These availability differences are small and must not be used as predictive-horizon selection evidence.

### Continuity and provenance limits

- Observed VNINDEX calendar gaps range from one to ten calendar days; most are one- or three-day gaps. Without an authoritative exchange calendar, this does not prove that every expected market session is present.
- The current database snapshot extends to 2026-10-01, but every post-2026-09-17 observation is outside this planning profile's historical boundary and is ineligible for the future experiment.
- The `prices` schema contains only identifier, symbol, time, OHLC and volume fields. It contains no provider, retrieval, unit, adjustment, batch or fingerprint columns.
- No corporate-action table was found.

**Structural conclusion:** daily formation and maturity capture appears operationally feasible. **Scientific conclusion:** no horizon, coverage threshold, sample size or predictive claim is established. **Provenance conclusion:** valid prospective labels remain blocked without the Section 9 safeguards.

## 7. Minimum-information and precision calculation

No numerical minimum is approved by this memo. Before registration:

1. Approve the smallest research-relevant mean daily Rank IC, `delta`, without using the best historical ADX result.
2. Approve confidence level, target power and maximum acceptable interval half-width.
3. Estimate or conservatively bound daily-IC variance and serial dependence using a null-preserving design study: synthetic data, explicit worst-case bounds, or pre-cutoff data with the factor/outcome association destroyed. These are design inputs, not confirmatory evidence.
4. Simulate the frozen block-bootstrap procedure across candidate `N` values and select the smallest `N` meeting both target power at `delta` and target interval precision under the approved assumptions.
5. Inflate the calendar enrollment window using a separately estimated structural formation/coverage rate, then add the `H`-session maturity tail.
6. Freeze `N`, `n_min`, `c_min`, the calendar/session end, and the no-extension rule before the first eligible formation.
7. Do not revise these requirements after prospective outcomes become visible. A required change creates a new hypothesis/protocol/experiment revision and a new evidence window.

**BLOCKED:** protocol freeze and experiment start until `delta`, precision target, dependence method, block rule, minimum mature sample, coverage threshold, and fixed end/stopping rule are documented.

### Provisional block-rule review

The rule `L = max(H, ceil(N^(1/3)))` is a planning baseline, not an established optimum.

- `H` is a lower bound because adjacent daily ICs reuse future market sessions when forward-return horizons overlap.
- `ceil(N^(1/3))` is an asymptotic heuristic intended to let blocks grow with sample size; it does not estimate the actual dependence length.
- Circular moving blocks assume the daily-IC process is sufficiently stationary/weakly dependent over the frozen window. Structural breaks or long regimes can make the interval too narrow.
- Circular wrapping creates artificial end-to-start adjacency. Its influence declines with a long series but must be disclosed.
- Effective information is not `N` stock rows and should not be approximated mechanically as `N/L` without validation. It is assessed through the frozen resampling design and its precision/power simulation.
- A predeclared longer-block sensitivity, such as `2L`, may diagnose interval fragility but cannot replace the primary interval or be selected after seeing which result is favorable.
- An automatic plug-in block selector or stationary bootstrap is a defensible alternative, but its algorithm, fallbacks and sensitivity rules must be frozen instead of choosing among methods after outcome exposure.

A fixed evaluation end is preferred. The minimum-information count is a validity gate at that end, not a rule allowing collection to continue until a desired result or precision appears. If the fixed window ends below the approved information/coverage requirement, the result is `INSUFFICIENT_EVIDENCE`.

## 8. Inference and falsification consistency

The proposed design is consistent with the current V1 contracts if the pending fields are explicitly frozen:

- `INSUFFICIENT_EVIDENCE` precedes any substantive disposition when the window, mature sample, coverage, or continuity requirement is unmet.
- `FALSIFIED` requires the separate preregistered falsification criterion; absence of support alone resolves to `INCONCLUSIVE`.
- `SUPPORTED` requires both statistical support and the minimum meaningful effect.
- Retrospective artifacts, Paper accounting snapshots, and pre-existing Forward V1 records cannot satisfy the prospective evidence binding.
- Hypothesis registration, protocol freeze, and experiment freeze must all precede the first eligible formation session.
- A completed experiment can set only its evidence disposition and explicit economic interpretation. It cannot set `ALPHA_VALIDATED`, `PRODUCTION_READY`, authorize capital, or replace a production policy.
- Experiment evidence must bind the registered hypothesis revision, protocol identity, evidence-family identity, cutoff and source identities.
- Raw-return and VNINDEX-relative Rank IC must not be counted as two outcomes or two supporting tests because they have identical within-date ranks.
- The block bootstrap must resample formation-date ICs, not individual stock rows, and must keep overlapping-horizon dependence inside contiguous blocks.
- Component claim A and claim B must be reported separately: `L > 0` establishes statistical positivity, while `L > delta` establishes the stronger approved relevance threshold. Failure of B is not falsification of A.

### Contract review result

No additional P0/P1 contract defect was found in this phase. The Phase 5 correction already requires valid completed results to contain evidence identities and prevents economic interpretation of invalid or insufficient evidence. No contract code is changed by this memo.

## 9. Prospective data-validity rules and provenance plan

### 9.1 Fail-closed label eligibility

The formation cross-section is fixed at formation time. Subsequent absence never rewrites membership.

| Feasibility class | Current conclusion |
|---|---|
| Verified and usable | Exact observed-session ordering, exact-date local closes, deterministic database-coverage membership, and same-series dimensionless return arithmetic are available read-only. |
| Usable only under explicit restrictions | A raw-return label is usable only when the security identity is stable and the interval has verified adjustment semantics or complete attributable corporate-action evidence. Exact-session absence remains missing rather than substituted. |
| Unresolved and blocking | Historical/current `prices` rows do not persist adjustment mode, provider batch provenance, corporate-action events, delisting terms or ticker-history identity. The database alone cannot certify corporate-action-safe labels. |

- **Suspension or missing target close:** label is missing with a reason code; no forward fill or nearest-session substitution. The date-level IC is unavailable if approved `n_min`/`c_min` is not met.
- **Incomplete maturity:** the formation remains pending and contributes no zero or partial label.
- **Delisting/ticker change:** require a canonical security-identity mapping and an attributable terminal/continuing price. Without it, the symbol label is unusable and reported separately.
- **Known corporate action:** require a deterministic transformation compatible with the declared price series and event terms. Do not delete an adverse or inconvenient outcome silently.
- **Unknown adjustment semantics:** mark the label provenance unresolved. Unit-scale cancellation does not correct splits, dividends, rights, bonus issues or ticker discontinuities.
- **Universe membership:** use only the formation-date database-coverage membership. This is not historical VN100 and does not prove security master completeness.
- **Reporting:** publish counts for frozen-universe members, factor-available members, mature labels, missing/suspended labels, corporate-action exclusions, delisting/ticker exclusions and defined daily ICs.

**Exact evaluation blocker:** primary evaluation cannot be valid unless every included return interval is supported by either (a) a verified price series whose adjustment/total-return semantics cover the relevant corporate actions, or (b) verified raw prices plus complete attributable corporate-action and security-identity records with a frozen deterministic return transformation. If neither condition is available, affected labels are unusable; if resulting coverage or continuity falls below approved thresholds, the experiment is `INSUFFICIENT_EVIDENCE`; if unresolved labels are included as valid, the evidence is `INVALID_EVIDENCE`.

### 9.2 Future-ingestion hardening

Before eligible prospective outcome capture, persist an immutable ingestion-batch manifest containing:

- provider, product/source and endpoint identity;
- provider package name/version and writer version;
- UTC retrieval timestamp and requested symbol/date/interval parameters;
- source price and volume unit declarations, with `UNKNOWN` allowed but never inferred from plausibility;
- adjustment mode (`RAW`, `ADJUSTED`, provider-defined, or `UNKNOWN`) and whether caller-selectable;
- corporate-action source/version and covered event types, or explicit `UNAVAILABLE`;
- symbol/security identifier and known ticker-history mapping status;
- raw-response content hash, normalized-batch content hash, row count and date/symbol bounds;
- normalization rules, scale conversion, duplicate policy, validation result and parent batch identity;
- immutable raw-response archive location or a documented reason raw preservation is unavailable.

Secrets and access tokens must never enter the manifest. Failed/partial batches must be recorded separately and must not silently merge with verified data.

**BLOCKED:** label-dependent prospective evaluation until the protocol specifies how `UNKNOWN` adjustment/corporate-action evidence affects eligibility.

### 9.3 Historical reconciliation

- Do not retroactively assign provider, adjustment or corporate-action metadata that was not persisted.
- Freeze the existing database hash and bounded snapshot identities as evidence of what was used.
- Reconcile only from independently recoverable raw/provider records and version each reconciled dataset non-destructively.
- Record verification state by batch/date/symbol (`VERIFIED`, `PARTIAL`, `UNKNOWN`) and preserve conflicts.
- Build ticker/security history and delisting/corporate-action tables only from attributable sources.
- Never overwrite historical artifacts or relabel reconciled data as the original input.

### 9.4 Evidence archive preservation

- Preserve each ignored artifact directory with its manifest, content hashes and original relative path.
- Preserve registry, protocol, experiment and result identities together with code commit, environment lock and bounded market-data fingerprint.
- Store immutable raw ingestion batches separately from normalized databases and separately from derived research artifacts.
- Maintain a chain-of-custody index; a missing artifact or hash mismatch makes evidence unavailable or invalid, never silently reconstructed.
- Keep current Forward V1 and Paper evidence under their original identities and limitations; do not merge them into a future experiment family.

## 10. Exposure-control timeline

1. **Historical discovery cutoff — fixed:** 2026-09-17.
2. **Already observed post-discovery data — excluded:** every Paper, Forward V1, market or research observation after the discovery cutoff and before the future eligibility boundary remains previously observable and cannot become prospective evidence for this experiment.
3. **Candidate approval — recorded:** Phase 6B request on 2026-10-02. No exact UTC timestamp is invented by this memo.
4. **Hypothesis registration timestamp — future:** must be persisted by an explicit lifecycle action; candidate approval is not registration.
5. **Protocol freeze timestamp — future:** must follow registration and bind every required declaration.
6. **Experiment freeze timestamp — future:** must follow protocol freeze and bind source identities, validity rules and the information cutoff.
7. **First eligible formation — future:** the first newly completed VNINDEX session strictly after registration, protocol freeze, experiment freeze, the historical cutoff, and any explicitly frozen operational cutoff. No backfill.
8. **Outcome maturity — future:** the exact `H`-th later completed VNINDEX session for each eligible formation. A formation is not evidence before this maturity and all label-validity checks.
9. **Final exposure — future:** one predeclared unblinding after the enrollment window closes and the primary maturity tail completes. Previously observed Forward V1 and Paper records remain excluded permanently.

## 11. Final human-approval package before Phase 7

Checked items reflect the explicit Phase 6B human decision. No unchecked item is approved by creation of this memo.

| Decision | Proposed methodology | Planning evidence | Required numerical input | Remaining blocker | Approval status |
|---|---|---|---|---|---|
| Candidate/question | Neutral ADX positive cross-sectional association; association only | Governance registry and retrospective discovery artifacts establish the question, not prospective validity | None | Explicit registration still required later | **APPROVED** |
| Primary estimand | Mean daily Spearman Rank IC against raw exact-session stock return; VNINDEX descriptive only | Algebraic rank invariance proves excess Rank IC is identical on the same rows | None | Human acceptance of raw-return primary and descriptive benchmark | **PROPOSED** |
| Horizon | One exact-session horizon; provisional `H=10` | 5/10/20 target-close availability is structurally high and similar; no performance evidence was inspected | Approved `H` | Scientific choice remains underdetermined | **PENDING_NUMERICAL_APPROVAL** |
| Universe/formation/maturity | Formation-date 50/5 database coverage; after completed VNINDEX session; exact `H`-th-session maturity | 1,972 post-warmup sessions and 84–100 observed members show operational feasibility | None unless 50/5 itself is reconsidered | Must approve database-availability limitation and no-backfill semantics | **PROPOSED** |
| Cross-sectional coverage | Require both `n_min` valid symbols and `c_min` of frozen formation membership | Historical exact ADX/close availability is 99.8924%, but this cannot set prospective thresholds | `n_min`, `c_min`, continuity tolerance | Null/structural calibration and human approval | **REQUIRES_EVIDENCE** |
| Independent information/window | Fixed enrollment window, minimum defined daily IC count, one final evaluation after maturity tail | Stock count is not independent information; overlap creates serial dependence | `N`, start/end, target power, precision | Precision/power design not yet performed | **REQUIRES_EVIDENCE** |
| Bootstrap/dependence | Circular moving blocks of daily ICs; provisional `L=max(H,ceil(N^(1/3)))` | Method preserves contiguous overlapping-horizon dependence; rule is heuristic | `L` rule, replication count/Monte Carlo tolerance | Method review and numerical approval | **PENDING_NUMERICAL_APPROVAL** |
| Confidence/error/multiplicity | Provisional two-sided 95% interval; one-test primary family | One factor/outcome/horizon avoids hidden multiple selection; raw/excess Rank IC are one test | Confidence level and interval construction | Human approval | **PENDING_NUMERICAL_APPROVAL** |
| Research relevance | Separate `delta > 0`; meaningful claim requires lower CI above `delta` | Statistical positivity alone cannot establish relevance | `delta` and rationale | No defensible value currently supplied | **REQUIRES_EVIDENCE** |
| Decision regions | Positive if `L>0`; meaningful if `L>delta`; falsified if `U<0`; otherwise inconclusive when evidence is valid/mature | Regions are disjoint and match the exact positive hypothesis | Strict/inclusive endpoints | Depends on CI and `delta` approval | **BLOCKED** |
| Label validity | Exact prices only; no imputation; verified adjustment/CA semantics or verified raw-event transformation | Schema has no provenance fields or corporate-action table | Coverage after fail-closed exclusions | Corporate-action-safe labels are not currently established | **BLOCKED** |
| Information exposure | Integrity/count metadata only before one final unblinding | Prevents optional stopping and outcome-informed revision | Access roles and final unblinding timestamp/rule | Operational approval and audit procedure | **PROPOSED** |

- [x] Candidate: `neutral-adx-directional-association-v1`.
- [x] Research question and positive expected direction.
- [x] Predictive-association-only scope.
- [ ] Approve daily Spearman Rank IC and raw stock forward return as the primary estimand.
- [ ] Approve VNINDEX as descriptive context only and acknowledge raw/excess Rank IC equivalence.
- [ ] Approve exactly one primary horizon.
- [ ] Approve the point-in-time universe and its “not historical VN100” limitation.
- [ ] Approve formation timing, eligibility, missing-data, delisting and continuity rules.
- [ ] Approve one formation-date IC as the analysis unit.
- [ ] Approve the block-bootstrap family, block-length rule, confidence level and Monte Carlo precision.
- [ ] Approve the single-primary-test multiplicity family and treatment of secondary results.
- [ ] Supply and approve `delta`, target power and precision.
- [ ] Review and approve the resulting `N`, `n_min`, `c_min` and continuity threshold.
- [ ] Approve the fixed window/end rule and prohibit outcome-dependent early stopping.
- [ ] Approve support, falsification, inconclusive, insufficient and invalid boundaries.
- [ ] Approve separate reporting of positive association (`L > 0`) and meaningful positive association (`L > delta`).
- [ ] Approve gross-association-only economic interpretation and prohibit execution claims.
- [ ] Satisfy the corporate-action-safe label condition or accept that primary evaluation remains blocked.
- [ ] Approve information-access controls and final unblinding event.
- [ ] Preserve/hash the referenced retrospective artifacts without treating them as prospective evidence.
- [ ] Register the approved hypothesis revision before protocol freeze.
- [ ] Freeze the complete protocol and controlled experiment before any eligible formation.
- [ ] Confirm explicitly that `RESEARCH_GATE_PASS`, `ALPHA_VALIDATED`, and `PRODUCTION_READY` remain separate assessments.

## 12. Readiness statement

**Phase 7 readiness: BLOCKED pending explicit methodology approvals and label provenance.** The candidate and research question are approved, but the Registry record remains DRAFT. No protocol or experiment may be frozen until the unchecked decisions are completed, numerical calculations are documented, and the corporate-action-safe label condition is satisfied or encoded as a fail-closed exclusion rule that still meets approved coverage. Existing Paper and Forward records remain excluded from the future experiment.
