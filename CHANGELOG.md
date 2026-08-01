# Changelog

## [v5.2.0] - 2026-08-02

### Added
- Trade expectancy in VND and percentage
- Average Win and Average Loss
- Holding-period statistics
- Profit distribution
- Holding distribution
- Exit-reason distribution
- Buy & Hold Return and CAGR
- Excess Return and Excess CAGR
- Modular console reporting
- Executive Summary

### Changed
- Reporting moved out of `backtesting/engine.py`
- Relative Strength supports historical `as_of_date`
- VNINDEX history is maintained for long backtests
- Backtests support `--start` and `--end`

### Fixed
- Relative Strength look-ahead bias
- Missing VNINDEX history for historical backtests
- Undefined date variables introduced during refactoring
- Benchmark metrics missing from exported CSV

### Testing
- Added Trade Analytics tests
- Added Trade Distribution tests
- Added Buy & Hold Benchmark tests

## [v5.1.0] - 2026-08-01

### Added
- Commissions
- Sell tax
- Buy and sell slippage
- Gross and Net Trade PnL
- Profit and Cost Breakdown

## [v5.0.0]

### Added
- Portfolio Simulator
- Shared cash
- Position sizing
- Equity curve
- Portfolio performance metrics
