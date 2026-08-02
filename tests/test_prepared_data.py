import pandas as pd
import pytest

from backtesting.prepared_data import (
    add_relative_strength_columns,
)


def test_add_relative_strength_columns():
    stock = pd.DataFrame(
        {
            "time": pd.date_range(
                "2026-01-01",
                periods=4,
                freq="D",
            ),
            "close": [
                100.0,
                105.0,
                110.0,
                120.0,
            ],
        }
    )

    benchmark = pd.DataFrame(
        {
            "time": pd.date_range(
                "2026-01-01",
                periods=4,
                freq="D",
            ),
            "close": [
                100.0,
                102.0,
                104.0,
                106.0,
            ],
        }
    )

    result = add_relative_strength_columns(
        stock,
        benchmark,
        period=2,
    )

    # Stock: 110 / 100 - 1 = 10%
    assert result.loc[
        2,
        "Stock_Return_20D",
    ] == pytest.approx(10.0)

    # Index: 104 / 100 - 1 = 4%
    assert result.loc[
        2,
        "Index_Return_20D",
    ] == pytest.approx(4.0)

    assert result.loc[
        2,
        "Relative_Strength_20D",
    ] == pytest.approx(6.0)


def test_relative_strength_has_no_lookahead():
    stock = pd.DataFrame(
        {
            "time": pd.date_range(
                "2026-01-01",
                periods=3,
                freq="D",
            ),
            "close": [100, 110, 500],
        }
    )

    benchmark = pd.DataFrame(
        {
            "time": pd.date_range(
                "2026-01-01",
                periods=3,
                freq="D",
            ),
            "close": [100, 105, 50],
        }
    )

    result = add_relative_strength_columns(
        stock,
        benchmark,
        period=1,
    )

    # Dòng ngày thứ hai không được chịu ảnh hưởng
    # bởi biến động rất lớn ở ngày thứ ba.
    assert result.loc[
        1,
        "Stock_Return_20D",
    ] == pytest.approx(10.0)

    assert result.loc[
        1,
        "Index_Return_20D",
    ] == pytest.approx(5.0)

    assert result.loc[
        1,
        "Relative_Strength_20D",
    ] == pytest.approx(5.0)


def test_relative_strength_rejects_invalid_period():
    with pytest.raises(
        ValueError,
        match="period must be greater than 0",
    ):
        add_relative_strength_columns(
            pd.DataFrame(),
            pd.DataFrame(),
            period=0,
        )	