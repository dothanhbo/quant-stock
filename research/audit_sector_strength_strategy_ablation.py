from __future__ import annotations

import numpy as np
import pandas as pd

from core.database import load_price_data
from core.sector import fetch_sector_mapping
from core.universe import get_vn100_symbols


FORWARD_HORIZONS = (5, 10, 20)
SECTOR_PERIOD = 20
Q5_CUTOFF = 0.80
Q1_CUTOFF = 0.20


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
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


def build_sector_strength(universe, mapping) -> pd.DataFrame:
    rows = []
    for symbol in universe:
        sector = mapping.get(symbol)
        if not sector:
            continue
        px = _prepare(load_price_data(symbol))
        if len(px) < SECTOR_PERIOD + 1:
            continue
        px["daily_return"] = px["close"].pct_change()
        px["sector"] = sector
        rows.append(px[["time", "sector", "daily_return"]])

    if not rows:
        return pd.DataFrame()

    daily = (
        pd.concat(rows, ignore_index=True)
        .dropna(subset=["daily_return"])
        .groupby(["time", "sector"], as_index=False)["daily_return"]
        .mean()
        .rename(columns={"daily_return": "sector_daily_return"})
        .sort_values(["sector", "time"])
    )

    daily["sector_return_20d"] = (
        daily.groupby("sector")["sector_daily_return"]
        .transform(
            lambda s: (1.0 + s).rolling(SECTOR_PERIOD).apply(np.prod, raw=True) - 1.0
        )
    )
    daily["sector_percentile_20d"] = daily.groupby("time")[
        "sector_return_20d"
    ].rank(method="average", pct=True)

    return daily.reset_index(drop=True)


def build_observations(universe, mapping, sector_strength) -> pd.DataFrame:
    rows = []
    for symbol in universe:
        if symbol not in mapping:
            continue
        px = _prepare(load_price_data(symbol))
        if len(px) < max(FORWARD_HORIZONS) + 1:
            continue

        obs = pd.DataFrame({"time": px["time"], "symbol": symbol})
        for h in FORWARD_HORIZONS:
            obs[f"forward_{h}d"] = px["close"].shift(-h) / px["close"] - 1.0
        obs["sector"] = symbol
        rows.append(obs)

    stocks = pd.concat(rows, ignore_index=True)
    stocks["sector"] = stocks["sector"].map(mapping)

    strength = sector_strength[
        ["time", "sector", "sector_percentile_20d"]
    ]
    return stocks.merge(strength, on=["time", "sector"], how="left")


def apply_soft_sector_adjustment(
    observations: pd.DataFrame,
    strength_weight: float,
) -> pd.DataFrame:
    """Research proxy for adding sector strength as a soft ranking adjustment.

    This does NOT change production scoring. It maps sector percentile to a
    bounded score adjustment centered at zero.
    """
    out = observations.copy()
    centered = out["sector_percentile_20d"] - 0.5
    out["sector_adjustment"] = centered * strength_weight
    out["research_score"] = out["sector_adjustment"]
    return out


def summarize_wfo_proxy(observations: pd.DataFrame) -> pd.DataFrame:
    """Simple date-fold research proxy, not the production trading engine."""
    valid = observations.dropna(
        subset=["sector_percentile_20d", "forward_5d", "forward_10d", "forward_20d"]
    ).copy()

    dates = sorted(valid["time"].unique())
    if len(dates) < 12:
        return pd.DataFrame()

    folds = np.array_split(dates, 12)
    rows = []

    for variant, weight in [
        ("baseline", 0.0),
        ("sector_soft", 20.0),
    ]:
        adj = apply_soft_sector_adjustment(valid, weight)
        adj = adj.sort_values(["time", "research_score"])

        for i, fold_dates in enumerate(folds, 1):
            fold = adj[adj["time"].isin(fold_dates)]
            if fold.empty:
                continue

            # Research-only top-quintile selection proxy.
            selected = fold[
                fold["sector_percentile_20d"] >= Q5_CUTOFF
            ] if variant == "sector_soft" else fold

            # Keep the comparison mechanically transparent: baseline uses all
            # eligible observations; sector_soft uses the top sector quintile.
            # This is deliberately a screening proxy, not a claim of production
            # behavior.
            if selected.empty:
                ret = np.nan
            else:
                ret = selected["forward_20d"].mean()

            rows.append(
                {
                    "variant": variant,
                    "fold": i,
                    "observations": len(selected),
                    "mean_forward_20d_pct": ret * 100 if pd.notna(ret) else np.nan,
                }
            )

    return pd.DataFrame(rows)


def run_audit() -> None:
    universe = tuple(str(s).strip().upper() for s in get_vn100_symbols())
    mapping_all = fetch_sector_mapping()
    mapping = {s: mapping_all[s] for s in universe if s in mapping_all}

    sector_strength = build_sector_strength(universe, mapping)
    observations = build_observations(universe, mapping, sector_strength)

    print("=== SECTOR STRENGTH STRATEGY-LEVEL RESEARCH PROXY ===")
    print(f"VN100 universe: {len(universe)}")
    print(f"Mapped VN100: {len(mapping)}/{len(universe)}")
    print(f"Sector-strength observations: {len(sector_strength):,}")
    print(f"Stock observations: {len(observations):,}")
    print()
    print("This is a research proxy, NOT the production WFO engine.")
    print("It tests whether sector-strength selection adds useful forward-return separation.")
    print()

    summary = summarize_wfo_proxy(observations)
    print(summary.to_string(index=False))

    print()
    print("=== TOP SECTOR QUINTILE VS ALL ===")
    for h in FORWARD_HORIZONS:
        valid = observations.dropna(subset=["sector_percentile_20d", f"forward_{h}d"])
        top = valid[valid["sector_percentile_20d"] >= Q5_CUTOFF][f"forward_{h}d"].mean()
        all_ret = valid[f"forward_{h}d"].mean()
        print(f"{h}D: top-Q5 {top:+.4%} | all {all_ret:+.4%} | spread {(top-all_ret)*100:+.4f}pp")

    print()
    print("Research-only: no production strategy/paper policy changed.")


if __name__ == "__main__":
    run_audit()
