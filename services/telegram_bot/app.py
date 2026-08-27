from __future__ import annotations

import logging

from services.telegram_bot.bot import TelegramQueryBot


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    TelegramQueryBot.from_env().run_forever()


if __name__ == "__main__":
    main()
