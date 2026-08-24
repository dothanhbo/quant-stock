import numpy as np
import pandas as pd


def calculate_rsi(
    close: pd.Series,
    period: int = 14
) -> pd.Series:
    """
    Tính RSI theo phương pháp Wilder.
    """

    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    rsi = 100 - (100 / (1 + rs))

    # Trường hợp không có phiên giảm
    rsi = rsi.where(avg_loss != 0, 100)

    # Trường hợp không có phiên tăng
    rsi = rsi.where(avg_gain != 0, 0)

    return rsi


def calculate_atr(
    df: pd.DataFrame,
    period: int = 14
) -> pd.Series:
    """
    Tính ATR theo phương pháp Wilder.
    """

    previous_close = df["close"].shift(1)

    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs()
        ],
        axis=1
    ).max(axis=1)

    atr = true_range.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    return atr


def calculate_adx(
    df: pd.DataFrame,
    period: int = 14
) -> pd.Series:
    """
    Tính ADX theo phương pháp Wilder.
    """

    high_diff = df["high"].diff()
    low_diff = -df["low"].diff()

    plus_dm = pd.Series(
        np.where(
            (high_diff > low_diff) & (high_diff > 0),
            high_diff,
            0.0
        ),
        index=df.index
    )

    minus_dm = pd.Series(
        np.where(
            (low_diff > high_diff) & (low_diff > 0),
            low_diff,
            0.0
        ),
        index=df.index
    )

    atr = calculate_atr(df, period)

    plus_dm_smoothed = plus_dm.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    minus_dm_smoothed = minus_dm.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    plus_di = 100 * (
        plus_dm_smoothed / atr.replace(0, np.nan)
    )

    minus_di = 100 * (
        minus_dm_smoothed / atr.replace(0, np.nan)
    )

    dx = (
        100
        * (plus_di - minus_di).abs()
        / (plus_di + minus_di).replace(0, np.nan)
    )

    adx = dx.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    return adx


def add_indicators(
    df: pd.DataFrame
) -> pd.DataFrame:
    """
    Thêm toàn bộ chỉ báo cần thiết cho chiến lược.
    """

    if df is None or df.empty:
        return pd.DataFrame()

    data = df.copy()

    required_columns = [
        "time",
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in data.columns
    ]

    if missing_columns:
        raise ValueError(
            f"Thiếu các cột: {missing_columns}"
        )

    # Chuẩn hóa dữ liệu
    data["time"] = pd.to_datetime(
        data["time"],
        errors="coerce"
    )

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]

    for column in numeric_columns:
        data[column] = pd.to_numeric(
            data[column],
            errors="coerce"
        )

    data = (
        data
        .dropna(
            subset=required_columns
        )
        .sort_values("time")
        .drop_duplicates(
            subset=["time"],
            keep="last"
        )
        .reset_index(drop=True)
    )

    # ==========================
    # EMA
    # ==========================

    data["EMA10"] = data["close"].ewm(
        span=10,
        adjust=False
    ).mean()

    data["EMA20"] = data["close"].ewm(
        span=20,
        adjust=False
    ).mean()

    data["EMA50"] = data["close"].ewm(
        span=50,
        adjust=False
    ).mean()

    # EMA20 dốc lên hay không
    data["EMA20_Rising"] = (
        data["EMA20"]
        > data["EMA20"].shift(1)
    )

    # ==========================
    # RSI
    # ==========================

    data["RSI"] = calculate_rsi(
        data["close"],
        period=14
    )

    # ==========================
    # VOLUME
    # ==========================

    data["Vol_MA20"] = (
        data["volume"]
        .rolling(
            window=20,
            min_periods=20
        )
        .mean()
    )

    data["Vol_Ratio"] = (
        data["volume"]
        / data["Vol_MA20"].replace(0, np.nan)
    )

    # Volume cao nhất của 5 phiên trước
    data["Previous_5D_Max_Volume"] = (
        data["volume"]
        .shift(1)
        .rolling(
            window=5,
            min_periods=5
        )
        .max()
    )

    data["Volume_Breakout_5D"] = (
        data["volume"]
        > data["Previous_5D_Max_Volume"]
    )

    # ==========================
    # ATR VÀ ADX
    # ==========================

    data["ATR14"] = calculate_atr(
        data,
        period=14
    )

    data["ATR_Percent"] = (
        data["ATR14"]
        / data["close"].replace(0, np.nan)
        * 100
    )

    data["ADX14"] = calculate_adx(
        data,
        period=14
    )

    # ==========================
    # BREAKOUT 20 PHIÊN
    # ==========================

    # Đỉnh của 20 phiên trước, không tính phiên hiện tại
    data["Previous_20D_High"] = (
        data["high"]
        .shift(1)
        .rolling(
            window=20,
            min_periods=20
        )
        .max()
    )

    data["Breakout_20D"] = (
        data["close"]
        > data["Previous_20D_High"]
    )

    # Retest context: only prior sessions are eligible. The current
    # session is shifted out so an Entry V2 signal cannot call today's
    # breakout a historical breakout.
    data["Recent_Breakout_10D"] = (
        data["Breakout_20D"]
        .shift(1)
        .rolling(
            window=10,
            min_periods=1,
        )
        .max()
        .fillna(False)
        .astype(bool)
    )

    data["Touched_EMA10"] = (
        data["low"]
        <= data["EMA10"] * 1.01
    )

    data["Reclaimed_EMA10"] = (
        data["close"]
        >= data["EMA10"]
    )

    # ==========================
    # TRÁNH MUA ĐUỔI
    # ==========================

    data["Distance_EMA20_Pct"] = (
        (
            data["close"]
            - data["EMA20"]
        )
        / data["EMA20"].replace(0, np.nan)
        * 100
    )

    data["Return_3D_Pct"] = (
        data["close"]
        .pct_change(periods=3)
        * 100
    )

    # ==========================
    # PRICE ACTION
    # ==========================

    candle_range = (
        data["high"]
        - data["low"]
    )

    candle_body = (
        data["close"]
        - data["open"]
    ).abs()

    data["Body_Ratio"] = np.where(
        candle_range > 0,
        candle_body / candle_range,
        0.0
    )

    data["Green_Candle"] = (
        data["close"]
        > data["open"]
    )

    data["Close_Upper_Half"] = np.where(
        candle_range > 0,
        data["close"]
        >= (
            data["low"]
            + candle_range * 0.5
        ),
        True
    )

    return data
