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
from backtesting.engine import build_exit_model, run_backtest
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
    "research_results/volume_candidate_matrix_holdout20"
)


@dataclass(frozen=True, slots=True)
class VolumeCandidateCase:
    case_id: str
    exit: str
    sizing: str


def build_cases() -> list[VolumeCandidateCase]:
    return [
        VolumeCandidateCase(
            case_id=f"volume_only__{exit_name}__{sizing}",
            exit=exit_name,
            sizing=sizing,
        )
        for exit_name in ("current", "frozen")
        for sizing in ("atr_risk", "fixed20")
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Final 2x2 exit/sizing matrix for the dynamic-volume-only "
            "hybrid entry candidate."
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
    parser.add_argument(
        "--only",
        nargs="*",
        default=None,
        help="Optional case_id list. Omit to run all four cases.",
    )
    return parser


def build_entry_model() -> HybridTrendDonchianEntryModel:
    return HybridTrendDonchianEntryModel(
        mode="trend_context",
        donchian_model=DonchianBreakoutEntryModel(
            use_regime_thresholds=True,
            regime_threshold_fields={"min_volume_ratio"},
        ),
        use_regime_thresholds=True,
        require_hybrid_score=True,
    )


def build_case_kwargs(
    *,
    case: VolumeCandidateCase,
    paper: PaperExecutionConfig,
    parity: BacktestPaperParityConfig,
    symbols: list[str],
    hold: int,
) -> dict:
    exit_model = (
        CurrentScannerExitModel()
        if case.exit == "current"
        else build_exit_model(
            name="atr",
            stop_atr_multiplier=2.0,
            target_atr_multiplier=5.0,
            break_even_trigger=5.0,
            trailing_atr_multiplier=2.0,
        )
    )
    position_sizer = (
        parity.build_position_sizer()
        if case.sizing == "atr_risk"
        else FixedFractionSizer(position_size_pct=20.0)
    )
    return {
        "symbols": symbols,
        "max_holding_days": hold,
        "entry_model": build_entry_model(),
        "exit_model": exit_model,
        "ranking_method": "signal_score",
        "position_sizer": position_sizer,
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
        "gate_median_near_zero": (
            float(summary["median_test_return_pct"]) >= -0.5
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
    return {
        **gates,
        "research_gate_passed": all(gates.values()),
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
        list(HOLDOUT20_SYMBOLS)
        if args.symbols is None
        else [
            value.strip().upper()
            for value in args.symbols
            if value.strip()
        ]
    )
    walk_forward_config = WalkForwardConfig(
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
            raise ValueError(
                "Unknown case_id: " + ", ".join(unknown)
            )
        cases = [case_map[value] for value in args.only]
    else:
        cases = all_cases

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_rows: list[dict] = []
    fold_frames: list[pd.DataFrame] = []

    for index, case in enumerate(cases, start=1):
        print("\n" + "#" * 96)
        print(f"VOLUME CANDIDATE {index}/{len(cases)}: {case.case_id}")
        print("#" * 96)

        result = run_walk_forward(
            config=walk_forward_config,
            initial_capital=parity.initial_cash,
            run_backtest_fn=run_backtest,
            backtest_kwargs=build_case_kwargs(
                case=case,
                paper=paper,
                parity=parity,
                symbols=symbols,
                hold=args.hold,
            ),
        )
        case_dir = output_dir / case.case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        result.save(
            folds_path=str(case_dir / "folds.csv"),
            summary_path=str(case_dir / "summary.csv"),
        )

        ordered_folds = result.folds.sort_values("fold")
        fold_returns = ordered_folds["test_return_pct"]
        row = {
            "case_id": case.case_id,
            "entry": "hybrid_dynamic_volume_only",
            "exit": case.exit,
            "sizing": case.sizing,
            "return_excluding_first_fold_pct": compound_return(
                fold_returns.iloc[1:]
            ),
            "recent_3_folds_return_pct": compound_return(
                fold_returns.iloc[-3:]
            ),
            **result.summary,
        }
        row.update(research_gates(row))
        summary_rows.append(row)

        folds = result.folds.copy()
        folds.insert(0, "sizing", case.sizing)
        folds.insert(0, "exit", case.exit)
        folds.insert(0, "entry", "hybrid_dynamic_volume_only")
        folds.insert(0, "case_id", case.case_id)
        fold_frames.append(folds)

        pd.DataFrame(summary_rows).to_csv(
            output_dir / "volume_candidate_summary_partial.csv",
            index=False,
            encoding="utf-8-sig",
        )

    summary_df = pd.DataFrame(summary_rows)
    summary_df = summary_df.sort_values(
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

    summary_path = output_dir / "volume_candidate_summary.csv"
    folds_path = output_dir / "volume_candidate_folds.csv"
    summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig")
    folds_df.to_csv(folds_path, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 140)
    print("VOLUME-ONLY CANDIDATE — FINAL MATRIX")
    print("=" * 140)
    columns = [
        "robust_rank",
        "case_id",
        "walk_forward_return_pct",
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
