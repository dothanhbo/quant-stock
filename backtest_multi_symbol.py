import os
import traceback

import pandas as pd

from backtest_engine import (
    INITIAL_CAPITAL,
    calculate_backtest_metrics,
    run_backtest
)
from sqlalchemy import text
from database import engine


OUTPUT_FOLDER = "backtest_results_multi"

WARMUP_ROWS = 80
MAX_HOLDING_DAYS = 20


def get_symbol_list():
    query = text("""
        SELECT DISTINCT symbol
        FROM prices
        WHERE symbol IS NOT NULL
        ORDER BY symbol
    """)

    with engine.connect() as connection:
        rows = connection.execute(query).fetchall()

    symbols = []

    for row in rows:
        symbol = str(row[0]).strip().upper()

        if not symbol:
            continue

        # VNINDEX chỉ là chỉ số tham chiếu,
        # không backtest như cổ phiếu.
        if symbol == "VNINDEX":
            continue

        symbols.append(symbol)

    return sorted(set(symbols))

def run_multi_symbol_backtest(symbols):
    all_trades = []
    symbol_summaries = []

    total_symbols = len(symbols)

    for index, symbol in enumerate(
        symbols,
        start=1
    ):
        print()
        print("=" * 72)
        print(
            f"[{index}/{total_symbols}] "
            f"Đang backtest {symbol}"
        )
        print("=" * 72)

        try:
            trades = run_backtest(
                symbol=symbol,
                warmup_rows=WARMUP_ROWS,
                max_holding_days=MAX_HOLDING_DAYS
            )

            if not trades:
                symbol_summaries.append({
                    "symbol": symbol,
                    "total_trades": 0,
                    "winning_trades": 0,
                    "losing_trades": 0,
                    "win_rate_pct": 0.0,
                    "average_return_pct": 0.0,
                    "compounded_return_pct": 0.0,
                    "profit_factor": 0.0,
                    "max_drawdown_pct": 0.0
                })

                print(
                    f"⚪ {symbol}: "
                    "Không có giao dịch"
                )

                continue

            for trade in trades:
                trade["symbol"] = symbol

            all_trades.extend(trades)

            _, _, metrics = (
                calculate_backtest_metrics(
                    trades=trades,
                    initial_capital=INITIAL_CAPITAL
                )
            )

            symbol_summaries.append({
                "symbol": symbol,
                "total_trades": metrics[
                    "total_trades"
                ],
                "winning_trades": metrics[
                    "winning_trades"
                ],
                "losing_trades": metrics[
                    "losing_trades"
                ],
                "win_rate_pct": round(
                    metrics["win_rate_pct"],
                    4
                ),
                "average_return_pct": round(
                    metrics["average_return_pct"],
                    4
                ),
                "compounded_return_pct": round(
                    metrics[
                        "compounded_return_pct"
                    ],
                    4
                ),
                "profit_factor": round(
                    metrics["profit_factor"],
                    4
                ),
                "max_drawdown_pct": round(
                    metrics["max_drawdown_pct"],
                    4
                )
            })

            print(
                f"✅ {symbol}: "
                f"{metrics['total_trades']} lệnh | "
                f"Win rate "
                f"{metrics['win_rate_pct']:.2f}% | "
                f"Return "
                f"{metrics['compounded_return_pct']:+.2f}%"
            )

        except Exception as error:
            print(
                f"❌ {symbol}: "
                f"{type(error).__name__}: {error}"
            )

            traceback.print_exc()

            symbol_summaries.append({
                "symbol": symbol,
                "error": str(error)
            })

    return all_trades, symbol_summaries

def calculate_overall_metrics(all_trades):
    if not all_trades:
        return None

    df = pd.DataFrame(all_trades).copy()

    df["return_pct"] = pd.to_numeric(
        df["return_pct"],
        errors="coerce"
    )

    df = df.dropna(
        subset=["return_pct"]
    )

    if df.empty:
        return None

    wins = df[df["return_pct"] > 0]
    losses = df[df["return_pct"] < 0]
    breakeven = df[df["return_pct"] == 0]

    total_trades = len(df)
    total_wins = len(wins)
    total_losses = len(losses)

    win_rate = (
        total_wins
        / total_trades
        * 100
    )

    average_return = df[
        "return_pct"
    ].mean()

    average_win = (
        wins["return_pct"].mean()
        if not wins.empty
        else 0.0
    )

    average_loss = (
        losses["return_pct"].mean()
        if not losses.empty
        else 0.0
    )

    gross_profit = wins[
        "return_pct"
    ].sum()

    gross_loss = abs(
        losses["return_pct"].sum()
    )

    if gross_loss > 0:
        profit_factor = (
            gross_profit / gross_loss
        )
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    payoff_ratio = (
        average_win / abs(average_loss)
        if average_loss != 0
        else 0.0
    )

    expectancy = (
        average_return
    )

    return {
        "total_symbols": df[
            "symbol"
        ].nunique(),
        "total_trades": total_trades,
        "winning_trades": total_wins,
        "losing_trades": total_losses,
        "breakeven_trades": len(breakeven),
        "win_rate_pct": win_rate,
        "average_return_pct": average_return,
        "average_win_pct": average_win,
        "average_loss_pct": average_loss,
        "best_trade_pct": df[
            "return_pct"
        ].max(),
        "worst_trade_pct": df[
            "return_pct"
        ].min(),
        "profit_factor": profit_factor,
        "payoff_ratio": payoff_ratio,
        "expectancy_pct": expectancy,
        "average_holding_days": df[
            "holding_days"
        ].mean()
    }

def export_results(
    all_trades,
    symbol_summaries,
    overall_metrics
):
    os.makedirs(
        OUTPUT_FOLDER,
        exist_ok=True
    )

    trades_df = pd.DataFrame(
        all_trades
    )

    summary_df = pd.DataFrame(
        symbol_summaries
    )

    if not summary_df.empty:
        sort_columns = [
            column
            for column in [
                "compounded_return_pct",
                "profit_factor",
                "total_trades"
            ]
            if column in summary_df.columns
        ]

        if sort_columns:
            summary_df = summary_df.sort_values(
                by=sort_columns,
                ascending=False,
                na_position="last"
            )

    trades_path = os.path.join(
        OUTPUT_FOLDER,
        "all_trades.csv"
    )

    summary_path = os.path.join(
        OUTPUT_FOLDER,
        "symbol_summary.csv"
    )

    metrics_path = os.path.join(
        OUTPUT_FOLDER,
        "overall_metrics.csv"
    )

    trades_df.to_csv(
        trades_path,
        index=False,
        encoding="utf-8-sig"
    )

    summary_df.to_csv(
        summary_path,
        index=False,
        encoding="utf-8-sig"
    )

    if overall_metrics is not None:
        metrics_df = pd.DataFrame([
            {
                "metric": key,
                "value": value
            }
            for key, value
            in overall_metrics.items()
        ])

        metrics_df.to_csv(
            metrics_path,
            index=False,
            encoding="utf-8-sig"
        )

    print()
    print("Đã xuất kết quả:")
    print(f"- {trades_path}")
    print(f"- {summary_path}")

    if overall_metrics is not None:
        print(f"- {metrics_path}")

def print_overall_summary(metrics):
    print()
    print("=" * 72)
    print("KẾT QUẢ BACKTEST ĐA MÃ")
    print("=" * 72)

    if metrics is None:
        print("Không có giao dịch nào.")
        return

    print(
        f"Số mã có giao dịch: "
        f"{metrics['total_symbols']}"
    )

    print(
        f"Tổng giao dịch: "
        f"{metrics['total_trades']}"
    )

    print(
        f"Lệnh thắng: "
        f"{metrics['winning_trades']}"
    )

    print(
        f"Lệnh thua: "
        f"{metrics['losing_trades']}"
    )

    print(
        f"Win rate: "
        f"{metrics['win_rate_pct']:.2f}%"
    )

    print(
        f"Return trung bình/lệnh: "
        f"{metrics['average_return_pct']:+.2f}%"
    )

    print(
        f"Lãi trung bình/lệnh thắng: "
        f"{metrics['average_win_pct']:+.2f}%"
    )

    print(
        f"Lỗ trung bình/lệnh thua: "
        f"{metrics['average_loss_pct']:+.2f}%"
    )

    print(
        f"Profit Factor: "
        f"{metrics['profit_factor']:.2f}"
    )

    print(
        f"Payoff Ratio: "
        f"{metrics['payoff_ratio']:.2f}"
    )

    print(
        f"Expectancy/lệnh: "
        f"{metrics['expectancy_pct']:+.2f}%"
    )

    print(
        f"Lệnh tốt nhất: "
        f"{metrics['best_trade_pct']:+.2f}%"
    )

    print(
        f"Lệnh tệ nhất: "
        f"{metrics['worst_trade_pct']:+.2f}%"
    )

    print(
        f"Số phiên giữ trung bình: "
        f"{metrics['average_holding_days']:.2f}"
    )

if __name__ == "__main__":
    symbols = get_symbol_list()

    print(
        f"Tổng số mã trong database: "
        f"{len(symbols)}"
    )

    all_trades, symbol_summaries = (
        run_multi_symbol_backtest(
            symbols=symbols
        )
    )

    overall_metrics = (
        calculate_overall_metrics(
            all_trades
        )
    )

    print_overall_summary(
        overall_metrics
    )

    export_results(
        all_trades=all_trades,
        symbol_summaries=symbol_summaries,
        overall_metrics=overall_metrics
    )