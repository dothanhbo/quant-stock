from __future__ import annotations

import argparse
import math
import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable
from backtesting.exit import ExitResult
from backtesting.trade import ExitExecution, ExitReason, Trade

import pandas as pd

try:
    from strategy.scanner import check_signal
except ImportError as exc:
    raise ImportError(
        "Không import được scanner.check_signal. "
        "Hãy đặt file này cùng thư mục với scanner.py."
    ) from exc


DEFAULT_DB_PATH = "market.db"
DEFAULT_OUTPUT_DIR = "backtest_results_optimized"
DEFAULT_INITIAL_CAPITAL = 100_000_000.0


@dataclass(frozen=True)
class BacktestConfig:
    stop_loss_pct: float = 5.0
    take_profit_pct: float = 10.0
    max_holding_days: int = 20
    min_adx: float = 30.0
    initial_capital: float = DEFAULT_INITIAL_CAPITAL
    position_size_pct: float = 100.0

    def validate(self) -> None:
        if self.stop_loss_pct <= 0:
            raise ValueError("stop_loss_pct phải lớn hơn 0.")
        if self.take_profit_pct <= 0:
            raise ValueError("take_profit_pct phải lớn hơn 0.")
        if self.max_holding_days < 1:
            raise ValueError("max_holding_days phải từ 1 trở lên.")
        if self.min_adx < 0:
            raise ValueError("min_adx không được âm.")
        if self.initial_capital <= 0:
            raise ValueError("initial_capital phải lớn hơn 0.")
        if not 0 < self.position_size_pct <= 100:
            raise ValueError("position_size_pct phải nằm trong khoảng (0, 100].")


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"Không tìm thấy database: {path.resolve()}")

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def get_symbol_list(db_path: str = DEFAULT_DB_PATH) -> list[str]:
    query = """
        SELECT DISTINCT symbol
        FROM prices
        WHERE symbol IS NOT NULL
          AND TRIM(symbol) <> ''
          AND UPPER(symbol) <> 'VNINDEX'
        ORDER BY symbol
    """

    with _connect(db_path) as connection:
        rows = connection.execute(query).fetchall()

    return [str(row["symbol"]).upper() for row in rows]


def load_price_data(
    symbol: str,
    db_path: str = DEFAULT_DB_PATH,
) -> pd.DataFrame:
    query = """
        SELECT symbol, time, open, high, low, close, volume
        FROM prices
        WHERE UPPER(symbol) = UPPER(?)
        ORDER BY time ASC
    """

    with _connect(db_path) as connection:
        df = pd.read_sql_query(query, connection, params=(symbol,))

    if df.empty:
        return df

    df["time"] = pd.to_datetime(df["time"], errors="coerce")

    numeric_columns = ["open", "high", "low", "close", "volume"]
    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = (
        df.dropna(subset=["time", "open", "high", "low", "close"])
        .drop_duplicates(subset=["time"], keep="last")
        .sort_values("time")
        .reset_index(drop=True)
    )

    return df


def _safe_float(value: Any, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default

    return result if math.isfinite(result) else default


def _get_signal_adx(signal: dict[str, Any]) -> float:
    for key in ("adx", "ADX", "ADX14", "adx14"):
        if key in signal:
            return _safe_float(signal[key])

    return math.nan


def _call_check_signal(
    symbol: str,
    signal_date: pd.Timestamp
) -> dict[str, Any] | None:
    signal_date_text = signal_date.strftime("%Y-%m-%d")

    result = check_signal(
        symbol,
        reference_date=signal_date_text,
        end_date=signal_date_text
    )

    if result is None:
        return None

    if not isinstance(result, dict):
        raise TypeError(
            f"check_signal({symbol}) phải trả về dict hoặc None, "
            f"nhưng nhận được {type(result).__name__}."
        )

    return result


def _simulate_exit(
    price_df: pd.DataFrame,
    entry_index: int,
    config: BacktestConfig,
) -> dict[str, Any]:
    entry_row = price_df.iloc[entry_index]
    entry_price = float(entry_row["open"])
    entry_date = pd.Timestamp(entry_row["time"])

    stop_price = entry_price * (1 - config.stop_loss_pct / 100)
    target_price = entry_price * (1 + config.take_profit_pct / 100)

    final_index = min(
        entry_index + config.max_holding_days - 1,
        len(price_df) - 1,
    )

    exit_index = final_index
    exit_price = float(price_df.iloc[final_index]["close"])
    exit_reason = ExitReason.TIME_EXIT
    execution = ExitExecution.NORMAL

    for current_index in range(entry_index, final_index + 1):
        row = price_df.iloc[current_index]

        day_open = float(row["open"])
        day_high = float(row["high"])
        day_low = float(row["low"])

        # Gap giảm xuyên stop: khớp tại giá mở cửa, không giả định được khớp ở stop.
        if day_open <= stop_price:
            exit_index = current_index
            exit_price = day_open
            exit_reason = ExitReason.STOP_LOSS
            execution = ExitExecution.STOP_GAP
            break

        # Gap tăng xuyên target: khớp tại giá mở cửa.
        if day_open >= target_price:
            exit_index = current_index
            exit_price = day_open
            exit_reason = ExitReason.TAKE_PROFIT
            execution = ExitExecution.TARGET_GAP
            break

        hit_stop = day_low <= stop_price
        hit_target = day_high >= target_price

        # Không có dữ liệu intraday nên dùng giả định bảo thủ.
        if hit_stop and hit_target:
            exit_index = current_index
            exit_price = stop_price
            exit_reason = ExitReason.STOP_LOSS
            execution = ExitExecution.SAME_DAY_SL_FIRST
            break

        if hit_stop:
            exit_index = current_index
            exit_price = stop_price
            exit_reason = ExitReason.STOP_LOSS
            execution = ExitExecution.NORMAL
            break

        if hit_target:
            exit_index = current_index
            exit_price = target_price
            exit_reason = ExitReason.TAKE_PROFIT
            execution = ExitExecution.NORMAL
            break

    exit_row = price_df.iloc[exit_index]
    return_pct = (exit_price / entry_price - 1) * 100

    return ExitResult(
    entry_index=entry_index,
    exit_index=exit_index,
    entry_date=pd.Timestamp(price_df.iloc[entry_index]["time"]),
    exit_date=pd.Timestamp(price_df.iloc[exit_index]["time"]),
    entry_price=entry_price,
    exit_price=exit_price,
    stop_price=stop_price,
    target_price=target_price,
    exit_reason=exit_reason,
    execution=execution,
    )

def backtest_symbol(
    symbol: str,
    config: BacktestConfig,
    db_path: str = DEFAULT_DB_PATH,
    warmup_bars: int = 60,
    verbose: bool = False,
) -> list[Trade]:
    """
    Backtest một mã.

    Quy tắc:
    - Tín hiệu được xác nhận sau khi phiên signal_date kết thúc.
    - Entry tại OPEN phiên kế tiếp.
    - Không mở lệnh mới khi lệnh cũ chưa thoát.
    """
    config.validate()
    symbol = symbol.upper().strip()

    price_df = load_price_data(symbol, db_path)

    if len(price_df) <= warmup_bars + 1:
        return []

    trades: list[Trade] = []
    next_allowed_signal_index = warmup_bars

    for signal_index in range(warmup_bars, len(price_df) - 1):
        if signal_index < next_allowed_signal_index:
            continue

        signal_date = pd.Timestamp(
            price_df.iloc[signal_index]["time"]
        )

        try:
            signal = _call_check_signal(symbol, signal_date)
        except Exception as error:
            if verbose:
                print(
                    f"⚠️ {symbol} {signal_date.date()}: "
                    f"lỗi tín hiệu: {error}"
                )
            continue

        if not signal:
            continue

        adx = _get_signal_adx(signal)

        if not math.isfinite(adx) or adx < config.min_adx:
            continue

        entry_index = signal_index + 1

        exit_info = _simulate_exit(
            price_df=price_df,
            entry_index=entry_index,
            config=config,
        )

        trade = Trade(
            symbol=symbol,
            entry_date=exit_info.entry_date,
            entry_price=exit_info.entry_price,
            quantity=1,
        )

        trade.close(
            exit_date=exit_info.exit_date,
            exit_price=exit_info.exit_price,
            reason=exit_info.exit_reason,
            execution=exit_info.execution,
        )

        trades.append(trade)

        # Không chồng lệnh trên cùng một mã.
        next_allowed_signal_index = exit_info.exit_index + 1

        if verbose:
            print(
                f"✅ {symbol} | "
                f"Signal {signal_date.date()} | "
                f"Entry {trade.entry_date.date()} "
                f"@ {trade.entry_price:.2f} | "
                f"Exit {trade.exit_date.date()} "
                f"@ {trade.exit_price:.2f} | "
                f"{trade.exit_reason.value} | "
                f"{trade.return_pct:+.2f}%"
            )

    return trades


def calculate_metrics(
    trades:  list[Trade],
    config: BacktestConfig,
) -> dict[str, Any]:

    if not trades:
        return {
            **asdict(config),
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "breakeven": 0,
            "win_rate_pct": 0.0,
            "average_return_pct": 0.0,
            "average_win_pct": 0.0,
            "average_loss_pct": 0.0,
            "profit_factor": 0.0,
            "payoff_ratio": 0.0,
            "expectancy_pct": 0.0,
            "best_trade_pct": 0.0,
            "worst_trade_pct": 0.0,
            "average_holding_days": 0.0,
        }

    returns = pd.Series(
        [trade.return_pct for trade in trades],
        dtype=float,
    )

    holding_days = pd.Series(
        [trade.holding_days for trade in trades],
        dtype=float,
    )

    winners = returns[returns > 0]
    losers = returns[returns < 0]
    breakeven = returns[returns == 0]

    gross_profit = float(winners.sum())
    gross_loss = abs(float(losers.sum()))

    avg_win = float(winners.mean()) if not winners.empty else 0.0
    avg_loss = float(losers.mean()) if not losers.empty else 0.0

    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0
        else (math.inf if gross_profit > 0 else 0.0)
    )
    payoff_ratio = (
        avg_win / abs(avg_loss)
        if avg_loss < 0
        else (math.inf if avg_win > 0 else 0.0)
    )

    return {
        **asdict(config),
        "total_trades": int(len(returns)),
        "wins": int(len(winners)),
        "losses": int(len(losers)),
        "breakeven": int(len(breakeven)),
        "win_rate_pct": float(len(winners) / len(returns) * 100),
        "average_return_pct": float(returns.mean()),
        "average_win_pct": avg_win,
        "average_loss_pct": avg_loss,
        "profit_factor": profit_factor,
        "payoff_ratio": payoff_ratio,
        "expectancy_pct": float(returns.mean()),
        "best_trade_pct": float(returns.max()),
        "worst_trade_pct": float(returns.min()),
        "average_holding_days": float(
            pd.Series(
                [trade.holding_days for trade in trades],
                dtype=float,
            ).mean()
        ),
    }


def build_equity_curve(
    trades: list[Trade],
    config: BacktestConfig,
) -> pd.DataFrame:
    """
    Equity curve đơn giản theo thứ tự exit_date.

    Lưu ý: khi chạy nhiều mã, các giao dịch có thể trùng thời gian.
    Equity curve này dùng cho so sánh tương đối, chưa phải portfolio simulator.
    """
    if not trades:
        return pd.DataFrame(
            [{
                "trade_number": 0,
                "equity": config.initial_capital,
            }]
        )

    ordered = sorted(
        trades,
        key=lambda trade: (
            trade.exit_date,
            trade.symbol,
            trade.entry_date,
        ),
    )

    capital = config.initial_capital
    rows = [
        {
            "trade_number": 0,
            "equity": capital,
        }
    ]

    for index, trade in enumerate(ordered, start=1):
        allocated = capital * config.position_size_pct / 100
        pnl = allocated * trade.return_pct / 100
        capital += pnl

        rows.append(
            {
                "trade_number": index,
                "symbol": trade.symbol,
                "exit_date": trade.exit_date,
                "return_pct": trade.return_pct,
                "pnl": pnl,
                "equity": capital,
            }
        )

    curve = pd.DataFrame(rows)

    curve["equity_peak"] = curve["equity"].cummax()

    curve["drawdown_pct"] = (
        curve["equity"] / curve["equity_peak"] - 1
    ) * 100

    return curve


def run_backtest(
    symbols: Iterable[str] | None = None,
    *,
    stop_loss_pct: float = 5.0,
    take_profit_pct: float = 10.0,
    max_holding_days: int = 20,
    min_adx: float = 30.0,
    db_path: str = DEFAULT_DB_PATH,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    position_size_pct: float = 100.0,
    warmup_bars: int = 60,
    verbose: bool = False,
) -> tuple[list[Trade], dict[str, Any], pd.DataFrame]:
    """
    Hàm chính để optimize_exit.py import.

    Ví dụ:
        trades, metrics, equity = run_backtest(
            symbols=None,
            stop_loss_pct=4,
            take_profit_pct=8,
            max_holding_days=15,
            min_adx=30,
        )
    """
    config = BacktestConfig(
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        max_holding_days=max_holding_days,
        min_adx=min_adx,
        initial_capital=initial_capital,
        position_size_pct=position_size_pct,
    )
    config.validate()

    selected_symbols = (
        get_symbol_list(db_path)
        if symbols is None
        else sorted({str(symbol).upper().strip() for symbol in symbols})
    )

    all_trades: list[Trade] = []

    for index, symbol in enumerate(selected_symbols, start=1):
        if verbose:
            print(f"[{index}/{len(selected_symbols)}] Backtest {symbol}")

        symbol_trades = backtest_symbol(
            symbol=symbol,
            config=config,
            db_path=db_path,
            warmup_bars=warmup_bars,
            verbose=verbose,
        )

        if symbol_trades:
           all_trades.extend(symbol_trades)

    trades = sorted(
        all_trades,
        key=lambda trade: (
            trade.exit_date,
            trade.symbol,
            trade.entry_date,
        ),
    )

    metrics = calculate_metrics(trades, config)
    equity = build_equity_curve(trades, config)

    if not equity.empty and "drawdown_pct" in equity.columns:
        metrics["max_drawdown_pct"] = float(equity["drawdown_pct"].min())
        metrics["final_equity"] = float(equity["equity"].iloc[-1])
    else:
        metrics["max_drawdown_pct"] = 0.0
        metrics["final_equity"] = config.initial_capital

    return trades, metrics, equity


def save_results(
    trades: list[Trade],
    metrics: dict[str, Any],
    equity: pd.DataFrame,
    output_dir: str,
    file_prefix: str,
) -> None:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    trades_path = output_path / f"{file_prefix}_trades.csv"
    metrics_path = output_path / f"{file_prefix}_metrics.csv"
    equity_path = output_path / f"{file_prefix}_equity_curve.csv"

    trades_df = pd.DataFrame(
        [trade.to_dict() for trade in trades]
    )

    trades_df.to_csv(trades_path, index=False, encoding="utf-8-sig")
    pd.DataFrame([metrics]).to_csv(
        metrics_path,
        index=False,
        encoding="utf-8-sig",
    )
    equity.to_csv(equity_path, index=False, encoding="utf-8-sig")

    print("\nĐã xuất:")
    print(f"- {trades_path}")
    print(f"- {metrics_path}")
    print(f"- {equity_path}")


def print_summary(metrics: dict[str, Any]) -> None:
    print("\n" + "=" * 76)
    print("KẾT QUẢ BACKTEST ENGINE V4.2")
    print("=" * 76)
    print(
        f"Cấu hình: ADX >= {metrics['min_adx']:.0f} | "
        f"SL {metrics['stop_loss_pct']:.1f}% | "
        f"TP {metrics['take_profit_pct']:.1f}% | "
        f"Hold {metrics['max_holding_days']} phiên"
    )
    print("-" * 76)
    print(f"Tổng giao dịch: {metrics['total_trades']}")
    print(f"Lệnh thắng: {metrics['wins']}")
    print(f"Lệnh thua: {metrics['losses']}")
    print(f"Win rate: {metrics['win_rate_pct']:.2f}%")
    print(f"Return trung bình/lệnh: {metrics['average_return_pct']:+.2f}%")
    print(f"Profit Factor: {metrics['profit_factor']:.2f}")
    print(f"Payoff Ratio: {metrics['payoff_ratio']:.2f}")
    print(f"Max Drawdown tham khảo: {metrics['max_drawdown_pct']:.2f}%")
    print(f"Vốn cuối tham khảo: {metrics['final_equity']:,.0f} VND")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest Engine V4.2 cho Quant Bot."
    )

    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--symbol",
        nargs="+",
        help="Một hoặc nhiều mã, ví dụ: --symbol HPG FPT",
    )
    target.add_argument(
        "--all",
        action="store_true",
        help="Backtest toàn bộ mã trong bảng prices.",
    )

    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--sl", type=float, default=5.0)
    parser.add_argument("--tp", type=float, default=10.0)
    parser.add_argument("--hold", type=int, default=20)
    parser.add_argument("--min-adx", type=float, default=30.0)
    parser.add_argument("--warmup", type=int, default=60)
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_DIR,
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Ẩn log từng mã/từng lệnh.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    symbols = None if args.all else args.symbol

    trades, metrics, equity = run_backtest(
        symbols=symbols,
        stop_loss_pct=args.sl,
        take_profit_pct=args.tp,
        max_holding_days=args.hold,
        min_adx=args.min_adx,
        db_path=args.db,
        warmup_bars=args.warmup,
        verbose=not args.quiet,
    )

    print_summary(metrics)

    target_name = (
        "ALL"
        if symbols is None
        else "_".join(str(item).upper() for item in symbols)
    )
    prefix = (
        f"{target_name}"
        f"_ADX{args.min_adx:g}"
        f"_SL{args.sl:g}"
        f"_TP{args.tp:g}"
        f"_H{args.hold}"
    ).replace(".", "p")

    save_results(
        trades=trades,
        metrics=metrics,
        equity=equity,
        output_dir=args.output,
        file_prefix=prefix,
    )


if __name__ == "__main__":
    main()