import json

from services.telegram_bot.analyst import (
    AIAnalystConfig,
    GeminiAnalyst,
    _AI_RESPONSE_SCHEMA,
    _extract_output_text,
    _validate_and_normalize,
)


def _ai_payload(short_bias="QUALIFIED"):
    return {
        "short_term": {"bias": short_bias, "reason": "Momentum có tín hiệu tích cực."},
        "medium_term": {"bias": "WATCH", "reason": "Cần duy trì cấu trúc xu hướng."},
        "long_term": {"bias": "INSUFFICIENT_DATA", "reason": "Không có dữ liệu fundamental."},
        "entry_quality": {"rating": "GOOD", "reason": "Timing kỹ thuật tương đối tốt."},
        "risk_level": {"rating": "MEDIUM", "reason": "ATR cho thấy biến động trung bình."},
        "summary": "Setup đáng theo dõi độc lập với rule cơ học của Quant.",
    }


def test_extract_output_text_from_gemini_shape() -> None:
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": json.dumps(_ai_payload(), ensure_ascii=False)}
                    ]
                }
            }
        ]
    }
    assert "short_term" in _extract_output_text(payload)


def test_watchlist_can_be_more_bullish_than_quant() -> None:
    normalized = _validate_and_normalize(
        _ai_payload(short_bias="QUALIFIED"),
        evaluation={"status": "WATCHLIST"},
    )
    assert normalized["short_term"]["bias"] == "QUALIFIED"
    assert normalized["entry_quality"]["rating"] == "GOOD"
    assert normalized["quant_comparison"]["stance"] == "MORE_BULLISH"


def test_passed_can_be_more_bearish_than_quant() -> None:
    normalized = _validate_and_normalize(
        _ai_payload(short_bias="WAIT"),
        evaluation={"status": "PASSED"},
    )
    assert normalized["short_term"]["bias"] == "WAIT"
    assert normalized["quant_comparison"]["stance"] == "MORE_BEARISH"


class _FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": json.dumps(_ai_payload(short_bias="WATCH"), ensure_ascii=False)}
                        ]
                    }
                }
            ]
        }


class _FakeSession:
    def post(self, *args, **kwargs):
        return _FakeResponse()


def test_gemini_analyst_success() -> None:
    analyst = GeminiAnalyst(
        AIAnalystConfig(
            enabled=True,
            api_key="test-key",
            model="gemini-3.6-flash",
        ),
        session=_FakeSession(),
    )
    result = analyst.analyze(
        {"symbol": "FPT", "status": "WATCHLIST", "score": 70, "min_score": 60},
        market_config={"regime": "SIDEWAY"},
    )
    assert result.success
    assert result.analysis["short_term"]["bias"] == "WATCH"
    assert result.analysis["quant_comparison"]["stance"] == "AGREE"


def test_structured_schema_limits_string_lengths() -> None:
    props = _AI_RESPONSE_SCHEMA["properties"]
    assert props["short_term"]["properties"]["reason"]["maxLength"] == 220
    assert props["medium_term"]["properties"]["reason"]["maxLength"] == 220
    assert props["long_term"]["properties"]["reason"]["maxLength"] == 180
    assert props["entry_quality"]["properties"]["reason"]["maxLength"] == 220
    assert props["risk_level"]["properties"]["reason"]["maxLength"] == 180
    assert props["summary"]["maxLength"] == 320


def test_normalizer_truncates_overlong_text_as_final_guard() -> None:
    payload = _ai_payload(short_bias="WATCH")
    payload["short_term"]["reason"] = "x" * 500
    payload["long_term"]["reason"] = "y" * 500
    payload["risk_level"]["reason"] = "z" * 500
    payload["summary"] = "s" * 700
    normalized = _validate_and_normalize(payload, evaluation={"status": "WATCHLIST"})
    assert len(normalized["short_term"]["reason"]) == 220
    assert len(normalized["long_term"]["reason"]) == 180
    assert len(normalized["risk_level"]["reason"]) == 180
    assert len(normalized["summary"]) == 320
