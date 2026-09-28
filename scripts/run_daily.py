from __future__ import annotations

import argparse
from functools import lru_cache
import json
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


@lru_cache(maxsize=1)
def resolve_required_market_symbols() -> tuple[str, ...]:
    """Resolve the current production VN100+VNINDEX universe once per run."""
    from core.universe import get_all_symbols

    symbols = tuple(
        dict.fromkeys(
            symbol.strip().upper()
            for symbol in get_all_symbols()
            if symbol.strip()
        )
    )
    if len(symbols) < 100 or "VNINDEX" not in symbols:
        raise RuntimeError(
            "Universe không hợp lệ cho integrity gate: "
            f"{len(symbols)} mã, VNINDEX={'có' if 'VNINDEX' in symbols else 'thiếu'}."
        )
    return symbols


def bootstrap_market_database() -> None:
    """Create required market tables only at the explicit daily runtime boundary."""
    from core.database import initialize_market_database

    initialize_market_database()


def update_market_data() -> tuple[int, list[str]]:
    # Lazy imports keep startup clean and avoid configuring
    # vnstock/Telegram until the relevant stage begins.
    from scripts.update_data import (
        update_all_symbols,
    )

    symbols = list(resolve_required_market_symbols())

    return update_all_symbols(
        symbols
    )


def validate_market_data():
    from core.market_data_integrity import check_market_data_integrity

    return check_market_data_integrity(
        required_symbols=resolve_required_market_symbols(),
    )


def get_market_date() -> str | None:
    from core.database import (
        get_reference_market_date,
    )

    return get_reference_market_date()


def run_forward_validation_daily():
    from quantlab.forward import run_forward_validation_daily as run_forward

    result = run_forward()
    print(json.dumps(result.as_dict(), sort_keys=True))
    return result


PAPER_V3_VERSION = "V3_BREADTH_40_60"


def _use_v3() -> bool:
    import os

    return (
        os.getenv("PAPER_STRATEGY_VERSION", "Q70_FROZEN").strip().upper()
        == PAPER_V3_VERSION
    )


def run_paper_v2_lifecycle():
    from scripts.run_paper_v2_lifecycle import main

    return main()


def run_paper_v3_lifecycle():
    from scripts.run_paper_v3_lifecycle import main

    return main()


def run_strategy_scanner(
    pending_execution_result=None,
):
    from strategy.scanner import run_scan

    if _use_v3():
        from strategy.paper_v3_scanner import PaperV3Scanner

        processor = PaperV3Scanner(threshold=0.70).process
    else:
        from strategy.paper_v2_scanner import PaperV2Scanner

        processor = PaperV2Scanner(threshold=0.70).process

    return run_scan(
        pending_execution_result=pending_execution_result,
        result_processor=processor,
    )

def main() -> int:
    load_dotenv()
    args = build_parser().parse_args()
    resolve_required_market_symbols.cache_clear()
    bootstrap_market_database()

    if args.skip_lifecycle and _use_v3():
        from scripts.run_paper_v3_lifecycle import configure_v3_environment

        configure_v3_environment()

    pending_execution_result = None

    def lifecycle_stage():
        nonlocal pending_execution_result
        if _use_v3():
            pending_execution_result = run_paper_v3_lifecycle()
        else:
            pending_execution_result = run_paper_v2_lifecycle()


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
        validate_market_data=validate_market_data,
        run_forward_validation=(
            run_forward_validation_daily
        ),
        get_market_date=(
            get_market_date
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
