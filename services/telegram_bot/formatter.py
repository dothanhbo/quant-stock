from __future__ import annotations

from html import escape
from typing import Any


_CONDITION_LABELS = {
    "trend_context": "Trend context",
    "trend_passed": "Trend",
    "donchian_passed": "Donchian",
    "breakout_20d": "Breakout 20 phiên",
    "hybrid_score": "Hybrid score",
}

_REASON_LABELS = {
    "insufficient_data": "Không đủ dữ liệu lịch sử",
    "indicator_nan": "Indicator chưa đủ dữ liệu",
    "relative_strength_data": "Không tính được Relative Strength",
    "stale_data": "Dữ liệu mã chưa cập nhật tới phiên chuẩn",
    "invalid_date": "Ngày dữ liệu không hợp lệ",
    "invalid_reference_date": "Ngày tham chiếu không hợp lệ",
    "other": "Không đủ dữ liệu để đánh giá",
}


def build_symbol_analysis_message(
    evaluation: dict[str, Any],
    *,
    market_config: dict[str, Any],
    ai_analysis: dict[str, Any] | None = None,
    ai_error: str | None = None,
) -> str:
    """Format a read-only, single-symbol strategy evaluation for Telegram."""
    symbol = escape(str(evaluation.get("symbol", "-")).upper())
    status = str(evaluation.get("status", "REJECTED")).upper()
    status_icon = {
        "PASSED": "🟢",
        "WATCHLIST": "🟡",
        "REJECTED": "🔴",
    }.get(status, "⚪")

    if "score" not in evaluation:
        reason = _REASON_LABELS.get(
            str(evaluation.get("reason", "other")),
            str(evaluation.get("reason", "Không xác định")),
        )
        return "\n".join(
            [
                f"📊 <b>{symbol} — QUICK ANALYSIS</b>",
                "",
                f"{status_icon} <b>{status}</b>",
                f"Lý do: {escape(reason)}",
            ]
        )

    regime = escape(str(market_config.get("regime", evaluation.get("regime", "UNKNOWN"))))
    score = _number(evaluation.get("score"), 0)
    min_score = _number(evaluation.get("min_score"), 0)
    date = escape(str(evaluation.get("date", market_config.get("date", "-"))))

    lines = [
        f"📊 <b>{symbol} — QUICK ANALYSIS</b>",
        f"📅 Phiên dữ liệu: <code>{date}</code>",
        f"🌐 Regime: <b>{regime}</b>",
        "",
        f"{status_icon} <b>{status}</b> — Score <b>{score}/100</b> | ngưỡng <b>{min_score}</b>",
        "",
        "<b>Chỉ báo chính</b>",
        f"• Giá: <code>{_price(evaluation.get('entry'))}</code>",
        f"• RS20: <code>{_signed(evaluation.get('relative_strength_20d'), 2)}%</code>",
        f"• RSI: <code>{_number(evaluation.get('rsi'), 1)}</code>",
        f"• ADX: <code>{_number(evaluation.get('adx'), 1)}</code>",
        f"• Volume: <code>{_number(evaluation.get('volume_ratio'), 2)}x MA20</code>",
        f"• ATR: <code>{_number(evaluation.get('atr'), 2)}</code> ({_number(evaluation.get('atr_percent'), 2)}%)",
        f"• EMA10/20/50: <code>{_price(evaluation.get('ema10'))} / {_price(evaluation.get('ema20'))} / {_price(evaluation.get('ema50'))}</code>",
        "",
        "<b>Điều kiện strategy</b>",
    ]

    conditions = evaluation.get("conditions") or {}
    if conditions:
        for key in _ordered_condition_keys(conditions):
            label = _CONDITION_LABELS.get(key, key)
            icon = "✅" if bool(conditions.get(key)) else "❌"
            lines.append(f"{icon} {escape(label)}")
    else:
        lines.append("• Không có condition chi tiết.")

    lines.extend(
        [
            "",
            "<b>Risk levels</b>",
            f"• Entry: <code>{_price(evaluation.get('entry'))}</code>",
            f"• Stop loss: <code>{_price(evaluation.get('stop_loss'))}</code>",
            f"• Take profit: <code>{_price(evaluation.get('take_profit'))}</code>",
        ]
    )

    failed = evaluation.get("failed_conditions") or []
    if failed:
        missing = ", ".join(_CONDITION_LABELS.get(str(item), str(item)) for item in failed)
        lines.extend(["", f"⚠️ Thiếu: <code>{escape(missing)}</code>"])

    lines.extend(["", f"🧮 <b>Quant conclusion:</b> {_assessment(evaluation, market_config)}"])

    if ai_analysis:
        lines.extend(_format_ai_analysis(ai_analysis))
    elif ai_error:
        lines.extend(["", f"🤖 <b>AI Analyst:</b> ⚠️ {escape(str(ai_error))}. Quant analysis phía trên vẫn hợp lệ."])

    return "\n".join(lines)


def build_symbol_help_message() -> str:
    return "\n".join(
        [
            "🤖 <b>Quant Stock Query Bot</b>",
            "",
            "Gửi một mã để xem đánh giá strategy hiện tại:",
            "<code>FPT</code>",
            "hoặc <code>/check FPT</code>",
            "",
            "Bot chỉ phân tích, không tạo lệnh BUY/SELL.",
        ]
    )


def _assessment(evaluation: dict[str, Any], market_config: dict[str, Any]) -> str:
    status = str(evaluation.get("status", "REJECTED")).upper()
    regime = str(market_config.get("regime", "UNKNOWN")).upper()
    breakout = bool(evaluation.get("breakout_20d"))
    trend_context = bool((evaluation.get("conditions") or {}).get("trend_context"))

    if status == "PASSED":
        text = "Đạt toàn bộ điều kiện entry của strategy hiện tại. Đây là tín hiệu QUALIFIED; việc có được chọn mua còn phụ thuộc ranking, giới hạn vị thế và risk policy của portfolio."
    elif status == "WATCHLIST":
        text = "Đang ở WATCHLIST: tín hiệu gần đạt nhưng vẫn thiếu ít nhất một điều kiện bắt buộc. Chưa phải entry hợp lệ."
    elif trend_context and not breakout:
        text = "Bối cảnh xu hướng chấp nhận được nhưng chưa có breakout 20 phiên hợp lệ. Chưa mua theo strategy hiện tại."
    else:
        text = "Chưa đạt cấu trúc entry của strategy hiện tại. Không có tín hiệu mua."

    if regime == "BEAR":
        text += " Regime hiện là BEAR nên production policy không mở vị thế mới."
    return escape(text)


def _ordered_condition_keys(conditions: dict[str, Any]) -> list[str]:
    preferred = ["trend_context", "trend_passed", "donchian_passed", "breakout_20d", "hybrid_score"]
    result = [key for key in preferred if key in conditions]
    result.extend(key for key in conditions if key not in result)
    return result


def _number(value: Any, decimals: int) -> str:
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


def _signed(value: Any, decimals: int) -> str:
    try:
        return f"{float(value):+.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


def _price(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if number.is_integer():
        return f"{number:,.0f}"
    return f"{number:,.2f}"


def _format_ai_analysis(ai: dict[str, Any]) -> list[str]:
    short = ai.get("short_term") or {}
    medium = ai.get("medium_term") or {}
    long_term = ai.get("long_term") or {}
    entry = ai.get("entry_quality") or {}
    risk = ai.get("risk_level") or {}
    comparison = ai.get("quant_comparison") or {}
    summary = escape(str(ai.get("summary", "-")).strip())
    stance = str(comparison.get("stance", "-")).upper()
    stance_icon = {
        "AGREE": "🤝",
        "MORE_BULLISH": "📈",
        "MORE_BEARISH": "📉",
    }.get(stance, "⚖️")
    stance_label = {
        "AGREE": "AGREE",
        "MORE_BULLISH": "AI MORE BULLISH",
        "MORE_BEARISH": "AI MORE BEARISH",
    }.get(stance, stance)

    return [
        "",
        "🤖 <b>AI ANALYST — INDEPENDENT VIEW</b>",
        f"• Ngắn hạn: <b>{escape(str(short.get('bias', '-')))}</b> — {escape(str(short.get('reason', '-')))}",
        f"• Trung hạn: <b>{escape(str(medium.get('bias', '-')))}</b> — {escape(str(medium.get('reason', '-')))}",
        f"• Dài hạn: <b>{escape(str(long_term.get('bias', '-')))}</b> — {escape(str(long_term.get('reason', '-')))}",
        f"• Entry quality: <b>{escape(str(entry.get('rating', '-')))}</b> — {escape(str(entry.get('reason', '-')))}",
        f"• Risk: <b>{escape(str(risk.get('rating', '-')))}</b> — {escape(str(risk.get('reason', '-')))}",
        "",
        f"{stance_icon} <b>AI vs QUANT:</b> {escape(str(comparison.get('quant', '-')))} vs {escape(str(comparison.get('ai', '-')))} — <b>{escape(stance_label)}</b>",
        "",
        f"💬 <b>Independent summary:</b> {summary}",
    ]
