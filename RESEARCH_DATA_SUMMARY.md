# Quant Stock Research — Data & Findings Summary

> **Release snapshot:** research framework milestone, compiled from the benchmark, walk-forward, portfolio, Monte Carlo, trade-distribution and risk-of-ruin outputs produced during the project.

## 1. Research scope

- **Market:** Vietnamese equities.
- **Primary research universe:** ACB, BID, BSR, DGW, DIG, FPT, FRT, GAS, HCM and HDB.
- **Historical period used by the final portfolio study:** 2018-08-04 to 2026-07-31.
- **Frozen validation baseline — entry model:** `hybrid_trend_donchian_v1__trend_context`.
- **Frozen validation baseline — exit configuration:** ATR stop `2.0`, ATR target `5.0`, maximum holding period `30` days.
- **Current project stage:** historical research and parameter selection are substantially complete; the priority now shifts from further tuning toward unseen-data collection, paper trading and out-of-sample validation.
- **Portfolio assumptions:** initial capital `100,000,000 VND`, maximum `5` concurrent positions, nominal allocation `20%` per position, no margin, transaction costs included.

## 2. Research pipeline completed

```text
Historical Data → SQLite → Indicators → Entry Models → Exit Models
→ Backtesting → Benchmarking → Walk-Forward → OOS Diagnostics
→ Portfolio Simulation → Parameter Stability → Monte Carlo
→ Trade Distribution → Risk of Ruin
```

The framework currently supports configurable Trend and Donchian models, hybrid entry logic, multiple exit models, strategy ablation, walk-forward diagnostics and portfolio-level testing.

## 3. Main strategy research findings

### 3.1 Exit-model benchmark

- On the initial Top-10 benchmark, `trailing_atr` ranked first by average Sharpe, while `atr` produced the higher average return.
- The exit tests showed that fixed exits were generally less competitive than volatility-based exits.
- Subsequent strategy research therefore used ATR-based exits as the common baseline.

### 3.2 Trend ablation

- Full-history in-sample testing favored `trend_v1__no_volume_no_rs` with approximately **+15.26% average return**, **0.32 average Sharpe**, and **7/10 positive symbols**.
- Out-of-sample diagnostics did **not** preserve that advantage. The simpler Donchian breakout model generalized better across the tested windows.
- This was the clearest evidence in the project that **optimization does not imply generalization**.

### 3.3 Donchian ablation

- In-sample, removing the EMA20 distance filter produced the strongest Donchian result: about **+14.17% average return**, **0.43 average Sharpe**, and **6/10 positive symbols**.
- Non-overlapping OOS testing did not confirm a decisive winner. `no_distance`, `no_volume_breakout_score`, `no_overheated`, and the baseline remained close, while removing relative strength was consistently weaker.
- Relative strength therefore remains a useful Donchian filter; the distance and overheating rules are not proven drivers of OOS performance.

### 3.4 Hybrid strategy

- `strict` generated high-quality but very sparse signals and is better treated as a high-conviction overlay than as a standalone strategy.
- `score_blend` generated too many weak signals and was rejected.
- `trend_context` offered the best balance between trade count, return quality and drawdown, and became the portfolio candidate.

## 4. Portfolio benchmark and parameter stability

The first portfolio benchmark using Hold 10 / ATR 2.0 / ATR 4.0 produced only **+0.82%** for Hybrid Trend Context after costs. Parameter stability testing showed that the weakness came primarily from exiting trends too early.

The strongest parameter region was not a single isolated point; several neighboring configurations with Hold 20–30 days and ATR targets 4–5 remained profitable. This suggests a meaningful stability plateau.

| Rank | Hold | ATR Stop | ATR Target | Return | CAGR | Sharpe | PF | Max DD | Trades |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 30 | 2.0 | 5.0 | +70.40% | 7.24% | 1.58 | 1.41 | -13.94% | 204 |
| 2 | 30 | 1.5 | 5.0 | +52.76% | 5.71% | 1.33 | 1.31 | -14.96% | 220 |
| 3 | 20 | 1.5 | 5.0 | +44.76% | 4.97% | 1.21 | 1.30 | -15.60% | 229 |
| 4 | 30 | 2.0 | 4.0 | +41.07% | 4.62% | 1.10 | 1.26 | -15.04% | 209 |
| 5 | 20 | 2.5 | 5.0 | +42.11% | 4.72% | 1.09 | 1.26 | -18.15% | 211 |

**Selected research configuration:** Hold `30`, ATR stop `2.0`, ATR target `5.0`.

## 5. Final trade distribution

| Metric | Value |
|---|---:|
| Total trades | 204 |
| Winning trades | 89 |
| Losing trades | 115 |
| Win rate | 43.63% |
| Portfolio return | 70.40% |
| Portfolio Sharpe | 1.58 |
| Portfolio max drawdown | -13.94% |
| Average trade return | 1.46% |
| Median trade return | -3.92% |
| Average winner | 11.13% |
| Average loser | -6.03% |
| Payoff ratio | 1.85 |
| Profit factor | 1.41 |
| Average holding period | 20.47 days |
| Median holding period | 16 days |
| Longest win streak | 9 |
| Longest loss streak | 9 |
| Transaction costs | 20,695,586 VND |

The system has a win rate below 50%, but the average winner is about **1.85×** the average loser. This is consistent with a trend-following profile: frequent small losses are offset by fewer, larger winners.

### 5.1 Exit reason contribution

| Exit reason | Trades | Win rate | Avg return | Net P&L | Avg hold |
|---|---:|---:|---:|---:|---:|
| TAKE_PROFIT | 56 | 100.00% | +14.00% | 189,783,152 VND | 20.68 days |
| TIME_EXIT | 41 | 80.49% | +4.71% | 48,832,479 VND | 42.22 days |
| STOP_LOSS | 107 | 0.00% | -6.36% | -168,216,199 VND | 12.02 days |

- `TAKE_PROFIT` and long `TIME_EXIT` trades generate the positive edge.
- `STOP_LOSS` is the largest trade category and represents the cost of participating in breakouts.

### 5.2 Holding-period contribution

| Holding bucket | Trades | Win rate | Expectancy | PF | Net P&L |
|---|---:|---:|---:|---:|---:|
| 11-15 | 29 | 55.17% | +5.33% | 3.45 | 43,410,798 VND |
| 31+ | 59 | 74.58% | +5.18% | 5.62 | 68,697,291 VND |
| 21-30 | 30 | 50.00% | +4.42% | 2.52 | 33,429,662 VND |
| 16-20 | 15 | 40.00% | +2.86% | 1.64 | 8,184,121 VND |
| 4-5 | 12 | 16.67% | -2.51% | 0.48 | -6,963,172 VND |
| 6-10 | 40 | 15.00% | -4.38% | 0.25 | -43,905,536 VND |
| 0-3 | 19 | 0.00% | -7.01% | 0.00 | -32,453,731 VND |

The strongest buckets are **11–15**, **21–30**, and **31+ days**. Trades closed within ten days are negative on average. This is one of the clearest findings of the project: the strategy's edge depends materially on allowing successful trends enough time to develop. It directly supports the decision to increase the maximum holding period from 10 to 30 days.

### 5.3 Performance by symbol

Top contributors by expectancy:

| Symbol | Trades | Win rate | Expectancy | PF | Net P&L |
|---|---:|---:|---:|---:|---:|
| DIG | 15 | 60.00% | +5.44% | 2.80 | 20,455,675 VND |
| FRT | 19 | 63.16% | +5.31% | 2.67 | 22,589,072 VND |
| BSR | 17 | 41.18% | +2.17% | 1.75 | 12,315,960 VND |
| ACB | 21 | 57.14% | +2.13% | 1.58 | 7,546,377 VND |
| FPT | 23 | 52.17% | +1.82% | 1.78 | 10,766,145 VND |

Weakest contributors:

| Symbol | Trades | Expectancy | PF | Net P&L |
|---|---:|---:|---:|---:|
| BID | 22 | -1.65% | 0.63 | -8,414,597 VND |
| GAS | 19 | -0.29% | 0.95 | -833,741 VND |
| HCM | 25 | +0.14% | 1.06 | 1,495,942 VND |

These results should not be used to permanently remove symbols without a separate universe-selection and OOS study; symbol-level samples remain small.

### 5.4 Performance by year

Positive-expectancy years: **5/9**. Negative-expectancy years: **4/9**.

| Year | Trades | Win rate | Expectancy | PF | Net P&L |
|---:|---:|---:|---:|---:|---:|
| 2018 | 3 | 0.00% | -8.07% | 0.00 | -4,832,021 VND |
| 2019 | 27 | 29.63% | -0.90% | 0.74 | -4,690,821 VND |
| 2020 | 28 | 50.00% | +2.94% | 1.83 | 14,807,811 VND |
| 2021 | 34 | 47.06% | +2.66% | 1.79 | 19,306,109 VND |
| 2022 | 17 | 29.41% | -0.25% | 0.94 | -1,426,939 VND |
| 2023 | 30 | 63.33% | +3.80% | 2.62 | 29,161,869 VND |
| 2024 | 30 | 46.67% | +1.25% | 1.43 | 10,876,019 VND |
| 2025 | 23 | 26.09% | -0.62% | 0.81 | -5,525,044 VND |
| 2026 | 12 | 58.33% | +3.32% | 2.32 | 12,722,450 VND |

## 6. Monte Carlo robustness

### 6.1 Shuffle test — sequence risk

- 10,000 simulations, preserving all 204 trades and changing only their order.
- Probability of profit: **100%** under the simplified fixed-fraction sequence model.
- Median maximum drawdown: **−14.43%**.
- Probability of drawdown beyond 20%: **9.93%**.
- Probability of drawdown beyond 30%: **0.05%**.
- Worst simulated drawdown: **−30.93%**.

### 6.2 Bootstrap test — sampling risk

- 10,000 simulations, sampling 204 trades with replacement.
- Probability of profit: **98.08%**.
- Probability of loss: **1.92%**.
- Median return: **+73.96%**.
- 5th-percentile return: **+11.96%**.
- Median CAGR: **7.18%**.
- Median maximum drawdown: **−14.67%**.
- Probability of drawdown beyond 20%: **17.43%**.
- Probability of drawdown beyond 30%: **1.48%**.
- Worst simulated drawdown: **−48.09%**.
- Simulated risk of complete ruin: **0%**.

The bootstrap results indicate that uncertainty in future trade quality is more important than the historical ordering of trades. This strengthens the case for validating the frozen strategy on genuinely unseen observations rather than continuing to optimize the same historical sample. These simulations use a simplified fixed-fraction model and do not reproduce portfolio concurrency, cash drag, correlated signals or regime clustering exactly.

## 7. Risk of ruin and position sizing

| Metric | Value |
|---|---:|
| Historical win probability | 43.63% |
| Average winner | 11.13% |
| Average loss | 6.03% |
| Payoff ratio | 1.85 |
| Full Kelly | 13.09% |
| Half Kelly | 6.54% |
| Quarter Kelly | 3.27% |
| Current nominal position size | 20.00% |
| Current size as % of Full Kelly | 152.82% |

Full Kelly is an aggressive theoretical estimate based on the historical sample. Quarter Kelly is a more conservative research reference; neither value should be treated as investment advice.

### 7.1 Risk by horizon at 20% nominal position size

| Horizon | Trades simulated | Profit probability | Median return | 5th-pctl return | Median DD | P(DD > 20%) |
|---:|---:|---:|---:|---:|---:|---:|
| 1 year(s) | 26 | 76.69% | +7.30% | -7.88% | -6.43% | 0.22% |
| 3 year(s) | 77 | 89.79% | +23.07% | -6.14% | -10.39% | 4.81% |
| 5 year(s) | 128 | 94.43% | +41.78% | -0.95% | -12.51% | 9.98% |

### 7.2 Five-year risk by position size

| Position size | Profit probability | Median return | 5th-pctl return | Median DD | P(DD > 20%) | P(DD > 30%) |
|---:|---:|---:|---:|---:|---:|---:|
| 10% | 95.59% | +19.55% | +0.67% | -6.46% | 0.15% | 0.00% |
| 15% | 94.92% | +30.07% | -0.16% | -9.59% | 2.25% | 0.04% |
| 20% | 94.74% | +41.68% | -0.50% | -12.50% | 9.72% | 0.66% |
| 25% | 94.44% | +52.91% | -1.44% | -15.61% | 24.22% | 2.95% |
| 30% | 94.15% | +65.72% | -2.73% | -18.35% | 40.57% | 8.06% |

Position size is the primary risk lever. Increasing allocation raises median return, but drawdown probability rises non-linearly. At 30% allocation, the simulated probability of exceeding 20% drawdown reaches roughly **40.57%**, compared with **0.15%** at 10%.

## 8. Current interpretation

1. The selected baseline is `hybrid_trend_donchian_v1__trend_context` with Hold `30`, ATR stop `2.0` and ATR target `5.0`. This configuration is now treated as **frozen for the next validation stage**, rather than a target for continued historical tuning.
2. The project found a plausible trend-following edge only after allowing winners more time and using a wider ATR target. Holding-period analysis shows that trades closed within ten days were negative on average, while longer holding buckets generated the strongest expectancy.
3. Hybrid Trend Context offers the best observed balance between signal quality and trade frequency relative to the tested alternatives.
4. The edge is driven by **payoff asymmetry and long-held winners**, not by a high win rate: historical win rate is `43.63%`, while the average winner (`+11.13%`) is about `1.85×` the average loser (`−6.03%`).
5. Transaction costs are material and must remain included in all future tests and paper-trading comparisons.
6. Monte Carlo results are encouraging, but future trade quality, market regime and correlated portfolio behavior remain meaningful sources of risk.
7. The research/optimization phase is substantially complete. The next objective is **validation on unseen data with frozen parameters**, supported by continued daily data collection and paper trading.
8. The current configuration remains a research candidate, not a production-ready or investment-recommendation system.

## 9. Limitations

- Small universe of ten symbols and possible survivorship/selection bias.
- Several parameter and model choices were made after observing historical results.
- Overlapping OOS windows were used in some diagnostics; non-overlapping checks were also run but sample size remained limited.
- Portfolio Monte Carlo uses bootstrapped trade returns rather than full event-level resimulation with correlated positions.
- Market liquidity, lot-size constraints, price limits, corporate actions and execution capacity may not be fully represented.
- Results are sensitive to data quality and to the implementation of signal timing, fees, slippage and taxes.

## 10. Recommended next validation steps

1. **Freeze the selected baseline:** keep `trend_context` with Hold `30`, ATR stop `2.0` and ATR target `5.0` unchanged during the initial forward-validation period unless an implementation error is discovered.
2. **Continue daily market-data collection:** preserve raw OHLCV and benchmark data so indicators and future features can be recomputed from the original source.
3. **Run forward and non-overlapping OOS validation:** evaluate the frozen configuration on observations that were not used for model or parameter selection.
4. **Maintain a daily paper-trading journal:** record signals, intended entries/exits, position sizing and portfolio state using the same frozen rules.
5. **Reconcile backtest versus paper execution:** compare theoretical signals with realistic fills, transaction costs, slippage, liquidity constraints, price limits and signal timing.
6. **Add market-regime attribution:** measure performance separately across bull, bear and sideways environments.
7. **Study portfolio concentration and correlation:** introduce correlation-aware allocation and hard exposure limits before considering larger position sizes.
8. **Delay further optimization until new evidence exists:** reopen model research only after enough unseen observations accumulate or a clearly defined failure mode appears.

## 11. Generated research data

The detailed CSV outputs are intentionally excluded from Git when `research_results/` is ignored. This document preserves the principal research findings in a reviewable format. To reproduce the detailed results, run the research scripts and regenerate:

```text
trade_distribution_summary.csv
trade_distribution_by_symbol.csv
trade_distribution_by_year.csv
trade_distribution_by_exit_reason.csv
trade_distribution_by_holding_bucket.csv
risk_of_ruin_summary.csv
risk_of_ruin_by_horizon.csv
risk_of_ruin_by_position_size.csv
risk_of_ruin_by_capital.csv
monte_carlo_*_summary.csv
portfolio_stability_*.csv
```

---

**Disclaimer:** This repository is an educational and research project. Historical and simulated performance does not guarantee future results and is not financial advice.