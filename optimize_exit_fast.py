"""
Fast Exit Optimizer V2.0
========================

Mục tiêu:
- Quét tín hiệu lịch sử của 100 mã đúng 1 lần.
- Lưu cache tín hiệu.
- Replay nhiều bộ SL/TP/Hold rất nhanh.
- Vẫn giữ quy tắc không chồng lệnh trên cùng một mã.

Đặt cùng thư mục với:
    backtest_engine.py
    scanner.py
    market.db

Chạy:
    py optimize_exit_fast.py

Kết quả:
    backtest_results_optimized/
    └── exit_optimization_fast/
        ├── signal_cache.pkl
        ├── exit_grid_results.csv
        └── top_exit_configs.csv
"""

from __future__ import annotations

import argparse
import itertools
import math
import pickle
import time
from pathlib import Path
from typing import Any

import pandas as pd

from scanner import check_signal
from backtest_engine import (
    BacktestConfig,
    build_equity_curve,
    calculate_metrics,
    get_symbol_list,
    load_price_data,
    _simulate_exit,
)


DEFAULT_OUTPUT_DIR = (
    Path("backtest_results_optimized")
    / "exit_optimization_fast"
)

DEFAULT_SL_VALUES = [3.0, 4.0, 5.0, 6.0]
DEFAULT_TP_VALUES = [6.0, 8.0, 10.0, 12.0]
DEFAULT_HOLD_VALUES = [10, 15, 20, 30]
DEFAULT_ADX_VALUES = [30.0]


def parse_list(
    raw: str,
    cast_type: type,
) -> list:
    values = []

    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        values.append(cast_type(item))

    if not values:
        raise ValueError("Danh sách tham số không được rỗng.")

    return values


def safe_float(
    value: Any,
    default: float = math.nan,
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default

    return number if math.isfinite(number) else default


def get_signal_value(
    signal: dict[str, Any],
    keys: tuple[str, ...],
    default: float = math.nan,
) -> float:
    for key in keys:
        if key in signal:
            return safe_float(signal[key], default)

    return default


def call_historical_signal(
    symbol: str,
    signal_date: pd.Timestamp,
) -> dict[str, Any] | None:
    date_text = signal_date.strftime("%Y-%m-%d")

    # Quan trọng: truyền cả reference_date và end_date
    # để scanner chỉ nhìn dữ liệu đến đúng ngày lịch sử.
    try:
        result = check_signal(
            symbol,
            reference_date=date_text,
            end_date=date_text,
        )
    except TypeError:
        # Fallback nếu scanner cũ chưa có end_date.
        result = check_signal(
            symbol,
            reference_date=date_text,
        )

    if result is None:
        return None

    if not isinstance(result, dict):
        raise TypeError(
            f"check_signal({symbol}) phải trả về dict hoặc None."
        )

    return result


def build_signal_cache(
    *,
    symbols: list[str],
    db_path: str,
    warmup_bars: int,
    cache_path: Path,
    force_rebuild: bool,
) -> dict[str, dict[str, Any]]:
    if cache_path.exists() and not force_rebuild:
        print(f"✅ Đang dùng cache có sẵn: {cache_path}")
        with cache_path.open("rb") as file:
            return pickle.load(file)

    print("=" * 76)
    print("GIAI ĐOẠN 1: QUÉT TÍN HIỆU MỘT LẦN")
    print("=" * 76)

    cache: dict[str, dict[str, Any]] = {}
    started = time.perf_counter()

    for symbol_number, symbol in enumerate(symbols, start=1):
        symbol_started = time.perf_counter()
        price_df = load_price_data(symbol, db_path)

        candidates: list[dict[str, Any]] = []

        if len(price_df) > warmup_bars + 1:
            for signal_index in range(
                warmup_bars,
                len(price_df) - 1,
            ):
                signal_date = pd.Timestamp(
                    price_df.iloc[signal_index]["time"]
                )

                try:
                    signal = call_historical_signal(
                        symbol,
                        signal_date,
                    )
                except Exception as exc:
                    print(
                        f"⚠️ {symbol} {signal_date.date()}: {exc}"
                    )
                    continue

                if not signal:
                    continue

                candidates.append(
                    {
                        "signal_index": signal_index,
                        "signal_date": signal_date,
                        "score": get_signal_value(
                            signal,
                            ("score", "Score"),
                        ),
                        "adx": get_signal_value(
                            signal,
                            ("adx", "ADX", "ADX14", "adx14"),
                        ),
                        "rsi": get_signal_value(
                            signal,
                            ("rsi", "RSI"),
                        ),
                        "volume_ratio": get_signal_value(
                            signal,
                            (
                                "volume_ratio",
                                "Vol_Ratio",
                                "vol_ratio",
                            ),
                        ),
                        "distance_ema20": get_signal_value(
                            signal,
                            (
                                "distance_ema20",
                                "Distance_EMA20_Pct",
                            ),
                        ),
                        "atr_percent": get_signal_value(
                            signal,
                            (
                                "atr_percent",
                                "ATR_Pct",
                                "atr_pct",
                            ),
                        ),
                        "return_3d": get_signal_value(
                            signal,
                            (
                                "return_3d",
                                "Return_3D_Pct",
                            ),
                        ),
                    }
                )

        cache[symbol] = {
            "prices": price_df,
            "signals": candidates,
        }

        elapsed = time.perf_counter() - symbol_started
        print(
            f"[{symbol_number}/{len(symbols)}] "
            f"{symbol}: {len(candidates)} tín hiệu "
            f"| {elapsed:.1f}s"
        )

        # Checkpoint sau từng mã.
        cache_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        with cache_path.open("wb") as file:
            pickle.dump(
                cache,
                file,
                protocol=pickle.HIGHEST_PROTOCOL,
            )

    total_elapsed = time.perf_counter() - started
    print(
        f"\n✅ Quét xong {len(symbols)} mã trong "
        f"{total_elapsed / 60:.1f} phút."
    )
    print(f"✅ Cache: {cache_path.resolve()}")

    return cache


def replay_symbol(
    *,
    symbol: str,
    price_df: pd.DataFrame,
    candidates: list[dict[str, Any]],
    config: BacktestConfig,
) -> pd.DataFrame:
    if price_df.empty or not candidates:
        return pd.DataFrame()

    trades: list[dict[str, Any]] = []
    next_allowed_signal_index = 0

    for candidate in candidates:
        signal_index = int(candidate["signal_index"])

        if signal_index < next_allowed_signal_index:
            continue

        adx = safe_float(candidate.get("adx"))

        if not math.isfinite(adx):
            continue

        if adx < config.min_adx:
            continue

        entry_index = signal_index + 1

        if entry_index >= len(price_df):
            continue

        exit_info = _simulate_exit(
            price_df,
            entry_index,
            config,
        )

        trades.append(
            {
                "symbol": symbol,
                "signal_date": candidate["signal_date"],
                "entry_date": exit_info["entry_date"],
                "exit_date": exit_info["exit_date"],
                "score": candidate.get("score"),
                "adx": adx,
                "rsi": candidate.get("rsi"),
                "volume_ratio": candidate.get(
                    "volume_ratio"
                ),
                "distance_ema20": candidate.get(
                    "distance_ema20"
                ),
                "atr_percent": candidate.get(
                    "atr_percent"
                ),
                "return_3d": candidate.get("return_3d"),
                "entry_price": exit_info["entry_price"],
                "exit_price": exit_info["exit_price"],
                "stop_price": exit_info["stop_price"],
                "target_price": exit_info["target_price"],
                "stop_loss_pct": config.stop_loss_pct,
                "take_profit_pct": config.take_profit_pct,
                "max_holding_days": (
                    config.max_holding_days
                ),
                "min_adx": config.min_adx,
                "holding_days": exit_info["holding_days"],
                "exit_reason": exit_info["exit_reason"],
                "return_pct": exit_info["return_pct"],
            }
        )

        # Không chồng lệnh trên cùng mã.
        next_allowed_signal_index = (
            int(exit_info["exit_index"]) + 1
        )

    return pd.DataFrame(trades)


def replay_config(
    cache: dict[str, dict[str, Any]],
    config: BacktestConfig,
) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    all_trades: list[pd.DataFrame] = []

    for symbol, symbol_data in cache.items():
        symbol_trades = replay_symbol(
            symbol=symbol,
            price_df=symbol_data["prices"],
            candidates=symbol_data["signals"],
            config=config,
        )

        if not symbol_trades.empty:
            all_trades.append(symbol_trades)

    trades = (
        pd.concat(all_trades, ignore_index=True)
        if all_trades
        else pd.DataFrame()
    )

    if not trades.empty:
        trades = trades.sort_values(
            ["signal_date", "symbol"]
        ).reset_index(drop=True)

    metrics = calculate_metrics(trades, config)
    equity = build_equity_curve(trades, config)

    if (
        not equity.empty
        and "drawdown_pct" in equity.columns
    ):
        metrics["max_drawdown_pct"] = float(
            equity["drawdown_pct"].min()
        )
        metrics["final_equity"] = float(
            equity["equity"].iloc[-1]
        )
    else:
        metrics["max_drawdown_pct"] = 0.0
        metrics["final_equity"] = (
            config.initial_capital
        )

    return trades, metrics, equity


def rank_results(
    results: pd.DataFrame,
    min_trades: int,
) -> pd.DataFrame:
    ranked = results.copy()

    numeric_columns = [
        "total_trades",
        "win_rate_pct",
        "average_return_pct",
        "profit_factor",
        "payoff_ratio",
        "max_drawdown_pct",
    ]

    for column in numeric_columns:
        ranked[column] = pd.to_numeric(
            ranked[column],
            errors="coerce",
        )

    ranked["qualified"] = (
        ranked["total_trades"] >= min_trades
    )

    ranked = ranked.sort_values(
        by=[
            "qualified",
            "profit_factor",
            "average_return_pct",
            "total_trades",
        ],
        ascending=[False, False, False, False],
    ).reset_index(drop=True)

    ranked.insert(
        0,
        "rank",
        range(1, len(ranked) + 1),
    )

    return ranked


def run_grid(
    *,
    cache: dict[str, dict[str, Any]],
    sl_values: list[float],
    tp_values: list[float],
    hold_values: list[int],
    adx_values: list[float],
    output_dir: Path,
    min_trades: int,
    top_n: int,
) -> None:
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    results_path = (
        output_dir / "exit_grid_results.csv"
    )
    top_path = (
        output_dir / "top_exit_configs.csv"
    )

    combinations = list(
        itertools.product(
            adx_values,
            sl_values,
            tp_values,
            hold_values,
        )
    )

    existing = pd.DataFrame()
    completed: set[tuple] = set()

    if results_path.exists():
        try:
            existing = pd.read_csv(results_path)

            for _, row in existing.iterrows():
                completed.add(
                    (
                        float(row["min_adx"]),
                        float(row["stop_loss_pct"]),
                        float(row["take_profit_pct"]),
                        int(row["max_holding_days"]),
                    )
                )
        except Exception:
            existing = pd.DataFrame()
            completed = set()

    pending = [
        combo
        for combo in combinations
        if (
            float(combo[0]),
            float(combo[1]),
            float(combo[2]),
            int(combo[3]),
        ) not in completed
    ]

    print("\n" + "=" * 76)
    print("GIAI ĐOẠN 2: REPLAY EXIT")
    print("=" * 76)
    print(f"Tổng cấu hình : {len(combinations)}")
    print(f"Đã hoàn thành : {len(completed)}")
    print(f"Còn lại       : {len(pending)}")
    print("-" * 76)

    new_rows: list[dict[str, Any]] = []
    started = time.perf_counter()

    for number, (adx, sl, tp, hold) in enumerate(
        pending,
        start=1,
    ):
        config_started = time.perf_counter()

        config = BacktestConfig(
            stop_loss_pct=sl,
            take_profit_pct=tp,
            max_holding_days=hold,
            min_adx=adx,
        )

        try:
            _, metrics, _ = replay_config(
                cache,
                config,
            )

            elapsed = (
                time.perf_counter()
                - config_started
            )

            row = {
                "min_adx": adx,
                "stop_loss_pct": sl,
                "take_profit_pct": tp,
                "max_holding_days": hold,
                "total_trades": metrics[
                    "total_trades"
                ],
                "wins": metrics["wins"],
                "losses": metrics["losses"],
                "win_rate_pct": metrics[
                    "win_rate_pct"
                ],
                "average_return_pct": metrics[
                    "average_return_pct"
                ],
                "average_win_pct": metrics[
                    "average_win_pct"
                ],
                "average_loss_pct": metrics[
                    "average_loss_pct"
                ],
                "profit_factor": metrics[
                    "profit_factor"
                ],
                "payoff_ratio": metrics[
                    "payoff_ratio"
                ],
                "average_holding_days": metrics[
                    "average_holding_days"
                ],
                "max_drawdown_pct": metrics[
                    "max_drawdown_pct"
                ],
                "final_equity": metrics[
                    "final_equity"
                ],
                "runtime_seconds": round(
                    elapsed,
                    3,
                ),
                "status": "OK",
                "error": "",
            }

            print(
                f"[{number}/{len(pending)}] "
                f"ADX>={adx:g} SL={sl:g}% "
                f"TP={tp:g}% H={hold} | "
                f"Trades={row['total_trades']} | "
                f"WR={row['win_rate_pct']:.2f}% | "
                f"Avg={row['average_return_pct']:+.2f}% | "
                f"PF={row['profit_factor']:.2f} | "
                f"{elapsed:.2f}s"
            )

        except KeyboardInterrupt:
            print("\n⏹️ Đã dừng theo yêu cầu.")
            break

        except Exception as exc:
            row = {
                "min_adx": adx,
                "stop_loss_pct": sl,
                "take_profit_pct": tp,
                "max_holding_days": hold,
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate_pct": 0.0,
                "average_return_pct": 0.0,
                "average_win_pct": 0.0,
                "average_loss_pct": 0.0,
                "profit_factor": 0.0,
                "payoff_ratio": 0.0,
                "average_holding_days": 0.0,
                "max_drawdown_pct": 0.0,
                "final_equity": 0.0,
                "runtime_seconds": 0.0,
                "status": "ERROR",
                "error": str(exc),
            }
            print(
                f"[{number}/{len(pending)}] ❌ {exc}"
            )

        new_rows.append(row)

        combined = pd.concat(
            [
                existing,
                pd.DataFrame(new_rows),
            ],
            ignore_index=True,
        )

        combined = combined.drop_duplicates(
            subset=[
                "min_adx",
                "stop_loss_pct",
                "take_profit_pct",
                "max_holding_days",
            ],
            keep="last",
        )

        combined.to_csv(
            results_path,
            index=False,
            encoding="utf-8-sig",
        )

    if not results_path.exists():
        print("Không có kết quả để xếp hạng.")
        return

    results = pd.read_csv(results_path)
    successful = results[
        results["status"] == "OK"
    ].copy()

    ranked = rank_results(
        successful,
        min_trades,
    )

    qualified = ranked[
        ranked["total_trades"] >= min_trades
    ]

    if qualified.empty:
        qualified = ranked

    top_results = qualified.head(top_n)

    top_results.to_csv(
        top_path,
        index=False,
        encoding="utf-8-sig",
    )

    total_elapsed = (
        time.perf_counter() - started
    )

    columns = [
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

    print("\n" + "=" * 76)
    print("TOP CẤU HÌNH")
    print("=" * 76)
    print(
        top_results[columns].to_string(
            index=False
        )
    )
    print("-" * 76)
    print(
        f"Thời gian replay: "
        f"{total_elapsed:.1f} giây"
    )
    print(
        f"Tất cả kết quả: "
        f"{results_path.resolve()}"
    )
    print(
        f"Top cấu hình: "
        f"{top_path.resolve()}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Quét tín hiệu một lần và tối ưu "
            "Exit bằng replay."
        )
    )

    parser.add_argument(
        "--sl",
        default="3,4,5,6",
    )
    parser.add_argument(
        "--tp",
        default="6,8,10,12",
    )
    parser.add_argument(
        "--hold",
        default="10,15,20,30",
    )
    parser.add_argument(
        "--adx",
        default="30",
    )
    parser.add_argument(
        "--db",
        default="market.db",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=60,
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=80,
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
    )
    parser.add_argument(
        "--force-rebuild-cache",
        action="store_true",
        help=(
            "Xóa logic dùng cache cũ và "
            "quét lại toàn bộ tín hiệu."
        ),
    )

    return parser


def main() -> None:
    args = build_parser().parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    cache_path = (
        output_dir / "signal_cache.pkl"
    )

    symbols = get_symbol_list(args.db)

    print(f"Tìm thấy {len(symbols)} mã.")

    cache = build_signal_cache(
        symbols=symbols,
        db_path=args.db,
        warmup_bars=args.warmup,
        cache_path=cache_path,
        force_rebuild=args.force_rebuild_cache,
    )

    run_grid(
        cache=cache,
        sl_values=parse_list(args.sl, float),
        tp_values=parse_list(args.tp, float),
        hold_values=parse_list(args.hold, int),
        adx_values=parse_list(args.adx, float),
        output_dir=output_dir,
        min_trades=args.min_trades,
        top_n=args.top,
    )


if __name__ == "__main__":
    main()
