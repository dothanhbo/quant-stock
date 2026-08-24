from __future__ import annotations

import argparse
import math
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
from research.run_momentum_component_ablation import yearly_rows


DEFAULT_OUTPUT_DIR = Path(
    "research_results/breadth_execution_robustness"
)
SLIPPAGE_LEVELS_PCT = (0.05, 0.10, 0.15, 0.20)
EXECUTION_DELAY_LEVELS = (0, 1)


def build_execution_stress_configs(
    shared: dict,
) -> dict[str, MonthlyMomentumConfig]:
    weekly_control = {
        "top_n": 5,
        "gross_exposure_pct": 80.0,
        "entry_rank_max": 5,
        "hold_rank_max": 10,
        "rebalance_tolerance_pct": 20.0,
        "regime_exposure_enabled": True,
        "bull_exposure_pct": 80.0,
        "sideway_exposure_pct": 40.0,
        "bear_exposure_pct": 0.0,
        "daily_exit_ema200_enabled": True,
        "daily_exit_momentum_enabled": True,
        "breadth_exposure_enabled": True,
        "breadth_review_frequency": "WEEKLY",
        "breadth_risk_on_pct": 60.0,
        "breadth_neutral_pct": 40.0,
        **shared,
    }
    cases: dict[str, MonthlyMomentumConfig] = {}
    for delay in EXECUTION_DELAY_LEVELS:
        timing = "next_open" if delay == 0 else "delay_1"
        for slippage_pct in SLIPPAGE_LEVELS_PCT:
            slippage_bps = int(round(slippage_pct * 100))
            case_id = f"{timing}__slip_{slippage_bps:03d}"
            cases[case_id] = MonthlyMomentumConfig(
                **weekly_control,
                slippage_pct=slippage_pct,
                execution_delay_sessions=delay,
            )
    return cases


def attach_order_participation(
    orders: pd.DataFrame,
    *,
    feature_cache: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    output = orders.copy()
    if output.empty:
        for column in (
            "adtv20_data_date",
            "adtv20_on_signal",
            "reference_notional",
            "participation_pct",
        ):
            output[column] = pd.Series(dtype=float)
        return output

    lookups = {}
    for symbol, features in feature_cache.items():
        required = {"time", "adtv20"}
        if features.empty or not required.issubset(features.columns):
            continue
        lookup = features[["time", "adtv20"]].copy()
        lookup["time"] = pd.to_datetime(
            lookup["time"], errors="coerce"
        ).dt.normalize()
        lookup["adtv20"] = pd.to_numeric(
            lookup["adtv20"], errors="coerce"
        )
        lookup = (
            lookup.dropna(subset=["time"])
            .drop_duplicates("time", keep="last")
            .sort_values("time")
            .reset_index(drop=True)
        )
        lookups[symbol] = lookup

    data_dates = []
    adtv_values = []
    reference_notionals = []
    participation_values = []
    for row in output.itertuples(index=False):
        signal_date = pd.Timestamp(row.signal_date).normalize()
        reference_notional = float(row.reference_open) * int(row.quantity)
        lookup = lookups.get(str(row.symbol))
        data_date = pd.NaT
        adtv20 = math.nan
        if lookup is not None:
            causal = lookup.loc[lookup["time"] <= signal_date]
            if not causal.empty:
                latest = causal.iloc[-1]
                candidate = float(latest["adtv20"])
                data_date = pd.Timestamp(latest["time"])
                if math.isfinite(candidate) and candidate > 0:
                    adtv20 = candidate
        participation_pct = (
            reference_notional / adtv20 * 100.0
            if math.isfinite(adtv20) and adtv20 > 0
            else math.nan
        )
        data_dates.append(data_date.date() if pd.notna(data_date) else None)
        adtv_values.append(adtv20)
        reference_notionals.append(reference_notional)
        participation_values.append(participation_pct)

    output["adtv20_data_date"] = data_dates
    output["adtv20_on_signal"] = adtv_values
    output["reference_notional"] = reference_notionals
    output["participation_pct"] = participation_values
    return output


def participation_metrics(
    orders: pd.DataFrame,
    *,
    initial_capital: float,
) -> dict[str, float | int]:
    valid = pd.to_numeric(
        orders.get("participation_pct", pd.Series(dtype=float)),
        errors="coerce",
    ).dropna()
    if valid.empty:
        return {
            "participation_observations": 0,
            "max_participation_pct": math.nan,
            "p95_participation_pct": math.nan,
            "orders_over_1pct_adtv20": 0,
            "orders_over_2pct_adtv20": 0,
            "estimated_capital_at_1pct_adtv20": math.nan,
            "estimated_capital_at_2pct_adtv20": math.nan,
        }
    maximum = float(valid.max())
    scale_to_one = 1.0 / maximum if maximum > 0 else math.nan
    return {
        "participation_observations": int(len(valid)),
        "max_participation_pct": maximum,
        "p95_participation_pct": float(valid.quantile(0.95)),
        "orders_over_1pct_adtv20": int((valid > 1.0).sum()),
        "orders_over_2pct_adtv20": int((valid > 2.0).sum()),
        "estimated_capital_at_1pct_adtv20": (
            initial_capital * scale_to_one
        ),
        "estimated_capital_at_2pct_adtv20": (
            initial_capital * scale_to_one * 2.0
        ),
    }


def participation_by_symbol(orders: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "case_id",
        "symbol",
        "orders",
        "max_participation_pct",
        "p95_participation_pct",
        "orders_over_1pct_adtv20",
        "orders_over_2pct_adtv20",
        "reference_notional",
        "minimum_adtv20_on_signal",
    ]
    if orders.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for (case_id, symbol), group in orders.groupby(
        ["case_id", "symbol"],
        sort=True,
    ):
        participation = pd.to_numeric(
            group["participation_pct"], errors="coerce"
        ).dropna()
        rows.append({
            "case_id": case_id,
            "symbol": symbol,
            "orders": int(len(group)),
            "max_participation_pct": (
                float(participation.max())
                if not participation.empty else math.nan
            ),
            "p95_participation_pct": (
                float(participation.quantile(0.95))
                if not participation.empty else math.nan
            ),
            "orders_over_1pct_adtv20": int((participation > 1.0).sum()),
            "orders_over_2pct_adtv20": int((participation > 2.0).sum()),
            "reference_notional": float(group["reference_notional"].sum()),
            "minimum_adtv20_on_signal": float(
                pd.to_numeric(
                    group["adtv20_on_signal"], errors="coerce"
                ).min()
            ),
        })
    return pd.DataFrame(rows, columns=columns)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Stress test execution cho weekly market-breadth control: "
            "4 mức slippage x 2 timing và capacity theo ADTV20."
        ),
    )
    parser.add_argument("--db", default="data/market.db")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--start", default="2019-08-07")
    parser.add_argument("--end", default="2026-08-21")
    parser.add_argument("--minimum-history", type=int, default=252)
    parser.add_argument(
        "--minimum-adtv",
        type=float,
        default=10_000_000_000.0,
    )
    parser.add_argument("--maximum-volatility", type=float, default=60.0)
    parser.add_argument("--price-scale", type=float, default=1000.0)
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
        get_symbol_list(args.db)
        if args.symbols is None
        else sorted({
            symbol.strip().upper()
            for symbol in args.symbols
            if symbol.strip()
        })
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
    }
    configs = build_execution_stress_configs(shared)
    benchmark_period = benchmark.copy()
    benchmark_period["time"] = pd.to_datetime(benchmark_period["time"])
    benchmark_period = benchmark_period[
        (benchmark_period["time"] >= pd.Timestamp(args.start))
        & (benchmark_period["time"] <= pd.Timestamp(args.end))
    ].sort_values("time").reset_index(drop=True)
    if benchmark_period.empty:
        raise ValueError("VNINDEX không có dữ liệu trong giai đoạn nghiên cứu.")
    benchmark_values = (
        parity.initial_cash
        * benchmark_period["close"]
        / float(benchmark_period.iloc[0]["close"])
    ).reset_index(drop=True)
    benchmark_final = float(benchmark_values.iloc[-1])
    benchmark_return = (
        benchmark_final / parity.initial_cash - 1.0
    ) * 100.0
    benchmark_cagr = cagr_pct(
        parity.initial_cash,
        benchmark_final,
        benchmark_period.iloc[0]["time"],
        benchmark_period.iloc[-1]["time"],
    )
    years = max(
        (
            benchmark_period.iloc[-1]["time"]
            - benchmark_period.iloc[0]["time"]
        ).days / 365.25,
        1 / 365.25,
    )

    summary_rows = []
    yearly_output = []
    order_frames = []
    stale_frames = []
    equity_output = pd.DataFrame({
        "date": benchmark_period["time"].dt.date.to_numpy(),
        "vnindex_equity": benchmark_values.to_numpy(),
    })
    run_args = {
        "feature_cache": feature_cache,
        "benchmark": benchmark,
        "start_date": args.start,
        "end_date": args.end,
        "initial_capital": parity.initial_cash,
    }
    for case_id, config in configs.items():
        print(f"Running {case_id}...")
        result = simulate_monthly_momentum(config=config, **run_args)
        equity = result["equity"]
        if len(equity) != len(equity_output):
            raise ValueError(
                f"Calendar mismatch ở {case_id}: "
                f"strategy={len(equity)}, benchmark={len(equity_output)}"
            )
        orders = attach_order_participation(
            result["orders"],
            feature_cache=feature_cache,
        )
        orders.insert(0, "case_id", case_id)
        order_frames.append(orders)
        participation = participation_metrics(
            orders,
            initial_capital=parity.initial_cash,
        )
        final_equity = float(result["final_equity"])
        strategy_cagr = cagr_pct(
            parity.initial_cash,
            final_equity,
            equity.iloc[0]["date"],
            equity.iloc[-1]["date"],
        )
        drawdown = float(result["max_drawdown_pct"])
        yearly = yearly_rows(
            case_id=case_id,
            equity=equity,
            benchmark_equity=benchmark_values,
        )
        yearly_output.extend(yearly)
        execution_gates = {
            "gate_positive_excess_cagr": strategy_cagr > benchmark_cagr,
            "gate_drawdown_within_25pct": drawdown >= -25.0,
            "gate_participation_within_1pct": (
                int(participation["orders_over_1pct_adtv20"]) == 0
            ),
        }
        summary_rows.append({
            "case_id": case_id,
            "execution_delay_sessions": config.execution_delay_sessions,
            "slippage_pct": config.slippage_pct,
            "final_equity": final_equity,
            "return_pct": float(result["total_return_pct"]),
            "cagr_pct": strategy_cagr,
            "max_drawdown_pct": drawdown,
            "calmar": strategy_cagr / abs(drawdown) if drawdown else 0.0,
            "excess_return_vs_vnindex_pct": (
                float(result["total_return_pct"]) - benchmark_return
            ),
            "excess_cagr_vs_vnindex_pct": strategy_cagr - benchmark_cagr,
            "orders": int(len(orders)),
            "transaction_cost": float(result["total_transaction_cost"]),
            "annualized_cost_drag_pct": (
                float(result["total_transaction_cost"])
                / float(result["average_equity"])
                / years
                * 100.0
            ),
            "annualized_turnover_pct": float(
                result["annualized_turnover_pct"]
            ),
            "stale_valuation_events": int(
                result["stale_valuation_events"]
            ),
            **participation,
            **execution_gates,
            "gate_no_stale_events": (
                int(result["stale_valuation_events"]) == 0
            ),
            "execution_robustness_candidate_passed": all(
                execution_gates.values()
            ),
            "point_in_time_universe_verified": False,
            "prospective_validation_passed": False,
            "research_gate_passed": False,
        })
        equity_output[f"{case_id}_equity"] = equity["equity"].to_numpy()
        stale = result["stale_events"].copy()
        stale.insert(0, "case_id", case_id)
        stale_frames.append(stale)

    summary = pd.DataFrame(summary_rows)
    summary["vnindex_return_pct"] = benchmark_return
    summary["vnindex_cagr_pct"] = benchmark_cagr
    summary["vnindex_max_drawdown_pct"] = price_drawdown_pct(
        benchmark_period["close"]
    )
    all_orders = pd.concat(order_frames, ignore_index=True)
    all_stale = pd.concat(stale_frames, ignore_index=True)
    capacity = participation_by_symbol(all_orders)

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    summary.to_csv(
        output / "execution_stress_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    equity_output.to_csv(
        output / "execution_stress_equity.csv",
        index=False,
        encoding="utf-8-sig",
    )
    pd.DataFrame(yearly_output).to_csv(
        output / "execution_stress_yearly.csv",
        index=False,
        encoding="utf-8-sig",
    )
    all_orders.to_csv(
        output / "execution_stress_orders.csv",
        index=False,
        encoding="utf-8-sig",
    )
    capacity.to_csv(
        output / "execution_stress_participation.csv",
        index=False,
        encoding="utf-8-sig",
    )
    all_stale.to_csv(
        output / "execution_stress_stale_events.csv",
        index=False,
        encoding="utf-8-sig",
    )
    print("\n" + summary.to_string(index=False))
    print(f"\nSaved execution robustness outputs to: {output}")


if __name__ == "__main__":
    main()
