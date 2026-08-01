# Quant Stock

![Python](https://img.shields.io/badge/Python-3.14-blue)
![Version](https://img.shields.io/badge/version-v5.1.0-success)
![Tests](https://img.shields.io/badge/tests-53%20passing-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

> Quantitative trading research framework for the Vietnam stock market.

## Overview

Quant Stock combines market-data processing, signal scanning, portfolio backtesting, execution-cost simulation, and performance analytics in a modular Python project.

## Features

### Market Data
- VN100 historical database
- Incremental updates
- VNINDEX support
- Shared universe provider

### Signal Scanner
- Multi-factor scoring
- Market-regime filter
- Watchlist generation
- Telegram notifications

### Portfolio Backtesting
- Multi-symbol simulation
- Shared cash management
- Position sizing
- Maximum positions
- Lot-size support
- Duplicate-symbol protection
- End-of-backtest position handling
- Rejected-trade tracking

### Trading Cost Simulation
- Buy commission
- Sell commission
- Sell tax
- Buy slippage
- Sell slippage
- Slippage-aware position sizing
- Configurable CLI parameters

### Performance Analytics
- Total Return
- CAGR
- Max Drawdown
- Annualized Volatility
- Sharpe Ratio
- Sortino Ratio
- Calmar Ratio
- Win Rate
- Profit Factor
- Payoff Ratio
- Gross Trade PnL
- Net Trade PnL
- Transaction Cost Breakdown

### Testing
- **53 passing unit tests**

## Run Backtests

```powershell
py -m backtesting.engine --symbol HPG FPT MBB MWG ACB --quiet
```

```powershell
py -m backtesting.engine --all --quiet
```

Run without trading costs:

```powershell
py -m backtesting.engine --symbol HPG FPT MBB MWG ACB --buy-fee 0 --sell-fee 0 --sell-tax 0 --buy-slippage 0 --sell-slippage 0 --quiet
```

Run tests:

```powershell
py -m pytest
```

## Roadmap

- ✅ v5.0 — Portfolio Backtesting Framework
- ✅ v5.1 — Transaction Costs, Sell Tax, Slippage, and Cost Breakdown
- 🚧 v5.2 — Daily Equity Curve, VNINDEX Benchmark, and Monthly Returns
- 📅 v5.3 — Walk-forward Analysis and Parameter Sensitivity

See [ROADMAP.md](ROADMAP.md).

## Disclaimer

This project is intended for research and educational purposes. It does not provide investment advice or guaranteed results.
