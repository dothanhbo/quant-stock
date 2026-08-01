from __future__ import annotations
import traceback
import argparse
import math
import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable
from backtesting.exit import ExitResult
from backtesting.trade import ExitExecution, ExitReason, Trade
from backtesting.exit_models import (
    BaseExitModel,
    DEFAULT_EXIT_MODEL,
)
from strategy.indicators import add_indicators
from backtesting.portfolio import Portfolio
from backtesting.portfolio_simulator import PortfolioSimulator
from backtesting.portfolio_metrics import (
    calculate_portfolio_metrics,
)
from collections import Counter
from core.universe import get_vn100_symbols
from backtesting.transaction_cost import TransactionCostConfig
from backtesting.trade_analytics import (
    calculate_trade_analytics,
)
from backtesting.trade_distribution import (
    calculate_trade_distribution,
)
from backtesting.benchmark import (
    calculate_buy_and_hold_benchmark,
)

import pandas as pd
from strategy.scanner import evaluate_symbol	


DEFAULT_DB_PATH = "market.db"
DEFAULT_OUTPUT_DIR = "backtest_results_optimized"
DEFAULT_INITIAL_CAPITAL = 1_000_000_000.0


@dataclass(frozen=True)
class BacktestConfig:
    stop_loss_pct: float = 5.0
    take_profit_pct: float = 10.0
    max_holding_days: int = 20
    min_adx: float = 30.0
    initial_capital: float = DEFAULT_INITIAL_CAPITAL
    position_size_pct: float = 20.0
    buy_slippage_pct: float = 0.05
    sell_slippage_pct: float = 0.05
    buy_commission_pct: float = 0.15
    sell_commission_pct: float = 0.15
    sell_tax_pct: float = 0.10

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
        if self.buy_commission_pct < 0:
            raise ValueError(
                "buy_commission_pct must be greater than or equal to 0"
            )

        if self.sell_commission_pct < 0:
            raise ValueError(
                "sell_commission_pct must be greater than or equal to 0"
            )

        if self.sell_tax_pct < 0:
            raise ValueError(
                "sell_tax_pct must be greater than or equal to 0"
            )


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
            return _safe_float(evaluation[key])

    return math.nan


def _evaluate_entry(
    symbol,
    signal_date,
    verbose=False,
):
    try:
        return evaluate_symbol(
            symbol=symbol,
            reference_date=signal_date,
            end_date=signal_date,
        )
    except Exception:
        traceback.print_exc()
        raise
   
        if verbose:
            print(
                f"❌ {symbol} {signal_date.date()}: "
                f"entry evaluation error: {exc}"
            )

        return {
            "status": "ERROR",
            "reason": str(exc),
            "failed_conditions": [],
        }


def _simulate_exit(
    price_df: pd.DataFrame,
    entry_index: int,
    config: BacktestConfig,
    exit_model: BaseExitModel = DEFAULT_EXIT_MODEL,
) -> ExitResult:
    entry_row = price_df.iloc[entry_index]
    entry_price = float(entry_row["open"])
    entry_date = pd.Timestamp(entry_row["time"])

    stop_price, target_price = exit_model.calculate_levels(
        entry_price=entry_price,
        entry_row=entry_row,
        config=config,
    )

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

def generate_candidate_trades(
    symbol: str,
    config: BacktestConfig,
    db_path: str = DEFAULT_DB_PATH,
    warmup_bars: int = 60,
    verbose: bool = False,
    exit_model: BaseExitModel = DEFAULT_EXIT_MODEL,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[Trade]:

    config.validate()
    symbol = symbol.upper().strip()

    price_df = load_price_data(symbol, db_path)

    price_df["time"] = pd.to_datetime(
        price_df["time"],
        errors="coerce",
    )

    if start_date is not None:
        start_ts = pd.Timestamp(start_date)
        price_df = price_df[
            price_df["time"] >= start_ts
        ]
  
    if end_date is not None:
        end_ts = pd.Timestamp(end_date)
        price_df = price_df[
            price_df["time"] <= end_ts
        ]

    price_df = price_df.reset_index(drop=True)

    if len(price_df) <= warmup_bars + 1:
        return []

    price_df = add_indicators(price_df)

    if price_df.empty:
        return []
   
    required_columns = {"ATR14", "ADX14"}

    missing_columns = required_columns.difference(price_df.columns)

    if missing_columns:
        raise ValueError(
            "Dữ liệu backtest thiếu indicator: "
            + ", ".join(sorted(missing_columns))
        )

    trades: list[Trade] = []

    next_allowed_signal_index = warmup_bars

    for signal_index in range(warmup_bars, len(price_df) - 1):
        if signal_index < next_allowed_signal_index:
            continue

        signal_date = pd.Timestamp(
            price_df.iloc[signal_index]["time"]
        )

        evaluation = _evaluate_entry(
            symbol=symbol,
            signal_date=signal_date,
            verbose=verbose,
        )

        status = evaluation.get("status", "UNKNOWN")

        if status != "PASSED":
            if verbose:
                score = evaluation.get("score")
                failed = evaluation.get("failed_conditions", [])

                print(
                    f"⏭️ {symbol} {signal_date.date()}: "
                    f"status={status}, "
                    f"reason={evaluation.get('reason')}, "
                    f"score={evaluation.get('score')}, "
                    f"min_score={evaluation.get('min_score')}, "
                    f"regime={evaluation.get('regime')}, "
                    f"failed={evaluation.get('failed_conditions', [])}"
                )

            continue

        adx = evaluation.get("adx")

        if adx is None:
            if verbose:
                print(
                   f"✅ {symbol} {signal_date.date()}: "
                   f"strategy PASSED, "
                   f"score={evaluation.get('score')}, "
                   f"adx={evaluation.get('adx')}"
                )
            continue

        entry_index = signal_index + 1

        exit_info = _simulate_exit(
            price_df=price_df,
            entry_index=entry_index,
            config=config,
            exit_model=exit_model,
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

def backtest_symbol(
    symbol: str,
    config: BacktestConfig,
    db_path: str = DEFAULT_DB_PATH,
    warmup_bars: int = 60,
    verbose: bool = False,
    exit_model: BaseExitModel = DEFAULT_EXIT_MODEL,
) -> list[Trade]:
    """
    Wrapper tương thích với code cũ.
    """
    return generate_candidate_trades(
        symbol=symbol,
        config=config,
        db_path=db_path,
        warmup_bars=warmup_bars,
        verbose=verbose,
        exit_model=exit_model,
    )


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
    start_date: str | None = None,
    end_date: str | None = None,
    buy_commission_pct: float = 0.15,
    sell_commission_pct: float = 0.15,
    sell_tax_pct: float = 0.10,
    buy_slippage_pct: float = 0.05,
    sell_slippage_pct: float = 0.05,
) -> tuple[list[Trade], dict[str, Any], pd.DataFrame]:
    config = BacktestConfig(
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        max_holding_days=max_holding_days,
        min_adx=min_adx,
        initial_capital=initial_capital,
        position_size_pct=position_size_pct,
        buy_commission_pct=buy_commission_pct,
        sell_commission_pct=sell_commission_pct,
        sell_tax_pct=sell_tax_pct,
        buy_slippage_pct=buy_slippage_pct,
        sell_slippage_pct=sell_slippage_pct,
    )
    config.validate()

    if symbols is None:
        symbols = get_vn100_symbols()

    symbols = list(symbols)

    selected_symbols = (
        get_symbol_list(db_path)
        if symbols is None
        else sorted({str(symbol).upper().strip() for symbol in symbols})
    )

    benchmark_metrics: dict[str, Any] = {
        "benchmark_symbol": None,
        "benchmark_start_date": None,
        "benchmark_end_date": None,
        "benchmark_start_price": 0.0,
        "benchmark_end_price": 0.0,
        "benchmark_return_pct": 0.0,
        "benchmark_final_equity": (
            config.initial_capital
        ),
        "benchmark_cagr_pct": 0.0,
        "strategy_vs_benchmark_pct": 0.0,
        "strategy_vs_benchmark_cagr_pct": 0.0,
    }

    all_trades: list[Trade] = []

    for index, symbol in enumerate(selected_symbols, start=1):
        if verbose:
            print(f"[{index}/{len(selected_symbols)}] Backtest {symbol}")

        symbol_trades = generate_candidate_trades(
            symbol=symbol,
            config=config,
            db_path=db_path,
            warmup_bars=warmup_bars,
            verbose=verbose,
            start_date=start_date,
            end_date=end_date,
        )

        if symbol_trades:
           all_trades.extend(symbol_trades)

    transaction_cost_config = TransactionCostConfig(
        buy_commission_pct=config.buy_commission_pct,
        sell_commission_pct=config.sell_commission_pct,
        sell_tax_pct=config.sell_tax_pct,
        buy_slippage_pct=config.buy_slippage_pct,
        sell_slippage_pct=config.sell_slippage_pct,
    )

    simulator = PortfolioSimulator(
        initial_cash=config.initial_capital,
        position_size_pct=config.position_size_pct,
        transaction_cost_config=transaction_cost_config,
    )

    result = simulator.simulate(all_trades)

    trades = result.executed_trades

    metrics = calculate_metrics(
        trades,
        config,
    )

    trade_distribution = calculate_trade_distribution(
        result.executed_trades
    )

    metrics.update(
        trade_distribution
    )

    gross_profits = [
        trade.net_pnl
        for trade in trades
        if trade.net_pnl > 0
    ]

    gross_losses = [
        trade.net_pnl
        for trade in trades
        if trade.net_pnl < 0
    ]

    gross_profit_amount = float(
        sum(gross_profits)
    )

    gross_loss_amount = float(
        abs(sum(gross_losses))
    )

    profit_factor_amount = (
        gross_profit_amount / gross_loss_amount
        if gross_loss_amount > 0
        else 0.0
    )

    total_buy_commission = sum(
        trade.buy_commission
        for trade in trades
    )

    total_sell_commission = sum(
        trade.sell_commission
        for trade in trades
    )

    total_sell_tax = sum(
        trade.sell_tax
        for trade in trades
    )

    total_transaction_cost = sum(
        trade.total_transaction_cost
        for trade in trades
    )

    gross_trading_pnl = sum(
        trade.gross_pnl
        for trade in trades
    )

    net_trading_pnl = sum(
        trade.net_pnl
        for trade in trades
    )

    metrics.update(
        {
            "gross_profit": float(sum(gross_profits)),
            "gross_loss": float(abs(sum(gross_losses))),
            "profit_factor": float(
                profit_factor_amount
            ),
            "gross_trading_pnl": float(gross_trading_pnl),
            "net_trading_pnl": float(net_trading_pnl),
            "total_buy_commission": float(
                total_buy_commission
            ),
            "total_sell_commission": float(
                total_sell_commission
            ),
            "total_sell_tax": float(
                total_sell_tax
            ),
            "total_transaction_cost": float(
                total_transaction_cost
            ),
        }
    )

    equity = result.equity_curve

    portfolio_metrics = calculate_portfolio_metrics(
        equity,
        final_equity=result.final_equity,
    )

    metrics.update(portfolio_metrics)

    trade_analytics = calculate_trade_analytics(
        result.executed_trades
    )

    metrics.update(
        trade_analytics
    )

    rejected_reason_counts = Counter(
        rejected.reason
        for rejected in result.rejected_trades
    )

    metrics["rejected_trades"] = len(
        result.rejected_trades
    )

    metrics["rejected_trade_reasons"] = dict(
        rejected_reason_counts
    )

    metrics["final_cash"] = result.final_cash
    metrics["final_market_value"] = (
        result.final_market_value
    )
    metrics["final_open_positions"] = (
        result.final_open_positions
    )

    metrics["total_return_pct"] = (
        result.final_equity / config.initial_capital - 1
    ) * 100

    if len(selected_symbols) == 1:
        benchmark_symbol = selected_symbols[0]

        benchmark_price_df = load_price_data(
            benchmark_symbol,
            db_path,
        )

        if start_date is not None:
            benchmark_price_df = benchmark_price_df[
                benchmark_price_df["time"]
                >= pd.Timestamp(start_date)
            ]

        if end_date is not None:
            benchmark_price_df = benchmark_price_df[
                benchmark_price_df["time"]
                <= pd.Timestamp(end_date)
            ]

        benchmark_metrics = (
            calculate_buy_and_hold_benchmark(
                benchmark_price_df,
                initial_capital=config.initial_capital,
            )
        )

        benchmark_metrics["benchmark_symbol"] = (
            benchmark_symbol
        )

        benchmark_metrics[
            "strategy_vs_benchmark_pct"
        ] = (
            metrics["total_return_pct"]
            - benchmark_metrics[
                "benchmark_return_pct"
            ]
        )

        benchmark_metrics[
            "strategy_vs_benchmark_cagr_pct"
        ] = (
            metrics["cagr_pct"]
            - benchmark_metrics[
                "benchmark_cagr_pct"
            ]
        )

    metrics.update(benchmark_metrics)

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
    print("KẾT QUẢ BACKTEST ENGINE V5.2")
    print_portfolio_summary(metrics)

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
    parser.add_argument("--start",type=str,default=None,help="Ngày bắt đầu backtest, định dạng YYYY-MM-DD.",)
    parser.add_argument("--end",type=str,default=None,help="Ngày kết thúc backtest, định dạng YYYY-MM-DD.",)
    parser.add_argument("--sl", type=float, default=5.0)
    parser.add_argument("--tp", type=float, default=10.0)
    parser.add_argument("--hold", type=int, default=20)
    parser.add_argument("--min-adx", type=float, default=30.0)
    parser.add_argument("--buy-fee",type=float,default=0.15,help="Phí mua theo phần trăm, mặc định 0.15.",)
    parser.add_argument("--sell-fee",type=float,default=0.15,help="Phí bán theo phần trăm, mặc định 0.15.",)
    parser.add_argument("--sell-tax",type=float,default=0.10,help="Thuế bán theo phần trăm, mặc định 0.10.",)
    parser.add_argument("--buy-slippage",type=float,default=0.05,help="Slippage khi mua (%%). Mặc định 0.05.",)
    parser.add_argument("--sell-slippage",type=float,default=0.05,help="Slippage khi bán (%%). Mặc định 0.05.",)
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

def print_portfolio_summary(
    metrics: dict[str, Any],
) -> None:
    print("\n" + "=" * 60)
    print("PORTFOLIO SUMMARY")
    print("=" * 60)

    print(
        f"Initial Capital : "
        f"{metrics['initial_capital']:,.0f} VND"
    )

    print(
        f"Final Equity    : "
        f"{metrics['final_equity']:,.0f} VND"
    )

    print(
        f"Total Return    : "
        f"{metrics['total_return_pct']:+.2f}%"
    )

    print()

    print(
        f"Max Drawdown    : "
        f"{metrics['max_drawdown_pct']:.2f}%"
    )

    print(
        f"CAGR            : "
        f"{metrics['cagr_pct']:.2f}%"
    )

    print(
        f"Sharpe Ratio    : "
        f"{metrics['sharpe_ratio']:.2f}"
    )

    print(
        f"Sortino Ratio   : "
        f"{metrics['sortino_ratio']:.2f}"
    )

    print(
        f"Calmar Ratio    : "
        f"{metrics['calmar_ratio']:.2f}"
    )

    print()

    print(
        f"Executed Trades : "
        f"{metrics['total_trades']}"
    )
 
    print(
        f"Rejected Trades : "
        f"{metrics.get('rejected_trades', 0)}"
    )

    print(
        f"Final Cash      : "
        f"{metrics.get('final_cash', 0):,.0f} VND"
    )

    print(
        f"Market Value    : "
        f"{metrics.get('final_market_value', 0):,.0f} VND"
    )

    print(
        f"Open Positions  : "
        f"{metrics.get('final_open_positions', 0)}"
    	)

    print(
        f"Win Rate        : "
        f"{metrics['win_rate_pct']:.2f}%"
    )

    print(
        f"Profit Factor   : "
        f"{metrics['profit_factor']:.2f}"
    )

    print()

    print("PROFIT & COST BREAKDOWN")
    print("-" * 60)

    print(
        f"Gross Profit    : "
        f"{metrics.get('gross_profit', 0):,.0f} VND"
    )

    print(
        f"Gross Loss      : "
        f"-{metrics.get('gross_loss', 0):,.0f} VND"
    )

    print(
        f"Gross Trade PnL : "
        f"{metrics.get('gross_trading_pnl', 0):+,.0f} VND"
    )

    print(
        f"Net Trade PnL   : "
        f"{metrics.get('net_trading_pnl', 0):+,.0f} VND"
    )

    print()

    print(
        f"Buy Commission  : "
        f"{metrics.get('total_buy_commission', 0):,.0f} VND"
    )

    print(
        f"Sell Commission : "
        f"{metrics.get('total_sell_commission', 0):,.0f} VND"
    )

    print(
        f"Sell Tax        : "
        f"{metrics.get('total_sell_tax', 0):,.0f} VND"
    )

    print(
        f"Total Cost      : "
        f"{metrics.get('total_transaction_cost', 0):,.0f} VND"
    )

    print(
        f"Payoff Ratio    : "
        f"{metrics['payoff_ratio']:.2f}"
    )

    print()
    print("TRADE ANALYTICS")
    print("-" * 60)

    print(
        f"Expectancy      : "
        f"{metrics.get('expectancy_amount', 0):+,.0f} "
        f"VND/trade"
    )

    print(
        f"Expectancy (%)  : "
        f"{metrics.get('expectancy_pct', 0):+.2f}%"
    )

    print()

    print(
        f"Average Win     : "
        f"{metrics.get('average_win_pct', 0):+.2f}%"
    )

    print(
        f"Average Loss    : "
        f"{metrics.get('average_loss_pct', 0):+.2f}%"
    )

    print(
        f"Average Win PnL : "
        f"{metrics.get('average_win_amount', 0):+,.0f} VND"
    )

    print(
        f"Average Loss PnL: "
        f"{metrics.get('average_loss_amount', 0):+,.0f} VND"
    )

    print()

    print("Holding Days")

    print(
        f"  Average       : "
        f"{metrics.get('average_holding_days', 0):.1f}"
    )

    print(
        f"  Median        : "
        f"{metrics.get('median_holding_days', 0):.1f}"
    )

    print(
        f"  Minimum       : "
        f"{metrics.get('min_holding_days', 0)}"
    )

    print(
        f"  Maximum       : "
        f"{metrics.get('max_holding_days', 0)}"
    )   

    reject_reasons = metrics.get(
        "rejected_trade_reasons",
        {},
    )

    if reject_reasons:
        print()
        print("Reject Reasons")

        for reason, count in sorted(
            reject_reasons.items()
        ):
            print(
                f"- {reason:<18}: {count}"
            )

    print("=" * 60)


def main() -> None:
    args = parse_args()
 
    if args.all:
        symbols = list(get_vn100_symbols())
    else:
        symbols = [
            symbol.upper().strip()
            for symbol in args.symbol
        ]

    trades, metrics, equity = run_backtest(
        symbols=symbols,
        stop_loss_pct=args.sl,
        take_profit_pct=args.tp,
        max_holding_days=args.hold,
        min_adx=args.min_adx,
        db_path=args.db,
        warmup_bars=args.warmup,
        verbose=not args.quiet,
        start_date=args.start,
        end_date=args.end,
        buy_slippage_pct=args.buy_slippage,
        sell_slippage_pct=args.sell_slippage,
        buy_commission_pct=args.buy_fee,
        sell_commission_pct=args.sell_fee,
        sell_tax_pct=args.sell_tax,
    )
    print(
        f"Phí mua {metrics['buy_commission_pct']:.2f}% | "
        f"Phí bán {metrics['sell_commission_pct']:.2f}% | "
        f"Thuế bán {metrics['sell_tax_pct']:.2f}%"
    )

    print_summary(metrics)

    target_name = (
        "ALL"
        if symbols is None
        else "_".join(str(item).upper() for item in symbols)
    )

    if args.all:
        symbol_label = "VN100"
    else:
        symbol_label = "_".join(symbols)

    def fmt_filename_value(value) -> str:
        return str(value).replace(".", "p")

    prefix = (
        f"{target_name}"
        f"_ADX{fmt_filename_value(args.min_adx)}"
        f"_SL{fmt_filename_value(args.sl)}"
        f"_TP{fmt_filename_value(args.tp)}"
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