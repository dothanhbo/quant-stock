"""
Research-only diagnostic: split BULL/SIDEWAY/BEAR into finer Market States.

Purpose
-------
Enrich the exact OOS trade-level file with causal VNINDEX + breadth features and
summarize trade outcomes by exploratory market state.

IMPORTANT:
- This script is RESEARCH ONLY. It does not modify production strategy logic.
- Breadth is calculated using only data available on each date.
- State thresholds are deliberately explicit and exploratory; do not promote
  them to production without WFO validation.
- By default, breadth uses all non-VNINDEX symbols present in data/market.db.
  For a stricter universe, pass --symbols-file with one symbol per line.

Expected DB schema:
    prices(time, symbol, close)

Expected OOS CSV:
    entry_date, symbol, net_return_pct, net_pnl, is_win, fold
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd


def load_prices(db_path: str, symbols: list[str] | None = None) -> pd.DataFrame:
    con = sqlite3.connect(db_path)
    try:
        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            sql = f"""
                SELECT time, symbol, close
                FROM prices
                WHERE symbol IN ({placeholders})
                ORDER BY time, symbol
            """
            df = pd.read_sql_query(sql, con, params=symbols)
        else:
            df = pd.read_sql_query(
                """
                SELECT time, symbol, close
                FROM prices
                WHERE symbol <> 'VNINDEX'
                ORDER BY time, symbol
                """,
                con,
            )
    finally:
        con.close()

    if df.empty:
        raise RuntimeError("Không đọc được dữ liệu prices từ database.")

    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["symbol"] = df["symbol"].astype(str).str.upper()
    return (
        df.dropna(subset=["time", "close", "symbol"])
        .drop_duplicates(["time", "symbol"], keep="last")
        .sort_values(["symbol", "time"])
        .reset_index(drop=True)
    )


def load_vnindex(db_path: str) -> pd.DataFrame:
    con = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT time, close
            FROM prices
            WHERE symbol = 'VNINDEX'
            ORDER BY time
            """,
            con,
        )
    finally:
        con.close()

    if df.empty:
        raise RuntimeError("Không có dữ liệu VNINDEX.")

    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = (
        df.dropna(subset=["time", "close"])
        .drop_duplicates("time", keep="last")
        .sort_values("time")
        .reset_index(drop=True)
    )

    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
    df["ema50_slope_10d"] = (df["ema50"] / df["ema50"].shift(10) - 1) * 100
    df["return_20d"] = (df["close"] / df["close"].shift(20) - 1) * 100
    df["distance_ema200"] = (df["close"] / df["ema200"] - 1) * 100

    enough = np.arange(len(df)) >= 199
    bull = (
        enough
        & (df["close"] > df["ema50"])
        & (df["ema50"] > df["ema200"])
        & (df["ema50_slope_10d"] > 0)
        & (df["return_20d"] > -2)
    )
    bear = (
        enough
        & (df["close"] < df["ema200"])
        & (df["ema50"] < df["ema200"])
        & (df["ema50_slope_10d"] < 0)
    )

    df["base_regime"] = np.select(
        [bull, bear],
        ["BULL", "BEAR"],
        default="SIDEWAY",
    )
    return df


def calculate_breadth(prices: pd.DataFrame) -> pd.DataFrame:
    """Daily breadth using per-symbol EMA20/EMA50, then cross-sectional %."""
    work = prices.copy()
    work["ema20"] = work.groupby("symbol")["close"].transform(
        lambda s: s.ewm(span=20, adjust=False).mean()
    )
    work["ema50"] = work.groupby("symbol")["close"].transform(
        lambda s: s.ewm(span=50, adjust=False).mean()
    )

    work["above_ema20"] = work["close"] > work["ema20"]
    work["above_ema50"] = work["close"] > work["ema50"]

    breadth = (
        work.groupby("time", as_index=False)
        .agg(
            breadth_ema20=("above_ema20", "mean"),
            breadth_ema50=("above_ema50", "mean"),
            breadth_universe=("symbol", "nunique"),
        )
        .sort_values("time")
        .reset_index(drop=True)
    )

    breadth["breadth_ema20_pct"] = breadth["breadth_ema20"] * 100
    breadth["breadth_ema50_pct"] = breadth["breadth_ema50"] * 100

    # Causal change: today's breadth vs 10 sessions ago.
    breadth["breadth_ema50_change_10d"] = (
        breadth["breadth_ema50_pct"] - breadth["breadth_ema50_pct"].shift(10)
    )
    breadth["breadth_ema20_change_10d"] = (
        breadth["breadth_ema20_pct"] - breadth["breadth_ema20_pct"].shift(10)
    )

    return breadth


def classify_state(row: pd.Series) -> str:
    regime = row["base_regime"]
    b50 = row["breadth_ema50_pct"]
    db50 = row["breadth_ema50_change_10d"]

    if pd.isna(b50):
        return "UNKNOWN"

    if regime == "BEAR":
        # Keep BEAR as one state initially; do not over-fragment the downside.
        return "BEAR"

    if regime == "BULL":
        # Exploratory labels only.
        if b50 < 50 and db50 < 0:
            return "DIVERGENT_BULL"
        if b50 >= 70 and db50 >= 0:
            return "HEALTHY_BULL"
        return "FRAGILE_BULL"

    # SIDEWAY: look for broad participation improving before index confirms BULL.
    if b50 >= 60 and db50 > 0:
        return "RECOVERY"
    return "NEUTRAL"


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    def pf(x: pd.Series) -> float:
        gains = x[x > 0].sum()
        losses = -x[x < 0].sum()
        if losses <= 0:
            return np.inf if gains > 0 else np.nan
        return gains / losses

    rows = []
    for state, g in df.groupby("market_state", dropna=False):
        r = pd.to_numeric(g["net_return_pct"], errors="coerce").dropna()
        pnl = pd.to_numeric(g.get("net_pnl"), errors="coerce").dropna()

        rows.append(
            {
                "market_state": state,
                "trades": len(g),
                "wins": int((r > 0).sum()),
                "win_rate_pct": round((r > 0).mean() * 100, 2) if len(r) else np.nan,
                "avg_net_return_pct": round(r.mean(), 3) if len(r) else np.nan,
                "median_net_return_pct": round(r.median(), 3) if len(r) else np.nan,
                "profit_factor": round(pf(r), 3) if len(r) else np.nan,
                "total_net_pnl": round(pnl.sum(), 2) if len(pnl) else np.nan,
                "avg_breadth_ema50_pct": round(g["breadth_ema50_pct"].mean(), 2),
                "avg_breadth_change_10d": round(g["breadth_ema50_change_10d"].mean(), 2),
            }
        )

    return pd.DataFrame(rows).sort_values("avg_net_return_pct", ascending=False)


def fold_stability(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (fold, state), g in df.groupby(["fold", "market_state"], dropna=False):
        r = pd.to_numeric(g["net_return_pct"], errors="coerce").dropna()
        if len(r) == 0:
            continue
        rows.append(
            {
                "fold": fold,
                "market_state": state,
                "trades": len(r),
                "win_rate_pct": round((r > 0).mean() * 100, 2),
                "avg_net_return_pct": round(r.mean(), 3),
                "median_net_return_pct": round(r.median(), 3),
            }
        )
    return pd.DataFrame(rows).sort_values(["market_state", "fold"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades", required=True, help="trade_level_oos.csv")
    parser.add_argument("--db", default="data/market.db")
    parser.add_argument("--output-dir", default="research_results/market_state_diagnostic")
    parser.add_argument(
        "--symbols-file",
        default=None,
        help="Optional file containing the exact breadth universe, one symbol/line.",
    )
    args = parser.parse_args()

    trades_path = Path(args.trades)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    trades = pd.read_csv(trades_path)
    required = {"entry_date", "symbol", "net_return_pct", "fold"}
    missing = required - set(trades.columns)
    if missing:
        raise ValueError(f"trade CSV thiếu columns: {sorted(missing)}")

    symbols = None
    if args.symbols_file:
        symbols = [
            x.strip().upper()
            for x in Path(args.symbols_file).read_text(encoding="utf-8").splitlines()
            if x.strip() and not x.strip().startswith("#")
        ]
        if "VNINDEX" in symbols:
            symbols.remove("VNINDEX")

    prices = load_prices(args.db, symbols=symbols)
    vn = load_vnindex(args.db)
    breadth = calculate_breadth(prices)

    market = vn.merge(breadth, on="time", how="left")
    market["market_state"] = market.apply(classify_state, axis=1)

    trades["entry_date"] = pd.to_datetime(trades["entry_date"], errors="coerce")
    enriched = trades.merge(
        market,
        left_on="entry_date",
        right_on="time",
        how="left",
        suffixes=("", "_market"),
    )

    # Preserve exact trade-level outcome; market features are frozen at entry date.
    enriched = enriched.drop(columns=["time"], errors="ignore")

    summary = summarize(enriched)
    stability = fold_stability(enriched)

    enriched.to_csv(out / "trade_level_market_state.csv", index=False)
    summary.to_csv(out / "market_state_summary.csv", index=False)
    stability.to_csv(out / "market_state_fold_stability.csv", index=False)

    print("\n=== MARKET STATE DIAGNOSTIC ===")
    print(f"Trades: {len(enriched)}")
    print(f"Breadth universe: {prices['symbol'].nunique()} symbols")
    print(f"Output: {out.resolve()}")

    print("\n--- State summary ---")
    print(summary.to_string(index=False))

    print("\n--- Fold stability ---")
    print(stability.to_string(index=False))

    print("\nIMPORTANT:")
    print("1) Đây là diagnostic, chưa phải production rule.")
    print("2) Thresholds: BULL healthy >=70% breadth + breadth rising;")
    print("   divergent = breadth <50% + breadth falling.")
    print("3) Nếu breadth universe không phải universe nghiên cứu chính, hãy chạy lại")
    print("   với --symbols-file để tránh kết luận sai.")
    print("4) Sau bước này mới kiểm tra incremental value so với score/RS/ADX/volume.")


if __name__ == "__main__":
    main()
