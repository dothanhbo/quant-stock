from __future__ import annotations

from pathlib import Path
import pandas as pd
import numpy as np

from core.database import load_price_data


FOLD_PATH = Path("research_results/q70_market_rs_ablation_wfo/fold_summary.csv")


def _find_column(df: pd.DataFrame, names: tuple[str, ...]) -> str:
    cols = {str(c).strip().lower(): c for c in df.columns}
    for name in names:
        if name.lower() in cols:
            return cols[name.lower()]
    raise KeyError(f"Missing one of {names}; available={list(df.columns)}")


def _load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def build_production_regime_series(vnindex_data: pd.DataFrame) -> pd.DataFrame:
    """Reproduce the repository's causal Market Regime definition.

    Production definition:
      BULL: close > EMA50 > EMA200, EMA50 slope(10D) > 0,
            and 20D return > -2%.
      BEAR: close < EMA200, EMA50 < EMA200, EMA50 slope(10D) < 0.
      Otherwise SIDEWAY.

    The calculation is performed using data available up to each date only.
    """
    if vnindex_data is None or vnindex_data.empty:
        raise ValueError("VNINDEX data is empty.")

    required = {"time", "close"}
    if not required.issubset(vnindex_data.columns):
        raise ValueError(f"VNINDEX data must contain {sorted(required)}")

    df = vnindex_data[["time", "close"]].copy()
    df["time"] = pd.to_datetime(df["time"], errors="coerce").dt.normalize()
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = (
        df.dropna()
        .drop_duplicates("time", keep="last")
        .sort_values("time")
        .reset_index(drop=True)
    )

    enough = np.arange(len(df)) >= 199

    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
    df["ema50_slope_10d_pct"] = (
        df["ema50"] / df["ema50"].shift(10) - 1.0
    ) * 100.0
    df["return_20d_pct"] = (
        df["close"] / df["close"].shift(20) - 1.0
    ) * 100.0

    bull = (
        enough
        & (df["close"] > df["ema50"])
        & (df["ema50"] > df["ema200"])
        & (df["ema50_slope_10d_pct"] > 0)
        & (df["return_20d_pct"] > -2)
    )

    bear = (
        enough
        & (df["close"] < df["ema200"])
        & (df["ema50"] < df["ema200"])
        & (df["ema50_slope_10d_pct"] < 0)
    )

    df["regime"] = np.select(
        [bull, bear],
        ["BULL", "BEAR"],
        default="SIDEWAY",
    )

    df.loc[~enough, "regime"] = "UNKNOWN"

    return df[
        [
            "time",
            "regime",
            "close",
            "ema50",
            "ema200",
            "ema50_slope_10d_pct",
            "return_20d_pct",
        ]
    ]


def _regime_at_or_before(regimes: pd.DataFrame, date) -> str:
    target = pd.Timestamp(date).normalize()
    eligible = regimes[regimes["time"] <= target]
    if eligible.empty:
        return "UNKNOWN"
    return str(eligible.iloc[-1]["regime"])


def build_regime_comparison(
    folds: pd.DataFrame,
    regimes: pd.DataFrame,
) -> pd.DataFrame:
    folds = folds.copy()
    folds.columns = [str(c).strip().lower() for c in folds.columns]
    regimes = regimes.copy()

    variant_col = _find_column(folds, ("variant",))
    fold_col = _find_column(folds, ("fold", "fold_id"))
    start_col = _find_column(folds, ("test_start",))
    return_col = _find_column(folds, ("test_return_pct", "fold_return_pct"))
    trades_col = _find_column(folds, ("test_trades", "total_trades", "trades"))

    selected = folds[
        folds[variant_col].isin(["A_current", "B_no_q70_rs"])
    ].copy()

    if selected.empty:
        raise ValueError("No A_current/B_no_q70_rs rows found.")

    fold_variant_counts = selected.groupby(fold_col)[variant_col].nunique()
    if (fold_variant_counts < 2).any():
        missing_folds = fold_variant_counts[fold_variant_counts < 2].index.tolist()
        raise ValueError(
            "Both A_current and B_no_q70_rs are required for every fold. "
            f"Missing variant in folds: {missing_folds}"
        )

    selected["regime"] = selected[start_col].apply(
        lambda x: _regime_at_or_before(regimes, x)
    )

    pivot = selected.pivot_table(
        index=[fold_col, "regime"],
        columns=variant_col,
        values=[return_col, trades_col],
        aggfunc="first",
    )

    rows = []
    for (fold, regime), values in pivot.iterrows():
        a_ret = float(values[(return_col, "A_current")])
        b_ret = float(values[(return_col, "B_no_q70_rs")])
        a_trades = float(values[(trades_col, "A_current")])
        b_trades = float(values[(trades_col, "B_no_q70_rs")])

        rows.append(
            {
                "fold": fold,
                "regime": regime,
                "A_return_pct": a_ret,
                "B_return_pct": b_ret,
                "A_minus_B_pp": a_ret - b_ret,
                "A_trades": a_trades,
                "B_trades": b_trades,
            }
        )

    return pd.DataFrame(rows).sort_values("fold").reset_index(drop=True)


def main() -> None:
    if not FOLD_PATH.exists():
        raise FileNotFoundError(FOLD_PATH)

    folds = _load(FOLD_PATH)
    vnindex = load_price_data("VNINDEX")
    regimes = build_production_regime_series(vnindex)
    result = build_regime_comparison(folds, regimes)

    print("=== Q70 MARKET RS × PRODUCTION REGIME ANALYSIS ===")
    print(f"Folds: {FOLD_PATH}")
    print("Regime source: VNINDEX price history")
    print(
        "Regime definition: repository Market Regime "
        "(EMA50/EMA200 + EMA50 slope 10D + 20D return)"
    )
    print()
    print(result.to_string(index=False))

    print()
    print("=== REGIME SUMMARY ===")
    summary = (
        result.groupby("regime")
        .agg(
            folds=("fold", "count"),
            A_mean_return_pct=("A_return_pct", "mean"),
            B_mean_return_pct=("B_return_pct", "mean"),
            mean_A_minus_B_pp=("A_minus_B_pp", "mean"),
            A_wins=("A_minus_B_pp", lambda s: int((s > 0).sum())),
            B_wins=("A_minus_B_pp", lambda s: int((s < 0).sum())),
            A_trades=("A_trades", "sum"),
            B_trades=("B_trades", "sum"),
        )
        .reset_index()
    )
    print(summary.to_string(index=False))

    print()
    print("=== FOLD 6 DETAIL ===")
    print(result[result["fold"] == 6].to_string(index=False))


if __name__ == "__main__":
    main()
