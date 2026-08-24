from __future__ import annotations

import os
import sqlite3

from dotenv import load_dotenv

from execution.exit_engine import (
    ExitEngine,
)
from execution.lifecycle_manager import (
    PaperLifecycleManager,
)
from execution.order_manager import (
    OrderManager,
)
from execution.paper_broker import (
    PaperBroker,
)
from execution.risk_guard import (
    RiskGuard,
    RiskLimits,
)
from execution.signal_executor import PaperSignalExecutor
from config.trading_policy import TradingPolicy


def env_float(
    name: str,
    default: float,
) -> float:
    return float(
        os.getenv(
            name,
            str(default),
        )
    )


def env_int(
    name: str,
    default: int,
) -> int:
    return int(
        os.getenv(
            name,
            str(default),
        )
    )


def main() -> None:
    load_dotenv()

    market_database_path = os.getenv(
        "MARKET_DATABASE_PATH",
        "data/market.db",
    )
    with sqlite3.connect(market_database_path) as connection:
        latest_value = connection.execute(
            "SELECT MAX(date(time)) FROM prices WHERE symbol = 'VNINDEX'"
        ).fetchone()[0]
    if latest_value:
        pending_result = PaperSignalExecutor.from_env().execute_pending_signals(
            valuation_date=str(latest_value),
            market_database_path=market_database_path,
        )
        if pending_result.executions:
            print(
                f"Pending next-open: {pending_result.filled_count} filled, "
                f"{pending_result.skipped_count} skipped, "
                f"{pending_result.rejected_count} rejected."
            )

    policy = TradingPolicy.from_env()

    broker = PaperBroker(
        initial_cash=env_float(
            "PAPER_INITIAL_CASH",
            100_000_000,
        ),
        commission_rate=env_float(
            "PAPER_COMMISSION_RATE",
            0.0015,
        ),
        slippage_bps=env_float(
            "PAPER_SLIPPAGE_BPS",
            5.0,
        ),
        sell_tax_rate=policy.sell_tax_rate,
        database_path=os.getenv(
            "PAPER_DATABASE_PATH",
            "data/paper_trading.db",
        ),
        restore_state=True,
    )

    order_manager = OrderManager(
        broker=broker,
        risk_guard=RiskGuard(
            RiskLimits(
                maximum_position_pct=env_float(
                    "PAPER_MAX_POSITION_PCT",
                    20.0,
                ),
                maximum_gross_exposure_pct=env_float(
                    "PAPER_MAX_EXPOSURE_PCT",
                    80.0,
                ),
                maximum_open_positions=env_int(
                    "PAPER_MAX_OPEN_POSITIONS",
                    10,
                ),
                maximum_daily_loss_pct=env_float(
                    "PAPER_MAX_DAILY_LOSS_PCT",
                    3.0,
                ),
                minimum_cash_buffer_pct=env_float(
                    "PAPER_MIN_CASH_BUFFER_PCT",
                    5.0,
                ),
            )
        ),
    )

    manager = PaperLifecycleManager(
        broker=broker,
        order_manager=order_manager,
        exit_engine=ExitEngine(),
        market_database_path=market_database_path,
    )

    result = manager.run()

    print("\n" + "=" * 64)
    print("PAPER POSITION LIFECYCLE")
    print("=" * 64)
    print(
        f"Ngày xử lý: {result.valuation_date}"
    )
    print(
        f"Giữ vị thế: {len(result.held)}"
    )
    print(
        f"Đã thoát: {len(result.exited)}"
    )

    for item in result.held:
        print(
            "\n"
            f"🟡 {item.symbol} | HOLD | "
            f"PnL {item.unrealized_pnl:+,.0f} đ "
            f"({item.unrealized_pnl_pct:+.2f}%) | "
            f"Stop {item.effective_stop_price:,.0f}"
        )

    for item in result.exited:
        print(
            "\n"
            f"🔴 {item.symbol} | EXIT "
            f"{item.reason} | "
            f"{item.quantity:,} cổ | "
            f"Fill {item.fill_price:,.0f} đ | "
            f"PnL {item.realized_pnl:+,.0f} đ "
            f"({item.return_pct:+.2f}%)"
        )

    if result.missing_states:
        print(
            "\n⚠️ Chưa có lifecycle state: "
            + ", ".join(
                result.missing_states
            )
        )

    if result.missing_prices:
        print(
            "\n⚠️ Thiếu OHLC: "
            + ", ".join(
                result.missing_prices
            )
        )

    if result.rejected_exits:
        print(
            "\n❌ SELL bị từ chối: "
            + ", ".join(
                result.rejected_exits
            )
        )

    print("\n" + "-" * 64)
    print(
        f"Cash: {result.cash:,.0f} đ"
    )
    print(
        f"Equity: {result.equity:,.0f} đ"
    )
    print(
        "Realized PnL: "
        f"{result.realized_pnl:+,.0f} đ"
    )
    print(
        "Unrealized PnL: "
        f"{result.unrealized_pnl:+,.0f} đ"
    )
    print(
        "Open positions: "
        f"{result.open_positions}"
    )


    print("\nℹ️ Lifecycle chỉ ghi log terminal; "
          "báo cáo danh mục được gửi sau scanner.")



if __name__ == "__main__":
    main()
