from __future__ import annotations

import numpy as np
import pandas as pd

from core.database import load_price_data
from core.sector import fetch_sector_mapping
from core.universe import get_vn100_symbols


def _prepare_price_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["time", "close"])

    out = df.copy()
    out["time"] = pd.to_datetime(out["time"], errors="coerce").dt.normalize()
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    return (
        out.dropna(subset=["time", "close"])
        .drop_duplicates("time", keep="last")
        .sort_values("time")
        .reset_index(drop=True)
    )


def build_sector_daily_returns(
    universe_symbols: list[str] | tuple[str, ...],
    sector_mapping: dict[str, str],
    as_of_date=None,
) -> pd.DataFrame:
    """Build equal-weight daily sector returns for the research universe."""
    rows = []
    cutoff = pd.Timestamp(as_of_date).normalize() if as_of_date is not None else None

    for symbol in universe_symbols:
        symbol = str(symbol).strip().upper()
        sector = sector_mapping.get(symbol)
        if not sector:
            continue

        prices = _prepare_price_frame(load_price_data(symbol))
        if cutoff is not None:
            prices = prices[prices["time"] <= cutoff]

        if len(prices) < 2:
            continue

        prices["daily_return"] = prices["close"].pct_change()
        prices["sector"] = sector
        prices["symbol"] = symbol
        rows.append(prices[["time", "symbol", "sector", "daily_return"]])

    if not rows:
        return pd.DataFrame(
            columns=["time", "sector", "sector_daily_return"]
        )

    stock_returns = pd.concat(rows, ignore_index=True)
    return (
        stock_returns.dropna(subset=["daily_return"])
        .groupby(["time", "sector"], as_index=False)["daily_return"]
        .mean()
        .rename(columns={"daily_return": "sector_daily_return"})
        .sort_values(["sector", "time"])
        .reset_index(drop=True)
    )


def add_sector_momentum(
    sector_daily_returns: pd.DataFrame,
    periods: tuple[int, ...] = (20, 60),
) -> pd.DataFrame:
    out = sector_daily_returns.copy()
    if out.empty:
        for period in periods:
            out[f"sector_return_{period}d"] = pd.Series(dtype=float)
        return out

    out["time"] = pd.to_datetime(out["time"]).dt.normalize()
    out = out.sort_values(["sector", "time"]).reset_index(drop=True)

    for period in periods:
        out[f"sector_return_{period}d"] = (
            out.groupby("sector")["sector_daily_return"]
            .transform(lambda s: (1.0 + s).rolling(period).apply(np.prod, raw=True) - 1.0)
        )

    return out


def add_cross_sectional_sector_rank(
    sector_momentum: pd.DataFrame,
    periods: tuple[int, ...] = (20, 60),
) -> pd.DataFrame:
    out = sector_momentum.copy()
    for period in periods:
        col = f"sector_return_{period}d"
        rank_col = f"sector_percentile_{period}d"
        out[rank_col] = out.groupby("time")[col].rank(
            method="average", pct=True
        )
    return out


def attach_sector_strength_to_stocks(
    stock_observations: pd.DataFrame,
    sector_strength: pd.DataFrame,
    sector_mapping: dict[str, str],
) -> pd.DataFrame:
    """Attach point-in-time sector strength to stock observations."""
    out = stock_observations.copy()
    if out.empty:
        return out

    out["symbol"] = out["symbol"].astype(str).str.strip().str.upper()
    out["time"] = pd.to_datetime(out["time"]).dt.normalize()

    strength = sector_strength.copy()
    strength["time"] = pd.to_datetime(strength["time"]).dt.normalize()

    strength = strength[
        [
            "time",
            "sector",
            "sector_return_20d",
            "sector_return_60d",
            "sector_percentile_20d",
            "sector_percentile_60d",
        ]
    ]

    out["sector"] = out["symbol"].map(sector_mapping)
    return out.merge(strength, on=["time", "sector"], how="left")


def build_stock_forward_observations(
    universe_symbols: list[str] | tuple[str, ...],
    sector_mapping: dict[str, str],
    sector_strength: pd.DataFrame,
    horizons: tuple[int, ...] = (5, 10, 20),
) -> pd.DataFrame:
    rows = []

    for symbol in universe_symbols:
        symbol = str(symbol).strip().upper()
        if symbol not in sector_mapping:
            continue

        prices = _prepare_price_frame(load_price_data(symbol))
        if len(prices) < max(horizons) + 1:
            continue

        obs = pd.DataFrame({"time": prices["time"], "symbol": symbol})
        for horizon in horizons:
            obs[f"forward_{horizon}d"] = (
                prices["close"].shift(-horizon) / prices["close"] - 1.0
            )

        rows.append(obs)

    if not rows:
        return pd.DataFrame()

    stocks = pd.concat(rows, ignore_index=True)
    return attach_sector_strength_to_stocks(stocks, sector_strength, sector_mapping)


def summarize_sector_strength_by_regime(
    observations: pd.DataFrame,
    regime: pd.DataFrame,
) -> pd.DataFrame:
    """Research-only: compare sector-strength tails within BULL/SIDEWAY/BEAR."""
    if observations.empty or regime.empty:
        return pd.DataFrame()

    out = observations.merge(regime[["time", "regime"]], on="time", how="inner")
    out = out.dropna(subset=["sector_percentile_20d", "regime"])

    rows = []
    for regime_name, group in out.groupby("regime"):
        low_cut = group["sector_percentile_20d"].quantile(0.20)
        high_cut = group["sector_percentile_20d"].quantile(0.80)

        low = group[group["sector_percentile_20d"] <= low_cut]
        high = group[group["sector_percentile_20d"] >= high_cut]

        for horizon in (5, 10, 20):
            col = f"forward_{horizon}d"
            rows.append(
                {
                    "regime": regime_name,
                    "forward_horizon": horizon,
                    "low_observations": len(low),
                    "high_observations": len(high),
                    "low_mean_forward": low[col].mean(),
                    "high_mean_forward": high[col].mean(),
                    "high_minus_low_pp": (high[col].mean() - low[col].mean()) * 100,
                }
            )

    return pd.DataFrame(rows)


def build_market_regime(vnindex_prices: pd.DataFrame) -> pd.DataFrame:
    """Research proxy matching the repository's BULL/SIDEWAY/BEAR definition."""
    prices = _prepare_price_frame(vnindex_prices)
    if prices.empty:
        return pd.DataFrame(columns=["time", "regime"])

    close = prices["close"]
    ema50 = close.ewm(span=50, adjust=False).mean()
    slope_10d = ema50.pct_change(10)
    return_20d = close.pct_change(20)

    regime = np.where(
        (close > ema50) & (slope_10d > 0) & (return_20d > 0),
        "BULL",
        np.where(
            (close < ema50) & (slope_10d < 0) & (return_20d < 0),
            "BEAR",
            "SIDEWAY",
        ),
    )

    return pd.DataFrame({"time": prices["time"], "regime": regime})


def run_audit() -> None:
    universe = tuple(
        str(s).strip().upper() for s in get_vn100_symbols()
    )
    mapping = fetch_sector_mapping()
    vn100_mapping = {s: mapping[s] for s in universe if s in mapping}

    sector_daily = build_sector_daily_returns(universe, vn100_mapping)
    sector_momentum = add_sector_momentum(sector_daily, periods=(20, 60))
    sector_strength = add_cross_sectional_sector_rank(
        sector_momentum, periods=(20, 60)
    )

    stocks = build_stock_forward_observations(
        universe,
        vn100_mapping,
        sector_strength,
        horizons=(5, 10, 20),
    )

    vnindex = _prepare_price_frame(load_price_data("VNINDEX"))
    regime = build_market_regime(vnindex)

    summary = summarize_sector_strength_by_regime(stocks, regime)

    print("=== SECTOR ROTATION × MARKET REGIME RESEARCH AUDIT ===")
    print(f"VN100 universe: {len(universe)}")
    print(f"Mapped VN100: {len(vn100_mapping)}/{len(universe)}")
    print(f"Sector observations: {len(sector_strength):,}")
    print(f"Stock observations: {len(stocks):,}")
    print(f"Regime observations: {len(regime):,}")
    print()
    print("=== 20D SECTOR STRENGTH: Q5 - Q1 BY REGIME ===")

    if summary.empty:
        print("No regime-aligned observations.")
        return

    for regime_name in ("BULL", "SIDEWAY", "BEAR"):
        part = summary[summary["regime"] == regime_name]
        print(f"\n{regime_name}")
        for _, row in part.sort_values("forward_horizon").iterrows():
            print(
                f"{int(row['forward_horizon'])}D: "
                f"{row['high_minus_low_pp']:+.4f}pp "
                f"(Q5={row['high_mean_forward']:.4f}, "
                f"Q1={row['low_mean_forward']:.4f}; "
                f"n={int(row['high_observations'])}/{int(row['low_observations'])})"
            )

    print("\nResearch-only: no production strategy/paper policy changed.")


if __name__ == "__main__":
    run_audit()
