from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from core.database import engine


_DAILY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS scan_daily_stats (
    scan_date TEXT PRIMARY KEY,
    regime TEXT NOT NULL,
    total_symbols INTEGER NOT NULL DEFAULT 0,
    fresh_count INTEGER NOT NULL DEFAULT 0,
    stale_count INTEGER NOT NULL DEFAULT 0,
    passed_count INTEGER NOT NULL DEFAULT 0,
    watchlist_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    min_score INTEGER,
    min_adx REAL,
    min_volume_ratio REAL,
    min_relative_strength REAL,
    reject_stats_json TEXT NOT NULL DEFAULT '{}',
    condition_fail_stats_json TEXT NOT NULL DEFAULT '{}',
    market_config_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
)
"""

_EVALUATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS scan_evaluation_history (
    scan_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    score INTEGER,
    min_score INTEGER,
    regime TEXT,
    failed_conditions_json TEXT NOT NULL DEFAULT '[]',
    missing_json TEXT NOT NULL DEFAULT '[]',
    relative_strength_20d REAL,
    volume_ratio REAL,
    adx REAL,
    rsi REAL,
    distance_ema20 REAL,
    return_3d REAL,
    entry REAL,
    stop_loss REAL,
    take_profit REAL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (scan_date, symbol)
)
"""

_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_scan_eval_status_date
ON scan_evaluation_history(status, scan_date)
"""


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )


def _to_float(
    value: Any,
) -> float | None:
    if value is None:
        return None

    try:
        return float(value)
    except (
        TypeError,
        ValueError,
    ):
        return None


def _to_int(
    value: Any,
) -> int | None:
    if value is None:
        return None

    try:
        return int(value)
    except (
        TypeError,
        ValueError,
    ):
        return None


def _reference_date(
    scan_stats: dict,
) -> str:
    value = scan_stats.get(
        "reference_date"
    )

    if value is None:
        raise ValueError(
            "scan_stats thiếu reference_date."
        )

    return str(value)[:10]


def _ensure_tables(
    connection,
) -> None:
    connection.execute(
        text(_DAILY_TABLE_SQL)
    )
    connection.execute(
        text(_EVALUATION_TABLE_SQL)
    )
    connection.execute(
        text(_INDEX_SQL)
    )


def persist_scan_telemetry(
    *,
    results: list[dict],
    scan_stats: dict,
) -> None:
    scan_date = _reference_date(
        scan_stats
    )
    market_config = dict(
        scan_stats.get(
            "market_config",
            {},
        )
        or {}
    )
    evaluations = list(
        scan_stats.get(
            "evaluations",
            [],
        )
        or []
    )

    watchlist_count = sum(
        1
        for item in evaluations
        if item.get("status")
        == "WATCHLIST"
    )
    rejected_count = sum(
        1
        for item in evaluations
        if item.get("status")
        == "REJECTED"
    )

    now_text = datetime.now(
        timezone.utc
    ).isoformat()

    daily_row = {
        "scan_date": scan_date,
        "regime": str(
            market_config.get(
                "regime",
                "UNKNOWN",
            )
        ),
        "total_symbols": int(
            scan_stats.get(
                "total_symbols",
                0,
            )
        ),
        "fresh_count": int(
            scan_stats.get(
                "fresh_count",
                0,
            )
        ),
        "stale_count": int(
            scan_stats.get(
                "stale_count",
                0,
            )
        ),
        "passed_count": len(
            results
        ),
        "watchlist_count": (
            watchlist_count
        ),
        "rejected_count": (
            rejected_count
        ),
        "error_count": int(
            scan_stats.get(
                "error_count",
                0,
            )
        ),
        "min_score": _to_int(
            market_config.get(
                "min_score"
            )
        ),
        "min_adx": _to_float(
            market_config.get(
                "min_adx"
            )
        ),
        "min_volume_ratio": (
            _to_float(
                market_config.get(
                    "min_volume_ratio"
                )
            )
        ),
        "min_relative_strength": (
            _to_float(
                market_config.get(
                    "min_relative_strength"
                )
            )
        ),
        "reject_stats_json": _json(
            scan_stats.get(
                "reject_stats",
                {},
            )
        ),
        "condition_fail_stats_json": (
            _json(
                scan_stats.get(
                    "condition_fail_stats",
                    {},
                )
            )
        ),
        "market_config_json": _json(
            market_config
        ),
        "created_at": now_text,
    }

    with engine.begin() as connection:
        _ensure_tables(
            connection
        )

        connection.execute(
            text(
                """
                DELETE FROM scan_daily_stats
                WHERE scan_date = :scan_date
                """
            ),
            {
                "scan_date": scan_date,
            },
        )

        connection.execute(
            text(
                """
                INSERT INTO scan_daily_stats (
                    scan_date,
                    regime,
                    total_symbols,
                    fresh_count,
                    stale_count,
                    passed_count,
                    watchlist_count,
                    rejected_count,
                    error_count,
                    min_score,
                    min_adx,
                    min_volume_ratio,
                    min_relative_strength,
                    reject_stats_json,
                    condition_fail_stats_json,
                    market_config_json,
                    created_at
                )
                VALUES (
                    :scan_date,
                    :regime,
                    :total_symbols,
                    :fresh_count,
                    :stale_count,
                    :passed_count,
                    :watchlist_count,
                    :rejected_count,
                    :error_count,
                    :min_score,
                    :min_adx,
                    :min_volume_ratio,
                    :min_relative_strength,
                    :reject_stats_json,
                    :condition_fail_stats_json,
                    :market_config_json,
                    :created_at
                )
                """
            ),
            daily_row,
        )

        connection.execute(
            text(
                """
                DELETE FROM scan_evaluation_history
                WHERE scan_date = :scan_date
                """
            ),
            {
                "scan_date": scan_date,
            },
        )

        for item in evaluations:
            connection.execute(
                text(
                    """
                    INSERT INTO scan_evaluation_history (
                        scan_date,
                        symbol,
                        status,
                        reason,
                        score,
                        min_score,
                        regime,
                        failed_conditions_json,
                        missing_json,
                        relative_strength_20d,
                        volume_ratio,
                        adx,
                        rsi,
                        distance_ema20,
                        return_3d,
                        entry,
                        stop_loss,
                        take_profit,
                        created_at
                    )
                    VALUES (
                        :scan_date,
                        :symbol,
                        :status,
                        :reason,
                        :score,
                        :min_score,
                        :regime,
                        :failed_conditions_json,
                        :missing_json,
                        :relative_strength_20d,
                        :volume_ratio,
                        :adx,
                        :rsi,
                        :distance_ema20,
                        :return_3d,
                        :entry,
                        :stop_loss,
                        :take_profit,
                        :created_at
                    )
                    """
                ),
                {
                    "scan_date": scan_date,
                    "symbol": str(
                        item.get(
                            "symbol",
                            "",
                        )
                    ),
                    "status": str(
                        item.get(
                            "status",
                            "REJECTED",
                        )
                    ),
                    "reason": str(
                        item.get(
                            "reason",
                            "",
                        )
                    ),
                    "score": _to_int(
                        item.get(
                            "score"
                        )
                    ),
                    "min_score": _to_int(
                        item.get(
                            "min_score"
                        )
                    ),
                    "regime": str(
                        item.get(
                            "regime",
                            market_config.get(
                                "regime",
                                "UNKNOWN",
                            ),
                        )
                    ),
                    "failed_conditions_json": (
                        _json(
                            item.get(
                                "failed_conditions",
                                [],
                            )
                        )
                    ),
                    "missing_json": _json(
                        item.get(
                            "missing",
                            [],
                        )
                    ),
                    "relative_strength_20d": (
                        _to_float(
                            item.get(
                                "relative_strength_20d"
                            )
                        )
                    ),
                    "volume_ratio": _to_float(
                        item.get(
                            "volume_ratio"
                        )
                    ),
                    "adx": _to_float(
                        item.get(
                            "adx"
                        )
                    ),
                    "rsi": _to_float(
                        item.get(
                            "rsi"
                        )
                    ),
                    "distance_ema20": _to_float(
                        item.get(
                            "distance_ema20"
                        )
                    ),
                    "return_3d": _to_float(
                        item.get(
                            "return_3d"
                        )
                    ),
                    "entry": _to_float(
                        item.get(
                            "entry"
                        )
                    ),
                    "stop_loss": _to_float(
                        item.get(
                            "stop_loss"
                        )
                    ),
                    "take_profit": _to_float(
                        item.get(
                            "take_profit"
                        )
                    ),
                    "created_at": now_text,
                },
            )


def print_scan_diagnostics(
    *,
    results: list[dict],
    scan_stats: dict,
    top_near_miss: int = 8,
) -> None:
    evaluations = list(
        scan_stats.get(
            "evaluations",
            [],
        )
        or []
    )
    market_config = dict(
        scan_stats.get(
            "market_config",
            {},
        )
        or {}
    )

    watchlist = [
        item
        for item in evaluations
        if item.get("status")
        == "WATCHLIST"
    ]
    rejected = [
        item
        for item in evaluations
        if item.get("status")
        == "REJECTED"
    ]

    print()
    print(
        "=" * 65
    )
    print(
        "🔎 SCAN DIAGNOSTICS"
    )
    print(
        "=" * 65
    )
    print(
        "Regime: "
        f"{market_config.get('regime', 'UNKNOWN')}"
    )
    print(
        "PASSED / WATCHLIST / REJECTED: "
        f"{len(results)} / "
        f"{len(watchlist)} / "
        f"{len(rejected)}"
    )

    fail_stats = dict(
        scan_stats.get(
            "condition_fail_stats",
            {},
        )
        or {}
    )

    print()
    print(
        "Điều kiện bị fail nhiều nhất:"
    )

    if fail_stats:
        for name, count in sorted(
            fail_stats.items(),
            key=lambda item: (
                item[1],
                item[0],
            ),
            reverse=True,
        ):
            print(
                f"- {name:<20}: "
                f"{count}"
            )
    else:
        print(
            "- Không có dữ liệu fail condition."
        )

    candidates = [
        item
        for item in evaluations
        if item.get("score")
        is not None
        and item.get("status")
        != "PASSED"
    ]

    candidates.sort(
        key=lambda item: (
            int(
                item.get(
                    "score",
                    -1,
                )
            ),
            float(
                item.get(
                    "relative_strength_20d",
                    -999.0,
                )
                or -999.0
            ),
        ),
        reverse=True,
    )

    print()
    print(
        f"Top {top_near_miss} near-miss:"
    )

    if not candidates:
        print(
            "- Không có candidate đủ dữ liệu."
        )
        return

    for item in candidates[
        :top_near_miss
    ]:
        failed = item.get(
            "failed_conditions",
            [],
        ) or []
        missing = item.get(
            "missing",
            [],
        ) or []

        issue_list = (
            missing
            if missing
            else failed
        )
        issue_text = (
            ", ".join(
                str(value)
                for value in issue_list
            )
            if issue_list
            else str(
                item.get(
                    "reason",
                    "score",
                )
            )
        )

        print(
            f"- {item.get('symbol', '?'):<6} "
            f"{item.get('status', '?'):<9} "
            f"score "
            f"{item.get('score', '?')}/"
            f"{item.get('min_score', market_config.get('min_score', '?'))}"
            f" | thiếu: {issue_text}"
        )
