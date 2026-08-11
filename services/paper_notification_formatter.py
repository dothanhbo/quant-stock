from __future__ import annotations

import html

from execution.signal_executor import (
    PaperClosedTradeSummary,
    PaperExecutionBatchResult,
    PaperPositionSummary,
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
        return (
            "Đã đạt số lệnh tối đa trong phiên quét."
        )

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


def build_paper_execution_message(
    result: PaperExecutionBatchResult,
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

    lines.extend(
        [
            "<b>📈 LỆNH MUA HÔM NAY</b>",
            "",
            (
                "📌 Bộ tính khối lượng: "
                f"<b>{html.escape(position_sizer_name)}</b>"
            ),
            (
                f"✅ Đã khớp: <b>{result.filled_count}</b> | "
                f"⏭ Bỏ qua: <b>{result.skipped_count}</b> | "
                f"❌ Từ chối: <b>{result.rejected_count}</b>"
            ),
            "",
        ]
    )

    if not result.executions:
        lines.extend(
            [
                "Không có lệnh mua phát sinh hôm nay.",
                "",
            ]
        )

    for execution in result.executions:
        symbol = html.escape(
            execution.symbol
        )

        if execution.status == "FILLED":
            fill_price = (
                execution.fill_price
                if execution.fill_price is not None
                else 0.0
            )

            lines.extend(
                [
                    (
                        f"🟢 <b>{symbol}</b> — "
                        "<b>ĐÃ MỞ VỊ THẾ</b>"
                    ),
                    (
                        "Số lượng: "
                        f"<b>{execution.quantity:,} cổ</b>"
                    ),
                    (
                        "Giá tín hiệu: "
                        f"{execution.requested_price:,.0f} đ"
                    ),
                    (
                        "Giá khớp: "
                        f"{fill_price:,.0f} đ"
                    ),
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
            )
            continue

        if execution.status == "SKIPPED":
            lines.extend(
                [
                    (
                        f"⏭ <b>{symbol}</b> — "
                        "<b>BỎ QUA</b>"
                    ),
                    format_skip_reason(
                        execution.reason
                    ),
                    "",
                ]
            )
            continue

        lines.extend(
            [
                (
                    f"❌ <b>{symbol}</b> — "
                    "<b>TỪ CHỐI</b>"
                ),
                html.escape(
                    execution.reason
                ),
                "",
            ]
        )

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
