# R3: market-data provenance bound into forward and paper evidence

Date: 2026-10-06. Scope: V1 data-provenance decision item R3 only. R4 (rebaseline), strategy
logic, scoring, entry/exit rules, portfolio and paper-execution policy, Manager UI, provider
selection and historical evidence are untouched. Frozen decisions stand: `market.db` history is
LEGACY_UNVERIFIED, KBS via `vnstock.api` stays operational, historical Q70 paper evidence is
LEGACY_UNVERIFIED.

## Problem

After R1+R2 the market data has a dataset version and a per-session origin, but forward and paper
evidence recorded none of it. A later revision, block or rebuild could not be traced to the
evidence that consumed the affected data, and evidence could not be told apart by what it rested on.

## Evidence map (who owns what)

| Evidence | Store | Written by | Mutable? |
|---|---|---|---|
| Forward formation (snapshot) | `data/forward_validation.db` `forward_formations/_positions` | `quantlab/forward/daily.py` -> `ForwardValidationLedger.record_formation` | append-only |
| Forward maturity / outcome | same DB `forward_maturities/_outcomes` | `daily.py` -> `record_maturities/record_outcomes` | append-only |
| Prospective paper observation | `data/prospective_portfolio_evidence.db` | `capture_prospective_portfolio_evidence` -> `ProspectivePortfolioEvidenceLedger.append` | append-only |
| Paper entry / exit order, fill, lifecycle, closed trade | `paper_trading*.db` | `execution/signal_executor.py`, `execution/lifecycle_manager.py` via `PaperBroker` | `reset()` deletes everything |
| `signals` table | `market.db` | `scripts/update_signal_results.py` (UPDATE in place) | mutable; left unbound |

No ownership ambiguity, no destructive migration, no strategy change was needed, so no STOP
condition applied.

## Model

`core/evidence_market_binding.py` is the single implementation.

* **Binding** (contract `core.evidence_market_binding` v1): an immutable payload with identity
  `mbind-<sha256[:40]>` over everything except `bound_at_utc`. It records the subject
  (`kind`, `ref`), `log_state` (`BOUND`, `NO_OBSERVATION_LOG`, `LOG_UNREADABLE`), the dataset
  (`dataset_version_id`, `baseline_id`, baseline content hash, provenance label, consumable,
  unresolved-block symbols, pending applications) and the session dependencies
  (`role`, `symbol`, `session`, `window_start`, `origin`, `observation_id`, `version_id`).
* **`MarketBindingSession`** pins the dataset version when the consumer starts, before it reads
  data. `bind()` re-reads the version and raises `StaleDatasetVersionError` if it advanced, so a
  binding can never silently point at a later ("latest") version. An optional
  `expected_dataset_version_id` is checked at construction.
* **`EvidenceQualifier`** evaluates bindings against the *current* log. Qualification is computed
  and appended, never stored inside the evidence.

### Qualification states

| State | Meaning | Use |
|---|---|---|
| `PROVENANCE_VERIFIED` | every dependency is an applied observation, no unresolved block | qualified |
| `REVIEWED_ACCEPTED` | quarantined, then accepted by a reviewer for exactly the current basis | qualified |
| `BOUND_LEGACY_INPUT` | bound, but depends on a `BASELINE_LEGACY` session | descriptive only, labelled `LEGACY_UNVERIFIED` |
| `LEGACY_UNBOUND` | recorded before R3, no binding | descriptive only, labelled `LEGACY_UNVERIFIED`, never upgraded |
| `QUARANTINED_UNRESOLVED_REVISION` | a dependency session is covered by an unresolved block | excluded |
| `QUARANTINED_INCOMPATIBLE_BASIS` | baseline changed or a dependency's provenance drifted | excluded |
| `QUARANTINED_MISSING_PROVENANCE` | no log at record time, unreadable log, absent/removed/unattributed session | excluded |
| `REVIEWED_REJECTED` | rejected by a reviewer (sticky) | excluded |

Precedence when several reasons apply: missing > incompatible > unresolved > legacy-unbound >
bound-legacy-input > verified. A review of ACCEPT only counts while the current basis
(state plus reasons) equals the reviewed basis; a new block supersedes it.

### Narrow quarantine

A dependency is affected by an unresolved block iff it is the same symbol and any affected
session lies in `[window_start or session, session]` (a block with no affected sessions, such as
an ambiguity block, affects the whole symbol). Therefore:

* an affected entry/formation session quarantines that evidence;
* an affected target/exit session quarantines that outcome (the window covers the holding period);
* an unrelated symbol, or a session outside the window, is not quarantined.

The operational gate (`require_market_provenance`) stays global and fail-closed. R3 changes
research qualification, not operational blocking.

## Forward binding

* `daily.py` creates the `MarketBindingSession` right after `require_market_provenance`, before
  the snapshot is built.
* **Formation**: dependencies `FORMATION_CLOSE` per position and `BENCHMARK_FORMATION_CLOSE`.
  Stored in `forward_market_bindings` in the same transaction as the formation.
* **Outcome**: dependencies `STOCK_TARGET_CLOSE` and `BENCHMARK_TARGET_CLOSE` on the exact target
  session, window starting at the formation session. Stored in the same transaction as the
  outcome. An outcome bound at a later dataset version records that later version; the formation
  binding keeps its own.
* If the dataset has a log and the outcome's target would already qualify as quarantined at bind
  time, `ProvenanceQuarantineError` is raised before any outcome is written (maturity events,
  which carry no return, may already exist). A dataset with no log is recorded as
  `NO_OBSERVATION_LOG` (qualifies QUARANTINED_MISSING_PROVENANCE) and not refused, preserving the
  legacy NOT_APPLICABLE operating mode.

## Paper binding

* **Entry**: `execute_pending_signals` pins the version, then binds `ENTRY_OPEN` (execution
  session) and `SIGNAL_REFERENCE` (signal session). The binding rides in
  `paper_orders.execution_context["market_binding"]`, written atomically with the order, fill and
  lifecycle rows.
* **Exit**: the lifecycle manager pins the version before bars are loaded and binds `EXIT_PRICE`
  on the exit session with the window starting at the entry date. A resumed pending sell keeps its
  original context and binding (the order store keeps the first context).
* **Observation**: `capture_prospective_portfolio_evidence` binds `MARK_CLOSE` per position and
  `BENCHMARK_CLOSE`, stored in `prospective_market_bindings` in the append transaction.
* `execution/paper_provenance.py` qualifies a closed trade from its entry and exit legs restricted
  to the traded symbol; a legacy leg makes the trade LEGACY_UNBOUND.

The paper store has no new tables: `reset()` deletes all tables, so append-only tables cannot live
there. Reviewed decisions for paper evidence would go to the prospective DB (not yet exposed).

## Legacy treatment

Nothing is back-filled. An evidence row without a binding is `LEGACY_UNBOUND`
(`LEGACY_UNVERIFIED`), preserved untouched, usable only as explicitly labelled descriptive
evidence, and it can never become verified by a later qualification. The `signals` table is
mutated in place by `update_signal_results` and stays unbound legacy.

## Database changes (additive, idempotent, no destructive migration)

* `forward_validation.db`: `forward_market_bindings`, `forward_qualification_events`.
* `prospective_portfolio_evidence.db`: `prospective_market_bindings`,
  `prospective_qualification_events`.
* Created with `CREATE TABLE/TRIGGER IF NOT EXISTS`; bindings are `UNIQUE(subject_kind,
  subject_ref)`; both tables reject UPDATE and DELETE by trigger. A pre-R3 database reads as "no
  bindings" and its rows are untouched (tested). No migration was run on any live database.

## Crash / retry model

Evidence and binding share one SQLite transaction, so there is no "evidence without provenance"
or "provenance without evidence" state. Tested: a crash while writing the binding, a crash after
the binding row is written but before commit (both roll back, ordinary exception and
`BaseException`), a retry of the same operation (no write, original `bound_at_utc` kept), a
duplicate update, a stale dataset version, a block appearing between the decision and the write
(binding records what was consumed; qualification quarantines it), and an outcome update that
arrives after a later version exists (stale session refused; a new decision binds the new
version). A retry after a crash is a new decision and binds the version it actually consumes.

## Consumer enforcement

| Consumer | Behaviour |
|---|---|
| `quantctl/forward_evidence.py` forward summaries | qualified / legacy-unbound / bound-legacy-input / quarantined / rejected counts; means exclude excluded outcomes and are descriptive; `qualified_mean_excess_forward_return_pct_points` is qualified only |
| Same module, paper points | `market_data_qualification` per observation |
| `analysis/paper_performance.py` | `qualifier` adds a `provenance_qualification` column; `qualification_required=True` keeps only qualified trades; `provenance_scope` is labelled. Without a qualifier the frame is byte-identical to before |
| `scripts/report_paper_performance.py` | `--qualified-only`, `--label-provenance` |

Account-level equity metrics are not filtered by trade qualification and are labelled so.

## Not done / backlog

* Reviewed-resolution and operator sync CLI (library only: `record_review`,
  `sync_qualification_events`).
* Selection ranking depends on unselected universe members; that dependency is bound only at
  `dataset_version_id` level, not per symbol.
* No queue-time binding of the pending signal; marks outside the lifecycle
  (`update_paper_positions`) are bound only through the prospective observation.
* `NO_OBSERVATION_LOG` evidence is quarantined at evaluation rather than refused.
* A deleted observation log reverts the dataset to NOT_APPLICABLE (R1/R2 known limit).
* Paper `reset()` still deletes paper history, including bindings held in orders.

## Revision 2: Sol 6.5 review fixes (six P1)

**P1-1 coherent capture.** `MarketBindingSession.bind()` reads the dataset version, reads every
dependency, then re-reads the version and the open application intents. R1/R2 versions are
append-only and totally ordered, so equal reads prove no application finalized in between. A moved
version raises `StaleDatasetVersionError`; a consumed session or window claimed by an open
application raises `PendingApplicationError` (a subclass). Nothing is bound and nothing is
retried against the new version; the caller re-runs its own decision.

**P1-2 consumed inputs.** Dependencies now have a coverage: `POINT`, `WINDOW` (every stored
session in `[window_start, session]`; the binding keeps a compact lineage summary, origin counts
and a digest, never the bars) or `UNPROVEN`. `ObservationLog.window_provenance` (read-only,
additive) produces the summary in one read. Windows bound:

* forward formation: `FORMATION_FEATURE_HISTORY` per selected symbol, from its first stored
  session to the formation session (ADX/RSI/EMA are recursive over the whole history);
* paper entry: `ADTV20_WINDOW` (the exact 20 sessions the sizing read) and `SIGNAL_HISTORY`;
* paper exit: `EXIT_HISTORY` (the Wilder ATR reads the whole history).

A legacy baseline session in any window gives `BOUND_LEGACY_INPUT`; a revision inside the window
quarantines; a changed window digest is `QUARANTINED_INCOMPATIBLE_BASIS`. Consequence: evidence
over history that starts in the legacy baseline can never be PROVENANCE_VERIFIED; only fully
prospective history can. Ranking over unselected universe members stays at dataset-version level.

**P1-3 paper observations.** A stored mark is labelled `MARK_CLOSE` only if it equals the
observation-session market close times the paper price scale (1000). Otherwise the dependency is
`STORED_MARK_UNVERIFIED` (origin `UNPROVEN_SOURCE`, quarantined as missing provenance) and the
check detail (stored mark, close, matched session) is kept. Each observation binding carries a
`lineage`: compact copies of the entry/exit legs of the trades added since the previous
observation, the entry legs of open positions, and closed/tracked trade counts. Observations are
qualified as a chronological series (`qualify_observation_series`), so cumulative realized PnL is
covered; untracked or pre-R3 trades make the observation `LEGACY_UNBOUND`. The copies live in the
prospective evidence DB, so a paper `reset()` does not remove them.

**P1-4 reviews.** Review events are replayed in order. `REJECT` is sticky; only the dedicated
`SUPERSEDE_REJECT` (which needs an active rejection) lifts it. `ACCEPT` is allowed only for an
unresolved-revision quarantine, and only counts while the basis (binding identities plus reasons)
is unchanged; missing provenance, incompatible basis and legacy evidence cannot be accepted.
`ACKNOWLEDGE` records awareness without changing qualification.

**P1-5 append-only.** `BEFORE INSERT ... WHEN <key already exists>` triggers abort any insert that
would collide, so `INSERT OR REPLACE` cannot replace a row with `recursive_triggers=0`; UPDATE and
DELETE triggers stay. The helper stays idempotent because it checks for an identical binding
before inserting.

**P1-6 stale qualifier.** `EvidenceQualifier.qualify()` compares `ObservationLog.state_signature()`
(last event, resolutions, applied sessions, versions) with its cached snapshot and reloads blocks
and caches when anything changed; a log that appears later is also picked up. Reports reusing a
qualifier therefore evaluate current state on every call.

**P2 taken (trivial).** Dependency sort key includes `window_start`; the console report prints the
provenance scope line.

**Remaining non-blocking.** Reviewed-resolution CLI; queue-time signal binding; per-symbol binding
of unselected ranking members; `NO_OBSERVATION_LOG` evidence is quarantined at evaluation, not
refused; marks are matched only against the last 10 sessions.

## Revision 3: three remaining P1 fixes (read-only audit of revision 2)

**P1-A review laundering.** `QualificationResult` now carries `underlying_base_state`: the
qualification with unresolved-revision blocks ignored (worst of the per-binding base states; a
`None` binding counts as `LEGACY_UNBOUND`). An operator `ACCEPT` can override an unresolved-revision
quarantine only when `underlying_base_state == PROVENANCE_VERIFIED`. Both `record_review(ACCEPT)`
(raises) and the replay in `qualify()` (a forged/stored ACCEPT is ignored) enforce it, so
`BOUND_LEGACY_INPUT` / `LEGACY_UNBOUND` (and missing / incompatible) evidence plus a block stays
quarantined and non-qualified after an ACCEPT. `REJECT` stays sticky and is allowed on any evidence.

**P1-B full-chain qualification.** The paper presentation (`quantctl/forward_evidence.py`) reads
the COMPLETE chronological observation chain of the strategy/source identity, qualifies it, and only
then applies `start_date` / `end_date` / `max_records` to what is shown. `qualify_observation_series`
also fails closed on its own: if an observation's `tracked_trade_count` exceeds the trade lineages
present in the supplied chain, the observation gets a `INCOMPLETE_OBSERVATION_CHAIN` part
(`QUARANTINED_MISSING_PROVENANCE`); missing trades are never inferred.

**P1-C VNINDEX entry history.** The real signal path (`get_market_regime`) reads the whole VNINDEX
history up to the signal date (EMA200, >=200 sessions) and 20-session relative strength reads its
tail, so the paper entry binding now carries
`REGIME_RS_HISTORY` = VNINDEX WINDOW `[first VNINDEX session, signal date]`. Trade legs
(`qualify_closed_trades`, `qualify_observation_series`) are evaluated against
`trade_leg_symbols(symbol)` = `{traded symbol, VNINDEX}` instead of the traded symbol alone, so a
revision inside the consumed VNINDEX window quarantines the trade / observation, a revision after
the window or an unrelated symbol does not, and a legacy VNINDEX history yields `BOUND_LEGACY_INPUT`.
Breadth stays dataset-level for V1. Not bound: the execution-session VNINDEX row used only to resolve
the next session date (existence, not a value).
