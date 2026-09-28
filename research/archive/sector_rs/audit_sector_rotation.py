from __future__ import annotations

"""
Research-only audit: Sector Rotation / Sector Strength.

Purpose
-------
Measure whether sector-level relative strength / momentum contains useful
cross-sectional information before touching production strategy.

This script does NOT modify production strategy or paper-trading policy.

Design
------
- Universe: current VN100 symbols from the repository.
- Sector metadata: existing core.sector abstraction / vnstock reference.
- For each date, compute equal-weight sector returns over 20D and 60D.
- Rank sectors cross-sectionally on each date.
- For each stock, attach its sector rank and measure forward stock returns.
- Also report sector-level forward returns for top/middle/bottom sectors.
- Current sector mapping is used as metadata; this is explicitly research-only
  and therefore may contain survivorship / historical-classification bias.
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from core.database import load_price_data
from core.sector import fetch_sector_mapping
from core.universe import get_vn100_symbols


SECTOR_MOMENTUM_PERIODS = (20, 60)
FORWARD_HORIZONS = (5, 10, 20)
MIN_SECTOR_MEMBERS = 2
BUCKETS = 5


def _prepare_price_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["time", "close"])

    out = df.copy()
    time_col = "time" if "time" in out.columns else "date"
    if time_col not in out.columns or "close" not in out.columns:
        return pd.DataFrame(columns=["time", "close"])

    out["time"] = pd.to_datetime(out[time_col], errors="coerce").dt.normalize()
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out[["time", "close"]].dropna()
    out = out[out["close"] > 0]
    return out.drop_duplicates("time").sort_values("time").reset_index(drop=True)


def build_sector_daily_returns(
    universe_symbols: list[str],
    sector_mapping: dict[str, str],
) -> pd.DataFrame:
    """Build one equal-weight daily return series per sector."""
    member_returns: dict[str, list[pd.DataFrame]] = {}

    for symbol in universe_symbols:
        sector = sector_mapping.get(symbol)
        if not sector:
            continue

        prices = _prepare_price_frame(load_price_data(symbol))
        if prices.empty:
            continue

        prices["daily_return"] = prices["close"].pct_change()
        prices["symbol"] = symbol
        member_returns.setdefault(sector, []).append(
            prices[["time", "symbol", "daily_return"]]
        )

    rows = []
    for sector, frames in member_returns.items():
        members = len(frames)
        if members < MIN_SECTOR_MEMBERS:
            continue

        data = pd.concat(frames, ignore_index=True)
        daily = (
            data.groupby("time", as_index=False)["daily_return"]
            .mean()
            .rename(columns={"daily_return": "sector_daily_return"})
        )
        daily["sector"] = sector
        daily["member_count"] = members
        rows.append(daily)

    if not rows:
        return pd.DataFrame(
            columns=["time", "sector", "sector_daily_return", "member_count"]
        )

    out = pd.concat(rows, ignore_index=True)
    return out.sort_values(["time", "sector"]).reset_index(drop=True)


def add_sector_momentum(
    sector_daily: pd.DataFrame,
    periods: tuple[int, ...] = SECTOR_MOMENTUM_PERIODS,
) -> pd.DataFrame:
    rows = []

    for sector, group in sector_daily.groupby("sector"):
        g = group.sort_values("time").copy()
        for period in periods:
            g[f"sector_return_{period}d"] = (
                (1.0 + g["sector_daily_return"])
                .rolling(period)
                .apply(np.prod, raw=True)
                - 1.0
            )
        rows.append(g)

    if not rows:
        return sector_daily.copy()

    return pd.concat(rows, ignore_index=True).sort_values(
        ["time", "sector"]
    ).reset_index(drop=True)


def add_cross_sectional_ranks(
    sector_momentum: pd.DataFrame,
) -> pd.DataFrame:
    out = sector_momentum.copy()

    for period in SECTOR_MOMENTUM_PERIODS:
        col = f"sector_return_{period}d"
        rank_col = f"sector_rank_{period}d"
        pct_col = f"sector_percentile_{period}d"

        out[rank_col] = out.groupby("time")[col].rank(
            method="average", ascending=True
        )
        out[pct_col] = out.groupby("time")[col].rank(
            method="average", pct=True, ascending=True
        )

    return out


def build_stock_observations(
    universe_symbols: list[str],
    sector_mapping: dict[str, str],
    sector_momentum: pd.DataFrame,
) -> pd.DataFrame:
    """Attach point-in-time sector strength to each stock and forward returns."""
    sector_lookup = sector_momentum.set_index(["time", "sector"])

    rows = []

    for symbol in universe_symbols:
        sector = sector_mapping.get(symbol)
        if not sector:
            continue

        prices = _prepare_price_frame(load_price_data(symbol))
        if prices.empty:
            continue

        prices["symbol"] = symbol

        for _, row in prices.iterrows():
            date = row["time"]
            key = (date, sector)
            if key not in sector_lookup.index:
                continue

            sector_row = sector_lookup.loc[key]
            if isinstance(sector_row, pd.DataFrame):
                sector_row = sector_row.iloc[0]

            item = {
                "time": date,
                "symbol": symbol,
                "sector": sector,
                "close": float(row["close"]),
                "sector_return_20d": sector_row.get("sector_return_20d"),
                "sector_return_60d": sector_row.get("sector_return_60d"),
                "sector_percentile_20d": sector_row.get(
                    "sector_percentile_20d"
                ),
                "sector_percentile_60d": sector_row.get(
                    "sector_percentile_60d"
                ),
            }

            idx = prices.index[prices["time"] == date][0]
            for horizon in FORWARD_HORIZONS:
                future_idx = idx + horizon
                if future_idx >= len(prices):
                    item[f"forward_{horizon}d"] = np.nan
                else:
                    future_close = float(prices.iloc[future_idx]["close"])
                    item[f"forward_{horizon}d"] = (
                        future_close / float(row["close"]) - 1.0
                    )

            rows.append(item)

    return pd.DataFrame(rows)


def summarize_buckets(
    observations: pd.DataFrame,
    percentile_col: str,
) -> pd.DataFrame:
    data = observations.dropna(subset=[percentile_col]).copy()
    if data.empty:
        return pd.DataFrame()

    # Use date-wise quintiles so sector leadership is measured relative
    # to the available sectors on that date.
    data["bucket"] = data.groupby("time")[percentile_col].transform(
        lambda x: pd.qcut(
            x.rank(method="first"),
            q=min(BUCKETS, len(x)),
            labels=False,
            duplicates="drop",
        )
        + 1
        if len(x) >= BUCKETS
        else np.nan
    )

    data = data.dropna(subset=["bucket"]).copy()
    data["bucket"] = data["bucket"].astype(int)

    agg = {"observations": ("symbol", "size")}
    for horizon in FORWARD_HORIZONS:
        agg[f"mean_forward_{horizon}d"] = (
            f"forward_{horizon}d",
            "mean",
        )
        agg[f"median_forward_{horizon}d"] = (
            f"forward_{horizon}d",
            "median",
        )
        agg[f"hit_rate_{horizon}d"] = (
            f"forward_{horizon}d",
            lambda x: float((x > 0).mean()),
        )

    return (
        data.groupby("bucket")
        .agg(**agg)
        .reset_index()
        .sort_values("bucket")
    )


def summarize_sector_level(sector_momentum: pd.DataFrame) -> pd.DataFrame:
    """Summarize top/bottom sector forward performance."""
    rows = []

    for period in SECTOR_MOMENTUM_PERIODS:
        pct_col = f"sector_percentile_{period}d"
        for horizon in FORWARD_HORIZONS:
            # Sector forward return from T to T+h, using compounded daily
            # sector returns after T. This is a sector-level diagnostic.
            data = sector_momentum.sort_values(["sector", "time"]).copy()
            data[f"sector_forward_{horizon}d"] = data.groupby("sector")[
                "sector_daily_return"
            ].transform(
                lambda x: (1.0 + x.shift(-1))
                .rolling(horizon)
                .apply(np.prod, raw=True)
                .shift(-(horizon - 1))
                - 1.0
            )

            data = data.dropna(
                subset=[pct_col, f"sector_forward_{horizon}d"]
            )
            if data.empty:
                continue

            top = data.loc[
                data[pct_col] >= 0.8, f"sector_forward_{horizon}d"
            ]
            bottom = data.loc[
                data[pct_col] <= 0.2, f"sector_forward_{horizon}d"
            ]

            rows.append(
                {
                    "momentum_period": period,
                    "forward_horizon": horizon,
                    "top_sector_mean_forward": top.mean(),
                    "bottom_sector_mean_forward": bottom.mean(),
                    "top_minus_bottom_pp": (top.mean() - bottom.mean()) * 100,
                    "top_observations": len(top),
                    "bottom_observations": len(bottom),
                }
            )

    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        default="research_results/sector_rotation_audit",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    universe = sorted(set(get_vn100_symbols()))
    mapping = fetch_sector_mapping()

    universe = [s.strip().upper() for s in universe if str(s).strip()]
    mapping = {
        str(k).strip().upper(): str(v).strip()
        for k, v in mapping.items()
        if str(k).strip() and str(v).strip()
    }

    sector_daily = build_sector_daily_returns(universe, mapping)
    sector_momentum = add_sector_momentum(sector_daily)
    sector_momentum = add_cross_sectional_ranks(sector_momentum)

    observations = build_stock_observations(
        universe, mapping, sector_momentum
    )

    sector_daily.to_csv(output_dir / "sector_daily_returns.csv", index=False)
    sector_momentum.to_csv(
        output_dir / "sector_momentum_timeseries.csv", index=False
    )
    observations.to_csv(
        output_dir / "stock_sector_strength_observations.csv", index=False
    )

    summary_rows = []
    for period in SECTOR_MOMENTUM_PERIODS:
        pct = f"sector_percentile_{period}d"
        bucket = summarize_buckets(observations, pct)
        bucket["momentum_period"] = period
        summary_rows.append(bucket)

    bucket_summary = (
        pd.concat(summary_rows, ignore_index=True)
        if summary_rows
        else pd.DataFrame()
    )
    bucket_summary.to_csv(
        output_dir / "stock_forward_bucket_summary.csv", index=False
    )

    sector_summary = summarize_sector_level(sector_momentum)
    sector_summary.to_csv(
        output_dir / "sector_top_bottom_summary.csv", index=False
    )

    print("=== SECTOR ROTATION / STRENGTH RESEARCH AUDIT ===")
    print(f"VN100 universe: {len(universe)}")
    print(
        f"Mapped VN100: "
        f"{sum(1 for s in universe if s in mapping)}/{len(universe)}"
    )
    print(
        f"Eligible sectors: "
        f"{sector_daily['sector'].nunique() if not sector_daily.empty else 0}"
    )
    print(
        f"Sector observations: {len(sector_momentum):,}"
    )
    print(
        f"Stock observations: {len(observations):,}"
    )

    if not bucket_summary.empty:
        print("\n=== STOCK FORWARD RETURN BY SECTOR STRENGTH BUCKET ===")
        print(
            bucket_summary[
                [
                    "momentum_period",
                    "bucket",
                    "observations",
                    "mean_forward_5d",
                    "mean_forward_10d",
                    "mean_forward_20d",
                    "hit_rate_5d",
                    "hit_rate_10d",
                    "hit_rate_20d",
                ]
            ].to_string(index=False)
        )

        print("\n=== TOP BUCKET - BOTTOM BUCKET ===")
        for period in SECTOR_MOMENTUM_PERIODS:
            sub = bucket_summary[
                bucket_summary["momentum_period"] == period
            ]
            if len(sub) < 2:
                continue
            low = sub.iloc[0]
            high = sub.iloc[-1]
            print(
                f"{period}D: "
                f"5D {(high['mean_forward_5d'] - low['mean_forward_5d']) * 100:+.4f}pp, "
                f"10D {(high['mean_forward_10d'] - low['mean_forward_10d']) * 100:+.4f}pp, "
                f"20D {(high['mean_forward_20d'] - low['mean_forward_20d']) * 100:+.4f}pp"
            )

    if not sector_summary.empty:
        print("\n=== SECTOR-LEVEL TOP/BOTTOM ===")
        print(sector_summary.to_string(index=False))

    print("\nResearch-only: no production strategy/paper policy changed.")


if __name__ == "__main__":
    main()
