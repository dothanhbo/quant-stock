from __future__ import annotations

"""Run the V3 paper lifecycle against the isolated V3 database."""

import os


def configure_v3_environment() -> None:
    os.environ["PAPER_TRADING_ENABLED"] = "true"
    os.environ["PAPER_DATABASE_PATH"] = os.getenv(
        "PAPER_V3_DATABASE_PATH",
        "data/paper_trading_v3.db",
    )
    os.environ["PAPER_ATR_STOP_MULTIPLIER"] = "2.0"
    os.environ["TRADING_ENTRY_MODEL"] = "hybrid"
    os.environ["TRADING_EXIT_MODEL"] = "atr"
    os.environ["TRADING_STOP_ATR_MULTIPLIER"] = "2.0"
    os.environ["TRADING_TARGET_ATR_MULTIPLIER"] = "5.0"
    os.environ["PAPER_ATR_TARGET_MULTIPLIER"] = "5.0"
    os.environ["PAPER_DISABLE_TRAILING"] = "true"
    os.environ["PAPER_V2_DISABLE_TRAILING"] = "true"
    os.environ["PAPER_STRATEGY_VERSION"] = "V3_BREADTH_40_60"


def main():
    configure_v3_environment()

    from scripts.run_paper_lifecycle import main as run_paper_lifecycle

    return run_paper_lifecycle()


if __name__ == "__main__":
    main()
