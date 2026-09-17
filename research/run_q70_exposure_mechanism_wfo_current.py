
from __future__ import annotations

import argparse
import copy
import sqlite3
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtesting.engine import (
    BacktestConfig,
    build_exit_model,
    generate_candidate_trades,
)
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.portfolio_simulator import PortfolioSimulator
from backtesting.transaction_cost import TransactionCostConfig
from backtesting.walk_forward import (
    WalkForwardConfig,
    build_walk_forward_folds,
)
from config.trading_policy import TradingPolicy
from execution.signal_executor import PaperExecutionConfig


FEATURES = (
    "signal_score",
    "relative_strength",
    "adx",
    "volume_ratio",
)


def safe_float(value: Any) -> float:
    try:
        x = float(value)
        return x if np.isfinite(x) else np.nan
    except (TypeError, ValueError):
        return np.nan


class FrozenQualityModel:
    """Train-only empirical percentile composite."""

    def __init__(self) -> None:
        self.arrays: dict[str, np.ndarray] = {}

    def fit(self, trades: list) -> None:
        for feature in FEATURES:
            values = [
                safe_float(getattr(t, feature, np.nan))
                for t in trades
            ]
            values = [v for v in values if np.isfinite(v)]
            self.arrays[feature] = np.sort(
                np.asarray(values, dtype=float)
            )

    def score(self, trade) -> float:
        scores: list[float] = []
        for feature in FEATURES:
            value = safe_float(getattr(trade, feature, np.nan))
            arr = self.arrays.get(feature)

            if not np.isfinite(value) or arr is None or len(arr) == 0:
                scores.append(0.5)
                continue

            scores.append(
                float(
                    np.searchsorted(
                        arr,
                        value,
                        side="right",
                    )
                    / len(arr)
                )
            )

        return float(np.mean(scores))


class Q70ExposureSizer:
    """
    Q70 eligibility is fixed.

    For every candidate:
      1. Q < 0.70 -> quantity 0
      2. Q >= 0.70 -> calculate the normal production quantity
      3. FRAGILE_BULL -> multiply only the quantity by fragile_scale
      4. all other states -> unchanged

    Thus the candidate pool is identical across all scales.
    """

    def __init__(
        self,
        base_sizer,
        quality_model: FrozenQualityModel,
        market_health: pd.DataFrame,
        fragile_scale: float,
    ) -> None:
        self.base_sizer = base_sizer
        self.quality_model = quality_model
        self.market_health = market_health
        self.fragile_scale = fragile_scale

    @property
    def name(self) -> str:
        return f"q70_fragile_{self.fragile_scale:g}"

    def _state(self, candidate) -> str:
        # Market-state diagnostic is explicitly keyed to entry_date.
        d = pd.Timestamp(candidate.entry_date).normalize()
        if d not in self.market_health.index:
            return "UNKNOWN"

        row = self.market_health.loc[d]

        regime = str(
            getattr(candidate, "market_regime", "") or ""
        ).upper()

        breadth = safe_float(row["breadth_ema50_pct"])
        change = safe_float(
            row["breadth_ema50_change_10d"]
        )

        if regime == "BEAR":
            return "BEAR"

        if regime == "BULL":
            if (
                np.isfinite(breadth)
                and np.isfinite(change)
                and breadth < 50
                and change < 0
            ):
                return "DIVERGENT_BULL"

            if (
                np.isfinite(breadth)
                and np.isfinite(change)
                and breadth >= 70
                and change >= 0
            ):
                return "HEALTHY_BULL"

            return "FRAGILE_BULL"

        if (
            regime == "SIDEWAY"
            and np.isfinite(breadth)
            and np.isfinite(change)
            and breadth >= 60
            and change > 0
        ):
            return "RECOVERY"

        return "NEUTRAL"

    def calculate_quantity(self, context) -> int:
        candidate = context.candidate

        # Fixed Q70 gate.
        if self.quality_model.score(candidate) < 0.70:
            return 0

        quantity = int(
            self.base_sizer.calculate_quantity(context)
        )

        if quantity <= 0:
            return 0

        if self._state(candidate) == "FRAGILE_BULL":
            quantity = int(quantity * self.fragile_scale)

        return (
            quantity // context.lot_size
        ) * context.lot_size


def load_market_health(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)

    required = {
        "time",
        "breadth_ema50_pct",
        "breadth_ema50_change_10d",
    }
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(
            f"Market-health missing columns: {sorted(missing)}"
        )

    df["time"] = pd.to_datetime(
        df["time"],
        errors="coerce",
    ).dt.normalize()

    df = (
        df.dropna(subset=["time"])
        .drop_duplicates("time")
        .set_index("time")
        .sort_index()
    )

    return df


def get_all_symbols(db_path: str) -> list[str]:
    con = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT DISTINCT symbol
            FROM prices
            WHERE UPPER(symbol) <> 'VNINDEX'
            ORDER BY symbol
            """,
            con,
        )
    finally:
        con.close()

    return [
        str(x).strip().upper()
        for x in df["symbol"].tolist()
        if str(x).strip()
    ]


def build_backtest_config(
    paper: PaperExecutionConfig,
    parity: BacktestPaperParityConfig,
    policy: TradingPolicy,
) -> BacktestConfig:
    return BacktestConfig(
        max_holding_days=policy.maximum_holding_days,
        initial_capital=parity.initial_cash,
        position_size_pct=parity.maximum_position_pct,
        buy_commission_pct=parity.commission_pct,
        sell_commission_pct=parity.commission_pct,
        sell_tax_pct=parity.sell_tax_pct,
        buy_slippage_pct=parity.slippage_pct,
        sell_slippage_pct=parity.slippage_pct,
        ranking_method="signal_score",
    )


def make_simulator(
    *,
    parity: BacktestPaperParityConfig,
    position_sizer,
    paper: PaperExecutionConfig,
) -> PortfolioSimulator:
    return PortfolioSimulator(
        initial_cash=parity.initial_cash,
        position_size_pct=parity.maximum_position_pct,
        position_sizer=position_sizer,
        ranking_method="signal_score",
        transaction_cost_config=parity.transaction_cost_config(),
        max_positions=parity.maximum_open_positions,
        lot_size=parity.lot_size,
        max_new_positions_per_day=paper.maximum_orders_per_scan,
        maximum_gross_exposure_pct=(
            parity.maximum_gross_exposure_pct
        ),
        minimum_cash_buffer_pct=(
            parity.minimum_cash_buffer_pct
        ),
    )


def summarize_returns(returns: list[float]) -> dict[str, float]:
    if not returns:
        return {
            "trades": 0,
            "win_rate_pct": np.nan,
            "mean_trade_return_pct": np.nan,
            "profit_factor": np.nan,
            "sum_trade_return_pct": 0.0,
        }

    r = np.asarray(returns, dtype=float)
    wins = r[r > 0]
    losses = r[r < 0]

    gp = float(wins.sum())
    gl = float(-losses.sum())

    return {
        "trades": int(len(r)),
        "win_rate_pct": float((r > 0).mean() * 100),
        "mean_trade_return_pct": float(r.mean()),
        "profit_factor": (
            gp / gl if gl > 0 else np.inf
        ),
        "sum_trade_return_pct": float(r.sum()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Mechanism WFO: fixed Q70 candidate eligibility; "
            "only FRAGILE_BULL exposure changes."
        )
    )
    parser.add_argument(
        "--market-health",
        required=True,
    )
    parser.add_argument(
        "--db-path",
        default="data/market.db",
    )
    parser.add_argument(
        "--start",
        default="2018-08-07",
    )
    parser.add_argument(
        "--end",
        default="2026-08-21",
    )
    parser.add_argument(
        "--train-months",
        type=int,
        default=24,
    )
    parser.add_argument(
        "--test-months",
        type=int,
        default=6,
    )
    parser.add_argument(
        "--step-months",
        type=int,
        default=6,
    )
    parser.add_argument(
        "--fragile-scales",
        nargs="+",
        type=float,
        default=[1.0, 0.75, 0.50, 0.25, 0.0],
    )
    parser.add_argument(
        "--output",
        default="research_results/q70_exposure_mechanism_wfo_current",
    )
    args = parser.parse_args()

    if any(
        scale < 0 or scale > 1
        for scale in args.fragile_scales
    ):
        raise ValueError(
            "fragile scales must be between 0 and 1."
        )

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    market_health = load_market_health(
        args.market_health
    )

    paper = PaperExecutionConfig.from_env()
    policy = TradingPolicy.from_env()
    parity = BacktestPaperParityConfig.from_paper_config(
        paper,
        sell_tax_rate=policy.sell_tax_rate,
    )

    symbols = get_all_symbols(args.db_path)

    wf = WalkForwardConfig(
        start_date=args.start,
        end_date=args.end,
        train_months=args.train_months,
        test_months=args.test_months,
        step_months=args.step_months,
    )
    folds = build_walk_forward_folds(wf)

    config = build_backtest_config(
        paper,
        parity,
        policy,
    )

    exit_model = build_exit_model(
        name="atr",
        stop_atr_multiplier=policy.stop_atr_multiplier,
        target_atr_multiplier=policy.target_atr_multiplier,
        break_even_trigger=policy.target_atr_multiplier,
        trailing_atr_multiplier=policy.stop_atr_multiplier,
    )
    entry_model = policy.build_entry_model()

    print(
        f"Universe: {len(symbols)} symbols | "
        f"folds: {len(folds)}"
    )
    print(
        f"Policy: entry={policy.entry_model}, "
        f"stop={policy.stop_atr_multiplier} ATR, "
        f"target={policy.target_atr_multiplier} ATR, "
        f"trailing={policy.trailing_atr_multiplier} ATR"
    )
    print(
        "Experiment: Q70 fixed; "
        "Fragile exposure only = "
        + ", ".join(map(str, args.fragile_scales))
    )

    # Generate candidates ONCE for the entire research horizon.
    # Every fold/policy receives the same candidate objects (deep-copied
    # before simulation so quantity_override cannot leak between runs).
    print("\nGenerating full-market candidate pool once...")
    all_candidates = []

    for i, symbol in enumerate(symbols, 1):
        trades = generate_candidate_trades(
            symbol=symbol,
            config=config,
            db_path=args.db_path,
            warmup_bars=60,
            verbose=False,
            start_date=args.start,
            end_date=args.end,
            entry_model=entry_model,
            exit_model=exit_model,
        )
        all_candidates.extend(trades)

        if i % 10 == 0 or i == len(symbols):
            print(
                f"  {i}/{len(symbols)} symbols | "
                f"candidates={len(all_candidates)}"
            )

    all_candidates.sort(
        key=lambda t: (
            pd.Timestamp(t.entry_date),
            str(t.symbol),
        )
    )

    print(
        f"Full candidate pool: {len(all_candidates)}"
    )

    fold_rows = []
    trade_rows = []

    for fold in folds:
        train_candidates = [
            t
            for t in all_candidates
            if (
                pd.Timestamp(t.entry_date)
                >= fold.train_start
                and pd.Timestamp(t.entry_date)
                <= fold.train_end
            )
        ]

        test_candidates = [
            t
            for t in all_candidates
            if (
                pd.Timestamp(t.entry_date)
                >= fold.test_start
                and pd.Timestamp(t.entry_date)
                <= fold.test_end
            )
        ]

        quality = FrozenQualityModel()
        quality.fit(train_candidates)

        q70_candidates = [
            t
            for t in test_candidates
            if quality.score(t) >= 0.70
        ]

        print(
            f"\nFOLD {fold.fold}: "
            f"train={len(train_candidates)} | "
            f"test={len(test_candidates)} | "
            f"Q70={len(q70_candidates)}"
        )

        for scale in args.fragile_scales:
            # Same Q70 candidate set, fresh deep copy, fresh simulator.
            candidates = copy.deepcopy(
                q70_candidates
            )

            base_sizer = parity.build_position_sizer()

            sizer = Q70ExposureSizer(
                base_sizer=base_sizer,
                quality_model=quality,
                market_health=market_health,
                fragile_scale=scale,
            )

            simulator = make_simulator(
                parity=parity,
                position_sizer=sizer,
                paper=paper,
            )

            result = simulator.simulate(
                candidates
            )

            executed = result.executed_trades
            returns = [
                float(t.net_return_pct)
                for t in executed
                if t.net_return_pct is not None
            ]

            stats = summarize_returns(returns)

            fold_rows.append(
                {
                    "policy": (
                        f"q70_f{scale:g}"
                    ),
                    "fold": fold.fold,
                    "train_start": fold.train_start.date(),
                    "train_end": fold.train_end.date(),
                    "test_start": fold.test_start.date(),
                    "test_end": fold.test_end.date(),
                    "all_test_candidates": len(
                        test_candidates
                    ),
                    "q70_candidates": len(
                        q70_candidates
                    ),
                    "executed_trades": len(
                        executed
                    ),
                    "final_equity": result.final_equity,
                    **stats,
                }
            )

            for t in executed:
                d = pd.Timestamp(
                    t.entry_date
                ).normalize()

                if d in market_health.index:
                    mh = market_health.loc[d]
                    breadth = safe_float(
                        mh["breadth_ema50_pct"]
                    )
                    change10 = safe_float(
                        mh["breadth_ema50_change_10d"]
                    )
                else:
                    breadth = np.nan
                    change10 = np.nan

                regime = str(
                    getattr(
                        t,
                        "market_regime",
                        "",
                    )
                    or ""
                ).upper()

                if regime == "BEAR":
                    state = "BEAR"
                elif regime == "BULL":
                    if (
                        np.isfinite(breadth)
                        and np.isfinite(change10)
                        and breadth < 50
                        and change10 < 0
                    ):
                        state = "DIVERGENT_BULL"
                    elif (
                        np.isfinite(breadth)
                        and np.isfinite(change10)
                        and breadth >= 70
                        and change10 >= 0
                    ):
                        state = "HEALTHY_BULL"
                    else:
                        state = "FRAGILE_BULL"
                elif (
                    regime == "SIDEWAY"
                    and np.isfinite(breadth)
                    and np.isfinite(change10)
                    and breadth >= 60
                    and change10 > 0
                ):
                    state = "RECOVERY"
                else:
                    state = "NEUTRAL"

                trade_rows.append(
                    {
                        "policy": f"q70_f{scale:g}",
                        "fold": fold.fold,
                        "symbol": t.symbol,
                        "entry_date": t.entry_date,
                        "exit_date": t.exit_date,
                        "net_return_pct": t.net_return_pct,
                        "quantity": t.quantity,
                        "signal_score": t.signal_score,
                        "relative_strength": t.relative_strength,
                        "adx": t.adx,
                        "volume_ratio": t.volume_ratio,
                        "market_regime": regime,
                        "market_state": state,
                        "breadth50": breadth,
                        "breadth50_change10d": change10,
                    }
                )

    folds_df = pd.DataFrame(fold_rows)
    trades_df = pd.DataFrame(trade_rows)

    folds_df.to_csv(
        output / "fold_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    trades_df.to_csv(
        output / "trade_level.csv",
        index=False,
        encoding="utf-8-sig",
    )

    summary_rows = []

    for name, g in folds_df.groupby(
        "policy",
        sort=False,
    ):
        fold_returns = []

        for _, row in g.sort_values("fold").iterrows():
            # Portfolio return per fold is based on final equity relative
            # to the fold's initial capital. Since every policy gets its
            # own fresh simulator, this is isolated per treatment.
            ret = (
                row["final_equity"]
                / parity.initial_cash
                - 1
            ) * 100
            fold_returns.append(ret)

        r = np.asarray(
            fold_returns,
            dtype=float,
        )

        compounded = (
            (np.prod(1 + r / 100) - 1) * 100
            if len(r)
            else np.nan
        )

        summary_rows.append(
            {
                "policy": name,
                "folds": len(r),
                "profitable_folds": int(
                    (r > 0).sum()
                ),
                "mean_fold_return_pct": (
                    r.mean()
                    if len(r)
                    else np.nan
                ),
                "median_fold_return_pct": (
                    np.median(r)
                    if len(r)
                    else np.nan
                ),
                "compounded_oos_return_pct": compounded,
                "worst_fold_pct": (
                    r.min()
                    if len(r)
                    else np.nan
                ),
                "best_fold_pct": (
                    r.max()
                    if len(r)
                    else np.nan
                ),
                "total_executed_trades": int(
                    g["executed_trades"].sum()
                ),
                "mean_profit_factor": (
                    g["profit_factor"]
                    .replace(
                        [np.inf, -np.inf],
                        np.nan,
                    )
                    .mean()
                ),
                "mean_q70_candidates": float(
                    g["q70_candidates"].mean()
                ),
            }
        )

    summary_df = pd.DataFrame(
        summary_rows
    )
    summary_df.to_csv(
        output / "summary.csv",
        index=False,
        encoding="utf-8-sig",
    )

    print("\n=== SUMMARY ===")
    print(
        summary_df.to_string(
            index=False
        )
    )
    print(
        f"\nOutput: {output.resolve()}"
    )


if __name__ == "__main__":
    main()
