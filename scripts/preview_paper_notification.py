from __future__ import annotations

from execution.signal_executor import (
    PaperClosedTradeSummary,
    PaperExecutionBatchResult,
    PaperPositionSummary,
    PaperSignalExecution,
)
from services.paper_notification_formatter import (
    build_paper_execution_message,
)


def build_preview_result() -> PaperExecutionBatchResult:
    return PaperExecutionBatchResult(
        enabled=True,
        position_sizer="atr_risk",
        cash=62_345_210,
        equity=101_284_530,
        gross_exposure_pct=38.44,
        open_positions=2,
        realized_pnl=901_651,
        unrealized_pnl=382_879,
        closed_today=[
            PaperClosedTradeSummary(
                symbol="VNM",
                quantity=300,
                entry_price=59_620,
                exit_price=62_750,
                realized_pnl=901_651,
                return_pct=5.04,
                holding_days=2,
                exit_reason="TAKE_PROFIT",
            ),
        ],
        positions=[
            PaperPositionSummary(
                symbol="HPG",
                quantity=900,
                average_price=28_450,
                market_price=29_200,
                cost_basis=25_605_000,
                market_value=26_280_000,
                unrealized_pnl=675_000,
                unrealized_pnl_pct=2.64,
                stop_price=27_350,
                take_profit_price=31_900,
                holding_days=5,
            ),
            PaperPositionSummary(
                symbol="FPT",
                quantity=100,
                average_price=126_500,
                market_price=123_800,
                cost_basis=12_650_000,
                market_value=12_380_000,
                unrealized_pnl=-270_000,
                unrealized_pnl_pct=-2.13,
                stop_price=121_900,
                take_profit_price=138_500,
                holding_days=3,
            ),
        ],
        executions=[
            PaperSignalExecution(
                symbol="MWG",
                status="FILLED",
                quantity=300,
                requested_price=82_000,
                fill_price=82_041,
                gross_value=24_612_300,
                commission=36_918,
                position_sizer="atr_risk",
            ),
            PaperSignalExecution(
                symbol="HPG",
                status="SKIPPED",
                position_sizer="atr_risk",
                reason="Đã có vị thế paper.",
            ),
            PaperSignalExecution(
                symbol="SSI",
                status="REJECTED",
                quantity=500,
                requested_price=36_500,
                position_sizer="atr_risk",
                reason="Vượt giới hạn tỷ trọng danh mục.",
            ),
        ],
    )


def main() -> None:
    result = build_preview_result()
    message = build_paper_execution_message(
        result
    )

    print(
        "\n"
        + "=" * 68
    )
    print(
        "PAPER NOTIFICATION PREVIEW"
    )
    print(
        "=" * 68
    )
    print(
        message
    )
    print(
        "=" * 68
    )
    print(
        "ℹ️ Preview chỉ tạo object trong memory."
    )
    print(
        "ℹ️ Không đọc/ghi paper_trading.db."
    )
    print(
        "ℹ️ Không gửi Telegram."
    )


if __name__ == "__main__":
    main()
