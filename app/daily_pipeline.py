from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from time import perf_counter
from typing import Callable


@dataclass(frozen=True, slots=True)
class PipelineStageResult:
    name: str
    success: bool
    duration_seconds: float
    warning: str = ""
    error: str = ""


@dataclass(slots=True)
class DailyPipelineResult:
    started_at: datetime
    finished_at: datetime | None = None
    stages: list[PipelineStageResult] = field(
        default_factory=list
    )

    @property
    def success(
        self,
    ) -> bool:
        return all(
            stage.success
            for stage in self.stages
        )

    @property
    def duration_seconds(
        self,
    ) -> float:
        return sum(
            stage.duration_seconds
            for stage in self.stages
        )


class DailyPipeline:
    """
    Orchestrates the end-of-day workflow.

    Default order:
        1. Update market data
        2. Manage existing paper positions and exits
        3. Scan new signals and execute paper BUY orders

    Exits run before entries so released cash can be reused by the
    scanner on the same daily run.
    """

    def __init__(
        self,
        *,
        update_market_data: Callable[
            [],
            tuple[int, list[str]],
        ],
        run_lifecycle: Callable[
            [],
            None,
        ],
        run_scanner: Callable[
            [],
            object,
        ],
        get_market_date: Callable[[], str | None] | None = None,
        get_today: Callable[[], date] = date.today,
    ) -> None:
        self.update_market_data = (
            update_market_data
        )
        self.run_lifecycle = run_lifecycle
        self.run_scanner = run_scanner
        self.get_market_date = get_market_date
        self.get_today = get_today

    def run(
        self,
        *,
        skip_update: bool = False,
        skip_lifecycle: bool = False,
        skip_scan: bool = False,
        stop_on_data_errors: bool = False,
    ) -> DailyPipelineResult:
        result = DailyPipelineResult(
            started_at=datetime.now()
        )

        print(
            "\n"
            + "=" * 68
        )
        print(
            "🚀 DAILY QUANT PIPELINE"
        )
        print(
            "=" * 68
        )
        print(
            "Thứ tự: Update Data → "
            "Paper Lifecycle → Scanner"
        )

        if not skip_update:
            data_stage = self._run_data_stage(
                stop_on_data_errors=(
                    stop_on_data_errors
                )
            )
            result.stages.append(
                data_stage
            )

            if not data_stage.success:
                result.finished_at = (
                    datetime.now()
                )
                self._print_summary(
                    result
                )
                return result
        else:
            result.stages.append(
                PipelineStageResult(
                    name="Update Market Data",
                    success=True,
                    duration_seconds=0.0,
                    warning="Đã bỏ qua theo yêu cầu.",
                )
            )

        # Sau khi update, chỉ chạy lifecycle/scanner khi market DB đã có
        # phiên của ngày hiện tại. Nhờ vậy weekend/ngày lễ/nghỉ bù được
        # phát hiện từ dữ liệu thực tế thay vì hard-code calendar.
        # Các lệnh --skip-update giữ nguyên hành vi vận hành thủ công cũ.
        if (
            not skip_update
            and self.get_market_date is not None
        ):
            session_stage = self._check_current_market_session()
            result.stages.append(session_stage)

            if session_stage.warning:
                result.finished_at = datetime.now()
                self._print_summary(result)
                return result

            if not session_stage.success:
                result.finished_at = datetime.now()
                self._print_summary(result)
                return result

        if not skip_lifecycle:
            lifecycle_stage = (
                self._run_stage(
                    name="Paper Lifecycle",
                    function=(
                        self.run_lifecycle
                    ),
                )
            )
            result.stages.append(
                lifecycle_stage
            )

            if not lifecycle_stage.success:
                result.finished_at = (
                    datetime.now()
                )
                self._print_summary(
                    result
                )
                return result
        else:
            result.stages.append(
                PipelineStageResult(
                    name="Paper Lifecycle",
                    success=True,
                    duration_seconds=0.0,
                    warning="Đã bỏ qua theo yêu cầu.",
                )
            )

        if not skip_scan:
            result.stages.append(
                self._run_stage(
                    name="Strategy Scanner",
                    function=self.run_scanner,
                )
            )
        else:
            result.stages.append(
                PipelineStageResult(
                    name="Strategy Scanner",
                    success=True,
                    duration_seconds=0.0,
                    warning="Đã bỏ qua theo yêu cầu.",
                )
            )

        result.finished_at = datetime.now()
        self._print_summary(
            result
        )
        return result


    def _check_current_market_session(self) -> PipelineStageResult:
        started = perf_counter()

        try:
            latest_market_date = self.get_market_date()
            today = self.get_today().isoformat()

            if latest_market_date != today:
                latest_text = latest_market_date or "không có"
                warning = (
                    f"Không có phiên thị trường mới cho {today} "
                    f"(market DB mới nhất: {latest_text}). "
                    "Bỏ qua lifecycle và scanner."
                )
                print(f"\n⏭️ {warning}")
                return PipelineStageResult(
                    name="Trading Session Guard",
                    success=True,
                    duration_seconds=perf_counter() - started,
                    warning=warning,
                )

            print(
                f"\n✅ Trading Session Guard: "
                f"market DB đã có phiên {today}."
            )
            return PipelineStageResult(
                name="Trading Session Guard",
                success=True,
                duration_seconds=perf_counter() - started,
            )

        except KeyboardInterrupt:
            raise
        except Exception as error:
            return PipelineStageResult(
                name="Trading Session Guard",
                success=False,
                duration_seconds=perf_counter() - started,
                error=f"{type(error).__name__}: {error}",
            )

    def _run_data_stage(
        self,
        *,
        stop_on_data_errors: bool,
    ) -> PipelineStageResult:
        print(
            "\n"
            + "-" * 68
        )
        print(
            "1/3 — CẬP NHẬT DỮ LIỆU THỊ TRƯỜNG"
        )
        print(
            "-" * 68
        )

        started = perf_counter()

        try:
            (
                success_count,
                failed_symbols,
            ) = self.update_market_data()

            duration = (
                perf_counter()
                - started
            )

            warning = ""

            if failed_symbols:
                warning = (
                    f"{len(failed_symbols)} mã vẫn lỗi: "
                    + ", ".join(
                        failed_symbols
                    )
                )

                if stop_on_data_errors:
                    return PipelineStageResult(
                        name="Update Market Data",
                        success=False,
                        duration_seconds=duration,
                        error=warning,
                    )

                print(
                    "\n⚠️ "
                    + warning
                )
                print(
                    "Pipeline vẫn tiếp tục; scanner "
                    "sẽ bỏ qua mã dữ liệu cũ."
                )

            return PipelineStageResult(
                name="Update Market Data",
                success=True,
                duration_seconds=duration,
                warning=warning,
            )

        except KeyboardInterrupt:
            raise

        except Exception as error:
            return PipelineStageResult(
                name="Update Market Data",
                success=False,
                duration_seconds=(
                    perf_counter()
                    - started
                ),
                error=(
                    f"{type(error).__name__}: "
                    f"{error}"
                ),
            )

    def _run_stage(
        self,
        *,
        name: str,
        function: Callable[
            [],
            object,
        ],
    ) -> PipelineStageResult:
        stage_number = {
            "Paper Lifecycle": "2/3",
            "Strategy Scanner": "3/3",
        }.get(
            name,
            "",
        )

        print(
            "\n"
            + "-" * 68
        )
        print(
            f"{stage_number} — "
            f"{name.upper()}"
        )
        print(
            "-" * 68
        )

        started = perf_counter()

        try:
            function()

            return PipelineStageResult(
                name=name,
                success=True,
                duration_seconds=(
                    perf_counter()
                    - started
                ),
            )

        except KeyboardInterrupt:
            raise

        except Exception as error:
            return PipelineStageResult(
                name=name,
                success=False,
                duration_seconds=(
                    perf_counter()
                    - started
                ),
                error=(
                    f"{type(error).__name__}: "
                    f"{error}"
                ),
            )

    @staticmethod
    def _print_summary(
        result: DailyPipelineResult,
    ) -> None:
        print(
            "\n"
            + "=" * 68
        )
        print(
            "📋 DAILY PIPELINE SUMMARY"
        )
        print(
            "=" * 68
        )

        for stage in result.stages:
            icon = (
                "✅"
                if stage.success
                else "❌"
            )

            print(
                f"{icon} {stage.name}: "
                f"{stage.duration_seconds:.1f}s"
            )

            if stage.warning:
                print(
                    f"   ⚠️ {stage.warning}"
                )

            if stage.error:
                print(
                    f"   {stage.error}"
                )

        print(
            "-" * 68
        )
        print(
            "Kết quả: "
            + (
                "THÀNH CÔNG"
                if result.success
                else "THẤT BẠI"
            )
        )
        print(
            f"Tổng thời gian: "
            f"{result.duration_seconds:.1f}s"
        )
