from __future__ import annotations

import html

from execution.signal_executor import (
    PaperExecutionBatchResult,
)


def format_skip_reason(
    reason: str,
) -> str:

    reason = reason.lower()

    if "dưới một lô" in reason:
        return (
            "Khối lượng tính toán nhỏ hơn "
            "1 lô (100 cổ), nên không mở vị thế."
        )

    if "đã có vị thế" in reason:
        return (
            "Đã có vị thế cho mã này trong danh mục."
        )

    if "giới hạn lệnh" in reason:
        return (
            "Đã đạt số lệnh tối đa trong phiên quét."
        )

    if "entry" in reason:
        return (
            "Không xác định được giá mua."
        )

    return reason


def build_paper_execution_message(
    result: PaperExecutionBatchResult,
) -> str:

    if not result.enabled:
        return ""

    position_sizer_name = (
        result.position_sizer
        .replace("_", " ")
        .title()
    )

    lines = [
        "<b>📈 CẬP NHẬT DANH MỤC PAPER TRADING</b>",
        "",
        (
            "📌 Bộ tính khối lượng: "
            f"<b>{html.escape(position_sizer_name)}</b>"
        ),
        "",
        f"✅ Đã khớp lệnh: <b>{result.filled_count}</b>",
        f"⏭ Bỏ qua: <b>{result.skipped_count}</b>",
        f"❌ Từ chối: <b>{result.rejected_count}</b>",
        "",
    ]

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
                    f"🟢 <b>{symbol}</b>",
                    "<b>Đã mở vị thế</b>",
                    "",
                    f"Khối lượng: {execution.quantity:,} cổ",
                    (
                        "Giá tín hiệu: "
                        f"{execution.requested_price:,.2f}"
                    ),
                    (
                        "Giá khớp: "
                        f"{fill_price:,.2f}"
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
                    f"⏭ <b>{symbol}</b>",
                    "<b>Không mở vị thế</b>",
                    "",
                    format_skip_reason(
                        execution.reason
                    ),
                    "",
                ]
            )

            continue

        lines.extend(
            [
                f"❌ <b>{symbol}</b>",
                "<b>Lệnh bị từ chối</b>",
                "",
                html.escape(
                    execution.reason
                ),
                "",
            ]
        )

    lines.extend(
        [
            "<b>📊 Danh mục sau giao dịch</b>",
            f"Tiền mặt: {result.cash:,.0f} đ",
            f"Tổng tài sản: {result.equity:,.0f} đ",
            (
                "Tỷ trọng danh mục: "
                f"{result.gross_exposure_pct:.2f}%"
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