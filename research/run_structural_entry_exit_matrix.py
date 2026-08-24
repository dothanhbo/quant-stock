from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv() -> bool:
        return False

from backtesting.current_logic import CurrentScannerExitModel
from backtesting.engine import run_backtest
from backtesting.exit_models import BaseExitModel
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.position_sizers import FixedFractionSizer
from backtesting.walk_forward import WalkForwardConfig, run_walk_forward
from execution.signal_executor import PaperExecutionConfig
from research.run_entry_ablation import compound_return
from research.universes import HOLDOUT20_SYMBOLS
from strategy.donchian_breakout_entry import DonchianBreakoutEntryModel
from strategy.hybrid_trend_donchian_entry import (
    HybridTrendDonchianEntryModel,
)


DEFAULT_OUTPUT_DIR = Path(
    "research_results/structural_entry_exit_matrix_holdout20"
)


class BreakEvenOverlayExitModel(BaseExitModel):
    """Keep scanner levels; raise next-session stop to entry after trigger."""

    def __init__(
        self,
        *,
        base_model: BaseExitModel | None = None,
        trigger_pct: float = 3.0,
    ) -> None:
        if trigger_pct <= 0:
            raise ValueError("trigger_pct phải lớn hơn 0")
        self.base_model = base_model or CurrentScannerExitModel()
        self.trigger_pct = float(trigger_pct)

    def calculate_levels(
        self,
        entry_price: float,
        entry_row: Any,
        config: Any,
    ) -> tuple[float, float]:
        return self.base_model.calculate_levels(
            entry_price=entry_price,
            entry_row=entry_row,
            config=config,
        )

    def update_levels(
        self,
        *,
        entry_price: float,
        current_row: Any,
        current_stop: float,
        current_target: float,
        highest_price: float,
        config: Any,
    ) -> tuple[float, float]:
        stop, target = self.base_model.update_levels(
            entry_price=entry_price,
            current_row=current_row,
            current_stop=current_stop,
            current_target=current_target,
            highest_price=highest_price,
            config=config,
        )
        trigger_price = entry_price * (1.0 + self.trigger_pct / 100.0)
        if highest_price >= trigger_price:
            stop = max(float(stop), entry_price)
        return float(stop), float(target)


@dataclass(frozen=True, slots=True)
class StructuralCase:
    case_id: str
    min_hybrid_score: int
    protective_exit: bool


def build_cases() -> list[StructuralCase]:
    return [
        StructuralCase(
            case_id=f"score_{score}__{exit_name}",
            min_hybrid_score=score,
            protective_exit=(exit_name == "break_even_3pct"),
        )
        for score in (60, 85)
        for exit_name in ("current", "break_even_3pct")
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "2x2 structural matrix: hybrid score gate 60/85 x "
            "current/break-even-3% exit. Fixed20 sizing."
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
    parser.add_argument("--sell-tax-rate", type=float, default=0.001)
    parser.add_argument("--break-even-trigger", type=float, default=3.0)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument(
        "--only",
        nargs="*",
        default=None,
        help="Optional case_id list. Omit to run all four cases.",
    )
    return parser


def build_entry_model(min_hybrid_score: int):
    return HybridTrendDonchianEntryModel(
        mode="trend_context",
        min_hybrid_score=min_hybrid_score,
        donchian_model=DonchianBreakoutEntryModel(
            use_regime_thresholds=True,
            regime_threshold_fields={"min_volume_ratio"},
        ),
        use_regime_thresholds=True,
        require_hybrid_score=True,
    )


def build_case_kwargs(
    *,
    case: StructuralCase,
    paper: PaperExecutionConfig,
    parity: BacktestPaperParityConfig,
    symbols: list[str],
    hold: int,
    break_even_trigger: float,
    db_path: str,
) -> dict:
    base_exit = CurrentScannerExitModel()
    exit_model = (
        BreakEvenOverlayExitModel(
            base_model=base_exit,
            trigger_pct=break_even_trigger,
        )
        if case.protective_exit
        else base_exit
    )
    return {
        "db_path": db_path,
        "symbols": symbols,
        "max_holding_days": hold,
        "entry_model": build_entry_model(case.min_hybrid_score),
        "exit_model": exit_model,
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
        "maximum_gross_exposure_pct": (
            parity.maximum_gross_exposure_pct
        ),
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
        "gate_recent_3_folds_non_negative": (
            float(summary["recent_3_folds_return_pct"]) >= 0.0
        ),
        "gate_drawdown_within_15pct": (
            float(summary["chained_max_drawdown_pct"]) >= -15.0
        ),
    }
    return {**gates, "research_gate_passed": all(gates.values())}


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
    all_cases = build_cases()
    case_map = {case.case_id: case for case in all_cases}
    if args.only:
        unknown = sorted(set(args.only) - set(case_map))
        if unknown:
            raise ValueError("Unknown case_id: " + ", ".join(unknown))
        cases = [case_map[value] for value in args.only]
    else:
        cases = all_cases

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_rows: list[dict] = []
    fold_frames: list[pd.DataFrame] = []

    for index, case in enumerate(cases, start=1):
        print("\n" + "#" * 96)
        print(f"STRUCTURAL CASE {index}/{len(cases)}: {case.case_id}")
        print("#" * 96)
        result = run_walk_forward(
            config=config,
            initial_capital=parity.initial_cash,
            run_backtest_fn=run_backtest,
            backtest_kwargs=build_case_kwargs(
                case=case,
                paper=paper,
                parity=parity,
                symbols=symbols,
                hold=args.hold,
                break_even_trigger=args.break_even_trigger,
                db_path=args.db,
            ),
        )
        case_dir = output_dir / case.case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        result.save(
            folds_path=str(case_dir / "folds.csv"),
            summary_path=str(case_dir / "summary.csv"),
        )

        ordered_folds = result.folds.sort_values("fold")
        returns = ordered_folds["test_return_pct"]
        row = {
            "case_id": case.case_id,
            "min_hybrid_score": case.min_hybrid_score,
            "protective_exit": case.protective_exit,
            "break_even_trigger_pct": (
                args.break_even_trigger if case.protective_exit else None
            ),
            "return_excluding_first_fold_pct": compound_return(
                returns.iloc[1:]
            ),
            "recent_3_folds_return_pct": compound_return(
                returns.iloc[-3:]
            ),
            **result.summary,
        }
        row.update(research_gates(row))
        summary_rows.append(row)

        folds = result.folds.copy()
        folds.insert(0, "protective_exit", case.protective_exit)
        folds.insert(0, "min_hybrid_score", case.min_hybrid_score)
        folds.insert(0, "case_id", case.case_id)
        fold_frames.append(folds)
        pd.DataFrame(summary_rows).to_csv(
            output_dir / "structural_matrix_summary_partial.csv",
            index=False,
            encoding="utf-8-sig",
        )

    summary_df = pd.DataFrame(summary_rows).sort_values(
        by=[
            "research_gate_passed",
            "median_test_return_pct",
            "recent_3_folds_return_pct",
            "return_excluding_first_fold_pct",
            "average_test_sharpe",
        ],
        ascending=[False, False, False, False, False],
    ).reset_index(drop=True)
    summary_df.insert(0, "robust_rank", range(1, len(summary_df) + 1))
    folds_df = pd.concat(fold_frames, ignore_index=True)
    summary_path = output_dir / "structural_matrix_summary.csv"
    folds_path = output_dir / "structural_matrix_folds.csv"
    summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig")
    folds_df.to_csv(folds_path, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 140)
    print("STRUCTURAL ENTRY/EXIT MATRIX")
    print("=" * 140)
    columns = [
        "robust_rank",
        "case_id",
        "walk_forward_return_pct",
        "profitable_folds",
        "median_test_return_pct",
        "return_excluding_first_fold_pct",
        "recent_3_folds_return_pct",
        "worst_test_drawdown_pct",
        "total_test_trades",
        "research_gate_passed",
    ]
    print(summary_df[columns].to_string(index=False))
    print(f"\nSaved: {summary_path}")
    print(f"Saved: {folds_path}")


if __name__ == "__main__":
    main()
