# Quant-Stock Canonical Roadmap — V1 to V3

**Document purpose:** canonical roadmap and AI reconciliation reference for the `quant-stock` project.

**Status model:** `NOT_STARTED`, `IN_PROGRESS`, `BLOCKED`, `PASS`, `CLOSED`, `DEFERRED`, `REJECTED`.

**Primary principle:** this is a long-lived personal quantitative investment decision-support system. Correctness, reproducibility, provenance, research integrity, operational safety, and maintainability take priority over feature count or short-term performance.

**Scope discipline:** do not open V2/V3 work until V1 is formally frozen unless a V1 blocker requires it.

---

# 1. Canonical Product Definition

## 1.1 What this system is

`quant-stock` is a personal quantitative research and decision-support platform for Vietnamese equities.

Canonical chain:

**market data → integrity/provenance → research → signal generation → portfolio/risk policy → paper/forward validation → execution simulation → evidence/reporting → human investment decision**

V1 is not intended to be:
- an institutional OMS/EMS;
- a high-frequency or tick-data platform;
- a fully automated live-trading system;
- a broker reconciliation system;
- a multi-user enterprise platform;
- an ML-first prediction platform.

## 1.2 Canonical engineering values

1. No silent data mutation.
2. No unsupported provenance claim.
3. No qualified result without qualified evidence.
4. No research claim without reproducible inputs.
5. Fail closed when market-data integrity is unresolved.
6. Historical legacy evidence remains legacy; never retroactively “wash” it into verified evidence.
7. Strategy semantics and evidence identity must be versioned independently from implementation details.
8. Operational convenience must not bypass research or integrity controls.
9. Every phase has explicit acceptance criteria.
10. Backlog is preferable to speculative scope expansion.

---

# 2. Release Model

## V1 — Trustworthy Quant MVP

Goal:

> A reliable personal quant decision-support system that can ingest data, qualify it, research strategies, produce reproducible signals, simulate portfolio/execution behaviour, track paper/forward evidence, and fail closed when evidence is not trustworthy.

V1 is complete when the system is credible and operable, not when it contains every possible quant feature.

## V2 — Professional Personal Quant

Goal:

> Improve portfolio construction, risk management, execution realism, and research quality while preserving V1 evidence guarantees.

## V3 — Scaled Research & Operations

Goal:

> Make the system more scalable, automated, research-efficient, and operationally mature without sacrificing methodological discipline.

---

# 3. V1 — Trustworthy Quant MVP

## R1 — Historical Mutation / Revision Safety
**Target status:** CLOSED

Purpose:
- prevent silent overwrites of historical market data;
- detect retrospective revisions;
- preserve observation/application history;
- establish immutable baseline identity.

Acceptance:
- historical changes detected before mutation;
- revision events auditable;
- unresolved revision blocks stateful downstream operation;
- baseline/log identity reproducible.

## R2 — Dataset Versioning / Market Provenance
**Target status:** CLOSED

Purpose:
- define dataset versions;
- bind admitted changes to explicit application decisions;
- establish source/session provenance;
- support deterministic integrity checks.

Acceptance:
- versions advance only on accepted changes;
- identity reproducible;
- exact admitted decision required;
- no-op/retry cannot silently bind to later version;
- block state persists across retry/restart.

## R3 — Evidence Provenance Binding
**Current status:** IN_PROGRESS

Purpose:

> Bind forward and paper evidence to the actual market-data state consumed, and prevent unqualified/legacy/revised evidence from entering qualified performance claims.

Core requirements:

### A. Coherent capture
Every binding refers to one coherent market-data version.

### B. Consumed input coverage
Bindings cover materially consumed market inputs, including:
- forward selected-symbol feature history;
- benchmark history;
- paper ADTV window;
- traded-symbol history;
- VNINDEX regime / relative-strength history;
- execution/mark/exit sessions.

### C. Paper observation honesty
Stored paper marks may be called market closes only when provenance is established. Realized PnL and open-position lineage must survive paper reset.

### D. Review semantics
Operator review must not launder legacy/missing evidence into qualified evidence. `REVIEWED_ACCEPTED` is valid only if the underlying state would otherwise be `PROVENANCE_VERIFIED` and the only blocker is an explicitly reviewed unresolved revision.

### E. Append-only guarantees
Immutable bindings/events resist UPDATE, DELETE, INSERT OR REPLACE, ON CONFLICT DO UPDATE, and equivalent replacement paths.

### F. Qualification freshness
Current qualified reports re-evaluate current revision/block state.

### G. Complete observation chain
Cumulative paper qualification uses the full provenance chain before display filtering.

### H. Legacy honesty
Expected states:
- `PROVENANCE_VERIFIED`
- `BOUND_LEGACY_INPUT`
- `LEGACY_UNBOUND`
- `QUARANTINED_UNRESOLVED_REVISION`
- `QUARANTINED_INCOMPATIBLE_BASIS`
- `QUARANTINED_MISSING_PROVENANCE`
- `REVIEWED_ACCEPTED`
- `REVIEWED_REJECTED`

Legacy evidence must never become verified automatically.

### R3 closure gate
R3 may be `CLOSED` only when:
- all known P1 provenance defects are fixed;
- focused R3 tests pass;
- mutation tests prove guards are meaningful;
- paper/forward consumers cannot leak unqualified evidence into qualified performance;
- isolated local validation passes;
- no live DB mutation occurs during validation;
- final R3 implementation is committed separately;
- R4 remains deferred.

---

## V1.1 Operational Closure
**Status:** NOT_STARTED

Purpose:

> Prove the complete operational path works coherently using the approved data, strategy, evidence, and paper components.

Canonical chain:
1. Market data update
2. Revision admission / integrity gate
3. Dataset version finalization
4. Strategy scan
5. Signal persistence / pending order state
6. Paper lifecycle
7. Forward validation lifecycle
8. Evidence qualification
9. Reporting / dashboard
10. Telegram / operational notification where enabled

Acceptance criteria:

### Data
- canonical DB path;
- stale data detectable;
- revisions block downstream mutation;
- no updater bypass;
- no unsupported provider fallback.

### Strategy
- all active entry paths use the same canonical processor;
- strategy identity/fingerprint matches the frozen contract;
- no Manager/CLI/cron path silently changes semantics.

### Paper
- canonical paper DB resolver;
- deterministic pending-order execution;
- retry/idempotency confirmed;
- lifecycle respects execution contract;
- no accidental cross-strategy routing.

### Forward
- deterministic formation/outcome lifecycle;
- provenance binding correct;
- blocked provenance cannot manufacture returns.

### Reporting
- qualified vs descriptive metrics visibly distinguished;
- legacy evidence remains labelled;
- quarantined evidence excluded from qualified metrics.

### Operations
- explicit failure state;
- explicit recovery path;
- Telegram failure does not corrupt core state;
- useful command exit codes.

Required outputs:
- operational audit memo;
- canonical daily command;
- canonical weekly command;
- known failure/recovery matrix.

---

## V1.2 MVP Acceptance & Freeze
**Status:** NOT_STARTED

Purpose:

> Define exactly what V1 promises and freeze it.

Required V1 acceptance document:

### Product scope
What V1 does.

### Out of scope
What V1 explicitly does not do.

### Canonical strategy contract
- entry model;
- ranking;
- regime gating;
- position limits;
- execution assumptions;
- stop/target policy;
- cost assumptions;
- holding policy.

### Data contract
- active operational provider;
- historical baseline qualification;
- prospective provenance requirements;
- known limitations.

### Evidence contract
- qualified evidence definition;
- descriptive legacy definition;
- quarantine rules.

### Operational contract
- daily process;
- weekly process;
- recovery steps;
- DB backup expectations;
- environment requirements.

### Known limitations
Accepted limitations must be explicit.

V1 freezes only when:
- no known P0/P1 defects remain;
- R1–R3 closed;
- operational closure passes;
- docs reflect actual implementation;
- focused regression set passes;
- isolated end-to-end smoke test passes;
- worktree is understood;
- deliberate V1 commit/tag created;
- post-V1 backlog documented.

Recommended release tag: `v1.0.0-mvp`.

---

# 4. V2 — Professional Personal Quant

V2 starts only after V1 freeze.

Objective:

> Better portfolio decisions, better risk control, better execution realism, and stronger research validity.

## V2.1 Portfolio Construction
Questions:
- how much capital per signal?
- how much correlated exposure?
- how much sector concentration?
- when should capital stay in cash?

Potential components:
- gross exposure;
- per-position cap;
- per-sector cap;
- correlated-cluster cap;
- cash reserve;
- rolling correlation;
- risk-budget allocation.

Progression:
1. current ATR-risk sizing;
2. normalized risk contribution;
3. capped risk-parity style allocation;
4. optimization only if evidence justifies it.

Acceptance:
must improve out-of-sample portfolio characteristics without unstable parameter optimization.

## V2.2 Portfolio Risk Engine
Target capabilities:
- portfolio volatility;
- realized/rolling drawdown;
- concentration risk;
- sector exposure;
- correlation exposure;
- beta/market exposure;
- regime-conditioned exposure;
- stress scenarios;
- risk contribution by position.

The output should answer:

> Why is the portfolio risky today?

## V2.3 Execution Realism
Potential components:
- dynamic slippage;
- liquidity-aware position cap;
- ADTV participation limit;
- gap risk;
- partial fills;
- next-open execution quality;
- realistic transaction costs;
- turnover cost analysis.

Do not build a full OMS/EMS in V2.

## V2.4 Research Robustness
Potential components:
- multiple-hypothesis tracking;
- false-discovery awareness;
- parameter stability;
- subperiod stability;
- regime stability;
- sector robustness;
- walk-forward governance;
- bootstrap confidence;
- effect-size reporting;
- feature decay / signal half-life;
- turnover-aware performance.

Prefer simple, stable, explainable, reproducible research over fragile optimization.

## V2.5 Data Quality Expansion
Possible scope:
- better corporate-action handling;
- source reconciliation;
- provider disagreement tracking;
- point-in-time universe improvements;
- survivorship-bias reduction;
- delisted-symbol support where feasible;
- stronger adjustment-basis metadata.

A full historical rebuild is a separate controlled project. Do not rewrite V1 history casually.

## V2 exit gate
V2 is complete when:
- portfolio construction is risk-aware;
- portfolio risk is explainable;
- execution assumptions are materially more realistic;
- research robustness checks are standardized;
- prospective evidence supports the claims;
- V1 reproducibility remains intact.

Suggested tag: `v2.0.0`.

---

# 5. V3 — Scaled Research & Operations

## V3.1 Research Orchestration
Potential:
- standardized experiment runner;
- experiment registry;
- reproducible manifests;
- queueing;
- resource budgets;
- automatic artifact capture;
- experiment comparison;
- promotion/rejection workflow.

Goal:

> An AI or human researcher can run a controlled experiment without bypassing governance.

## V3.2 Factor / Signal Library
Potential:
- standardized factor interface;
- factor metadata;
- factor provenance;
- factor decay;
- factor correlation;
- neutralization where justified;
- combination experiments.

Do not create many factors without hypotheses.

## V3.3 Model Registry
Only if needed.

Possible states:
- DRAFT
- RESEARCH
- CANDIDATE
- PAPER
- FORWARD
- PROMOTED
- RETIRED
- REJECTED

Track:
- strategy version;
- factor set;
- parameters;
- research period;
- validation state;
- evidence package;
- promotion state.

## V3.4 Operational Monitoring
Target:
- scheduled health checks;
- DB integrity monitoring;
- stale-data alerts;
- pipeline status;
- quarantine alerts;
- paper lifecycle alerts;
- backup verification;
- run-history summaries.

## V3.5 Broker / Live Reconciliation
Optional and explicitly gated.

Possible:
- broker account read-only integration;
- holdings reconciliation;
- order reconciliation;
- execution-quality comparison.

Live order placement is a separate major decision.

## V3.6 Scale / Performance
Only optimize when needed:
- faster feature computation;
- cached immutable research datasets;
- vectorized experiment runs;
- parallel offline jobs;
- larger universe support.

Do not add distributed complexity before actual bottlenecks exist.

## V3 exit gate
V3 is complete when:
- research execution is standardized and scalable;
- operational monitoring is mature;
- experiment/model lifecycle is auditable;
- portfolio/risk/execution evidence remains reproducible;
- the system can be maintained without relying on one AI session or one person's memory.

---

# 6. Explicit Non-Goals Before V1 Freeze

Do not add before V1 unless they become a direct blocker:
- LSTM / Transformer prediction;
- generic AI stock picker;
- reinforcement learning;
- intraday/tick architecture;
- alternative-data pipeline;
- news sentiment;
- macro forecasting engine;
- automated fundamental valuation;
- generic portfolio optimizer;
- pairs/stat-arb;
- broker live-order execution;
- full multi-user auth;
- cloud distributed compute;
- enterprise UI polish;
- R4 global historical rebuild.

---

# 7. Backlog Schema

Every future item should have:

```yaml
id:
category:
title:
status:
priority: P0 | P1 | P2 | P3
target_release: V1 | V2 | V3 | FUTURE
problem:
why_it_matters:
acceptance_criteria:
dependencies:
evidence_required:
out_of_scope:
```

Suggested categories:
`DATA`, `RESEARCH`, `STRATEGY`, `PORTFOLIO`, `RISK`, `EXECUTION`, `FORWARD`, `PAPER`, `OPERATIONS`, `REPORTING`, `UI`, `INFRA`, `GOVERNANCE`.

---

# 8. AI Reconciliation Protocol

A future AI review should first identify:

## 8.1 Project state
- Git HEAD;
- branch;
- dirty files;
- active strategy version;
- active paper store;
- active forward store;
- active market dataset version;
- unresolved integrity blocks;
- latest operational session.

## 8.2 Roadmap state
Return a status for:

```text
R1:
R2:
R3:
V1_OPERATIONAL_CLOSURE:
V1_FREEZE:
V2_PORTFOLIO:
V2_RISK:
V2_EXECUTION:
V2_RESEARCH_ROBUSTNESS:
V2_DATA:
V3_RESEARCH_ORCHESTRATION:
V3_FACTOR_LIBRARY:
V3_MODEL_REGISTRY:
V3_MONITORING:
V3_BROKER_RECONCILIATION:
```

Allowed statuses:
`NOT_STARTED`, `IN_PROGRESS`, `BLOCKED`, `PASS`, `CLOSED`, `DEFERRED`, `REJECTED`.

Every claimed status should cite concrete repository evidence.

## 8.3 Drift check

The AI must identify:

### Intended drift
Deliberately accepted and documented changes.

### Unintended drift
Implementation that no longer matches:
- strategy contract;
- provenance contract;
- evidence contract;
- execution contract;
- risk contract;
- roadmap scope.

### Scope creep
Features not required by the active milestone.

### Missing implementation
A roadmap requirement claimed complete but absent in code/tests/evidence.

### Unsupported claim
Documentation says more than evidence proves.

---

# 9. Canonical AI Review Template

```text
## CURRENT RELEASE TARGET

## CURRENT CHECKPOINT

## GIT / STATE SUMMARY

## ROADMAP STATUS

## CONTRACT DRIFT

## DATA / PROVENANCE STATUS

## RESEARCH STATUS

## STRATEGY STATUS

## PORTFOLIO / RISK STATUS

## PAPER / FORWARD STATUS

## OPERATIONS STATUS

## BLOCKERS
P0:
P1:

## NON-BLOCKING BACKLOG
P2:
P3:

## SCOPE-CREEP CHECK

## ACCEPTANCE-GATE STATUS

## NEXT RECOMMENDED STEP

## DO NOT DO YET
```

---

# 10. Decision Rules for Future AI

A phase is not complete merely because:
- code exists;
- tests pass;
- a dashboard displays something;
- a backtest is positive.

A phase is complete only when:
- implementation exists;
- the real production path uses it;
- tests cover failure cases;
- evidence supports the claimed state;
- documentation matches implementation;
- accepted limitations are explicit.

Prefer:

**not yet verified**

over:

**probably fine**.

---

# 11. Canonical Sequence From Current State

```text
R3 fixes
→ R3 narrow read-only review
→ R3 isolated validation
→ R3 commit
→ V1 operational closure
→ V1 acceptance audit
→ V1 freeze/tag
→ stabilization period
→ V2 planning
```

Do not start V2 implementation before V1 freeze.

---

# 12. Definition of Success

## V1
> This system produces reproducible quantitative investment evidence from controlled market data, distinguishes verified from legacy/quarantined evidence, runs a consistent strategy/paper/forward workflow, and fails closed when its evidence is not trustworthy.

## V2
> The system constructs and evaluates portfolios with explicit risk, concentration, correlation and execution constraints, and its research process is robust against common forms of overfitting.

## V3
> The system can scale research and operations through standardized, auditable workflows without losing reproducibility or methodological discipline.

---

# 13. Change-Control Rule

This document is intended to be canonical.

Any future modification should record:

```text
Date:
Change:
Reason:
Affected release:
Evidence:
Approved by:
```

Do not silently rewrite prior release definitions after the fact.

Major roadmap changes should be committed separately from implementation changes whenever practical.
