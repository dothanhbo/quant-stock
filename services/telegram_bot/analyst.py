from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import requests
from requests import Session

from services.telegram_bot.prompt import SYSTEM_PROMPT, build_analysis_input

logger = logging.getLogger("QuantBot.ai_analyst")

_ALLOWED_BIASES = {"AVOID", "WAIT", "WATCH", "POSSIBLE_ENTRY", "QUALIFIED"}
_ALLOWED_LONG_TERM = {"INSUFFICIENT_DATA", "WATCH"}
_ALLOWED_ENTRY = {"POOR", "UNCONFIRMED", "FAIR", "GOOD", "LATE"}
_ALLOWED_RISK = {"LOW", "MEDIUM", "HIGH"}

_REASON_MAX_CHARS = 220
_LONG_TERM_REASON_MAX_CHARS = 180
_RISK_REASON_MAX_CHARS = 180
_SUMMARY_MAX_CHARS = 320

_AI_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "short_term": {
            "type": "object",
            "properties": {
                "bias": {"type": "string", "enum": sorted(_ALLOWED_BIASES)},
                "reason": {"type": "string", "maxLength": _REASON_MAX_CHARS},
            },
            "required": ["bias", "reason"],
            "additionalProperties": False,
        },
        "medium_term": {
            "type": "object",
            "properties": {
                "bias": {"type": "string", "enum": sorted(_ALLOWED_BIASES)},
                "reason": {"type": "string", "maxLength": _REASON_MAX_CHARS},
            },
            "required": ["bias", "reason"],
            "additionalProperties": False,
        },
        "long_term": {
            "type": "object",
            "properties": {
                "bias": {"type": "string", "enum": sorted(_ALLOWED_LONG_TERM)},
                "reason": {"type": "string", "maxLength": _LONG_TERM_REASON_MAX_CHARS},
            },
            "required": ["bias", "reason"],
            "additionalProperties": False,
        },
        "entry_quality": {
            "type": "object",
            "properties": {
                "rating": {"type": "string", "enum": sorted(_ALLOWED_ENTRY)},
                "reason": {"type": "string", "maxLength": _REASON_MAX_CHARS},
            },
            "required": ["rating", "reason"],
            "additionalProperties": False,
        },
        "risk_level": {
            "type": "object",
            "properties": {
                "rating": {"type": "string", "enum": sorted(_ALLOWED_RISK)},
                "reason": {"type": "string", "maxLength": _RISK_REASON_MAX_CHARS},
            },
            "required": ["rating", "reason"],
            "additionalProperties": False,
        },
        "summary": {"type": "string", "maxLength": _SUMMARY_MAX_CHARS},
    },
    "required": [
        "short_term",
        "medium_term",
        "long_term",
        "entry_quality",
        "risk_level",
        "summary",
    ],
    "additionalProperties": False,
}


@dataclass(slots=True, frozen=True)
class AIAnalystConfig:
    enabled: bool
    api_key: str
    model: str
    timeout_seconds: float = 12.0
    endpoint_base: str = "https://generativelanguage.googleapis.com/v1beta/models"
    max_output_tokens: int = 650

    @classmethod
    def from_env(cls) -> "AIAnalystConfig":
        enabled = _env_bool("AI_ANALYSIS_ENABLED", default=False)
        return cls(
            enabled=enabled,
            api_key=os.getenv("GEMINI_API_KEY", "").strip(),
            model=os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip() or "gemini-3.6-flash",
            timeout_seconds=float(os.getenv("AI_ANALYSIS_TIMEOUT_SECONDS", "30")),
            endpoint_base=os.getenv(
                "GEMINI_API_BASE_URL",
                "https://generativelanguage.googleapis.com/v1beta/models",
            ).strip().rstrip("/"),
            max_output_tokens=int(os.getenv("AI_ANALYSIS_MAX_OUTPUT_TOKENS", "1200")),
        )


@dataclass(slots=True, frozen=True)
class AIAnalysisResult:
    analysis: dict[str, Any] | None
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.analysis is not None and not self.error


class GeminiAnalyst:
    """Read-only Gemini interpretation layer. Never writes orders or changes Quant output."""

    def __init__(self, config: AIAnalystConfig, *, session: Session | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()

    @classmethod
    def from_env(cls, *, session: Session | None = None) -> "GeminiAnalyst":
        return cls(AIAnalystConfig.from_env(), session=session)

    def analyze(
        self,
        evaluation: dict[str, Any],
        *,
        market_config: dict[str, Any],
    ) -> AIAnalysisResult:
        if not self.config.enabled:
            return AIAnalysisResult(None, "disabled")
        if not self.config.api_key:
            return AIAnalysisResult(None, "GEMINI_API_KEY chưa được cấu hình")
        if "score" not in evaluation:
            return AIAnalysisResult(None, "Quant Engine chưa có đủ dữ liệu để gửi AI")

        endpoint = f"{self.config.endpoint_base}/{self.config.model}:generateContent"
        payload = {
            "system_instruction": {
                "parts": [{"text": SYSTEM_PROMPT}],
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": build_analysis_input(
                                evaluation,
                                market_config=market_config,
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": _AI_RESPONSE_SCHEMA,
                "maxOutputTokens": self.config.max_output_tokens,
                "temperature": 0.2,
                "thinkingConfig": {
                    "thinkingLevel": "minimal"
                },
            },
        }
        headers = {
            "x-goog-api-key": self.config.api_key,
            "Content-Type": "application/json",
        }

        try:
            response = self.session.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            response_payload = response.json()
            raw = _extract_output_text(response_payload)
            try:
                parsed = _parse_json_object(raw)
                normalized = _validate_and_normalize(parsed, evaluation=evaluation)
                return AIAnalysisResult(normalized)
            except (ValueError, TypeError, KeyError) as exc:
                logger.warning(
                    "Gemini analyst invalid structured output: %s | finishReason=%s | raw=%r",
                    exc,
                    _extract_finish_reason(response_payload),
                    raw[:800],
                )
                return AIAnalysisResult(None, "AI trả về dữ liệu không hợp lệ")
        except requests.Timeout:
            logger.warning("Gemini analyst timed out after %.1fs", self.config.timeout_seconds)
            return AIAnalysisResult(None, "AI timeout")
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            detail = _safe_error_detail(exc.response)
            logger.warning("Gemini analyst HTTP %s: %s", status, detail or exc)
            return AIAnalysisResult(None, f"Gemini API lỗi HTTP {status}")
        except requests.RequestException as exc:
            logger.warning("Gemini analyst request failed: %s", exc)
            return AIAnalysisResult(None, "AI API không khả dụng")
        except (ValueError, TypeError, KeyError) as exc:
            logger.warning("Gemini analyst response parsing failed: %s", exc)
            return AIAnalysisResult(None, "AI trả về dữ liệu không hợp lệ")




def _extract_finish_reason(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates or not isinstance(candidates[0], dict):
        return "UNKNOWN"
    return str(candidates[0].get("finishReason", "UNKNOWN"))

def _safe_error_detail(response: requests.Response | None) -> str:
    if response is None:
        return ""
    try:
        payload = response.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            message = str(error.get("message", "")).strip()
            return message[:500]
    except (ValueError, TypeError):
        pass
    return str(getattr(response, "text", ""))[:500]


def _extract_output_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    texts: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content") or {}
        if not isinstance(content, dict):
            continue
        for part in content.get("parts") or []:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
    if texts:
        return "\n".join(texts)
    raise ValueError("Gemini response does not contain candidate text")


def _parse_json_object(text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("AI output is not a JSON object")
    return parsed


def _validate_and_normalize(
    data: dict[str, Any],
    *,
    evaluation: dict[str, Any],
) -> dict[str, Any]:
    short = _horizon(data.get("short_term"), allowed=_ALLOWED_BIASES, max_chars=_REASON_MAX_CHARS)
    medium = _horizon(data.get("medium_term"), allowed=_ALLOWED_BIASES, max_chars=_REASON_MAX_CHARS)
    long_term = _horizon(data.get("long_term"), allowed=_ALLOWED_LONG_TERM, max_chars=_LONG_TERM_REASON_MAX_CHARS)
    entry = _rating(data.get("entry_quality"), allowed=_ALLOWED_ENTRY, max_chars=_REASON_MAX_CHARS)
    risk = _rating(data.get("risk_level"), allowed=_ALLOWED_RISK, max_chars=_RISK_REASON_MAX_CHARS)
    summary = _clean_text(data.get("summary"), max_chars=_SUMMARY_MAX_CHARS)

    # Important: do NOT cap AI opinion to Quant status. The whole point of this
    # layer is an independent second opinion. We only compare the two views.
    comparison = _compare_ai_with_quant(
        quant_status=str(evaluation.get("status", "REJECTED")).upper(),
        ai_bias=short["bias"],
    )

    return {
        "short_term": short,
        "medium_term": medium,
        "long_term": long_term,
        "entry_quality": entry,
        "risk_level": risk,
        "quant_comparison": comparison,
        "summary": summary,
    }


def _compare_ai_with_quant(*, quant_status: str, ai_bias: str) -> dict[str, str]:
    quant_rank = {
        "REJECTED": 0,
        "WATCHLIST": 2,
        "PASSED": 4,
    }.get(quant_status, 2)
    ai_rank = {
        "AVOID": 0,
        "WAIT": 1,
        "WATCH": 2,
        "POSSIBLE_ENTRY": 3,
        "QUALIFIED": 4,
    }[ai_bias]

    if ai_rank == quant_rank:
        stance = "AGREE"
    elif ai_rank > quant_rank:
        stance = "MORE_BULLISH"
    else:
        stance = "MORE_BEARISH"

    return {
        "quant": quant_status,
        "ai": ai_bias,
        "stance": stance,
    }

def _horizon(value: Any, *, allowed: set[str], max_chars: int = _REASON_MAX_CHARS) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("missing horizon object")
    bias = str(value.get("bias", "")).upper().strip()
    if bias not in allowed:
        raise ValueError(f"invalid bias: {bias}")
    return {"bias": bias, "reason": _clean_text(value.get("reason"), max_chars=max_chars)}


def _rating(value: Any, *, allowed: set[str], max_chars: int = _REASON_MAX_CHARS) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("missing rating object")
    rating = str(value.get("rating", "")).upper().strip()
    if rating not in allowed:
        raise ValueError(f"invalid rating: {rating}")
    return {"rating": rating, "reason": _clean_text(value.get("reason"), max_chars=max_chars)}


def _clean_text(value: Any, *, max_chars: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        raise ValueError("missing explanation text")
    return text[:max_chars]


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# Backward-compatible alias so older imports/tests do not break unexpectedly.
OpenAIAnalyst = GeminiAnalyst
