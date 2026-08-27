from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import requests
from requests import Session

from services.telegram_bot.analyst import GeminiAnalyst
from services.telegram_bot.formatter import (
    build_symbol_analysis_message,
    build_symbol_help_message,
)
from services.telegram_client import TelegramClient, TelegramConfig

logger = logging.getLogger("QuantBot.telegram_query")
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")


@dataclass(slots=True)
class TelegramQueryBot:
    config: TelegramConfig
    session: Session
    poll_timeout_seconds: int = 25
    analyst: GeminiAnalyst | None = None

    @classmethod
    def from_env(cls) -> "TelegramQueryBot":
        session = requests.Session()
        return cls(
            config=TelegramConfig.from_env(),
            session=session,
            analyst=GeminiAnalyst.from_env(session=session),
        )

    def run_forever(self) -> None:
        from strategy.scanner import get_all_symbols

        allowed_symbols = {str(item).upper() for item in get_all_symbols()}
        offset: int | None = None
        logger.info("Telegram query bot started with %s symbols.", len(allowed_symbols))

        while True:
            try:
                updates = self._get_updates(offset=offset)
                for update in updates:
                    update_id = update.get("update_id")
                    if update_id is not None:
                        offset = int(update_id) + 1
                    self._handle_update(update, allowed_symbols=allowed_symbols)
            except KeyboardInterrupt:
                raise
            except Exception:
                logger.exception("Telegram query loop failed; retrying.")
                time.sleep(2.0)

    def _get_updates(self, *, offset: int | None) -> list[dict[str, Any]]:
        url = f"https://api.telegram.org/bot{self.config.token}/getUpdates"
        params: dict[str, Any] = {
            "timeout": self.poll_timeout_seconds,
            "allowed_updates": '["message"]',
        }
        if offset is not None:
            params["offset"] = offset

        response = self.session.get(
            url,
            params=params,
            timeout=self.poll_timeout_seconds + 10,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(payload.get("description", "Telegram getUpdates failed."))
        result = payload.get("result", [])
        return result if isinstance(result, list) else []

    def _handle_update(self, update: dict[str, Any], *, allowed_symbols: set[str]) -> None:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = str(message.get("text", "")).strip()

        # Private by default: ignore messages from any chat other than CHAT_ID.
        if not chat_id or chat_id != str(self.config.chat_id):
            logger.warning("Ignored unauthorized Telegram chat_id=%s", chat_id or "-")
            return

        if not text:
            return

        symbol = parse_symbol_request(text)
        if symbol is None:
            self._send_to_chat(chat_id, build_symbol_help_message())
            return

        if symbol not in allowed_symbols:
            self._send_to_chat(
                chat_id,
                f"❌ Không tìm thấy <b>{symbol}</b> trong market database.\nDùng mã đang có dữ liệu, ví dụ <code>FPT</code>.",
            )
            return

        from strategy.market_regime import get_market_regime
        from strategy.scanner import evaluate_symbol

        market_config = get_market_regime()
        evaluation = evaluate_symbol(
            symbol,
            market_config=market_config,
        )
        ai_analysis = None
        ai_error = None
        if self.analyst is not None:
            result = self.analyst.analyze(
                evaluation,
                market_config=market_config,
            )
            ai_analysis = result.analysis
            if result.error not in {None, "disabled"}:
                ai_error = result.error

        reply = build_symbol_analysis_message(
            evaluation,
            market_config=market_config,
            ai_analysis=ai_analysis,
            ai_error=ai_error,
        )
        self._send_to_chat(chat_id, reply)

    def _send_to_chat(self, chat_id: str, message: str) -> None:
        # Reuse the production send client while preserving the incoming chat target.
        config = TelegramConfig(
            token=self.config.token,
            chat_id=chat_id,
            parse_mode=self.config.parse_mode,
            timeout_seconds=self.config.timeout_seconds,
            maximum_message_length=self.config.maximum_message_length,
            maximum_attempts=self.config.maximum_attempts,
            backoff_seconds=self.config.backoff_seconds,
        )
        result = TelegramClient(config, session=self.session).send_message(message)
        if not result.success:
            raise RuntimeError(result.error or "Không gửi được Telegram reply.")


def parse_symbol_request(text: str) -> str | None:
    value = str(text or "").strip().upper()
    if not value:
        return None

    if value in {"/START", "/HELP", "HELP"}:
        return None

    if value.startswith("/CHECK"):
        parts = value.split()
        if len(parts) != 2:
            return None
        value = parts[1]

    # Telegram may append @botname to commands, but not to plain symbols.
    if "@" in value:
        return None

    return value if _SYMBOL_RE.fullmatch(value) else None
