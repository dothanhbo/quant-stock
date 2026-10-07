from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import time

import pandas as pd
from dotenv import load_dotenv
from vnstock.api.quote import Quote
from pathlib import Path

load_dotenv()

from core.database import (
    cleanup_price_duplicates,
    get_latest_price_date,
    save_price_data,
)
from core.market_admission import kbs_context
from core.universe import (
    get_all_symbols,
)
from enum import Enum

# Cập nhật lại vài phiên gần nhất để phòng dữ liệu EOD bị điều chỉnh.
REFRESH_OVERLAP_DAYS = 7

# Giới hạn Community khoảng 60 request/phút.
REQUEST_DELAY_SECONDS = 1.2

# Sau lượt chính, chỉ retry những mã bị lỗi.
RETRY_ROUNDS = 2
RETRY_COOLDOWN_SECONDS = 45.0

FAILED_LOG_PATH = "logs/update_data_failed.txt"

class UpdateStatus(str, Enum):
    SUCCESS = "SUCCESS"
    RETRYABLE_ERROR = "RETRYABLE_ERROR"
    NEEDS_BACKFILL = "NEEDS_BACKFILL"
    # The provider's overlap disagrees with stored history (changed or
    # incomplete). market.db is unchanged, the candidate is retained in the
    # observation log, and the symbol is blocked for this run. Never retried:
    # a retry would fetch the same revised data.
    REVISION_BLOCKED = "REVISION_BLOCKED"

def calculate_start_date(
    symbol: str,
) -> datetime:
    latest_date = get_latest_price_date(
        symbol
    )

    if (
        latest_date is None
        or pd.isna(latest_date)
    ):
        raise RuntimeError(
            f"{symbol} chưa có dữ liệu lịch sử. "
            "Hãy chạy backfill_market_data.py trước."
        )

    latest_datetime = pd.to_datetime(
        latest_date,
        errors="coerce",
    )

    if pd.isna(
        latest_datetime
    ):
        raise RuntimeError(
            f"{symbol} có latest_date không hợp lệ: "
            f"{latest_date}"
        )

    return (
        latest_datetime.to_pydatetime()
        - timedelta(
            days=REFRESH_OVERLAP_DAYS
        )
    )


def update_symbol(
    symbol: str,
) -> UpdateStatus:
    symbol = (
        symbol
        .strip()
        .upper()
    )

    try:
        start_date = calculate_start_date(
            symbol
        )
    except RuntimeError as error:
        if "chưa có dữ liệu lịch sử" in str(
            error
        ):
            print(
                f"📦 {symbol}: cần backfill lần đầu."
            )
            return UpdateStatus.NEEDS_BACKFILL

        print(
            f"❌ {symbol}: {error}"
        )
        return UpdateStatus.RETRYABLE_ERROR

    end_date = datetime.now()

    print(
        f"📥 {symbol}: "
        f"{start_date:%Y-%m-%d} "
        f"→ {end_date:%Y-%m-%d}"
    )

    try:
        quote = Quote(
            symbol=symbol,
            source="KBS",
        )

        df = quote.history(
            start=start_date.strftime(
                "%Y-%m-%d"
            ),
            end=end_date.strftime(
                "%Y-%m-%d"
            ),
            interval="1D",
        )

        if (
            df is None
            or df.empty
        ):
            # A window that contains already-stored sessions must not come
            # back empty. Treat it as a failed fetch (retryable, fails closed)
            # instead of reporting success on stale data.
            print(
                f"⚠️ {symbol}: provider trả về rỗng cho cửa sổ "
                "đã có dữ liệu; coi là lỗi fetch."
            )
            return UpdateStatus.RETRYABLE_ERROR

        df = df.copy()
        df["symbol"] = symbol

        outcome = save_price_data(
            df,
            context=kbs_context("UPDATE", start_date, end_date),
            symbol=symbol,
        )

        if outcome.blocked:
            print(
                f"🛑 {symbol}: REVISION BLOCKED "
                f"({outcome.result.value}: {outcome.reason}); "
                "market.db giữ nguyên, observation "
                f"{(outcome.observation_id or '')[:20]} đã được lưu."
            )
            return UpdateStatus.REVISION_BLOCKED

        if not outcome.applied:
            print(
                f"❌ {symbol}: {outcome.result.value}: "
                f"{outcome.error or outcome.reason}"
            )
            return UpdateStatus.RETRYABLE_ERROR

        latest_api_date = pd.to_datetime(
            df["time"],
            errors="coerce",
        ).max()

        latest_text = (
            latest_api_date.strftime(
                "%Y-%m-%d"
            )
            if not pd.isna(
                latest_api_date
            )
            else "không rõ"
        )

        print(
            f"✅ {symbol}: "
            f"append {outcome.rows_appended} phiên mới, "
            f"overlap giống hệt, mới nhất {latest_text}"
        )

        return UpdateStatus.SUCCESS

    except KeyboardInterrupt:
        print(
            "\n⛔ Người dùng dừng cập nhật."
        )
        raise

    except Exception as error:
        error_name = type(
            error
        ).__name__

        print(
            f"❌ {symbol}: "
            f"{error_name}: {error}"
        )

        return UpdateStatus.RETRYABLE_ERROR

def run_symbol_batch(
    symbols: list[str],
    *,
    label: str,
) -> tuple[
    list[str],
    list[str],
    list[str],
    list[str],
]:
    succeeded: list[str] = []
    retryable: list[str] = []
    needs_backfill: list[str] = []
    blocked: list[str] = []

    print(
        "\n"
        + "=" * 60
    )
    print(
        label
    )
    print(
        "=" * 60
    )

    for index, symbol in enumerate(
        symbols,
        start=1,
    ):
        print(
            f"\n[{index}/{len(symbols)}]"
        )

        status = update_symbol(
            symbol
        )

        if status == UpdateStatus.SUCCESS:
            succeeded.append(
                symbol
            )

        elif (
            status
            == UpdateStatus.NEEDS_BACKFILL
        ):
            needs_backfill.append(
                symbol
            )

        elif (
            status
            == UpdateStatus.REVISION_BLOCKED
        ):
            blocked.append(
                symbol
            )

        else:
            retryable.append(
                symbol
            )

        time.sleep(
            REQUEST_DELAY_SECONDS
        )

    return (
        succeeded,
        retryable,
        needs_backfill,
        blocked,
    )

def retry_failed_symbols(
    failed_symbols: list[str],
) -> tuple[list[str], list[str], list[str], list[str]]:
    recovered: list[str] = []
    blocked: list[str] = []
    remaining = list(
        dict.fromkeys(
            failed_symbols
        )
    )
    needs_backfill: list[str] = []


    for retry_round in range(
        1,
        RETRY_ROUNDS + 1,
    ):
        if not remaining:
            break

        print(
            "\n"
            + "=" * 60
        )
        print(
            f"🔁 Chờ {RETRY_COOLDOWN_SECONDS:.0f} giây "
            f"trước retry lần {retry_round}"
        )
        print(
            "Mã cần retry: "
            + ", ".join(
                remaining
            )
        )
        print(
            "=" * 60
        )

        time.sleep(
            RETRY_COOLDOWN_SECONDS
        )

        (
            round_succeeded,
            round_failed,
            round_needs_backfill,
            round_blocked,
        ) = run_symbol_batch(
            remaining,
            label=(
                f"RETRY LẦN {retry_round}"
            ),
        )

        if round_needs_backfill:
            print(
                "📦 Chuyển sang danh sách backfill: "
                + ", ".join(
                    round_needs_backfill
                )
            )

        recovered.extend(
            round_succeeded
        )

        blocked.extend(
            round_blocked
        )

        needs_backfill.extend(
            round_needs_backfill
        )

        if round_needs_backfill:
            print(
                "📦 Chuyển sang danh sách backfill: "
                + ", ".join(
                    round_needs_backfill
                )
            )

        remaining = round_failed

    needs_backfill = list(
        dict.fromkeys(
            needs_backfill
        )
    )

    return (
        recovered,
        remaining,
        needs_backfill,
        list(dict.fromkeys(blocked)),
    )


def write_failed_log(
    failed_symbols: list[str],
) -> None:
    log_path = Path(
        FAILED_LOG_PATH
    )
    log_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not failed_symbols:
        if log_path.exists():
            log_path.unlink()
        return

    content = (
        f"updated_at={datetime.now().isoformat()}\n"
        + "\n".join(
            failed_symbols
        )
        + "\n"
    )

    log_path.write_text(
        content,
        encoding="utf-8",
    )


def update_all_symbols(
    symbols: list[str],
) -> tuple[int, list[str]]:
    normalized_symbols = list(
        dict.fromkeys(
            symbol.strip().upper()
            for symbol in symbols
            if symbol.strip()
        )
    )

    print(
        f"\n🚀 Bắt đầu cập nhật "
        f"{len(normalized_symbols)} mã..."
    )

    (
        first_success,
        first_failed,
        needs_backfill,
        blocked_revisions,
    ) = run_symbol_batch(
        normalized_symbols,
        label="LƯỢT CẬP NHẬT CHÍNH",
    )

    recovered: list[str] = []
    final_failed = first_failed

    if first_failed:
        (
            recovered,
            final_failed,
            retry_backfill,
            retry_blocked,
        ) = retry_failed_symbols(
            first_failed
        )

        blocked_revisions = list(
            dict.fromkeys(
                [
                    *blocked_revisions,
                    *retry_blocked,
                ]
            )
        )

        needs_backfill.extend(
            retry_backfill
        )

        needs_backfill = list(
            dict.fromkeys(
                needs_backfill
            )
        )

    total_success = (
        len(first_success)
        + len(recovered)
    )

    write_failed_log(
        final_failed
    )

    print(
        "\n"
        + "=" * 60
    )
    print(
        "📊 KẾT QUẢ CUỐI"
    )
    print(
        "=" * 60
    )
    print(
        f"✅ Thành công: "
        f"{total_success}/{len(normalized_symbols)}"
    )
    print(
        f"🔁 Khôi phục sau retry: "
        f"{len(recovered)}"
    )

    if final_failed:
        print(
            "❌ Lỗi tạm thời sau retry: "
            + ", ".join(
                final_failed
            )
        )
        print(
            f"📝 Đã ghi vào: "
            f"{FAILED_LOG_PATH}"
        )

    if needs_backfill:
        print(
            "📦 Cần backfill lần đầu: "
            + ", ".join(
                needs_backfill
            )
        )

    if blocked_revisions:
        print(
            "🛑 REVISION BLOCKED (market.db không bị ghi đè, "
            "cần review thủ công, không tự rebuild): "
            + ", ".join(
                blocked_revisions
            )
        )

    if (
        not final_failed
        and not needs_backfill
        and not blocked_revisions
    ):
        print(
            "✅ Tất cả mã đã cập nhật thành công."
        )

    pipeline_issues = [
        *final_failed,
        *needs_backfill,
        *blocked_revisions,
    ]

    return (
        total_success,
        pipeline_issues,
    )

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Incremental market-data updater "
            "với retry riêng cho mã lỗi."
        )
    )

    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help=(
            "Chỉ cập nhật các mã được chỉ định, "
            "ví dụ: --symbols FRT FTS SJS"
        ),
    )

    parser.add_argument(
        "--cleanup-only",
        action="store_true",
        help=(
            "Chỉ phân tích duplicate trong bảng prices (dry-run, không xóa), "
            "không gọi API market data."
        ),
    )

    return parser


def main() -> int:
    """Run the standalone updater and return a truthful process exit code.

    Exit code 1 means at least one requested symbol is still failed or needs
    backfill after retries. Previously the process exited 0 in that case, so
    the operation-history ledger (and QuantCtl/Manager) recorded SUCCESS for an
    incomplete market-data update. Rows already written are unchanged either
    way; only the reported outcome differs. The daily pipeline is unaffected:
    it calls update_all_symbols() directly and already fails closed.
    """
    args = build_parser().parse_args()

    if args.cleanup_only:
        # V1: analysis only. Deleting rows would change the dataset without
        # advancing the dataset version, so apply is disabled (fail closed).
        result = cleanup_price_duplicates()
        print("\n🧹 CLEANUP MARKET DATA (dry-run; apply is disabled in V1)")
        print(f"Tổng số dòng           : {result['rows']:,}")
        print(f"Dòng trùng giống hệt   : {result['redundant_identical_rows']:,}")
        print(f"Nhóm trùng xung đột    : {result['conflicting_groups']:,}")
        print("Không có dòng nào bị xóa hoặc sửa.")
        return 1 if result["conflicting_groups"] else 0

    if args.symbols:
        symbols = args.symbols
    else:
        symbols = list(
            get_all_symbols()
        )

        if len(symbols) < 100:
            raise RuntimeError(
                "Danh sách universe không hợp lệ: "
                f"chỉ có {len(symbols)} mã"
            )

    _, pipeline_issues = update_all_symbols(
        symbols
    )

    if pipeline_issues:
        print(
            "\n❌ Market-data update incomplete; exit code 1: "
            + ", ".join(pipeline_issues)
        )
        return 1
    return 0


if __name__ == "__main__":
    from quantctl.run_history import run_tracked_entrypoint

    raise SystemExit(run_tracked_entrypoint("update", main))
