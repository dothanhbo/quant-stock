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
from backtesting.walk_forward import WalkForwardConfig, run_walk_forward
from execution.signal_executor import PaperExecutionConfig
from research.run_entry_ablation import compound_return
from research.universes import HOLDOUT20_SYMBOLS
from strategy.donchian_breakout_entry import (
    DonchianBreakoutEntryModel,
    REGIME_THRESHOLD_FIELDS,
)
from strategy.hybrid_trend_donchian_entry import (
    HybridTrendDonchianEntryModel,
)


DEFAULT_OUTPUT_DIR = Path(
    "research_results/regime_threshold_ablation_holdout20"
)


@dataclass(frozen=True, slots=True)
class RegimeThresholdCase:
    case_id: str
    threshold_fields: frozenset[str]


def build_cases() -> list[RegimeThresholdCase]:
    single_cases = [
        RegimeThresholdCase(
            case_id=f"only__{field}",
            threshold_fields=frozenset({field}),
        )
        for field in sorted(REGIME_THRESHOLD_FIELDS)
    ]
    return [
        RegimeThresholdCase(
            case_id="legacy_static__baseline",
            threshold_fields=frozenset(),
        ),
        *single_cases,
        RegimeThresholdCase(
            case_id="all_regime_thresholds__current",
            threshold_fields=REGIME_THRESHOLD_FIELDS,
        ),
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "One-factor-at-a-time ablation of the five Donchian "
            "regime-aware thresholds."
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
        help="Optional case_id list. Omit to run all seven cases.",
    )
    return parser


def build_entry_model(case: RegimeThresholdCase):
    use_regime_thresholds = bool(case.threshold_fields)
    donchian = DonchianBreakoutEntryModel(
        use_regime_thresholds=use_regime_thresholds,
        regime_threshold_fields=(
            case.threshold_fields
            if use_regime_thresholds
            else None
        ),
    )
    return HybridTrendDonchianEntryModel(
        mode="trend_context",
        donchian_model=donchian,
        use_regime_thresholds=use_regime_thresholds,
        require_hybrid_score=True,
    )


def build_case_kwargs(
    *,
    case: RegimeThresholdCase,
    paper: PaperExecutionConfig,
    parity: BacktestPaperParityConfig,
    symbols: list[str],
    hold: int,
) -> dict:
    return {
        "symbols": symbols,
        "max_holding_days": hold,
        "entry_model": build_entry_model(case),
        "exit_model": CurrentScannerExitModel(),
        "ranking_method": "signal_score",
        "position_sizer": parity.build_position_sizer(),
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
        print(
            f"REGIME THRESHOLD ABLATION {index}/{len(cases)}: "
            f"{case.case_id}"
        )
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
        summary_rows.append({
            "case_id": case.case_id,
            "threshold_fields": ",".join(sorted(case.threshold_fields)),
            "return_excluding_first_fold_pct": compound_return(
                fold_returns.iloc[1:]
            ),
            "recent_3_folds_return_pct": compound_return(
                fold_returns.iloc[-3:]
            ),
            **result.summary,
        })
        folds = result.folds.copy()
        folds.insert(
            0,
            "threshold_fields",
            ",".join(sorted(case.threshold_fields)),
        )
        folds.insert(0, "case_id", case.case_id)
        fold_frames.append(folds)

        pd.DataFrame(summary_rows).to_csv(
            output_dir / "regime_threshold_summary_partial.csv",
            index=False,
            encoding="utf-8-sig",
        )

    summary_df = pd.DataFrame(summary_rows)
    summary_df = summary_df.sort_values(
        by=[
            "median_test_return_pct",
            "recent_3_folds_return_pct",
            "return_excluding_first_fold_pct",
            "average_test_sharpe",
            "worst_test_return_pct",
        ],
        ascending=[False, False, False, False, False],
    ).reset_index(drop=True)
    summary_df.insert(0, "robust_rank", range(1, len(summary_df) + 1))
    folds_df = pd.concat(fold_frames, ignore_index=True)

    summary_path = output_dir / "regime_threshold_summary.csv"
    folds_path = output_dir / "regime_threshold_folds.csv"
    summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig")
    folds_df.to_csv(folds_path, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 140)
    print("REGIME THRESHOLD ABLATION — ROBUSTNESS RANKING")
    print("=" * 140)
    columns = [
        "robust_rank",
        "case_id",
        "walk_forward_return_pct",
        "median_test_return_pct",
        "return_excluding_first_fold_pct",
        "recent_3_folds_return_pct",
        "average_test_sharpe",
        "worst_test_return_pct",
        "worst_test_drawdown_pct",
        "total_test_trades",
    ]
    print(summary_df[columns].to_string(index=False))
    print(f"\nSaved: {summary_path}")
    print(f"Saved: {folds_path}")


if __name__ == "__main__":
    main()
