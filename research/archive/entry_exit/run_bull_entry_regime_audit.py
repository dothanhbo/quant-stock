"""Read-only, point-in-time diagnosis of BULL-regime OOS trades.

This runner does not create a new entry rule or re-run walk-forward selection.
It enriches already executed OOS trades with features available on the signal
session (the session before the next-open entry), then compares BULL with
SIDEWAY and exposes stable segments for later hypothesis testing.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from backtesting.prepared_data import (
    load_backtest_price_data,
    prepare_backtest_dataset,
)
from research.universes import HOLDOUT20_SYMBOLS
from strategy.indicators import add_indicators
from strategy.market_regime import prepare_market_regime_history


DEFAULT_CASE_ID = "fixed_atr_2_4"
DEFAULT_OUTPUT_DIR = Path("research_results/bull_entry_regime_audit")


def finite(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def labeled_bucket(
    value: float | int | None,
    *,
    edges: list[float],
    labels: list[str],
) -> str:
    number = finite(value, default=math.nan)
    if not math.isfinite(number):
        return "UNKNOWN"
    index = int(np.digitize(number, edges, right=False))
    return labels[min(index, len(labels) - 1)]


def profit_factor(pnl: pd.Series) -> float:
    numeric = pd.to_numeric(pnl, errors="coerce").fillna(0.0)
    wins = float(numeric[numeric > 0].sum())
    losses = abs(float(numeric[numeric < 0].sum()))
    if losses == 0:
        return math.inf if wins > 0 else 0.0
    return wins / losses


def aggregate(
    frame: pd.DataFrame,
    *,
    dimensions: Iterable[str],
) -> pd.DataFrame:
    rows: list[dict] = []
    for dimension in dimensions:
        if dimension not in frame:
            continue
        for value, group in frame.groupby(dimension, dropna=False):
            returns = pd.to_numeric(group["return_pct"], errors="coerce")
            pnl = pd.to_numeric(group["pnl"], errors="coerce").fillna(0.0)
            rows.append(
                {
                    "dimension": dimension,
                    "value": str(value),
                    "trades": int(len(group)),
                    "wins": int((pnl > 0).sum()),
                    "win_rate_pct": float((pnl > 0).mean() * 100),
                    "average_return_pct": float(returns.mean()),
                    "median_return_pct": float(returns.median()),
                    "total_net_pnl": float(pnl.sum()),
                    "profit_factor": profit_factor(pnl),
                    "average_holding_days": float(
                        pd.to_numeric(
                            group["holding_days"], errors="coerce"
                        ).mean()
                    ),
                }
            )
    return pd.DataFrame(rows)


def calculate_breadth(
    *,
    symbols: list[str],
    db_path: str,
) -> pd.DataFrame:
    frames = []
    for symbol in sorted(set(symbols)):
        raw = load_backtest_price_data(symbol, db_path=db_path)
        if raw.empty:
            continue
        data = add_indicators(raw)
        data["time"] = pd.to_datetime(data["time"]).dt.normalize()
        frames.append(
            data[["time", "close", "EMA20", "EMA50"]].assign(
                symbol=symbol
            )
        )
    if not frames:
        return pd.DataFrame(columns=["time"])
    all_data = pd.concat(frames, ignore_index=True)
    return (
        all_data.assign(
            above_ema20=all_data["close"] > all_data["EMA20"],
            above_ema50=all_data["close"] > all_data["EMA50"],
        )
        .groupby("time", as_index=False)
        .agg(
            breadth_symbols=("symbol", "nunique"),
            breadth_above_ema20_pct=("above_ema20", lambda values: float(values.mean() * 100)),
            breadth_above_ema50_pct=("above_ema50", lambda values: float(values.mean() * 100)),
        )
    )


def prepare_market_features(db_path: str) -> pd.DataFrame:
    benchmark = load_backtest_price_data("VNINDEX", db_path=db_path)
    market = prepare_market_regime_history(benchmark)
    market["time"] = pd.to_datetime(market["time"]).dt.normalize()
    market["Market_Distance_EMA50_Pct"] = (
        (market["close"] / market["EMA50"] - 1) * 100
    )
    return market[
        [
            "time",
            "Market_Regime",
            "EMA50_Slope_10D",
            "Return_20D",
            "Distance_EMA200",
            "Market_Distance_EMA50_Pct",
        ]
    ].copy()


def lookup_signal_row(
    frame: pd.DataFrame,
    entry_date: pd.Timestamp,
) -> pd.Series | None:
    eligible = frame[frame["time"] < entry_date]
    if eligible.empty:
        return None
    return eligible.iloc[-1]


def enrich_selected_case(
    *,
    trades: pd.DataFrame,
    db_path: str,
    price_scale: float,
    breadth_symbols: list[str],
) -> pd.DataFrame:
    market = prepare_market_features(db_path).set_index("time")
    breadth = calculate_breadth(
        symbols=breadth_symbols,
        db_path=db_path,
    ).set_index("time")
    feature_cache: dict[str, pd.DataFrame] = {}
    rows: list[dict] = []

    for trade in trades.itertuples(index=False):
        symbol = str(trade.symbol).upper()
        if symbol not in feature_cache:
            prepared = prepare_backtest_dataset(symbol, db_path=db_path)
            prepared["time"] = pd.to_datetime(prepared["time"]).dt.normalize()
            feature_cache[symbol] = prepared
        entry_date = pd.Timestamp(trade.entry_date).normalize()
        signal = lookup_signal_row(feature_cache[symbol], entry_date)
        row = trade._asdict()
        row["entry_date"] = entry_date.date().isoformat()
        if signal is None:
            row["feature_status"] = "MISSING_SIGNAL_SESSION"
            rows.append(row)
            continue

        signal_date = pd.Timestamp(signal["time"]).normalize()
        row.update(
            {
                "feature_status": "OK",
                "signal_date": signal_date.date().isoformat(),
                "signal_close": finite(signal.get("close")),
                "signal_rsi": finite(signal.get("RSI")),
                "signal_adx": finite(signal.get("ADX14")),
                "signal_vol_ratio": finite(signal.get("Vol_Ratio")),
                "signal_atr_percent": finite(signal.get("ATR_Percent")),
                "signal_distance_ema20_pct": finite(
                    signal.get("Distance_EMA20_Pct")
                ),
                "signal_return_3d_pct": finite(signal.get("Return_3D_Pct")),
                "signal_stock_return_20d_pct": finite(
                    signal.get("Stock_Return_20D")
                ),
                "signal_relative_strength_20d": finite(
                    signal.get("Relative_Strength_20D")
                ),
                "signal_breakout_20d": bool(signal.get("Breakout_20D", False)),
                "signal_volume_breakout_5d": bool(
                    signal.get("Volume_Breakout_5D", False)
                ),
                "signal_ema20_rising": bool(signal.get("EMA20_Rising", False)),
                "signal_above_ema50": bool(
                    finite(signal.get("close")) > finite(signal.get("EMA50"))
                ),
                "signal_ema20_above_ema50": bool(
                    finite(signal.get("EMA20")) > finite(signal.get("EMA50"))
                ),
                "signal_breakout_distance_pct": (
                    (finite(signal.get("close")) / finite(signal.get("Previous_20D_High")) - 1) * 100
                    if finite(signal.get("Previous_20D_High")) > 0
                    else math.nan
                ),
                "entry_gap_from_signal_close_pct": (
                    (finite(getattr(trade, "entry_price")) / price_scale / finite(signal.get("close")) - 1) * 100
                    if finite(signal.get("close")) > 0
                    else math.nan
                ),
            }
        )
        if signal_date in market.index:
            for column, value in market.loc[signal_date].items():
                if column != "Market_Regime":
                    row[f"market_{column.lower()}"] = finite(value)
        if signal_date in breadth.index:
            for column, value in breadth.loc[signal_date].items():
                row[column] = finite(value)
        rows.append(row)

    result = pd.DataFrame(rows)
    return add_buckets(result)


def add_buckets(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    specs = {
        "signal_score_bucket": ("signal_score", [70, 80, 85], ["<70", "70-79", "80-84", "85+"]),
        "signal_rs_bucket": ("signal_relative_strength_20d", [5, 10, 20], ["<5%", "5-9.9%", "10-19.9%", "20%+"]),
        "signal_adx_bucket": ("signal_adx", [25, 30, 40], ["<25", "25-29.9", "30-39.9", "40+"]),
        "signal_volume_bucket": ("signal_vol_ratio", [1.3, 1.8, 2.5], ["<1.3x", "1.3-1.79x", "1.8-2.49x", "2.5x+"]),
        "signal_rsi_bucket": ("signal_rsi", [55, 60, 65], ["<55", "55-59.9", "60-64.9", "65+"]),
        "signal_distance_ema20_bucket": ("signal_distance_ema20_pct", [3, 6, 9], ["<3%", "3-5.9%", "6-8.9%", "9%+"]),
        "signal_return_3d_bucket": ("signal_return_3d_pct", [3, 6, 10], ["<3%", "3-5.9%", "6-9.9%", "10%+"]),
        "market_return20_bucket": ("market_return_20d", [3, 7, 12], ["<3%", "3-6.9%", "7-11.9%", "12%+"]),
        "market_slope_bucket": ("market_ema50_slope_10d", [0.5, 1.5, 3], ["<0.5%", "0.5-1.49%", "1.5-2.99%", "3%+"]),
        "market_distance_ema200_bucket": ("market_distance_ema200", [3, 8, 15], ["<3%", "3-7.9%", "8-14.9%", "15%+"]),
        "breadth_ema50_bucket": ("breadth_above_ema50_pct", [50, 65, 80], ["<50%", "50-64%", "65-79%", "80%+"]),
        "entry_gap_bucket": ("entry_gap_from_signal_close_pct", [0, 1, 3], ["gap down", "0-0.99%", "1-2.99%", "3%+"]),
    }
    for output, (source, edges, labels) in specs.items():
        result[output] = [
            labeled_bucket(value, edges=edges, labels=labels)
            for value in result.get(source, pd.Series(index=result.index, dtype=float))
        ]
    result["signal_year"] = pd.to_datetime(
        result["signal_date"], errors="coerce"
    ).dt.year.astype("Int64").astype(str)
    return result


def build_summary(
    enriched: pd.DataFrame,
    *,
    selected_case: str,
) -> dict:
    result = {
        "case_id": selected_case,
        "feature_timing": "signal session strictly before next-open entry",
        "feature_status_counts": enriched["feature_status"].value_counts().to_dict(),
    }
    for regime in ("BULL", "SIDEWAY"):
        group = enriched[
            (enriched["market_regime"] == regime)
            & (enriched["feature_status"] == "OK")
        ]
        pnl = pd.to_numeric(group["pnl"], errors="coerce").fillna(0.0)
        returns = pd.to_numeric(group["return_pct"], errors="coerce")
        result[regime.lower()] = {
            "trades": int(len(group)),
            "win_rate_pct": float((pnl > 0).mean() * 100) if len(group) else 0.0,
            "average_return_pct": finite(returns.mean()),
            "median_return_pct": finite(returns.median()),
            "total_net_pnl": float(pnl.sum()),
            "profit_factor": profit_factor(pnl),
        }
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only point-in-time BULL entry/regime diagnostic."
    )
    parser.add_argument("--trades", required=True, help="v2 trade_level_oos.csv")
    parser.add_argument("--db", default="data/market.db")
    parser.add_argument("--case", default=DEFAULT_CASE_ID)
    parser.add_argument("--price-scale", type=float, default=1000.0)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.price_scale <= 0:
        raise ValueError("--price-scale must be greater than 0")
    trades_path = Path(args.trades)
    if not trades_path.exists():
        raise FileNotFoundError(f"Không tìm thấy trades file: {trades_path}")
    trades = pd.read_csv(trades_path)
    required = {"case_id", "market_regime", "entry_date", "entry_price", "pnl", "return_pct"}
    missing = required.difference(trades.columns)
    if missing:
        raise ValueError("Trades file thiếu cột: " + ", ".join(sorted(missing)))
    selected = trades[trades["case_id"] == args.case].copy()
    if selected.empty:
        cases = ", ".join(sorted(trades["case_id"].dropna().unique()))
        raise ValueError(f"Không tìm thấy case {args.case}. Có: {cases}")

    enriched = enrich_selected_case(
        trades=selected,
        db_path=args.db,
        price_scale=args.price_scale,
        breadth_symbols=list(HOLDOUT20_SYMBOLS),
    )
    bull = enriched[enriched["market_regime"] == "BULL"].copy()
    comparison = enriched[enriched["market_regime"].isin(["BULL", "SIDEWAY"])].copy()
    dimensions = [
        "signal_year", "symbol", "signal_score_bucket", "signal_rs_bucket",
        "signal_adx_bucket", "signal_volume_bucket", "signal_rsi_bucket",
        "signal_distance_ema20_bucket", "signal_return_3d_bucket",
        "market_return20_bucket", "market_slope_bucket",
        "market_distance_ema200_bucket", "breadth_ema50_bucket",
        "entry_gap_bucket", "exit_reason",
    ]
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    enriched.to_csv(output / "selected_case_feature_enriched.csv", index=False, encoding="utf-8-sig")
    bull.to_csv(output / "bull_trade_enriched.csv", index=False, encoding="utf-8-sig")
    aggregate(bull, dimensions=dimensions).to_csv(
        output / "bull_feature_diagnostics.csv", index=False, encoding="utf-8-sig"
    )
    aggregate(comparison, dimensions=["market_regime", *dimensions]).to_csv(
        output / "bull_vs_sideway_diagnostics.csv", index=False, encoding="utf-8-sig"
    )
    summary = build_summary(enriched, selected_case=args.case)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Saved: {output.resolve()}")


if __name__ == "__main__":
    main()
