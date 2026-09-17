from __future__ import annotations

import numpy as np
import pandas as pd

from core.database import load_price_data
from core.sector import fetch_sector_mapping
from core.universe import get_vn100_symbols
from research.run_sector_rs_predictive_audit import (
    build_observations,
    SECTOR_RS_PERIOD,
    FORWARD_HORIZONS,
)


VN_RS_PERIOD = 20
BUCKETS = 5


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or not {"time", "close"}.issubset(df.columns):
        return pd.DataFrame(columns=["time", "close"])
    out = df[["time", "close"]].copy()
    out["time"] = pd.to_datetime(out["time"], errors="coerce")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.dropna(subset=["time", "close"])
    out = out.drop_duplicates("time", keep="last")
    return out.sort_values("time").reset_index(drop=True)


def _rs_at_date(
    stock: pd.DataFrame,
    benchmark: pd.DataFrame,
    date: pd.Timestamp,
    period: int,
) -> float | None:
    stock_hist = stock[stock["time"] <= date].tail(period + 1)
    bench_hist = benchmark[benchmark["time"] <= date].tail(period + 1)
    if len(stock_hist) < period + 1 or len(bench_hist) < period + 1:
        return None

    stock_start = float(stock_hist.iloc[0]["close"])
    stock_end = float(stock_hist.iloc[-1]["close"])
    bench_start = float(bench_hist.iloc[0]["close"])
    bench_end = float(bench_hist.iloc[-1]["close"])

    if min(stock_start, bench_start) <= 0:
        return None

    return (stock_end / stock_start - 1.0) - (bench_end / bench_start - 1.0)


def add_vnindex_rs(
    observations: pd.DataFrame,
    price_data: dict[str, pd.DataFrame],
    benchmark_data: pd.DataFrame,
    *,
    period: int = VN_RS_PERIOD,
) -> pd.DataFrame:
    """Add point-in-time 20D RS vs VNINDEX to existing sector-RS observations."""
    if observations.empty:
        return observations.copy()

    benchmark = _prepare(benchmark_data)
    prepared = {
        symbol: _prepare(df)
        for symbol, df in price_data.items()
    }

    cache: dict[tuple[str, pd.Timestamp], float | None] = {}
    values = []

    for row in observations.itertuples(index=False):
        key = (row.symbol, pd.Timestamp(row.date))
        if key not in cache:
            cache[key] = _rs_at_date(
                prepared.get(row.symbol, pd.DataFrame()),
                benchmark,
                pd.Timestamp(row.date),
                period,
            )
        values.append(cache[key])

    out = observations.copy()
    out["rs_vnindex_20d"] = values
    return out


def add_daily_buckets(
    observations: pd.DataFrame,
    *,
    rs_column: str = "rs_sector_60d",
    buckets: int = BUCKETS,
) -> pd.DataFrame:
    if observations.empty:
        return observations.copy()

    out = observations.copy()
    bucket_values = []

    for _, group in out.groupby("date", sort=False):
        ranks = group[rs_column].rank(method="first")
        if len(group) < buckets:
            bucket_values.extend([np.nan] * len(group))
            continue
        bucket = pd.qcut(
            ranks,
            q=buckets,
            labels=False,
            duplicates="drop",
        )
        bucket_values.extend((bucket.astype(float) + 1).tolist())

    # groupby iteration preserves row order only if observations are grouped
    # contiguously by date; build a safer indexed assignment instead.
    out["bucket"] = np.nan
    for date, idx in out.groupby("date", sort=False).groups.items():
        group = out.loc[idx]
        if len(group) < buckets:
            continue
        ranks = group[rs_column].rank(method="first")
        bucket = pd.qcut(
            ranks,
            q=buckets,
            labels=False,
            duplicates="drop",
        )
        out.loc[idx, "bucket"] = bucket.astype(float) + 1

    return out


def summarize_distribution(
    observations: pd.DataFrame,
    *,
    horizons: tuple[int, ...] = FORWARD_HORIZONS,
) -> pd.DataFrame:
    """Overall median and positive-return hit rate by RS bucket."""
    if observations.empty:
        return pd.DataFrame()

    out = add_daily_buckets(observations)
    rows = []

    for bucket, group in out.dropna(subset=["bucket"]).groupby("bucket"):
        row = {
            "bucket": int(bucket),
            "observations": len(group),
            "mean_rs": float(group["rs_sector_60d"].mean()),
        }
        for horizon in horizons:
            col = f"forward_{horizon}d"
            values = pd.to_numeric(group[col], errors="coerce").dropna()
            row[f"median_forward_{horizon}d"] = (
                float(values.median()) if not values.empty else np.nan
            )
            row[f"hit_rate_{horizon}d"] = (
                float((values > 0).mean()) if not values.empty else np.nan
            )
        rows.append(row)

    return pd.DataFrame(rows).sort_values("bucket").reset_index(drop=True)


def summarize_yearly_spread(
    observations: pd.DataFrame,
    *,
    horizons: tuple[int, ...] = FORWARD_HORIZONS,
) -> pd.DataFrame:
    """Compute Q5-Q1 forward-return spread separately for each calendar year."""
    if observations.empty:
        return pd.DataFrame()

    out = add_daily_buckets(observations)
    out["year"] = pd.to_datetime(out["date"]).dt.year

    rows = []
    for year, group in out.groupby("year", sort=True):
        row = {"year": int(year)}
        for horizon in horizons:
            col = f"forward_{horizon}d"
            q1 = group.loc[group["bucket"] == 1, col].dropna()
            q5 = group.loc[group["bucket"] == 5, col].dropna()
            row[f"q1_mean_{horizon}d"] = float(q1.mean()) if not q1.empty else np.nan
            row[f"q5_mean_{horizon}d"] = float(q5.mean()) if not q5.empty else np.nan
            row[f"q5_minus_q1_{horizon}d"] = (
                float(q5.mean() - q1.mean())
                if not q1.empty and not q5.empty
                else np.nan
            )
        rows.append(row)

    return pd.DataFrame(rows)


def summarize_correlation(
    observations: pd.DataFrame,
) -> pd.DataFrame:
    """Correlation between Sector RS 60D and VNINDEX RS 20D."""
    if observations.empty or "rs_vnindex_20d" not in observations.columns:
        return pd.DataFrame()

    rows = []
    clean = observations[["rs_sector_60d", "rs_vnindex_20d"]].dropna()
    if len(clean) >= 2:
        pearson = clean["rs_sector_60d"].corr(clean["rs_vnindex_20d"], method="pearson")
        spearman = clean["rs_sector_60d"].rank().corr(clean["rs_vnindex_20d"].rank(),method="pearson",)
    else:
        pearson = np.nan
        spearman = np.nan

    rows.append({
        "observations": len(clean),
        "pearson": float(pearson) if pd.notna(pearson) else np.nan,
        "spearman": float(spearman) if pd.notna(spearman) else np.nan,
    })
    return pd.DataFrame(rows)


def main() -> None:
    universe = tuple(dict.fromkeys(
        str(s).strip().upper()
        for s in get_vn100_symbols()
        if s is not None and str(s).strip()
    ))
    mapping = fetch_sector_mapping()
    mapped = tuple(symbol for symbol in universe if symbol in mapping)

    print(
        f"Universe={len(universe)} | mapped={len(mapped)} | "
        f"Sector RS={SECTOR_RS_PERIOD}D | VNINDEX RS={VN_RS_PERIOD}D"
    )

    price_data = {symbol: load_price_data(symbol) for symbol in mapped}
    observations = build_observations(price_data, mapping, mapped)

    if observations.empty:
        raise RuntimeError("No predictive observations produced.")

    benchmark_data = load_price_data("VNINDEX")
    observations = add_vnindex_rs(observations, price_data, benchmark_data)

    distribution = summarize_distribution(observations)
    yearly = summarize_yearly_spread(observations)
    correlation = summarize_correlation(observations)

    print(f"Observations: {len(observations):,}")
    print(
        f"Dates: {observations['date'].min().date()} -> "
        f"{observations['date'].max().date()}"
    )
    print(f"Symbols with observations: {observations['symbol'].nunique()}")
    print()

    print("MEDIAN + HIT RATE BY BUCKET")
    print(distribution.to_string(index=False))
    print()

    print("YEARLY Q5 - Q1 SPREAD")
    print(yearly.to_string(index=False))
    print()

    print("SECTOR RS 60D vs VNINDEX RS 20D")
    print(correlation.to_string(index=False))
    print()

    print("INTERPRETATION RULE")
    print(
        "Research diagnostic only. Current VN100 membership and current sector "
        "mapping are used historically, so survivorship/point-in-time metadata "
        "bias remains. Do not change production policy from this output alone."
    )


if __name__ == "__main__":
    main()
