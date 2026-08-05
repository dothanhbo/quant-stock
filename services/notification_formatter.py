from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from typing import Any


def escape_telegram_html(
    value: Any,
) -> str:
    return html.escape(
        str(value),
        quote=False,
    )


def format_price(
    price: Any,
) -> str:
    """Convert VCI's thousand-VND price into a display string."""
    try:
        price_vnd = float(price) * 1000
    except (TypeError, ValueError):
        return "-"

    return f"{price_vnd:,.0f} VNĐ"


def build_scan_message(
    signals: Sequence[Mapping[str, Any]],
    *,
    top_n: int = 10,
    watchlist: Sequence[
        Mapping[str, Any]
    ] | None = None,
    market_config: Mapping[
        str,
        Any,
    ] | None = None,
) -> str:
    """Build a Telegram HTML message for the daily VN100 scan."""
    watchlist_items = list(
        watchlist or []
    )
    market = dict(
        market_config or {}
    )
    regime = escape_telegram_html(
        market.get(
            "regime",
            "UNKNOWN",
        )
    )

    lines = [
        "📊 <b>KẾT QUẢ QUÉT VN100</b>",
        f"Thị trường: <b>{regime}</b>",
        "",
    ]

    if signals:
        lines.append(
            "🚀 <b>TOP TÍN HIỆU</b>"
        )

        for index, signal in enumerate(
            signals[:top_n],
            start=1,
        ):
            symbol = escape_telegram_html(
                signal.get(
                    "symbol",
                    "-",
                )
            )
            breakout = (
                "Có"
                if signal.get(
                    "breakout_20d",
                    False,
                )
                else "Chưa"
            )

            lines.extend(
                [
                    (
                        f"<b>{index}. {symbol}</b> — "
                        f"{_number(signal.get('score'), 0)}/100"
                    ),
                    (
                        "💪 RS20: "
                        f"<code>{_signed_number(signal.get('relative_strength_20d'), 2)}%</code>"
                    ),
                    (
                        "💰 Entry: "
                        f"<code>{escape_telegram_html(format_price(signal.get('entry')))}</code>"
                    ),
                    (
                        "📈 RSI: "
                        f"<code>{_number(signal.get('rsi'), 1)}</code> | "
                        "ADX: "
                        f"<code>{_number(signal.get('adx'), 1)}</code>"
                    ),
                    (
                        "📊 Volume: "
                        f"<code>{_number(signal.get('volume_ratio'), 2)}x MA20</code>"
                    ),
                    (
                        "⚡ Breakout 20 phiên: "
                        f"<code>{breakout}</code>"
                    ),
                    (
                        "🛑 SL: "
                        f"<code>{escape_telegram_html(format_price(signal.get('stop_loss')))}</code> "
                        f"({_number(signal.get('stop_loss_pct'), 2)}%)"
                    ),
                    (
                        "🎯 TP: "
                        f"<code>{escape_telegram_html(format_price(signal.get('take_profit')))}</code> "
                        f"(+{_number(signal.get('take_profit_pct'), 2)}%)"
                    ),
                    "",
                ]
            )
    else:
        lines.extend(
            [
                "Không có mã nào đạt toàn bộ điều kiện hôm nay.",
                "",
            ]
        )

    if watchlist_items:
        lines.append(
            "🟡 <b>WATCHLIST GẦN ĐẠT</b>"
        )

        for index, item in enumerate(
            watchlist_items[:5],
            start=1,
        ):
            symbol = escape_telegram_html(
                item.get(
                    "symbol",
                    "-",
                )
            )
            missing = escape_telegram_html(
                ", ".join(
                    str(value)
                    for value in item.get(
                        "missing",
                        [],
                    )
                )
                or "-"
            )

            lines.append(
                f"{index}. <b>{symbol}</b> — "
                f"{_number(item.get('score'), 0)}/"
                f"{_number(item.get('min_score'), 0)} | "
                "RS "
                f"<code>{_signed_number(item.get('relative_strength_20d'), 2)}%</code> | "
                f"thiếu: <code>{missing}</code>"
            )

    lines.extend(
        [
            "",
            (
                f"✅ Tín hiệu: <b>{len(signals)}</b> | "
                f"Watchlist: <b>{len(watchlist_items)}</b>"
            ),
        ]
    )

    return "\n".join(lines)


def _number(
    value: Any,
    decimals: int,
) -> str:
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


def _signed_number(
    value: Any,
    decimals: int,
) -> str:
    try:
        return f"{float(value):+.{decimals}f}"
    except (TypeError, ValueError):
        return "-"
