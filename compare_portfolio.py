from __future__ import annotations

from collections import Counter

from backtesting.engine import BacktestConfig, backtest_symbol
from backtesting.exit_models import FixedExitModel
from backtesting.portfolio_simulator import PortfolioSimulator


SYMBOLS = [
    "HPG",
    "FPT",
    "MBB",
    "SSI",
]


def format_money(value: float) -> str:
    return f"{value:,.0f} VND"


def print_trade(trade, prefix: str = "") -> None:
    print(
        f"{prefix}"
        f"{trade.symbol:<5} | "
        f"Entry: {trade.entry_date:%Y-%m-%d} @ {trade.entry_price:,.2f} | "
        f"Exit: {trade.exit_date:%Y-%m-%d} @ {trade.exit_price:,.2f} | "
        f"Qty: {trade.quantity:,} | "
        f"PnL: {trade.pnl:,.0f} | "
        f"Return: {trade.return_pct:+.2f}%"
    )


def main() -> None:
    config = BacktestConfig(
        initial_capital=100_000_000,
        position_size_pct=20.0,
    )

    exit_model = FixedExitModel(
        stop_loss_pct=5.0,
        take_profit_pct=10.0,
        max_holding_days=20,
    )

    candidate_trades = []

    print("=" * 100)
    print("BƯỚC 1: SINH CANDIDATE TRADES")
    print("=" * 100)

    for symbol in SYMBOLS:
        print(f"\nĐang backtest {symbol}...")

        trades = backtest_symbol(
            symbol=symbol,
            config=config,
            exit_model=exit_model,
            verbose=False,
        )

        candidate_trades.extend(trades)

        print(f"{symbol}: {len(trades)} candidate trade(s)")

    candidate_trades.sort(
        key=lambda trade: (
            trade.entry_date,
            trade.symbol,
        )
    )

    print("\n" + "=" * 100)
    print(f"TỔNG CANDIDATE TRADES: {len(candidate_trades)}")
    print("=" * 100)

    if not candidate_trades:
        print("Không có candidate trade nào để mô phỏng.")
        return

    for trade in candidate_trades:
        print_trade(trade)

    print("\n" + "=" * 100)
    print("BƯỚC 2: CHẠY ONE PORTFOLIO SIMULATOR")
    print("=" * 100)

    simulator = PortfolioSimulator(
        initial_cash=config.initial_capital,
        position_size_pct=config.position_size_pct,
        max_positions=5,
        lot_size=100,
    )

    result = simulator.simulate(candidate_trades)

    print("\n--- EXECUTED TRADES ---")

    if result.executed_trades:
        for trade in result.executed_trades:
            print_trade(trade, prefix="✅ ")
    else:
        print("Không có giao dịch nào được thực thi.")

    print("\n--- REJECTED TRADES ---")

    if result.rejected_trades:
        for trade in result.rejected_trades:
            print_trade(trade, prefix="⛔ ")
    else:
        print("Không có giao dịch nào bị từ chối.")

    winning_trades = [
        trade
        for trade in result.executed_trades
        if trade.pnl > 0
    ]

    losing_trades = [
        trade
        for trade in result.executed_trades
        if trade.pnl < 0
    ]

    total_profit = sum(
        trade.pnl
        for trade in winning_trades
    )

    total_loss = abs(
        sum(
            trade.pnl
            for trade in losing_trades
        )
    )

    win_rate = (
        len(winning_trades)
        / len(result.executed_trades)
        * 100
        if result.executed_trades
        else 0.0
    )

    profit_factor = (
        total_profit / total_loss
        if total_loss > 0
        else float("inf")
    )

    candidate_by_symbol = Counter(
        trade.symbol
        for trade in candidate_trades
    )

    executed_by_symbol = Counter(
        trade.symbol
        for trade in result.executed_trades
    )

    print("\n" + "=" * 100)
    print("KẾT QUẢ ONE PORTFOLIO")
    print("=" * 100)

    print(f"Vốn ban đầu:      {format_money(config.initial_capital)}")
    print(f"Tiền mặt cuối:    {format_money(result.final_cash)}")
    print(f"Equity cuối:      {format_money(result.final_equity)}")
    print(
        f"Net PnL:          "
        f"{format_money(result.final_equity - config.initial_capital)}"
    )
    print(
        f"Portfolio return: "
        f"{(
            result.final_equity / config.initial_capital - 1
        ) * 100:+.2f}%"
    )

    print("-" * 100)

    print(f"Candidate trades: {len(candidate_trades)}")
    print(f"Executed trades:  {len(result.executed_trades)}")
    print(f"Rejected trades:  {len(result.rejected_trades)}")
    print(f"Lệnh thắng:       {len(winning_trades)}")
    print(f"Lệnh thua:        {len(losing_trades)}")
    print(f"Win rate:         {win_rate:.2f}%")

    if profit_factor == float("inf"):
        print("Profit factor:    ∞")
    else:
        print(f"Profit factor:    {profit_factor:.2f}")

    print("\n--- THỐNG KÊ THEO MÃ ---")

    for symbol in SYMBOLS:
        print(
            f"{symbol:<5} | "
            f"Candidate: {candidate_by_symbol[symbol]:>3} | "
            f"Executed: {executed_by_symbol[symbol]:>3}"
        )

    print("\n--- EQUITY CURVE ---")

    if result.equity_curve.empty:
        print("Equity curve rỗng.")
    else:
        print(result.equity_curve.tail(10).to_string(index=False))

        output_path = "portfolio_equity_curve.csv"

        result.equity_curve.to_csv(
            output_path,
            index=False,
            encoding="utf-8-sig",
        )

        print(f"\nĐã xuất: {output_path}")


if __name__ == "__main__":
    main()