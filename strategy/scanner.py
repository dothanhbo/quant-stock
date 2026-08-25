from __future__ import annotations

from collections import Counter
from typing import Optional

import pandas as pd
from sqlalchemy import text
from dotenv import load_dotenv

from config.strategy_loader import COMMON_CONFIG
from config.trading_policy import TradingPolicy
from core.database import engine, get_reference_market_date, get_symbol_latest_dates, load_price_data
from core.signal_database import save_signal
from core.scan_telemetry import (
    persist_scan_telemetry,
    print_scan_diagnostics,
)
from execution.signal_executor import (
    PaperExecutionBatchResult,
    PaperSignalExecutor,
)
from reporting.dashboard import print_end_of_day_dashboard, print_scan_results
from services.notification_formatter import build_scan_message
from services.paper_notification_formatter import (
    build_paper_execution_message,
)
from services.telegram_client import TelegramClient
from strategy.cache import get_indicators_cached
from strategy.filters import REQUIRED_INDICATORS, trend_passes
from strategy.market_regime import get_market_regime
from strategy.relative_strength import calculate_relative_strength
from strategy.trend_strategy_v1 import TrendStrategyV1
from core.universe import get_vn100_symbols

MIN_DATA_ROWS = int(COMMON_CONFIG["min_data_rows"])
TOP_RESULTS = int(COMMON_CONFIG["top_results"])
TOP_WATCHLIST = int(COMMON_CONFIG["top_watchlist"])
RS_PERIOD = int(COMMON_CONFIG.get("rs_period", 20))
from strategy.base_strategy import BaseStrategy

load_dotenv()

TRADING_POLICY = TradingPolicy.from_env()
strategy = TRADING_POLICY.build_entry_model()

telegram_client = TelegramClient.from_env()
paper_signal_executor = PaperSignalExecutor.from_env()

# Backward-compatible aliases for earlier imports.
_trend_passes = trend_passes


def get_all_symbols() -> list[str]:
    query = text("SELECT DISTINCT symbol FROM prices ORDER BY symbol ASC")
    with engine.connect() as connection:
        rows = connection.execute(query).fetchall()
    return [str(row[0]) for row in rows]


def _prepare_price_data(symbol: str, end_date=None) -> pd.DataFrame:
    df = load_price_data(symbol)
    if df.empty:
        return df

    prepared = df.copy()
    prepared["time"] = pd.to_datetime(
        prepared["time"],
        errors="coerce",
    )
    prepared = prepared.dropna(
        subset=["time"]
    )

    prepared["trading_date"] = (
        prepared["time"]
        .dt.strftime("%Y-%m-%d")
    )
    prepared = (
        prepared
        .sort_values("time")
        .drop_duplicates(
            subset=["trading_date"],
            keep="last",
        )
        .drop(columns=["trading_date"])
        .reset_index(drop=True)
    )

    if end_date is not None:
        cutoff = pd.to_datetime(end_date, errors="coerce")
        if pd.isna(cutoff):
            raise ValueError(f"end_date không hợp lệ: {end_date}")
        prepared = prepared[prepared["time"] <= cutoff].copy()
    return prepared

def evaluate_prepared_row(
    *,
    symbol: str,
    latest: pd.Series,
    market_config: dict,
    entry_model: BaseStrategy | None = None,
) -> dict:
    entry_model = (
        entry_model
        or strategy
    )
    base = {
        "symbol": symbol,
        "status": "REJECTED",
        "reason": "other",
    }

    latest_date = pd.to_datetime(
        latest.get("time"),
        errors="coerce",
    )

    if pd.isna(latest_date):
        return {
            **base,
            "reason": "invalid_date",
        }

    date_text = latest_date.strftime("%Y-%m-%d")

    missing_indicators = [
        column
        for column in REQUIRED_INDICATORS
        if (
            column not in latest.index
            or pd.isna(latest[column])
        )
    ]

    if missing_indicators:
        return {
            **base,
            "reason": "indicator_nan",
            "missing_indicators": missing_indicators,
            "date": date_text,
        }

    relative_strength = pd.to_numeric(
        latest.get("Relative_Strength_20D"),
        errors="coerce",
    )

    if pd.isna(relative_strength):
        return {
            **base,
            "reason": "relative_strength_data",
            "date": date_text,
        }

    decision = entry_model.evaluate(
        latest=latest,
        relative_strength=float(
            relative_strength
        ),
        market_config=market_config,
    )

    # Entry models may expose legacy regime-based risk levels. Production
    # always overwrites them with the frozen policy used by paper/research.
    stop_loss, take_profit = TRADING_POLICY.calculate_levels(
        entry_price=float(latest["close"]),
        atr=float(latest["ATR14"]),
    )
    decision.update({
        "entry": round(float(latest["close"]), 2),
        "stop_loss": round(stop_loss, 2),
        "take_profit": round(take_profit, 2),
        "stop_atr_multiplier": TRADING_POLICY.stop_atr_multiplier,
        "target_atr_multiplier": TRADING_POLICY.target_atr_multiplier,
        "maximum_holding_days": TRADING_POLICY.maximum_holding_days,
        "execution_timing": TRADING_POLICY.execution_timing,
    })

    return {
        **base,
        **decision,
        "date": date_text,
        "ema10": round(
            float(latest["EMA10"]),
            2,
        ),
        "ema20": round(
            float(latest["EMA20"]),
            2,
        ),
        "ema50": round(
            float(latest["EMA50"]),
            2,
        ),
        "rsi": round(
            float(latest["RSI"]),
            2,
        ),
        "adx": round(
            float(latest["ADX14"]),
            2,
        ),
        "atr": round(
            float(latest["ATR14"]),
            2,
        ),
        "atr_percent": round(
            float(latest["ATR_Percent"]),
            2,
        ),
        "volume_ratio": round(
            float(latest["Vol_Ratio"]),
            2,
        ),
        "distance_ema20": round(
            float(latest["Distance_EMA20_Pct"]),
            2,
        ),
        "return_3d": round(
            float(latest["Return_3D_Pct"]),
            2,
        ),
        "breakout_20d": bool(
            latest["Breakout_20D"]
        ),
        "volume_breakout_5d": bool(
            latest["Volume_Breakout_5D"]
        ),
        "stock_return_20d": round(
            float(latest["Stock_Return_20D"]),
            2,
        ),
        "index_return_20d": round(
            float(latest["Index_Return_20D"]),
            2,
        ),
        "relative_strength_20d": round(
            float(relative_strength),
            2,
        ),
    }

def evaluate_symbol(
    symbol: str,
    reference_date: Optional[str] = None,
    end_date=None,
    market_config: Optional[dict] = None,
    entry_model: BaseStrategy | None = None,
) -> dict:
    entry_model = (
        entry_model
        or strategy
    )
    market_config = market_config or get_market_regime(end_date=end_date)

    base = {
        "symbol": symbol,
        "status": "REJECTED",
        "reason": "other",
    }

    df = _prepare_price_data(
        symbol=symbol,
        end_date=end_date,
    )

    if df.empty:
        return base

    if len(df) < MIN_DATA_ROWS:
        return {
            **base,
            "reason": "insufficient_data",
        }

    data = get_indicators_cached(
        symbol,
        df,
        end_date=end_date,
    )

    if data.empty:
        return base

    latest = data.iloc[-1]

    latest_date = pd.to_datetime(
        latest["time"],
        errors="coerce",
    )

    if pd.isna(latest_date):
        return base

    date_text = latest_date.strftime("%Y-%m-%d")

    if reference_date is not None:
        reference_date_parsed = pd.to_datetime(
            reference_date,
            errors="coerce",
        )

        if pd.isna(reference_date_parsed):
            return {
                **base,
                "reason": "invalid_reference_date",
            }

        reference_date_text = reference_date_parsed.strftime(
            "%Y-%m-%d"
        )

        if date_text != reference_date_text:
            return {
                **base,
                "reason": "stale_data",
                "latest_date": date_text,
                "reference_date": reference_date_text,
            }

    rs = calculate_relative_strength(
        symbol,
        benchmark="VNINDEX",
        period=RS_PERIOD,
        as_of_date=latest["time"],
    )

    prepared_latest = latest.copy()

    prepared_latest[
        "Stock_Return_20D"
    ] = rs["stock_return"]

    prepared_latest[
        "Index_Return_20D"
    ] = rs["index_return"]

    prepared_latest[
        "Relative_Strength_20D"
    ] = rs["relative_strength"]

    return evaluate_prepared_row(
        symbol=symbol,
        latest=prepared_latest,
        market_config=market_config,
        entry_model=entry_model,
    )

def check_signal(
    symbol,
    reference_date=None,
    end_date=None,
    market_config=None,
):
    """Backtest-compatible API."""
    evaluation = evaluate_symbol(
        symbol=symbol,
        reference_date=reference_date,
        end_date=end_date,
        market_config=market_config,
    )

    if evaluation["status"] == "PASSED":
        return evaluation

    return None


def scan_all_symbols(market_config=None):
    market_config = market_config or get_market_regime()
    symbols = [str(symbol).strip().upper() for symbol in get_vn100_symbols()]
    if not symbols:
        raise RuntimeError("Không lấy được snapshot VN100; dừng scan để tránh sai universe.")
    reference_date = get_reference_market_date()
    latest_dates = get_symbol_latest_dates()
    fresh_symbols = [symbol for symbol in symbols if latest_dates.get(symbol) == reference_date]
    stale_symbols = [
        {"symbol": symbol, "latest_date": latest_dates.get(symbol)}
        for symbol in symbols if latest_dates.get(symbol) != reference_date
    ]

    print("\n" + "=" * 65)
    print("📅 KIỂM TRA NGÀY DỮ LIỆU")
    print("=" * 65)
    print(f"Ngày chuẩn thị trường: {reference_date}")
    print(f"Mã dữ liệu đúng ngày: {len(fresh_symbols)}/{len(symbols)}")
    print(f"Mã dữ liệu cũ/lỗi: {len(stale_symbols)}")

    signals: list[dict] = []
    watchlist: list[dict] = []
    evaluations: list[dict] = []
    scan_errors: list[dict] = []
    reject_stats: Counter = Counter()
    condition_fail_stats: Counter = Counter()

    print(f"\n🔍 Bắt đầu quét {len(fresh_symbols)} mã hợp lệ...")
    for index, symbol in enumerate(fresh_symbols, start=1):
        print(f"\rĐang quét {index}/{len(fresh_symbols)}: {symbol}", end="", flush=True)
        try:
            evaluation = evaluate_symbol(
                symbol,
                reference_date=reference_date,
                market_config=market_config,
            )
            evaluations.append(evaluation)
            reject_stats[evaluation["reason"]] += 1
            condition_fail_stats.update(evaluation.get("failed_conditions", []))
            if evaluation["status"] == "PASSED":
                signals.append(evaluation)
            elif evaluation["status"] == "WATCHLIST":
                watchlist.append(evaluation)
        except Exception as error:
            reject_stats["exception"] += 1
            scan_errors.append({"symbol": symbol, "error": str(error)})
            print(f"\n❌ Lỗi quét {symbol}: {error}")

    sort_key = lambda item: (item["score"], item["relative_strength_20d"], item["volume_ratio"], item["adx"])
    signals.sort(key=sort_key, reverse=True)
    watchlist.sort(key=sort_key, reverse=True)

    scan_stats = {
        "reference_date": reference_date,
        "total_symbols": len(symbols),
        "fresh_count": len(fresh_symbols),
        "stale_count": len(stale_symbols),
        "stale_symbols": stale_symbols,
        "error_count": len(scan_errors),
        "scan_errors": scan_errors,
        "reject_stats": dict(reject_stats),
        "condition_fail_stats": dict(condition_fail_stats),
        "watchlist": watchlist,
        "evaluations": evaluations,
        "market_config": market_config,
    }
    return signals, scan_stats


def run_scan(
    *,
    pending_execution_result: (
        PaperExecutionBatchResult | None
    ) = None,
) -> tuple[list[dict], dict]:
    """Run the production scan, persist passed signals and notify Telegram."""
    market_config = get_market_regime()
    print("\n" + "=" * 65)
    print("📊 MARKET REGIME")
    print("=" * 65)
    print(f"Trạng thái: {market_config['regime']}")
    print(f"Điểm tối thiểu: {market_config['min_score']}")
    print(f"ADX tối thiểu: {market_config['min_adx']}")
    print(f"Volume tối thiểu: {market_config['min_volume_ratio']:.2f}x MA20")
    print(f"RS tối thiểu: {market_config['min_relative_strength']:+.2f}%")

    results, scan_stats = scan_all_symbols(market_config=market_config)
    watchlist = scan_stats["watchlist"]
    print_scan_results(
        results,
        watchlist,
        top_results=TOP_RESULTS,
        top_watchlist=TOP_WATCHLIST,
    )
    print_end_of_day_dashboard(
        results,
        scan_stats,
    )
    print_scan_diagnostics(
        results=results,
        scan_stats=scan_stats,
    )

    try:
        persist_scan_telemetry(
            results=results,
            scan_stats=scan_stats,
        )
        print(
            "\n✅ Đã lưu scan telemetry "
            "vào market database."
        )
    except Exception as error:
        print(
            "\n⚠️ Không lưu được scan telemetry; "
            f"scanner vẫn tiếp tục: {error}"
        )

    saved_count = duplicate_count = save_failed_count = 0
    for signal in results:
        try:
            if save_signal(signal):
                saved_count += 1
            else:
                duplicate_count += 1
        except Exception as error:
            save_failed_count += 1
            print(f"❌ Không lưu được {signal['symbol']}: {error}")

    print("\n" + "-" * 60)
    print(f"Tín hiệu mới: {saved_count}")
    print(f"Tín hiệu trùng: {duplicate_count}")
    print(f"Lỗi lưu: {save_failed_count}")

    paper_result = (
        paper_signal_executor.queue_signals(
            results,
            report_date=(
                scan_stats["reference_date"]
            ),
        )
    )

    if paper_result.enabled:
        print("\n" + "-" * 60)
        print("PAPER TRADING")
        print(
            f"Queued for next open: "
            f"{paper_result.queued_count}"
        )
        print(
            f"Filled: "
            f"{paper_result.filled_count}"
        )
        print(
            f"Skipped: "
            f"{paper_result.skipped_count}"
        )
        print(
            f"Rejected: "
            f"{paper_result.rejected_count}"
        )
        print(
            f"Cash: "
            f"{paper_result.cash:,.0f}"
        )
        print(
            f"Equity: "
            f"{paper_result.equity:,.0f}"
        )

    try:
        message = build_scan_message(
            results,
            top_n=TOP_RESULTS,
            watchlist=watchlist,
            market_config=market_config,
        )

        telegram_result = (
            telegram_client.send_message(
                message
            )
        )

        if telegram_result.success:
            print(
                "\n✅ Đã gửi kết quả quét lên "
                "Telegram "
                f"({telegram_result.chunks_sent} phần)."
            )
        else:
            print(
                "\n⚠️ Không gửi được Telegram: "
                f"{telegram_result.error}"
            )

        paper_message = (
            build_paper_execution_message(
                paper_result,
                processed_result=(
                    pending_execution_result
                ),
            )
        )

        if paper_message:
            paper_telegram_result = (
                telegram_client.send_message(
                    paper_message
                )
            )

            if paper_telegram_result.success:
                print(
                    "✅ Đã gửi kết quả paper trading "
                    "lên Telegram."
                )
            else:
                print(
                    "⚠️ Không gửi được paper trading "
                    "lên Telegram: "
                    f"{paper_telegram_result.error}"
                )

    except Exception as error:
        print(
            "\n⚠️ Telegram bị bỏ qua, scanner "
            f"vẫn hoàn tất: {error}"
        )

    return results, scan_stats


if __name__ == "__main__":
    run_scan()
