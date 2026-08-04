"""
Walk-forward validation for Quant Stock.

Phase 3.1:
- Create rolling train/test windows.
- Run the existing portfolio backtest only on each OOS test window.
- Export per-window summary, combined trades, equity curves, and aggregate report.

Run:
    py -m research.walk_forward
    py -m research.walk_forward --start 2020-01-01 --end 2026-07-31
    py -m research.walk_forward --symbols HPG FPT MBB ACB
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable

import pandas as pd

from backtesting.engine import get_symbol_list, run_backtest


DEFAULT_OUTPUT_DIR = Path("research_results/walk_forward")
DEFAULT_DB_PATH = "market.db"


@dataclass(frozen=True)
class WalkForwardWindow:
    window_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str


@dataclass(frozen=True)
class WalkForwardConfig:
    start_date: str = "2020-01-01"
    end_date: str = "2026-07-31"
    train_years: int = 3
    test_months: int = 12
    step_months: int = 12
    anchored: bool = False

    initial_capital: float = 1_000_000_000.0
    position_size_pct: float = 20.0

    stop_loss_pct: float = 5.0
    take_profit_pct: float = 10.0
    max_holding_days: int = 20
    min_adx: float = 30.0

    buy_commission_pct: float = 0.15
    sell_commission_pct: float = 0.15
    sell_tax_pct: float = 0.10
    buy_slippage_pct: float = 0.05
    sell_slippage_pct: float = 0.05

    min_trades_per_window: int = 5

    def validate(self) -> None:
        start = pd.Timestamp(self.start_date)
        end = pd.Timestamp(self.end_date)

        if start >= end:
            raise ValueError("start_date phải nhỏ hơn end_date.")
        if self.train_years < 1:
            raise ValueError("train_years phải từ 1 trở lên.")
        if self.test_months < 1:
            raise ValueError("test_months phải từ 1 trở lên.")
        if self.step_months < 1:
            raise ValueError("step_months phải từ 1 trở lên.")
        if self.initial_capital <= 0:
            raise ValueError("initial_capital phải lớn hơn 0.")
        if not 0 < self.position_size_pct <= 100:
            raise ValueError("position_size_pct phải nằm trong khoảng (0, 100].")
        if self.min_trades_per_window < 0:
            raise ValueError("min_trades_per_window không được âm.")


def _date_text(value: pd.Timestamp) -> str:
    return value.strftime("%Y-%m-%d")


def build_walk_forward_windows(
    config: WalkForwardConfig,
) -> list[WalkForwardWindow]:
    """Create non-overlapping or overlapping rolling OOS windows."""
    config.validate()

    global_start = pd.Timestamp(config.start_date).normalize()
    global_end = pd.Timestamp(config.end_date).normalize()

    first_test_start = global_start + pd.DateOffset(years=config.train_years)
    test_start = first_test_start
    windows: list[WalkForwardWindow] = []

    while test_start <= global_end:
        train_start = (
            global_start
            if config.anchored
            else test_start - pd.DateOffset(years=config.train_years)
        )
        train_end = test_start - pd.Timedelta(days=1)
        test_end = min(
            test_start + pd.DateOffset(months=config.test_months)
            - pd.Timedelta(days=1),
            global_end,
        )

        if train_end < train_start or test_end < test_start:
            break

        windows.append(
            WalkForwardWindow(
                window_id=len(windows) + 1,
                train_start=_date_text(train_start),
                train_end=_date_text(train_end),
                test_start=_date_text(test_start),
                test_end=_date_text(test_end),
            )
        )

        test_start += pd.DateOffset(months=config.step_months)

    if not windows:
        raise ValueError(
            "Không tạo được window. Hãy tăng khoảng dữ liệu hoặc giảm train_years."
        )

    return windows


def _safe_number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _trade_to_dict(trade: Any) -> dict[str, Any]:
    if hasattr(trade, "to_dict"):
        row = trade.to_dict()
        if isinstance(row, dict):
            return row

    if hasattr(trade, "__dataclass_fields__"):
        return asdict(trade)

    if hasattr(trade, "__dict__"):
        return dict(vars(trade))

    return {"trade": str(trade)}


def _normalize_trades(trades: Any) -> pd.DataFrame:
    if isinstance(trades, pd.DataFrame):
        return trades.copy()

    if not trades:
        return pd.DataFrame()

    return pd.DataFrame([_trade_to_dict(trade) for trade in trades])


def _metric_row(
    window: WalkForwardWindow,
    metrics: dict[str, Any],
    trade_count: int,
    elapsed_seconds: float,
    min_trades: int,
) -> dict[str, Any]:
    total_trades = int(metrics.get("total_trades", trade_count) or 0)

    return {
        **asdict(window),
        "elapsed_seconds": round(elapsed_seconds, 2),
        "total_trades": total_trades,
        "enough_trades": total_trades >= min_trades,
        "initial_capital": _safe_number(metrics.get("initial_capital")),
        "final_equity": _safe_number(metrics.get("final_equity")),
        "total_return_pct": _safe_number(metrics.get("total_return_pct")),
        "cagr_pct": _safe_number(metrics.get("cagr_pct")),
        "max_drawdown_pct": _safe_number(metrics.get("max_drawdown_pct")),
        "annualized_volatility_pct": _safe_number(
            metrics.get("annualized_volatility_pct")
        ),
        "sharpe_ratio": _safe_number(metrics.get("sharpe_ratio")),
        "sortino_ratio": _safe_number(metrics.get("sortino_ratio")),
        "calmar_ratio": _safe_number(metrics.get("calmar_ratio")),
        "profit_factor": _safe_number(metrics.get("profit_factor")),
        "win_rate_pct": _safe_number(metrics.get("win_rate_pct")),
        "expectancy_pct": _safe_number(metrics.get("expectancy_pct")),
        "benchmark_return_pct": _safe_number(
            metrics.get("benchmark_return_pct")
        ),
        "strategy_vs_benchmark_pct": _safe_number(
            metrics.get("strategy_vs_benchmark_pct")
        ),
    }


def summarize_walk_forward(summary_df: pd.DataFrame) -> dict[str, Any]:
    if summary_df.empty:
        return {}

    valid = summary_df[summary_df["enough_trades"]].copy()
    source = valid if not valid.empty else summary_df

    positive_windows = int((source["total_return_pct"] > 0).sum())
    total_windows = int(len(source))

    return {
        "windows_total": int(len(summary_df)),
        "windows_used": total_windows,
        "windows_with_enough_trades": int(summary_df["enough_trades"].sum()),
        "positive_windows": positive_windows,
        "positive_window_rate_pct": (
            positive_windows / total_windows * 100 if total_windows else 0.0
        ),
        "total_oos_trades": int(summary_df["total_trades"].sum()),
        "mean_oos_return_pct": _safe_number(source["total_return_pct"].mean()),
        "median_oos_return_pct": _safe_number(
            source["total_return_pct"].median()
        ),
        "worst_oos_return_pct": _safe_number(source["total_return_pct"].min()),
        "best_oos_return_pct": _safe_number(source["total_return_pct"].max()),
        "mean_sharpe_ratio": _safe_number(source["sharpe_ratio"].mean()),
        "median_sharpe_ratio": _safe_number(source["sharpe_ratio"].median()),
        "mean_max_drawdown_pct": _safe_number(
            source["max_drawdown_pct"].mean()
        ),
        "worst_max_drawdown_pct": _safe_number(
            source["max_drawdown_pct"].min()
        ),
        "mean_profit_factor": _safe_number(source["profit_factor"].mean()),
        "mean_win_rate_pct": _safe_number(source["win_rate_pct"].mean()),
        "robust": bool(
            total_windows >= 3
            and positive_windows / total_windows >= 0.60
            and _safe_number(source["sharpe_ratio"].median()) > 0
        ),
    }


def run_walk_forward(
    *,
    config: WalkForwardConfig,
    symbols: Iterable[str] | None = None,
    db_path: str = DEFAULT_DB_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    config.validate()
    output_dir.mkdir(parents=True, exist_ok=True)

    selected_symbols = (
        sorted({str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()})
        if symbols
        else get_symbol_list(db_path)
    )

    if not selected_symbols:
        raise ValueError("Không tìm thấy symbol để chạy walk-forward.")

    windows = build_walk_forward_windows(config)
    summary_rows: list[dict[str, Any]] = []
    all_trade_frames: list[pd.DataFrame] = []

    print("=" * 92)
    print("WALK-FORWARD OOS VALIDATION")
    print("=" * 92)
    print(f"Symbols       : {len(selected_symbols)}")
    print(f"Windows       : {len(windows)}")
    print(f"Train/Test    : {config.train_years} năm / {config.test_months} tháng")
    print(f"Mode          : {'Anchored' if config.anchored else 'Rolling'}")
    print(f"Output        : {output_dir}")
    print("=" * 92)

    for window in windows:
        print(
            f"[{window.window_id:02d}/{len(windows):02d}] "
            f"Train {window.train_start} -> {window.train_end} | "
            f"OOS {window.test_start} -> {window.test_end}"
        )

        started_at = perf_counter()

        trades, metrics, equity_df = run_backtest(
            symbols=selected_symbols,
            db_path=db_path,
            start_date=window.test_start,
            end_date=window.test_end,
            initial_capital=config.initial_capital,
            position_size_pct=config.position_size_pct,
            stop_loss_pct=config.stop_loss_pct,
            take_profit_pct=config.take_profit_pct,
            max_holding_days=config.max_holding_days,
            min_adx=config.min_adx,
            buy_commission_pct=config.buy_commission_pct,
            sell_commission_pct=config.sell_commission_pct,
            sell_tax_pct=config.sell_tax_pct,
            buy_slippage_pct=config.buy_slippage_pct,
            sell_slippage_pct=config.sell_slippage_pct,
            verbose=False,
        )

        elapsed = perf_counter() - started_at
        trades_df = _normalize_trades(trades)

        summary_rows.append(
            _metric_row(
                window=window,
                metrics=metrics,
                trade_count=len(trades_df),
                elapsed_seconds=elapsed,
                min_trades=config.min_trades_per_window,
            )
        )

        if not trades_df.empty:
            trades_df.insert(0, "window_id", window.window_id)
            trades_df.insert(1, "oos_start", window.test_start)
            trades_df.insert(2, "oos_end", window.test_end)
            all_trade_frames.append(trades_df)

        if isinstance(equity_df, pd.DataFrame) and not equity_df.empty:
            curve = equity_df.copy()
            curve.insert(0, "window_id", window.window_id)
            curve.to_csv(
                output_dir / f"equity_window_{window.window_id:02d}.csv",
                index=False,
                encoding="utf-8-sig",
            )

        print(
            f"    Trades={summary_rows[-1]['total_trades']} | "
            f"Return={summary_rows[-1]['total_return_pct']:.2f}% | "
            f"Sharpe={summary_rows[-1]['sharpe_ratio']:.2f} | "
            f"MDD={summary_rows[-1]['max_drawdown_pct']:.2f}% | "
            f"{elapsed:.1f}s"
        )

    summary_df = pd.DataFrame(summary_rows)
    trades_df = (
        pd.concat(all_trade_frames, ignore_index=True)
        if all_trade_frames
        else pd.DataFrame()
    )
    aggregate = summarize_walk_forward(summary_df)

    summary_df.to_csv(
        output_dir / "summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    trades_df.to_csv(
        output_dir / "trades.csv",
        index=False,
        encoding="utf-8-sig",
    )

    with (output_dir / "aggregate.json").open("w", encoding="utf-8") as file:
        json.dump(aggregate, file, indent=2, ensure_ascii=False)

    with (output_dir / "config.json").open("w", encoding="utf-8") as file:
        json.dump(
            {
                **asdict(config),
                "db_path": db_path,
                "symbols": selected_symbols,
            },
            file,
            indent=2,
            ensure_ascii=False,
        )

    return summary_df, trades_df, aggregate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rolling walk-forward OOS validation."
    )
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--symbols", nargs="*", default=None)

    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2026-7-31")
    parser.add_argument("--train-years", type=int, default=3)
    parser.add_argument("--test-months", type=int, default=12)
    parser.add_argument("--step-months", type=int, default=12)
    parser.add_argument("--anchored", action="store_true")

    parser.add_argument("--capital", type=float, default=1_000_000_000.0)
    parser.add_argument("--position-size", type=float, default=20.0)
    parser.add_argument("--sl", type=float, default=5.0)
    parser.add_argument("--tp", type=float, default=10.0)
    parser.add_argument("--hold", type=int, default=20)
    parser.add_argument("--min-adx", type=float, default=30.0)
    parser.add_argument("--min-trades", type=int, default=5)

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    config = WalkForwardConfig(
        start_date=args.start,
        end_date=args.end,
        train_years=args.train_years,
        test_months=args.test_months,
        step_months=args.step_months,
        anchored=args.anchored,
        initial_capital=args.capital,
        position_size_pct=args.position_size,
        stop_loss_pct=args.sl,
        take_profit_pct=args.tp,
        max_holding_days=args.hold,
        min_adx=args.min_adx,
        min_trades_per_window=args.min_trades,
    )

    summary_df, _, aggregate = run_walk_forward(
        config=config,
        symbols=args.symbols,
        db_path=args.db,
        output_dir=Path(args.output),
    )

    print()
    print("=" * 92)
    print("WALK-FORWARD SUMMARY")
    print("=" * 92)

    display_columns = [
        "window_id",
        "test_start",
        "test_end",
        "total_trades",
        "total_return_pct",
        "sharpe_ratio",
        "max_drawdown_pct",
        "profit_factor",
        "enough_trades",
    ]
    print(summary_df[display_columns].to_string(index=False))

    print("-" * 92)
    for key, value in aggregate.items():
        print(f"{key}: {value}")

    print("=" * 92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
