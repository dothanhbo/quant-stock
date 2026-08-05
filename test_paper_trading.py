from execution.models import (
    Order,
    OrderSide,
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


def main() -> None:
    broker = PaperBroker(
        initial_cash=100_000_000,
        commission_rate=0.0015,
        slippage_bps=5.0,
    )

    risk_guard = RiskGuard(
        RiskLimits(
            maximum_position_pct=25.0,
            maximum_gross_exposure_pct=80.0,
            maximum_open_positions=10,
            maximum_daily_loss_pct=3.0,
            minimum_cash_buffer_pct=5.0,
        )
    )

    manager = OrderManager(
        broker=broker,
        risk_guard=risk_guard,
    )

    buy_fill = manager.buy_market(
        symbol="HPG",
        quantity=500,
        price=25_000,
    )

    print("BUY FILL")
    print(buy_fill)
    print()

    broker.update_market_price(
        "HPG",
        27_000,
    )

    print("AFTER PRICE UPDATE")
    print(
        broker.get_portfolio_snapshot()
    )
    print(
        broker.get_position("HPG")
    )
    print()

    sell_fill = manager.sell_market(
        symbol="HPG",
        quantity=200,
        price=27_000,
    )

    print("SELL FILL")
    print(sell_fill)
    print()

    print("FINAL SNAPSHOT")
    print(
        broker.get_portfolio_snapshot()
    )
    print(
        broker.get_position("HPG")
    )


if __name__ == "__main__":
    main()
