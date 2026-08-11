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


def _get_quantity(item: object) -> int:
    return int(getattr(item, "quantity", 0) or 0)


def _get_average_price(item: object) -> float | None:
    for attribute in ("average_price", "entry_price", "avg_price"):
        value = getattr(item, attribute, None)
        if value is not None:
            return float(value)
    return None


def build_holdings_section(result: LifecycleRunResult) -> list[str]:
    lines = ["<b>🟡 DANH MỤC ĐANG NẮM GIỮ</b>", ""]

    if not result.held:
        return [*lines, "Không có vị thế đang mở.", ""]

    for item in result.held:
        quantity = _get_quantity(item)
        average_price = _get_average_price(item)

        lines.extend([
            f"🟡 <b>{html.escape(item.symbol)}</b>",
            f"Số lượng: <b>{quantity:,} cổ</b>",
        ])

        if average_price is not None:
            lines.append(f"Giá vốn: {average_price:,.0f} đ")

        lines.extend([
            f"Giá hiện tại: {item.market_price:,.0f} đ",
            (
                "Lãi/lỗ chưa thực hiện: "
                f"{money(item.unrealized_pnl)} "
                f"({pct(item.unrealized_pnl_pct)})"
            ),
            f"Mức dừng hiện tại: {item.effective_stop_price:,.0f} đ",
            "",
        ])

    return lines


def build_exited_positions_section(
    result: LifecycleRunResult,
) -> list[str]:
    if not result.exited:
        return []

    lines = ["<b>🔴 VỊ THẾ ĐÃ ĐÓNG HÔM NAY</b>", ""]

    for item in result.exited:
        reason = _REASON.get(
            item.reason,
            item.reason.replace("_", " ").title(),
        )

        lines.extend([
            f"🔴 <b>{html.escape(item.symbol)}</b>",
            f"Số lượng: <b>{item.quantity:,} cổ</b>",
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

    return lines


def build_lifecycle_warning_section(
    result: LifecycleRunResult,
) -> list[str]:
    lines: list[str] = []

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

    return lines


def build_lifecycle_message(result: LifecycleRunResult) -> str:
    lines = [
        "<b>📒 CẬP NHẬT VỊ THẾ PAPER TRADING</b>",
        "",
        f"Ngày xử lý: <b>{result.valuation_date}</b>",
        "",
        *build_holdings_section(result),
        *build_exited_positions_section(result),
        *build_lifecycle_warning_section(result),
    ]
    return "\n".join(lines).strip()
