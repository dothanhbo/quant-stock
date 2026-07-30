import os

import pandas as pd


INPUT_FILE = os.path.join(
    "backtest_results_multi",
    "all_trades.csv"
)

OUTPUT_FOLDER = os.path.join(
    "backtest_results_multi",
    "diagnostics"
)


def load_trades():
    if not os.path.exists(INPUT_FILE):
        raise FileNotFoundError(
            f"Không tìm thấy file: {INPUT_FILE}"
        )

    df = pd.read_csv(INPUT_FILE)

    if df.empty:
        raise ValueError(
            "File all_trades.csv không có dữ liệu."
        )

    required_columns = [
        "symbol",
        "return_pct",
        "exit_reason"
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing_columns:
        raise ValueError(
            f"Thiếu các cột bắt buộc: {missing_columns}"
        )

    df["return_pct"] = pd.to_numeric(
        df["return_pct"],
        errors="coerce"
    )

    df = df.dropna(
        subset=["return_pct"]
    ).reset_index(drop=True)

    return df


def calculate_group_metrics(group):
    total_trades = len(group)

    if total_trades == 0:
        return pd.Series()

    winning_trades = group[
        group["return_pct"] > 0
    ]

    losing_trades = group[
        group["return_pct"] < 0
    ]

    total_wins = len(winning_trades)
    total_losses = len(losing_trades)

    win_rate = (
        total_wins
        / total_trades
        * 100
    )

    average_return = group[
        "return_pct"
    ].mean()

    average_win = (
        winning_trades[
            "return_pct"
        ].mean()
        if total_wins > 0
        else 0.0
    )

    average_loss = (
        losing_trades[
            "return_pct"
        ].mean()
        if total_losses > 0
        else 0.0
    )

    gross_profit = winning_trades[
        "return_pct"
    ].sum()

    gross_loss = abs(
        losing_trades[
            "return_pct"
        ].sum()
    )

    if gross_loss > 0:
        profit_factor = (
            gross_profit / gross_loss
        )
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    payoff_ratio = (
        average_win / abs(average_loss)
        if average_loss != 0
        else 0.0
    )

    return pd.Series({
        "total_trades": total_trades,
        "wins": total_wins,
        "losses": total_losses,
        "win_rate_pct": win_rate,
        "average_return_pct": average_return,
        "average_win_pct": average_win,
        "average_loss_pct": average_loss,
        "profit_factor": profit_factor,
        "payoff_ratio": payoff_ratio,
        "best_trade_pct": group[
            "return_pct"
        ].max(),
        "worst_trade_pct": group[
            "return_pct"
        ].min()
    })


def analyze_by_score(df):
    if "score" not in df.columns:
        print(
            "⚪ Không có cột score, bỏ qua."
        )
        return None

    data = df.copy()

    data["score"] = pd.to_numeric(
        data["score"],
        errors="coerce"
    )

    data = data.dropna(
        subset=["score"]
    )

    bins = [
        float("-inf"),
        59,
        69,
        79,
        89,
        float("inf")
    ]

    labels = [
        "<60",
        "60-69",
        "70-79",
        "80-89",
        ">=90"
    ]

    data["score_group"] = pd.cut(
        data["score"],
        bins=bins,
        labels=labels
    )

    result = (
        data.groupby(
            "score_group",
            observed=False
        )
        .apply(
            calculate_group_metrics,
            include_groups=False
        )
        .reset_index()
    )

    return result


def analyze_by_exit_reason(df):
    return (
        df.groupby(
            "exit_reason"
        )
        .apply(
            calculate_group_metrics,
            include_groups=False
        )
        .reset_index()
    )


def analyze_by_symbol(df):
    result = (
        df.groupby(
            "symbol"
        )
        .apply(
            calculate_group_metrics,
            include_groups=False
        )
        .reset_index()
    )

    result = result.sort_values(
        by=[
            "average_return_pct",
            "profit_factor"
        ],
        ascending=False
    )

    return result


def analyze_numeric_column(
    df,
    column,
    bins,
    labels
):
    if column not in df.columns:
        print(
            f"⚪ Không có cột {column}, bỏ qua."
        )
        return None

    data = df.copy()

    data[column] = pd.to_numeric(
        data[column],
        errors="coerce"
    )

    data = data.dropna(
        subset=[column]
    )

    if data.empty:
        print(
            f"⚪ Cột {column} không có dữ liệu hợp lệ."
        )
        return None

    group_column = (
        f"{column}_group"
    )

    data[group_column] = pd.cut(
        data[column],
        bins=bins,
        labels=labels,
        include_lowest=True
    )

    result = (
        data.groupby(
            group_column,
            observed=False
        )
        .apply(
            calculate_group_metrics,
            include_groups=False
        )
        .reset_index()
    )

    return result


def analyze_holding_days(df):
    if "holding_days" not in df.columns:
        return None

    return analyze_numeric_column(
        df=df,
        column="holding_days",
        bins=[
            0,
            2,
            5,
            10,
            20,
            float("inf")
        ],
        labels=[
            "1-2",
            "3-5",
            "6-10",
            "11-20",
            ">20"
        ]
    )


def export_result(
    dataframe,
    filename
):
    if dataframe is None:
        return

    if dataframe.empty:
        return

    output_path = os.path.join(
        OUTPUT_FOLDER,
        filename
    )

    dataframe.to_csv(
        output_path,
        index=False,
        encoding="utf-8-sig"
    )

    print(
        f"✅ Đã xuất: {output_path}"
    )


def print_table(
    title,
    dataframe
):
    if dataframe is None:
        return

    if dataframe.empty:
        return

    print()
    print("=" * 90)
    print(title)
    print("=" * 90)

    print(
        dataframe.to_string(
            index=False
        )
    )


def run_diagnostics():
    os.makedirs(
        OUTPUT_FOLDER,
        exist_ok=True
    )

    df = load_trades()

    print(
        f"Tổng số giao dịch đọc được: "
        f"{len(df)}"
    )

    score_result = analyze_by_score(
        df
    )

    exit_result = analyze_by_exit_reason(
        df
    )

    symbol_result = analyze_by_symbol(
        df
    )

    rsi_result = analyze_numeric_column(
        df=df,
        column="rsi",
        bins=[
            float("-inf"),
            45,
            50,
            55,
            60,
            65,
            70,
            float("inf")
        ],
        labels=[
            "<45",
            "45-49",
            "50-54",
            "55-59",
            "60-64",
            "65-69",
            ">=70"
        ]
    )

    adx_result = analyze_numeric_column(
        df=df,
        column="adx",
        bins=[
            float("-inf"),
            20,
            25,
            30,
            40,
            float("inf")
        ],
        labels=[
            "<20",
            "20-24",
            "25-29",
            "30-39",
            ">=40"
        ]
    )

    volume_result = analyze_numeric_column(
        df=df,
        column="volume_ratio",
        bins=[
            float("-inf"),
            1.0,
            1.2,
            1.5,
            2.0,
            float("inf")
        ],
        labels=[
            "<1.0",
            "1.0-1.19",
            "1.2-1.49",
            "1.5-1.99",
            ">=2.0"
        ]
    )

    atr_result = analyze_numeric_column(
        df=df,
        column="atr_percent",
        bins=[
            float("-inf"),
            2,
            3,
            4,
            5,
            float("inf")
        ],
        labels=[
            "<2%",
            "2-2.99%",
            "3-3.99%",
            "4-4.99%",
            ">=5%"
        ]
    )

    distance_ema20_result = (
        analyze_numeric_column(
            df=df,
            column="distance_ema20",
            bins=[
                float("-inf"),
                0,
                2,
                4,
                6,
                10,
                float("inf")
            ],
            labels=[
                "<0%",
                "0-1.99%",
                "2-3.99%",
                "4-5.99%",
                "6-9.99%",
                ">=10%"
            ]
        )
    )

    return_3d_result = (
        analyze_numeric_column(
            df=df,
            column="return_3d",
            bins=[
                float("-inf"),
                0,
                3,
                5,
                8,
                12,
                float("inf")
            ],
            labels=[
                "<0%",
                "0-2.99%",
                "3-4.99%",
                "5-7.99%",
                "8-11.99%",
                ">=12%"
            ]
        )
    )

    holding_result = analyze_holding_days(
        df
    )

    top_symbols = (
        symbol_result[
            symbol_result[
                "total_trades"
            ] >= 5
        ]
        .sort_values(
            by="average_return_pct",
            ascending=False
        )
        .head(20)
    )

    worst_symbols = (
        symbol_result[
            symbol_result[
                "total_trades"
            ] >= 5
        ]
        .sort_values(
            by="average_return_pct",
            ascending=True
        )
        .head(20)
    )

    export_result(
        score_result,
        "performance_by_score.csv"
    )

    export_result(
        exit_result,
        "performance_by_exit_reason.csv"
    )

    export_result(
        symbol_result,
        "performance_by_symbol.csv"
    )

    export_result(
        top_symbols,
        "top_symbols.csv"
    )

    export_result(
        worst_symbols,
        "worst_symbols.csv"
    )

    export_result(
        rsi_result,
        "performance_by_rsi.csv"
    )

    export_result(
        adx_result,
        "performance_by_adx.csv"
    )

    export_result(
        volume_result,
        "performance_by_volume_ratio.csv"
    )

    export_result(
        atr_result,
        "performance_by_atr_percent.csv"
    )

    export_result(
        distance_ema20_result,
        "performance_by_distance_ema20.csv"
    )

    export_result(
        return_3d_result,
        "performance_by_return_3d.csv"
    )

    export_result(
        holding_result,
        "performance_by_holding_days.csv"
    )

    print_table(
        "HIỆU QUẢ THEO SCORE",
        score_result
    )

    print_table(
        "HIỆU QUẢ THEO EXIT REASON",
        exit_result
    )

    print_table(
        "TOP SYMBOLS - TỐI THIỂU 5 LỆNH",
        top_symbols
    )

    print_table(
        "WORST SYMBOLS - TỐI THIỂU 5 LỆNH",
        worst_symbols
    )


if __name__ == "__main__":
    run_diagnostics()