from __future__ import annotations

"""Historical evaluation of the frozen production Q70 policy only."""

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Iterable, Literal

import pandas as pd

from backtesting.engine import (
    BacktestConfig,
    CandidateGenerationCollection,
    HistoricalEvaluationRow,
    _validate_coverage_index,
    calculate_metrics,
    calculate_portfolio_metrics,
    collect_candidate_generation,
)
from backtesting.exit_models import ATRExitModel
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.portfolio_simulator import PortfolioSimulator
from backtesting.trade import Trade
from config.strategy_config import Q70_FROZEN
from core.database_coverage import CoverageUniverseIndex, build_database_coverage_index
from core.historical_breadth import HistoricalBreadthIndex, build_historical_breadth_index
from core.universe import get_vn100_symbols
from execution.signal_executor import PaperExecutionConfig
from strategy.hybrid_trend_donchian_entry import HybridTrendDonchianEntryModel
from strategy.paper_v2_gate import PaperV2QualityGate, QUALITY_FEATURES


UniverseMode = Literal["current_vn100", "database_coverage"]
_Q70_THRESHOLD = 0.70
_ATR_STOP = 2.0
_ATR_TARGET = 5.0


def _normalize_symbols(symbols: Iterable[str]) -> list[str]:
    return sorted(
        {
            str(symbol).strip().upper()
            for symbol in symbols
            if str(symbol).strip() and str(symbol).strip().upper() != "VNINDEX"
        }
    )


def _key(symbol: str, signal_date: datetime) -> tuple[str, datetime]:
    return str(symbol).strip().upper(), pd.Timestamp(signal_date).to_pydatetime()


def _validate_frozen_policy() -> None:
    if (
        Q70_FROZEN.entry_model != "hybrid_trend_donchian"
        or not Q70_FROZEN.quality_enabled
        or Q70_FROZEN.quality_threshold != _Q70_THRESHOLD
        or Q70_FROZEN.stop_atr_multiplier != _ATR_STOP
        or Q70_FROZEN.target_atr_multiplier != _ATR_TARGET
    ):
        raise ValueError("Q70_FROZEN does not match the frozen evaluator policy")


def _frozen_paper_config(
    paper_execution_config: PaperExecutionConfig | None,
) -> PaperExecutionConfig:
    paper = paper_execution_config or PaperExecutionConfig()
    if (
        paper.atr_stop_multiplier != _ATR_STOP
        or paper.target_atr_multiplier != _ATR_TARGET
    ):
        raise ValueError("frozen Q70 evaluator requires ATR stop 2.0 and target 5.0")
    return paper


def _signal_from_evaluation(
    row: HistoricalEvaluationRow,
    breadth_fields: dict[str, float],
) -> dict[str, Any]:
    return {
        "symbol": str(row.symbol).strip().upper(),
        "date": pd.Timestamp(row.signal_date).date().isoformat(),
        "score": row.score,
        "relative_strength_20d": row.relative_strength_20d,
        "adx": row.adx,
        "regime": row.regime,
        **breadth_fields,
    }


def _coverage_counts(index: CoverageUniverseIndex | None) -> tuple[int | None, int | None, float | None]:
    if index is None:
        return None, None, None
    counts = tuple(index.eligible_count_by_session.values())
    return (
        min(counts, default=0),
        max(counts, default=0),
        float(sum(counts) / len(counts)) if counts else 0.0,
    )


def run_frozen_q70_backtest(
    symbols: Iterable[str] | None = None,
    *,
    db_path: str = "market.db",
    start_date: str,
    end_date: str,
    warmup_bars: int = 60,
    verbose: bool = False,
    universe_mode: UniverseMode = "current_vn100",
    minimum_history_sessions: int = 50,
    maximum_staleness_sessions: int = 5,
    coverage_index: CoverageUniverseIndex | None = None,
    breadth_index: HistoricalBreadthIndex | None = None,
    current_vn100_symbols: Iterable[str] | None = None,
    paper_execution_config: PaperExecutionConfig | None = None,
    parity_config: BacktestPaperParityConfig | None = None,
) -> tuple[list[Trade], dict[str, Any], pd.DataFrame]:
    """Evaluate the immutable Q70 policy using existing candidates and simulator.

    ``symbols`` takes precedence over the requested universe mode.  No entry,
    quality, exit, or risk parameter is exposed because this evaluator is for
    the frozen production policy rather than optimization.
    """
    _validate_frozen_policy()
    if universe_mode not in {"current_vn100", "database_coverage"}:
        raise ValueError("universe_mode is not supported")
    if minimum_history_sessions < 0 or maximum_staleness_sessions < 0:
        raise ValueError("coverage thresholds must be non-negative")

    paper = _frozen_paper_config(paper_execution_config)
    parity = parity_config or BacktestPaperParityConfig.from_paper_config(paper)
    if parity.atr_stop_multiplier != _ATR_STOP:
        raise ValueError("parity_config requires atr_stop_multiplier=2.0")

    explicit_symbols = symbols is not None
    active_coverage: CoverageUniverseIndex | None = None
    if explicit_symbols:
        selected_symbols = _normalize_symbols(symbols or ())
        effective_universe_mode = "explicit_symbols"
    elif universe_mode == "database_coverage":
        if coverage_index is None:
            active_coverage = build_database_coverage_index(
                start_date,
                end_date,
                minimum_history_sessions=minimum_history_sessions,
                maximum_staleness_sessions=maximum_staleness_sessions,
                database_path=db_path,
            )
        else:
            _validate_coverage_index(
                coverage_index,
                start_date=start_date,
                end_date=end_date,
                minimum_history_sessions=minimum_history_sessions,
                maximum_staleness_sessions=maximum_staleness_sessions,
            )
            active_coverage = coverage_index
        selected_symbols = list(active_coverage.candidate_symbols)
        effective_universe_mode = "database_coverage"
    else:
        selected_symbols = _normalize_symbols(
            current_vn100_symbols
            if current_vn100_symbols is not None
            else get_vn100_symbols()
        )
        effective_universe_mode = "legacy_current_vn100_retroactive"

    if breadth_index is None:
        breadth_index = build_historical_breadth_index(
            start_date,
            end_date,
            universe_mode=(
                "database_coverage" if active_coverage is not None else "current_vn100"
            ),
            symbols=(None if active_coverage is not None else selected_symbols),
            coverage_index=active_coverage,
            database_path=db_path,
        )
    elif breadth_index.start_date > str(pd.Timestamp(start_date).date()) or breadth_index.end_date < str(pd.Timestamp(end_date).date()):
        raise ValueError("breadth_index does not cover the requested date range")

    config = BacktestConfig(
        initial_capital=parity.initial_cash,
        position_size_pct=parity.fixed_fraction_pct,
        # ATR levels are owned by the fixed ATR exit model below.
        stop_loss_pct=5.0,
        take_profit_pct=10.0,
        min_adx=0.0,
    )
    entry_model = HybridTrendDonchianEntryModel()
    exit_model = ATRExitModel(
        stop_atr_multiplier=_ATR_STOP,
        target_atr_multiplier=_ATR_TARGET,
    )

    collections: list[CandidateGenerationCollection] = []
    for symbol in selected_symbols:
        collections.append(
            collect_candidate_generation(
                symbol=symbol,
                config=config,
                db_path=db_path,
                warmup_bars=warmup_bars,
                verbose=verbose,
                entry_model=entry_model,
                exit_model=exit_model,
                start_date=start_date,
                end_date=end_date,
            )
        )

    evaluations_by_date: dict[datetime, list[HistoricalEvaluationRow]] = defaultdict(list)
    candidates_by_key: dict[tuple[str, datetime], Trade] = {}
    for collection in collections:
        for row in collection.evaluations:
            evaluations_by_date[pd.Timestamp(row.signal_date).to_pydatetime()].append(row)
        for candidate in collection.candidates:
            if candidate.signal_date is None:
                continue
            candidate_key = _key(candidate.symbol, candidate.signal_date)
            if candidate_key in candidates_by_key:
                raise ValueError("candidate Trades must be unique by symbol and signal_date")
            candidates_by_key[candidate_key] = candidate

    # Sector RS is observational telemetry only: it is not a Q70 feature and
    # must not make historical candidate processing retrieve a live universe.
    # An explicit empty mapping makes the production gate record unavailable
    # telemetry without calling its Vnstock sector provider.  The immutable
    # selected universe is still supplied so no fallback can reach VN100.
    gate = PaperV2QualityGate(
        _Q70_THRESHOLD,
        sector_mapping={},
        sector_universe_symbols=tuple(selected_symbols),
    )
    accepted_candidates: list[Trade] = []
    rejection_counts: Counter[str] = Counter()
    rejection_state_counts: Counter[str] = Counter()
    total_evaluations = 0
    base_entry_candidates = 0

    for signal_date in sorted(evaluations_by_date):
        applicable_rows = evaluations_by_date[signal_date]
        if active_coverage is not None:
            applicable_rows = [
                row for row in applicable_rows
                if active_coverage.is_eligible(row.symbol, signal_date)
            ]
        total_evaluations += len(applicable_rows)
        fields = breadth_index.signal_fields_as_of(signal_date)
        quality_universe = [_signal_from_evaluation(row, fields) for row in applicable_rows]

        candidate_signals: list[dict[str, Any]] = []
        for row in applicable_rows:
            candidate = candidates_by_key.get(_key(row.symbol, row.signal_date))
            if not row.base_entry_passed or candidate is None:
                continue
            base_entry_candidates += 1
            candidate_signals.append(_signal_from_evaluation(row, fields))

        accepted, rejected = gate.apply(
            candidate_signals,
            quality_universe=quality_universe,
        )
        for rejected_signal in rejected:
            rejection_counts[str(rejected_signal.get("paper_v2_gate", "UNKNOWN"))] += 1
            rejection_state_counts[
                str(rejected_signal.get("paper_v2_state", "UNKNOWN"))
            ] += 1
        for accepted_signal in accepted:
            candidate = candidates_by_key[_key(
                str(accepted_signal["symbol"]), signal_date
            )]
            accepted_candidates.append(candidate)

    simulator = PortfolioSimulator(
        initial_cash=parity.initial_cash,
        position_size_pct=parity.fixed_fraction_pct,
        position_sizer=parity.build_position_sizer(),
        max_positions=parity.maximum_open_positions,
        lot_size=parity.lot_size,
        ranking_method="signal_score",
        transaction_cost_config=parity.transaction_cost_config(),
        max_new_positions_per_day=paper.maximum_orders_per_scan,
        maximum_gross_exposure_pct=parity.maximum_gross_exposure_pct,
        minimum_cash_buffer_pct=parity.minimum_cash_buffer_pct,
    )
    result = simulator.simulate(accepted_candidates)
    metrics = calculate_metrics(result.executed_trades, config)
    metrics.update(calculate_portfolio_metrics(result.equity_curve, final_equity=result.final_equity))
    coverage_min, coverage_max, coverage_mean = _coverage_counts(active_coverage)
    metrics.update(
        {
            "policy_fingerprint": "Q70_FROZEN/hybrid_trend_donchian/ATR2x5/fixed",
            "requested_universe_mode": universe_mode,
            "effective_universe_mode": effective_universe_mode,
            "q70_threshold": _Q70_THRESHOLD,
            "q70_features": QUALITY_FEATURES,
            "total_evaluation_rows": total_evaluations,
            "base_entry_candidates": base_entry_candidates,
            "q70_accepted_candidates": len(accepted_candidates),
            "q70_rejection_counts": dict(rejection_counts),
            "q70_rejection_state_counts": dict(rejection_state_counts),
            "coverage_eligible_count_min": coverage_min,
            "coverage_eligible_count_max": coverage_max,
            "coverage_eligible_count_mean": coverage_mean,
            "retrospective_current_vn100_warning": (
                effective_universe_mode == "legacy_current_vn100_retroactive"
            ),
            "entry_model": Q70_FROZEN.entry_model,
            "atr_stop_multiplier": _ATR_STOP,
            "atr_target_multiplier": _ATR_TARGET,
            "trailing_enabled": False,
            "break_even_enabled": False,
            "paper_execution": {
                "lot_size": parity.lot_size,
                "commission_rate": parity.commission_rate,
                "slippage_bps": parity.slippage_bps,
                "maximum_open_positions": parity.maximum_open_positions,
                "maximum_new_positions": paper.maximum_orders_per_scan,
                "maximum_gross_exposure_pct": parity.maximum_gross_exposure_pct,
                "minimum_cash_buffer_pct": parity.minimum_cash_buffer_pct,
                "maximum_daily_loss_pct": parity.maximum_daily_loss_pct,
            },
        }
    )
    return result.executed_trades, metrics, result.equity_curve
