"""
Research-only ablation scaffold for V2 vs V2 + Sector RS 60D.

This module deliberately does NOT modify production strategy code.
It provides pure functions for applying a frozen, training-derived
Sector RS percentile threshold to OOS observations and comparing
baseline vs filtered trade candidates.

The actual repository-specific WFO adapter should call these functions
inside each fold, using parameters derived only from that fold's training
window.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


DEFAULT_SECTOR_RS_PERCENTILE = 0.80


@dataclass(frozen=True)
class SectorRSFoldConfig:
    """Frozen fold-specific Sector RS selection rule."""

    percentile: float = DEFAULT_SECTOR_RS_PERCENTILE
    require_available: bool = True

    def __post_init__(self) -> None:
        if not 0.0 < self.percentile < 1.0:
            raise ValueError("percentile must be strictly between 0 and 1")


def fit_sector_rs_threshold(
    train_sector_rs: Iterable[float],
    *,
    percentile: float = DEFAULT_SECTOR_RS_PERCENTILE,
) -> float:
    """
    Fit a Sector RS threshold on TRAIN data only.

    The returned threshold is frozen and must be applied unchanged to OOS.
    """
    values = pd.to_numeric(pd.Series(list(train_sector_rs)), errors="coerce")
    values = values.replace([np.inf, -np.inf], np.nan).dropna()
    if values.empty:
        raise ValueError("cannot fit Sector RS threshold from empty data")
    if not 0.0 < percentile < 1.0:
        raise ValueError("percentile must be strictly between 0 and 1")
    return float(values.quantile(percentile))


def apply_sector_rs_filter(
    observations: pd.DataFrame,
    *,
    threshold: float,
    sector_rs_column: str = "sector_rs_60d",
    require_available: bool = True,
) -> pd.DataFrame:
    """
    Apply a frozen Sector RS threshold to OOS observations.

    Baseline candidates are preserved; rows failing the Sector RS condition
    are marked `sector_rs_selected=False` rather than deleted. This makes the
    function suitable for paired baseline-vs-variant accounting.
    """
    if sector_rs_column not in observations.columns:
        raise KeyError(f"missing required column: {sector_rs_column}")

    out = observations.copy()
    rs = pd.to_numeric(out[sector_rs_column], errors="coerce")
    available = rs.notna() & np.isfinite(rs)

    if require_available:
        selected = available & (rs >= threshold)
    else:
        selected = (~available) | (rs >= threshold)

    out["sector_rs_available"] = available
    out["sector_rs_threshold"] = float(threshold)
    out["sector_rs_selected"] = selected.astype(bool)
    return out


def summarize_selection(
    observations: pd.DataFrame,
    *,
    selected_column: str = "sector_rs_selected",
) -> dict[str, float]:
    """Return descriptive selection statistics for a fold."""
    if selected_column not in observations.columns:
        raise KeyError(f"missing required column: {selected_column}")
    selected = observations[selected_column].astype(bool)
    return {
        "observations": float(len(observations)),
        "selected": float(selected.sum()),
        "selection_rate": float(selected.mean()) if len(observations) else np.nan,
    }


def compare_fold_returns(
    baseline_returns: Iterable[float],
    variant_returns: Iterable[float],
) -> dict[str, float]:
    """Paired descriptive comparison; no strategy verdict is made."""
    base = pd.to_numeric(pd.Series(list(baseline_returns)), errors="coerce").dropna()
    variant = pd.to_numeric(pd.Series(list(variant_returns)), errors="coerce").dropna()
    return {
        "baseline_mean_return": float(base.mean()) if not base.empty else np.nan,
        "variant_mean_return": float(variant.mean()) if not variant.empty else np.nan,
        "mean_return_delta": (
            float(variant.mean() - base.mean())
            if not base.empty and not variant.empty
            else np.nan
        ),
        "baseline_count": float(len(base)),
        "variant_count": float(len(variant)),
    }


def main() -> None:
    print("Research-only Sector RS strategy ablation scaffold")
    print(f"Default frozen percentile: {DEFAULT_SECTOR_RS_PERCENTILE:.0%}")
    print("No production strategy/policy changes were made.")
    print("Next step: wire these pure functions into the existing chained-OOS WFO adapter.")
    print("Baseline and Sector-RS variant must use identical execution/cost assumptions.")


if __name__ == "__main__":
    main()
