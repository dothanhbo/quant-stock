from __future__ import annotations

import os

from config.strategy_config import V3_BREADTH_PAPER
from config import trading_policy
from strategy.paper_v3_scanner import PaperV3Scanner


def configure() -> None:
    os.environ["PAPER_TRADING_ENABLED"] = "true"
    os.environ["PAPER_DATABASE_PATH"] = os.getenv(
        "PAPER_V3_DATABASE_PATH",
        "data/paper_trading_v3.db",
    )
    os.environ["TRADING_EXIT_MODEL"] = "atr"
    os.environ["PAPER_STRATEGY_VERSION"] = "V3_BREADTH_40_60"
    trading_policy.apply_strategy_config(V3_BREADTH_PAPER)
    os.environ["TRADING_TRAILING_ATR_MULTIPLIER"] = "2.0"
    os.environ["PAPER_V2_ENABLED"] = "false"
    os.environ["PAPER_V2_QUALITY_THRESHOLD"] = "0.70"


def main() -> None:
    configure()
    from strategy import scanner

    v3_scanner = PaperV3Scanner(
        float(os.getenv("PAPER_V2_QUALITY_THRESHOLD", "0.70"))
    )
    results, stats = scanner.run_scan(
        result_processor=v3_scanner.process,
    )

    print("\n" + "=" * 65)
    print("🧪 PAPER V3 — Q70 + MARKET BREADTH")
    print("=" * 65)
    print(f"V3 accepted     : {len(results)}")
    print(f"Breadth blocked : {stats['paper_v3_zero_exposure']}")
    print(f"Full exposure   : {stats['paper_v3_full_exposure']}")
    print(f"Half exposure   : {stats['paper_v3_half_exposure']}")
    print(f"Q threshold     : {v3_scanner.gate.threshold:.2f}")

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
