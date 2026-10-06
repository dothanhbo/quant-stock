# Strategy Identity v3: Design and Evidence (Phase 3)

Date: 2026-10-06. Status: DESIGN ONLY, nothing implemented. Read-only phase: no code, config, env, DB or evidence changes.
Telegram token finding: REMEDIATED / CLOSED (historical record only).

## 1. RESULT

**CONDITIONAL GO for the design.** Q70 and V3 can be described as a closed, explicit behavioral contract. The current fingerprints are incomplete and partly polluted:

- **Missing behavior.** No existing fingerprint covers the entry model's internal parameters, the YAML regime thresholds, indicator windows, the regime classifier, the quality-gate state rules, ranking, or the breadth rule constants.
- **Pollution.** `runtime_configuration` v2 mixes in paths, store ids, cash and other run context, so one strategy can produce many hashes.
- **Q70 entry model.** The intended value is `hybrid_trend_donchian` (trend_context), with high confidence. Q70 runtime does not enforce it, because the V2 wrapper never pins `TRADING_ENTRY_MODEL`. The VPS `.env` therefore decides it, and I cannot see that file.

**Blocking condition.** The owner must confirm, read-only, which entry model the Q70 paper store actually ran (§4). Until then, binding Q70 paper history to a v3 identity is BLOCKED. The design and research-side binding are not blocked.

**Additional finding.** Several research↔paper differences are now visible, though none were changed:

- max holding: 20 sessions vs 30 days;
- regime portfolio caps: absent vs applied;
- ranking: `signal_score` vs scan order;
- `PAPER_MAX_OPEN_POSITIONS`: default 10 in the executor vs 5 in the lifecycle.

These are owner decisions (§14).

## 2. Q70 CONTRACT MAP

| Layer | Contract element | Source of truth today | Pinned? |
|---|---|---|---|
| Identity | `Q70_FROZEN` | `config/paper_store.py`, `config/strategy_config.py` | yes (code) |
| Universe | VN100 scanner universe; `min_data_rows` 80; RS vs VNINDEX over `rs_period` 20 | `strategy/scanner.py` + `config/strategy.yaml` common | YAML (unpinned file) |
| Indicators | EMA10/20/50, RSI14, ATR14, ADX14, VolMA20, 5d volume breakout, 20d Donchian high excl. current bar | `strategy/indicators.py` constants | code |
| Regime | VNINDEX EMA50/EMA200, 10d slope, 20d return > −2%, ≥200 sessions → BULL/SIDEWAY/BEAR/UNKNOWN | `strategy/market_regime.py` | code |
| Regime thresholds | `min_score`, `min_adx`, `min_volume_ratio`, `min_relative_strength`, `max_distance_ema20`, `max_return_3d`, `watchlist_margin` per regime | `config/strategy.yaml` regimes | YAML (unpinned) |
| Entry model | `HybridTrendDonchianEntryModel(mode="trend_context")`, weights 0.4/0.6, `min_hybrid_score` 60, regime thresholds on, hard score required; TrendV1 score weights (25/15/12/10/18/20→100); RSI 45–72 | `strategy/hybrid_trend_donchian_entry.py`, `trend_strategy_v1.py`, `scoring.py`, `filters.py`, YAML common | **NOT pinned for Q70**: `TradingPolicy` default "hybrid", env `TRADING_ENTRY_MODEL` overrides |
| Quality gate | `PaperV2QualityGate`: equal-weight percentile of {score, RS20, ADX} over all scanner evaluations (searchsorted right); reject BEAR, DIVERGENT_BULL (breadth<50 & Δ<0), quality<0.70 | `strategy/paper_v2_gate.py`, `app/strategy_scan.py` `Q70_QUALITY_THRESHOLD` | code (`PAPER_V2_QUALITY_THRESHOLD` env is **inert**, but it is fingerprinted) |
| Market state | breadth = % of universe > EMA50 (≥50 sessions), Δ over 10d | `strategy/market_state.py` | code |
| Signal levels | stop/target = `TradingPolicy.calculate_levels` (ATR 2×/5×) | scanner `evaluate_prepared_row` | wrapper pins `TRADING_STOP/TARGET_ATR_MULTIPLIER` |
| Fill-time levels | executor policy = TradingPolicy with stop=`PAPER_ATR_STOP_MULTIPLIER` (pinned 2.0), target=`PAPER_ATR_TARGET_MULTIPLIER` (**not pinned for V2**, default 5.0) | `execution/signal_executor.py` | partial |
| Exit | ATR fixed, no trailing (`PAPER_V2_DISABLE_TRAILING=true`), time exit `TRADING_MAX_HOLDING_DAYS` default 30 | `config/trading_policy.py`, `scripts/run_paper_lifecycle.py` | trailing pinned; holding not pinned |
| Execution timing | `next_open` | TradingPolicy default (env) | not pinned |
| Sizing/portfolio | `atr_risk` 1%, max orders/scan 3, lot 100, max position 20%, gross 80%, open positions 10 (executor) / 5 (lifecycle), daily loss 3%, cash buffer 5%, ADTV cap; `RegimePortfolioPolicy` defaults (BULL max 5 / heat 5%, SIDEWAY 3 / 4%, BEAR blocked, UNKNOWN skipped) | `signal_executor.py`, `run_paper_lifecycle.py`, `backtesting/regime_policy.py` | env defaults, not pinned |
| Costs | commission 0.0015, slippage 5 bps, sell tax 0.001 | executor / TradingPolicy | env defaults |

## 3. V3 CONTRACT MAP

V3 inherits every Q70 element above. It differs in these ways:

| Element | V3 value | Source |
|---|---|---|
| Identity | `V3_BREADTH_40_60` | `config/paper_store.py` |
| Entry model | `hybrid` **pinned** (`TRADING_ENTRY_MODEL=hybrid` since 2026-09-21) | `scripts/run_paper_v3_lifecycle.py` |
| Fill target | `PAPER_ATR_TARGET_MULTIPLIER=5.0` pinned; `PAPER_DISABLE_TRAILING` and `PAPER_V2_DISABLE_TRAILING` pinned | same |
| Breadth exposure | breadth ≥60 → 1.0×, 40–60 → 0.5×, <40 → 0 (block), missing → 0; applied to quantity at execution | `strategy/breadth_exposure.py` (`V3_BREADTH_VERSION`), executor |
| Gate | same `PaperV2QualityGate` 0.70; `PAPER_V2_ENABLED=false` is set (inert for the canonical path) | `scripts/run_paper_v3.py`, `app/strategy_scan.py` |

Holding days, execution timing, sizing, portfolio limits and costs are not pinned for V3 either.

## 4. Q70 ENTRY MODEL FINDING

The evidence, in the required priority order:

1. **Frozen implementation.** `backtesting/frozen_q70_evaluator.py` hardcodes `HybridTrendDonchianEntryModel()` and `_validate_frozen_policy` asserts `Q70_FROZEN.entry_model == "hybrid_trend_donchian"`. → **hybrid**.
2. **Q70 tests.** `test_strategy_config.py` and `test_run_frozen_q70_volume_priority_wfo.py` reference `hybrid_trend_donchian`. No test asserts which entry model the Q70 *paper runtime* resolves. → **hybrid** (research side only).
3. **Wrappers.** `configure_v2_environment` does **not** set `TRADING_ENTRY_MODEL`. → **silent**: runtime falls back to the `TradingPolicy` default "hybrid" unless `.env` overrides it.
4. **Prereg/research docs.** The registry record `frozen-q70-historical-policy-v1` references `Q70_FROZEN/hybrid_trend_donchian/ATR2x5/fixed`. → **hybrid**.
5. **Persisted artifacts.** `frozen_q70_paired_full_2018_2026_v4_instrumented/.../policy_fingerprint.json` contains `entry_model: hybrid_trend_donchian`. → **hybrid**.
6. **Historical fingerprints.** The policy-fingerprint string is `Q70_FROZEN/hybrid_trend_donchian/ATR2x5/fixed`. → **hybrid**.
7. **Git history.** The `TradingPolicy` default became "hybrid" on 2026-08-24, and Q70 paper started on 2026-09-18. The production scanner used TrendV1 before 2026-08-24, so the generic V1 store spans both entry models. → consistent with hybrid for Q70 paper, **if** `.env` did not override.
8. **README.** Consistent.

**Intended value:** `hybrid_trend_donchian`, mode `trend_context`. Confidence is HIGH for research identity.

**Q70 paper store: not proven.** The model actually used there depends on the VPS `.env`.

**Owner verification (read-only, no mutation).**

Compare the distinct `paper_position_lifecycle.policy_fingerprint` values in `data/paper_trading_v2.db` against these hashes, both computed with default env plus V2 wrapper pins:

- `072312b7aba83b2b1a0d0b8940a0bc7d12139aa85c084ae1b0cc535c699645e7` = `entry_model` "hybrid"
- `72f0a8fdb081ac70c9061af695e64241c0382acc1fae3e1fffd6b744ae88fb23` = `entry_model` "trend"

```sql
-- sqlite3 -readonly data/paper_trading_v2.db
SELECT policy_fingerprint, COUNT(*), MIN(entry_date), MAX(entry_date)
FROM paper_position_lifecycle GROUP BY policy_fingerprint;
```

A third, unknown hash means other env drift (target, holding days, risk). That result would itself be evidence for v3. Also run `grep -n TRADING_ENTRY_MODEL .env` on the VPS (prints the name only, no secrets).

**Do not infer from V3.** V3's pin is not evidence for Q70.

## 5. STRATEGY.YAML TRACE

The file is unchanged since 2026-07-30. It is loaded once at import by `config/strategy_loader.py`. Neither `.env` nor the wrappers override it.

| Key | Consumer | Affects decisions? |
|---|---|---|
| `common.min_data_rows` 80 | scanner eligibility | **A** |
| `common.rsi_min/max` 45/72 | `filters.py`, `scoring.py` | **A** |
| `common.rs_period` 20 | scanner RS | **A** |
| `common.top_results/top_watchlist` 10 | display + Telegram only (paper queue receives all post-gate results) | **B** |
| `common.indicator_cache_size` 256 | `strategy/cache.py` | **B** |
| `regimes.*.min_score, min_adx, min_volume_ratio, min_relative_strength, max_distance_ema20, max_return_3d, watchlist_margin` | `market_regime.build_market_config` → hybrid/Donchian/TrendV1 | **A** |
| `regimes.*.rr_ratio, atr_stop_multiplier` | `risk/levels.py`; **overwritten** in production by `TradingPolicy.calculate_levels`; still read by `backtesting/current_logic.py` | **E**: dead for Q70/V3 production, live for legacy backtests |

Conclusion: hash the **consumed, normalized values**, not the raw YAML bytes. Raw bytes would change on comments and whitespace, and would include the dead `rr_ratio` / `atr_stop_multiplier`.

## 6. CURRENT FINGERPRINT

| Fingerprint | Where | Covers | Missing / polluted |
|---|---|---|---|
| `quantctl.runtime_configuration` v2 (sha256 canonical JSON) | prospective evidence `runtime_configuration_fingerprint` | strategy id, TradingPolicy asdict, PaperExecutionConfig asdict, lifecycle limits, flags | **Missing:** entry internals, YAML thresholds, indicator/regime/gate/breadth constants. **Polluted:** market DB path, store path, override var, initial cash and inert `PAPER_V2_QUALITY_THRESHOLD` all change the hash without changing strategy |
| Executor `policy_fingerprint` | `paper_position_lifecycle.policy_fingerprint` (since `ee96270`, 2026-09-28) | `asdict(TradingPolicy)` with paper stop/target | Entry *name* only. No YAML, gate or breadth. Positions before 2026-09-28 have none |
| `FrozenQ70Policy` v1 | `quantlab/alpha/frozen_q70.py` | name, version, threshold, quality components, weights, percentile method | Gate only. No entry, exit or execution |
| String label `Q70_FROZEN/hybrid_trend_donchian/ATR2x5/fixed` | research manifests, registry | human label | Not a hash; not verifiable |
| Registry `specification_fingerprint` 4079fae3… | `research/alpha_hypotheses/registry_v1.json` | hypothesis spec document | Document identity, not behavior |

None of these answers: "is this the same strategy that was researched?"

## 7. CLASSIFICATION TABLE

| Item | Class |
|---|---|
| Entry model + mode + weights + `min_hybrid_score` + `use_regime_thresholds` + `require_hybrid_score` | A |
| TrendV1 score weights, RSI band, regime trend rules | A |
| Indicator windows (EMA/RSI/ATR/ADX/VolMA/breakout/Donchian, exclude-current) | A |
| Regime classifier constants | A |
| YAML consumed regime thresholds; `min_data_rows`; `rs_period` | A |
| Quality gate: features, weighting, percentile method, universe, threshold 0.70, state rules (BEAR, DIVERGENT_BULL, HEALTHY, RECOVERY) | A |
| Market-state breadth definition (EMA50, 50 sessions, 10d change) | A |
| V3 breadth tiers 60/40, multipliers 1.0/0.5/0, missing→0 | A |
| Exit: model atr, stop 2.0, target 5.0, trailing off, max holding | A |
| Execution timing `next_open` | A |
| Sizing method, risk %, max orders/scan, position/exposure/open-position limits, `RegimePortfolioPolicy`, ranking order | A (execution contract) |
| Costs: commission, slippage, sell tax, lot size | A (execution contract; also C for simulation) |
| `top_results`, `top_watchlist`, cache size, Telegram formatting | B |
| `PAPER_TRADING_ENABLED`, `PAPER_V2_ENABLED`, `PAPER_V2_QUALITY_THRESHOLD` (inert) | B |
| Store id/path, override var, market DB path, git commit, data cutoff, DB content hash, universe snapshot, initial cash, run timestamp | C |
| `TELEGRAM_TOKEN`, chat ids, provider keys | D (never hashed, never stored) |
| YAML `rr_ratio`, `atr_stop_multiplier`; `.env`-only unknown keys | E |

## 8. PROPOSED STRATEGY IDENTITY V3

Contract `quant.strategy_identity` version `v3`, built from an **explicit normalized dict**. It is never built from raw env or raw YAML.

```
{
  "contract": "quant.strategy_identity", "version": "v3",
  "strategy": "Q70_FROZEN" | "V3_BREADTH_40_60",
  "signal": {
    "universe":   {"source": "scanner_vn100", "min_data_rows": 80, "rs_period": 20, "rs_benchmark": "VNINDEX"},
    "indicators": {"ema": [10,20,50], "rsi": 14, "atr": 14, "adx": 14, "volume_ma": 20,
                   "volume_breakout_window": 5, "donchian_window": 20, "donchian_excludes_current": true},
    "regime":     {"index": "VNINDEX", "ema_fast": 50, "ema_slow": 200, "slope_window": 10,
                   "return_window": 20, "return_floor_pct": -2.0, "min_sessions": 200},
    "regime_thresholds": {"BULL": {...7 consumed keys...}, "SIDEWAY": {...}, "BEAR": {...}, "UNKNOWN": {...}},
    "entry": {"model": "hybrid_trend_donchian", "mode": "trend_context", "trend_weight": 0.4,
              "donchian_weight": 0.6, "min_hybrid_score": 60, "use_regime_thresholds": true,
              "require_hybrid_score": true, "trend_score_weights": {...}, "rsi_min": 45, "rsi_max": 72},
    "quality_gate": {"features": ["score","relative_strength_20d","adx"], "weighting": "equal",
                     "percentile": "searchsorted_right", "universe": "all_scanner_evaluations",
                     "threshold": 0.70, "reject_states": ["BEAR","DIVERGENT_BULL"]},
    "market_state": {"breadth_ema": 50, "min_history": 50, "change_window": 10, ...state cutoffs...},
    "breadth_exposure": null | {"version": V3_BREADTH_VERSION, "full": 60, "half": 40, "half_multiplier": 0.5, "missing": 0.0}
  },
  "execution": {
    "timing": "next_open",
    "exit": {"model": "atr", "stop_atr": 2.0, "target_atr": 5.0, "trailing": false, "max_holding": <value + unit>},
    "sizing": {"method": "atr_risk", "risk_per_trade_pct": 1.0, "lot_size": 100},
    "limits": {"max_orders_per_scan": 3, "max_position_pct": 20, "max_gross_exposure_pct": 80,
               "max_open_positions": <owner decision>, "max_daily_loss_pct": 3, "min_cash_buffer_pct": 5, ...},
    "regime_portfolio": {...RegimePortfolioPolicy...} | null,
    "ranking": ["score","relative_strength_20d","volume_ratio","adx"],
    "costs": {"commission": 0.0015, "slippage_bps": 5, "sell_tax": 0.001}
  }
}
```

**Hashes.** Use `quantlab.identity.canonical_json` with sha256, and publish three hashes:

- `signal_identity` = hash(signal) answers "same signals?";
- `execution_identity` = hash(execution) answers "same trading?";
- `strategy_identity_v3` = hash(whole).

This lets research (signal identical, simulated execution) be compared with paper honestly.

**Where the values come from.** Signal values come from the live objects the scanner actually built: the entry-model instance's attributes, the loaded `REGIME_CONFIGS`, gate constants, and the resolved `TradingPolicy` / `PaperExecutionConfig`. They are then compared to a **frozen expected contract** per strategy that is checked into code. The contract records *what ran*, and the check catches drift.

## 9. STRATEGY IDENTITY VS RUN IDENTITY

| Strategy identity (§8): what the rules are | Run identity: what this execution touched |
|---|---|
| Contract A values only | git commit + dirty flag, Python/package versions |
| Stable across machines, paths and dates | market DB path + content hash/max date, data cutoff, universe snapshot hash |
| | paper store id + path, initial cash, run id, timestamps |
| | `strategy_identity_v3` + `signal_identity` + `execution_identity` (by reference) |

`runtime_configuration` v2 continues to exist as a **run** fingerprint. It is not repurposed. Run identity *references* strategy identity; it never re-hashes it.

## 10. BACKWARD COMPATIBILITY

- Nothing historical is rewritten, migrated or re-hashed in place. That includes artifacts, registry, DB columns and evidence records.
- These remain readable and are labeled `legacy_incomplete_identity`:
  - `runtime_configuration` v2;
  - executor `policy_fingerprint`;
  - `FrozenQ70Policy` v1;
  - string labels.
- A one-way **attestation map** is a new file. Each entry is `legacy hash → reconstructed v3 identity + confidence + evidence`. It is only added for cases proven by code at that commit. Q70 paper is attested only after the §4 owner check.
- New columns or fields are additive and nullable. Readers that don't know v3 keep working.
- No change to Q70/V3 decisions, thresholds or env. Phase 2 golden scan fixtures must stay byte-identical.

## 11. FAIL-CLOSED RULES

1. The scan or paper lifecycle refuses to run when the live computed contract ≠ the frozen expected contract for the selected strategy. It raises `StrategyIdentityMismatch` before any side effect (signal save, queue, Telegram). Same pattern as Phase 2 `_verify_scanner_policy`.
2. The Q70 entry model must resolve to `hybrid_trend_donchian` / `trend_context`. Today this is enforced in research but not in paper. **Enforcing it in paper is a behavior gate and needs owner approval (§14 Q1).**
3. Unknown, missing or non-finite contract values raise an error. There are no defaults inside the identity builder.
4. Inert or unknown strategy-affecting env vars (e.g. `PAPER_V2_QUALITY_THRESHOLD` ≠ 0.70) raise an error instead of being silently ignored. This also needs owner approval.
5. Secrets (class D) are excluded by allowlist construction, not by a denylist.
6. Evidence writers refuse to record a run without `strategy_identity_v3`, once v3 is enabled. Legacy readers stay tolerant.

## 12. FUTURE TEST PLAN

- **Golden contract tests.** The exact expected v3 dict and hashes for Q70 and V3, under a hostile env (reusing `tests/scan_parity_support.py`).
- **Sensitivity.** Each class-A field, when mutated, changes the right sub-hash. Each B/C field, when mutated, leaves `strategy_identity` unchanged. Class-D values never appear in serialized output.
- **YAML.** Comment/whitespace edits leave the hash unchanged. A consumed threshold edit changes `signal_identity`. `rr_ratio` edits leave it unchanged, documented as E.
- **Entrypoint parity.** Daily, `quantctl scan`, `python -m strategy.scanner` and Manager produce the identical v3 hash, extending `test_canonical_scan_path.py`.
- **Fail-closed.** `TRADING_ENTRY_MODEL=trend` under Q70 → mismatch before side effects (only once rule 2 is approved).
- **Legacy.** The v2 runtime fingerprint and executor `policy_fingerprint` are byte-identical before and after the change.
- **Regression.** The Phase 2 golden scan fixtures are unchanged.
- **Research/paper.** The frozen evaluator's v3 `signal_identity` equals paper's `signal_identity` for Q70.

## 13. IMPLEMENTATION PLAN

Each batch is separately approved and has no behavior change unless marked.

1. **B1, pure builder.** `quantlab/identity/strategy_identity_v3.py`: frozen expected contracts and `build_strategy_identity(...)` over explicit inputs. Golden and sensitivity tests. Not wired in.
2. **B2, read-only observation.** Compute v3 alongside existing fingerprints in the scan path and in `quantctl` runtime config. Log/report only, never block. Verify parity across entrypoints.
3. **B3, evidence (additive).** Add nullable `strategy_identity_v3` / `signal_identity` / `execution_identity` fields to new prospective evidence and lifecycle records. No migration of old rows.
4. **B4, research binding.** The frozen evaluator emits v3 `signal_identity` in new artifacts. Old artifacts are untouched.
5. **B5, attestation map.** Map legacy hash → v3, research first. Q70 paper is included only after the §4 owner check.
6. **B6, fail-closed (behavior gate, owner approval).** Enforce rules 1–4. Pin the Q70 entry model only if Q1 is answered.

## 14. OPEN QUESTIONS

1. **Q70 paper entry model.** Run the §4 read-only check. If it shows `trend`, Q70 paper history is not the researched strategy. Should that history then be labeled a distinct variant, or restarted?
2. **Max holding.** Research uses 20 *sessions*, paper uses 30 *days* (`TRADING_MAX_HOLDING_DAYS`). Which one is the Q70 contract?
3. **Regime portfolio caps.** Paper applies `RegimePortfolioPolicy`; the frozen research did not. Is this part of the Q70 contract, or a paper-only overlay recorded under execution identity?
4. **Ranking.** Research uses `signal_score`; paper uses scan order (score, RS20, volume ratio, ADX). Which one is canonical?
5. **`PAPER_MAX_OPEN_POSITIONS`.** The executor defaults to 10 and the lifecycle to 5 for the same variable. Which value is the contract?
6. **Env pins.** Should `PAPER_ATR_TARGET_MULTIPLIER`, `TRADING_MAX_HOLDING_DAYS`, `TRADING_EXECUTION_TIMING`, sizing/limits and costs become pinned for Q70 and V3, i.e. env overrides fail closed? Or should they be allowed and recorded in `execution_identity`?

## 15. RECOMMENDATION

**CONDITIONAL.**

- B1 to B4 can proceed once you approve them. They are additive, have no behavior change, and are fully reversible.
- B5 for Q70 paper is blocked until the §4 owner check is done.
- B6 is blocked until owner decisions Q1 to Q6 are made.

## Implementation status: Phase 3B (B1–B4, shadow and additive only)

Implemented on 2026-10-06. Nothing is enforced, pinned, migrated or attested. B5 and B6 are not started.

- **B1, the builder.** `quantlab/strategy_identity.py` is a pure builder: an allowlisted contract, SHA-256 hashes over `quantlab.identity.canonical_json`, and three outputs (`signal_identity`, `execution_identity`, `strategy_identity_v3`) plus per-block fingerprints for diagnosis.
- **B1, the collectors.** `quantlab/strategy_identity_runtime.py` reads the runtime as it was constructed. It reads the environment but never writes it, and `shadow_strategy_identity` turns any failure into `INCOMPLETE`.
- **B2.** `app/strategy_scan.record_shadow_strategy_identity` logs the identity of every canonical scan, built from the scanner's own `TRADING_POLICY` and entry model. `scripts/run_paper_lifecycle.py` computes it before capturing evidence.
- **B3.** `ProspectivePortfolioEvidenceRecord` gains nullable `strategy_identity_v3`, `signal_identity` and `execution_identity` fields. They are included in the record identity only when present, so pre-v3 records keep their exact `record_identity`. No table or column changes were made, and `paper_position_lifecycle` is unchanged.
- **B4.** `run_frozen_q70_backtest` metrics gain `signal_identity` and a signal-only `strategy_identity_v3` block. Every other metric is unchanged.

**Golden values** (default environment plus wrapper pins), as pinned in `tests/test_strategy_identity_v3.py`:

- Q70 `strategy_identity_v3` = `680ee2fb…`, `signal_identity` = `7a4c16c4…`.
- V3 `strategy_identity_v3` = `bc54c7a2…`, `signal_identity` = `f03ae140…`.
- Q70 and V3 share `execution_identity` = `6d8fe10e…` under these pins.

**Known limitation.** Behavior-defining code literals are declared, not introspected: indicator windows, regime classifier, gate state cutoffs and scoring sub-weights, under `DECLARED_CODE_CONSTANTS_VERSION`. Editing one of those literals does not move the identity unless the declared block is updated too. The git commit in run provenance still captures such an edit.
