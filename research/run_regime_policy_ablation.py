from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv() -> bool:
        return False

from backtesting.current_logic import CurrentScannerExitModel
from backtesting.engine import run_backtest
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.position_sizers import FixedFractionSizer
from backtesting.regime_policy import (
    RegimePortfolioPolicy,
    RegimePortfolioRule,
)
from backtesting.walk_forward import WalkForwardConfig, run_walk_forward
from execution.signal_executor import PaperExecutionConfig
from research.run_entry_ablation import compound_return
from research.universes import HOLDOUT20_SYMBOLS
from strategy.donchian_breakout_entry import DonchianBreakoutEntryModel
from strategy.hybrid_trend_donchian_entry import (
    HybridTrendDonchianEntryModel,
)


DEFAULT_OUTPUT_DIR = Path(
    "research_results/regime_policy_ablation_holdout20"
)


@dataclass(frozen=True, slots=True)
class RegimePolicyCase:
    case_id: str
    block_bear: bool
    sideway_max_positions: int | None


def build_cases() -> list[RegimePolicyCase]:
    return [
        RegimePolicyCase(
            case_id="baseline__no_portfolio_regime_policy",
            block_bear=False,
            sideway_max_positions=None,
        ),
        RegimePolicyCase(
            case_id="bear_block__full_sideway_exposure",
            block_bear=True,
            sideway_max_positions=5,
        ),
        RegimePolicyCase(
            case_id="bear_block__sideway_max_3",
            block_bear=True,
            sideway_max_positions=3,
        ),
    ]


def build_policy(case: RegimePolicyCase) -> RegimePortfolioPolicy | None:
    if not case.block_bear:
        return None
    sideway_positions = case.sideway_max_positions
    if sideway_positions is None or sideway_positions < 1:
        raise ValueError("sideway_max_positions phải từ 1 trở lên.")
    return RegimePortfolioPolicy(
        rules={
            "BULL": RegimePortfolioRule(
                allow_new_positions=True,
                max_positions=5,
                max_portfolio_heat_pct=None,
            ),
            "SIDEWAY": RegimePortfolioRule(
                allow_new_positions=True,
                max_positions=sideway_positions,
                max_portfolio_heat_pct=None,
            ),
            "BEAR": RegimePortfolioRule(
                allow_new_positions=False,
                max_positions=0,
                max_portfolio_heat_pct=None,
            ),
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Controlled regime-policy WFO. Entry, exit, ranking and sizing "
            "stay fixed; only portfolio permission/slots change by regime."
        )
    )
    parser.add_argument("--db", default="market.db")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--start", default="2018-08-07")
    parser.add_argument("--end", default="2026-08-21")
    parser.add_argument("--train-months", type=int, default=24)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=6)
    parser.add_argument("--hold", type=int, default=30)
    parser.add_argument("--score", type=int, default=60)
    parser.add_argument("--sell-tax-rate", type=float, default=0.001)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--only", nargs="*", default=None)
    return parser


def build_entry_model(score: int):
    return HybridTrendDonchianEntryModel(
        mode="trend_context",
        min_hybrid_score=score,
        donchian_model=DonchianBreakoutEntryModel(
            use_regime_thresholds=True,
            regime_threshold_fields={"min_volume_ratio"},
        ),
        use_regime_thresholds=True,
        require_hybrid_score=True,
    )


def build_backtest_kwargs(
    *,
    case: RegimePolicyCase,
    score: int,
    paper: PaperExecutionConfig,
    parity: BacktestPaperParityConfig,
    symbols: list[str],
    hold: int,
    db_path: str,
) -> dict:
    return {
        "db_path": db_path,
        "symbols": symbols,
        "max_holding_days": hold,
        "entry_model": build_entry_model(score),
        "exit_model": CurrentScannerExitModel(),
        "ranking_method": "signal_score",
        "position_sizer": FixedFractionSizer(position_size_pct=20.0),
        "regime_policy": build_policy(case),
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


def research_gates(summary: dict) -> dict:
    gates = {
        "gate_profitable_folds_at_least_half": (
            int(summary["profitable_folds"]) >= 6
        ),
        "gate_median_non_negative": (
            float(summary["median_test_return_pct"]) >= 0.0
        ),
        "gate_excluding_first_fold_non_negative": (
            float(summary["return_excluding_first_fold_pct"]) >= 0.0
        ),
        "gate_excluding_first_two_folds_non_negative": (
            float(summary["return_excluding_first_two_folds_pct"]) >= 0.0
        ),
        "gate_recent_3_folds_non_negative": (
            float(summary["recent_3_folds_return_pct"]) >= 0.0
        ),
        "gate_chained_drawdown_within_15pct": (
            float(summary["chained_max_drawdown_pct"]) >= -15.0
        ),
    }
    return {**gates, "research_gate_passed": all(gates.values())}


def main() -> None:
    load_dotenv()
    args = build_parser().parse_args()
    if not 0 <= args.score <= 100:
        raise ValueError("score phải nằm trong khoảng 0–100.")

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
    config = WalkForwardConfig(
        start_date=args.start,
        end_date=args.end,
        train_months=args.train_months,
        test_months=args.test_months,
        step_months=args.step_months,
    )

    all_cases = build_cases()
    case_map = {case.case_id: case for case in all_cases}
    if args.only:
        unknown = sorted(set(args.only) - set(case_map))
        if unknown:
            raise ValueError("Unknown case_id: " + ", ".join(unknown))
        cases = [case_map[case_id] for case_id in args.only]
    else:
        cases = all_cases

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_rows: list[dict] = []
    fold_frames: list[pd.DataFrame] = []

    for index, case in enumerate(cases, start=1):
        print("\n" + "#" * 96)
        print(f"REGIME POLICY {index}/{len(cases)}: {case.case_id}")
        print("#" * 96)
        result = run_walk_forward(
            config=config,
            initial_capital=parity.initial_cash,
            run_backtest_fn=run_backtest,
            backtest_kwargs=build_backtest_kwargs(
                case=case,
                score=args.score,
                paper=paper,
                parity=parity,
                symbols=symbols,
                hold=args.hold,
                db_path=args.db,
            ),
        )
        ordered = result.folds.sort_values("fold")
        returns = ordered["test_return_pct"]
        row = {
            "case_id": case.case_id,
            "block_bear": case.block_bear,
            "sideway_max_positions": case.sideway_max_positions,
            "min_hybrid_score": args.score,
            "return_excluding_first_fold_pct": compound_return(returns.iloc[1:]),
            "return_excluding_first_two_folds_pct": compound_return(returns.iloc[2:]),
            "recent_3_folds_return_pct": compound_return(returns.iloc[-3:]),
            **result.summary,
        }
        row.update(research_gates(row))
        summary_rows.append(row)

        folds = result.folds.copy()
        folds.insert(0, "sideway_max_positions", case.sideway_max_positions)
        folds.insert(0, "block_bear", case.block_bear)
        folds.insert(0, "case_id", case.case_id)
        fold_frames.append(folds)
        pd.DataFrame(summary_rows).to_csv(
            output_dir / "regime_policy_summary_partial.csv",
            index=False,
            encoding="utf-8-sig",
        )

    summary_df = pd.DataFrame(summary_rows).sort_values(
        by=[
            "research_gate_passed",
            "median_test_return_pct",
            "walk_forward_return_pct",
        ],
        ascending=[False, False, False],
    )
    summary_df.insert(0, "robust_rank", range(1, len(summary_df) + 1))
    folds_df = pd.concat(fold_frames, ignore_index=True)
    summary_path = output_dir / "regime_policy_summary.csv"
    folds_path = output_dir / "regime_policy_folds.csv"
    summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig")
    folds_df.to_csv(folds_path, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 140)
    print("REGIME POLICY ABLATION SUMMARY")
    print("=" * 140)
    print(summary_df.to_string(index=False))
    print(f"\nSaved: {summary_path}")
    print(f"Saved: {folds_path}")


if __name__ == "__main__":
    main()
