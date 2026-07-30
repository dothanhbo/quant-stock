import pandas as pd

from core.database import load_price_data
from modules.indicators import add_indicators


def get_vnindex_status():
    df = load_price_data("VNINDEX")

    if df.empty or len(df) < 50:
        return {
            "available": False,
            "safe": False,
            "message": "Không đủ dữ liệu VNINDEX"
        }

    data = add_indicators(df)
    latest = data.iloc[-1]

    close = float(latest["close"])
    ema20 = float(latest["EMA20"])
    ema50 = float(latest["EMA50"])
    rsi = float(latest["RSI"])

    above_ema20 = close > ema20
    above_ema50 = close > ema50
    ema20_rising = bool(latest["EMA20_Rising"])

    return_20d = (
        data["close"].pct_change(20).iloc[-1] * 100
    )

    safe = (
        above_ema20
        and ema20_rising
    )

    if safe and above_ema50:
        regime = "THUẬN LỢI"
        nav = "70%–100% NAV"
        volume_factor = 1.2

    elif above_ema20:
        regime = "TRUNG TÍNH"
        nav = "40%–60% NAV"
        volume_factor = 1.3

    else:
        regime = "RỦI RO"
        nav = "20%–30% NAV"
        volume_factor = 1.5

    return {
        "available": True,
        "safe": safe,
        "regime": regime,
        "nav": nav,
        "volume_factor": volume_factor,
        "date": latest["time"].strftime("%Y-%m-%d"),
        "close": round(close, 2),
        "ema20": round(ema20, 2),
        "ema50": round(ema50, 2),
        "rsi": round(rsi, 2),
        "return_20d": round(float(return_20d), 2),
        "above_ema20": above_ema20,
        "above_ema50": above_ema50,
        "ema20_rising": ema20_rising
    }


def calculate_market_breadth(symbols):
    total = 0
    above_ema20_count = 0
    above_ema50_count = 0
    bullish_stack_count = 0

    for symbol in symbols:
        if symbol == "VNINDEX":
            continue

        df = load_price_data(symbol)

        if df.empty or len(df) < 50:
            continue

        try:
            data = add_indicators(df)
            latest = data.iloc[-1]

            required = [
                "close",
                "EMA10",
                "EMA20",
                "EMA50"
            ]

            if latest[required].isna().any():
                continue

            total += 1

            if latest["close"] > latest["EMA20"]:
                above_ema20_count += 1

            if latest["close"] > latest["EMA50"]:
                above_ema50_count += 1

            if (
                latest["close"] > latest["EMA10"]
                and latest["EMA10"] > latest["EMA20"]
                and latest["EMA20"] > latest["EMA50"]
            ):
                bullish_stack_count += 1

        except Exception:
            continue

    if total == 0:
        return {
            "total": 0,
            "above_ema20_pct": 0,
            "above_ema50_pct": 0,
            "bullish_stack_pct": 0
        }

    return {
        "total": total,
        "above_ema20_pct": round(
            above_ema20_count / total * 100,
            2
        ),
        "above_ema50_pct": round(
            above_ema50_count / total * 100,
            2
        ),
        "bullish_stack_pct": round(
            bullish_stack_count / total * 100,
            2
        )
    }