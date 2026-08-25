from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from app.daily_pipeline import (
    DailyPipeline,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Chạy toàn bộ quy trình Quant Stock "
            "cuối ngày bằng một lệnh."
        )
    )

    parser.add_argument(
        "--skip-update",
        action="store_true",
        help="Bỏ qua cập nhật market data.",
    )
    parser.add_argument(
        "--skip-lifecycle",
        action="store_true",
        help="Bỏ qua quản lý vị thế paper.",
    )
    parser.add_argument(
        "--skip-scan",
        action="store_true",
        help="Bỏ qua scanner và paper BUY.",
    )
    parser.add_argument(
        "--stop-on-data-errors",
        action="store_true",
        help=(
            "Dừng pipeline nếu còn bất kỳ "
            "mã dữ liệu nào cập nhật lỗi."
        ),
    )

    return parser


def update_market_data(
) -> tuple[int, list[str]]:
    # Lazy imports keep startup clean and avoid configuring
    # vnstock/Telegram until the relevant stage begins.
    from core.universe import (
        get_all_symbols,
    )
    from scripts.update_data import (
        update_all_symbols,
    )

    symbols = list(
        get_all_symbols()
    )

    if len(symbols) < 100:
        raise RuntimeError(
            "Universe không hợp lệ: "
            f"chỉ có {len(symbols)} mã."
        )

    return update_all_symbols(
        symbols
    )


def run_paper_lifecycle():
    from scripts.run_paper_lifecycle import (
        main,
    )

    return main()


def run_strategy_scanner(
    pending_execution_result=None,
):
    from strategy.scanner import (
        run_scan,
    )

    return run_scan(
        pending_execution_result=(
            pending_execution_result
        )
    )


def main() -> int:
    load_dotenv()
    args = build_parser().parse_args()

    pending_execution_result = None

    def lifecycle_stage():
        nonlocal pending_execution_result
        pending_execution_result = (
            run_paper_lifecycle()
        )

    def scanner_stage():
        return run_strategy_scanner(
            pending_execution_result=(
                pending_execution_result
            )
        )

    pipeline = DailyPipeline(
        update_market_data=(
            update_market_data
        ),
        run_lifecycle=(
            lifecycle_stage
        ),
        run_scanner=(
            scanner_stage
        ),
    )

    try:
        result = pipeline.run(
            skip_update=args.skip_update,
            skip_lifecycle=(
                args.skip_lifecycle
            ),
            skip_scan=args.skip_scan,
            stop_on_data_errors=(
                args.stop_on_data_errors
            ),
        )
    except KeyboardInterrupt:
        print(
            "\n⛔ Pipeline đã được người dùng dừng."
        )
        return 130

    return (
        0
        if result.success
        else 1
    )


if __name__ == "__main__":
    sys.exit(
        main()
    )
