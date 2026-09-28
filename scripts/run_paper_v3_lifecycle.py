from __future__ import annotations

"""Run the V3 paper lifecycle against the isolated V3 database."""

import os

from config.paper_store import V3_STRATEGY_IDENTITY, apply_active_paper_store_environment


def configure_v3_environment() -> None:
    os.environ["PAPER_TRADING_ENABLED"] = "true"
    apply_active_paper_store_environment(strategy_identity=V3_STRATEGY_IDENTITY)
    os.environ["PAPER_ATR_STOP_MULTIPLIER"] = "2.0"
    os.environ["TRADING_ENTRY_MODEL"] = "hybrid"
    os.environ["TRADING_EXIT_MODEL"] = "atr"
    os.environ["TRADING_STOP_ATR_MULTIPLIER"] = "2.0"
    os.environ["TRADING_TARGET_ATR_MULTIPLIER"] = "5.0"
    os.environ["PAPER_ATR_TARGET_MULTIPLIER"] = "5.0"
    os.environ["PAPER_DISABLE_TRAILING"] = "true"
    os.environ["PAPER_V2_DISABLE_TRAILING"] = "true"


def main():
    configure_v3_environment()

    from scripts.run_paper_lifecycle import main as run_paper_lifecycle

    return run_paper_lifecycle()


if __name__ == "__main__":
    main()
