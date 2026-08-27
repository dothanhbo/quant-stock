from services.telegram_bot.formatter import build_symbol_analysis_message


def test_build_symbol_analysis_message_for_passed_signal() -> None:
    evaluation = {
        "symbol": "FPT",
        "status": "PASSED",
        "score": 82,
        "min_score": 60,
        "date": "2026-08-27",
        "entry": 100000,
        "stop_loss": 95000,
        "take_profit": 112000,
        "relative_strength_20d": 6.5,
        "rsi": 58.2,
        "adx": 27.4,
        "volume_ratio": 1.4,
        "atr": 2100,
        "atr_percent": 2.1,
        "ema10": 99000,
        "ema20": 97000,
        "ema50": 93000,
        "breakout_20d": True,
        "conditions": {
            "trend_context": True,
            "donchian_passed": True,
            "breakout_20d": True,
            "hybrid_score": True,
        },
        "failed_conditions": [],
    }
    message = build_symbol_analysis_message(
        evaluation,
        market_config={"regime": "SIDEWAY"},
    )
    assert "FPT — QUICK ANALYSIS" in message
    assert "PASSED" in message
    assert "82/100" in message
    assert "Donchian" in message
    assert "QUALIFIED" in message


def test_build_symbol_analysis_message_for_missing_data() -> None:
    message = build_symbol_analysis_message(
        {"symbol": "ABC", "status": "REJECTED", "reason": "insufficient_data"},
        market_config={"regime": "SIDEWAY"},
    )
    assert "REJECTED" in message
    assert "Không đủ dữ liệu" in message


def test_formatter_appends_ai_analysis() -> None:
    evaluation = {
        "symbol": "VIC", "status": "WATCHLIST", "score": 54, "min_score": 60,
        "date": "2026-08-26", "entry": 230, "stop_loss": 214.99,
        "take_profit": 250, "relative_strength_20d": -2.01, "rsi": 65.5,
        "adx": 17.2, "volume_ratio": 1.79, "atr": 7.5, "atr_percent": 3.26,
        "ema10": 213.01, "ema20": 212.03, "ema50": 211.23,
        "conditions": {"trend_context": True, "donchian_passed": False},
        "failed_conditions": ["donchian_passed"],
    }
    ai = {
        "short_term": {"bias": "WAIT", "reason": "Chưa xác nhận entry."},
        "medium_term": {"bias": "WATCH", "reason": "Theo dõi cấu trúc trend."},
        "long_term": {"bias": "INSUFFICIENT_DATA", "reason": "Thiếu fundamental."},
        "entry_quality": {"rating": "UNCONFIRMED", "reason": "Chưa đủ điều kiện."},
        "risk_level": {"rating": "MEDIUM", "reason": "ATR ở mức vừa."},
        "quant_comparison": {"quant": "WATCHLIST", "ai": "WAIT", "stance": "MORE_BEARISH"},
        "summary": "Chưa nên mua đuổi; chờ xác nhận thêm.",
    }
    message = build_symbol_analysis_message(
        evaluation, market_config={"regime": "SIDEWAY"}, ai_analysis=ai
    )
    assert "AI ANALYST — INDEPENDENT VIEW" in message
    assert "Ngắn hạn" in message
    assert "WAIT" in message
    assert "Entry quality" in message
    assert "INSUFFICIENT_DATA" in message


def test_formatter_shows_quant_comparison() -> None:
    evaluation = {"symbol": "FPT", "status": "WATCHLIST", "score": 58, "min_score": 60, "entry": 100, "conditions": {}}
    ai = {
        "short_term": {"bias": "POSSIBLE_ENTRY", "reason": "Momentum đang cải thiện."},
        "medium_term": {"bias": "WATCH", "reason": "Cần thêm xác nhận."},
        "long_term": {"bias": "INSUFFICIENT_DATA", "reason": "Thiếu fundamental."},
        "entry_quality": {"rating": "FAIR", "reason": "Có thể theo dõi timing."},
        "risk_level": {"rating": "MEDIUM", "reason": "Rủi ro vừa."},
        "quant_comparison": {"quant": "WATCHLIST", "ai": "POSSIBLE_ENTRY", "stance": "MORE_BULLISH"},
        "summary": "AI tích cực hơn Quant nhưng đây không phải Quant entry.",
    }
    message = build_symbol_analysis_message(evaluation, market_config={"regime": "SIDEWAY"}, ai_analysis=ai)
    assert "AI vs QUANT" in message
    assert "AI MORE BULLISH" in message
    assert "Independent summary" in message
