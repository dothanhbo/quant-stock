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


DEFAULT_OUTPUT_DIR = Path("research_results/momentum_component_ablation")
TOP5_OUTPUT_DIR = Path("research_results/top5_risk_ablation")
BREADTH_OUTPUT_DIR = Path("research_results/breadth_overlay_ablation")
BREADTH_HYSTERESIS_OUTPUT_DIR = Path(
    "research_results/breadth_hysteresis_ablation"
)


def build_ablation_configs(shared: dict) -> dict[str, MonthlyMomentumConfig]:
    top10_buffer = {
        "top_n": 10,
        "gross_exposure_pct": 80.0,
        "entry_rank_max": 10,
        "hold_rank_max": 15,
        "rebalance_tolerance_pct": 20.0,
        **shared,
    }
    return {
        "baseline_top5": MonthlyMomentumConfig(
            top_n=5,
            gross_exposure_pct=80.0,
            **shared,
        ),
        "top10_buffer": MonthlyMomentumConfig(**top10_buffer),
        "top10_buffer_regime": MonthlyMomentumConfig(
            **top10_buffer,
            regime_exposure_enabled=True,
            bull_exposure_pct=80.0,
            sideway_exposure_pct=40.0,
            bear_exposure_pct=0.0,
        ),
        "top10_buffer_ema_exit": MonthlyMomentumConfig(
            **top10_buffer,
            daily_exit_ema200_enabled=True,
        ),
        "top10_buffer_momentum_exit": MonthlyMomentumConfig(
            **top10_buffer,
            daily_exit_momentum_enabled=True,
        ),
        "full_v2": MonthlyMomentumConfig(
            **top10_buffer,
            regime_exposure_enabled=True,
            bull_exposure_pct=80.0,
            sideway_exposure_pct=40.0,
            bear_exposure_pct=0.0,
            daily_exit_ema200_enabled=True,
            daily_exit_momentum_enabled=True,
        ),
    }


def build_top5_risk_configs(shared: dict) -> dict[str, MonthlyMomentumConfig]:
    top5_buffer = {
        "top_n": 5,
        "gross_exposure_pct": 80.0,
        "entry_rank_max": 5,
        "hold_rank_max": 10,
        "rebalance_tolerance_pct": 20.0,
        **shared,
    }
    return {
        "baseline_top5": MonthlyMomentumConfig(
            top_n=5,
            gross_exposure_pct=80.0,
            **shared,
        ),
        "top5_buffer": MonthlyMomentumConfig(**top5_buffer),
        "top5_buffer_regime": MonthlyMomentumConfig(
            **top5_buffer,
            regime_exposure_enabled=True,
            bull_exposure_pct=80.0,
            sideway_exposure_pct=40.0,
            bear_exposure_pct=0.0,
        ),
        "top5_buffer_ema_exit": MonthlyMomentumConfig(
            **top5_buffer,
            daily_exit_ema200_enabled=True,
        ),
        "top5_buffer_momentum_exit": MonthlyMomentumConfig(
            **top5_buffer,
            daily_exit_momentum_enabled=True,
        ),
        "top5_full_risk": MonthlyMomentumConfig(
            **top5_buffer,
            regime_exposure_enabled=True,
            bull_exposure_pct=80.0,
            sideway_exposure_pct=40.0,
            bear_exposure_pct=0.0,
            daily_exit_ema200_enabled=True,
            daily_exit_momentum_enabled=True,
        ),
    }


def build_breadth_overlay_configs(
    shared: dict,
) -> dict[str, MonthlyMomentumConfig]:
    full_risk = {
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
        **shared,
    }
    return {
        "top5_full_risk_control": MonthlyMomentumConfig(**full_risk),
        "breadth_monthly": MonthlyMomentumConfig(
            **full_risk,
            breadth_exposure_enabled=True,
            breadth_review_frequency="MONTHLY",
            breadth_risk_on_pct=60.0,
            breadth_neutral_pct=40.0,
        ),
        "breadth_weekly": MonthlyMomentumConfig(
            **full_risk,
            breadth_exposure_enabled=True,
            breadth_review_frequency="WEEKLY",
            breadth_risk_on_pct=60.0,
            breadth_neutral_pct=40.0,
        ),
        "breadth_daily": MonthlyMomentumConfig(
            **full_risk,
            breadth_exposure_enabled=True,
            breadth_review_frequency="DAILY",
            breadth_risk_on_pct=60.0,
            breadth_neutral_pct=40.0,
        ),
    }


def build_breadth_hysteresis_configs(
    shared: dict,
) -> dict[str, MonthlyMomentumConfig]:
    weekly_breadth = {
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
    return {
        "breadth_weekly_control": MonthlyMomentumConfig(**weekly_breadth),
        "breadth_weekly_confirm_2": MonthlyMomentumConfig(
            **weekly_breadth,
            breadth_recovery_confirmation_periods=2,
        ),
        "breadth_weekly_risk_on_monthly": MonthlyMomentumConfig(
            **weekly_breadth,
            breadth_recovery_frequency="MONTHLY",
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ablation causal của từng component trong Momentum V2.",
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
    parser.add_argument(
        "--matrix",
        choices=("top10", "top5", "breadth", "breadth_hysteresis"),
        default="top10",
        help=(
            "Chọn ma trận ablation; breadth_hysteresis so sánh weekly gốc, "
            "xác nhận hồi phục 2 tuần và chỉ risk-on lại ở rebalance tháng."
        ),
    )
    parser.add_argument("--output", default=None)
    return parser


def yearly_rows(
    *,
    case_id: str,
    equity: pd.DataFrame,
    benchmark_equity: pd.Series,
) -> list[dict]:
    data = equity[["date", "equity"]].copy()
    data["date"] = pd.to_datetime(data["date"])
    data["benchmark_equity"] = benchmark_equity.to_numpy()
    data["year"] = data["date"].dt.year
    previous_strategy = None
    previous_benchmark = None
    rows = []
    for year, group in data.groupby("year"):
        group = group.sort_values("date")
        strategy_start = (
            previous_strategy
            if previous_strategy is not None
            else float(group.iloc[0]["equity"])
        )
        benchmark_start = (
            previous_benchmark
            if previous_benchmark is not None
            else float(group.iloc[0]["benchmark_equity"])
        )
        strategy_end = float(group.iloc[-1]["equity"])
        benchmark_end = float(group.iloc[-1]["benchmark_equity"])
        strategy_return = (strategy_end / strategy_start - 1.0) * 100.0
        benchmark_return = (benchmark_end / benchmark_start - 1.0) * 100.0
        rows.append({
            "case_id": case_id,
            "year": int(year),
            "strategy_return_pct": strategy_return,
            "vnindex_return_pct": benchmark_return,
            "excess_return_pct": strategy_return - benchmark_return,
        })
        previous_strategy = strategy_end
        previous_benchmark = benchmark_end
    return rows


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
    if args.matrix == "breadth_hysteresis":
        configs = build_breadth_hysteresis_configs(shared)
    elif args.matrix == "breadth":
        configs = build_breadth_overlay_configs(shared)
    elif args.matrix == "top5":
        configs = build_top5_risk_configs(shared)
    else:
        configs = build_ablation_configs(shared)
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
    benchmark_return = (benchmark_final / parity.initial_cash - 1.0) * 100.0
    benchmark_cagr = cagr_pct(
        parity.initial_cash,
        benchmark_final,
        benchmark_period.iloc[0]["time"],
        benchmark_period.iloc[-1]["time"],
    )

    summary_rows = []
    yearly_output = []
    order_frames = []
    stale_frames = []
    selection_frames = []
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
        final_equity = float(result["final_equity"])
        total_return = float(result["total_return_pct"])
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
        profitable_years = sum(row["strategy_return_pct"] > 0 for row in yearly)
        years_beating = sum(row["excess_return_pct"] > 0 for row in yearly)
        gates = {
            "gate_positive_excess_cagr": strategy_cagr > benchmark_cagr,
            "gate_drawdown_within_25pct": drawdown >= -25.0,
            "gate_turnover_within_500pct": (
                float(result["annualized_turnover_pct"]) <= 500.0
            ),
            "gate_no_stale_events": int(result["stale_valuation_events"]) == 0,
        }
        summary_rows.append({
            "case_id": case_id,
            "matrix": args.matrix,
            "top_n": config.top_n,
            "entry_rank_max": config.effective_entry_rank,
            "hold_rank_max": config.effective_hold_rank,
            "rebalance_tolerance_pct": config.rebalance_tolerance_pct,
            "regime_enabled": config.regime_exposure_enabled,
            "ema200_exit_enabled": config.daily_exit_ema200_enabled,
            "momentum_exit_enabled": config.daily_exit_momentum_enabled,
            "breadth_enabled": config.breadth_exposure_enabled,
            "breadth_review_frequency": config.breadth_review_frequency,
            "breadth_risk_on_pct": config.breadth_risk_on_pct,
            "breadth_neutral_pct": config.breadth_neutral_pct,
            "breadth_recovery_confirmation_periods": (
                config.breadth_recovery_confirmation_periods
            ),
            "breadth_recovery_frequency": config.breadth_recovery_frequency,
            "final_equity": final_equity,
            "return_pct": total_return,
            "cagr_pct": strategy_cagr,
            "max_drawdown_pct": drawdown,
            "calmar": strategy_cagr / abs(drawdown) if drawdown else 0.0,
            "excess_return_vs_vnindex_pct": total_return - benchmark_return,
            "excess_cagr_vs_vnindex_pct": strategy_cagr - benchmark_cagr,
            "orders": int(len(result["orders"])),
            "transaction_cost": float(result["total_transaction_cost"]),
            "annualized_turnover_pct": float(result["annualized_turnover_pct"]),
            "annualized_turnover_on_initial_pct": float(
                result["annualized_turnover_on_initial_pct"]
            ),
            "stale_valuation_events": int(result["stale_valuation_events"]),
            "profitable_years": profitable_years,
            "years_beating_vnindex": years_beating,
            **gates,
            "exploratory_candidate_passed": all(gates.values()),
            "point_in_time_universe_verified": False,
            "prospective_validation_passed": False,
            "research_gate_passed": False,
        })
        equity_output[f"{case_id}_equity"] = equity["equity"].to_numpy()
        orders = result["orders"].copy()
        orders.insert(0, "case_id", case_id)
        order_frames.append(orders)
        stale = result["stale_events"].copy()
        stale.insert(0, "case_id", case_id)
        stale_frames.append(stale)
        selected = result["snapshots"]
        if not selected.empty:
            selected = selected.loc[selected["selected"]].copy()
            selected.insert(0, "case_id", case_id)
            selection_frames.append(selected)

    summary = pd.DataFrame(summary_rows)
    summary["vnindex_return_pct"] = benchmark_return
    summary["vnindex_cagr_pct"] = benchmark_cagr
    summary["vnindex_max_drawdown_pct"] = price_drawdown_pct(
        benchmark_period["close"]
    )
    output = Path(
        args.output
        if args.output is not None
        else (
            BREADTH_HYSTERESIS_OUTPUT_DIR
            if args.matrix == "breadth_hysteresis"
            else (
                BREADTH_OUTPUT_DIR
                if args.matrix == "breadth"
                else (
                    TOP5_OUTPUT_DIR
                    if args.matrix == "top5"
                    else DEFAULT_OUTPUT_DIR
                )
            )
        )
    )
    output.mkdir(parents=True, exist_ok=True)
    summary.to_csv(output / "component_ablation_summary.csv", index=False, encoding="utf-8-sig")
    equity_output.to_csv(output / "component_ablation_equity.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(yearly_output).to_csv(
        output / "component_ablation_yearly.csv", index=False, encoding="utf-8-sig"
    )
    pd.concat(order_frames, ignore_index=True).to_csv(
        output / "component_ablation_orders.csv", index=False, encoding="utf-8-sig"
    )
    pd.concat(stale_frames, ignore_index=True).to_csv(
        output / "component_ablation_stale_events.csv", index=False, encoding="utf-8-sig"
    )
    pd.concat(selection_frames, ignore_index=True).to_csv(
        output / "component_ablation_selections.csv", index=False, encoding="utf-8-sig"
    )
    print("\n" + summary.to_string(index=False))
    print(f"\nSaved component ablation outputs to: {output}")


if __name__ == "__main__":
    main()
