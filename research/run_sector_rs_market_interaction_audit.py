from __future__ import annotations

import numpy as np
import pandas as pd

from core.database import load_price_data
from core.sector import fetch_sector_mapping
from core.universe import get_vn100_symbols
from research.run_sector_rs_predictive_audit import build_observations, FORWARD_HORIZONS
from research.run_sector_rs_robustness_audit import add_vnindex_rs

SECTOR_RS_PERIOD = 60
VNINDEX_RS_PERIOD = 20
LOW_QUANTILE = 0.20
HIGH_QUANTILE = 0.80


def assign_interaction_groups(
    observations: pd.DataFrame,
    *,
    sector_column: str = "rs_sector_60d",
    market_column: str = "rs_vnindex_20d",
    low_quantile: float = LOW_QUANTILE,
    high_quantile: float = HIGH_QUANTILE,
) -> pd.DataFrame:
    if observations.empty:
        return observations.copy()
    if not 0 < low_quantile < high_quantile < 1:
        raise ValueError("Require 0 < low_quantile < high_quantile < 1")
    required = {"date", sector_column, market_column}
    missing = required - set(observations.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")

    out = observations.copy()
    out["sector_tail"] = pd.NA
    out["market_tail"] = pd.NA
    out["interaction_group"] = pd.NA

    for date, idx in out.groupby("date", sort=False).groups.items():
        group = out.loc[idx]
        sector = pd.to_numeric(group[sector_column], errors="coerce")
        market = pd.to_numeric(group[market_column], errors="coerce")
        if sector.notna().sum() < 5 or market.notna().sum() < 5:
            continue

        sector_low = sector.quantile(low_quantile)
        sector_high = sector.quantile(high_quantile)
        market_low = market.quantile(low_quantile)
        market_high = market.quantile(high_quantile)

        sector_tail = pd.Series(pd.NA, index=idx, dtype="object")
        market_tail = pd.Series(pd.NA, index=idx, dtype="object")
        sector_tail.loc[sector <= sector_low] = "LOW"
        sector_tail.loc[sector >= sector_high] = "HIGH"
        market_tail.loc[market <= market_low] = "LOW"
        market_tail.loc[market >= market_high] = "HIGH"

        groups = pd.Series(pd.NA, index=idx, dtype="object")
        both = sector_tail.notna() & market_tail.notna()
        groups.loc[both] = (
            sector_tail.loc[both].astype(str)
            + "_"
            + market_tail.loc[both].astype(str)
        )

        out.loc[idx, "sector_tail"] = sector_tail
        out.loc[idx, "market_tail"] = market_tail
        out.loc[idx, "interaction_group"] = groups

    return out


def summarize_interaction(
    observations: pd.DataFrame,
    *,
    horizons: tuple[int, ...] = FORWARD_HORIZONS,
) -> pd.DataFrame:
    if observations.empty:
        return pd.DataFrame()

    grouped = observations.dropna(subset=["interaction_group"])
    rows = []
    for name in ("LOW_LOW", "LOW_HIGH", "HIGH_LOW", "HIGH_HIGH"):
        group = grouped[grouped["interaction_group"] == name]
        if group.empty:
            continue

        row = {
            "group": name,
            "observations": len(group),
            "mean_sector_rs": float(group["rs_sector_60d"].mean()),
            "mean_vnindex_rs": float(group["rs_vnindex_20d"].mean()),
        }
        for horizon in horizons:
            col = f"forward_{horizon}d"
            values = pd.to_numeric(group[col], errors="coerce").dropna()
            row[f"mean_forward_{horizon}d"] = float(values.mean()) if not values.empty else np.nan
            row[f"median_forward_{horizon}d"] = float(values.median()) if not values.empty else np.nan
            row[f"hit_rate_{horizon}d"] = float((values > 0).mean()) if not values.empty else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def summarize_conditional_sector_effect(
    observations: pd.DataFrame,
    *,
    horizons: tuple[int, ...] = FORWARD_HORIZONS,
) -> pd.DataFrame:
    if observations.empty:
        return pd.DataFrame()

    grouped = observations.dropna(subset=["interaction_group"])
    rows = []
    for market_tail in ("LOW", "HIGH"):
        low = grouped[grouped["interaction_group"] == f"LOW_{market_tail}"]
        high = grouped[grouped["interaction_group"] == f"HIGH_{market_tail}"]
        row = {"market_tail": market_tail}
        for horizon in horizons:
            col = f"forward_{horizon}d"
            low_values = pd.to_numeric(low[col], errors="coerce").dropna()
            high_values = pd.to_numeric(high[col], errors="coerce").dropna()
            row[f"sector_high_minus_low_{horizon}d"] = (
                float(high_values.mean() - low_values.mean())
                if not low_values.empty and not high_values.empty else np.nan
            )
            row[f"low_sector_mean_{horizon}d"] = float(low_values.mean()) if not low_values.empty else np.nan
            row[f"high_sector_mean_{horizon}d"] = float(high_values.mean()) if not high_values.empty else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    universe = tuple(dict.fromkeys(
        str(s).strip().upper() for s in get_vn100_symbols()
        if s is not None and str(s).strip()
    ))
    mapping = fetch_sector_mapping()
    mapped = tuple(symbol for symbol in universe if symbol in mapping)

    print(
        f"Universe={len(universe)} | mapped={len(mapped)} | "
        f"Sector RS={SECTOR_RS_PERIOD}D | VNINDEX RS={VNINDEX_RS_PERIOD}D"
    )
    print("Interaction tails: bottom/top 20% per feature; middle observations excluded")

    price_data = {symbol: load_price_data(symbol) for symbol in mapped}
    observations = build_observations(price_data, mapping, mapped)
    if observations.empty:
        raise RuntimeError("No predictive observations produced.")

    benchmark_data = load_price_data("VNINDEX")
    observations = add_vnindex_rs(
        observations, price_data, benchmark_data, period=VNINDEX_RS_PERIOD
    )
    observations = assign_interaction_groups(observations)

    summary = summarize_interaction(observations)
    conditional = summarize_conditional_sector_effect(observations)
    grouped_count = int(observations["interaction_group"].notna().sum())

    print(f"Observations: {len(observations):,}")
    print(f"Dates: {observations['date'].min().date()} -> {observations['date'].max().date()}")
    print(f"Symbols with observations: {observations['symbol'].nunique()}")
    print(f"Extreme-tail interaction observations: {grouped_count:,}")
    print()
    print("4-GROUP INTERACTION")
    print(summary.to_string(index=False))
    print()
    print("CONDITIONAL SECTOR EFFECT")
    print("Positive value means HIGH Sector RS outperformed LOW Sector RS while holding the Market-RS tail fixed.")
    print(conditional.to_string(index=False))
    print()
    print("INTERPRETATION RULE")
    print(
        "Research diagnostic only. Current VN100 membership and current sector mapping "
        "are used historically, so survivorship/point-in-time metadata bias remains. "
        "Do not change production policy from this output alone."
    )


if __name__ == "__main__":
    main()
