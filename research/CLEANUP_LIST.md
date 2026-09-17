# Quant Bot cleanup list

## Safe to delete locally / from source tree

These are generated caches or explicit backup copies. They are not needed to run the bot.

### Python/test caches

- all `**/__pycache__/` directories (18 directories in the uploaded tree)
- all `**/*.pyc` files (531 files in the uploaded tree)
- `.pytest_cache/`

### Explicit backup files

- `config/strategy_config.py.bak_q70_frozen`
- `scripts/paper_v2_smoke_test.py.bak_q70_frozen`
- `scripts/run_daily.py.bak_q70_daily`
- `scripts/run_paper_v2.py.bak_q70_frozen`
- `scripts/run_paper_v2_lifecycle.py.bak_q70_frozen`
- `strategy/paper_v2_gate.py.bak_q70_frozen`
- `strategy/paper_v2_scanner.py.bak_q70_frozen`
- `strategy/relative_strength_v2.py.bak_rs_universe_fix`
- `tests/test_paper_v2_scanner.py.bak_q70_frozen`
- `tests/test_strategy_config.py.bak_q70_frozen`

## Keep, but do not commit runtime data

Do not delete these from the live server just for cleanup:

- `data/market.db`
- `data/paper_trading.db`
- `data/paper_trading_v2.db`
- future `data/paper_trading_v3.db`

They are runtime state/history. They should remain ignored by Git.

## Review before deleting

The uploaded repo contains a large research history with many one-off WFO/audit scripts. Do not bulk-delete `research/*.py` solely because a file is not imported by production; these scripts are intentionally standalone research artifacts.

The following families are candidates for archiving into a separate research archive after their results are preserved:

- old parameter/grid/optimizer experiments
- old exit-matrix and exit-robustness experiments
- superseded sector-rotation/sector-RS experiments
- superseded exhaustion experiments
- duplicated `run_*_v2/v3/v4` research variants
- generated research CSV/report directories if present outside the uploaded archive

Before deleting any such research script, preserve its final result and the hypothesis/conclusion that caused it to be superseded.

## Git hygiene

The uploaded tree contains a `.git` directory plus a very large working-tree diff. Do not copy the `.git` directory into a deployment patch. Commit source changes deliberately and keep generated/runtime artifacts ignored.
