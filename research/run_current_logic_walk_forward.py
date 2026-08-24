from __future__ import annotations

import argparse
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - project requirements normally provide it.
    def load_dotenv() -> bool:
        return False

from backtesting.current_logic import CurrentScannerExitModel
from backtesting.engine import build_exit_model, run_backtest
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.walk_forward import (
    WalkForwardConfig,
    print_walk_forward_report,
    run_walk_forward,
)
from execution.signal_executor import PaperExecutionConfig
from research.universes import HOLDOUT20_SYMBOLS
from strategy.trend_strategy_v1 import TrendStrategyV1
from config.trading_policy import TradingPolicy


DEFAULT_OUTPUT_DIR = Path(
    "research_results/current_logic_walk_forward"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Walk-forward using the current daily scanner "
            "and paper-execution configuration."
        )
    )
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
    policy = TradingPolicy.from_env()
    parity = BacktestPaperParityConfig.from_paper_config(
        paper,
        sell_tax_rate=args.sell_tax_rate,
    )
    symbols = (
        list(HOLDOUT20_SYMBOLS)
        if args.symbols is None
        else [
            value.strip().upper()
            for value in args.symbols
            if value.strip()
        ]
    )

    config = WalkForwardConfig(
        start_date=args.start,
        end_date=args.end,
        train_months=args.train_months,
        test_months=args.test_months,
        step_months=args.step_months,
    )

    backtest_kwargs = {
        "symbols": symbols,
        "max_holding_days": policy.maximum_holding_days,
        "entry_model": policy.build_entry_model(),
        "exit_model": build_exit_model(
            name="atr",
            stop_atr_multiplier=policy.stop_atr_multiplier,
            target_atr_multiplier=policy.target_atr_multiplier,
            break_even_trigger=policy.target_atr_multiplier,
            trailing_atr_multiplier=policy.stop_atr_multiplier,
        ),
        "ranking_method": "signal_score",
        "position_sizer": parity.build_position_sizer(),
        "buy_commission_pct": parity.commission_pct,
        "sell_commission_pct": parity.commission_pct,
        "sell_tax_pct": parity.sell_tax_pct,
        "buy_slippage_pct": parity.slippage_pct,
        "sell_slippage_pct": parity.slippage_pct,
        "max_positions": parity.maximum_open_positions,
        "lot_size": parity.lot_size,
        "max_new_positions_per_day": (
            paper.maximum_orders_per_scan
        ),
        "maximum_gross_exposure_pct": (
            parity.maximum_gross_exposure_pct
        ),
        "minimum_cash_buffer_pct": (
            parity.minimum_cash_buffer_pct
        ),
    }

    result = run_walk_forward(
        config=config,
        initial_capital=parity.initial_cash,
        run_backtest_fn=run_backtest,
        backtest_kwargs=backtest_kwargs,
    )
    print_walk_forward_report(result)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    folds_path = output_dir / "folds.csv"
    summary_path = output_dir / "summary.csv"
    result.save(
        folds_path=str(folds_path),
        summary_path=str(summary_path),
    )

    print(f"Đã xuất: {folds_path}")
    print(f"Đã xuất: {summary_path}")


if __name__ == "__main__":
    main()
