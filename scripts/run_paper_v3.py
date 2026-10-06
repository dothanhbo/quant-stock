from __future__ import annotations

import os

from config.paper_store import V3_STRATEGY_IDENTITY, apply_active_paper_store_environment

from config.strategy_config import V3_BREADTH_PAPER
from config import trading_policy


def configure() -> None:
    os.environ["PAPER_TRADING_ENABLED"] = "true"
    apply_active_paper_store_environment(strategy_identity=V3_STRATEGY_IDENTITY)
    os.environ["TRADING_EXIT_MODEL"] = "atr"
    trading_policy.apply_strategy_config(V3_BREADTH_PAPER)
    os.environ["TRADING_TRAILING_ATR_MULTIPLIER"] = "2.0"
    os.environ["PAPER_V2_ENABLED"] = "false"
    os.environ["PAPER_V2_QUALITY_THRESHOLD"] = "0.70"


def main() -> None:
    configure()
    # Canonical strategy-aware path (same as daily / quantctl scan).
    from app.strategy_scan import run_strategy_scan

    results, stats = run_strategy_scan(strategy_identity=V3_STRATEGY_IDENTITY)

    print("\n" + "=" * 65)
    print("🧪 PAPER V3 — Q70 + MARKET BREADTH")
    print("=" * 65)
    print(f"V3 accepted     : {len(results)}")
    print(f"Breadth blocked : {stats['paper_v3_zero_exposure']}")
    print(f"Full exposure   : {stats['paper_v3_full_exposure']}")
    print(f"Half exposure   : {stats['paper_v3_half_exposure']}")
    print(f"Q threshold     : {stats['paper_v3_quality_threshold']:.2f}")

    for item in results + stats["paper_v3_breadth_blocked"]:
        print(
            f"{item.get('symbol', ''):>6} | "
            f"Q={item.get('paper_v2_quality', 0.0):.3f} | "
            f"Breadth={item.get('breadth_ema50_pct', float('nan')):.2f}% | "
            f"Exposure={item.get('breadth_exposure_pct', 0.0):.0f}% | "
            f"Gate={item.get('paper_v3_breadth_gate', '')}"
        )


if __name__ == "__main__":
    main()
