"""Console presentation for scan results and end-of-day statistics."""

from __future__ import annotations


def print_end_of_day_dashboard(signals: list[dict], scan_stats: dict) -> None:
    market_config = scan_stats.get("market_config", {})
    fails = scan_stats.get("condition_fail_stats", {})
    watchlist = scan_stats.get("watchlist", [])
    width = 58
    print("\n" + "=" * width)
    print("📈 DASHBOARD CUỐI PHIÊN")
    print("=" * width)
    print(f"Market      : {market_config.get('regime', 'UNKNOWN')}")
    print(f"Date        : {scan_stats.get('reference_date', 'N/A')}")
    print(f"Scanned     : {scan_stats.get('fresh_count', 0)}/{scan_stats.get('total_symbols', 0)}")
    print(f"Passed      : {len(signals)}")
    print(f"Watchlist   : {len(watchlist)}")
    print(f"Stale/Error : {scan_stats.get('stale_count', 0)}/{scan_stats.get('error_count', 0)}")
    print("-" * width)
    print("Rejected conditions (independent counts)")
    rows = [
        ("Trend", "trend"), ("Volume", "volume"), ("ADX", "adx"),
        ("RSI", "rsi"), ("Distance EMA20", "distance"),
        ("Overheated", "overheated"), ("Relative Strength", "relative_strength"),
    ]
    for label, key in rows:
        print(f"{label:<20}: {int(fails.get(key, 0))}")
    print("=" * width)


def print_scan_results(signals: list[dict], watchlist: list[dict] | None = None, *, top_results: int = 10, top_watchlist: int = 10) -> None:
    watchlist = watchlist or []
    print("\n\n" + "=" * 65)
    print("🚀 KẾT QUẢ QUÉT CỔ PHIẾU NGẮN HẠN")
    print("=" * 65)
    if not signals:
        print("Không có mã nào thỏa toàn bộ điều kiện hôm nay.")
    for index, signal in enumerate(signals[:top_results], start=1):
        print(f"\n#{index} {signal['symbol']} | {signal['score']}/100 | RS {signal['relative_strength_20d']:+.2f}%")
        print(
            f"Entry {signal['entry']:.2f} | SL {signal['stop_loss']:.2f} "
            f"({signal['stop_loss_pct']:.2f}%) | TP {signal['take_profit']:.2f} "
            f"(+{signal['take_profit_pct']:.2f}%) | RR {signal['rr_ratio']:.1f}"
        )
        print(
            f"RSI {signal['rsi']:.1f} | ADX {signal['adx']:.1f} | "
            f"Volume {signal['volume_ratio']:.2f}x | EMA20 {signal['distance_ema20']:+.2f}%"
        )
        if signal["reasons"]:
            print("Lý do: " + "; ".join(signal["reasons"]))

    if watchlist:
        print("\n" + "-" * 65)
        print("🟡 WATCHLIST – CÁC MÃ GẦN ĐẠT")
        for index, item in enumerate(watchlist[:top_watchlist], start=1):
            missing = ", ".join(item.get("missing", []))
            print(
                f"{index}. {item['symbol']} | {item['score']}/{item['min_score']} | "
                f"RS {item['relative_strength_20d']:+.2f}% | Thiếu: {missing}"
            )
