# Quant Stock V1

Quant Stock is a personal quantitative-research and decision-support platform
for Vietnamese equities. It combines reproducible historical research,
prospective Forward V1 evidence, portfolio/risk/execution studies, monitored
paper execution, and an end-of-day production pipeline.

It does **not** place orders with a live broker. Historical, paper, and forward
results are evidence for research and operations; they are not investment
advice or proof of future profitability.

## V1 architecture

```text
DATA / INGESTION
  core/, scripts/update_data.py, data/market.db
        |
RESEARCH / EVALUATION
  quantlab/, backtesting/, research/
        |
DECISION GATES
  frozen policy identities and research decision artifacts
        |
FORWARD VALIDATION
  quantlab/forward/, research/forward_validation/protocol_v1.json
        |
PORTFOLIO / RISK / EXECUTION
  quantlab/portfolio/, quantlab/execution/, execution/
        |
DAILY PRODUCTION
  main.py -> scripts/run_daily.py -> app/daily_pipeline.py
        |
MONITORING
  quantlab/monitoring.py, quantlab/monitoring_history.py
```

The `research/archive/` tree is historical provenance, not the active API.
Other older research runners are retained when they are needed to reproduce or
interpret earlier protocols. Generated research evidence belongs under the
ignored `research_results/` directory.

## Canonical production operation

The VPS is the sole canonical production runtime and owns persistent market,
Forward, and paper state. GitHub Actions is not a production scheduler or
database owner. Run commands from the repository root on the VPS.

The one canonical daily entrypoint is:

```bash
python -m scripts.run_daily
```

Operational `update`, `scan`, and `daily` executions are recorded in the lazy
local ledger `data/operation_history.db`. QuantCtl and Quant Manager read the
same ledger through `python -m quantctl history`; read-only inspection never
creates it. Daily step detail comes from the existing `DailyPipelineResult`
stage boundaries. Automatic history retention is intentionally deferred while
the sequential VPS workload remains small.

`main.py` is a compatibility alias for the same command. The daily stage order
is:

1. update the resolved current VN100 plus VNINDEX universe;
2. enforce the market-data integrity gate;
3. record/mature activated Forward V1 evidence;
4. execute pending paper entries and manage existing paper positions;
5. scan the same completed market session and queue new paper signals.

The integrity gate runs even with `--skip-update`. An incomplete provider run,
missing/stale required symbol, duplicate required key, or invalid OHLCV fails
closed before Forward, paper, or scanner state can change. A valid prior market
session on a non-trading day is `NOT_APPLICABLE`; stateful downstream stages
are skipped rather than replayed against stale data.

Useful diagnostic flags are `--skip-update`, `--skip-lifecycle`, `--skip-scan`,
and `--stop-on-data-errors`. They do not create a second canonical production
entrypoint.

## Databases and path contracts

All databases are runtime data and ignored by Git.

| Store | Default | Override / owner |
|---|---|---|
| Market OHLCV | repository-root `data/market.db` | explicit function argument, then non-empty `MARKET_DATABASE_PATH`, then the default; relative market paths resolve from repository root |
| Forward V1 | `data/forward_validation.db` | Forward CLI/runtime `--ledger` or `ledger_path`; append-only after explicit activation |
| Paper V1 | `data/paper_trading.db` | `PAPER_DATABASE_PATH` |
| Paper V2 | `data/paper_trading_v2.db` | `PAPER_V2_DATABASE_PATH`, assigned to the common paper runtime by the V2 wrapper |
| Paper V3 | `data/paper_trading_v3.db` | `PAPER_V3_DATABASE_PATH`, assigned to the common paper runtime by the V3 wrapper |

Market data and paper state are intentionally separate. V2 and V3 paper state
must also remain isolated. Paper-path overrides are process-working-directory
relative, so production services run from the repository root and use explicit
absolute overrides when that cannot be guaranteed.

## Paper policies

The default daily paper policy is frozen Q70 (`Q70_FROZEN`). Set
`PAPER_STRATEGY_VERSION=V3_BREADTH_40_60` to select the isolated Paper V3
wrapper. V3 preserves Q70 selection and applies breadth exposure only after a
candidate passes Q70; see [research/README_V3.md](research/README_V3.md).

Protected entry and exit intents are idempotent and persisted, but paper fills
remain simulations. Paper execution uses next-session timing, configured costs,
risk limits, exposure limits, and persistent lifecycle recovery; it is not a
substitute for broker reconciliation.

## Forward V1

Forward V1 is prospective, append-only evidence governed by
`research/forward_validation/protocol_v1.json`. Activation is explicit, missed
formation sessions are audit gaps, and evidence at or before the historical or
operational activation boundary cannot be backfilled.

Read status without activating or recording a formation:

```bash
python -m research.run_quantlab_forward_validation status
```

The daily production command invokes the Forward daily operation only after a
successful current-session market-data integrity gate. Do not use the
`activate`, `record`, or `mature` maintenance commands without following the
frozen protocol and preserving the ledger first.

## Configuration

Keep secrets and host-specific overrides in an untracked `.env`. Do not commit
tokens, API keys, or database files.

Important active variables include:

| Area | Variables |
|---|---|
| Market | `MARKET_DATABASE_PATH` |
| Paper selection/state | `PAPER_STRATEGY_VERSION`, `PAPER_TRADING_ENABLED`, `PAPER_DATABASE_PATH`, `PAPER_V2_DATABASE_PATH`, `PAPER_V3_DATABASE_PATH` |
| Frozen execution policy | `TRADING_ENTRY_MODEL`, `TRADING_EXIT_MODEL`, `TRADING_EXECUTION_TIMING`, `TRADING_STOP_ATR_MULTIPLIER`, `TRADING_TARGET_ATR_MULTIPLIER`, `TRADING_TRAILING_ATR_MULTIPLIER`, `TRADING_MAX_HOLDING_DAYS` |
| Paper sizing/risk/costs | `PAPER_INITIAL_CASH`, `PAPER_POSITION_SIZER`, `PAPER_RISK_PER_TRADE_PCT`, `PAPER_FIXED_FRACTION_PCT`, `PAPER_ATR_STOP_MULTIPLIER`, `PAPER_ATR_TARGET_MULTIPLIER`, `PAPER_MAX_ORDERS_PER_SCAN`, `PAPER_LOT_SIZE`, `PAPER_COMMISSION_RATE`, `PAPER_SLIPPAGE_BPS`, `PAPER_SELL_TAX_RATE`, `PAPER_MAX_POSITION_PCT`, `PAPER_MAX_EXPOSURE_PCT`, `PAPER_MAX_OPEN_POSITIONS`, `PAPER_MAX_DAILY_LOSS_PCT`, `PAPER_MIN_CASH_BUFFER_PCT`, `PAPER_MAX_ORDER_ADTV20_PCT` |
| Telegram | `TELEGRAM_TOKEN`, `CHAT_ID` |
| Optional AI explanation | `AI_ANALYSIS_ENABLED`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `AI_ANALYSIS_TIMEOUT_SECONDS`, `AI_ANALYSIS_MAX_OUTPUT_TOKENS` |

V2/V3 wrappers deliberately freeze their entry/exit values before importing
scanner or lifecycle singletons. Do not use environment overrides to relabel a
different policy as Q70 or V3.

## Setup and validation

Python 3.12+ and SQLite 3 are expected.

```bash
python -m venv .venv
python -m pip install -r requirements.txt
python -m pytest -q
```

Initialize only the market schema selected by the canonical resolver:

```bash
python -m scripts.init_db
```

Operational and maintenance commands:

```bash
# Market-only incremental update (network/provider access)
python -m scripts.update_data

# Read/check current universe coverage
python -m scripts.check_market_data --summary-only

# Paper performance report
python -m scripts.report_paper_performance --database data/paper_trading.db

# Read-only Telegram query service (separate VPS process)
python -m services.telegram_bot.app

# Paper dashboard
python -m streamlit run dashboard/app.py

# Persist one monitoring snapshot under research_results/
python -m research.run_quantlab_monitoring_snapshot

# Compare persisted monitoring snapshots
python -m research.run_quantlab_monitoring_history
```

The Telegram query process and daily pipeline may share the canonical market
database, but only one Telegram `getUpdates` consumer should use a bot token at
a time. AI analysis is explanatory only and cannot alter Quant decisions.

## Research boundary

`quantlab/` contains the deterministic data, feature, evaluation, portfolio,
risk, execution-realism, Forward, and monitoring contracts used by current
canonical research. `backtesting/` and `strategy/` retain compatible historical
and production-parity machinery. Standalone runners under `research/` are not
production schedules; each must preserve its inputs, identities, universe,
costs, and output directory.

Database coverage is not historical VN100 membership. Historical comparisons
that apply the current VN100 retrospectively remain explicitly biased legacy
comparisons. Do not overwrite or relabel earlier canonical artifacts.

## Repository map

| Path | Role |
|---|---|
| `core/` | canonical paths, database access, universe and market integrity |
| `quantlab/` | deterministic research, Forward, risk, execution, and monitoring infrastructure |
| `backtesting/` | historical candidate, simulation, metrics, WFO, and compatibility infrastructure |
| `strategy/` | feature/entry/scanner and Q70/V3 paper policy surfaces |
| `execution/` | paper broker, persistence, idempotent entry/exit lifecycle and risk guards |
| `app/` | daily orchestration |
| `scripts/` | supported production and maintenance entrypoints |
| `services/` | Telegram transport/query/formatting and optional AI explanation |
| `research/` | canonical research runners, protocol inputs, and historical provenance |
| `analysis/`, `reporting/`, `dashboard/` | paper/backtest inspection and presentation |
| `tests/` | current and frozen-contract regression coverage |

## V1 limitations

- There is no live-broker adapter or automated broker order execution.
- Paper fills and costs cannot reproduce exchange queues, partial fills, or
  institutional order-book impact.
- Historical provider/price-unit and corporate-action provenance is incomplete
  for some evidence; retained audits describe the boundary.
- Database coverage does not reconstruct point-in-time historical VN100.
- Forward V1 evidence accumulates prospectively and cannot be backfilled; its
  sample and monitoring history may remain sparse.
- Historical paper records can have weaker provenance than current idempotent
  entry/exit records.
- The VPS is a single canonical runtime, not high-availability infrastructure;
  backup, retention, process supervision, and filesystem durability remain VPS
  operational responsibilities.
- Historical profitability is not validated future edge.

## License

MIT. See [LICENSE](LICENSE).
