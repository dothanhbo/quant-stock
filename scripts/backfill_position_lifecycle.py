from __future__ import annotations

import argparse
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from execution.lifecycle_models import PositionLifecycleState
from execution.signal_executor import PaperSignalExecutor


PRICE_SCALE = 1000.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill lifecycle state cho một vị thế paper "
            "đã mở trước khi lifecycle được khởi tạo tự động."
        )
    )
    parser.add_argument(
        "--symbol",
        required=True,
        help="Mã cổ phiếu, ví dụ BAF.",
    )
    parser.add_argument(
        "--market-db",
        default="data/market.db",
        help="Đường dẫn market database.",
    )
    return parser


def load_latest_signal(
    *,
    db_path: Path,
    symbol: str,
) -> sqlite3.Row:
    if not db_path.exists():
        raise FileNotFoundError(
            f"Không tìm thấy market DB: {db_path}"
        )

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row

    try:
        row = connection.execute(
            """
            SELECT
                signal_date,
                symbol,
                entry,
                stop_loss,
                take_profit
            FROM signals
            WHERE UPPER(symbol) = ?
              AND stop_loss IS NOT NULL
            ORDER BY
                signal_date DESC,
                id DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        raise RuntimeError(
            f"Không tìm thấy signal có stop_loss cho {symbol}."
        )

    return row


def load_latest_market_close(
    *,
    db_path: Path,
    symbol: str,
) -> tuple[str, float] | None:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row

    try:
        row = connection.execute(
            """
            SELECT
                time,
                close
            FROM prices
            WHERE UPPER(symbol) = ?
              AND close IS NOT NULL
            ORDER BY date(time) DESC, id DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        return None

    return (
        str(row["time"])[:10],
        float(row["close"]) * PRICE_SCALE,
    )


def resolve_buy_fill(
    executor: PaperSignalExecutor,
    symbol: str,
):
    candidates = []

    for fill in executor.broker.get_fills():
        if fill.symbol.strip().upper() != symbol:
            continue

        side = getattr(
            fill.side,
            "value",
            fill.side,
        )

        if str(side).upper().endswith("BUY"):
            candidates.append(fill)

    if not candidates:
        raise RuntimeError(
            f"Không tìm thấy BUY fill của {symbol}."
        )

    return max(
        candidates,
        key=lambda item: item.created_at,
    )


def main() -> int:
    load_dotenv()
    args = build_parser().parse_args()

    symbol = args.symbol.strip().upper()
    market_db = Path(args.market_db)

    executor = PaperSignalExecutor.from_env()
    position = executor.broker.get_position(symbol)

    if position is None or position.quantity <= 0:
        raise RuntimeError(
            f"{symbol} không có vị thế paper đang mở."
        )

    existing = executor.broker.get_position_lifecycle(
        symbol
    )
    if existing is not None:
        print(
            f"ℹ️ {symbol} đã có lifecycle state. "
            "Không ghi đè."
        )
        return 0

    signal = load_latest_signal(
        db_path=market_db,
        symbol=symbol,
    )

    fill = resolve_buy_fill(
        executor,
        symbol,
    )

    stop_price = (
        float(signal["stop_loss"])
        * PRICE_SCALE
    )

    take_profit_price = (
        float(signal["take_profit"])
        * PRICE_SCALE
        if signal["take_profit"] is not None
        else None
    )

    signal_date = date.fromisoformat(
        str(signal["signal_date"])[:10]
    )

    state = PositionLifecycleState(
        symbol=symbol,
        entry_date=signal_date,
        entry_price=float(fill.price),
        initial_quantity=int(position.quantity),
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        highest_price=float(fill.price),
        updated_at=datetime.now(
            timezone.utc
        ),
    )

    executor.broker.save_position_lifecycle(
        state
    )

    latest_market = load_latest_market_close(
        db_path=market_db,
        symbol=symbol,
    )

    if latest_market is not None:
        market_date, market_price = latest_market
        executor.broker.update_market_price(
            symbol,
            market_price,
            persist_snapshot=True,
        )
    else:
        market_date = "N/A"
        market_price = position.market_price

    print("=" * 72)
    print("PAPER LIFECYCLE BACKFILL")
    print("=" * 72)
    print(f"Symbol       : {symbol}")
    print(f"Entry date   : {signal_date}")
    print(f"Fill price   : {fill.price:,.0f} đ")
    print(f"Stop Loss    : {stop_price:,.0f} đ")
    print(
        "Take Profit  : "
        + (
            f"{take_profit_price:,.0f} đ"
            if take_profit_price is not None
            else "N/A"
        )
    )
    print(
        f"Market price : {market_price:,.0f} đ "
        f"({market_date})"
    )
    print("=" * 72)
    print("✅ Lifecycle state đã được tạo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
