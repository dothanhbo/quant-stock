from __future__ import annotations

import os
from config.strategy_config import Q70_FROZEN
from config import trading_policy
from strategy.paper_v2_scanner import PaperV2Scanner

def configure() -> None:
    os.environ["PAPER_TRADING_ENABLED"] = "true"
    os.environ["PAPER_DATABASE_PATH"] = os.getenv(
        "PAPER_V2_DATABASE_PATH",
        "data/paper_trading_v2.db",
    )

    # Candidate selected by the causal robustness + state/quality research.
    os.environ["TRADING_EXIT_MODEL"] = "atr"
    trading_policy.apply_strategy_config(Q70_FROZEN)

    # Fixed ATR means no trailing is part of the V2 thesis.
    # Keep the field harmless for compatibility with existing config objects.
    os.environ["TRADING_TRAILING_ATR_MULTIPLIER"] = "2.0"

    os.environ["PAPER_V2_ENABLED"] = "true"
    os.environ["PAPER_V2_QUALITY_THRESHOLD"] = "0.70"


def main() -> None:
    configure()

    from strategy import scanner

    v2_scanner = PaperV2Scanner(
        float(os.getenv("PAPER_V2_QUALITY_THRESHOLD", "0.70"))
    )

    results, stats = scanner.run_scan(
        result_processor=v2_scanner.process,
    )

    quality_universe = stats.get("evaluations", [])

    print("\n" + "=" * 65)
    print("🧪 PAPER V2 QUALITY / MARKET STATE")
    print("=" * 65)
    print(f"Quality universe : {len(quality_universe)}")
    print(
        "Quality scored  : "
        f"{stats['paper_v2_quality_scored']}"
    )
    print(f"Q threshold     : {v2_scanner.gate.threshold:.2f}")
    #print(f"V1 candidates   : {stats.get('paper_v2_v1_candidates', 'N/A')}")
    print(f"V2 accepted     : {len(results)}")
    print(f"V2 rejected     : {len(stats['paper_v2_rejected'])}")

    for item in results + stats["paper_v2_rejected"]:
        print(
            f"{item.get('symbol', ''):>6} | "
            f"Q={item.get('paper_v2_quality', 0.0):.3f} | "
            f"State={item.get('paper_v2_state', 'UNKNOWN')} | "
            f"Gate={item.get('paper_v2_gate', '')}"
        )

    market_state = quality_universe[0].get("market_state") if quality_universe else None
    breadth = quality_universe[0].get("breadth_ema50_pct") if quality_universe else None
    breadth_change = quality_universe[0].get("breadth_ema50_change_10d") if quality_universe else None
    if market_state is not None:
        print(
            f"Market State   : {market_state} | "
            f"Breadth EMA50  : {breadth:.2f}% | "
            f"Δ10D           : {breadth_change:+.2f}pp"
        )

if __name__ == "__main__":
    main()
