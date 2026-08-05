from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import sqlite3

from core.universe import (
    get_all_symbols,
)


DEFAULT_DATABASE_PATH = Path(
    "data/market.db"
)


@dataclass(frozen=True, slots=True)
class SymbolDataSummary:
    symbol: str
    row_count: int
    first_date: str | None
    latest_date: str | None

    @property
    def has_data(
        self,
    ) -> bool:
        return (
            self.row_count > 0
            and self.first_date is not None
            and self.latest_date is not None
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


def validate_database(
    database_path: Path,
) -> None:
    if not database_path.exists():
        raise FileNotFoundError(
            "Không tìm thấy market database: "
            f"{database_path}"
        )

    with sqlite3.connect(
        database_path
    ) as connection:
        table = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'prices'
            """
        ).fetchone()

    if table is None:
        raise RuntimeError(
            "Database không có bảng prices."
        )


def load_symbol_summaries(
    *,
    database_path: Path,
    symbols: list[str],
) -> list[SymbolDataSummary]:
    validate_database(
        database_path
    )

    if not symbols:
        return []

    placeholders = ", ".join(
        "?"
        for _ in symbols
    )

    query = f"""
        SELECT
            symbol,
            COUNT(*) AS row_count,
            MIN(substr(time, 1, 10)) AS first_date,
            MAX(substr(time, 1, 10)) AS latest_date
        FROM prices
        WHERE symbol IN ({placeholders})
        GROUP BY symbol
    """

    with sqlite3.connect(
        database_path
    ) as connection:
        rows = connection.execute(
            query,
            symbols,
        ).fetchall()

    row_map = {
        str(symbol).strip().upper(): (
            int(row_count),
            str(first_date)
            if first_date is not None
            else None,
            str(latest_date)
            if latest_date is not None
            else None,
        )
        for (
            symbol,
            row_count,
            first_date,
            latest_date,
        ) in rows
    }

    summaries: list[
        SymbolDataSummary
    ] = []

    for symbol in symbols:
        values = row_map.get(
            symbol
        )

        if values is None:
            summaries.append(
                SymbolDataSummary(
                    symbol=symbol,
                    row_count=0,
                    first_date=None,
                    latest_date=None,
                )
            )
            continue

        (
            row_count,
            first_date,
            latest_date,
        ) = values

        summaries.append(
            SymbolDataSummary(
                symbol=symbol,
                row_count=row_count,
                first_date=first_date,
                latest_date=latest_date,
            )
        )

    return summaries


def find_market_reference_date(
    summaries: list[
        SymbolDataSummary
    ],
) -> str | None:
    dates = [
        summary.latest_date
        for summary in summaries
        if summary.latest_date is not None
    ]

    if not dates:
        return None

    counts = Counter(
        dates
    )

    return counts.most_common(
        1
    )[0][0]


def calculate_stale_days(
    *,
    latest_date: str,
    reference_date: str,
) -> int:
    latest = date.fromisoformat(
        latest_date
    )
    reference = date.fromisoformat(
        reference_date
    )

    return (
        reference
        - latest
    ).days


def print_detail_report(
    *,
    summaries: list[
        SymbolDataSummary
    ],
    reference_date: str | None,
) -> None:
    print(
        "\n"
        + "=" * 78
    )
    print(
        "📊 MARKET DATA HEALTH CHECK"
    )
    print(
        "=" * 78
    )

    if reference_date is not None:
        print(
            "Ngày chuẩn thị trường: "
            f"{reference_date}"
        )

    print(
        "-" * 78
    )

    for summary in summaries:
        if not summary.has_data:
            print(
                f"📦 {summary.symbol:<10} | "
                "KHÔNG CÓ DỮ LIỆU"
            )
            continue

        status = "✅"
        stale_text = ""

        if (
            reference_date is not None
            and summary.latest_date
            != reference_date
        ):
            stale_days = calculate_stale_days(
                latest_date=(
                    summary.latest_date
                ),
                reference_date=(
                    reference_date
                ),
            )
            status = "⚠️"
            stale_text = (
                f" | cũ {stale_days} ngày"
            )

        print(
            f"{status} {summary.symbol:<10} | "
            f"{summary.row_count:>5} dòng | "
            f"{summary.first_date} → "
            f"{summary.latest_date}"
            f"{stale_text}"
        )


def print_summary_report(
    *,
    summaries: list[
        SymbolDataSummary
    ],
    reference_date: str | None,
) -> None:
    missing = [
        summary.symbol
        for summary in summaries
        if not summary.has_data
    ]

    stale = [
        summary
        for summary in summaries
        if (
            summary.has_data
            and reference_date is not None
            and summary.latest_date
            != reference_date
        )
    ]

    healthy_count = (
        len(summaries)
        - len(missing)
        - len(stale)
    )

    latest_distribution = Counter(
        summary.latest_date
        if summary.latest_date is not None
        else "NO_DATA"
        for summary in summaries
    )

    print(
        "\n"
        + "=" * 78
    )
    print(
        "📋 TÓM TẮT"
    )
    print(
        "=" * 78
    )
    print(
        f"Tổng số mã       : "
        f"{len(summaries)}"
    )
    print(
        f"Dữ liệu đúng ngày: "
        f"{healthy_count}"
    )
    print(
        f"Dữ liệu cũ       : "
        f"{len(stale)}"
    )
    print(
        f"Chưa có dữ liệu  : "
        f"{len(missing)}"
    )

    print(
        "\nPhân bố ngày dữ liệu mới nhất:"
    )

    for latest_date, count in sorted(
        latest_distribution.items(),
        key=lambda item: (
            item[0] == "NO_DATA",
            item[0],
        ),
        reverse=True,
    ):
        label = (
            "Không có dữ liệu"
            if latest_date == "NO_DATA"
            else latest_date
        )

        print(
            f"  {label:<20}: {count}"
        )

    if stale:
        print(
            "\n⚠️ Mã dữ liệu cũ:"
        )

        for summary in stale:
            stale_days = calculate_stale_days(
                latest_date=(
                    summary.latest_date
                ),
                reference_date=(
                    reference_date
                ),
            )

            print(
                f"  - {summary.symbol}: "
                f"{summary.latest_date} "
                f"(cũ {stale_days} ngày)"
            )

    if missing:
        print(
            "\n📦 Mã cần backfill:"
        )

        for symbol in missing:
            print(
                f"  - {symbol}"
            )

        print(
            "\nLệnh gợi ý:"
        )
        print(
            "py -m scripts.backfill_market_data "
            "--symbols "
            + " ".join(
                missing
            )
        )

    if (
        not stale
        and not missing
    ):
        print(
            "\n✅ Toàn bộ market data đang đồng bộ."
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Kiểm tra tình trạng dữ liệu của "
            "toàn bộ universe hoặc một số mã."
        )
    )

    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help=(
            "Chỉ kiểm tra các mã được chỉ định. "
            "Ví dụ: --symbols VNM HPG FPT"
        ),
    )

    parser.add_argument(
        "--database",
        default=str(
            DEFAULT_DATABASE_PATH
        ),
        help=(
            "Đường dẫn tới market.db. "
            "Mặc định: data/market.db"
        ),
    )

    parser.add_argument(
        "--summary-only",
        action="store_true",
        help=(
            "Chỉ in phần tổng kết, "
            "không in từng mã."
        ),
    )

    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.symbols:
        symbols = normalize_symbols(
            args.symbols
        )
    else:
        symbols = normalize_symbols(
            list(
                get_all_symbols()
            )
        )

        if len(symbols) < 100:
            raise RuntimeError(
                "Universe không hợp lệ: "
                f"chỉ có {len(symbols)} mã."
            )

    summaries = load_symbol_summaries(
        database_path=Path(
            args.database
        ),
        symbols=symbols,
    )

    reference_date = (
        find_market_reference_date(
            summaries
        )
    )

    if not args.summary_only:
        print_detail_report(
            summaries=summaries,
            reference_date=reference_date,
        )

    print_summary_report(
        summaries=summaries,
        reference_date=reference_date,
    )


if __name__ == "__main__":
    main()
