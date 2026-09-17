# Quant Bot V3 Breadth Patch

## V3 definition

V3 is the frozen Q70/Donchian/ATR baseline plus a market-level exposure multiplier:

- breadth >= 60%: 100% of normal position size
- 40% <= breadth < 60%: 50% of normal position size
- breadth < 40%: 0% new-position exposure

The multiplier is based only on point-in-time `breadth_ema50_pct` already computed by `strategy.market_state`.
It does not alter Q70, Donchian, entry timing, ATR stop/target, or the V2 database.

## Files to copy

Copy the files in this patch at the same relative paths into the project, replacing files with the same path:

- `config/strategy_config.py`
- `core/universe.py`
- `execution/signal_executor.py`
- `scripts/run_daily.py`
- `scripts/run_paper_lifecycle.py`
- `scripts/run_paper_v3.py` (new)
- `scripts/run_paper_v3_lifecycle.py` (new)
- `strategy/breadth_exposure.py` (new)
- `strategy/paper_v3_scanner.py` (new)
- `strategy/scanner.py`
- `tests/test_breadth_exposure.py` (new)
- `tests/test_paper_v3_scanner.py` (new)
- `tests/test_next_open_execution.py`
- `tests/test_strategy_config.py`

## Server setup

Keep the existing V2 database untouched. V3 uses a separate database by default:

`data/paper_trading_v3.db`

Set this in `.env` to switch the daily pipeline to V3:

`PAPER_STRATEGY_VERSION=V3_BREADTH_40_60`

Optional explicit database path:

`PAPER_V3_DATABASE_PATH=data/paper_trading_v3.db`

If `PAPER_STRATEGY_VERSION` is unset, daily execution remains on V2 for backward compatibility.

Run:

`python -m pytest -q`

The uploaded source passed 447 tests after this patch, using dummy Telegram credentials for test collection.

## Important

Do not copy the local `data/*.db` files from the patch into the server. The V3 database should start as a new paper-validation database. Keep the old V1/V2 databases as historical records.
