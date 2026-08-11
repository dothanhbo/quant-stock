from __future__ import annotations

from execution.lifecycle_manager import LifecycleRunResult
from execution.signal_executor import PaperExecutionBatchResult
from services.lifecycle_notification_formatter import (
    build_exited_positions_section,
    build_holdings_section,
    build_lifecycle_warning_section,
    money,
)
from services.paper_notification_formatter import (
    build_today_orders_section,
)


def _total_current_shares(
    lifecycle_result: LifecycleRunResult,
) -> int:
    return sum(
        int(getattr(item, "quantity", 0) or 0)
        for item in lifecycle_result.held
    )


def build_daily_paper_trading_message(
    *,
    lifecycle_result: LifecycleRunResult,
    execution_result: PaperExecutionBatchResult,
) -> str:
    lines = [
        "<b>📒 BÁO CÁO PAPER TRADING HẰNG NGÀY</b>",
        "",
        f"Ngày xử lý: <b>{lifecycle_result.valuation_date}</b>",
        "",
        *build_holdings_section(lifecycle_result),
        *build_exited_positions_section(lifecycle_result),
        *build_today_orders_section(execution_result),
        *build_lifecycle_warning_section(lifecycle_result),
        "<b>📊 TỔNG QUAN DANH MỤC</b>",
        "",
        f"Tiền mặt: {execution_result.cash:,.0f} đ",
        f"Tổng tài sản: {execution_result.equity:,.0f} đ",
        (
            "Lãi/lỗ đã thực hiện: "
            f"{money(lifecycle_result.realized_pnl)}"
        ),
        (
            "Lãi/lỗ chưa thực hiện: "
            f"{money(lifecycle_result.unrealized_pnl)}"
        ),
        (
            "Tỷ trọng cổ phiếu: "
            f"{execution_result.gross_exposure_pct:.2f}%"
        ),
        (
            "Tổng số cổ phiếu đang nắm giữ: "
            f"{_total_current_shares(lifecycle_result):,} cổ"
        ),
        (
            "Số vị thế đang nắm giữ: "
            f"{execution_result.open_positions}"
        ),
    ]
    return "\n".join(lines).strip()
