from __future__ import annotations

"""Run the V2 lifecycle against the isolated V2 paper database."""

import os


def main():
    os.environ["PAPER_TRADING_ENABLED"] = "true"
    os.environ["PAPER_DATABASE_PATH"] = os.getenv(
        "PAPER_V2_DATABASE_PATH",
        "data/paper_trading_v2.db",
    )
    os.environ["PAPER_ATR_STOP_MULTIPLIER"] = "2.0"
    os.environ["TRADING_EXIT_MODEL"] = "atr"
    os.environ["TRADING_STOP_ATR_MULTIPLIER"] = "2.0"
    os.environ["TRADING_TARGET_ATR_MULTIPLIER"] = "5.0"
    os.environ["PAPER_V2_DISABLE_TRAILING"] = "true"

    # Execute the existing lifecycle entrypoint with V2 environment.
    from scripts.run_paper_lifecycle import main as run_paper_lifecycle

    return run_paper_lifecycle()


if __name__ == "__main__":
    main()
