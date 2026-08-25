from __future__ import annotations

import html

from execution.signal_executor import (
    PaperClosedTradeSummary,
    PaperExecutionBatchResult,
    PaperPositionSummary,
    PaperSignalExecution,
)


_REASON = {
    "STOP_LOSS": "Cắt lỗ",
    "TAKE_PROFIT": "Chốt lời",
    "TRAILING_STOP": "Trailing Stop",
    "TIME_EXIT": "Hết thời gian nắm giữ",
    "EXIT_SIGNAL": "Tín hiệu thoát",
}


def format_skip_reason(
    reason: str,
) -> str:
    normalized_reason = reason.lower()

    if "dưới một lô" in normalized_reason:
        return (
            "Khối lượng tính toán nhỏ hơn "
            "1 lô (100 cổ), nên không mở vị thế."
        )

    if "đã có vị thế" in normalized_reason:
        return (
            "Đã có vị thế cho mã này trong danh mục."
        )

    if "giới hạn lệnh" in normalized_reason:
        return reason

    if "entry" in normalized_reason:
        return "Không xác định được giá mua."

    return reason


def money(
    value: float,
) -> str:
    return f"{value:+,.0f} đ"


def pct(
    value: float,
) -> str:
    return f"{value:+.2f}%"


def _price(
    value: float | None,
) -> str:
    if value is None:
        return "N/A"

    return f"{value:,.0f} đ"


def _pnl_icon(
    value: float,
) -> str:
    if value > 0:
        return "🟢"

    if value < 0:
        return "🔴"

    return "⚪"


def _build_closed_trade_lines(
    trade: PaperClosedTradeSummary,
) -> list[str]:
    reason = _REASON.get(
        trade.exit_reason,
        trade.exit_reason
        .replace("_", " ")
        .title(),
    )

    return [
        (
            f"{_pnl_icon(trade.realized_pnl)} "
            f"<b>{html.escape(trade.symbol)}</b>"
        ),
        (
            "Số lượng: "
            f"<b>{trade.quantity:,} cổ</b>"
        ),
        f"Giá vốn: {trade.entry_price:,.0f} đ",
        f"Giá bán: {trade.exit_price:,.0f} đ",
        (
            "Lãi/lỗ thực hiện: "
            f"<b>{money(trade.realized_pnl)}</b> "
            f"({pct(trade.return_pct)})"
        ),
        (
            "Thời gian nắm giữ: "
            f"{trade.holding_days} ngày"
        ),
        (
            "Lý do thoát: "
            f"<b>{html.escape(reason)}</b>"
        ),
        "",
    ]


def _build_position_lines(
    position: PaperPositionSummary,
) -> list[str]:
    holding_days = (
        f"{position.holding_days} ngày"
        if position.holding_days is not None
        else "N/A"
    )

    return [
        (
            f"{_pnl_icon(position.unrealized_pnl)} "
            f"<b>{html.escape(position.symbol)}</b>"
        ),
        (
            "Số lượng: "
            f"<b>{position.quantity:,} cổ</b>"
        ),
        (
            "Giá vốn: "
            f"{position.average_price:,.0f} đ"
        ),
        (
            "Giá hiện tại: "
            f"{position.market_price:,.0f} đ"
        ),
        (
            "Giá trị hiện tại: "
            f"{position.market_value:,.0f} đ"
        ),
        (
            "Lãi/lỗ chưa thực hiện: "
            f"<b>{money(position.unrealized_pnl)}</b> "
            f"({pct(position.unrealized_pnl_pct)})"
        ),
        (
            "SL hiện tại: "
            f"<b>{_price(position.stop_price)}</b>"
        ),
        (
            "TP hiện tại: "
            f"<b>{_price(position.take_profit_price)}</b>"
        ),
        (
            "Thời gian nắm giữ: "
            f"{holding_days}"
        ),
        "",
    ]


def _build_signal_priority_lines(
    execution: PaperSignalExecution,
) -> list[str]:
    rank = execution.signal_rank
    score = execution.signal_score

    if rank is not None and score is not None:
        return [
            f"Ưu tiên: <b>Hạng #{rank}</b> | "
            f"Điểm: <b>{score:g}/100</b>"
        ]

    if rank is not None:
        return [f"Ưu tiên: <b>Hạng #{rank}</b>"]

    if score is not None:
        return [f"Điểm tín hiệu: <b>{score:g}/100</b>"]

    return []


def _build_execution_lines(
    execution: PaperSignalExecution,
) -> list[str]:
    symbol = html.escape(
        execution.symbol
    )
    priority_lines = (
        _build_signal_priority_lines(
            execution
        )
    )

    if execution.status == "QUEUED":
        return [
            f"🕘 <b>{symbol}</b> — "
            "<b>CHỜ OPEN PHIÊN KẾ TIẾP</b>",
            *priority_lines,
            html.escape(execution.reason),
            "",
        ]

    if execution.status == "FILLED":
        fill_price = (
            execution.fill_price
            if execution.fill_price is not None
            else 0.0
        )

        return [
            f"🟢 <b>{symbol}</b> — "
            "<b>ĐÃ MỞ VỊ THẾ</b>",
            *priority_lines,
            (
                "Số lượng: "
                f"<b>{execution.quantity:,} cổ</b>"
            ),
            (
                "Giá open tham chiếu: "
                f"{execution.requested_price:,.0f} đ"
            ),
            f"Giá khớp: {fill_price:,.0f} đ",
            (
                "Giá trị lệnh: "
                f"{execution.gross_value:,.0f} đ"
            ),
            (
                "Phí giao dịch: "
                f"{execution.commission:,.0f} đ"
            ),
            "",
        ]

    if execution.status == "SKIPPED":
        return [
            f"⏭ <b>{symbol}</b> — <b>BỎ QUA</b>",
            *priority_lines,
            (
                "Lý do: "
                + html.escape(
                    format_skip_reason(
                        execution.reason
                    )
                )
            ),
            "",
        ]

    return [
        f"❌ <b>{symbol}</b> — <b>TỪ CHỐI</b>",
        *priority_lines,
        "Lý do: " + html.escape(execution.reason),
        "",
    ]


def build_processed_buy_orders_section(
    result: PaperExecutionBatchResult | None,
) -> list[str]:
    executions = (
        [
            execution
            for execution in result.executions
            if execution.status != "QUEUED"
        ]
        if result is not None
        else []
    )

    filled_count = sum(
        execution.status == "FILLED"
        for execution in executions
    )
    skipped_count = sum(
        execution.status == "SKIPPED"
        for execution in executions
    )
    rejected_count = sum(
        execution.status == "REJECTED"
        for execution in executions
    )

    lines = [
        "<b>📈 LỆNH MUA ĐÃ XỬ LÝ TẠI OPEN HÔM NAY</b>",
        "",
        (
            f"✅ Đã khớp: <b>{filled_count}</b> | "
            f"⏭ Bỏ qua: <b>{skipped_count}</b> | "
            f"❌ Từ chối: <b>{rejected_count}</b>"
        ),
        "",
    ]

    if not executions:
        return [
            *lines,
            "Không có lệnh chờ được xử lý trong lần chạy này.",
            "",
        ]

    for execution in executions:
        lines.extend(
            _build_execution_lines(
                execution
            )
        )

    return lines


def build_queued_signals_section(
    result: PaperExecutionBatchResult,
) -> list[str]:
    executions = list(
        result.executions
    )

    lines = [
        "<b>🕘 TÍN HIỆU MỚI CHỜ OPEN PHIÊN KẾ TIẾP</b>",
        "",
        (
            f"🕘 Chờ open: <b>{result.queued_count}</b> | "
            f"⏭ Bỏ qua: <b>{result.skipped_count}</b> | "
            f"❌ Từ chối: <b>{result.rejected_count}</b>"
        ),
        "",
    ]

    if not executions:
        return [
            *lines,
            "Không có tín hiệu mua mới cuối phiên.",
            "",
        ]

    for execution in executions:
        lines.extend(
            _build_execution_lines(
                execution
            )
        )

    return lines


def build_paper_execution_message(
    result: PaperExecutionBatchResult,
    *,
    processed_result: PaperExecutionBatchResult | None = None,
) -> str:
    if not result.enabled:
        return ""

    total_shares = sum(
        position.quantity
        for position in result.positions
    )

    market_value = sum(
        position.market_value
        for position in result.positions
    )

    lines = [
        "<b>📒 BÁO CÁO PAPER TRADING CUỐI NGÀY</b>",
        "",
        "<b>🔴 VỊ THẾ ĐÃ ĐÓNG HÔM NAY</b>",
        "",
    ]

    if result.closed_today:
        for trade in result.closed_today:
            lines.extend(
                _build_closed_trade_lines(
                    trade
                )
            )
    else:
        lines.extend(
            [
                "Không có vị thế đóng hôm nay.",
                "",
            ]
        )

    lines.extend(
        [
            "<b>🟡 DANH MỤC CUỐI NGÀY</b>",
            "",
        ]
    )

    if result.positions:
        for position in result.positions:
            lines.extend(
                _build_position_lines(
                    position
                )
            )
    else:
        lines.extend(
            [
                "Không còn vị thế đang nắm giữ.",
                "",
            ]
        )

    position_sizer_name = (
        result.position_sizer
        .replace("_", " ")
        .title()
    )
    lines.extend([
        (
            "📌 Bộ tính khối lượng: "
            f"<b>{html.escape(position_sizer_name)}</b>"
        ),
        "",
        *build_processed_buy_orders_section(
            processed_result
        ),
        *build_queued_signals_section(
            result
        ),
    ])

    net_pnl = (
        result.realized_pnl
        + result.unrealized_pnl
    )

    lines.extend(
        [
            "<b>📊 TỔNG QUAN CUỐI NGÀY</b>",
            "",
            f"Tiền mặt: {result.cash:,.0f} đ",
            (
                "Giá trị cổ phiếu: "
                f"{market_value:,.0f} đ"
            ),
            f"Tổng tài sản: {result.equity:,.0f} đ",
            (
                "Lãi/lỗ đã thực hiện: "
                f"{money(result.realized_pnl)}"
            ),
            (
                "Lãi/lỗ chưa thực hiện: "
                f"{money(result.unrealized_pnl)}"
            ),
            f"Tổng PnL: {money(net_pnl)}",
            (
                "Tỷ trọng cổ phiếu: "
                f"{result.gross_exposure_pct:.2f}%"
            ),
            (
                "Tổng số cổ phiếu: "
                f"{total_shares:,} cổ"
            ),
            (
                "Số vị thế đang nắm giữ: "
                f"{result.open_positions}"
            ),
        ]
    )

    return "\n".join(
        lines
    ).strip()
