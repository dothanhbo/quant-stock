from datetime import date
from execution.lifecycle_manager import (
    LifecycleExit,
    LifecycleHold,
    LifecycleRunResult,
)
from services.lifecycle_notification_formatter import (
    build_lifecycle_message,
)

def test_hold_message():
    result = LifecycleRunResult(
        valuation_date=date(2026,8,5),
        held=[
            LifecycleHold(
                symbol="VNM",
                valuation_date=date(2026,8,5),
                market_price=58600,
                highest_price=59529.75,
                effective_stop_price=58020,
                trailing_stop_price=None,
                unrealized_pnl=-305713,
                unrealized_pnl_pct=-1.71,
            )
        ],
        cash=82114287,
        equity=99694287,
        unrealized_pnl=-305713,
        open_positions=1,
    )
    message = build_lifecycle_message(result)
    assert "Tiếp tục nắm giữ" in message
    assert "58,020 đ" in message
    assert "-305,713 đ" in message

def test_exit_message():
    result = LifecycleRunResult(
        valuation_date=date(2026,8,6),
        exited=[
            LifecycleExit(
                symbol="VNM",
                valuation_date=date(2026,8,6),
                quantity=300,
                reference_exit_price=62750,
                fill_price=62718.625,
                realized_pnl=920000,
                return_pct=5.15,
                holding_days=1,
                reason="TAKE_PROFIT",
                order_id="x",
            )
        ],
        cash=100900000,
        equity=100900000,
        realized_pnl=900000,
        open_positions=0,
    )
    message = build_lifecycle_message(result)
    assert "Đã đóng vị thế" in message
    assert "Chốt lời" in message
    assert "+920,000 đ" in message
