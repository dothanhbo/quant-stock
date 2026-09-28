from __future__ import annotations

import numpy as np
import pandas as pd

from core.database import load_price_data
from core.sector import fetch_sector_mapping
from core.universe import get_vn100_symbols


SECTOR_RS_PERIOD = 60
FORWARD_HORIZONS = (5, 10, 20)
MIN_PEERS = 2
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


def _return_at(df: pd.DataFrame, date: pd.Timestamp, horizon: int) -> float | None:
    rows = df[df["time"] >= date]
    if rows.empty:
        return None

    start_idx = rows.index[0]
    end_pos = df.index.get_loc(start_idx) + horizon
    if end_pos >= len(df):
        return None

    start = float(df.loc[start_idx, "close"])
    end = float(df.iloc[end_pos]["close"])
    if not np.isfinite(start) or not np.isfinite(end) or start <= 0:
        return None
    return end / start - 1.0


def build_observations(
    price_data: dict[str, pd.DataFrame],
    sector_mapping: dict[str, str],
    universe_symbols: tuple[str, ...],
    *,
    rs_period: int = SECTOR_RS_PERIOD,
    horizons: tuple[int, ...] = FORWARD_HORIZONS,
    min_peers: int = MIN_PEERS,
) -> pd.DataFrame:
    """Build point-in-time sector-RS observations and future returns.

    Sector benchmark is equal-weighted across eligible VN100 peers and excludes
    the target stock. RS uses the prior rs_period observations ending at T.
    Forward returns start at T and end N trading observations later.
    """
    if rs_period <= 0:
        raise ValueError("rs_period must be > 0")

    universe = tuple(dict.fromkeys(
        str(s).strip().upper() for s in universe_symbols
        if s is not None and str(s).strip()
    ))
    mapping = {
        str(k).strip().upper(): str(v).strip()
        for k, v in sector_mapping.items()
        if k is not None and v is not None and str(v).strip()
    }

    prices = {
        symbol: _prepare(price_data.get(symbol, pd.DataFrame()))
        for symbol in universe
    }

    rows: list[dict] = []

    for symbol in universe:
        stock = prices[symbol]
        sector = mapping.get(symbol)
        if sector is None or len(stock) < rs_period + 1:
            continue

        peers = tuple(
            peer for peer in universe
            if peer != symbol and mapping.get(peer) == sector
        )
        if len(peers) < min_peers:
            continue

        peer_frames = {
            peer: prices[peer]
            for peer in peers
            if len(prices[peer]) >= rs_period + 1
        }
        if len(peer_frames) < min_peers:
            continue

        # Only evaluate dates for which the target and every selected peer have
        # enough history. This keeps the benchmark genuinely point-in-time.
        candidate_dates = stock["time"].tolist()

        for date in candidate_dates:
            stock_hist = stock[stock["time"] <= date]
            if len(stock_hist) < rs_period + 1:
                continue

            stock_rows = stock_hist.tail(rs_period + 1)
            stock_start = float(stock_rows.iloc[0]["close"])
            stock_end = float(stock_rows.iloc[-1]["close"])
            if stock_start <= 0:
                continue

            peer_returns = []
            eligible_peers = 0
            for peer, peer_df in peer_frames.items():
                peer_hist = peer_df[peer_df["time"] <= date]
                if len(peer_hist) < rs_period + 1:
                    continue
                peer_rows = peer_hist.tail(rs_period + 1)
                start = float(peer_rows.iloc[0]["close"])
                end = float(peer_rows.iloc[-1]["close"])
                if start <= 0:
                    continue
                peer_returns.append(end / start - 1.0)
                eligible_peers += 1

            if eligible_peers < min_peers:
                continue

            stock_return = stock_end / stock_start - 1.0
            sector_return = float(np.mean(peer_returns))
            rs_sector = stock_return - sector_return

            observation = {
                "date": pd.Timestamp(date),
                "symbol": symbol,
                "sector": sector,
                "rs_sector_60d": rs_sector,
                "stock_return_60d": stock_return,
                "sector_return_60d": sector_return,
                "peer_count": eligible_peers,
            }

            for horizon in horizons:
                observation[f"forward_{horizon}d"] = _return_at(
                    stock, pd.Timestamp(date), horizon
                )

            rows.append(observation)

    return pd.DataFrame(rows)


def summarize_buckets(
    observations: pd.DataFrame,
    *,
    rs_column: str = "rs_sector_60d",
    buckets: int = BUCKETS,
    horizons: tuple[int, ...] = FORWARD_HORIZONS,
) -> pd.DataFrame:
    if observations.empty:
        return pd.DataFrame()

    frames = []
    for date, group in observations.groupby("date", sort=True):
        group = group.dropna(subset=[rs_column]).copy()
        if len(group) < buckets:
            continue

        group["bucket"] = pd.qcut(
            group[rs_column],
            q=buckets,
            labels=False,
            duplicates="drop",
        )
        group["bucket"] = group["bucket"].astype("Int64")

        for bucket, bucket_group in group.groupby("bucket", dropna=True):
            row = {
                "date": date,
                "bucket": int(bucket) + 1,
                "n": len(bucket_group),
                "mean_rs": float(bucket_group[rs_column].mean()),
                "median_rs": float(bucket_group[rs_column].median()),
            }
            for horizon in horizons:
                col = f"forward_{horizon}d"
                values = pd.to_numeric(bucket_group[col], errors="coerce").dropna()
                row[f"mean_forward_{horizon}d"] = (
                    float(values.mean()) if not values.empty else np.nan
                )
                row[f"median_forward_{horizon}d"] = (
                    float(values.median()) if not values.empty else np.nan
                )
            frames.append(row)

    return pd.DataFrame(frames)


def summarize_spread(
    bucket_summary: pd.DataFrame,
    *,
    horizons: tuple[int, ...] = FORWARD_HORIZONS,
) -> pd.DataFrame:
    if bucket_summary.empty:
        return pd.DataFrame()

    rows = []
    for horizon in horizons:
        low = bucket_summary.loc[
            bucket_summary["bucket"] == 1, f"mean_forward_{horizon}d"
        ].dropna()
        high = bucket_summary.loc[
            bucket_summary["bucket"] == bucket_summary["bucket"].max(),
            f"mean_forward_{horizon}d",
        ].dropna()

        rows.append({
            "horizon": horizon,
            "low_bucket_mean": float(low.mean()) if not low.empty else np.nan,
            "high_bucket_mean": float(high.mean()) if not high.empty else np.nan,
            "high_minus_low": (
                float(high.mean() - low.mean())
                if not low.empty and not high.empty
                else np.nan
            ),
            "dates_low": int(len(low)),
            "dates_high": int(len(high)),
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
    if not mapped:
        raise RuntimeError("No mapped VN100 symbols.")

    print(
        f"Universe={len(universe)} | mapped={len(mapped)} | "
        f"RS period={SECTOR_RS_PERIOD} | horizons={FORWARD_HORIZONS}"
    )

    price_data = {
        symbol: load_price_data(symbol)
        for symbol in mapped
    }

    observations = build_observations(
        price_data,
        mapping,
        mapped,
    )
    if observations.empty:
        raise RuntimeError("No predictive observations produced.")

    summary = summarize_buckets(observations)
    spread = summarize_spread(summary)

    print(f"Observations: {len(observations):,}")
    print(
        f"Dates: {observations['date'].min().date()} -> "
        f"{observations['date'].max().date()}"
    )
    print(f"Symbols with observations: {observations['symbol'].nunique()}")
    print()
    print("BUCKETS (1=lowest RS, 5=highest RS)")
    print(
        summary.groupby("bucket", as_index=False)
        .agg(
            dates=("date", "nunique"),
            observations=("n", "sum"),
            mean_rs=("mean_rs", "mean"),
            forward_5d=("mean_forward_5d", "mean"),
            forward_10d=("mean_forward_10d", "mean"),
            forward_20d=("mean_forward_20d", "mean"),
        )
        .to_string(index=False)
    )
    print()
    print("HIGH - LOW SPREAD")
    print(spread.to_string(index=False))

    print()
    print("INTERPRETATION RULE")
    print(
        "This is a predictive diagnostic, not a strategy backtest. "
        "Do not change Q70 or production policy from this output alone."
    )


if __name__ == "__main__":
    main()
