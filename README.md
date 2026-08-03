# Quant Stock Research Framework

## Release Summary

This release transforms the project from a single-strategy backtesting
tool into a quantitative research framework capable of:

-   Historical data management with SQLite
-   Indicator pipeline
-   Modular Entry / Exit models
-   Backtesting engine
-   Exit model benchmarking
-   Entry model benchmarking
-   Entry × Exit strategy matrix
-   Walk-Forward Optimization (WFO)
-   Strategy diagnostics
-   Strategy ablation studies (In-Sample & Out-of-Sample)

## Current Architecture

    Historical Data
          │
    SQLite Database
          │
    Indicator Engine
          │
    Entry Models
          │
    Exit Models
          │
    Backtesting Engine
          │
    Benchmark Engine
          │
    Walk Forward Research
          │
    Research Reports

## Major Findings

### Exit Models

-   ATR Exit consistently outperformed Fixed Exit in several scenarios.
-   Trailing ATR improved trend capture but was less stable across
    windows.

### Entry Models

-   Trend V1 remains a competitive baseline.
-   Donchian Breakout is simpler but more robust.

### Ablation Study

In-sample: - `trend_v1__no_volume_no_rs` produced the highest average
return.

Out-of-sample: - `donchian_breakout_v1` won every WFO window.

Conclusion:

> Better in-sample performance does not imply better generalization.

## Current Status

Completed

-   Data pipeline
-   Research framework
-   Backtesting engine
-   Entry benchmark
-   Exit benchmark
-   Strategy matrix
-   Walk-forward optimization
-   OOS diagnostics
-   Ablation framework

Next Research

-   Donchian feature engineering
-   Monte Carlo simulation
-   Parameter stability analysis
-   Portfolio construction
