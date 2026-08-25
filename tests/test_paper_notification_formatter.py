from execution.signal_executor import (
    PaperExecutionBatchResult,
    PaperSignalExecution,
)
from services.paper_notification_formatter import (
    build_paper_execution_message,
)


def _execution(
    symbol: str,
    status: str,
    rank: int,
    score: float,
) -> PaperSignalExecution:
    return PaperSignalExecution(
        symbol=symbol,
        status=status,
        quantity=100 if status == "FILLED" else 0,
        requested_price=10_000,
        fill_price=(
            10_005
            if status == "FILLED"
            else None
        ),
        gross_value=(
            1_000_500
            if status == "FILLED"
            else 0
        ),
        commission=(
            1_501
            if status == "FILLED"
            else 0
        ),
        position_sizer="atr_risk",
        signal_rank=rank,
        signal_score=score,
        reason=(
            "Đã đạt giới hạn lệnh mua mới trong ngày "
            "(tối đa 3 lệnh)."
            if status == "SKIPPED"
            else ""
        ),
    )


def test_daily_message_separates_processed_and_new_orders() -> None:
    processed = PaperExecutionBatchResult(
        enabled=True,
        position_sizer="atr_risk",
        executions=[
            _execution("PDR", "FILLED", 1, 88),
            _execution("VSC", "FILLED", 2, 86),
            _execution("GAS", "FILLED", 3, 82),
            _execution("SBT", "SKIPPED", 4, 79),
            _execution("DIG", "SKIPPED", 5, 79),
            _execution("BAF", "SKIPPED", 6, 77),
        ],
    )
    queued = PaperExecutionBatchResult(
        enabled=True,
        position_sizer="atr_risk",
        executions=[
            PaperSignalExecution(
                symbol="DBC",
                status="QUEUED",
                signal_rank=1,
                signal_score=81,
                reason=(
                    "Chờ khớp tại open phiên kế tiếp."
                ),
            ),
            PaperSignalExecution(
                symbol="SJS",
                status="QUEUED",
                signal_rank=2,
                signal_score=67,
                reason=(
                    "Chờ khớp tại open phiên kế tiếp."
                ),
            ),
        ],
        cash=67_904_914,
        equity=99_419_914,
        gross_exposure_pct=31.70,
        open_positions=3,
        unrealized_pnl=-580_086,
    )

    message = build_paper_execution_message(
        queued,
        processed_result=processed,
    )

    assert "LỆNH MUA ĐÃ XỬ LÝ TẠI OPEN HÔM NAY" in message
    assert "Đã khớp: <b>3</b>" in message
    assert "Bỏ qua: <b>3</b>" in message
    assert "PDR" in message
    assert "Hạng #1" in message
    assert "88/100" in message
    assert "SBT" in message
    assert "Hạng #4" in message
    assert "tối đa 3 lệnh" in message
    assert "TÍN HIỆU MỚI CHỜ OPEN PHIÊN KẾ TIẾP" in message
    assert "Chờ open: <b>2</b>" in message
    assert "DBC" in message
    assert "SJS" in message


def test_daily_message_does_not_call_queued_signals_filled() -> None:
    queued = PaperExecutionBatchResult(
        enabled=True,
        position_sizer="atr_risk",
        executions=[
            PaperSignalExecution(
                symbol="DBC",
                status="QUEUED",
                signal_rank=1,
                signal_score=81,
                reason=(
                    "Chờ khớp tại open phiên kế tiếp."
                ),
            ),
        ],
    )

    message = build_paper_execution_message(
        queued
    )

    assert "Đã khớp: <b>0</b>" in message
    assert "Chờ open: <b>1</b>" in message
    assert "ĐÃ MỞ VỊ THẾ" not in message
