from __future__ import annotations

"""Run the V2 lifecycle against the isolated V2 paper database."""

import os

from config.paper_store import Q70_STRATEGY_IDENTITY, apply_active_paper_store_environment


def configure_v2_environment() -> None:
    """Pin the frozen Q70 (V2) runtime environment.

    Shared by the Q70 lifecycle and by the canonical strategy-aware scan
    (``app.strategy_scan``) so every operational entrypoint resolves the same
    policy. Values and order are unchanged from the former inline ``main``.
    """
    os.environ["PAPER_TRADING_ENABLED"] = "true"
    apply_active_paper_store_environment(strategy_identity=Q70_STRATEGY_IDENTITY)
    os.environ["PAPER_ATR_STOP_MULTIPLIER"] = "2.0"
    os.environ["TRADING_EXIT_MODEL"] = "atr"
    os.environ["TRADING_STOP_ATR_MULTIPLIER"] = "2.0"
    os.environ["TRADING_TARGET_ATR_MULTIPLIER"] = "5.0"
    os.environ["PAPER_V2_DISABLE_TRAILING"] = "true"


def main():
    configure_v2_environment()

    # Execute the existing lifecycle entrypoint with V2 environment.
    from scripts.run_paper_lifecycle import main as run_paper_lifecycle

    return run_paper_lifecycle()


if __name__ == "__main__":
    main()
