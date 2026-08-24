from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv() -> bool:
        return False

from backtesting.engine import get_symbol_list, load_price_data
from backtesting.paper_parity import BacktestPaperParityConfig
from execution.signal_executor import PaperExecutionConfig
from research.monthly_momentum_baseline import (
    MonthlyMomentumConfig,
    prepare_symbol_features,
    simulate_monthly_momentum,
)


DEFAULT_OUTPUT_DIR = Path("research_results/monthly_momentum_baseline")


def cagr_pct(initial: float, final: float, start, end) -> float:
    days = (pd.Timestamp(end) - pd.Timestamp(start)).days
    if initial <= 0 or final <= 0 or days <= 0:
        return 0.0
    return float(((final / initial) ** (365.25 / days) - 1.0) * 100.0)


def price_drawdown_pct(prices: pd.Series) -> float:
    values = pd.to_numeric(prices, errors="coerce").dropna()
    values = values[values > 0]
    if values.empty:
        return 0.0
    return float(((values / values.cummax()) - 1.0).min() * 100.0)


def build_yearly_returns(equity: pd.DataFrame) -> pd.DataFrame:
    if equity.empty:
        return pd.DataFrame()
    data = equity.copy()
    data["date"] = pd.to_datetime(data["date"])
    data["year"] = data["date"].dt.year
    rows = []
    previous_strategy = None
    previous_benchmark = None
    for year, group in data.groupby("year"):
        group = group.sort_values("date")
        strategy_start = (
            previous_strategy if previous_strategy is not None
            else float(group.iloc[0]["equity"])
        )
        benchmark_start = (
            previous_benchmark if previous_benchmark is not None
            else float(group.iloc[0]["benchmark_equity"])
        )
        strategy_end = float(group.iloc[-1]["equity"])
        benchmark_end = float(group.iloc[-1]["benchmark_equity"])
        rows.append({
            "year": int(year),
            "strategy_return_pct": (strategy_end / strategy_start - 1.0) * 100.0,
            "vnindex_return_pct": (benchmark_end / benchmark_start - 1.0) * 100.0,
            "excess_return_pct": (
                (strategy_end / strategy_start) - (benchmark_end / benchmark_start)
            ) * 100.0,
        })
        previous_strategy = strategy_end
        previous_benchmark = benchmark_end
    return pd.DataFrame(rows)


def exploratory_gates(summary: dict) -> dict:
    gates = {
        "gate_excess_return_vs_vnindex_positive": (
            float(summary["excess_return_vs_vnindex_pct"]) > 0.0
        ),
        "gate_excess_cagr_vs_vnindex_positive": (
            float(summary["excess_cagr_vs_vnindex_pct"]) > 0.0
        ),
        "gate_drawdown_within_25pct": (
            float(summary["strategy_max_drawdown_pct"]) >= -25.0
        ),
        "gate_profitable_years_at_least_half": (
            int(summary["profitable_years"]) * 2 >= int(summary["years"])
        ),
        "gate_annualized_turnover_within_300pct": (
            float(summary["annualized_turnover_pct"]) <= 300.0
        ),
        "gate_no_stale_valuation_events": (
            int(summary["stale_valuation_events"]) == 0
        ),
    }
    exploratory_passed = all(gates.values())
    final_gates = {
        **gates,
        "exploratory_gate_passed": exploratory_passed,
        "gate_point_in_time_universe_verified": bool(
            summary["point_in_time_universe_verified"]
        ),
        "gate_prospective_validation_passed": bool(
            summary["prospective_validation_passed"]
        ),
    }
    return {
        **final_gates,
        "research_gate_passed": all(final_gates.values()),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Exploratory point-in-time feature universe with monthly "
            "6-1 momentum, EMA200 trend and explicit VNINDEX benchmark."
        )
    )
    parser.add_argument("--db", default="data/market.db")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--start", default="2019-08-07")
    parser.add_argument("--end", default="2026-08-21")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--gross-exposure", type=float, default=80.0)
    parser.add_argument("--minimum-history", type=int, default=252)
    parser.add_argument("--minimum-adtv", type=float, default=10_000_000_000.0)
    parser.add_argument("--maximum-volatility", type=float, default=60.0)
    parser.add_argument(
        "--price-scale",
        type=float,
        default=1000.0,
        help="DB lưu giá theo nghìn VND; mặc định đổi sang VND bằng hệ số 1000.",
    )
    parser.add_argument(
        "--sell-tax-rate",
        type=float,
        default=0.001,
        help="Thuế bán dạng tỷ lệ thập phân; 0.001 tương đương 0.10%%.",
    )
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
        get_symbol_list(args.db)
        if args.symbols is None
        else sorted({value.strip().upper() for value in args.symbols if value.strip()})
    )
    symbols = [symbol for symbol in symbols if symbol != "VNINDEX"]
    benchmark = load_price_data("VNINDEX", args.db)
    if benchmark.empty:
        raise ValueError("VNINDEX không có dữ liệu trong database được chỉ định.")
    feature_cache = {}
    metadata_rows = []
    print(f"Preparing dynamic universe: {len(symbols)} symbols")
    for index, symbol in enumerate(symbols, start=1):
        prices = load_price_data(symbol, args.db)
        features = prepare_symbol_features(
            prices,
            price_scale=args.price_scale,
        )
        if not features.empty:
            feature_cache[symbol] = features
        metadata_rows.append({
            "symbol": symbol,
            "rows": int(len(prices)),
            "first_date": (
                pd.Timestamp(prices["time"].min()).date() if not prices.empty else None
            ),
            "last_date": (
                pd.Timestamp(prices["time"].max()).date() if not prices.empty else None
            ),
            "point_in_time_membership_verified": False,
            "source_inventory": "current_database_symbols",
        })
        if index % 20 == 0 or index == len(symbols):
            print(f"Prepared {index}/{len(symbols)}")

    config = MonthlyMomentumConfig(
        top_n=args.top_n,
        gross_exposure_pct=args.gross_exposure,
        minimum_history_rows=args.minimum_history,
        minimum_adtv20=args.minimum_adtv,
        maximum_volatility63_pct=args.maximum_volatility,
        lot_size=parity.lot_size,
        commission_pct=parity.commission_pct,
        sell_tax_pct=parity.sell_tax_pct,
        slippage_pct=parity.slippage_pct,
    )
    result = simulate_monthly_momentum(
        feature_cache=feature_cache,
        benchmark=benchmark,
        start_date=args.start,
        end_date=args.end,
        initial_capital=parity.initial_cash,
        config=config,
    )
    equity = result["equity"].copy()
    if equity.empty:
        raise ValueError("Không có phiên VNINDEX trong khoảng thời gian nghiên cứu.")
    benchmark_period = benchmark[
        (benchmark["time"] >= pd.Timestamp(args.start))
        & (benchmark["time"] <= pd.Timestamp(args.end))
    ].copy()
    benchmark_period = benchmark_period.sort_values("time")
    if benchmark_period.empty:
        raise ValueError("VNINDEX không có dữ liệu trong khoảng thời gian nghiên cứu.")
    benchmark_period["benchmark_equity"] = (
        parity.initial_cash
        * benchmark_period["close"]
        / float(benchmark_period.iloc[0]["close"])
    )
    equity["date"] = pd.to_datetime(equity["date"])
    equity = equity.merge(
        benchmark_period[["time", "benchmark_equity"]].rename(columns={"time": "date"}),
        on="date",
        how="left",
    )
    yearly = build_yearly_returns(equity)
    strategy_final = float(result["final_equity"])
    benchmark_final = float(equity.iloc[-1]["benchmark_equity"])
    strategy_return = float(result["total_return_pct"])
    benchmark_return = (benchmark_final / parity.initial_cash - 1.0) * 100.0
    strategy_cagr = cagr_pct(
        parity.initial_cash, strategy_final, equity.iloc[0]["date"], equity.iloc[-1]["date"]
    )
    benchmark_cagr = cagr_pct(
        parity.initial_cash, benchmark_final, equity.iloc[0]["date"], equity.iloc[-1]["date"]
    )
    snapshots = result["snapshots"]
    summary = {
        "research_status": "EXPLORATORY_ONLY",
        "database_path": str(Path(args.db).resolve()),
        "inventory_symbols": len(symbols),
        "feature_ready_symbols": len(feature_cache),
        "universe_method": "dynamic_features_from_current_database_inventory",
        "point_in_time_universe_verified": False,
        "prospective_validation_passed": False,
        "start_date": equity.iloc[0]["date"].date(),
        "end_date": equity.iloc[-1]["date"].date(),
        "top_n": config.top_n,
        "gross_exposure_pct": config.gross_exposure_pct,
        "minimum_history_rows": config.minimum_history_rows,
        "minimum_adtv20": config.minimum_adtv20,
        "stock_price_scale_to_vnd": args.price_scale,
        "maximum_volatility63_pct": config.maximum_volatility63_pct,
        "strategy_final_equity": strategy_final,
        "strategy_return_pct": strategy_return,
        "strategy_cagr_pct": strategy_cagr,
        "strategy_max_drawdown_pct": float(result["max_drawdown_pct"]),
        "strategy_calmar": (
            strategy_cagr / abs(float(result["max_drawdown_pct"]))
            if float(result["max_drawdown_pct"]) else 0.0
        ),
        "vnindex_final_equity": benchmark_final,
        "vnindex_return_pct": benchmark_return,
        "vnindex_cagr_pct": benchmark_cagr,
        "vnindex_max_drawdown_pct": price_drawdown_pct(benchmark_period["close"]),
        "excess_return_vs_vnindex_pct": strategy_return - benchmark_return,
        "excess_cagr_vs_vnindex_pct": strategy_cagr - benchmark_cagr,
        "total_orders": int(len(result["orders"])),
        "total_transaction_cost": float(result["total_transaction_cost"]),
        "annualized_turnover_pct": float(result["annualized_turnover_pct"]),
        "annualized_turnover_on_initial_pct": float(
            result["annualized_turnover_on_initial_pct"]
        ),
        "stale_valuation_events": int(result["stale_valuation_events"]),
        "rebalance_periods": int(snapshots["signal_date"].nunique()) if not snapshots.empty else 0,
        "average_eligible_symbols": (
            float(snapshots.groupby("signal_date")["eligible"].sum().mean())
            if not snapshots.empty else 0.0
        ),
        "unique_selected_symbols": (
            int(snapshots.loc[snapshots["selected"], "symbol"].nunique())
            if not snapshots.empty else 0
        ),
        "years": int(len(yearly)),
        "profitable_years": int((yearly["strategy_return_pct"] > 0).sum()),
        "years_beating_vnindex": int((yearly["excess_return_pct"] > 0).sum()),
        "benchmark_note": "VNINDEX price-only close-to-close; dividends excluded",
    }
    summary.update(exploratory_gates(summary))

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    equity.to_csv(output / "monthly_momentum_equity.csv", index=False, encoding="utf-8-sig")
    result["orders"].to_csv(
        output / "monthly_momentum_orders.csv", index=False, encoding="utf-8-sig"
    )
    snapshots.to_csv(
        output / "monthly_momentum_universe_snapshots.csv", index=False, encoding="utf-8-sig"
    )
    result["stale_events"].to_csv(
        output / "monthly_momentum_stale_events.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(metadata_rows).to_csv(
        output / "monthly_momentum_inventory_metadata.csv", index=False, encoding="utf-8-sig"
    )
    yearly.to_csv(output / "monthly_momentum_yearly.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([summary]).to_csv(
        output / "monthly_momentum_summary.csv", index=False, encoding="utf-8-sig"
    )
    print("\n" + pd.DataFrame([summary]).to_string(index=False))
    print(f"\nSaved monthly momentum outputs to: {output}")


if __name__ == "__main__":
    main()
