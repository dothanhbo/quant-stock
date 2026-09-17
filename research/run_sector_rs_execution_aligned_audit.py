from __future__ import annotations

import numpy as np
import pandas as pd

from core.database import load_price_data
from core.sector import fetch_sector_mapping
from core.universe import get_vn100_symbols
from research.run_sector_rs_predictive_audit import build_observations
from research.run_sector_rs_robustness_audit import add_vnindex_rs
from research.run_sector_rs_market_interaction_audit import assign_interaction_groups
from research.run_sector_rs_regime_interaction_audit import (
    build_regime_series,
    attach_regime,
)

FORWARD_HORIZONS = (5, 10, 20)


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or not {"time", "close"}.issubset(df.columns):
        return pd.DataFrame(columns=["time", "close"])
    out = df[["time", "close"]].copy()
    out["time"] = pd.to_datetime(out["time"], errors="coerce")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.dropna(subset=["time", "close"])
    out = out.drop_duplicates("time", keep="last")
    return out.sort_values("time").reset_index(drop=True)


def add_execution_aligned_forward_returns(
    observations: pd.DataFrame,
    price_data: dict[str, pd.DataFrame],
    *,
    horizons: tuple[int, ...] = FORWARD_HORIZONS,
) -> pd.DataFrame:
    """Replace Close(T)->Close(T+N) returns with Close(T+1)->Close(T+N+1).

    This is a research approximation of entering on the next trading close.
    It deliberately uses the same signal date T while shifting the return
    window one trading observation forward.
    """
    out = observations.copy()
    prepared = {symbol: _prepare(df) for symbol, df in price_data.items()}

    values = {h: [] for h in horizons}

    for row in out.itertuples(index=False):
        df = prepared.get(row.symbol, pd.DataFrame())
        if df.empty:
            for h in horizons:
                values[h].append(np.nan)
            continue

        date = pd.Timestamp(row.date)
        eligible = df.index[df["time"] >= date]
        if len(eligible) == 0:
            for h in horizons:
                values[h].append(np.nan)
            continue

        start_pos = int(eligible[0]) + 1
        for h in horizons:
            end_pos = start_pos + h
            if start_pos >= len(df) or end_pos >= len(df):
                values[h].append(np.nan)
                continue

            start = float(df.iloc[start_pos]["close"])
            end = float(df.iloc[end_pos]["close"])
            values[h].append(
                end / start - 1.0
                if np.isfinite(start) and np.isfinite(end) and start > 0
                else np.nan
            )

    for h in horizons:
        out[f"forward_{h}d"] = values[h]

    return out


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
        rg = grouped[grouped["regime"] == regime]
        for group_name in ("LOW_LOW", "LOW_HIGH", "HIGH_LOW", "HIGH_HIGH"):
            group = rg[rg["interaction_group"] == group_name]
            if group.empty:
                continue

            row = {
                "regime": regime,
                "group": group_name,
                "observations": len(group),
            }
            for h in horizons:
                col = f"forward_{h}d"
                vals = pd.to_numeric(group[col], errors="coerce").dropna()
                row[f"mean_forward_{h}d"] = float(vals.mean()) if not vals.empty else np.nan
                row[f"median_forward_{h}d"] = float(vals.median()) if not vals.empty else np.nan
                row[f"hit_rate_{h}d"] = float((vals > 0).mean()) if not vals.empty else np.nan
            rows.append(row)

    return pd.DataFrame(rows)


def summarize_high_high_spread(
    summary: pd.DataFrame,
    *,
    horizons: tuple[int, ...] = FORWARD_HORIZONS,
) -> pd.DataFrame:
    rows = []
    for regime in ("BULL", "SIDEWAY", "BEAR"):
        rg = summary[summary["regime"] == regime]
        hh = rg[rg["group"] == "HIGH_HIGH"]
        ll = rg[rg["group"] == "LOW_LOW"]

        row = {"regime": regime}
        for h in horizons:
            a = hh[f"mean_forward_{h}d"].dropna()
            b = ll[f"mean_forward_{h}d"].dropna()
            row[f"high_high_minus_low_low_{h}d"] = (
                float(a.iloc[0] - b.iloc[0])
                if not a.empty and not b.empty else np.nan
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

    print(f"Universe={len(universe)} | mapped={len(mapped)}")
    print("Execution alignment: signal at Close(T) -> entry at next trading observation -> forward return from T+1")
    print("Regime: research proxy only")

    price_data = {symbol: load_price_data(symbol) for symbol in mapped}
    observations = build_observations(price_data, mapping, mapped)
    if observations.empty:
        raise RuntimeError("No predictive observations produced.")

    benchmark = load_price_data("VNINDEX")
    observations = add_vnindex_rs(observations, price_data, benchmark, period=20)
    observations = assign_interaction_groups(observations)
    observations = add_execution_aligned_forward_returns(observations, price_data)

    regime = build_regime_series(benchmark)
    observations = attach_regime(observations, regime)

    summary = summarize_regime_interaction(observations)
    spread = summarize_high_high_spread(summary)

    print(f"Observations: {len(observations):,}")
    print(f"Regime-labeled observations: {observations['regime'].notna().sum():,}")
    print()
    print("EXECUTION-ALIGNED REGIME × 4-GROUP INTERACTION")
    print(summary.to_string(index=False))
    print()
    print("EXECUTION-ALIGNED HIGH_HIGH − LOW_LOW")
    print(spread.to_string(index=False))
    print()
    print("INTERPRETATION RULE")
    print("Research diagnostic only. Current VN100/current sector mapping and research regime proxy remain subject to survivorship/metadata bias.")
    print("Do not change production policy from this output alone.")


if __name__ == "__main__":
    main()
