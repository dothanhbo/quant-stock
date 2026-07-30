"""
Tối ưu tham số thoát lệnh cho Backtest Engine V4.2.

Chạy:
    py -m backtesting.optimize_exit

Mặc định thử 64 cấu hình:
    SL   : 3%, 4%, 5%, 6%
    TP   : 6%, 8%, 10%, 12%
    Hold : 10, 15, 20, 30 phiên
    ADX  : 30

Kết quả:
    backtest_results_optimized/
    └── exit_optimization/
        ├── exit_grid_results.csv
        └── top_exit_configs.csv

Có checkpoint: nếu bị dừng giữa chừng, chạy lại sẽ bỏ qua các cấu hình đã hoàn thành.
"""

from __future__ import annotations

import argparse
import itertools
import math
import time
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from backtesting.engine import run_backtest
except ImportError as exc:
    raise ImportError(
        "Không import được run_backtest từ backtest_engine.py. "
        "Hãy đặt optimize_exit.py cùng thư mục với backtest_engine.py."
    ) from exc


DEFAULT_OUTPUT_DIR = Path(
    "backtest_results_optimized"
) / "exit_optimization"

DEFAULT_SL_VALUES = [3.0, 4.0, 5.0, 6.0]
DEFAULT_TP_VALUES = [6.0, 8.0, 10.0, 12.0]
DEFAULT_HOLD_VALUES = [10, 15, 20, 30]
DEFAULT_ADX_VALUES = [30.0]


def parse_number_list(
    raw_value: str,
    *,
    cast_type: type = float,
) -> list:
    values = []

    for item in raw_value.split(","):
        item = item.strip()
        if not item:
            continue

        try:
            values.append(cast_type(item))
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"Giá trị không hợp lệ: {item}"
            ) from exc

    if not values:
        raise argparse.ArgumentTypeError(
            "Danh sách tham số không được để trống."
        )

    return values


def safe_metric(
    metrics: dict[str, Any],
    key: str,
    default: float = 0.0,
) -> float:
    try:
        value = float(metrics.get(key, default))
    except (TypeError, ValueError):
        return default

    if not math.isfinite(value):
        return 999999.0 if value > 0 else default

    return value


def config_key(
    sl: float,
    tp: float,
    hold: int,
    adx: float,
) -> tuple[float, float, int, float]:
    return (
        round(float(sl), 6),
        round(float(tp), 6),
        int(hold),
        round(float(adx), 6),
    )


def load_completed_configs(
    results_path: Path,
) -> tuple[pd.DataFrame, set[tuple[float, float, int, float]]]:
    if not results_path.exists():
        return pd.DataFrame(), set()

    try:
        existing = pd.read_csv(results_path)
    except Exception as exc:
        print(
            f"⚠️ Không đọc được checkpoint cũ: {exc}. "
            "Chương trình sẽ chạy lại từ đầu."
        )
        return pd.DataFrame(), set()

    required = {
        "stop_loss_pct",
        "take_profit_pct",
        "max_holding_days",
        "min_adx",
    }

    if existing.empty or not required.issubset(existing.columns):
        return pd.DataFrame(), set()

    completed = {
        config_key(
            row["stop_loss_pct"],
            row["take_profit_pct"],
            row["max_holding_days"],
            row["min_adx"],
        )
        for _, row in existing.iterrows()
    }

    return existing, completed


def rank_results(results: pd.DataFrame) -> pd.DataFrame:
    if results.empty:
        return results

    ranked = results.copy()

    numeric_columns = [
        "total_trades",
        "win_rate_pct",
        "average_return_pct",
        "profit_factor",
        "payoff_ratio",
        "average_holding_days",
        "max_drawdown_pct",
        "final_capital",
    ]

    for column in numeric_columns:
        if column in ranked.columns:
            ranked[column] = pd.to_numeric(
                ranked[column],
                errors="coerce",
            )

    # Không ưu tiên cấu hình có quá ít giao dịch.
    ranked["enough_trades"] = (
        ranked["total_trades"].fillna(0) >= 80
    )

    # Xếp hạng chính:
    # 1. Có ít nhất 80 lệnh
    # 2. Profit Factor cao
    # 3. Return trung bình cao
    # 4. Nhiều giao dịch hơn để giảm rủi ro overfit
    ranked = ranked.sort_values(
        by=[
            "enough_trades",
            "profit_factor",
            "average_return_pct",
            "total_trades",
        ],
        ascending=[False, False, False, False],
    ).reset_index(drop=True)

    ranked.insert(0, "rank", range(1, len(ranked) + 1))
    return ranked


def run_optimizer(
    *,
    sl_values: list[float],
    tp_values: list[float],
    hold_values: list[int],
    adx_values: list[float],
    db_path: str,
    output_dir: Path,
    min_trades: int,
    top_n: int,
    verbose_backtest: bool,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    results_path = output_dir / "exit_grid_results.csv"
    top_path = output_dir / "top_exit_configs.csv"

    existing_df, completed = load_completed_configs(results_path)

    combinations = list(
        itertools.product(
            adx_values,
            sl_values,
            tp_values,
            hold_values,
        )
    )

    total = len(combinations)
    pending = [
        combo
        for combo in combinations
        if config_key(
            sl=combo[1],
            tp=combo[2],
            hold=combo[3],
            adx=combo[0],
        ) not in completed
    ]

    print("=" * 76)
    print("EXIT OPTIMIZER V1.0")
    print("=" * 76)
    print(f"Tổng cấu hình trong lưới : {total}")
    print(f"Đã hoàn thành trước đó    : {len(completed)}")
    print(f"Còn phải chạy             : {len(pending)}")
    print(f"Output                    : {output_dir.resolve()}")
    print("-" * 76)

    new_rows: list[dict[str, Any]] = []
    overall_start = time.perf_counter()

    for run_number, (adx, sl, tp, hold) in enumerate(
        pending,
        start=1,
    ):
        config_start = time.perf_counter()

        print(
            f"[{run_number}/{len(pending)}] "
            f"ADX>={adx:g} | SL={sl:g}% | "
            f"TP={tp:g}% | Hold={hold}"
        )

        try:
            trades, metrics, equity_curve = run_backtest(
                symbols=None,
                stop_loss_pct=sl,
                take_profit_pct=tp,
                max_holding_days=hold,
                min_adx=adx,
                db_path=db_path,
                verbose=verbose_backtest,
            )

            max_drawdown = 0.0
            final_capital = 0.0

            if (
                equity_curve is not None
                and not equity_curve.empty
            ):
                if "drawdown_pct" in equity_curve.columns:
                    max_drawdown = float(
                        pd.to_numeric(
                            equity_curve["drawdown_pct"],
                            errors="coerce",
                        ).min()
                    )

                if "equity" in equity_curve.columns:
                    final_capital = float(
                        pd.to_numeric(
                            equity_curve["equity"],
                            errors="coerce",
                        ).iloc[-1]
                    )

            elapsed = time.perf_counter() - config_start

            row = {
                "stop_loss_pct": sl,
                "take_profit_pct": tp,
                "max_holding_days": hold,
                "min_adx": adx,
                "total_trades": int(
                    metrics.get("total_trades", len(trades))
                ),
                "wins": int(metrics.get("wins", 0)),
                "losses": int(metrics.get("losses", 0)),
                "win_rate_pct": safe_metric(
                    metrics,
                    "win_rate_pct",
                ),
                "average_return_pct": safe_metric(
                    metrics,
                    "average_return_pct",
                ),
                "profit_factor": safe_metric(
                    metrics,
                    "profit_factor",
                ),
                "payoff_ratio": safe_metric(
                    metrics,
                    "payoff_ratio",
                ),
                "average_win_pct": safe_metric(
                    metrics,
                    "average_win_pct",
                ),
                "average_loss_pct": safe_metric(
                    metrics,
                    "average_loss_pct",
                ),
                "average_holding_days": safe_metric(
                    metrics,
                    "average_holding_days",
                ),
                "max_drawdown_pct": max_drawdown,
                "final_capital": final_capital,
                "runtime_seconds": round(elapsed, 2),
                "status": "OK",
                "error": "",
            }

            print(
                f"    Trades={row['total_trades']} | "
                f"WR={row['win_rate_pct']:.2f}% | "
                f"Avg={row['average_return_pct']:+.2f}% | "
                f"PF={row['profit_factor']:.2f} | "
                f"Thời gian={elapsed:.1f}s"
            )

        except KeyboardInterrupt:
            print("\n⏹️ Đã dừng theo yêu cầu.")
            break

        except Exception as exc:
            elapsed = time.perf_counter() - config_start
            row = {
                "stop_loss_pct": sl,
                "take_profit_pct": tp,
                "max_holding_days": hold,
                "min_adx": adx,
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate_pct": 0.0,
                "average_return_pct": 0.0,
                "profit_factor": 0.0,
                "payoff_ratio": 0.0,
                "average_win_pct": 0.0,
                "average_loss_pct": 0.0,
                "average_holding_days": 0.0,
                "max_drawdown_pct": 0.0,
                "final_capital": 0.0,
                "runtime_seconds": round(elapsed, 2),
                "status": "ERROR",
                "error": str(exc),
            }
            print(f"    ❌ Lỗi: {exc}")

        new_rows.append(row)

        # Ghi checkpoint sau từng cấu hình.
        combined = pd.concat(
            [
                existing_df,
                pd.DataFrame(new_rows),
            ],
            ignore_index=True,
        )

        combined = combined.drop_duplicates(
            subset=[
                "stop_loss_pct",
                "take_profit_pct",
                "max_holding_days",
                "min_adx",
            ],
            keep="last",
        )

        combined.to_csv(
            results_path,
            index=False,
            encoding="utf-8-sig",
        )

    if results_path.exists():
        results = pd.read_csv(results_path)
    else:
        results = pd.DataFrame()

    successful = results[
        results.get(
            "status",
            pd.Series(index=results.index, dtype=str),
        ).fillna("OK") == "OK"
    ].copy()

    if successful.empty:
        print("\nKhông có cấu hình nào chạy thành công.")
        return

    ranked = rank_results(successful)

    # Dùng min_trades do người dùng chọn để tạo Top.
    qualified = ranked[
        ranked["total_trades"] >= min_trades
    ].copy()

    if qualified.empty:
        print(
            f"\n⚠️ Không có cấu hình nào đạt tối thiểu "
            f"{min_trades} giao dịch. "
            "Top sẽ lấy theo toàn bộ kết quả."
        )
        qualified = ranked.copy()

    top_results = qualified.head(top_n)
    top_results.to_csv(
        top_path,
        index=False,
        encoding="utf-8-sig",
    )

    total_elapsed = time.perf_counter() - overall_start

    print("\n" + "=" * 76)
    print("TOP CẤU HÌNH")
    print("=" * 76)

    display_columns = [
        "rank",
        "min_adx",
        "stop_loss_pct",
        "take_profit_pct",
        "max_holding_days",
        "total_trades",
        "win_rate_pct",
        "average_return_pct",
        "profit_factor",
        "payoff_ratio",
        "max_drawdown_pct",
    ]

    print(
        top_results[
            [c for c in display_columns if c in top_results.columns]
        ].to_string(index=False)
    )

    print("-" * 76)
    print(f"Thời gian lượt chạy này : {total_elapsed / 60:.1f} phút")
    print(f"Toàn bộ kết quả         : {results_path.resolve()}")
    print(f"Top {top_n} cấu hình       : {top_path.resolve()}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Tối ưu SL, TP, Holding và ADX.",
    )

    parser.add_argument(
        "--sl",
        default=",".join(str(x) for x in DEFAULT_SL_VALUES),
        help="Danh sách SL, ví dụ: 3,4,5,6",
    )
    parser.add_argument(
        "--tp",
        default=",".join(str(x) for x in DEFAULT_TP_VALUES),
        help="Danh sách TP, ví dụ: 6,8,10,12",
    )
    parser.add_argument(
        "--hold",
        default=",".join(str(x) for x in DEFAULT_HOLD_VALUES),
        help="Danh sách số phiên giữ, ví dụ: 10,15,20,30",
    )
    parser.add_argument(
        "--adx",
        default=",".join(str(x) for x in DEFAULT_ADX_VALUES),
        help="Danh sách ADX, ví dụ: 30 hoặc 20,25,30",
    )
    parser.add_argument(
        "--db",
        default="market.db",
        help="Đường dẫn SQLite database.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Thư mục xuất kết quả.",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=80,
        help="Số giao dịch tối thiểu để vào bảng Top.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Số cấu hình tốt nhất cần xuất.",
    )
    parser.add_argument(
        "--verbose-backtest",
        action="store_true",
        help="In từng giao dịch. Không khuyến nghị vì output rất dài.",
    )

    return parser


def main() -> None:
    args = build_parser().parse_args()

    sl_values = parse_number_list(args.sl, cast_type=float)
    tp_values = parse_number_list(args.tp, cast_type=float)
    hold_values = parse_number_list(args.hold, cast_type=int)
    adx_values = parse_number_list(args.adx, cast_type=float)

    run_optimizer(
        sl_values=sl_values,
        tp_values=tp_values,
        hold_values=hold_values,
        adx_values=adx_values,
        db_path=args.db,
        output_dir=Path(args.output_dir),
        min_trades=args.min_trades,
        top_n=args.top,
        verbose_backtest=args.verbose_backtest,
    )


if __name__ == "__main__":
    main()
