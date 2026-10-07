"""Provider history backfill through the R1/R2 admission guard.

Two modes:

* ordinary (unchanged): ``--symbols`` or the whole universe, rolling window
  ``today - 8*365 days .. today`` (source mode ``BACKFILL``);
* explicit range (V1 P1-OPS-1 acquisition): ``--symbols SYM --start YYYY-MM-DD
  [--end YYYY-MM-DD]`` for EXACTLY ONE symbol that already has stored history.
  Source mode ``BACKFILL_EXPLICIT_RANGE``. It records one provider observation
  of exactly the requested window so that a reviewed rebase can prove
  FULL_STORED_HISTORY coverage. It is an observation path only: the batch goes
  through ``save_price_data`` -> ``admit_price_batch`` like every other write,
  so a back-adjusted history is persisted and BLOCKED, never written over the
  stored rows. If the provider's earliest returned session is later than the
  symbol's first stored session, nothing is admitted and the command reports
  ``PROVIDER_HISTORY_COVERAGE_INSUFFICIENT`` (no observation, no block, no
  market change). ``--dry-run`` prints the plan and the stored range without
  calling the provider.

Exit codes (explicit range): 0 observation recorded with full coverage
(admitted, or blocked for review), 2 refused / coverage insufficient, 1 fetch
or admission failure.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import json
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd
from vnstock.api.quote import Quote

import core.database as market_database
from core import market_admission
from core.database import (
    save_price_data,
)
from core.market_admission import kbs_context
from core.universe import (
    get_all_symbols,
)


# Bản Community của vnstock giới hạn OHLCV 1D
# tối đa khoảng 8 năm.
BACKFILL_YEARS = 8

# KBS đang phản hồi nhanh hơn VCI trong quá trình test.
DATA_SOURCE = "KBS"

# Khoảng nghỉ giữa các request.
REQUEST_DELAY_SECONDS = 1.2


def get_backfill_start_date() -> datetime:
    return (
        datetime.now()
        - timedelta(
            days=365 * BACKFILL_YEARS
        )
    )


def normalize_symbols(
    symbols: list[str],
) -> list[str]:
    return list(
        dict.fromkeys(
            symbol.strip().upper()
            for symbol in symbols
            if symbol.strip()
        )
    )


# ==========================================================================
# Explicit-range acquisition (V1 P1-OPS-1: reviewed-rebase preparation)
# ==========================================================================
EXPLICIT_RANGE_MODE = "BACKFILL_EXPLICIT_RANGE"
COVERAGE_FULL = "FULL_STORED_HISTORY_COVERED"
COVERAGE_INSUFFICIENT = "PROVIDER_HISTORY_COVERAGE_INSUFFICIENT"


class ExplicitRangeRefused(ValueError):
    """The explicit-range request is invalid; nothing was fetched or written."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass
class ExplicitRangeResult:
    symbol: str
    status: str
    requested_start: str
    requested_end: str
    first_stored_session: str | None
    last_stored_session: str | None
    stored_session_count: int
    earliest_returned_session: str | None = None
    latest_returned_session: str | None = None
    returned_session_count: int = 0
    missing_range: list[str] | None = None
    coverage: str | None = None
    admission: dict[str, object] = field(default_factory=dict)
    exit_code: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "status": self.status,
            "source_mode": EXPLICIT_RANGE_MODE,
            "requested_window": [self.requested_start, self.requested_end],
            "stored_history": {
                "first_session": self.first_stored_session,
                "last_session": self.last_stored_session,
                "sessions": self.stored_session_count,
            },
            "returned": {
                "earliest_session": self.earliest_returned_session,
                "latest_session": self.latest_returned_session,
                "sessions": self.returned_session_count,
            },
            "missing_range": self.missing_range,
            "coverage": self.coverage,
            "admission": self.admission,
        }


def parse_iso_date(text: str, name: str) -> date:
    """Strict ``YYYY-MM-DD`` (no other formats, no inference)."""
    value = str(text).strip()
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ExplicitRangeRefused("INVALID_DATE", f"{name} must be YYYY-MM-DD, got {text!r}") from error
    if parsed.isoformat() != value:
        raise ExplicitRangeRefused("INVALID_DATE", f"{name} must be YYYY-MM-DD, got {text!r}")
    return parsed


def stored_session_range(symbol: str, database_path: str | Path | None = None) -> tuple[str | None, str | None, int]:
    """Read-only: ``(first, last, count)`` of the stored sessions of ``symbol``."""
    path = Path(database_path or market_database.DATABASE_PATH)
    if not path.is_file():
        return None, None, 0
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    try:
        row = connection.execute(
            "SELECT MIN(substr(time,1,10)), MAX(substr(time,1,10)), COUNT(DISTINCT substr(time,1,10)) "
            "FROM prices WHERE symbol=?",
            (symbol,),
        ).fetchone()
    finally:
        connection.close()
    return (row[0], row[1], int(row[2] or 0)) if row and row[0] else (None, None, 0)


def validate_explicit_range(
    symbols: list[str] | None,
    start: str,
    end: str | None,
    *,
    today: date,
) -> tuple[str, date, date]:
    """Validate an explicit-range request BEFORE any provider call."""
    if not symbols:
        raise ExplicitRangeRefused(
            "EXPLICIT_START_REQUIRES_EXPLICIT_SYMBOL",
            "--start is only accepted together with --symbols naming exactly one symbol; "
            "an explicit historical window is never applied to the whole universe",
        )
    normalized = normalize_symbols(symbols)
    if len(normalized) != 1:
        raise ExplicitRangeRefused(
            "EXPLICIT_START_REQUIRES_EXACTLY_ONE_SYMBOL",
            f"--start accepts exactly one symbol in V1 (got {len(normalized)}: {', '.join(normalized[:5])})",
        )
    start_date = parse_iso_date(start, "--start")
    end_date = today if end is None else parse_iso_date(end, "--end")
    if end_date > today:
        raise ExplicitRangeRefused("END_IN_FUTURE", f"--end {end_date} is after today ({today})")
    if start_date > end_date:
        raise ExplicitRangeRefused("START_AFTER_END", f"--start {start_date} is after --end {end_date}")
    return normalized[0], start_date, end_date


def plan_explicit_range(symbol: str, start_date: date, end_date: date) -> ExplicitRangeResult:
    """Read-only plan; refuses windows that cannot serve a reviewed rebase."""
    first, last, count = stored_session_range(symbol)
    if first is None:
        raise ExplicitRangeRefused(
            "SYMBOL_HAS_NO_STORED_HISTORY",
            f"{symbol} has no stored history; explicit-range acquisition prepares a reviewed rebase of "
            "an existing symbol (use ordinary backfill for a new symbol)",
        )
    if start_date.isoformat() > first:
        raise ExplicitRangeRefused(
            "EXPLICIT_START_AFTER_FIRST_STORED_SESSION",
            f"--start {start_date} is after {symbol}'s first stored session {first}; the observation could "
            f"never cover the full stored history. Use --start {first}",
        )
    if start_date.isoformat() < first:
        # Earlier provider sessions would extend the stored history backwards: the
        # guard would record a non-rebaseable BLOCKED_HISTORY_EXTENSION block.
        raise ExplicitRangeRefused(
            "EXPLICIT_START_BEFORE_FIRST_STORED_SESSION",
            f"--start {start_date} is before {symbol}'s first stored session {first}; explicit-range "
            f"acquisition starts exactly at the first stored session. Use --start {first}",
        )
    if end_date.isoformat() < last:
        raise ExplicitRangeRefused(
            "EXPLICIT_END_BEFORE_LAST_STORED_SESSION",
            f"--end {end_date} is before {symbol}'s last stored session {last}; use --end {last} or later",
        )
    return ExplicitRangeResult(
        symbol=symbol,
        status="PLANNED",
        requested_start=start_date.isoformat(),
        requested_end=end_date.isoformat(),
        first_stored_session=first,
        last_stored_session=last,
        stored_session_count=count,
    )


def backfill_explicit_range(
    symbol: str,
    *,
    start_date: date,
    end_date: date,
) -> ExplicitRangeResult:
    """Fetch exactly ``start..end`` for one symbol and hand it to the admission guard.

    Coverage is verified BEFORE admission: if the provider's earliest returned
    session is later than the first stored session, nothing is admitted (no
    observation, no block, no market change) and the result is
    ``PROVIDER_HISTORY_COVERAGE_INSUFFICIENT``. The requested start is never
    moved forward and history is never trimmed.
    """
    result = plan_explicit_range(symbol, start_date, end_date)
    request_start, request_end = result.requested_start, result.requested_end
    quote = Quote(symbol=symbol, source=DATA_SOURCE)
    frame = quote.history(start=request_start, end=request_end, interval="1D")
    if frame is None:
        frame = pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    frame = frame.copy()
    frame["symbol"] = symbol

    cutoff = market_admission.completed_session_cutoff(market_admission.utc_now())
    try:
        rows, _excluded = market_admission.normalize_candidate(
            frame, symbol, request_start=request_start, request_end=request_end, cutoff=cutoff
        )
    except market_admission.CandidateRejected:
        rows = None  # structurally invalid: the guard below records the rejection itself
    if rows is not None:
        sessions = sorted(rows)
        result.returned_session_count = len(sessions)
        result.earliest_returned_session = sessions[0] if sessions else None
        result.latest_returned_session = sessions[-1] if sessions else None
        first = str(result.first_stored_session)
        if not sessions or sessions[0] > first:
            result.status = result.coverage = COVERAGE_INSUFFICIENT
            result.missing_range = [first, _previous_text(sessions[0]) if sessions else request_end]
            result.exit_code = 2
            return result
        result.coverage = COVERAGE_FULL

    outcome = save_price_data(
        frame,
        context=kbs_context(EXPLICIT_RANGE_MODE, request_start, request_end),
        symbol=symbol,
    )
    result.admission = {
        "result": outcome.result.value,
        "reason": outcome.reason,
        "observation_id": outcome.observation_id,
        "applied": outcome.applied,
        "blocked": outcome.blocked,
        "already_applied": outcome.already_applied,
        "appended_sessions": list(outcome.appended_sessions),
    }
    if outcome.blocked:
        result.status = "OBSERVATION_RECORDED_BLOCKED"  # the expected outcome for a back-adjustment
        result.exit_code = 0
    elif outcome.applied:
        result.status = "OBSERVATION_RECORDED_ADMITTED"
        result.exit_code = 0
    else:
        result.status = f"ADMISSION_{outcome.result.value}"
        result.exit_code = 1
    return result


def _previous_text(session: str) -> str:
    return (date.fromisoformat(session) - timedelta(days=1)).isoformat()


def backfill_symbol(
    symbol: str,
    *,
    start_date: datetime,
    end_date: datetime,
) -> bool:
    symbol = (
        symbol
        .strip()
        .upper()
    )

    print(
        f"📥 {symbol}: "
        f"{start_date:%Y-%m-%d} "
        f"→ {end_date:%Y-%m-%d}"
    )

    try:
        quote = Quote(
            symbol=symbol,
            source=DATA_SOURCE,
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
            print(
                f"⚠️ {symbol}: Không có dữ liệu."
            )
            return False

        df = df.copy()
        df["symbol"] = symbol

        # Backfill uses the same revision admission guard as the updater:
        # it never replaces stored rows, and a window that starts later than
        # the oldest stored history is compared only against the stored
        # sessions inside the window. Nothing is rebuilt here.
        outcome = save_price_data(
            df,
            context=kbs_context("BACKFILL", start_date, end_date),
            symbol=symbol,
        )

        if outcome.blocked:
            print(
                f"🛑 {symbol}: REVISION BLOCKED "
                f"({outcome.result.value}: {outcome.reason}); "
                "market.db giữ nguyên, observation đã được lưu. "
                "Không tự rebuild."
            )
            return False

        if not outcome.applied:
            print(
                f"❌ {symbol}: {outcome.result.value}: "
                f"{outcome.error or outcome.reason}"
            )
            return False

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
            f"mới nhất {latest_text}"
        )

        return True

    except KeyboardInterrupt:
        print(
            "\n⛔ Người dùng dừng backfill."
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

        return False


def backfill_all_symbols(
    symbols: list[str],
) -> tuple[int, list[str]]:
    normalized_symbols = normalize_symbols(
        symbols
    )

    if not normalized_symbols:
        raise ValueError(
            "Không có mã hợp lệ để backfill."
        )

    start_date = (
        get_backfill_start_date()
    )
    end_date = datetime.now()

    success_count = 0
    failed_symbols: list[str] = []

    print(
        f"\n🚀 Bắt đầu backfill "
        f"{len(normalized_symbols)} mã..."
    )
    print(
        f"Nguồn dữ liệu: {DATA_SOURCE}"
    )
    print(
        f"Khoảng dữ liệu: "
        f"{start_date:%Y-%m-%d} "
        f"→ {end_date:%Y-%m-%d}"
    )

    for index, symbol in enumerate(
        normalized_symbols,
        start=1,
    ):
        print(
            f"\n[{index}/{len(normalized_symbols)}]"
        )

        success = backfill_symbol(
            symbol,
            start_date=start_date,
            end_date=end_date,
        )

        if success:
            success_count += 1
        else:
            failed_symbols.append(
                symbol
            )

        time.sleep(
            REQUEST_DELAY_SECONDS
        )

    print(
        "\n"
        + "=" * 60
    )
    print(
        "📊 KẾT QUẢ BACKFILL"
    )
    print(
        "=" * 60
    )
    print(
        f"✅ Thành công: "
        f"{success_count}/"
        f"{len(normalized_symbols)}"
    )

    if failed_symbols:
        print(
            "❌ Mã lỗi: "
            + ", ".join(
                failed_symbols
            )
        )
    else:
        print(
            "✅ Tất cả mã đã backfill thành công."
        )

    return (
        success_count,
        failed_symbols,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill dữ liệu lịch sử cho "
            "toàn bộ universe hoặc một số mã."
        )
    )

    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help=(
            "Chỉ backfill các mã được chỉ định. "
            "Ví dụ: --symbols SJS VIB"
        ),
    )

    parser.add_argument(
        "--start",
        default=None,
        help=(
            "Explicit-range acquisition (YYYY-MM-DD) for EXACTLY ONE symbol named with --symbols, "
            "normally that symbol's first stored session (reviewed-rebase preparation). "
            "Observation only: the data still goes through the revision admission guard."
        ),
    )
    parser.add_argument(
        "--end",
        default=None,
        help="Explicit-range end (YYYY-MM-DD, default today, never after today). Requires --start.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --start: print the planned window and the stored range; no provider call, no write.",
    )

    return parser


def _explicit_main(args: argparse.Namespace) -> int:
    try:
        symbol, start_date, end_date = validate_explicit_range(
            args.symbols, args.start, args.end, today=datetime.now().date()
        )
        if args.dry_run:
            # Read-only planning aid: always prints the stored range, also when the
            # requested start is not the first stored session (then exit 2).
            first, last, count = stored_session_range(symbol)
            report: dict[str, object] = {
                "symbol": symbol,
                "source_mode": EXPLICIT_RANGE_MODE,
                "requested_window": [start_date.isoformat(), end_date.isoformat()],
                "stored_history": {"first_session": first, "last_session": last, "sessions": count},
                "provider_called": False,
            }
            try:
                plan_explicit_range(symbol, start_date, end_date)
            except ExplicitRangeRefused as error:
                print(json.dumps({**report, "status": "REFUSED", "code": error.code, "message": str(error)},
                                 indent=2, sort_keys=True))
                return 2
            print(json.dumps({**report, "status": "PLANNED"}, indent=2, sort_keys=True))
            return 0
        result = backfill_explicit_range(symbol, start_date=start_date, end_date=end_date)
    except ExplicitRangeRefused as error:
        print(json.dumps({"status": "REFUSED", "code": error.code, "message": str(error)}, indent=2))
        return 2
    except Exception as error:  # noqa: BLE001 - provider/admission failure: nothing to retry silently
        print(json.dumps({"status": "FAILED", "error": f"{type(error).__name__}: {error}"}, indent=2))
        return 1
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return result.exit_code


def main(argv: list[str] | None = None) -> int | None:
    args = build_parser().parse_args(argv)

    if args.start is not None:
        return _explicit_main(args)
    if args.end is not None or args.dry_run:
        print(
            json.dumps(
                {
                    "status": "REFUSED",
                    "code": "EXPLICIT_RANGE_OPTION_WITHOUT_START",
                    "message": "--end and --dry-run are only valid with --start",
                }
            )
        )
        return 2

    if args.symbols:
        symbols = normalize_symbols(
            args.symbols
        )
    else:
        symbols = list(
            get_all_symbols()
        )

        if len(symbols) < 100:
            raise RuntimeError(
                "Danh sách universe không hợp lệ: "
                f"chỉ có {len(symbols)} mã"
            )

    backfill_all_symbols(
        symbols
    )
    return None


if __name__ == "__main__":
    sys.exit(main())