# Quant Bot V3 — Market Breadth Paper Candidate

Paper V3 keeps the frozen Q70 entry policy and adds market-breadth exposure
control. Breadth does not change the Q70 score, its component percentiles, or
the Q70 acceptance decision.

## Decision order and exposure

`PaperV3Scanner` applies the frozen Q70 gate first. Breadth exposure is
calculated only for Q70-accepted candidates from their point-in-time
`breadth_ema50_pct` value:

- breadth >= 60%: 100% of normal position size (`FULL`)
- 40% <= breadth < 60%: 50% of normal position size (`HALF`)
- breadth < 40%: 0% new-position exposure (`BREADTH_BLOCK`)
- missing or non-finite breadth: fail closed to 0% new-position exposure

Candidates blocked at 0% are retained in V3 breadth diagnostics rather than
returned as accepted candidates.

Q70-rejected candidates do not receive breadth exposure. They report
`breadth_exposure_applied=False`, no breadth multiplier or percentage, and
`paper_v3_breadth_gate=NOT_APPLICABLE`.

## Runtime configuration

The daily pipeline defaults to `Q70_FROZEN`. Select Paper V3 with:

```text
PAPER_STRATEGY_VERSION=V3_BREADTH_40_60
```

Paper database defaults and overrides are:

```text
V2: data/paper_trading_v2.db  (PAPER_V2_DATABASE_PATH)
V3: data/paper_trading_v3.db  (PAPER_V3_DATABASE_PATH)
```

V2 and V3 paper state remain isolated. The V3 lifecycle configures the hybrid
entry model, ATR stop 2.0, ATR target 5.0, and disables trailing behavior.

## Validation boundary

V3 remains an exposure-control candidate. Historical research artifacts and
paper observations should retain their policy, universe, cost, and database
provenance rather than being relabeled or overwritten.
