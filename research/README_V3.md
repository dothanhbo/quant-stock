# Quant Bot V3 — Market Breadth Paper Candidate

V3 is the frozen Q70 + Hard Donchian + ATR 2/5 baseline with one additional
market-level exposure overlay.

## Frozen V3 rule

- breadth >= 60%: 100% of normal position size
- 40% <= breadth < 60%: 50% of normal position size
- breadth < 40%: 0% new-position exposure
- missing/non-finite breadth: fail closed (0% new exposure)

Breadth uses the point-in-time `breadth_ema50_pct` already produced by the
scanner. It does not change Q70, Donchian, entry timing, ATR stop/target, or
the Q70 quality features.

## Runtime

The default daily pipeline remains `Q70_FROZEN` for backward compatibility.

To run the V3 paper candidate, set:

`PAPER_STRATEGY_VERSION=V3_BREADTH_40_60`

and optionally:

`PAPER_V3_DATABASE_PATH=data/paper_trading_v3.db`

V3 uses its own paper database. Existing V1/V2 databases are not migrated or
modified.

The V3 runner and lifecycle entrypoints live in `scripts/` alongside the V2
entrypoints. `scripts/run_daily.py` selects V2 or V3 from
`PAPER_STRATEGY_VERSION`.

## Validation boundary

V3 is an exposure-overlay candidate. It must be validated against the frozen
Q70 baseline before being treated as the production strategy. Historical
research results should remain reproducible artifacts; they are not silently
replaced by paper-trading observations.
