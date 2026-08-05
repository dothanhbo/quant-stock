from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import requests
from dotenv import load_dotenv
from requests import Response, Session

logger = logging.getLogger("QuantBot.telegram")


class TelegramConfigurationError(ValueError):
    """Raised when Telegram credentials are missing or invalid."""


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    token: str
    chat_id: str
    parse_mode: str = "HTML"
    timeout_seconds: float = 20.0
    maximum_message_length: int = 3900
    maximum_attempts: int = 4
    backoff_seconds: float = 1.0

    @classmethod
    def from_env(
        cls,
        *,
        env_file: str | None = None,
    ) -> TelegramConfig:
        load_dotenv(
            dotenv_path=env_file,
            override=False,
        )

        token = (
            os.getenv("TELEGRAM_TOKEN", "")
            .strip()
        )
        chat_id = (
            os.getenv("CHAT_ID", "")
            .strip()
        )

        if not token:
            raise TelegramConfigurationError(
                "Thiếu TELEGRAM_TOKEN trong môi trường."
            )

        if not chat_id:
            raise TelegramConfigurationError(
                "Thiếu CHAT_ID trong môi trường."
            )

        return cls(
            token=token,
            chat_id=chat_id,
        )


@dataclass(slots=True)
class TelegramSendResult:
    success: bool
    message_ids: list[int] = field(
        default_factory=list
    )
    chunks_sent: int = 0
    status_code: int | None = None
    error: str | None = None


class TelegramClient:
    """Small resilient client for Telegram Bot sendMessage API."""

    def __init__(
        self,
        config: TelegramConfig,
        *,
        session: Session | None = None,
    ) -> None:
        self.config = config
        self._session = (
            session
            or requests.Session()
        )
        self._send_message_url = (
            "https://api.telegram.org/"
            f"bot{config.token}/sendMessage"
        )

    @classmethod
    def from_env(
        cls,
        *,
        env_file: str | None = None,
        session: Session | None = None,
    ) -> TelegramClient:
        return cls(
            TelegramConfig.from_env(
                env_file=env_file,
            ),
            session=session,
        )

    def send_message(
        self,
        message: str,
        *,
        disable_web_page_preview: bool = True,
    ) -> TelegramSendResult:
        chunks = split_telegram_message(
            message,
            maximum_length=(
                self.config.maximum_message_length
            ),
        )

        if not chunks:
            return TelegramSendResult(
                success=False,
                error="Nội dung Telegram trống.",
            )

        message_ids: list[int] = []

        for chunk_index, chunk in enumerate(
            chunks,
            start=1,
        ):
            response = self._send_chunk(
                chunk,
                disable_web_page_preview=(
                    disable_web_page_preview
                ),
            )

            if not response.success:
                response.message_ids = message_ids
                response.chunks_sent = (
                    chunk_index - 1
                )
                return response

            message_ids.extend(
                response.message_ids
            )

        return TelegramSendResult(
            success=True,
            message_ids=message_ids,
            chunks_sent=len(chunks),
            status_code=200,
        )

    def _send_chunk(
        self,
        message: str,
        *,
        disable_web_page_preview: bool,
    ) -> TelegramSendResult:
        payload = {
            "chat_id": self.config.chat_id,
            "text": message,
            "parse_mode": (
                self.config.parse_mode
            ),
            "disable_web_page_preview": (
                disable_web_page_preview
            ),
        }

        last_error: str | None = None
        last_status_code: int | None = None

        for attempt in range(
            1,
            self.config.maximum_attempts + 1,
        ):
            try:
                response = self._session.post(
                    self._send_message_url,
                    json=payload,
                    timeout=(
                        self.config.timeout_seconds
                    ),
                )
            except requests.RequestException as exc:
                last_error = str(exc)

                if attempt >= (
                    self.config.maximum_attempts
                ):
                    break

                self._sleep_before_retry(
                    attempt=attempt,
                )
                continue

            last_status_code = (
                response.status_code
            )

            if response.ok:
                message_id = _extract_message_id(
                    response
                )
                return TelegramSendResult(
                    success=True,
                    message_ids=(
                        [message_id]
                        if message_id is not None
                        else []
                    ),
                    chunks_sent=1,
                    status_code=(
                        response.status_code
                    ),
                )

            last_error = _extract_error_message(
                response
            )

            if not _is_retryable_response(
                response
            ):
                break

            if attempt >= (
                self.config.maximum_attempts
            ):
                break

            retry_after = _extract_retry_after(
                response
            )
            self._sleep_before_retry(
                attempt=attempt,
                retry_after=retry_after,
            )

        logger.warning(
            "Telegram send failed: status=%s error=%s",
            last_status_code,
            last_error,
        )

        return TelegramSendResult(
            success=False,
            status_code=last_status_code,
            error=(
                last_error
                or "Không gửi được Telegram."
            ),
        )

    def _sleep_before_retry(
        self,
        *,
        attempt: int,
        retry_after: float | None = None,
    ) -> None:
        delay = (
            retry_after
            if retry_after is not None
            else (
                self.config.backoff_seconds
                * (2 ** (attempt - 1))
            )
        )

        logger.info(
            "Retry Telegram after %.2f seconds (attempt %s/%s)",
            delay,
            attempt + 1,
            self.config.maximum_attempts,
        )
        time.sleep(delay)


def split_telegram_message(
    message: str,
    *,
    maximum_length: int = 3900,
) -> list[str]:
    """Split a long message while preferring line boundaries."""
    text = str(message or "").strip()

    if not text:
        return []

    if maximum_length <= 0:
        raise ValueError(
            "maximum_length phải lớn hơn 0."
        )

    chunks: list[str] = []
    current_lines: list[str] = []
    current_length = 0

    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        if len(line) > maximum_length:
            if current_lines:
                chunks.append(
                    "\n".join(current_lines)
                )
                current_lines = []
                current_length = 0

            for start in range(
                0,
                len(line),
                maximum_length,
            ):
                chunks.append(
                    line[
                        start:
                        start + maximum_length
                    ]
                )
            continue

        additional_length = (
            len(line)
            + (1 if current_lines else 0)
        )

        if (
            current_lines
            and current_length
            + additional_length
            > maximum_length
        ):
            chunks.append(
                "\n".join(current_lines)
            )
            current_lines = [line]
            current_length = len(line)
            continue

        current_lines.append(line)
        current_length += additional_length

    if current_lines:
        chunks.append(
            "\n".join(current_lines)
        )

    return [
        chunk
        for chunk in chunks
        if chunk.strip()
    ]


def _is_retryable_response(
    response: Response,
) -> bool:
    return (
        response.status_code == 429
        or 500 <= response.status_code < 600
    )


def _extract_retry_after(
    response: Response,
) -> float | None:
    try:
        payload: dict[str, Any] = (
            response.json()
        )
    except ValueError:
        payload = {}

    retry_after = (
        payload
        .get("parameters", {})
        .get("retry_after")
    )

    if retry_after is None:
        retry_after = response.headers.get(
            "Retry-After"
        )

    try:
        return float(retry_after)
    except (TypeError, ValueError):
        return None


def _extract_message_id(
    response: Response,
) -> int | None:
    try:
        value = (
            response.json()
            .get("result", {})
            .get("message_id")
        )
        return (
            int(value)
            if value is not None
            else None
        )
    except (TypeError, ValueError):
        return None


def _extract_error_message(
    response: Response,
) -> str:
    try:
        payload: dict[str, Any] = (
            response.json()
        )
        description = payload.get(
            "description"
        )
        if description:
            return str(description)
    except ValueError:
        pass

    body = response.text.strip()
    return (
        body
        if body
        else (
            "Telegram HTTP "
            f"{response.status_code}"
        )
    )
