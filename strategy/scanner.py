"""VN100 scanner orchestration.

Business rules live in dedicated modules:
- strategy.filters
- strategy.scoring
- strategy.watchlist
- risk.levels
- reporting.dashboard
"""

from __future__ import annotations

from collections import Counter
from typing import Optional

import pandas as pd
from sqlalchemy import text

from config.strategy_loader import COMMON_CONFIG
from core.database import engine, get_reference_market_date, get_symbol_latest_dates, load_price_data
from core.signal_database import save_signal
from reporting.dashboard import print_end_of_day_dashboard, print_scan_results
from risk.levels import calculate_risk_levels
from services.telegram import build_scan_message, send_telegram
from strategy.cache import get_indicators_cached
from strategy.filters import REQUIRED_INDICATORS, evaluate_conditions, trend_passes
from strategy.market_regime import get_market_regime
from strategy.relative_strength import calculate_relative_strength
from strategy.scoring import calculate_score
from strategy.watchlist import classify

MIN_DATA_ROWS = int(COMMON_CONFIG["min_data_rows"])
TOP_RESULTS = int(COMMON_CONFIG["top_results"])
TOP_WATCHLIST = int(COMMON_CONFIG["top_watchlist"])
RS_PERIOD = int(COMMON_CONFIG.get("rs_period", 20))

# Backward-compatible aliases for earlier imports.
_trend_passes = trend_passes


def get_all_symbols() -> list[str]:
    query = text("SELECT DISTINCT symbol FROM prices ORDER BY symbol ASC")
    with engine.connect() as connection:
        rows = connection.execute(query).fetchall()
    return [str(row[0]) for row in rows]


def _prepare_price_data(symbol: str, end_date=None) -> pd.DataFrame:
    df = load_price_data(symbol)
    if df.empty:
        return df

    prepared = df.copy()
    prepared["time"] = pd.to_datetime(prepared["time"], errors="coerce")
    prepared = prepared.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)

    if end_date is not None:
        cutoff = pd.to_datetime(end_date, errors="coerce")
        if pd.isna(cutoff):
            raise ValueError(f"end_date không hợp lệ: {end_date}")
        prepared = prepared[prepared["time"] <= cutoff].copy()
    return prepared


def evaluate_symbol(
    symbol: str,
    reference_date: Optional[str] = None,
    end_date=None,
    market_config: Optional[dict] = None,
) -> dict:
    """Evaluate one symbol and always return a structured status/result."""
    market_config = market_config or get_market_regime(end_date=end_date)
    base = {"symbol": symbol, "status": "REJECTED", "reason": "other"}

    df = _prepare_price_data(symbol, end_date=end_date)
    if df.empty:
        return base
    if len(df) < MIN_DATA_ROWS:
        return {**base, "reason": "insufficient_data"}

    data = get_indicators_cached(symbol, df, end_date=end_date)
    if data.empty:
        return base

    latest = data.iloc[-1]
    latest_date = pd.to_datetime(latest["time"], errors="coerce")
    if pd.isna(latest_date):
        return base
    date_text = latest_date.strftime("%Y-%m-%d")
    if reference_date is not None and date_text != reference_date:
        return {**base, "reason": "stale_data"}
    if latest[REQUIRED_INDICATORS].isna().any():
        return {**base, "reason": "indicator_nan"}

    rs = calculate_relative_strength(symbol, period=RS_PERIOD, end_date=end_date)
    if not rs["available"]:
        return {**base, "reason": "relative_strength_data"}
    relative_strength = float(rs["relative_strength"])

    conditions = evaluate_conditions(latest, relative_strength, market_config)
    score, reasons = calculate_score(latest, relative_strength)
    status, reason, missing = classify(score, conditions, market_config)
    risk = calculate_risk_levels(latest, market_config)

    result = {
        **base,
        "status": status,
        "reason": reason,
        "date": date_text,
        "score": score,
        "min_score": int(market_config["min_score"]),
        "regime": market_config["regime"],
        **risk,
        "ema10": round(float(latest["EMA10"]), 2),
        "ema20": round(float(latest["EMA20"]), 2),
        "ema50": round(float(latest["EMA50"]), 2),
        "rsi": round(float(latest["RSI"]), 2),
        "adx": round(float(latest["ADX14"]), 2),
        "atr": round(float(latest["ATR14"]), 2),
        "atr_percent": round(float(latest["ATR_Percent"]), 2),
        "volume_ratio": round(float(latest["Vol_Ratio"]), 2),
        "distance_ema20": round(float(latest["Distance_EMA20_Pct"]), 2),
        "return_3d": round(float(latest["Return_3D_Pct"]), 2),
        "breakout_20d": bool(latest["Breakout_20D"]),
        "volume_breakout_5d": bool(latest["Volume_Breakout_5D"]),
        "stock_return_20d": rs["stock_return"],
        "index_return_20d": rs["index_return"],
        "relative_strength_20d": rs["relative_strength"],
        "conditions": conditions,
        "failed_conditions": [name for name, passed in conditions.items() if not passed],
        "reasons": reasons,
    }
    if status == "WATCHLIST":
        result["missing"] = missing
    return result


def check_signal(symbol, reference_date=None, end_date=None, market_config=None):
    """Backtest-compatible API: return a passed signal or ``None``."""
    evaluation = evaluate_symbol(symbol, reference_date, end_date, market_config)
    return evaluation if evaluation["status"] == "PASSED" else None


def scan_all_symbols(market_config=None):
    market_config = market_config or get_market_regime()
    symbols = [symbol for symbol in get_all_symbols() if symbol != "VNINDEX"]
    reference_date = get_reference_market_date()
    latest_dates = get_symbol_latest_dates()
    fresh_symbols = [symbol for symbol in symbols if latest_dates.get(symbol) == reference_date]
    stale_symbols = [
        {"symbol": symbol, "latest_date": latest_dates.get(symbol)}
        for symbol in symbols if latest_dates.get(symbol) != reference_date
    ]

    print("\n" + "=" * 65)
    print("📅 KIỂM TRA NGÀY DỮ LIỆU")
    print("=" * 65)
    print(f"Ngày chuẩn thị trường: {reference_date}")
    print(f"Mã dữ liệu đúng ngày: {len(fresh_symbols)}/{len(symbols)}")
    print(f"Mã dữ liệu cũ/lỗi: {len(stale_symbols)}")

    signals: list[dict] = []
    watchlist: list[dict] = []
    scan_errors: list[dict] = []
    reject_stats: Counter = Counter()
    condition_fail_stats: Counter = Counter()

    print(f"\n🔍 Bắt đầu quét {len(fresh_symbols)} mã hợp lệ...")
    for index, symbol in enumerate(fresh_symbols, start=1):
        print(f"\rĐang quét {index}/{len(fresh_symbols)}: {symbol}", end="", flush=True)
        try:
            evaluation = evaluate_symbol(symbol, reference_date=reference_date, market_config=market_config)
            reject_stats[evaluation["reason"]] += 1
            condition_fail_stats.update(evaluation.get("failed_conditions", []))
            if evaluation["status"] == "PASSED":
                signals.append(evaluation)
            elif evaluation["status"] == "WATCHLIST":
                watchlist.append(evaluation)
        except Exception as error:
            reject_stats["exception"] += 1
            scan_errors.append({"symbol": symbol, "error": str(error)})
            print(f"\n❌ Lỗi quét {symbol}: {error}")

    sort_key = lambda item: (item["score"], item["relative_strength_20d"], item["volume_ratio"], item["adx"])
    signals.sort(key=sort_key, reverse=True)
    watchlist.sort(key=sort_key, reverse=True)

    scan_stats = {
        "reference_date": reference_date,
        "total_symbols": len(symbols),
        "fresh_count": len(fresh_symbols),
        "stale_count": len(stale_symbols),
        "stale_symbols": stale_symbols,
        "error_count": len(scan_errors),
        "scan_errors": scan_errors,
        "reject_stats": dict(reject_stats),
        "condition_fail_stats": dict(condition_fail_stats),
        "watchlist": watchlist,
        "market_config": market_config,
    }
    return signals, scan_stats


def run_scan() -> tuple[list[dict], dict]:
    """Run the production scan, persist passed signals and notify Telegram."""
    market_config = get_market_regime()
    print("\n" + "=" * 65)
    print("📊 MARKET REGIME")
    print("=" * 65)
    print(f"Trạng thái: {market_config['regime']}")
    print(f"Điểm tối thiểu: {market_config['min_score']}")
    print(f"ADX tối thiểu: {market_config['min_adx']}")
    print(f"Volume tối thiểu: {market_config['min_volume_ratio']:.2f}x MA20")
    print(f"RS tối thiểu: {market_config['min_relative_strength']:+.2f}%")

    results, scan_stats = scan_all_symbols(market_config=market_config)
    watchlist = scan_stats["watchlist"]
    print_scan_results(results, watchlist, top_results=TOP_RESULTS, top_watchlist=TOP_WATCHLIST)
    print_end_of_day_dashboard(results, scan_stats)

    saved_count = duplicate_count = save_failed_count = 0
    for signal in results:
        try:
            if save_signal(signal):
                saved_count += 1
            else:
                duplicate_count += 1
        except Exception as error:
            save_failed_count += 1
            print(f"❌ Không lưu được {signal['symbol']}: {error}")

    print("\n" + "-" * 60)
    print(f"Tín hiệu mới: {saved_count}")
    print(f"Tín hiệu trùng: {duplicate_count}")
    print(f"Lỗi lưu: {save_failed_count}")

    try:
        message = build_scan_message(results, top_n=TOP_RESULTS, watchlist=watchlist, market_config=market_config)
        send_telegram(message)
        print("\n✅ Đã gửi kết quả quét lên Telegram.")
    except Exception as error:
        print(f"\n❌ Gửi Telegram thất bại: {error}")
    return results, scan_stats


if __name__ == "__main__":
    run_scan()
