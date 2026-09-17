from __future__ import annotations
import math
import argparse
import copy
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtesting.engine import BacktestConfig, build_exit_model, generate_candidate_trades
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.portfolio_metrics import calculate_portfolio_metrics
import backtesting.portfolio_simulator as portfolio_simulator_module
from backtesting.portfolio_simulator import PortfolioSimulator
from backtesting.ranking import RankingMethod
from backtesting.transaction_cost import TransactionCostConfig
from backtesting.walk_forward import WalkForwardConfig, build_walk_forward_folds
from config.strategy_config import V2_Q70_ATR45_9
from config.trading_policy import TradingPolicy, apply_strategy_config
from core.database import load_price_data
from core.sector import fetch_sector_mapping
from core.universe import get_vn100_symbols
from execution.signal_executor import PaperExecutionConfig
from research.universes import HOLDOUT20_SYMBOLS

QUALITY_FEATURES = ("signal_score", "relative_strength", "adx", "volume_ratio")
SECTOR_MOMENTUM_PERIOD = 20
DEFAULT_SECTOR_SCORE_ADJUSTMENT = 10.0


def _num(value: Any, default: float = np.nan) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if np.isfinite(x) else default


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


def build_sector_strength_daily(
    universe: list[str],
    mapping: dict[str, str],
    db_path: str,
) -> pd.DataFrame:
    """Build point-in-time equal-weight 20D sector momentum and daily percentile."""
    # db_path is retained in the function signature for CLI/API compatibility.
    del db_path

    rows = []

    for symbol in universe:
        sector = mapping.get(symbol)
        if not sector:
            continue

        prices = _prepare(load_price_data(symbol))
        if len(prices) < SECTOR_MOMENTUM_PERIOD + 1:
            continue

        prices["daily_return"] = prices["close"].pct_change()
        prices["sector"] = sector
        rows.append(prices[["time", "sector", "daily_return"]])

    if not rows:
        return pd.DataFrame(
            columns=[
                "time",
                "sector",
                "sector_return_20d",
                "sector_percentile_20d",
            ]
        )

    daily = (
        pd.concat(rows, ignore_index=True)
        .dropna(subset=["daily_return"])
        .groupby(["time", "sector"], as_index=False)["daily_return"]
        .mean()
        .rename(columns={"daily_return": "sector_daily_return"})
        .sort_values(["sector", "time"])
        .reset_index(drop=True)
    )

    daily["sector_return_20d"] = (
        daily.groupby("sector")["sector_daily_return"]
        .transform(
            lambda s: (1.0 + s)
            .rolling(SECTOR_MOMENTUM_PERIOD)
            .apply(np.prod, raw=True)
            - 1.0
        )
    )

    daily["sector_percentile_20d"] = daily.groupby("time")[
        "sector_return_20d"
    ].rank(method="average", pct=True)

    return daily


def frozen_percentile(value: Any, ref: np.ndarray) -> float:
    x = _num(value)
    if not np.isfinite(x) or ref.size == 0:
        return 0.5
    return float(np.searchsorted(ref, x, side="right") / ref.size)


def fit_quality(candidates: list[Any]) -> dict[str, np.ndarray]:
    refs: dict[str, np.ndarray] = {}

    for feature in QUALITY_FEATURES:
        vals = np.asarray(
            [_num(getattr(c, feature, np.nan)) for c in candidates],
            dtype=float,
        )
        refs[feature] = np.sort(vals[np.isfinite(vals)])

    return refs


def quality_score(candidate: Any, refs: dict[str, np.ndarray]) -> float:
    return float(
        np.mean(
            [
                frozen_percentile(
                    getattr(candidate, feature, np.nan),
                    refs[feature],
                )
                for feature in QUALITY_FEATURES
            ]
        )
    )


def candidate_sector_percentile(
    candidate: Any,
    sector_strength: pd.DataFrame,
    mapping: dict[str, str],
) -> float:
    symbol = str(getattr(candidate, "symbol", "")).strip().upper()
    sector = mapping.get(symbol)

    if not sector:
        return np.nan

    date = getattr(candidate, "signal_date", None)
    if date is None:
        date = getattr(candidate, "entry_date", None)
    if date is None:
        return np.nan

    date = pd.Timestamp(date).normalize()

    rows = sector_strength[
        (sector_strength["time"] == date)
        & (sector_strength["sector"] == sector)
    ]

    if rows.empty:
        return np.nan

    return _num(rows.iloc[0]["sector_percentile_20d"])


def sector_adjusted_score(
    candidate: Any,
    sector_strength: pd.DataFrame,
    mapping: dict[str, str],
    adjustment: float,
) -> float:
    """Return the research-only ranking score for one candidate."""
    pct = candidate_sector_percentile(candidate, sector_strength, mapping)
    base_score = _num(getattr(candidate, "signal_score", np.nan))

    if np.isfinite(pct) and np.isfinite(base_score):
        return float(base_score + adjustment * (pct - 0.5))

    return base_score


def research_rank_candidates(
    candidates: list[Any],
    method: RankingMethod | str,
    sector_strength: pd.DataFrame,
    mapping: dict[str, str],
    adjustment: float,
) -> list[Any]:
    """Rank candidates for the research-only sector-strength variant.

    Only SIGNAL_SCORE ranking is modified. Any other ranking method is delegated
    to the production ranking implementation.
    """
    if method != RankingMethod.SIGNAL_SCORE and str(method) != str(
        RankingMethod.SIGNAL_SCORE
    ):
        return portfolio_simulator_module.rank_candidates(
            candidates,
            method=method,
        )

    scored = [
        (
            candidate,
            sector_adjusted_score(
                candidate,
                sector_strength,
                mapping,
                adjustment,
            ),
        )
        for candidate in candidates
    ]

    scored.sort(
        key=lambda item: (
            _num(item[1], default=-np.inf),
            _num(getattr(item[0], "signal_score", np.nan), default=-np.inf),
        ),
        reverse=True,
    )

    return [candidate for candidate, _ in scored]


def ranking_diagnostics(
    candidates: list[Any],
    sector_strength: pd.DataFrame,
    mapping: dict[str, str],
    adjustment: float,
    top_n: int = 5,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Measure whether sector strength actually changes daily execution ranking.

    PortfolioSimulator ranks entry candidates date-by-date. This diagnostic
    mirrors that granularity instead of comparing one global candidate list.
    It is research-only and does not mutate production ranking.
    """
    rows: list[dict[str, Any]] = []
    changed_days = 0
    changed_positions = 0
    top_n_changed_days = 0
    total_candidates = 0
    available_candidates = 0
    score_adjustments: list[float] = []

    grouped: dict[str, list[Any]] = {}
    for candidate in candidates:
        raw_date = getattr(candidate, "entry_date", None)
        if raw_date is None:
            raw_date = getattr(candidate, "signal_date", None)
        if raw_date is None:
            key = ""
        else:
            try:
                key = pd.Timestamp(raw_date).date().isoformat()
            except Exception:
                key = str(raw_date)
        grouped.setdefault(key, []).append(candidate)

    for date_key in sorted(grouped):
        day_candidates = grouped[date_key]
        baseline_ranked = portfolio_simulator_module.rank_candidates(
            day_candidates,
            method=RankingMethod.SIGNAL_SCORE,
        )
        sector_ranked = research_rank_candidates(
            day_candidates,
            RankingMethod.SIGNAL_SCORE,
            sector_strength,
            mapping,
            adjustment,
        )

        baseline_symbols = [str(getattr(c, "symbol", "")) for c in baseline_ranked]
        sector_symbols = [str(getattr(c, "symbol", "")) for c in sector_ranked]

        total_candidates += len(day_candidates)
        changed = baseline_symbols != sector_symbols
        if changed:
            changed_days += 1

        for i, (before, after) in enumerate(zip(baseline_symbols, sector_symbols)):
            if before != after:
                changed_positions += 1

        if baseline_symbols[:top_n] != sector_symbols[:top_n]:
            top_n_changed_days += 1

        day_adjustments: list[float] = []
        for candidate in day_candidates:
            pct = candidate_sector_percentile(candidate, sector_strength, mapping)
            base_score = _num(getattr(candidate, "signal_score", np.nan))
            if np.isfinite(pct):
                available_candidates += 1
            if np.isfinite(pct) and np.isfinite(base_score):
                delta = float(adjustment * (pct - 0.5))
                day_adjustments.append(delta)
                score_adjustments.append(delta)

        rows.append(
            {
                "date": date_key,
                "candidate_count": len(day_candidates),
                "ranking_changed": int(changed),
                "top_n_changed": int(baseline_symbols[:top_n] != sector_symbols[:top_n]),
                "baseline_top_symbols": "|".join(baseline_symbols[:top_n]),
                "sector_top_symbols": "|".join(sector_symbols[:top_n]),
                "mean_score_adjustment": float(np.mean(day_adjustments)) if day_adjustments else np.nan,
                "min_score_adjustment": float(np.min(day_adjustments)) if day_adjustments else np.nan,
                "max_score_adjustment": float(np.max(day_adjustments)) if day_adjustments else np.nan,
            }
        )

    summary = {
        "diagnostic_days": len(grouped),
        "diagnostic_candidates": total_candidates,
        "sector_data_available_candidates": available_candidates,
        "ranking_changed_days": changed_days,
        "ranking_changed_day_pct": (changed_days / len(grouped) * 100.0) if grouped else 0.0,
        "changed_rank_positions": changed_positions,
        "top_n": top_n,
        "top_n_changed_days": top_n_changed_days,
        "top_n_changed_day_pct": (top_n_changed_days / len(grouped) * 100.0) if grouped else 0.0,
        "mean_abs_score_adjustment": float(np.mean(np.abs(score_adjustments))) if score_adjustments else 0.0,
        "max_abs_score_adjustment": float(np.max(np.abs(score_adjustments))) if score_adjustments else 0.0,
    }
    return summary, rows


def build_candidates(
    symbols: list[str],
    db_path: str,
    policy: TradingPolicy,
    paper: PaperExecutionConfig,
    start_date: str,
    end_date: str,
) -> list[Any]:
    config = BacktestConfig(
        max_holding_days=policy.maximum_holding_days,
        initial_capital=paper.initial_cash,
        buy_commission_pct=paper.commission_rate * 100,
        sell_commission_pct=paper.commission_rate * 100,
        sell_tax_pct=paper.sell_tax_rate * 100,
        buy_slippage_pct=paper.slippage_bps / 100,
        sell_slippage_pct=paper.slippage_bps / 100,
        ranking_method="signal_score",
    )

    entry_model = policy.build_entry_model()

    exit_model = build_exit_model(
        name="atr",
        stop_atr_multiplier=policy.stop_atr_multiplier,
        target_atr_multiplier=policy.target_atr_multiplier,
        break_even_trigger=policy.target_atr_multiplier,
        trailing_atr_multiplier=policy.trailing_atr_multiplier,
    )

    out: list[Any] = []

    for symbol in symbols:
        out.extend(
            generate_candidate_trades(
                symbol=symbol,
                config=config,
                db_path=db_path,
                warmup_bars=60,
                verbose=False,
                start_date=start_date,
                end_date=end_date,
                entry_model=entry_model,
                exit_model=exit_model,
            )
        )

    return out


def simulate(
    candidates: list[Any],
    paper: PaperExecutionConfig,
    parity: BacktestPaperParityConfig,
    initial_capital: float,
    sector_strength: pd.DataFrame | None = None,
    mapping: dict[str, str] | None = None,
    sector_adjustment: float = 0.0,
):
    costs = TransactionCostConfig(
        buy_commission_pct=parity.commission_pct,
        sell_commission_pct=parity.commission_pct,
        sell_tax_pct=parity.sell_tax_pct,
        buy_slippage_pct=parity.slippage_pct,
        sell_slippage_pct=parity.slippage_pct,
    )

    sim = PortfolioSimulator(
        initial_cash=initial_capital,
        position_size_pct=paper.fixed_fraction_pct,
        position_sizer=parity.build_position_sizer(),
        ranking_method="signal_score",
        transaction_cost_config=costs,
        max_positions=paper.maximum_open_positions,
        lot_size=paper.lot_size,
        max_new_positions_per_day=paper.maximum_orders_per_scan,
        maximum_gross_exposure_pct=paper.maximum_gross_exposure_pct,
        minimum_cash_buffer_pct=paper.minimum_cash_buffer_pct,
    )

    original_rank_candidates = portfolio_simulator_module.rank_candidates

    try:
        if sector_strength is not None and mapping is not None:
            def _research_rank(candidates_to_rank, method):
                return research_rank_candidates(
                    candidates_to_rank,
                    method,
                    sector_strength,
                    mapping,
                    sector_adjustment,
                )

            portfolio_simulator_module.rank_candidates = _research_rank

        result = sim.simulate(copy.deepcopy(candidates))
    finally:
        portfolio_simulator_module.rank_candidates = original_rank_candidates

    metrics = calculate_portfolio_metrics(
        result.equity_curve,
        final_equity=float(result.final_equity),
    )

    return result.executed_trades, metrics



# ---------------------------------------------------------------------------
# Q70 ALPHA AUDIT
# ---------------------------------------------------------------------------

FORWARD_HORIZONS = (5, 10, 20)
Q70_BINS = (
    ("Q00_50", 0.00, 0.50),
    ("Q50_60", 0.50, 0.60),
    ("Q60_70", 0.60, 0.70),
    ("Q70_80", 0.70, 0.80),
    ("Q80_90", 0.80, 0.90),
    ("Q90_100", 0.90, 1.0000001),
)


def _audit_num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _audit_symbol(candidate):
    return str(getattr(candidate, "symbol", "") or "").upper().strip()


def _audit_date(candidate):
    for attr in ("entry_date", "signal_date", "date"):
        value = getattr(candidate, attr, None)
        if value is not None:
            try:
                return pd.Timestamp(value).normalize()
            except Exception:
                return None
    return None


def _q70_bucket(score):
    x = _audit_num(score)
    if x is None:
        return "Q_INVALID"
    for name, lo, hi in Q70_BINS:
        if lo <= x < hi:
            return name
    return "Q_INVALID"


def _attach_q70_labels(candidates, refs, quality_threshold):
    """Attach audit metadata without mutating Trade objects."""
    labelled = []
    for candidate in candidates:
        score = quality_score(candidate, refs)
        score_num = _audit_num(score)
        labelled.append(
            {
                "candidate": candidate,
                "q70_score": float(score_num) if score_num is not None else math.nan,
                "q70_pass": bool(
                    score_num is not None and score_num >= quality_threshold
                ),
                "q70_bucket": _q70_bucket(score_num),
            }
        )
    return labelled


def _load_close_series(symbol):
    """Load one symbol's daily close series through the project's data layer."""
    try:
        frame = load_price_data(symbol)
    except Exception:
        return None

    if frame is None:
        return None

    df = pd.DataFrame(frame).copy()
    if df.empty:
        return None

    date_col = (
        "time"
        if "time" in df.columns
        else "date"
        if "date" in df.columns
        else "Date"
        if "Date" in df.columns
        else None
    )
    close_col = (
        "close"
        if "close" in df.columns
        else "Close"
        if "Close" in df.columns
        else None
    )
    if date_col is None or close_col is None:
        return None

    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df[close_col] = pd.to_numeric(df[close_col], errors="coerce")
    df = df.dropna(subset=[date_col, close_col]).sort_values(date_col)
    if df.empty:
        return None

    return (
        df.assign(_audit_date=df[date_col].dt.normalize())
        .drop_duplicates("_audit_date", keep="last")
        .set_index("_audit_date")[close_col]
        .astype(float)
    )


def _forward_returns_from_db(labelled_candidates, db_path, forward_end):
    """
    Measure raw close-to-close forward returns.

    Q70 is fitted on TRAIN only. Prices after each candidate date are used only
    as realized outcomes. This is an alpha-classification audit, not actual
    portfolio/trade PnL.
    """
    del db_path, forward_end

    symbols = {
        _audit_symbol(item["candidate"])
        for item in labelled_candidates
        if _audit_symbol(item["candidate"])
    }

    by_symbol = {
        symbol: _load_close_series(symbol)
        for symbol in sorted(symbols)
    }
    by_symbol = {
        symbol: closes
        for symbol, closes in by_symbol.items()
        if closes is not None and not closes.empty
    }

    rows = []
    for item in labelled_candidates:
        candidate = item["candidate"]
        symbol = _audit_symbol(candidate)
        entry_date = _audit_date(candidate)
        closes = by_symbol.get(symbol)

        if not symbol or entry_date is None or closes is None:
            continue

        eligible = closes.loc[closes.index >= entry_date]
        if eligible.empty:
            continue

        entry_px = _audit_num(eligible.iloc[0])
        if entry_px is None or entry_px <= 0:
            continue

        for horizon in FORWARD_HORIZONS:
            if len(eligible) <= horizon:
                continue

            exit_px = _audit_num(eligible.iloc[horizon])
            if exit_px is None or exit_px <= 0:
                continue

            rows.append(
                {
                    "symbol": symbol,
                    "entry_date": entry_date,
                    "q70_score": item["q70_score"],
                    "q70_group": "Q70_PASS" if item["q70_pass"] else "Q70_FAIL",
                    "q70_bucket": item["q70_bucket"],
                    "horizon_sessions": horizon,
                    "forward_return_pct": (exit_px / entry_px - 1.0) * 100.0,
                }
            )

    return pd.DataFrame(rows)


def summarize_q70_alpha(rows):
    if rows is None or rows.empty:
        return pd.DataFrame()

    result = []
    for (group, horizon), g in rows.groupby(["q70_group", "horizon_sessions"]):
        r = pd.to_numeric(g["forward_return_pct"], errors="coerce").dropna()
        if r.empty:
            continue
        result.append(
            {
                "q70_group": group,
                "horizon_sessions": int(horizon),
                "n": int(len(r)),
                "mean_return_pct": float(r.mean()),
                "median_return_pct": float(r.median()),
                "win_rate_pct": float((r > 0).mean() * 100.0),
                "std_return_pct": float(r.std(ddof=1)) if len(r) > 1 else math.nan,
            }
        )

    return pd.DataFrame(result).sort_values(["horizon_sessions", "q70_group"])


def q70_alpha_spread(summary):
    if summary is None or summary.empty:
        return pd.DataFrame()

    result = []
    for horizon in FORWARD_HORIZONS:
        part = summary[summary["horizon_sessions"] == horizon].set_index("q70_group")
        if "Q70_PASS" not in part.index or "Q70_FAIL" not in part.index:
            continue

        p = part.loc["Q70_PASS"]
        f = part.loc["Q70_FAIL"]
        result.append(
            {
                "horizon_sessions": horizon,
                "pass_n": int(p["n"]),
                "fail_n": int(f["n"]),
                "mean_pass_pct": float(p["mean_return_pct"]),
                "mean_fail_pct": float(f["mean_return_pct"]),
                "mean_spread_pass_minus_fail_pct": float(
                    p["mean_return_pct"] - f["mean_return_pct"]
                ),
                "median_spread_pass_minus_fail_pct": float(
                    p["median_return_pct"] - f["median_return_pct"]
                ),
                "win_rate_spread_pp": float(
                    p["win_rate_pct"] - f["win_rate_pct"]
                ),
            }
        )

    return pd.DataFrame(result)


def summarize_q70_buckets(rows):
    """Check whether forward returns are monotonic across Q70 score buckets."""
    if rows is None or rows.empty:
        return pd.DataFrame()

    result = []
    for (bucket, horizon), g in rows.groupby(["q70_bucket", "horizon_sessions"]):
        r = pd.to_numeric(g["forward_return_pct"], errors="coerce").dropna()
        if r.empty:
            continue
        result.append(
            {
                "q70_bucket": bucket,
                "horizon_sessions": int(horizon),
                "n": int(len(r)),
                "mean_return_pct": float(r.mean()),
                "median_return_pct": float(r.median()),
                "win_rate_pct": float((r > 0).mean() * 100.0),
                "std_return_pct": float(r.std(ddof=1)) if len(r) > 1 else math.nan,
            }
        )

    bucket_order = {name: i for i, (name, _, _) in enumerate(Q70_BINS)}
    out = pd.DataFrame(result)
    if out.empty:
        return out

    out["_order"] = out["q70_bucket"].map(bucket_order).fillna(999)
    return out.sort_values(["horizon_sessions", "_order"]).drop(columns="_order")


def _fold_group_stats(rows, group):
    result = []
    for horizon in FORWARD_HORIZONS:
        part = rows[
            (rows["q70_group"] == group)
            & (rows["horizon_sessions"] == horizon)
        ]
        r = pd.to_numeric(part["forward_return_pct"], errors="coerce").dropna()
        result.append(
            {
                "n": int(len(r)),
                "mean": float(r.mean()) if not r.empty else math.nan,
                "median": float(r.median()) if not r.empty else math.nan,
                "win_rate": float((r > 0).mean() * 100.0) if not r.empty else math.nan,
            }
        )
    return result


def build_fold_spread(rows, fold_id):
    """One row per fold/horizon for robustness: PASS minus FAIL."""
    if rows is None or rows.empty:
        return pd.DataFrame()

    result = []
    for horizon in FORWARD_HORIZONS:
        part = rows[rows["horizon_sessions"] == horizon]
        p = part[part["q70_group"] == "Q70_PASS"]
        f = part[part["q70_group"] == "Q70_FAIL"]

        pr = pd.to_numeric(p["forward_return_pct"], errors="coerce").dropna()
        fr = pd.to_numeric(f["forward_return_pct"], errors="coerce").dropna()

        if pr.empty or fr.empty:
            continue

        result.append(
            {
                "fold": fold_id,
                "horizon_sessions": horizon,
                "pass_n": int(len(pr)),
                "fail_n": int(len(fr)),
                "pass_mean_pct": float(pr.mean()),
                "fail_mean_pct": float(fr.mean()),
                "mean_spread_pct": float(pr.mean() - fr.mean()),
                "pass_median_pct": float(pr.median()),
                "fail_median_pct": float(fr.median()),
                "median_spread_pct": float(pr.median() - fr.median()),
                "pass_win_rate_pct": float((pr > 0).mean() * 100.0),
                "fail_win_rate_pct": float((fr > 0).mean() * 100.0),
                "win_rate_spread_pp": float(
                    (pr > 0).mean() * 100.0 - (fr > 0).mean() * 100.0
                ),
            }
        )

    return pd.DataFrame(result)


def run_alpha_fold(
    train,
    test,
    quality_threshold,
    db_path,
    forward_end,
    fold_id,
    output_dir,
):
    refs = fit_quality(train)

    # Exact Q70 predicate from the existing run_fold(): fit on TRAIN,
    # classify TEST.
    labelled = _attach_q70_labels(test, refs, quality_threshold)

    rows = _forward_returns_from_db(labelled, db_path, forward_end)
    if rows.empty:
        return rows, pd.DataFrame(), pd.DataFrame()

    rows.insert(0, "fold", fold_id)
    fold_summary = summarize_q70_alpha(rows)
    fold_spread = build_fold_spread(rows, fold_id)
    return rows, fold_summary, fold_spread

def main() -> None:
    parser = argparse.ArgumentParser(description="Q70 forward-return alpha audit (research-only)")
    parser.add_argument("--db-path", default="data/market.db")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--start", default="2018-08-07")
    parser.add_argument("--end", default="2026-08-21")
    parser.add_argument("--train-months", type=int, default=24)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=6)
    parser.add_argument("--quality-threshold", type=float, default=0.70)
    parser.add_argument("--output", default="research_results/q70_alpha_wfo")
    args = parser.parse_args()

    apply_strategy_config(V2_Q70_ATR45_9)

    paper = PaperExecutionConfig.from_env()
    policy = TradingPolicy.from_env()

    symbols = (
        list(HOLDOUT20_SYMBOLS)
        if args.symbols is None
        else sorted({s.strip().upper() for s in args.symbols if s.strip()})
    )

    folds = build_walk_forward_folds(
        WalkForwardConfig(
            start_date=args.start,
            end_date=args.end,
            train_months=args.train_months,
            test_months=args.test_months,
            step_months=args.step_months,
        )
    )

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    print("=== Q70 ALPHA AUDIT WFO ===")
    print(
        f"V2={V2_Q70_ATR45_9.name} | symbols={len(symbols)} | "
        f"folds={len(folds)} | Q70={args.quality_threshold:.0%}"
    )
    print("Question: does Q70 PASS outperform Q70 FAIL on forward 5/10/20 sessions?")
    print("Q70 thresholds are fit on TRAIN only for each fold.")

    all_rows = []
    fold_summaries = []
    fold_spreads = []

    for fold_id, fold in enumerate(folds, start=1):
        train = build_candidates(
            symbols, args.db_path, policy, paper,
            str(fold.train_start.date()), str(fold.train_end.date()),
        )
        test = build_candidates(
            symbols, args.db_path, policy, paper,
            str(fold.test_start.date()), str(fold.test_end.date()),
        )

        rows, summary, fold_spread = run_alpha_fold(
            train=train,
            test=test,
            quality_threshold=args.quality_threshold,
            db_path=args.db_path,
            forward_end=fold.test_end,
            fold_id=fold_id,
            output_dir=output,
        )

        if not rows.empty:
            all_rows.append(rows)
        if not summary.empty:
            summary.insert(0, "fold", fold_id)
            fold_summaries.append(summary)
        if not fold_spread.empty:
            fold_spreads.append(fold_spread)

        print(
            f"fold={fold_id:02d} | test={len(test):4d} | "
            f"forward_rows={len(rows):4d}"
        )

    if not all_rows:
        raise RuntimeError(
            "No forward-return rows were produced. "
            "Check DatabaseManager OHLC method/schema before interpreting results."
        )

    raw = pd.concat(all_rows, ignore_index=True)
    summary = summarize_q70_alpha(raw)
    spread = q70_alpha_spread(summary)
    buckets = summarize_q70_buckets(raw)
    fold_spread = (
        pd.concat(fold_spreads, ignore_index=True)
        if fold_spreads
        else pd.DataFrame()
    )

    raw.to_csv(output / "q70_alpha_raw.csv", index=False)
    summary.to_csv(output / "q70_alpha_summary.csv", index=False)
    spread.to_csv(output / "q70_alpha_spread.csv", index=False)
    buckets.to_csv(output / "q70_alpha_buckets.csv", index=False)
    fold_spread.to_csv(output / "q70_alpha_fold_spread.csv", index=False)

    if fold_summaries:
        pd.concat(fold_summaries, ignore_index=True).to_csv(
            output / "q70_alpha_fold_summary.csv", index=False
        )

    print("\n=== Q70 ALPHA SUMMARY ===")
    print(summary.to_string(index=False))
    print("\n=== PASS - FAIL SPREAD ===")
    print(spread.to_string(index=False))
    print("\n=== Q70 SCORE BUCKETS ===")
    print(buckets.to_string(index=False))
    print("\n=== FOLD-LEVEL PASS - FAIL SPREAD ===")
    print(fold_spread.to_string(index=False))

    if not fold_spread.empty:
        for horizon in FORWARD_HORIZONS:
            h = fold_spread[fold_spread["horizon_sessions"] == horizon]
            if not h.empty:
                print(
                    f"\n{horizon}D robustness: "
                    f"{int((h['mean_spread_pct'] > 0).sum())}/{len(h)} folds "
                    f"have positive mean spread."
                )

    print(f"\nOutput: {output.resolve()}")


if __name__ == "__main__":
    main()
