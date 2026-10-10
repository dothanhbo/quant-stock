# MOM20–H10 prospective descriptive protocol V1

Protocol ID: `mom20-h10-prospective-descriptive-v1` · Document revision: **2** · Finalized: **2026-10-10**

**Status: PROTOCOL_FROZEN_PENDING_ACTIVATION — METHODOLOGICAL POLICY FROZEN — BLOCKED_FOR_ACTIVATION — NOT REGISTERED — NOT AUTHORIZED TO RUN.**

Research classification: **PROSPECTIVE_DESCRIPTIVE**. Data qualification and any inferential claim require separate approval and evidence. Existing controlled ADX V1 remains DATA_BLOCKED. This document does not edit the hypothesis registry, controlled-experiment contracts, Daily collection, or an existing frozen protocol.

The methodology below adopts the owner's MOM20 protocol-finalization policies. Policy-freeze record: **2026-10-10T06:33:41Z**; this is not an evaluator-freeze, checkpoint-qualification, registration or activation timestamp. Prior draft revision 1 SHA256: `cb51b461cf9201ed1304c8e584c4c482a2a945ede5e6a64838bf0c6b5af9ea5b`. Its exact bytes are retained in the isolated finalization audit. The minimum cross-section of 80 is the owner's proposed V1 policy adopted here; it is not a calibrated power or precision guarantee.

Only evidence-dependent activation fields remain **BLOCKED_FOR_ACTIVATION**. They must be explicitly approved and bound to this document's SHA256 in an activation record before the first prospective session; this document creates no such record. Changes to the frozen methodology require a new immutable revision with this revision's SHA256 as parent. No missing activation value is inferred from a software PASS, clock time or Git commit.

## 1. Fixed scientific scope

| Field | Fixed value |
| --- | --- |
| Directional hypothesis | Higher MOM20 is associated with higher subsequent H10 returns. |
| Feature | `MOM20(i,t) = close(i,t)/close(i,t−20) − 1`. Twenty benchmark **sessions**, not calendar days or twenty available stock bars. |
| Outcome | `H10(i,t) = close(i,t+10)/close(i,t) − 1`, at the exact tenth subsequent completed benchmark session. No nearest-date substitution. |
| Membership | Exactly the existing 100 equities in FIXED_COHORT_V1; VNINDEX is the calendar benchmark, not an equity member. |
| Primary metric | One daily cross-sectional Spearman Rank IC of ascending MOM20 and H10 ranks, average ranks for ties, among explicitly eligible pairs. No sign reversal or portfolio selection. |
| Primary aggregation | Arithmetic equal-date mean over defined daily ICs; undefined dates remain in coverage reports and are never assigned zero. |
| Enrollment budget | Twelve calendar months from the first eligible future session after protocol approval, evaluator freeze and successful checkpoint qualification, followed by the exact H10 maturity tail. No automatic extension to rescue coverage or results. |
| Minimum cross-section | At least **80 of the fixed 100** members with valid frozen MOM20 features and matured valid H10 labels; equivalent to 80% membership coverage. Both rank series must be nonconstant. |
| Feature warm-up | Existing historical prices may provide the exact 20-session lag/prefix, admitted and locked before outcomes; they never enter independent evaluation evidence. |
| Input stream | Future immutable **Daily research checkpoints**, with recorded source provenance, cohort identity, market session, UTC capture time and restorable bytes. No second updater, provider substitution or retrospective qualifying of history. |
| Exposure | ADX14, MOM20, VOL20 and Research Synthesis V1 are development evidence. None is independent holdout evidence; this hypothesis was selected after their results were seen. |
| Interpretation | Prospective descriptive association only; no significance, power, meaningful-effect, alpha, profitability or deployment claim. |

Exact cohort membership file: `research/alpha_hypotheses/mom20_h10_exploratory_20261010/inputs/cohort_v1_tickers.txt`.

- Membership SHA256: `a56872b859b018d8a8dfb7f034b15ceb9a68ea2805aeb5c7150fa53204d14faf`.
- Manifest SHA256: `503959d0d300963130fc16274ef28ff293d8d01975beac73053d20685b8cfb27`.
- Identity: `FIXED_COHORT_V1:a56872b859b018d8a8dfb7f034b15ceb9a68ea2805aeb5c7150fa53204d14faf`.

The manifest's original ADX scope does not create a MOM20 approval: this document reuses **membership only**. The operational watchlist may change; V1 research membership cannot. New securities belong to an independently identified V2, never a replacement inside V1.

## 2. Activation, earliest session and fixed stopping rule

**Fixed activation rule.** Let `B` be the latest UTC timestamp of protocol approval, evaluator/configuration/runtime freeze, successful checkpoint qualification under the declared descriptive admission policy, and any later exposure of candidate prospective outcomes. Record those timestamps and this document's SHA256 before enrollment. Define `A` as the first eligible completed VNINDEX trading session whose local session date is **strictly after the Asia/Ho_Chi_Minh date of B**. A session is eligible for starting enrollment only when its checkpoint passes the fixed completion, receipt, provenance, identity and snapshot rules. `A` is unset and must not be backdated.

Starting eligibility is decided from information available at formation capture, **never from future H10 labels or the eventual IC**. The 80 matured-pair rule governs whether a daily IC is defined later; it cannot shift `A` after outcomes are observed. An eligible starting checkpoint with insufficient feature/label coverage retains an undefined IC and still consumes enrollment time. Sessions before `A`, including failed readiness attempts, are retained as readiness/continuity records, not retrospective evaluation dates.

The earliest feature/formation date can be `A`: an existing admitted historical warm-up supplies the exact `t−20` close and recorded calendar prefix. Do not wait for 21 new post-activation closes solely because they are new. History is locked in the selected formation checkpoint, with its source/vintage and basis limitations recorded. If exact endpoints or basis/identity evidence are unavailable, retain the member's unavailable reason; do not shorten the lag, fill prices or select a favorable historical vintage. Historical MOM20/VOL20/ADX results and pre-`A` outcomes are excluded from primary evaluation.

The twelve-month formation window is `[A, A + 12 calendar months)`, interpreted in Asia/Ho_Chi_Minh; only calendar sessions in that interval are scheduled. Warm-up, missing sessions and undefined dates consume the fixed resource budget. The last scheduled formation is the last calendar session before the anniversary. Each formation's target is its exact `t+10`; the tail ends after the last scheduled target plus the approved receipt/grace deadline. No early stopping, optional extension or outcome-dependent inclusion is permitted. A safety interruption is logged; it does not authorize restarting the clock.

**Fixed calendar rule.** Align `t−20` and `t+10` to the ordered completed **VNINDEX sessions**, retaining the calendar's source, version/hash, publication/capture timestamps, closure/amendment records and qualification limits. The current checkpoint basis is stored VNINDEX sessions, not an independently verified official calendar. An absent benchmark session must not be silently treated as a holiday or compress the horizon. Unresolved calendar gaps/amendments make affected features/labels unavailable. A corrected calendar is a new immutable vintage; it cannot silently redefine already frozen targets.

**BLOCKED_FOR_ACTIVATION — one narrow timing decision.** The owner must supply, before activation, all three items in one deterministic timing rule: **(1)** the attributable criterion for completed benchmark/equity EOD bars, **(2)** the checkpoint receipt deadline `C(s)` for each session `s`, expressed in Asia/Ho_Chi_Minh with UTC conversion and closure handling, and **(3)** a fixed maturity grace `G(s)` and its time/session units, with an absolute final label deadline. None is currently set.

Existing evidence does not support selecting numerical cutoff/grace values. `core/market_admission.py` sets a **15:00 ICT weekday date boundary** and explicitly declares no holiday calendar or publication-lag evidence. `app/daily_pipeline.py` gates checkpoint creation on a complete update/integrity PASS; `quantlab/catalog/research_checkpoint.py` explicitly says integrity PASS is not official EOD completion or scientific qualification. A 15:15 attempt is not a completion guarantee. These facts do not establish a receipt SLA or grace period. The owner decision is limited to the three timing fields above; no new audit, provider request or scheduling project is initiated.

H10 matures only after its exact target session `T=t+10` has completed and an admitted target close is present. Grace permits a late receipt of that **same target session**, not substitution with a later trading day's close. At the fixed deadline, unresolved outcomes receive final missing/censored reasons. If a qualified on-time checkpoint already supplies an endpoint, grace cannot replace it with a favorable revision. No final primary analysis can occur until the last scheduled target and its approved grace deadline have passed.

## 3. Identity continuity and missingness

Retain one scheduled record for each of the **100 original members** on every scheduled session, even when no feature or outcome can be calculated. Never reconstruct membership from current database coverage, drop a delisted name from the denominator or silently substitute a ticker.

**Fixed continuity rule.** No silent ticker mapping. Keep the original member key, old/new aliases, attributable continuity evidence, source/retrieval reference, reviewer and effective date. Only a verified continuation of the same security may supply that member's data; issuer identity alone is insufficient. Unresolved reuse, merger/exchange conversion or delisting treatment remains unavailable. Corporate actions affecting price comparability require explicit event/basis evidence; do not invent cash payouts, total returns or zero-return liquidation labels. Identity gaps exclude affected pairs and remain in the fixed-member denominator; they do not authorize replacement.

For admission, exact endpoint closes must be finite, positive, identity-consistent and basis-comparable. The MOM20 formula needs its two exact endpoints; it does **not** require twenty consecutive observed equity bars. Record intervening missing bars separately, without imputing them or moving `t−20`. H10 likewise requires its exact target endpoint; do not slide it to the next available stock bar. Unknown missingness remains unknown rather than being called a suspension or delisting without evidence.

Record distinct reasons, including warm-up, missing formation/lag/target bar, incomplete target maturity, missed/late checkpoint, incomplete session, calendar uncertainty, unresolved security identity, corporate-action/price-basis uncertainty, revision conflict, failed provenance, and constant cross-sectional ranks. A feature is fixed from the formation checkpoint before its outcome is available. At maturity, available finite feature/outcome pairs define the daily cross-section; retain all excluded records and report both member and date coverage.

**Fixed coverage rule: `n_min = 80`.** At maturity, at least 80 original members must have both valid frozen features and valid exact-session H10 labels. If fewer qualify or either rank series is constant, IC is undefined. Report the actual count and exclusion reasons, including every scheduled undefined date; zero IC is a defined value and stays in the mean denominator. The exploratory software minimum of five does not govern this protocol. No additional stale-bar, liquidity or outcome-based screen is authorized.

## 4. Checkpoint admissibility and revisions

Every admitted checkpoint must have restorable market/provenance bytes; verified file hashes; checkpoint ID, dataset fingerprint/version, cohort membership/hash, session and UTC capture time; recorded provider/package/normalization/units evidence; provenance reconciliation; and permitted-retention evidence. Reject corrupted snapshots, unresolved provenance applications/blocks, missing cohort identity, mismatched sessions, unexpected source mixing or inconsistent snapshots. Successful captures of the same identity count once. Keep rejected/missed attempts as availability evidence.

The existing Daily hook records **KBS** update provenance, `qualified: false`, unknown price basis, an unverified session calendar and historical-provider uncertainty. Those fields must survive unchanged. They do not establish scientific data qualification. Historical history inside a future checkpoint is not automatically prospective evidence. The checkpoint feature must be separately integrated/approved for use; this document neither merges its branch nor activates it.

**Fixed primary snapshot-selection rule.** For each formation session, select the **earliest admissible immutable checkpoint by UTC capture timestamp** after the declared completion criterion and no later than `C(s)`; ties are broken by lexical checkpoint ID. Deduplicate identical checkpoint IDs. Lock its feature inputs, historical warm-up, formation close, cohort/calendar identities and exact `t+10` target rule before any H10 outcome is available. The future target's calendar date is identified as the tenth subsequent completed VNINDEX session as calendar records arrive, not assumed to be known at formation or chosen from stock returns. Select the exact target-session endpoint by the same rule. If none is admissible by `C(T)`, select the earliest admissible receipt of that same target session within the fixed `G(T)` deadline; otherwise retain a missing label. This ordering is frozen before enrollment; it cannot be chosen based on realized ICs or returns.

Preserve revisions as additional immutable vintages; never overwrite or switch an already selected primary checkpoint. Record each revision's source/version/hash, receipt time and relation to locked inputs. If a revision makes the locked formation/feature basis incompatible with the target basis, or comparability cannot be established, mark the affected pair unavailable with a revision/basis reason under the predeclared admission policy. Do not recompute a feature from future history, mix VCI/KBS, shift an exact session or retrospectively select a better vintage. No alternative restated primary analysis is authorized.

**BLOCKED_FOR_ACTIVATION — successful checkpoint qualification and retained-input evidence.** Supply evidence that the current Daily checkpoint stream passes the source, version, immutable-byte, retention, cohort/calendar, units, continuity and price-basis admission checks. Record what is qualified for descriptive use and what remains unknown; `qualified: false` or a provider name is insufficient. Define attributable coherent-basis acceptance/exclusion criteria before enrollment, including corporate-action/revision conflicts across feature/label endpoints. Unknown adjustment semantics must not be reported as verified RAW/total returns. This is an operational descriptive-input admission gate, not a claim that scientific data qualification or inferential requirements have been satisfied. No acquisition, rewrite or remediation is authorized by this document.

This policy does not guarantee that all source data are historically point-in-time; it preserves what was actually available at declared captures. Qualification is a separate evidentiary conclusion, not a new label assigned by this protocol.

## 5. Single final primary analysis and reporting

After the fixed enrollment closes and the approved H10 maturity/deadline completes, evaluate **once**: daily Spearman IC of the frozen MOM20 feature against admitted H10 returns, then the equal-date mean of defined ICs. Preserve complete scheduled dates, member rows, exclusions and undefined reasons. No parameter search, score reversal, alternative horizon, model comparison, subgroup selection or backtest is authorized.

The final descriptive report contains: protocol/code/cohort/input identities; approved dates/calendar/cutoffs; scheduled/defined/undefined date counts and reasons; eligible/expected pairs and daily cross-section distribution; the complete daily IC series; primary equal-date mean; positive/zero/negative IC frequency; fixed calendar-year slices with partial years labeled; revision/capture/identity coverage; deviations and source limitations. Calendar-year slices are descriptive context, not extra primary tests. Overlapping H10 labels and shared market shocks make dates dependent; security-date counts are not independent sample sizes.

No p-values, significance decisions, confidence/power targets or inferential bootstrap settings are frozen here. An inference specification requires separate approval before relevant prospective outcomes are exposed and cannot retrospectively convert this descriptive enrollment into an independently confirmatory experiment.

**Fixed exposure rule.** Interim research access is limited to completeness, provenance, availability and integrity diagnostics; no interim MOM20/H10/IC summaries for model selection. Operational personnel may necessarily see prices; record that access honestly and preserve immutable feature/capture bindings. No outcome-guided feature, horizon, direction, membership, threshold, cutoff, revision policy or strategy change is permitted. No interim analysis is executed or scheduled by this document.

**BLOCKED_FOR_ACTIVATION — evaluator freeze and release identity.** Record the exact prospective evaluator/adapter version, configuration (`MOM20`, `H10`, 100-member cohort, minimum 80, calendar/snapshot policy), imported source hashes, runtime and dependency versions, deterministic tie/undefined handling, final command, UTC freeze time, and responsible access/release roles before enrollment. Existing exploratory code/hash identities are development references, not a frozen prospective evaluator; their minimum-five setting cannot silently carry over. This task neither implements nor runs an evaluator. A Git commit of this document does not satisfy the evaluator gate.

## 6. Deviations and invalid evidence

A deviation includes a changed feature/horizon/direction/coverage policy, unapproved ticker mapping, membership substitution, missed or late checkpoint, changed calendar/completion/cutoff/revision rule, unrecorded exposure, early stop/extension, or outcomes seen before final freeze. Report the date, affected members/checkpoints, reason, decision authority and impact on primary eligibility. Operational misses remain in the scheduled denominator; they are not erased.

Evidence is invalid for the primary prospective claim when its feature was computed/revised using future information, its enrollment was backdated, its identity/hash/provenance cannot be verified, it uses an unapproved vintage/source, or it violates the finally approved calendar/price-basis/identity rules. Fail closed for affected pairs or dates; a systemic exposure/selection breach invalidates an independent-validation interpretation. Preserve evidence, do not repair history or silently restart. Any amendment receives a new immutable document revision and explicit prospective applicability; earlier observations cannot be relabeled to fit it.

## 7. Existing governance and development references

Use the existing decision-state, exposure, immutable revision and timestamp conventions in `research/alpha_hypotheses/preregistration_decision_memo_v1.md` and `quantlab/hypotheses/controlled_experiment.py`. That memo approves an ADX candidate, not a MOM20 controlled experiment; this owner's finalization instruction fixes the descriptive MOM20 policies above. The controlled contract requires prospective start after discovery/freeze and registration-before-freeze sequencing. This document records only its methodological policy freeze; it creates no registry record, controlled-experiment freeze or experiment object.

Development evidence:

- VCI ADX study: `research/alpha_hypotheses/vci_adx14_h10_exploratory_20261009/`.
- MOM20 and VOL20: `research/alpha_hypotheses/{mom20_h10_exploratory_20261010,vol20_h10_exploratory_20261010}/`; historical input SHA256 `75704e29fe5a3bcf44dba754b42264664523f3270b2eaeefcc7c6812f4055d01`, stored history through 2026-10-06.
- Synthesis: `research/alpha_hypotheses/research_synthesis_v1_20261010/RESEARCH_SYNTHESIS_V1.md`, SHA256 `ee5e0d22db8ec02871fcef86b14e37a5f7bfcedd156be6bdd92397aaa306ff8d`; synthesis source hashes trace the saved studies.
- Published Daily checkpoint implementation: `babe062e60b35dad2c624ecf26ceafc3e6f99b37`; no production incorporation or scientific qualification is implied.

**Remaining activation gates:** the owner's explicit completion/receipt/grace rule; successful checkpoint qualification with permitted retention and coherent-basis admission evidence; the prospective evaluator/configuration/source/runtime freeze and access roles; and an explicit activation record binding all of these to this document hash before the resulting first eligible session. Actual dates, numeric cutoff/grace and evaluator identities remain unset. These are **BLOCKED_FOR_ACTIVATION**, not permission to invent defaults.

**PROTOCOL_FROZEN_PENDING_ACTIVATION.** Data/inference readiness is not upgraded by this methodological freeze, document commit or software checks. No provider request, historical replay, factor experiment, prospective enrollment, scheduling, production edit or merge is authorized or performed.
