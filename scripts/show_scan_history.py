from __future__ import annotations

import argparse
import json

from sqlalchemy import text

from core.database import engine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Xem lịch sử scan telemetry "
            "đã lưu trong market database."
        )
    )
    parser.add_argument(
        "--days",
        type=int,
        default=10,
        help="Số phiên gần nhất cần hiển thị.",
    )
    parser.add_argument(
        "--date",
        help=(
            "Xem chi tiết một ngày "
            "YYYY-MM-DD."
        ),
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Số near-miss cần hiển thị.",
    )
    return parser


def _pretty_json(
    value: str | None,
) -> dict:
    if not value:
        return {}

    try:
        parsed = json.loads(
            value
        )
        return (
            parsed
            if isinstance(
                parsed,
                dict,
            )
            else {}
        )
    except json.JSONDecodeError:
        return {}


def show_recent(
    days: int,
) -> None:
    query = text(
        """
        SELECT
            scan_date,
            regime,
            passed_count,
            watchlist_count,
            rejected_count,
            fresh_count,
            stale_count,
            condition_fail_stats_json
        FROM scan_daily_stats
        ORDER BY scan_date DESC
        LIMIT :days
        """
    )

    with engine.connect() as connection:
        rows = connection.execute(
            query,
            {"days": max(days, 1)},
        ).mappings().all()

    if not rows:
        print(
            "Chưa có scan telemetry."
        )
        return

    print()
    print(
        "=" * 86
    )
    print(
        "SCAN HISTORY"
    )
    print(
        "=" * 86
    )

    for row in rows:
        fails = _pretty_json(
            row[
                "condition_fail_stats_json"
            ]
        )
        top_fails = sorted(
            fails.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:3]
        fail_text = ", ".join(
            f"{name}:{count}"
            for name, count in top_fails
        ) or "-"

        print(
            f"{row['scan_date']} | "
            f"{row['regime']:<7} | "
            f"P/W/R "
            f"{row['passed_count']}/"
            f"{row['watchlist_count']}/"
            f"{row['rejected_count']} | "
            f"fresh {row['fresh_count']} | "
            f"top fail: {fail_text}"
        )


def show_date(
    scan_date: str,
    top: int,
) -> None:
    daily_query = text(
        """
        SELECT *
        FROM scan_daily_stats
        WHERE scan_date = :scan_date
        """
    )
    eval_query = text(
        """
        SELECT
            symbol,
            status,
            score,
            min_score,
            reason,
            failed_conditions_json,
            relative_strength_20d,
            volume_ratio,
            adx,
            rsi
        FROM scan_evaluation_history
        WHERE scan_date = :scan_date
          AND status != 'PASSED'
          AND score IS NOT NULL
        ORDER BY
            score DESC,
            relative_strength_20d DESC
        LIMIT :top
        """
    )

    with engine.connect() as connection:
        daily = connection.execute(
            daily_query,
            {"scan_date": scan_date},
        ).mappings().first()
        rows = connection.execute(
            eval_query,
            {
                "scan_date": scan_date,
                "top": max(top, 1),
            },
        ).mappings().all()

    if daily is None:
        print(
            f"Không có telemetry ngày {scan_date}."
        )
        return

    print()
    print(
        "=" * 86
    )
    print(
        f"SCAN DETAIL — {scan_date}"
    )
    print(
        "=" * 86
    )
    print(
        f"Regime: {daily['regime']}"
    )
    print(
        "PASSED / WATCHLIST / REJECTED: "
        f"{daily['passed_count']} / "
        f"{daily['watchlist_count']} / "
        f"{daily['rejected_count']}"
    )

    fails = _pretty_json(
        daily[
            "condition_fail_stats_json"
        ]
    )

    print()
    print(
        "Failed conditions:"
    )
    for name, count in sorted(
        fails.items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        print(
            f"- {name:<20}: {count}"
        )

    print()
    print(
        f"Top {top} near-miss:"
    )
    if not rows:
        print(
            "- Không có near-miss."
        )
        return

    for row in rows:
        failed = []
        try:
            failed = json.loads(
                row[
                    "failed_conditions_json"
                ]
                or "[]"
            )
        except json.JSONDecodeError:
            pass

        print(
            f"- {row['symbol']:<6} "
            f"{row['status']:<9} "
            f"{row['score']}/{row['min_score']} "
            f"| fail: {', '.join(failed) or row['reason']} "
            f"| RS {row['relative_strength_20d']} "
            f"| Vol {row['volume_ratio']} "
            f"| ADX {row['adx']} "
            f"| RSI {row['rsi']}"
        )


def main() -> None:
    args = build_parser().parse_args()

    if args.date:
        show_date(
            args.date,
            args.top,
        )
        return

    show_recent(
        args.days
    )


if __name__ == "__main__":
    main()
