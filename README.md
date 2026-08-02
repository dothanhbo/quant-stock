# Quant Stock Research Platform

Nền tảng nghiên cứu định lượng cho thị trường chứng khoán Việt Nam.

## Tính năng hiện có

- SQLite historical database
- Incremental data update và backfill khoảng 8 năm
- Indicator engine và market regime
- Backtesting engine
- Portfolio simulation
- Transaction costs, tax và slippage
- Fixed, ATR, Break-even và Trailing ATR exits
- Grid Search và multiprocessing
- Exit-model benchmark
- Symbol winner matrix
- Walk-forward train selection
- Out-of-sample walk-forward testing
- CSV research reports

## Research workflow

```text
Data
→ Indicators
→ Entry Strategy
→ Exit Model
→ Backtest
→ Benchmark
→ Grid Search
→ Walk-Forward Optimization
→ Out-of-Sample Validation
```

## Kết quả WFO gần nhất

- Positive windows: 0/3
- Average Return: -3.58%
- Median Return: -4.46%
- Average Sharpe: -2.18
- Average Profit Factor: 0.85
- Average Drawdown: -13.79%

Kết luận hiện tại: ưu tiên Entry Framework và Entry Research.
