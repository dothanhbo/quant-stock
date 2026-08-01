# Changelog

All notable changes to this project are documented in this file.

## [v5.1.0] - 2026-08-01

### Added

- Buy commission simulation
- Sell commission simulation
- Sell tax simulation
- Fixed buy slippage
- Fixed sell slippage
- Slippage-aware position sizing
- CLI parameters for fees, tax, and slippage
- Gross Trade PnL
- Net Trade PnL
- Gross Profit
- Gross Loss
- Total Transaction Cost
- Profit and Cost Breakdown

### Changed

- Portfolio cash now reflects commissions, tax, and slippage.
- Trade PnL and return metrics now support net results.
- Profit Factor is calculated from monetary net PnL.
- Total Return is calculated from Initial Capital and Final Equity.
- Output filenames use compact labels for full-universe runs.

### Fixed

- Same-day entry and exit ordering.
- Remaining open positions after simulation events.
- Quantity calculations that ignored buy-side costs.
- Summary inconsistencies between Final Equity, Total Return, and Net Trade PnL.
- Profit Factor mismatch between return percentages and monetary PnL.
- Excessively long filenames on Windows.

### Testing

- Added transaction-cost tests.
- Added slippage tests.
- Expanded Trade, Portfolio, and PortfolioSimulator coverage.
- **53 passing tests**

## [v5.0.0]

### Added

- Multi-symbol portfolio backtesting
- Shared cash
- Position sizing
- Lot-size support
- Maximum positions
- Duplicate-symbol protection
- Equity curve
- Rejected-trade reporting
- Portfolio summary
- CAGR, Sharpe, Sortino, Calmar, and Max Drawdown
