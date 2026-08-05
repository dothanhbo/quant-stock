from __future__ import annotations

import argparse
import os
from datetime import date

from dotenv import load_dotenv

from execution.lifecycle_models import (
    PositionLifecycleState,
)
from execution.paper_broker import (
    PaperBroker,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Đăng ký lifecycle cho vị thế paper "
            "đã tồn tại trước Milestone 3."
        )
    )
    parser.add_argument(
        "--symbol",
        required=True,
    )
    parser.add_argument(
        "--entry-date",
        required=True,
    )
    parser.add_argument(
        "--stop",
        required=True,
        type=float,
        help="Giá theo nghìn đồng.",
    )
    parser.add_argument(
        "--target",
        type=float,
        default=None,
        help="Giá theo nghìn đồng.",
    )
    parser.add_argument(
        "--trailing-atr",
        type=float,
        default=None,
    )
    parser.add_argument(
        "--max-holding-days",
        type=int,
        default=None,
    )
    return parser


def main() -> None:
    load_dotenv()
    args = build_parser().parse_args()

    broker = PaperBroker(
        initial_cash=float(
            os.getenv(
                "PAPER_INITIAL_CASH",
                "100000000",
            )
        ),
        commission_rate=float(
            os.getenv(
                "PAPER_COMMISSION_RATE",
                "0.0015",
            )
        ),
        slippage_bps=float(
            os.getenv(
                "PAPER_SLIPPAGE_BPS",
                "5",
            )
        ),
        database_path=os.getenv(
            "PAPER_DATABASE_PATH",
            "data/paper_trading.db",
        ),
        restore_state=True,
    )

    symbol = args.symbol.strip().upper()
    position = broker.get_position(
        symbol
    )

    if position is None:
        raise RuntimeError(
            f"Không có vị thế paper {symbol}."
        )

    broker.save_position_lifecycle(
        PositionLifecycleState(
            symbol=symbol,
            entry_date=date.fromisoformat(
                args.entry_date
            ),
            entry_price=(
                position.average_price
            ),
            initial_quantity=(
                position.quantity
            ),
            stop_price=(
                args.stop * 1000
            ),
            take_profit_price=(
                args.target * 1000
                if args.target is not None
                else None
            ),
            highest_price=max(
                position.average_price,
                position.market_price,
            ),
            trailing_stop_price=None,
            trailing_atr_multiplier=(
                args.trailing_atr
            ),
            maximum_holding_days=(
                args.max_holding_days
            ),
        )
    )

    print(
        f"✅ Đã đăng ký lifecycle cho "
        f"{symbol}."
    )


if __name__ == "__main__":
    main()
