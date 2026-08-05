from __future__ import annotations
import html
from execution.lifecycle_manager import LifecycleRunResult

_REASON = {
    "STOP_LOSS": "Cắt lỗ",
    "TAKE_PROFIT": "Chốt lời",
    "TRAILING_STOP": "Trailing Stop",
    "TIME_EXIT": "Hết thời gian nắm giữ",
    "EXIT_SIGNAL": "Tín hiệu thoát",
}

def money(value: float) -> str:
    return f"{value:+,.0f} đ"

def pct(value: float) -> str:
    return f"{value:+.2f}%"

def build_lifecycle_message(
    result: LifecycleRunResult,
) -> str:
    lines = [
        "<b>📒 CẬP NHẬT VỊ THẾ PAPER TRADING</b>",
        "",
        f"Ngày xử lý: <b>{result.valuation_date}</b>",
        f"🟡 Tiếp tục nắm giữ: <b>{len(result.held)}</b>",
        f"🔴 Đã đóng vị thế: <b>{len(result.exited)}</b>",
        "",
    ]

    for item in result.held:
        lines.extend([
            f"🟡 <b>{html.escape(item.symbol)}</b>",
            "<b>Tiếp tục nắm giữ</b>",
            "",
            f"Giá đóng cửa: {item.market_price:,.0f} đ",
            (
                "Lãi/lỗ chưa thực hiện: "
                f"{money(item.unrealized_pnl)} "
                f"({pct(item.unrealized_pnl_pct)})"
            ),
            f"Mức dừng hiện tại: {item.effective_stop_price:,.0f} đ",
            "",
        ])

    for item in result.exited:
        reason = _REASON.get(
            item.reason,
            item.reason.replace("_"," ").title(),
        )
        lines.extend([
            f"🔴 <b>{html.escape(item.symbol)}</b>",
            "<b>Đã đóng vị thế</b>",
            "",
            f"Khối lượng: {item.quantity:,} cổ",
            f"Giá khớp bán: {item.fill_price:,.0f} đ",
            (
                "Lãi/lỗ thực hiện: "
                f"{money(item.realized_pnl)} "
                f"({pct(item.return_pct)})"
            ),
            f"Thời gian nắm giữ: {item.holding_days} ngày",
            f"Lý do thoát: <b>{html.escape(reason)}</b>",
            "",
        ])

    if result.missing_states:
        lines.extend([
            "<b>⚠️ Thiếu lifecycle state</b>",
            html.escape(", ".join(result.missing_states)),
            "",
        ])

    if result.missing_prices:
        lines.extend([
            "<b>⚠️ Thiếu dữ liệu OHLC</b>",
            html.escape(", ".join(result.missing_prices)),
            "",
        ])

    if result.rejected_exits:
        lines.extend([
            "<b>❌ Lệnh bán bị từ chối</b>",
            html.escape(", ".join(result.rejected_exits)),
            "",
        ])

    lines.extend([
        "<b>📊 Danh mục cuối ngày</b>",
        f"Tiền mặt: {result.cash:,.0f} đ",
        f"Tổng tài sản: {result.equity:,.0f} đ",
        f"Lãi/lỗ đã thực hiện: {money(result.realized_pnl)}",
        f"Lãi/lỗ chưa thực hiện: {money(result.unrealized_pnl)}",
        f"Số vị thế đang nắm giữ: {result.open_positions}",
    ])
    return "\n".join(lines).strip()
