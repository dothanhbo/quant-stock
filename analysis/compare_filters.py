import os
import pandas as pd


INPUT_FILE = os.path.join(
    "backtest_results_multi",
    "all_trades.csv"
)

OUTPUT_FOLDER = os.path.join(
    "backtest_results_multi",
    "filter_comparison"
)


def load_trades():
    if not os.path.exists(INPUT_FILE):
        raise FileNotFoundError(
            f"Không tìm thấy file: {INPUT_FILE}"
        )

    df = pd.read_csv(INPUT_FILE)

    numeric_columns = [
        "return_pct",
        "adx",
        "distance_ema20",
        "rsi",
        "volume_ratio",
        "score",
        "holding_days"
    ]

    for column in numeric_columns:
        if column in df.columns:
            df[column] = pd.to_numeric(
                df[column],
                errors="coerce"
            )

    df = df.dropna(
        subset=["return_pct"]
    ).reset_index(drop=True)

    return df


def calculate_metrics(name, df):
    total_trades = len(df)

    if total_trades == 0:
        return {
            "strategy": name,
            "total_trades": 0
        }

    winners = df[
        df["return_pct"] > 0
    ]

    losers = df[
        df["return_pct"] < 0
    ]

    wins = len(winners)
    losses = len(losers)

    gross_profit = winners[
        "return_pct"
    ].sum()

    gross_loss = abs(
        losers["return_pct"].sum()
    )

    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0
        else float("inf")
    )

    average_win = (
        winners["return_pct"].mean()
        if wins > 0
        else 0.0
    )

    average_loss = (
        losers["return_pct"].mean()
        if losses > 0
        else 0.0
    )

    payoff_ratio = (
        average_win / abs(average_loss)
        if average_loss != 0
        else 0.0
    )

    win_rate = (
        wins / total_trades * 100
    )

    average_return = df[
        "return_pct"
    ].mean()

    compounded_return = (
        (
            (1 + df["return_pct"] / 100)
            .prod()
            - 1
        )
        * 100
    )

    return {
        "strategy": name,
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": win_rate,
        "average_return_pct": average_return,
        "average_win_pct": average_win,
        "average_loss_pct": average_loss,
        "profit_factor": profit_factor,
        "payoff_ratio": payoff_ratio,
        "compounded_return_pct": compounded_return,
        "best_trade_pct": df["return_pct"].max(),
        "worst_trade_pct": df["return_pct"].min()
    }


def build_filters(df):
    filters = {
        "BASE": df,

        "ADX >= 20": df[
            df["adx"] >= 20
        ],

        "ADX >= 25": df[
            df["adx"] >= 25
        ],

        "ADX >= 30": df[
            df["adx"] >= 30
        ],

        "ADX >= 40": df[
            df["adx"] >= 40
        ],

        "Distance EMA20 < 2%": df[
            df["distance_ema20"] < 2
        ],

        "ADX >= 25 + Distance < 2%": df[
            (df["adx"] >= 25)
            & (df["distance_ema20"] < 2)
        ],

        "ADX >= 30 + Distance < 2%": df[
            (df["adx"] >= 30)
            & (df["distance_ema20"] < 2)
        ],

        "ADX >= 25 + Volume < 2": df[
            (df["adx"] >= 25)
            & (df["volume_ratio"] < 2)
        ],

        "ADX >= 30 + Volume < 2": df[
            (df["adx"] >= 30)
            & (df["volume_ratio"] < 2)
        ],

        "ADX >= 25 + Distance < 2% + Volume < 2": df[
            (df["adx"] >= 25)
            & (df["distance_ema20"] < 2)
            & (df["volume_ratio"] < 2)
        ]
    }

    return filters


def main():
    os.makedirs(
        OUTPUT_FOLDER,
        exist_ok=True
    )

    df = load_trades()

    required_columns = [
        "adx",
        "distance_ema20",
        "volume_ratio"
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing_columns:
        raise ValueError(
            f"Thiếu các cột: {missing_columns}"
        )

    filters = build_filters(df)

    results = []

    for name, filtered_df in filters.items():
        metrics = calculate_metrics(
            name,
            filtered_df
        )

        results.append(metrics)

    result_df = pd.DataFrame(results)

    result_df = result_df.sort_values(
        by=[
            "profit_factor",
            "average_return_pct"
        ],
        ascending=False
    ).reset_index(drop=True)

    output_path = os.path.join(
        OUTPUT_FOLDER,
        "filter_comparison.csv"
    )

    result_df.to_csv(
        output_path,
        index=False,
        encoding="utf-8-sig"
    )

    print()
    print("=" * 120)
    print("SO SÁNH CÁC BỘ LỌC")
    print("=" * 120)

    display_columns = [
        "strategy",
        "total_trades",
        "win_rate_pct",
        "average_return_pct",
        "profit_factor",
        "payoff_ratio",
        "compounded_return_pct"
    ]

    print(
        result_df[
            display_columns
        ].to_string(
            index=False
        )
    )

    print()
    print(
        f"✅ Đã xuất: {output_path}"
    )


if __name__ == "__main__":
    main()