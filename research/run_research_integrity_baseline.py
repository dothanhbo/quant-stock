from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv() -> bool:
        return False

from backtesting.current_logic import CurrentScannerExitModel
from backtesting.engine import load_price_data, run_backtest
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.position_sizers import FixedFractionSizer
from backtesting.walk_forward import (
    WalkForwardConfig,
    build_walk_forward_folds,
    calculate_chained_drawdown_pct,
)
from execution.signal_executor import PaperExecutionConfig
from research.run_entry_ablation import compound_return
from research.universes import HOLDOUT20_SYMBOLS
from strategy.donchian_breakout_entry import DonchianBreakoutEntryModel
from strategy.hybrid_trend_donchian_entry import HybridTrendDonchianEntryModel


DEFAULT_OUTPUT_DIR = Path("research_results/research_integrity_baseline")


def finite(value, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def calculate_cagr_pct(
    initial_value: float,
    final_value: float,
    start_date,
    end_date,
) -> float:
    days = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days
    if initial_value <= 0 or final_value <= 0 or days <= 0:
        return 0.0
    return float(
        ((final_value / initial_value) ** (365.25 / days) - 1.0) * 100.0
    )


def calculate_price_drawdown_pct(prices: pd.Series) -> float:
    values = pd.to_numeric(prices, errors="coerce").dropna()
    values = values[values > 0]
    if values.empty:
        return 0.0
    return float(((values / values.cummax()) - 1.0).min() * 100.0)


def build_entry_model():
    return HybridTrendDonchianEntryModel(
        mode="trend_context",
        min_hybrid_score=60,
        donchian_model=DonchianBreakoutEntryModel(
            use_regime_thresholds=True,
            regime_threshold_fields={"min_volume_ratio"},
        ),
        use_regime_thresholds=True,
        require_hybrid_score=True,
    )


def build_attribution(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    frames: list[dict] = []
    dimensions = ("symbol", "entry_year", "market_regime", "exit_reason")
    for dimension in dimensions:
        for value, group in trades.groupby(dimension, dropna=False):
            pnl = pd.to_numeric(group["net_pnl"], errors="coerce").fillna(0.0)
            returns = pd.to_numeric(
                group["net_return_pct"], errors="coerce"
            ).dropna()
            wins = pnl[pnl > 0]
            losses = pnl[pnl < 0]
            gross_profit = float(wins.sum())
            gross_loss = abs(float(losses.sum()))
            frames.append({
                "dimension": dimension,
                "value": str(value),
                "trades": int(len(group)),
                "wins": int((pnl > 0).sum()),
                "win_rate_pct": float((pnl > 0).mean() * 100.0),
                "average_return_pct": finite(returns.mean()),
                "median_return_pct": finite(returns.median()),
                "total_net_pnl": float(pnl.sum()),
                "profit_factor": (
                    gross_profit / gross_loss if gross_loss > 0 else 0.0
                ),
                "transaction_cost": float(
                    pd.to_numeric(
                        group["transaction_cost"], errors="coerce"
                    ).fillna(0.0).sum()
                ),
            })
    return pd.DataFrame(frames)


def build_universe_metadata(
    *,
    symbols: list[str],
    db_path: str,
    required_start,
    required_end,
) -> pd.DataFrame:
    rows = []
    start = pd.Timestamp(required_start)
    end = pd.Timestamp(required_end)
    for symbol in symbols:
        prices = load_price_data(symbol, db_path)
        dates = pd.to_datetime(prices.get("time"), errors="coerce").dropna()
        first_date = dates.min() if not dates.empty else pd.NaT
        last_date = dates.max() if not dates.empty else pd.NaT
        rows.append({
            "symbol": symbol,
            "rows": int(len(prices)),
            "first_date": (
                first_date.date() if not pd.isna(first_date) else None
            ),
            "last_date": last_date.date() if not pd.isna(last_date) else None,
            "covers_required_start": (
                bool(first_date <= start) if not pd.isna(first_date) else False
            ),
            "covers_required_end": (
                bool(last_date >= end) if not pd.isna(last_date) else False
            ),
            "point_in_time_membership_verified": False,
            "universe_method": "static_current_holdout20",
        })
    return pd.DataFrame(rows)


def research_gates(summary: dict) -> dict:
    gates = {
        "gate_profitable_folds_at_least_half": (
            int(summary["profitable_folds"]) >= 6
        ),
        "gate_median_non_negative": (
            float(summary["median_test_return_pct"]) >= 0.0
        ),
        "gate_excluding_first_two_folds_non_negative": (
            float(summary["return_excluding_first_two_folds_pct"]) >= 0.0
        ),
        "gate_recent_3_folds_non_negative": (
            float(summary["recent_3_folds_return_pct"]) >= 0.0
        ),
        "gate_chained_drawdown_within_15pct": (
            float(summary["strategy_chained_drawdown_pct"]) >= -15.0
        ),
        "gate_excess_return_vs_vnindex_positive": (
            float(summary["excess_return_vs_vnindex_pct"]) > 0.0
        ),
        "gate_point_in_time_universe_verified": bool(
            summary["point_in_time_universe_verified"]
        ),
    }
    return {**gates, "research_gate_passed": all(gates.values())}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Decision baseline with explicit DB, chained OOS metrics, "
            "VNINDEX benchmark, attribution and universe-bias metadata."
        )
    )
    parser.add_argument("--db", default="data/market.db")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--start", default="2018-08-07")
    parser.add_argument("--end", default="2026-08-21")
    parser.add_argument("--train-months", type=int, default=24)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=6)
    parser.add_argument("--hold", type=int, default=30)
    parser.add_argument("--sell-tax-rate", type=float, default=0.001)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR))
    return parser


def main() -> None:
    load_dotenv()
    args = build_parser().parse_args()
    paper = PaperExecutionConfig.from_env()
    parity = BacktestPaperParityConfig.from_paper_config(
        paper,
        sell_tax_rate=args.sell_tax_rate,
    )
    symbols = (
        list(HOLDOUT20_SYMBOLS)
        if args.symbols is None
        else [value.strip().upper() for value in args.symbols if value.strip()]
    )
    folds = build_walk_forward_folds(WalkForwardConfig(
        start_date=args.start,
        end_date=args.end,
        train_months=args.train_months,
        test_months=args.test_months,
        step_months=args.step_months,
    ))
    backtest_kwargs = {
        "db_path": args.db,
        "symbols": symbols,
        "max_holding_days": args.hold,
        "entry_model": build_entry_model(),
        "exit_model": CurrentScannerExitModel(),
        "ranking_method": "signal_score",
        "position_sizer": FixedFractionSizer(position_size_pct=20.0),
        "buy_commission_pct": parity.commission_pct,
        "sell_commission_pct": parity.commission_pct,
        "sell_tax_pct": parity.sell_tax_pct,
        "buy_slippage_pct": parity.slippage_pct,
        "sell_slippage_pct": parity.slippage_pct,
        "max_positions": parity.maximum_open_positions,
        "lot_size": parity.lot_size,
        "max_new_positions_per_day": paper.maximum_orders_per_scan,
        "maximum_gross_exposure_pct": parity.maximum_gross_exposure_pct,
        "minimum_cash_buffer_pct": parity.minimum_cash_buffer_pct,
    }

    current_capital = float(parity.initial_cash)
    fold_rows: list[dict] = []
    trade_rows: list[dict] = []
    equity_curves: list[pd.DataFrame] = []
    for fold in folds:
        print(f"Integrity fold {fold.fold}/{len(folds)}")
        trades, metrics, equity = run_backtest(
            **backtest_kwargs,
            start_date=str(fold.test_start.date()),
            end_date=str(fold.test_end.date()),
            initial_capital=current_capital,
            verbose=False,
        )
        if isinstance(equity, pd.DataFrame):
            equity_curves.append(equity.copy())
        final_equity = finite(metrics.get("final_equity"), current_capital)
        fold_return = finite(metrics.get("total_return_pct"))
        fold_rows.append({
            **fold.to_dict(),
            "test_initial_capital": current_capital,
            "test_final_equity": final_equity,
            "test_trades": len(trades),
            "test_return_pct": fold_return,
            "test_sharpe": finite(metrics.get("sharpe_ratio")),
            "test_drawdown_pct": finite(metrics.get("max_drawdown_pct")),
            "test_profitable": fold_return > 0,
        })
        for trade in trades:
            row = trade.to_dict()
            row["fold"] = fold.fold
            row["entry_year"] = pd.Timestamp(trade.entry_date).year
            trade_rows.append(row)
        current_capital = final_equity

    folds_df = pd.DataFrame(fold_rows)
    trades_df = pd.DataFrame(trade_rows)
    attribution_df = build_attribution(trades_df)
    universe_df = build_universe_metadata(
        symbols=symbols,
        db_path=args.db,
        required_start=folds[0].test_start,
        required_end=folds[-1].test_end,
    )

    benchmark = load_price_data("VNINDEX", args.db)
    benchmark = benchmark[
        (benchmark["time"] >= folds[0].test_start)
        & (benchmark["time"] <= folds[-1].test_end)
    ].copy()
    benchmark_return = (
        (float(benchmark["close"].iloc[-1]) / float(benchmark["close"].iloc[0]) - 1)
        * 100.0
        if len(benchmark) >= 2
        else 0.0
    )
    benchmark_final = parity.initial_cash * (1.0 + benchmark_return / 100.0)
    strategy_return = (current_capital / parity.initial_cash - 1.0) * 100.0
    returns = folds_df["test_return_pct"]
    strategy_dd = calculate_chained_drawdown_pct(equity_curves)
    benchmark_dd = calculate_price_drawdown_pct(benchmark["close"])
    strategy_cagr = calculate_cagr_pct(
        parity.initial_cash, current_capital, folds[0].test_start, folds[-1].test_end
    )
    benchmark_cagr = calculate_cagr_pct(
        parity.initial_cash, benchmark_final, folds[0].test_start, folds[-1].test_end
    )
    summary = {
        "database_path": str(Path(args.db).resolve()),
        "database_exists": Path(args.db).exists(),
        "symbols": len(symbols),
        "universe_method": "static_current_holdout20",
        "point_in_time_universe_verified": False,
        "oos_start": folds[0].test_start.date(),
        "oos_end": folds[-1].test_end.date(),
        "folds": len(folds_df),
        "initial_capital": parity.initial_cash,
        "strategy_final_equity": current_capital,
        "strategy_return_pct": strategy_return,
        "strategy_cagr_pct": strategy_cagr,
        "strategy_chained_drawdown_pct": strategy_dd,
        "strategy_calmar": strategy_cagr / abs(strategy_dd) if strategy_dd else 0.0,
        "vnindex_price_return_pct": benchmark_return,
        "vnindex_price_cagr_pct": benchmark_cagr,
        "vnindex_price_drawdown_pct": benchmark_dd,
        "vnindex_calmar": benchmark_cagr / abs(benchmark_dd) if benchmark_dd else 0.0,
        "cash_return_pct": 0.0,
        "excess_return_vs_vnindex_pct": strategy_return - benchmark_return,
        "excess_cagr_vs_vnindex_pct": strategy_cagr - benchmark_cagr,
        "profitable_folds": int(folds_df["test_profitable"].sum()),
        "median_test_return_pct": float(returns.median()),
        "return_excluding_first_fold_pct": compound_return(returns.iloc[1:]),
        "return_excluding_first_two_folds_pct": compound_return(returns.iloc[2:]),
        "recent_3_folds_return_pct": compound_return(returns.iloc[-3:]),
        "total_test_trades": int(folds_df["test_trades"].sum()),
        "universe_symbols_missing_start_coverage": int(
            (~universe_df["covers_required_start"]).sum()
        ),
        "universe_symbols_missing_end_coverage": int(
            (~universe_df["covers_required_end"]).sum()
        ),
        "benchmark_note": "VNINDEX close-to-close price return; dividends excluded",
    }
    summary.update(research_gates(summary))

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    folds_df.to_csv(output / "integrity_folds.csv", index=False, encoding="utf-8-sig")
    trades_df.to_csv(output / "integrity_trades.csv", index=False, encoding="utf-8-sig")
    attribution_df.to_csv(
        output / "integrity_attribution.csv", index=False, encoding="utf-8-sig"
    )
    universe_df.to_csv(
        output / "integrity_universe_metadata.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame([summary]).to_csv(
        output / "integrity_summary.csv", index=False, encoding="utf-8-sig"
    )
    print("\n" + pd.DataFrame([summary]).to_string(index=False))
    print(f"\nSaved research integrity outputs to: {output}")


if __name__ == "__main__":
    main()
