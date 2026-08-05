from __future__ import annotations

import os

from dotenv import load_dotenv

from execution.paper_broker import PaperBroker
from execution.position_updater import (
    PositionUpdateEngine,
)


def _read_float(
    name: str,
    default: float,
) -> float:
    value = os.getenv(
        name
    )

    if value is None:
        return default

    return float(
        value
    )


def main() -> None:
    load_dotenv()

    broker = PaperBroker(
        initial_cash=_read_float(
            "PAPER_INITIAL_CASH",
            100_000_000,
        ),
        commission_rate=_read_float(
            "PAPER_COMMISSION_RATE",
            0.0015,
        ),
        slippage_bps=_read_float(
            "PAPER_SLIPPAGE_BPS",
            5.0,
        ),
        database_path=os.getenv(
            "PAPER_DATABASE_PATH",
            "data/paper_trading.db",
        ),
        restore_state=True,
    )

    engine = PositionUpdateEngine(
        broker=broker,
        market_database_path=os.getenv(
            "MARKET_DATABASE_PATH",
            "data/market.db",
        ),
    )

    result = engine.update_open_positions()

    print(
        "\n"
        + "=" * 60
    )
    print(
        "CẬP NHẬT VỊ THẾ PAPER"
    )
    print(
        "=" * 60
    )
    print(
        f"Ngày định giá: "
        f"{result.valuation_date}"
    )
    print(
        f"Đã cập nhật: "
        f"{result.updated_count}"
    )
    print(
        f"Thiếu dữ liệu: "
        f"{result.missing_count}"
    )

    for item in result.updated:
        print(
            "\n"
            f"{item.symbol} | "
            f"{item.quantity:,} cổ | "
            f"Giá: {item.market_price:,.0f} đ | "
            f"PnL chưa thực hiện: "
            f"{item.unrealized_pnl:+,.0f} đ "
            f"({item.unrealized_pnl_pct:+.2f}%)"
        )

    if result.missing_symbols:
        print(
            "\nThiếu giá đóng cửa: "
            + ", ".join(
                result.missing_symbols
            )
        )

    print(
        "\n"
        + "-" * 60
    )
    print(
        f"Tiền mặt: "
        f"{result.cash:,.0f} đ"
    )
    print(
        f"Giá trị vị thế: "
        f"{result.positions_value:,.0f} đ"
    )
    print(
        f"Tổng tài sản: "
        f"{result.equity:,.0f} đ"
    )
    print(
        f"PnL chưa thực hiện: "
        f"{result.unrealized_pnl:+,.0f} đ"
    )
    print(
        f"Tỷ trọng đầu tư: "
        f"{result.gross_exposure_pct:.2f}%"
    )
    print(
        f"Vị thế đang mở: "
        f"{result.open_positions}"
    )


if __name__ == "__main__":
    main()
