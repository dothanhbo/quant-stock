import os
import requests
from dotenv import load_dotenv


load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")


def format_price(price):
    """
    Dữ liệu VCI thường ở đơn vị nghìn đồng.
    Ví dụ 20.9 -> 20,900 VNĐ.
    """
    price_vnd = price * 1000

    return f"{price_vnd:,.0f} VNĐ"


def build_scan_message(signals, top_n=10, watchlist=None, market_config=None):
    """Tạo thông báo Telegram gồm Top tín hiệu và watchlist."""
    watchlist = watchlist or []
    market_config = market_config or {}
    regime = market_config.get("regime", "UNKNOWN")

    lines = [
        "📊 *KẾT QUẢ QUÉT VN100*",
        f"Thị trường: *{regime}*",
        "",
    ]

    if signals:
        lines.append("🚀 *TOP TÍN HIỆU*")
        for index, signal in enumerate(signals[:top_n], start=1):
            breakout = "Có" if signal["breakout_20d"] else "Chưa"
            lines.extend([
                f"*{index}. {signal['symbol']}* — {signal['score']}/100",
                f"💪 RS20: `{signal.get('relative_strength_20d', 0):+.2f}%`",
                f"💰 Entry: `{format_price(signal['entry'])}`",
                f"📈 RSI: `{signal['rsi']:.1f}` | ADX: `{signal['adx']:.1f}`",
                f"📊 Volume: `{signal['volume_ratio']:.2f}x MA20`",
                f"⚡ Breakout 20 phiên: `{breakout}`",
                f"🛑 SL: `{format_price(signal['stop_loss'])}` ({signal['stop_loss_pct']:.2f}%)",
                f"🎯 TP: `{format_price(signal['take_profit'])}` (+{signal['take_profit_pct']:.2f}%)",
                "",
            ])
    else:
        lines.extend(["Không có mã nào đạt toàn bộ điều kiện hôm nay.", ""])

    if watchlist:
        lines.append("🟡 *WATCHLIST GẦN ĐẠT*")
        for index, item in enumerate(watchlist[:5], start=1):
            missing = ", ".join(item.get("missing", []))
            lines.append(
                f"{index}. *{item['symbol']}* — {item['score']}/{item['min_score']} | "
                f"RS `{item.get('relative_strength_20d', 0):+.2f}%` | thiếu: `{missing}`"
            )

    lines.extend([
        "",
        f"✅ Tín hiệu: *{len(signals)}* | Watchlist: *{len(watchlist)}*",
    ])
    return "\n".join(lines)


def send_telegram(message):
    if not TELEGRAM_TOKEN:
        raise ValueError(
            "Thiếu TELEGRAM_TOKEN trong file .env"
        )

    if not CHAT_ID:
        raise ValueError(
            "Thiếu CHAT_ID trong file .env"
        )

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }

    response = requests.post(
        url,
        json=payload,
        timeout=20
    )

    if not response.ok:
        raise RuntimeError(
            f"Telegram lỗi {response.status_code}: "
            f"{response.text}"
        )

    return True