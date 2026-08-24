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
from research.run_monthly_momentum_baseline import cagr_pct, price_drawdown_pct


DEFAULT_OUTPUT_DIR = Path("research_results/momentum_v2_comparison")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="So sánh monthly momentum baseline với Momentum V2 causal.",
    )
    parser.add_argument("--db", default="data/market.db")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--start", default="2019-08-07")
    parser.add_argument("--end", default="2026-08-21")
    parser.add_argument("--minimum-history", type=int, default=252)
    parser.add_argument("--minimum-adtv", type=float, default=10_000_000_000.0)
    parser.add_argument("--maximum-volatility", type=float, default=60.0)
    parser.add_argument("--price-scale", type=float, default=1000.0)
    parser.add_argument("--sell-tax-rate", type=float, default=0.001)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR))
    return parser


def benchmark_equity(
    benchmark: pd.DataFrame,
    *,
    start_date,
    end_date,
    initial_capital: float,
) -> pd.DataFrame:
    period = benchmark.copy()
    period["time"] = pd.to_datetime(period["time"])
    period = period[
        (period["time"] >= pd.Timestamp(start_date))
        & (period["time"] <= pd.Timestamp(end_date))
    ].sort_values("time")
    if period.empty:
        raise ValueError("VNINDEX không có dữ liệu trong giai đoạn nghiên cứu.")
    period["vnindex_equity"] = (
        initial_capital
        * period["close"]
        / float(period.iloc[0]["close"])
    )
    return period[["time", "vnindex_equity"]].rename(columns={"time": "date"})


def comparison_yearly_returns(equity: pd.DataFrame) -> pd.DataFrame:
    data = equity.copy()
    data["date"] = pd.to_datetime(data["date"])
    data["year"] = data["date"].dt.year
    value_columns = ["baseline_equity", "v2_equity", "vnindex_equity"]
    previous: dict[str, float] = {}
    rows = []
    for year, group in data.groupby("year"):
        group = group.sort_values("date")
        row: dict[str, float | int] = {"year": int(year)}
        for column in value_columns:
            start = previous.get(column, float(group.iloc[0][column]))
            end = float(group.iloc[-1][column])
            row[column.replace("_equity", "_return_pct")] = (
                end / start - 1.0
            ) * 100.0
            previous[column] = end
        row["v2_excess_vs_baseline_pct"] = (
            row["v2_return_pct"] - row["baseline_return_pct"]
        )
        row["v2_excess_vs_vnindex_pct"] = (
            row["v2_return_pct"] - row["vnindex_return_pct"]
        )
        rows.append(row)
    return pd.DataFrame(rows)


def strategy_summary(
    *,
    name: str,
    result: dict,
    equity: pd.DataFrame,
    initial_capital: float,
    benchmark_final: float,
    benchmark_cagr: float,
) -> dict:
    final = float(result["final_equity"])
    total_return = float(result["total_return_pct"])
    cagr = cagr_pct(
        initial_capital,
        final,
        equity.iloc[0]["date"],
        equity.iloc[-1]["date"],
    )
    drawdown = float(result["max_drawdown_pct"])
    return {
        "strategy": name,
        "final_equity": final,
        "return_pct": total_return,
        "cagr_pct": cagr,
        "max_drawdown_pct": drawdown,
        "calmar": cagr / abs(drawdown) if drawdown else 0.0,
        "excess_return_vs_vnindex_pct": (
            total_return - (benchmark_final / initial_capital - 1.0) * 100.0
        ),
        "excess_cagr_vs_vnindex_pct": cagr - benchmark_cagr,
        "orders": int(len(result["orders"])),
        "transaction_cost": float(result["total_transaction_cost"]),
        "annualized_turnover_pct": float(result["annualized_turnover_pct"]),
        "annualized_turnover_on_initial_pct": float(
            result["annualized_turnover_on_initial_pct"]
        ),
        "stale_valuation_events": int(result["stale_valuation_events"]),
        "gate_positive_excess_cagr": cagr > benchmark_cagr,
        "gate_drawdown_within_25pct": drawdown >= -25.0,
        "gate_turnover_within_500pct": (
            float(result["annualized_turnover_pct"]) <= 500.0
        ),
        "gate_no_stale_events": int(result["stale_valuation_events"]) == 0,
    }


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
        else sorted({symbol.strip().upper() for symbol in args.symbols if symbol.strip()})
    )
    symbols = [symbol for symbol in symbols if symbol != "VNINDEX"]
    benchmark = load_price_data("VNINDEX", args.db)
    if benchmark.empty:
        raise ValueError("Không tìm thấy VNINDEX trong database.")

    feature_cache = {}
    print(f"Preparing shared feature cache: {len(symbols)} symbols")
    for index, symbol in enumerate(symbols, start=1):
        features = prepare_symbol_features(
            load_price_data(symbol, args.db),
            price_scale=args.price_scale,
        )
        if not features.empty:
            feature_cache[symbol] = features
        if index % 20 == 0 or index == len(symbols):
            print(f"Prepared {index}/{len(symbols)}")

    shared = {
        "minimum_history_rows": args.minimum_history,
        "minimum_adtv20": args.minimum_adtv,
        "maximum_volatility63_pct": args.maximum_volatility,
        "lot_size": parity.lot_size,
        "commission_pct": parity.commission_pct,
        "sell_tax_pct": parity.sell_tax_pct,
        "slippage_pct": parity.slippage_pct,
    }
    baseline_config = MonthlyMomentumConfig(
        top_n=5,
        gross_exposure_pct=80.0,
        **shared,
    )
    v2_config = MonthlyMomentumConfig(
        top_n=10,
        gross_exposure_pct=80.0,
        entry_rank_max=10,
        hold_rank_max=15,
        regime_exposure_enabled=True,
        bull_exposure_pct=80.0,
        sideway_exposure_pct=40.0,
        bear_exposure_pct=0.0,
        daily_exit_enabled=True,
        rebalance_tolerance_pct=20.0,
        **shared,
    )
    run_args = {
        "feature_cache": feature_cache,
        "benchmark": benchmark,
        "start_date": args.start,
        "end_date": args.end,
        "initial_capital": parity.initial_cash,
    }
    print("Running baseline...")
    baseline = simulate_monthly_momentum(config=baseline_config, **run_args)
    print("Running Momentum V2...")
    v2 = simulate_monthly_momentum(config=v2_config, **run_args)

    base_equity = baseline["equity"][["date", "equity"]].rename(
        columns={"equity": "baseline_equity"}
    )
    v2_equity = v2["equity"].rename(columns={
        "cash": "v2_cash",
        "market_value": "v2_market_value",
        "equity": "v2_equity",
        "positions": "v2_positions",
        "running_peak": "v2_running_peak",
        "drawdown_pct": "v2_drawdown_pct",
    })
    comparison = base_equity.merge(v2_equity, on="date", how="inner")
    comparison["date"] = pd.to_datetime(comparison["date"])
    vnindex = benchmark_equity(
        benchmark,
        start_date=args.start,
        end_date=args.end,
        initial_capital=parity.initial_cash,
    )
    comparison = comparison.merge(vnindex, on="date", how="left")
    yearly = comparison_yearly_returns(comparison)
    benchmark_final = float(comparison.iloc[-1]["vnindex_equity"])
    benchmark_cagr = cagr_pct(
        parity.initial_cash,
        benchmark_final,
        comparison.iloc[0]["date"],
        comparison.iloc[-1]["date"],
    )
    summary_rows = [
        strategy_summary(
            name="BASELINE_TOP5",
            result=baseline,
            equity=comparison,
            initial_capital=parity.initial_cash,
            benchmark_final=benchmark_final,
            benchmark_cagr=benchmark_cagr,
        ),
        strategy_summary(
            name="MOMENTUM_V2",
            result=v2,
            equity=comparison,
            initial_capital=parity.initial_cash,
            benchmark_final=benchmark_final,
            benchmark_cagr=benchmark_cagr,
        ),
        {
            "strategy": "VNINDEX_PRICE_ONLY",
            "final_equity": benchmark_final,
            "return_pct": (benchmark_final / parity.initial_cash - 1.0) * 100.0,
            "cagr_pct": benchmark_cagr,
            "max_drawdown_pct": price_drawdown_pct(
                benchmark.loc[
                    (pd.to_datetime(benchmark["time"]) >= pd.Timestamp(args.start))
                    & (pd.to_datetime(benchmark["time"]) <= pd.Timestamp(args.end)),
                    "close",
                ]
            ),
        },
    ]
    summary = pd.DataFrame(summary_rows)
    v2_row = summary.loc[summary["strategy"] == "MOMENTUM_V2"].iloc[0]
    summary["exploratory_candidate_passed"] = False
    summary.loc[
        summary["strategy"] == "MOMENTUM_V2",
        "exploratory_candidate_passed",
    ] = bool(
        v2_row["gate_positive_excess_cagr"]
        and v2_row["gate_drawdown_within_25pct"]
        and v2_row["gate_turnover_within_500pct"]
        and v2_row["gate_no_stale_events"]
    )
    summary["point_in_time_universe_verified"] = False
    summary["prospective_validation_passed"] = False
    summary["research_gate_passed"] = False

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    summary.to_csv(output / "momentum_v2_comparison_summary.csv", index=False, encoding="utf-8-sig")
    comparison.to_csv(output / "momentum_v2_comparison_equity.csv", index=False, encoding="utf-8-sig")
    yearly.to_csv(output / "momentum_v2_yearly.csv", index=False, encoding="utf-8-sig")
    v2["orders"].to_csv(output / "momentum_v2_orders.csv", index=False, encoding="utf-8-sig")
    v2["snapshots"].to_csv(output / "momentum_v2_snapshots.csv", index=False, encoding="utf-8-sig")
    v2["stale_events"].to_csv(output / "momentum_v2_stale_events.csv", index=False, encoding="utf-8-sig")
    print("\n" + summary.to_string(index=False))
    print(f"\nSaved Momentum V2 comparison to: {output}")


if __name__ == "__main__":
    main()
