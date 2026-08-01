# Quant Stock

![Python](https://img.shields.io/badge/Python-3.14-blue)
![Version](https://img.shields.io/badge/version-v5.2-success)
![Tests](https://img.shields.io/badge/tests-passing-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

> Quantitative research and backtesting framework for the Vietnam stock market.

## Overview

Quant Stock combines market-data processing, signal scanning, portfolio simulation, execution-cost modeling, trade analytics, and benchmark comparison.

## Features

### Market Data
- VN100 historical database
- VNINDEX benchmark data
- Incremental updates
- Long-history backfill
- Configurable backtest dates

### Portfolio Backtesting
- Multi-symbol simulation
- Shared cash
- Position sizing
- Maximum-position control
- Rejected-trade tracking

### Execution Simulation
- Buy and sell commission
- Sell tax
- Buy and sell slippage
- Slippage-aware position sizing

### Analytics
- Total Return, CAGR, Max Drawdown
- Sharpe, Sortino, Calmar
- Win Rate, Profit Factor, Payoff Ratio
- Expectancy
- Average Win and Average Loss
- Holding-period statistics
- Profit distribution
- Holding distribution
- Exit-reason distribution

### Benchmark
- Buy & Hold Return
- Buy & Hold CAGR
- Strategy Excess Return
- Strategy Excess CAGR

## Commands

```powershell
py -m backtesting.engine --symbol ACB --start 2015-07-16 --end 2026-07-31 --quiet
```

```powershell
py -m pytest
```

## Report Sections

```text
PORTFOLIO SUMMARY
PROFIT & COST BREAKDOWN
TRADE ANALYTICS
TRADE DISTRIBUTIONS
BUY & HOLD BENCHMARK
EXECUTIVE SUMMARY
```

## Roadmap

- ✅ v5.0 Portfolio Backtesting
- ✅ v5.1 Transaction Costs and Slippage
- ✅ v5.2 Analytics, Benchmark, and Reporting
- 🚧 v5.3 Strategy Validation

See [ROADMAP.md](ROADMAP.md).

## Disclaimer

For research and educational purposes only. This project does not provide investment advice.
