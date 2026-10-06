# Owner Contract Resolution: Q70 and V3 (Phase 3C)

Date: 2026-10-06. Status: decision record only. No production code, records or environment were changed, and B5/B6 are not implemented.

**Provenance limitation (owner statement).** The VPS that ran historical Q70 paper no longer exists. Its `.env` and runtime environment cannot be inspected or reconstructed. Historical paper runtime behavior is therefore classified only from evidence held in the records themselves.

## RESULT

**CONDITIONAL.**

- **Entry model.** The canonical Q70/V3 entry model, for research and for the future, is `hybrid_trend_donchian` / `trend_context`, with high confidence.
- **Historical paper.** Historical Q70 paper runtime is classified `LEGACY_UNVERIFIED`. That label already exists in the repository (`quantlab/ingestion_provenance.py`, `quantlab/operational_admission.py`). The reason code is `ENTRY_MODEL_NOT_ENFORCED`. Individual records can be upgraded only by record-level evidence.
- **Already settled by evidence:**
  - max open positions = 10 (the lifecycle's 5 is dormant);
  - the fixed-fraction mismatch is dormant;
  - fill target 5.0, timing `next_open`, sizing `atr_risk` 1%, the research-parity limits and costs.
- **Still need an owner choice.** Three items have clear evidence and a recommendation, but adopting the recommendation would change runtime:
  - maximum holding;
  - regime caps;
  - ranking tie-break.

**Correction to the Phase 3 design doc.** Paper maximum holding is counted in **market sessions**, not calendar days. `PaperLifecycleManager._count_holding_sessions` passes `holding_sessions` to `ExitEngine`. The difference with research is 20 vs 30 *sessions*.

## ENTRY MODEL DECISION

| Item | Decision | Confidence |
|---|---|---|
| Canonical research Q70 | `hybrid_trend_donchian` / `trend_context` (weights 0.4/0.6, `min_hybrid_score` 60, regime thresholds on, hard score on) | High. The frozen evaluator hardcodes and validates it; `Q70_FROZEN.entry_model`; persisted `policy_fingerprint.json`; registry record; label string |
| Canonical future Q70 | Same, **FIXED** | High. Equals the current `TradingPolicy` default and the V3 wrapper pin. Under the current wrapper env, adopting it changes no runtime behavior; it only blocks a `TRADING_ENTRY_MODEL=trend` override |
| Historical Q70 paper runtime | `LEGACY_UNVERIFIED` / `ENTRY_MODEL_NOT_ENFORCED` | High that it is *unverifiable as a class*. The V2 wrapper never pinned the entry model, and the VPS `.env` is gone |

**Limitations.**

- Even a record that matches "hybrid" proves the *selector*, not the code version that was deployed. The commit running on the old VPS is unknown.
- Record-level upgrade (see "Historical paper classification") requires a surviving copy of the Q70 paper store. Without one, every historical Q70 paper record stays `LEGACY_UNVERIFIED`.

## MAX HOLDING DECISION

| | |
|---|---|
| Research | 20 sessions. `BacktestConfig.max_holding_days` default; the frozen evaluator never overrides it. `_simulate_exit` exits at the close of bar `entry_index + 20` |
| Paper | 30 sessions. `TradingPolicy.maximum_holding_days` default (introduced 2026-08-24 in `b59a18a`), env `TRADING_MAX_HOLDING_DAYS`, not pinned. Each position records its own value in `paper_position_lifecycle.maximum_holding_days` |
| Strongest evidence | Both defaults were set independently. No document, test or prereg chooses 30 for Q70, and 20 is what every Q70 research result used |
| Intentional? | **Accidental divergence**: two unrelated defaults, never reconciled |
| Proposed canonical | **20 sessions** (the research-validated value), as a contract value. Status **UNRESOLVED until owner approval** |
| Runtime change? | **Yes.** New paper positions would exit after 20 sessions instead of 30. Open positions keep their recorded per-position value |
| Historical interpretation? | No. Historical positions are read from their own `maximum_holding_days` column. Where that column is null, they are `LEGACY_UNVERIFIED` |
| Confidence | High on facts. Medium on equivalence: research exits at that session's close, and B6 must test whether paper's time exit fills at the same session's close or the next open |

## REGIME CAPS DECISION

| | |
|---|---|
| Research | None. The frozen evaluator builds `PortfolioSimulator` without `regime_policy` |
| Paper | `RegimePortfolioPolicy()` defaults in the executor (present since 2026-08-26, before Q70 paper started on 2026-09-18): BULL ≤5 positions / 5% heat; SIDEWAY ≤3 / 4%; BEAR blocked; UNKNOWN blocked |
| Strongest evidence | Code: `PaperSignalExecutor.__init__` uses `regime_policy or RegimePortfolioPolicy()`. Research metrics record no regime policy |
| Intentional? | Intentional as a paper **risk overlay** (a deliberate executor feature). **Never validated with Q70.** The BEAR block is redundant with the gate. The UNKNOWN block and the BULL/SIDEWAY position and heat caps are real differences: with an open-position limit of 10, caps of 5 and 3 bind |
| Classification | **Execution overlay**, not signal contract |
| Proposed canonical | Keep the current paper behavior as a named overlay, `REGIME_CAPS_V1`, status CONFIGURABLE_BUT_RECORDED. Paper results must not be presented as reproducing research until the overlay is validated or removed (a research decision, out of scope here) |
| Runtime change? | No, for the recommendation. Removing the caps would change runtime |
| Historical interpretation? | Paper history is "Q70 signals + REGIME_CAPS_V1 execution", not research-equivalent execution |
| Confidence | High on facts. Medium on the recommendation, which is an owner choice |

## RANKING DECISION

| | |
|---|---|
| Research | `rank_candidates(method="signal_score")`: stable sort by `signal_score` descending. Ties keep candidate-generation order |
| Paper | `strategy/scanner.py` `sort_key = (score, relative_strength_20d, volume_ratio, adx)` descending. The executor processes queued signals in that order, up to `maximum_orders_per_scan` = 3. Full ties keep scan iteration order |
| Strongest evidence | Same primary key. They differ **only on equal scores**, which are common (score is a normalized 0–100 composite). The v4-instrumented volume-priority study exists precisely because tie-slot priority changes executions |
| Intentional? | Accidental. The paper key predates Q70 (display and scan ordering) |
| Proposed canonical | Primary key `score` desc is **FIXED**. Tie-break: recommend paper's explicit `(relative_strength_20d, volume_ratio, adx)` desc, plus a final `symbol` asc for full determinism. Status UNRESOLVED (owner) |
| Runtime change? | Explicit tie-breaks: none for paper. Adding the final `symbol` tie-break changes paper only on full ties. Research reproductions would differ on ties and must be labeled with their ranking method |
| Historical interpretation? | Research results stay "signal_score + generation-order ties". Paper history stays "scan order" |
| Confidence | High on facts, medium on the recommendation |

## MAX OPEN POSITIONS DECISION

| | |
|---|---|
| Research | 10 (`paper_execution.maximum_open_positions` in the v4 artifact; `Q70_FROZEN.max_open_positions` = 10) |
| Paper entries | 10. The executor's `PaperExecutionConfig.maximum_open_positions` is the only entry path that checks it |
| Paper lifecycle | 5, but **dormant**. The lifecycle only places SELL orders, and `RiskGuard.validate_order` returns for SELL before the open-position, exposure and cash checks. `OrderManager.sell_market` passes no `daily_realized_pnl`, so the lifecycle's daily-loss limit is inert too. Only the kill-switch, price and duplicate checks apply to lifecycle sells |
| Intentional? | Accidental. One environment variable has two defaults, and only one of them is ever consulted |
| Proposed canonical | **10, FIXED.** Treat lifecycle risk limits as NOT_APPLICABLE (dormant) rather than as contract values |
| Runtime change? | No |
| Historical interpretation? | No. The 5 never constrained anything. `runtime_configuration` v2 recording 5 is misleading but historical, so leave it |
| Confidence | High (code path traced). B6 should add a test that pins "lifecycle limits are dormant" |

## EXECUTION SETTINGS DECISION

| Setting | Research | Paper now | Proposed | Runtime change | Confidence |
|---|---|---|---|---|---|
| Signal stop/target | ATR 2/5 (fixed exit model) | `TRADING_*_ATR_MULTIPLIER` 2/5, pinned by both wrappers | FIXED 2.0/5.0 | No | High |
| Fill stop | ATR 2 | `PAPER_ATR_STOP_MULTIPLIER` 2.0, pinned | FIXED 2.0 | No | High |
| Fill target | ATR 5 | `PAPER_ATR_TARGET_MULTIPLIER`: default 5.0, pinned for V3 only | FIXED 5.0 | No, under the current env | High |
| Trailing | off | `PAPER_V2_DISABLE_TRAILING=true`, pinned by both | FIXED off | No | High |
| Timing | next-bar open | `next_open` (`TradingPolicy.validate` rejects anything else) | FIXED | No | High |
| Holding | 20 sessions | 30 sessions | see "Max holding decision" | yes, if adopted | — |
| Sizer | `atr_risk` 1% (v4 artifact) | `atr_risk` 1% (env default) | FIXED | No | High |
| Max orders/scan | 3 (`max_new_positions_per_day`) | 3 | FIXED | No | High |
| Max position / gross / cash buffer / daily loss | 20% / 80% / 5% / 3% (artifact parity) | same (executor defaults) | FIXED | No | High |
| Max open positions | 10 | 10 (executor) | FIXED | No | High |
| Lot size | 100 | 100 | FIXED (exchange rule) | No | High |
| ADTV20 order cap | not shown in the research artifact | 1% (executor) | CONFIGURABLE_BUT_RECORDED; research parity unknown | No | Low |
| Commission / slippage / sell tax | 0.0015 / 5 bps / 0.001 | same | FIXED for contract v1 (a change makes a new contract version) | No | High |
| Initial cash | run setting | run setting | Run identity, not contract | — | — |

Every "FIXED" row above is fixed at the value the current default environment already resolves to. Adopting them changes nothing **unless** the current `.env` overrides one of them. That is a local check, not a VPS one.

## FIXED_FRACTION ASSESSMENT

- **Where each value lives.** `PaperExecutionConfig.fixed_fraction_pct` is 10 and feeds the executor's `FixedFractionSizer`. `TradingPolicy.fixed_fraction_pct` is 20 and feeds the policy-level sizer used by backtesting helpers.
- **Dormant.** Both are inert while `position_sizer = atr_risk`, which is the paper default and the research v4 setting.
- **Intent.** Accidental. The two defaults were introduced independently (2026-08-05 and 2026-08-24).
- **Proposal.** Sizer **FIXED = atr_risk**. Record `fixed_fraction_pct` as NOT_APPLICABLE while `atr_risk` is fixed. If a fixed-fraction variant is ever wanted, it must be a new contract with one value.
- **Impact.** No runtime change and no change to how history is read.
- **Confidence.** High for paper. Medium for research: B5-A should confirm that `BacktestConfig.position_size_pct`, which is set from `parity.fixed_fraction_pct` in the evaluator, is unused under `atr_risk`.

## FINAL Q70 CONTRACT

**Q70_SIGNAL_CONTRACT**

| Field | Value | Status |
|---|---|---|
| Strategy | `Q70_FROZEN` | FIXED |
| Eligibility | VN100 scanner universe; `min_data_rows` 80; RS period 20 vs VNINDEX | FIXED (membership snapshot = run identity) |
| Features | EMA 10/20/50, RSI14, ATR14, ADX14 (Wilder), VolMA20, 5-session volume breakout, 20-session Donchian high excluding the current bar, 3-session return | FIXED (declared constants `2026-10-06.v1`) |
| Regime classifier | VNINDEX EMA50/EMA200, 10-session slope, 20-session return > −2%, ≥200 sessions | FIXED (declared) |
| Regime thresholds | from `config/strategy.yaml`, the 7 consumed keys × 4 regimes. For BULL, SIDEWAY, BEAR, UNKNOWN in that order: `min_score` 60/66/74/72, `min_adx` 18/20/24/22, `min_volume_ratio` 1.00/1.10/1.25/1.20, `min_relative_strength` 0/1/3/2, `max_distance_ema20` 12/9/7/8, `max_return_3d` 16/13/10/12, `watchlist_margin` 10/10/12/10 | FIXED |
| YAML `rr_ratio`, `atr_stop_multiplier` | overwritten in production | NOT_APPLICABLE (excluded) |
| Entry model | hybrid / `trend_context`, 0.4/0.6, `min_hybrid_score` 60, regime thresholds on, hard score on, default Trend V1 and Donchian sub-models | FIXED (future). Historical paper: LEGACY_UNVERIFIED |
| RSI band | 45–72 | FIXED |
| Quality gate | {score, RS20, ADX}, equal mean, `searchsorted` right over finite values, missing → 0.5, same-session evaluation universe, threshold 0.70 | FIXED |
| Gate states | reject BEAR and DIVERGENT_BULL (BULL & breadth<50 & Δ<0) | FIXED |
| Market state | % above EMA50, ≥50 sessions history, 10-session change | FIXED |
| Breadth exposure | none | FIXED |
| Sector RS 60D | telemetry only | NOT_APPLICABLE |

**Q70_EXECUTION_CONTRACT**

| Field | Value | Status |
|---|---|---|
| Timing | `next_open` | FIXED |
| Signal levels | ATR stop 2.0 / target 5.0 | FIXED |
| Fill levels | ATR stop 2.0 / target 5.0 | FIXED |
| Trailing | disabled | FIXED |
| Max holding | currently 30 sessions; recommend 20 | UNRESOLVED |
| Sizer | `atr_risk`, risk 1.0% | FIXED |
| `fixed_fraction_pct` | dormant | NOT_APPLICABLE (recorded) |
| Orders/scan · position · gross · open positions · cash buffer · daily loss | 3 · 20% · 80% · 10 · 5% · 3% | FIXED |
| Lifecycle risk limits | dormant (sell-only path) | NOT_APPLICABLE (recorded) |
| ADTV20 cap | 1% | CONFIGURABLE_BUT_RECORDED |
| Regime caps | `REGIME_CAPS_V1` (current defaults) | UNRESOLVED (recommend CONFIGURABLE_BUT_RECORDED overlay) |
| Ranking | `score` desc | FIXED |
| Ranking tie-break | RS20, volume ratio, ADX desc (+ symbol?) | UNRESOLVED |
| Costs | commission 0.0015, slippage 5 bps, sell tax 0.001, lot 100 | FIXED |
| All execution fields, pre-v3 paper records | — | LEGACY_UNVERIFIED unless the per-position lifecycle row records them |

"NOT_APPLICABLE" is used only for fields that are proven inert. It is recorded in the identity so nothing is hidden.

## FINAL V3 CONTRACT

**V3_SIGNAL_CONTRACT.** Identical to Q70, except for these fields:

| Field | Value | Status |
|---|---|---|
| Strategy | `V3_BREADTH_40_60` | FIXED |
| Breadth exposure | breadth ≥60 → 1.0×, 40–60 → 0.5×, <40 → 0, missing → 0; applied to order quantity | FIXED |
| Entry model | hybrid / `trend_context` | FIXED (wrapper-pinned since 2026-09-21). Historical V3 paper: LEGACY_UNVERIFIED (deployed wrapper version unprovable) |

**V3_EXECUTION_CONTRACT.** Identical to Q70, with the same three UNRESOLVED items: holding, regime caps, tie-break. The fill target is already pinned by the wrapper. `PAPER_DISABLE_TRAILING` is pinned but inert; the lifecycle reads `PAPER_V2_DISABLE_TRAILING`, which is also pinned.

## HISTORICAL PAPER CLASSIFICATION

The repository label is `LEGACY_UNVERIFIED`, carried with machine-readable reason codes. Records are never mutated. Classifications live in a separate append-only attestation map keyed by `record_identity` or `(store, table, row id)`.

| Case | Label | How it may be upgraded |
|---|---|---|
| No policy fingerprint (positions before 2026-09-28, legacy rows) | `LEGACY_UNVERIFIED` + `NO_POLICY_FINGERPRINT`, `ENTRY_MODEL_NOT_ENFORCED`, `RUNTIME_ENVIRONMENT_UNAVAILABLE` | Entry model only: the persisted signal payload (`paper_pending_signals.payload` / market-DB signal row) contains `entry_model`. `"hybrid_trend_donchian_v1__trend_context"` → `ENTRY_MODEL_RECORD_ATTESTED`. Execution fields only from that row's own lifecycle columns (stop, target, holding) |
| Fingerprint exists | exact match with the enumerated canonical candidate (`072312b7…` = hybrid + V2 pins + defaults) → `POLICY_FINGERPRINT_MATCHED` (all `TradingPolicy` fields incl. entry selector and holding). Match with the trend candidate (`72f0a8fd…`) → `POLICY_FINGERPRINT_MATCHED_NONCANONICAL`. No match → `LEGACY_UNVERIFIED` + `POLICY_FINGERPRINT_UNMATCHED` (no brute-forcing) | Never covers YAML, gate, regime caps or executor limits; those stay `LEGACY_UNVERIFIED` + `SIGNAL_CONTRACT_NOT_FINGERPRINTED` |
| v3 identity recorded (new evidence) | `STRATEGY_IDENTITY_V3_RECORDED`, then `CONTRACT_MATCHED` if it equals the canonical golden for that contract version, else `CONTRACT_DEVIATION` with the differing block names | Automatic comparison |

All historical labels also carry `DEPLOYED_CODE_VERSION_UNKNOWN`, because the VPS commit is gone. Without a surviving paper-store copy, every historical record stays `LEGACY_UNVERIFIED`.

## B5 READINESS

| Part | Verdict | Why |
|---|---|---|
| A. Research attestation | **GO** | Code plus persisted artifacts prove the entry model, gate, threshold, exits and parity limits. Label `RECONSTRUCTED_FROM_CODE_AND_ARTIFACT`, not "recorded". `strategy.yaml` is unchanged since 2026-07-30, before the v4 artifact. Declared code literals remain a limitation |
| B. Historical paper attestation | **BLOCKED** as a class. **CONDITIONAL** per record | Only records with record-level evidence (signal payload `entry_model` or an exact candidate-fingerprint match) in an available store copy. Everything else stays `LEGACY_UNVERIFIED` |
| C. Future paper attestation | **GO** | B3 already records v3 fingerprints on new evidence. Attest against a *versioned* canonical contract; the golden changes only when the owner resolves holding, caps or tie-break |

## B6 READINESS

Safe to enforce from now on, meaning no runtime change under the current wrapper environment; enforcement only blocks drift:

- Entry model hybrid / `trend_context` for Q70 and V3.
- `exit_model` atr, timing `next_open` (already validated).
- Signal stop/target 2/5, fill stop 2, fill target 5, trailing off.
- Quality gate threshold, features and state rules; V3 breadth constants.
- `PAPER_V2_QUALITY_THRESHOLD` must be absent or 0.70 (currently inert).
- Consumed YAML thresholds, via `signal_identity` against the golden.
- Sizer `atr_risk` 1%; orders/scan 3; position 20%; gross 80%; open positions 10; cash buffer 5%; daily loss 3%; lot 100; costs.

**Precondition.** Check the *current* local/production `.env` once for overrides of these keys.

Not safe yet:

- max holding (pending decision);
- regime caps (pending);
- ranking tie-break (pending);
- ADTV cap (research parity unknown);
- lifecycle risk limits (dormant; mark N/A, don't enforce);
- `fixed_fraction_pct` (dormant);
- declared code literals (enforcing declared values does not detect code drift; needs an AST/source digest first).

## REMAINING UNCERTAINTIES

1. Deployed code version on the old VPS, which is unknowable.
2. Whether a copy of the historical Q70/V3 paper stores still exists.
3. Exact equivalence of the time-exit fill (research: session close; paper: lifecycle exit path).
4. Whether the research ADTV/capacity model matches the paper's 1% ADTV20 cap.
5. Whether `BacktestConfig.position_size_pct` is fully unused under `atr_risk` in research.
6. How often score ties occur, and the measured effect of regime caps. Both are research questions, not run here.
7. Declared code literals are not drift-detected.

## RECOMMENDATION

**CONDITIONAL GO.**

- **Adopt now (no runtime change):**
  - entry model FIXED hybrid / `trend_context`;
  - historical paper = `LEGACY_UNVERIFIED`;
  - max open positions 10 with lifecycle limits marked N/A;
  - `fixed_fraction` N/A;
  - all FIXED execution settings above.
- **Owner decisions needed** (each changes runtime or interpretation):
  - holding: 20 recommended;
  - regime caps: keep as a recorded overlay recommended;
  - tie-break: explicit paper order recommended.
- **Next steps.** Once these are decided:
  - B5-A and B5-C can proceed;
  - B5-B stays blocked except for per-record evidence;
  - B6 can enforce the "safe" list after a one-time local `.env` check.

## Implementation status: B5-A, B5-C and safe B6 (2026-10-06)

The owner accepted the decisions above with these final choices:

- holding = 20 market sessions;
- regime caps kept as the `REGIME_CAPS_V1` overlay;
- ranking unchanged, with no `symbol` tie-break;
- max open positions = 10, with the lifecycle's 5 NOT_APPLICABLE;
- `atr_risk` sizing at 1%.

**Holding compatibility, verified before enforcing.** Paper and research time exits fill at the same point: the close of the 20th session after the entry session. Research does this in `_simulate_exit` (close of bar `entry_index + 20`). Paper does it with `_count_holding_sessions` + `ExitEngine` (`TIME_EXIT` at `bar.close_price` when held sessions ≥ 20). A test pins this. One residual edge case: research counts the symbol's own bars, while paper counts VNINDEX sessions, so the two differ only when the symbol skips sessions (halts).

**Where each piece lives:**

- **Canonical contract.** `quantlab/strategy_contract.py` holds the explicit execution list. The signal snapshot is in `config/strategy_contracts/canonical_signal_v1.json`, version `canonical-2026-10-06.v1`.
- **B6, enforcement.** `enforce_strategy_contract` fails closed with field paths. It runs at two places:
  - `app/strategy_scan.run_strategy_scan`, before `run_scan`;
  - `scripts/run_paper_lifecycle.main`, before the first paper-store write.
- **B5-C, evidence.** New evidence records carry v3 fingerprints, contract version, status (`CONTRACT_MATCHED` / `CONTRACT_DEVIATION`), deviation paths and the execution overlay. Old records keep `record_identity` byte-for-byte and are labeled `LEGACY_UNVERIFIED` / `NO_STRATEGY_IDENTITY_V3` (derived, never stored).
- **B5-A, research attestation.** `quantlab/research_attestation.py` writes to the append-only registry `research/attestations/strategy_identity_v3_research.jsonl`. It holds one record for `frozen_q70_paired_full_2018_2026_v4_instrumented`, labeled `RECONSTRUCTED_FROM_CODE_AND_ARTIFACT`.
- **B5-B.** Not implemented. It remains per-record and conditional.

## Review fixes (independent adversarial review, 2026-10-06)

- **P1-1.** Daily runs `app.strategy_scan.preflight_strategy_contract` right after argument parsing and before market bootstrap, market update, forward evidence, lifecycle and scan. It uses the same pinning and enforcement as the scan and lifecycle boundaries, which keep their own checks as defense in depth.
- **P1-2.** Before any scan side effect, `_verify_retained_executor` checks a `PaperSignalExecutor` retained by a reused `strategy.scanner` module against the selected strategy. The check covers the store path, the broker store, every `PaperExecutionConfig` field, the fill policy and the regime overlay. A mismatch fails closed; nothing is replaced. Fresh processes have no retained executor and are unaffected.
- **P1-3.** `scripts/migrate_open_positions_policy.py` never fills a NULL holding from today's default. It fills only from the position's own entry-order `execution_context.lifecycle.maximum_holding_days`; everything else is left NULL as `NO_EVIDENCE`.
- **P2-1.** Research attestation v2 validates each claim against every persisted source that records it. A contradiction refuses the attestation; missing evidence is reported as `UNPROVEN`. The v1 registry line is retained (append-only), and v2 supersedes it.
- **P2-2.** Contract status is derived from the identity at the evidence boundary (`capture_prospective_portfolio_evidence(strategy_identity_v3=...)`). Records check that:
  - the fingerprints bind together (`strategy_identity_v3` = combine(strategy, signal, execution));
  - the attestation metadata is complete;
  - MATCHED has no deviations and DEVIATION has at least one;
  - a MATCHED signal identity equals the canonical one.
- **Tests.** The suite never loads the developer `.env` (`tests/conftest.py`).

**Known P2 limitation (unchanged).** The 20-session holding is counted on the symbol's own bars in research and on VNINDEX sessions in paper. A halted or missing symbol bar can shift the time exit by a session.

## Second review fixes (three P2 findings, 2026-10-06)

- **P2-1.** New evidence records store the normalized signal and execution contracts.
  - **What the record recomputes:** on construction (and therefore on every read), the record recomputes `signal_identity`, `execution_identity`, `strategy_identity_v3`, the canonical comparison, the deviation paths, the status and the overlay from those contracts.
  - **What must match:** stored values must equal the derived ones exactly.
  - **Unknown versions:** an unknown contract version cannot carry a status.
  - **Capture side:** `evidence_attestation_fields` rebuilds the identity from its contracts and rejects stale fingerprints.
- **P2-2.** Research attestation v3 checks every value of every claim alias in all JSON and CSV files at the artifact root and in the arm folder. That covers aliases such as `policy`/`frozen_q70` and `parity`/`paper_parity`, persisted `trailing_enabled`, and CSV columns such as `quality_threshold`. Any contradiction refuses the attestation. `execution_timing` is claimed and reported `UNPROVEN` when nothing records it. The v1/v2 registry lines are kept and v3 is appended.
- **P2-3.** The retained-executor check compares the executor's actual `position_sizer` object against what a fresh executor builds from the selected configuration. Dataclass equality covers the type and every active parameter.

## Third review fixes (four P2 findings, 2026-10-06)

- **P2-1.**
  - **Immutable contracts:** a record's validated signal and execution contracts are frozen (read-only mappings and tuples).
  - **Independent exports:** `as_dict()` returns an independent plain copy.
  - **Append re-validation:** `ProspectivePortfolioEvidenceLedger.append` rebuilds the record from its current payload, which recomputes every fingerprint, the comparison, the deviation paths, the status and `record_identity`. It rejects any difference from the cached values before writing, and refuses new early-v3 records.
- **P2-2.** Three identity formats are recognized:
  - `PRE_V3`: no v3 metadata.
  - `EARLY_V3_INCOMPLETE`: v3 fingerprints and/or status from Phase 3B/3D, with no embedded contracts. It stays readable with its payload and `record_identity` unchanged. Contracts are never invented. Its stored status is exposed only as `historical_contract_status`. Its provenance is `LEGACY_UNVERIFIED` / `LEGACY_INCOMPLETE_V3_IDENTITY`, and `strategy_identity_verification` reports the same.
  - `CONTRACT_COMPLETE_V3`: fully recomputed and verified.
  - The fixtures in `tests/fixtures/evidence_compat/` were produced with the historical code.
- **P2-3.** Research attestation v4 scopes evidence per claim to explicit contexts: file category, block path, key or column, and value type.
  - Same-named keys elsewhere are ignored, such as benchmark settings, example configs and unrelated CSV columns.
  - All legitimate aliases are still checked (`policy`/`frozen_q70`, `paper_execution`/`parity`/`paper_parity`).
  - The v1–v3 registry lines are kept and v4 is appended.
- **P2-4.** Every observed value is validated individually with type-aware equality. A bool never equals 1; numbers never accept bools; CSV `1` stays an int. There is no de-duplication, so CSV row order cannot change a result.
