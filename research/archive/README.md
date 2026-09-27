# Historical Research Archive

This directory preserves historical source code for provenance and experiment
archaeology. Its contents are not part of the current active research API or
runner surface. Some archived runners may no longer execute against the current
engine or module APIs.

Archival does not mean that an experiment was invalid. Archived implementations
must not be silently modernized: changing their behavior would destroy the
historical provenance they preserve.

## Archived families

- **Exit matrix:** `run_exit_matrix_v2.py` is a historical discovery and
  delegation helper. Active v3 and causal v4 remain outside this archive because
  they address different research questions.
- **State-quality candidate:** archived v3 predates the current candidate API.
  Active v4 contains the API-compatible implementation.
- **State-quality engine:** the archived unsuffixed runner preserves legacy
  trailing wiring and its earlier output schema. Active v2 remains visible.
- **State-quality exposure:** the archived unsuffixed and v2 runners consume an
  obsolete market-health schema. Active v3 consumes canonical point-in-time
  health fields.
- **Legacy backtesting:** `optimize_exit_fast.py` is an approximate replay
  utility and is not behaviorally equivalent to full-engine `optimize_exit.py`.
  The multi-symbol producer and its two CSV consumers are preserved together as
  one historical workflow based on `backtest_results_multi/all_trades.csv`; it
  may not run against the current engine.
- **Allocation demos:** these are console demonstrations, not pytest tests.
