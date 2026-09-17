"""
Research-only PIT Market State diagnostic.

Builds cross-sectional breadth from the full database universe while avoiding
future/survivorship leakage caused by including a symbol before it had enough
history to support the indicator.

IMPORTANT:
- Research only. No production strategy logic is changed.
- Breadth at date T uses only observations <= T.
- A symbol becomes breadth-eligible only after MIN_HISTORY_SESSIONS
  observations ending on or before T. It must also have an observation on T.
- This controls listing/history-start bias inside the database universe. It
  cannot correct for symbols that are absent from the database entirely.
- Market-state thresholds remain exploratory and must be WFO validated.

Expected DB schema:
    prices(time, symbol, close)

Expected OOS CSV:
    entry_date, symbol, net_return_pct, fold
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd


MIN_HISTORY_SESSIONS = 50


def read_prices(db_path: str, symbols: list[str] | None = None) -> pd.DataFrame:
    con = sqlite3.connect(db_path)
    try:
        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            df = pd.read_sql_query(
                f"""
                SELECT time, symbol, close
                FROM prices
                WHERE symbol IN ({placeholders})
                ORDER BY time, symbol
                """,
                con,
                params=symbols,
            )
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
    df["symbol"] = df["symbol"].astype(str).str.upper()
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = (
        df.dropna(subset=["time", "symbol", "close"])
        .drop_duplicates(["time", "symbol"], keep="last")
        .sort_values(["symbol", "time"])
        .reset_index(drop=True)
    )
    return df


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
    df["base_regime"] = np.select([bull, bear], ["BULL", "BEAR"], default="SIDEWAY")
    return df


def calculate_pit_breadth(prices: pd.DataFrame, min_history: int) -> pd.DataFrame:
    """Calculate breadth using only each symbol's history available at T."""
    work = prices.copy()

    # EMA is causal: pandas ewm at row T only uses rows <= T.
    work["ema20"] = work.groupby("symbol")["close"].transform(
        lambda s: s.ewm(span=20, adjust=False).mean()
    )
    work["ema50"] = work.groupby("symbol")["close"].transform(
        lambda s: s.ewm(span=50, adjust=False).mean()
    )

    # Number of observations available for the symbol through date T.
    work["history_n"] = work.groupby("symbol").cumcount() + 1
    work["eligible"] = work["history_n"] >= min_history
    work["above_ema20"] = work["close"] > work["ema20"]
    work["above_ema50"] = work["close"] > work["ema50"]

    eligible = work[work["eligible"]].copy()
    breadth = (
        eligible.groupby("time", as_index=False)
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
    breadth["breadth_ema50_change_5d"] = (
        breadth["breadth_ema50_pct"] - breadth["breadth_ema50_pct"].shift(5)
    )
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

    if pd.isna(b50) or pd.isna(db50):
        return "UNKNOWN"
    if regime == "BEAR":
        return "BEAR"
    if regime == "BULL":
        if b50 < 50 and db50 < 0:
            return "DIVERGENT_BULL"
        if b50 >= 70 and db50 >= 0:
            return "HEALTHY_BULL"
        return "FRAGILE_BULL"
    if b50 >= 60 and db50 > 0:
        return "RECOVERY"
    return "NEUTRAL"


def profit_factor(r: pd.Series) -> float:
    gains = r[r > 0].sum()
    losses = -r[r < 0].sum()
    if losses <= 0:
        return np.inf if gains > 0 else np.nan
    return gains / losses


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for state, g in df.groupby("market_state", dropna=False):
        r = pd.to_numeric(g["net_return_pct"], errors="coerce").dropna()
        pnl = pd.to_numeric(g.get("net_pnl"), errors="coerce").dropna() if "net_pnl" in g else pd.Series(dtype=float)
        rows.append(
            {
                "market_state": state,
                "trades": len(r),
                "wins": int((r > 0).sum()),
                "win_rate_pct": round((r > 0).mean() * 100, 2) if len(r) else np.nan,
                "avg_net_return_pct": round(r.mean(), 3) if len(r) else np.nan,
                "median_net_return_pct": round(r.median(), 3) if len(r) else np.nan,
                "profit_factor": round(profit_factor(r), 3) if len(r) else np.nan,
                "total_net_pnl": round(pnl.sum(), 2) if len(pnl) else np.nan,
                "avg_breadth_ema50_pct": round(g["breadth_ema50_pct"].mean(), 2),
                "avg_breadth_change_10d": round(g["breadth_ema50_change_10d"].mean(), 2),
                "avg_breadth_universe": round(g["breadth_universe"].mean(), 1),
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
    parser.add_argument("--trades", required=True, help="exact OOS trade_level_oos.csv")
    parser.add_argument("--db", default="data/market.db")
    parser.add_argument("--output-dir", default="research_results/market_state_diagnostic_pit")
    parser.add_argument(
        "--symbols-file",
        default=None,
        help="Optional research universe. If omitted, all non-VNINDEX DB symbols are used.",
    )
    parser.add_argument(
        "--min-history",
        type=int,
        default=MIN_HISTORY_SESSIONS,
        help="Minimum per-symbol observations before breadth eligibility (default 50).",
    )
    args = parser.parse_args()

    min_history = args.min_history
    if min_history < 1:
        raise ValueError("--min-history must be >= 1")

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
        symbols = [x for x in symbols if x != "VNINDEX"]
        if not symbols:
            raise ValueError("--symbols-file không chứa symbol hợp lệ.")

    prices = read_prices(args.db, symbols=symbols)
    vn = load_vnindex(args.db)
    breadth = calculate_pit_breadth(prices, min_history=min_history)

    market = vn.merge(breadth, on="time", how="left", validate="one_to_one")
    market["market_state"] = market.apply(classify_state, axis=1)

    trades["entry_date"] = pd.to_datetime(trades["entry_date"], errors="coerce")
    enriched = trades.merge(
        market,
        left_on="entry_date",
        right_on="time",
        how="left",
        suffixes=("", "_market"),
        validate="many_to_one",
    ).drop(columns=["time"], errors="ignore")

    # Hard integrity checks: every non-null state must come from an entry date
    # that has a causal breadth record; no duplicate market dates after merge.
    missing_market = int(enriched["market_state"].isna().sum())
    if missing_market:
        raise RuntimeError(
            f"{missing_market} OOS trades không map được Market State. "
            "Kiểm tra date alignment/data coverage."
        )

    summary = summarize(enriched)
    stability = fold_stability(enriched)

    enriched.to_csv(out / "trade_level_market_state.csv", index=False)
    summary.to_csv(out / "market_state_summary.csv", index=False)
    stability.to_csv(out / "market_state_fold_stability.csv", index=False)
    breadth.to_csv(out / "market_health_pit_daily.csv", index=False)

    print("\n=== PIT FULL-MARKET STATE DIAGNOSTIC ===")
    print(f"Trades: {len(enriched)}")
    print(f"DB symbols used: {prices['symbol'].nunique()}")
    print(f"Min history sessions: {min_history}")
    print(f"Median breadth universe: {breadth['breadth_universe'].median():.1f}")
    print(f"Breadth universe min/max: {breadth['breadth_universe'].min()} / {breadth['breadth_universe'].max()}")
    print(f"Output: {out.resolve()}")

    print("\n--- State summary ---")
    print(summary.to_string(index=False))

    print("\n--- Fold stability ---")
    print(stability.to_string(index=False))

    print("\nINTEGRITY NOTES:")
    print("1) Breadth is calculated per symbol using only history <= each date T.")
    print("2) Symbols enter breadth only after the minimum history requirement.")
    print("3) Only symbols actually trading on T are included in that day's denominator.")
    print("4) This controls listing/history-start bias inside this DB, but cannot fix")
    print("   survivorship from symbols absent from the database altogether.")
    print("5) State thresholds remain exploratory; no production logic was changed.")


if __name__ == "__main__":
    main()
