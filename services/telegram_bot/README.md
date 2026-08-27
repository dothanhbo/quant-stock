# Telegram Query Bot — Gemini Free Analyst

The query bot is read-only. It reuses `strategy.scanner.evaluate_symbol()` and the same market-regime configuration as the production scanner.

## Run locally

```powershell
py -m services.telegram_bot.app
```

Accepted messages from the configured `CHAT_ID`:

```text
FPT
/check FPT
```

The bot never creates BUY/SELL orders.

## Gemini AI Analyst — Free Tier

The AI layer runs only when you explicitly query a ticker. Quant Engine remains authoritative; Gemini only interprets the already-computed snapshot.

Add to `.env`:

```env
AI_ANALYSIS_ENABLED=true
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.6-flash
AI_ANALYSIS_TIMEOUT_SECONDS=30
```

The old `OPENAI_API_KEY` / `OPENAI_MODEL` variables are no longer used by this module and can be removed from `.env` if nothing else in the project needs them.

Flow:

```text
Telegram ticker
  -> evaluate_symbol() / market regime
  -> deterministic Quant snapshot
  -> Gemini API (read-only interpretation)
  -> short / medium / long-term + entry quality + risk
  -> one Telegram reply
```

Guardrails:

- AI is never an entry condition.
- AI cannot make REJECTED/WATCHLIST appear strategy-confirmed.
- Without fundamental data, long-term analysis remains insufficient/watch-only.
- Timeout/API failure never blocks the Quant result.
- API errors are logged to terminal with HTTP status for easier diagnosis.

## Server deployment

For now, run daily pipeline and Telegram bot as separate processes on the same server, sharing the committed `market.db`. Do not run two `getUpdates` consumers with the same Telegram token simultaneously.

AI analyst is intentionally independent from Quant status. It may be more bullish or more bearish, but never changes execution.

Recommended:
`AI_ANALYSIS_MAX_OUTPUT_TOKENS=1200`


### Giới hạn độ dài AI

Structured output giới hạn trực tiếp độ dài từng reason (180–220 ký tự) và summary (320 ký tự) để tránh JSON bị cắt giữa chừng và giữ Telegram dễ đọc. Parser vẫn cắt an toàn lần cuối nếu provider trả dài hơn schema.
