from __future__ import annotations

import pandas as pd

from strategy.cache  import get_indicators_cached
from core.database import load_price_data


def prepare_backtest_dataset(
    symbol: str,
    *,
    end_date=None,
) -> pd.DataFrame:
    """
    Chuẩn bị toàn bộ dữ liệu cho backtest.

    Hiện tại:
        - Load dữ liệu giá
        - Tính indicator

    Các phiên bản tiếp theo sẽ bổ sung:
        - Relative Strength
        - Market Regime
        - Indicator Cache
    """

    df = load_price_data(symbol)

    if df.empty:
        return df

    df = get_indicators_cached(
        symbol,
        df,
        end_date=end_date,
    )

    return df