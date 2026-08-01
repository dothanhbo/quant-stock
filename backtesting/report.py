from __future__ import annotations

from typing import Any


def print_portfolio_summary(
    metrics: dict[str, Any],
) -> None:
    print("\n" + "=" * 60)
    print("PORTFOLIO SUMMARY")
    print("=" * 60)

    print(
        f"Initial Capital : "
        f"{metrics['initial_capital']:,.0f} VND"
    )
    print(
        f"Final Equity    : "
        f"{metrics['final_equity']:,.0f} VND"
    )
    print(
        f"Total Return    : "
        f"{metrics['total_return_pct']:+.2f}%"
    )

    print()

    print(
        f"Max Drawdown    : "
        f"{metrics['max_drawdown_pct']:.2f}%"
    )
    print(
        f"CAGR            : "
        f"{metrics['cagr_pct']:.2f}%"
    )
    print(
        f"Sharpe Ratio    : "
        f"{metrics['sharpe_ratio']:.2f}"
    )
    print(
        f"Sortino Ratio   : "
        f"{metrics['sortino_ratio']:.2f}"
    )
    print(
        f"Calmar Ratio    : "
        f"{metrics['calmar_ratio']:.2f}"
    )

    print()

    print(
        f"Executed Trades : "
        f"{metrics['total_trades']}"
    )
    print(
        f"Rejected Trades : "
        f"{metrics.get('rejected_trades', 0)}"
    )
    print(
        f"Final Cash      : "
        f"{metrics.get('final_cash', 0):,.0f} VND"
    )
    print(
        f"Market Value    : "
        f"{metrics.get('final_market_value', 0):,.0f} VND"
    )
    print(
        f"Open Positions  : "
        f"{metrics.get('final_open_positions', 0)}"
    )
    print(
        f"Win Rate        : "
        f"{metrics['win_rate_pct']:.2f}%"
    )
    print(
        f"Profit Factor   : "
        f"{metrics['profit_factor']:.2f}"
    )
    print(
        f"Payoff Ratio    : "
        f"{metrics['payoff_ratio']:.2f}"
    )


def print_cost_breakdown(
    metrics: dict[str, Any],
) -> None:
    print()
    print("PROFIT & COST BREAKDOWN")
    print("-" * 60)

    print(
        f"Gross Profit    : "
        f"{metrics.get('gross_profit', 0):,.0f} VND"
    )
    print(
        f"Gross Loss      : "
        f"-{metrics.get('gross_loss', 0):,.0f} VND"
    )
    print(
        f"Gross Trade PnL : "
        f"{metrics.get('gross_trading_pnl', 0):+,.0f} VND"
    )
    print(
        f"Net Trade PnL   : "
        f"{metrics.get('net_trading_pnl', 0):+,.0f} VND"
    )

    print()

    print(
        f"Buy Commission  : "
        f"{metrics.get('total_buy_commission', 0):,.0f} VND"
    )
    print(
        f"Sell Commission : "
        f"{metrics.get('total_sell_commission', 0):,.0f} VND"
    )
    print(
        f"Sell Tax        : "
        f"{metrics.get('total_sell_tax', 0):,.0f} VND"
    )
    print(
        f"Total Cost      : "
        f"{metrics.get('total_transaction_cost', 0):,.0f} VND"
    )


def print_trade_analytics(
    metrics: dict[str, Any],
) -> None:
    print()
    print("TRADE ANALYTICS")
    print("-" * 60)

    print(
        f"Expectancy      : "
        f"{metrics.get('expectancy_amount', 0):+,.0f} "
        f"VND/trade"
    )
    print(
        f"Expectancy (%)  : "
        f"{metrics.get('expectancy_pct', 0):+.2f}%"
    )

    print()

    print(
        f"Average Win     : "
        f"{metrics.get('average_win_pct', 0):+.2f}%"
    )
    print(
        f"Average Loss    : "
        f"{metrics.get('average_loss_pct', 0):+.2f}%"
    )
    print(
        f"Average Win PnL : "
        f"{metrics.get('average_win_amount', 0):+,.0f} VND"
    )
    print(
        f"Average Loss PnL: "
        f"{metrics.get('average_loss_amount', 0):+,.0f} VND"
    )

    print()

    print("Holding Days")
    print(
        f"  Average       : "
        f"{metrics.get('average_holding_days', 0):.1f}"
    )
    print(
        f"  Median        : "
        f"{metrics.get('median_holding_days', 0):.1f}"
    )
    print(
        f"  Minimum       : "
        f"{metrics.get('min_holding_days', 0)}"
    )
    print(
        f"  Maximum       : "
        f"{metrics.get('max_holding_days', 0)}"
    )


def print_distributions(
    metrics: dict[str, Any],
) -> None:
    print()
    print("TRADE DISTRIBUTIONS")
    print("-" * 60)

    print("Profit Distribution")
    for label, count in metrics.get(
        "profit_distribution",
        {},
    ).items():
        print(f"  {label:<16}: {count}")

    print()
    print("Holding Distribution")
    for label, count in metrics.get(
        "holding_distribution",
        {},
    ).items():
        print(f"  {label:<16}: {count}")

    print()
    print("Exit Reason Distribution")

    exit_distribution = metrics.get(
        "exit_reason_distribution",
        {},
    )

    if exit_distribution:
        for reason, count in exit_distribution.items():
            print(f"  {reason:<16}: {count}")
    else:
        print("  No trades")


def print_benchmark_comparison(
    metrics: dict[str, Any],
) -> None:
    symbol = metrics.get("benchmark_symbol")

    if not symbol:
        return

    print()
    print("BUY & HOLD BENCHMARK")
    print("-" * 60)

    print(f"Symbol          : {symbol}")
    print(
        f"Period          : "
        f"{metrics.get('benchmark_start_date')} "
        f"to {metrics.get('benchmark_end_date')}"
    )
    print(
        f"Strategy Return : "
        f"{metrics.get('total_return_pct', 0):+.2f}%"
    )
    print(
        f"Buy & Hold      : "
        f"{metrics.get('benchmark_return_pct', 0):+.2f}%"
    )
    print(
        f"Excess Return   : "
        f"{metrics.get('strategy_vs_benchmark_pct', 0):+.2f}%"
    )

    print()

    print(
        f"Strategy CAGR   : "
        f"{metrics.get('cagr_pct', 0):+.2f}%"
    )
    print(
        f"Buy & Hold CAGR : "
        f"{metrics.get('benchmark_cagr_pct', 0):+.2f}%"
    )
    print(
        f"Excess CAGR     : "
        f"{metrics.get('strategy_vs_benchmark_cagr_pct', 0):+.2f}%"
    )


def print_reject_reasons(
    metrics: dict[str, Any],
) -> None:
    reject_reasons = metrics.get(
        "rejected_trade_reasons",
        {},
    )

    if not reject_reasons:
        return

    print()
    print("REJECT REASONS")
    print("-" * 60)

    for reason, count in sorted(
        reject_reasons.items()
    ):
        print(f"- {reason:<18}: {count}")

def print_executive_summary(metrics: dict[str, Any]) -> None:
    strategy_return = float(metrics.get("total_return_pct", 0.0))
    benchmark_return = float(metrics.get("benchmark_return_pct", 0.0))
    excess_return = float(metrics.get("strategy_vs_benchmark_pct", 0.0))
    strategy_cagr = float(metrics.get("cagr_pct", 0.0))
    benchmark_cagr = float(metrics.get("benchmark_cagr_pct", 0.0))

    print()
    print("EXECUTIVE SUMMARY")
    print("-" * 60)
    print(f"Strategy Return : {strategy_return:+.2f}%")

    if not metrics.get("benchmark_symbol"):
        print("Assessment      : Benchmark is available only for single-symbol backtests.")
        return

    print(f"Buy & Hold      : {benchmark_return:+.2f}%")
    print(f"Excess Return   : {excess_return:+.2f}%")
    print()
    print(f"Strategy CAGR   : {strategy_cagr:+.2f}%")
    print(f"Buy & Hold CAGR : {benchmark_cagr:+.2f}%")
    print()

    result = (
        "OUTPERFORMED"
        if excess_return > 0
        else "UNDERPERFORMED"
        if excess_return < 0
        else "MATCHED BENCHMARK"
    )
    print(f"Result          : {result}")

    if excess_return < 0:
        print("Assessment      : Current strategy does not outperform passive Buy & Hold.")
    elif excess_return > 0:
        print("Assessment      : Current strategy outperforms passive Buy & Hold.")
    else:
        print("Assessment      : Current strategy matches passive Buy & Hold.")


def print_backtest_report(
    metrics: dict[str, Any],
) -> None:
    print("KẾT QUẢ BACKTEST ENGINE V5.2")

    print_portfolio_summary(metrics)
    print_cost_breakdown(metrics)
    print_trade_analytics(metrics)
    print_distributions(metrics)
    print_benchmark_comparison(metrics)
    print_executive_summary(metrics)
    print_reject_reasons(metrics)

    print("=" * 60)