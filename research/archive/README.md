# Historical Research Archive

This tree preserves historical experiment source for reproducibility and
provenance. It is not the active research API, must not be imported by
production, and is not a supported operational entrypoint surface.

Current supported Quant Lab runners remain in the `research/` root as
`run_quantlab_*.py`. Historical files may depend on old artifact layouts,
configuration, provider behavior, or APIs and are not guaranteed to execute
independently against the current engine. Tests that still import archived
modules protect frozen semantics or compatibility; they do not make those
modules current entrypoints.

The archive is grouped by research question:

- `q70/`: frozen-Q70 and Q70 attribution/robustness experiments;
- `state_quality/`: state-quality candidate, exposure, and portfolio studies;
- `entry_exit/`: entry/exit matrices, ablations, and legacy exit optimization;
- `sector_rs/`: sector-relative-strength and rotation studies;
- `exhaustion/`: exhaustion robustness and sensitivity experiments;
- `regime/`: regime and historical market-state experiments;
- `benchmarks/`: legacy benchmark matrices and model comparisons;
- `diagnostics/`: one-off diagnostics, reports, parity, and trade attribution;
- `portfolio/`: legacy portfolio, ranking, momentum, Monte Carlo, and risk work;
- `parameter_search/`: grids, ablations, parameter spaces, and stability work;
- `legacy_wfo/`: pre-QuantLab walk-forward implementations;
- `legacy_backtesting/`, `exit_matrix/`, and `allocation_demos/`: earlier
  explicitly archived families;
- `misc/`: provenance that does not fit another stable family.

Git history and the `v1.0.0` tag preserve the exact pre-pruning layout. Do not
silently modernize archived behavior or relabel archived results as current
Quant Lab evidence.
