from pathlib import Path

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


DATABASE_PATH = Path(
    "paper_trading_test.db"
)


def build_manager() -> tuple[
    PaperBroker,
    OrderManager,
]:
    broker = PaperBroker(
        initial_cash=100_000_000,
        commission_rate=0.0015,
        slippage_bps=5.0,
        database_path=DATABASE_PATH,
        restore_state=True,
    )

    manager = OrderManager(
        broker=broker,
        risk_guard=RiskGuard(
            RiskLimits(
                maximum_position_pct=25.0,
                maximum_gross_exposure_pct=80.0,
                maximum_open_positions=10,
                maximum_daily_loss_pct=3.0,
                minimum_cash_buffer_pct=5.0,
            )
        ),
    )

    return broker, manager


def main() -> None:
    if DATABASE_PATH.exists():
        DATABASE_PATH.unlink()

    wal_path = Path(
        f"{DATABASE_PATH}-wal"
    )
    shm_path = Path(
        f"{DATABASE_PATH}-shm"
    )

    wal_path.unlink(
        missing_ok=True
    )
    shm_path.unlink(
        missing_ok=True
    )

    broker, manager = build_manager()

    fill = manager.buy_market(
        symbol="HPG",
        quantity=500,
        price=25_000,
    )

    print("FIRST PROCESS")
    print(fill)
    print(
        broker.get_portfolio_snapshot()
    )
    print(
        broker.get_position("HPG")
    )
    print()

    del manager
    del broker

    restored_broker, restored_manager = (
        build_manager()
    )

    print("RESTORED PROCESS")
    print(
        restored_broker.get_portfolio_snapshot()
    )
    print(
        restored_broker.get_position("HPG")
    )
    print(
        f"Orders: "
        f"{len(restored_broker.get_orders())}"
    )
    print(
        f"Fills: "
        f"{len(restored_broker.get_fills())}"
    )
    print()

    restored_broker.update_market_price(
        "HPG",
        27_000,
    )

    sell_fill = (
        restored_manager.sell_market(
            symbol="HPG",
            quantity=200,
            price=27_000,
        )
    )

    print("AFTER RESTORED SELL")
    print(sell_fill)
    print(
        restored_broker.get_portfolio_snapshot()
    )
    print(
        restored_broker.get_position("HPG")
    )

    assert fill is not None
    assert sell_fill is not None

    position = restored_broker.get_position(
        "HPG"
    )

    assert position is not None
    assert position.quantity == 300
    assert len(
        restored_broker.get_orders()
    ) == 2
    assert len(
        restored_broker.get_fills()
    ) == 2

    print()
    print(
        "✅ Persistence smoke test passed."
    )


if __name__ == "__main__":
    main()
