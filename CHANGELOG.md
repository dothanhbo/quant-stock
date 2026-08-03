# CHANGELOG

## Current Release

### Added

-   Configurable TrendStrategyV1
-   Entry model registry
-   Exit model registry
-   ATR / Break-even / Trailing ATR exits
-   Entry benchmark
-   Exit benchmark
-   Entry × Exit benchmark
-   Walk Forward Optimizer
-   Strategy diagnostics
-   Ablation benchmark
-   Out-of-Sample diagnostics

### Refactored

-   Entry model architecture
-   Benchmark framework
-   Walk-forward workflow
-   Research reporting
-   Strategy naming system

### Fixed

-   Entry model injection
-   Scanner compatibility
-   ATR exit integration
-   Backtest consistency
-   Metric consistency
-   CSV export improvements

### Research Findings

-   Donchian Breakout generalized better than Trend V1.
-   Removing filters improved IS performance but reduced OOS robustness.
-   Walk Forward successfully detected overfitting.
