from __future__ import annotations

import numpy as np
import pandas as pd

from core.database import load_price_data
from core.sector import fetch_sector_mapping
from core.universe import get_vn100_symbols
from research.run_sector_rs_predictive_audit import build_observations
from research.run_sector_rs_robustness_audit import add_vnindex_rs
from research.run_sector_rs_market_interaction_audit import (
    assign_interaction_groups,
    summarize_interaction,
)

REGIME_EMA_PERIOD = 50
REGIME_RETURN_PERIOD = 20
REGIME_SLOPE_LOOKBACK = 10
FORWARD_HORIZONS = (5, 10, 20)


def _prepare_index(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or not {"time", "close"}.issubset(df.columns):
        return pd.DataFrame(columns=["time", "close"])

    out = df[["time", "close"]].copy()
    out["time"] = pd.to_datetime(out["time"], errors="coerce")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.dropna(subset=["time", "close"])
    out = out.drop_duplicates("time", keep="last")
    return out.sort_values("time").reset_index(drop=True)


def build_regime_series(
    vnindex_data: pd.DataFrame,
    *,
    ema_period: int = REGIME_EMA_PERIOD,
    return_period: int = REGIME_RETURN_PERIOD,
    slope_lookback: int = REGIME_SLOPE_LOOKBACK,
) -> pd.DataFrame:
    """Build a research regime proxy from VNINDEX.

    BULL: close > EMA50, EMA50 slope > 0, and 20D return > 0.
    BEAR: close < EMA50, EMA50 slope < 0, and 20D return < 0.
    Otherwise SIDEWAY.

    This deliberately stays in the research layer. It should only be treated
    as equivalent to production Market State after comparing definitions.
    """
    if min(ema_period, return_period, slope_lookback) <= 0:
        raise ValueError("periods must be > 0")

    out = _prepare_index(vnindex_data)
    if out.empty:
        return pd.DataFrame(columns=["date", "regime"])

    out["ema50"] = out["close"].ewm(
        span=ema_period, adjust=False, min_periods=ema_period
    ).mean()
    out["ema50_slope"] = out["ema50"] - out["ema50"].shift(slope_lookback)
    out["return_20d"] = out["close"] / out["close"].shift(return_period) - 1.0

    out["regime"] = "SIDEWAY"
    bull = (
        (out["close"] > out["ema50"])
        & (out["ema50_slope"] > 0)
        & (out["return_20d"] > 0)
    )
    bear = (
        (out["close"] < out["ema50"])
        & (out["ema50_slope"] < 0)
        & (out["return_20d"] < 0)
    )
    out.loc[bull, "regime"] = "BULL"
    out.loc[bear, "regime"] = "BEAR"

    return out[["time", "regime"]].rename(columns={"time": "date"}).dropna()


def attach_regime(
    observations: pd.DataFrame,
    regime_series: pd.DataFrame,
) -> pd.DataFrame:
    if observations.empty:
        return observations.copy()

    left = observations.copy()
    left["date"] = pd.to_datetime(left["date"])
    right = regime_series.copy()
    right["date"] = pd.to_datetime(right["date"])

    return left.merge(right, on="date", how="left")


def summarize_regime_interaction(
    observations: pd.DataFrame,
    *,
    horizons: tuple[int, ...] = FORWARD_HORIZONS,
) -> pd.DataFrame:
    if observations.empty:
        return pd.DataFrame()

    grouped = observations.dropna(subset=["regime", "interaction_group"])
    rows = []

    for regime in ("BULL", "SIDEWAY", "BEAR"):
        regime_group = grouped[grouped["regime"] == regime]
        for interaction in ("LOW_LOW", "LOW_HIGH", "HIGH_LOW", "HIGH_HIGH"):
            group = regime_group[
                regime_group["interaction_group"] == interaction
            ]
            if group.empty:
                continue

            row = {
                "regime": regime,
                "group": interaction,
                "observations": len(group),
            }

            for horizon in horizons:
                col = f"forward_{horizon}d"
                values = pd.to_numeric(group[col], errors="coerce").dropna()
                row[f"mean_forward_{horizon}d"] = (
                    float(values.mean()) if not values.empty else np.nan
                )
                row[f"median_forward_{horizon}d"] = (
                    float(values.median()) if not values.empty else np.nan
                )
                row[f"hit_rate_{horizon}d"] = (
                    float((values > 0).mean()) if not values.empty else np.nan
                )

            rows.append(row)

    return pd.DataFrame(rows)


def summarize_high_high_spread(
    summary: pd.DataFrame,
    *,
    horizons: tuple[int, ...] = FORWARD_HORIZONS,
) -> pd.DataFrame:
    if summary.empty:
        return pd.DataFrame()

    rows = []
    for regime in ("BULL", "SIDEWAY", "BEAR"):
        group = summary[summary["regime"] == regime]
        hh = group[group["group"] == "HIGH_HIGH"]
        ll = group[group["group"] == "LOW_LOW"]

        row = {"regime": regime}
        for horizon in horizons:
            hh_values = hh[f"mean_forward_{horizon}d"].dropna()
            ll_values = ll[f"mean_forward_{horizon}d"].dropna()
            row[f"high_high_minus_low_low_{horizon}d"] = (
                float(hh_values.iloc[0] - ll_values.iloc[0])
                if not hh_values.empty and not ll_values.empty
                else np.nan
            )
        rows.append(row)

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
        f"Sector RS=60D | VNINDEX RS=20D"
    )
    print(
        "Regime proxy: close vs EMA50 + EMA50 slope(10D) + VNINDEX return(20D)"
    )
    print("NOTE: research proxy only; verify against production Market State before using it.")

    price_data = {symbol: load_price_data(symbol) for symbol in mapped}
    observations = build_observations(price_data, mapping, mapped)
    if observations.empty:
        raise RuntimeError("No predictive observations produced.")

    benchmark_data = load_price_data("VNINDEX")
    observations = add_vnindex_rs(
        observations, price_data, benchmark_data, period=20
    )
    observations = assign_interaction_groups(observations)

    regime_series = build_regime_series(benchmark_data)
    observations = attach_regime(observations, regime_series)

    summary = summarize_regime_interaction(observations)
    spread = summarize_high_high_spread(summary)

    clean = observations.dropna(subset=["regime"])
    print(f"Observations: {len(observations):,}")
    print(f"Regime-labeled observations: {len(clean):,}")
    print()
    print("REGIME COUNTS")
    print(clean["regime"].value_counts().sort_index().to_string())
    print()
    print("REGIME × 4-GROUP INTERACTION")
    print(summary.to_string(index=False))
    print()
    print("HIGH_HIGH − LOW_LOW")
    print(spread.to_string(index=False))
    print()
    print("INTERPRETATION RULE")
    print(
        "Research diagnostic only. Current VN100 membership/current sector mapping "
        "and the regime proxy can introduce bias. Do not change production policy "
        "from this output alone."
    )


if __name__ == "__main__":
    main()
