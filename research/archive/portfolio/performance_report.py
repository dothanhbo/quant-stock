from __future__ import annotations

import math

import pandas as pd
from sqlalchemy import text

from core.database import engine


# ==========================================
# CẤU HÌNH
# ==========================================

CLOSED_STATUSES = [
    "WIN",
    "LOSS",
    "EXPIRED",
    "AMBIGUOUS"
]


# ==========================================
# ĐỌC DỮ LIỆU TÍN HIỆU
# ==========================================

def load_all_signals() -> pd.DataFrame:
    query = text("""
        SELECT *
        FROM signals
        ORDER BY signal_date ASC, id ASC
    """)

    return pd.read_sql(
        query,
        engine
    )


def load_closed_signals() -> pd.DataFrame:
    query = text("""
        SELECT *
        FROM signals
        WHERE status IN (
            'WIN',
            'LOSS',
            'EXPIRED',
            'AMBIGUOUS'
        )
        ORDER BY exit_date ASC, id ASC
    """)

    return pd.read_sql(
        query,
        engine
    )


# ==========================================
# HÀM PHỤ
# ==========================================

def safe_number(value, default=0.0) -> float:
    try:
        number = float(value)

        if math.isnan(number):
            return default

        return number

    except (TypeError, ValueError):
        return default


def format_pct(value) -> str:
    return f"{safe_number(value):+.2f}%"


# ==========================================
# TÍNH CHỈ SỐ HIỆU SUẤT
# ==========================================

def calculate_performance(
    closed_df: pd.DataFrame
) -> dict:
    if closed_df.empty:
        return {
            "total_closed": 0,
            "wins": 0,
            "losses": 0,
            "expired": 0,
            "ambiguous": 0,
            "win_rate": 0.0,
            "average_result": 0.0,
            "average_win": 0.0,
            "average_loss": 0.0,
            "expectancy": 0.0,
            "profit_factor": 0.0,
            "average_holding_days": 0.0,
            "best_trade": 0.0,
            "worst_trade": 0.0
        }

    data = closed_df.copy()

    data["result"] = pd.to_numeric(
        data["result"],
        errors="coerce"
    )

    data["holding_days"] = pd.to_numeric(
        data["holding_days"],
        errors="coerce"
    )

    valid_results = data.dropna(
        subset=["result"]
    )

    wins_df = valid_results[
        valid_results["result"] > 0
    ]

    losses_df = valid_results[
        valid_results["result"] < 0
    ]

    total_closed = len(data)
    wins = len(data[data["status"] == "WIN"])
    losses = len(data[data["status"] == "LOSS"])
    expired = len(data[data["status"] == "EXPIRED"])
    ambiguous = len(
        data[data["status"] == "AMBIGUOUS"]
    )

    decisive_trades = wins + losses

    win_rate = (
        wins / decisive_trades * 100
        if decisive_trades > 0
        else 0.0
    )

    average_result = (
        valid_results["result"].mean()
        if not valid_results.empty
        else 0.0
    )

    average_win = (
        wins_df["result"].mean()
        if not wins_df.empty
        else 0.0
    )

    average_loss = (
        losses_df["result"].mean()
        if not losses_df.empty
        else 0.0
    )

    probability_win = (
        len(wins_df) / len(valid_results)
        if len(valid_results) > 0
        else 0.0
    )

    probability_loss = (
        len(losses_df) / len(valid_results)
        if len(valid_results) > 0
        else 0.0
    )

    expectancy = (
        probability_win * average_win
        +
        probability_loss * average_loss
    )

    gross_profit = (
        wins_df["result"].sum()
        if not wins_df.empty
        else 0.0
    )

    gross_loss = abs(
        losses_df["result"].sum()
        if not losses_df.empty
        else 0.0
    )

    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    average_holding_days = (
        data["holding_days"].mean()
        if data["holding_days"].notna().any()
        else 0.0
    )

    best_trade = (
        valid_results["result"].max()
        if not valid_results.empty
        else 0.0
    )

    worst_trade = (
        valid_results["result"].min()
        if not valid_results.empty
        else 0.0
    )

    return {
        "total_closed": total_closed,
        "wins": wins,
        "losses": losses,
        "expired": expired,
        "ambiguous": ambiguous,
        "win_rate": round(win_rate, 2),
        "average_result": round(
            average_result,
            2
        ),
        "average_win": round(
            average_win,
            2
        ),
        "average_loss": round(
            average_loss,
            2
        ),
        "expectancy": round(
            expectancy,
            2
        ),
        "profit_factor": (
            round(profit_factor, 2)
            if math.isfinite(profit_factor)
            else float("inf")
        ),
        "average_holding_days": round(
            average_holding_days,
            2
        ),
        "best_trade": round(
            best_trade,
            2
        ),
        "worst_trade": round(
            worst_trade,
            2
        )
    }


# ==========================================
# PHÂN TÍCH THEO NHÓM ĐIỂM
# ==========================================

def performance_by_score(
    closed_df: pd.DataFrame
) -> pd.DataFrame:
    if closed_df.empty:
        return pd.DataFrame()

    data = closed_df.copy()

    data["score"] = pd.to_numeric(
        data["score"],
        errors="coerce"
    )

    data["result"] = pd.to_numeric(
        data["result"],
        errors="coerce"
    )

    data = data.dropna(
        subset=["score", "result"]
    )

    if data.empty:
        return pd.DataFrame()

    data["score_group"] = pd.cut(
        data["score"],
        bins=[
            0,
            54,
            64,
            74,
            84,
            100
        ],
        labels=[
            "0-54",
            "55-64",
            "65-74",
            "75-84",
            "85-100"
        ],
        include_lowest=True
    )

    report = (
        data
        .groupby(
            "score_group",
            observed=True
        )
        .agg(
            signals=("id", "count"),
            average_result=("result", "mean"),
            win_rate=(
                "result",
                lambda values: (
                    (values > 0).mean() * 100
                )
            ),
            best_result=("result", "max"),
            worst_result=("result", "min")
        )
        .reset_index()
    )

    numeric_columns = [
        "average_result",
        "win_rate",
        "best_result",
        "worst_result"
    ]

    report[numeric_columns] = (
        report[numeric_columns]
        .round(2)
    )

    return report


# ==========================================
# PHÂN TÍCH RELATIVE STRENGTH
# ==========================================

def performance_by_relative_strength(
    closed_df: pd.DataFrame
) -> pd.DataFrame:
    if (
        closed_df.empty
        or "relative_strength"
        not in closed_df.columns
    ):
        return pd.DataFrame()

    data = closed_df.copy()

    data["relative_strength"] = pd.to_numeric(
        data["relative_strength"],
        errors="coerce"
    )

    data["result"] = pd.to_numeric(
        data["result"],
        errors="coerce"
    )

    data = data.dropna(
        subset=[
            "relative_strength",
            "result"
        ]
    )

    if data.empty:
        return pd.DataFrame()

    data["rs_group"] = pd.cut(
        data["relative_strength"],
        bins=[
            float("-inf"),
            0,
            2,
            5,
            10,
            float("inf")
        ],
        labels=[
            "RS < 0",
            "0 đến 2",
            "2 đến 5",
            "5 đến 10",
            "RS > 10"
        ],
        right=False
    )

    report = (
        data
        .groupby(
            "rs_group",
            observed=True
        )
        .agg(
            signals=("id", "count"),
            average_result=("result", "mean"),
            win_rate=(
                "result",
                lambda values: (
                    (values > 0).mean() * 100
                )
            )
        )
        .reset_index()
    )

    report[
        [
            "average_result",
            "win_rate"
        ]
    ] = report[
        [
            "average_result",
            "win_rate"
        ]
    ].round(2)

    return report


# ==========================================
# IN BÁO CÁO
# ==========================================

def print_performance_report() -> None:
    all_signals = load_all_signals()
    closed_signals = load_closed_signals()

    metrics = calculate_performance(
        closed_signals
    )

    open_count = len(
        all_signals[
            all_signals["status"] == "OPEN"
        ]
    ) if not all_signals.empty else 0

    print("\n" + "=" * 65)
    print("📈 BÁO CÁO HIỆU SUẤT QUANT BOT")
    print("=" * 65)

    print(
        f"Tổng tín hiệu đã lưu: "
        f"{len(all_signals)}"
    )

    print(
        f"Tín hiệu đang OPEN: "
        f"{open_count}"
    )

    print(
        f"Tín hiệu đã đóng: "
        f"{metrics['total_closed']}"
    )

    if metrics["total_closed"] == 0:
        print(
            "\nChưa có đủ tín hiệu đã đóng "
            "để tính hiệu suất."
        )
        return

    print("\n---------- KẾT QUẢ ----------")

    print(f"WIN: {metrics['wins']}")
    print(f"LOSS: {metrics['losses']}")
    print(f"EXPIRED: {metrics['expired']}")
    print(
        f"AMBIGUOUS: "
        f"{metrics['ambiguous']}"
    )

    print(
        f"Win rate: "
        f"{metrics['win_rate']:.2f}%"
    )

    print(
        f"Lợi nhuận trung bình: "
        f"{format_pct(metrics['average_result'])}"
    )

    print(
        f"Lệnh thắng trung bình: "
        f"{format_pct(metrics['average_win'])}"
    )

    print(
        f"Lệnh thua trung bình: "
        f"{format_pct(metrics['average_loss'])}"
    )

    print(
        f"Expectancy mỗi tín hiệu: "
        f"{format_pct(metrics['expectancy'])}"
    )

    profit_factor = metrics["profit_factor"]

    if math.isinf(profit_factor):
        profit_factor_text = "∞"
    else:
        profit_factor_text = (
            f"{profit_factor:.2f}"
        )

    print(
        f"Profit Factor: "
        f"{profit_factor_text}"
    )

    print(
        f"Thời gian giữ trung bình: "
        f"{metrics['average_holding_days']:.2f} "
        f"phiên"
    )

    print(
        f"Lệnh tốt nhất: "
        f"{format_pct(metrics['best_trade'])}"
    )

    print(
        f"Lệnh xấu nhất: "
        f"{format_pct(metrics['worst_trade'])}"
    )

    score_report = performance_by_score(
        closed_signals
    )

    if not score_report.empty:
        print("\n---------- THEO SCORE ----------")
        print(
            score_report.to_string(
                index=False
            )
        )

    rs_report = (
        performance_by_relative_strength(
            closed_signals
        )
    )

    if not rs_report.empty:
        print(
            "\n---------- "
            "THEO RELATIVE STRENGTH ----------"
        )

        print(
            rs_report.to_string(
                index=False
            )
        )


if __name__ == "__main__":
    print_performance_report()