"""Audit market-price units and paper/backtest sizing parity.

The market database stores prices in thousand VND. Paper execution converts to
VND at its boundary; research must do the same before ATR sizing and lot-rounding.
This audit checks the convention without changing market or paper-trading data.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import pandas as pd

from execution.signal_executor import PaperSignalExecutor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit raw market-db price unit versus paper execution VND."
    )
    parser.add_argument("--db", default="data/market.db")
    parser.add_argument("--expected-scale", type=float, default=1000.0)
    parser.add_argument("--output", default="research_results/market_price_parity_audit.json")
    return parser


def load_latest_prices(db_path: Path) -> pd.DataFrame:
    query = """
        WITH latest AS (
            SELECT MAX(date(time)) AS market_date FROM prices
        )
        SELECT symbol, date(time) AS market_date, open, high, low, close, volume
        FROM prices
        WHERE date(time) = (SELECT market_date FROM latest)
          AND UPPER(symbol) <> 'VNINDEX'
          AND close > 0
        ORDER BY symbol
    """
    with sqlite3.connect(db_path) as connection:
        return pd.read_sql_query(query, connection)


def main() -> None:
    args = build_parser().parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        raise FileNotFoundError(f"Không tìm thấy database: {db_path.resolve()}")
    if args.expected_scale <= 0:
        raise ValueError("--expected-scale phải lớn hơn 0")

    latest = load_latest_prices(db_path)
    if latest.empty:
        raise RuntimeError("Không có giá đóng cửa hợp lệ để audit.")

    raw_median = float(latest["close"].median())
    raw_min = float(latest["close"].min())
    raw_max = float(latest["close"].max())
    vnd_median = raw_median * args.expected_scale
    paper_scale = float(PaperSignalExecutor.PRICE_SCALE)
    scale_matches_paper = paper_scale == float(args.expected_scale)
    plausible_vnd_range = 1_000 <= vnd_median <= 1_000_000

    report = {
        "status": "PASS" if scale_matches_paper and plausible_vnd_range else "FAIL",
        "database": str(db_path),
        "latest_market_date": str(latest.iloc[0]["market_date"]),
        "symbols_checked": int(len(latest)),
        "raw_market_unit": "thousand_vnd",
        "raw_close_range": {"min": raw_min, "median": raw_median, "max": raw_max},
        "expected_price_scale_to_vnd": float(args.expected_scale),
        "scaled_close_median_vnd": vnd_median,
        "paper_executor_price_scale": paper_scale,
        "paper_and_backtest_scale_match": scale_matches_paper,
        "median_scaled_price_is_plausible_vnd": plausible_vnd_range,
        "required_backtest_argument": (
            f"--market-price-scale {args.expected_scale:g}"
        ),
        "interpretation": (
            "Raw database prices must be multiplied by the configured scale "
            "before ATR Risk sizing, VND notional checks, and board-lot rounding."
        ),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
