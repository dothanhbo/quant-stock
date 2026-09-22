from __future__ import annotations

"""Historical evaluation of the frozen production Q70 policy only."""

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime
import math
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


@dataclass(frozen=True, slots=True)
class FrozenQ70Decision:
    """Immutable audit record for one base-entry candidate's real gate path."""

    phase: str
    symbol: str
    signal_date: datetime
    candidate_key: str
    score: Any
    relative_strength_20d: Any
    adx: Any
    percentile_score: float | None
    percentile_relative_strength_20d: float | None
    percentile_adx: float | None
    quality_score: float
    quality_threshold: float
    market_state: str
    breadth_ema50_pct: Any
    breadth_ema50_change_10d: Any
    gate_accepted: bool
    gate_reason: str
    execution_disposition: str
    executed_trade_key: str | None


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


def _candidate_key(symbol: str, signal_date: datetime) -> str:
    normalized_symbol, normalized_date = _key(symbol, signal_date)
    return f"{normalized_symbol}|{normalized_date.isoformat()}"


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
    collect_decision_ledger: bool = False,
    decision_phase: str = "unspecified",
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
    gate_decisions: dict[str, FrozenQ70Decision] = {}
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

        def observe_gate_decision(
            signal: dict[str, Any],
            decision: Any,
            components: dict[str, float],
        ) -> None:
            if not collect_decision_ledger:
                return
            candidate_key = _candidate_key(str(signal["symbol"]), signal_date)
            if candidate_key in gate_decisions:
                raise ValueError("duplicate frozen Q70 decision candidate key")
            gate_decisions[candidate_key] = FrozenQ70Decision(
                phase=decision_phase,
                symbol=str(signal["symbol"]).strip().upper(),
                signal_date=pd.Timestamp(signal_date).to_pydatetime(),
                candidate_key=candidate_key,
                score=signal.get("score"),
                relative_strength_20d=signal.get("relative_strength_20d"),
                adx=signal.get("adx"),
                percentile_score=components.get("score"),
                percentile_relative_strength_20d=components.get("relative_strength_20d"),
                percentile_adx=components.get("adx"),
                quality_score=float(signal["paper_v2_quality"]),
                quality_threshold=_Q70_THRESHOLD,
                market_state=str(signal["paper_v2_state"]),
                breadth_ema50_pct=signal.get("breadth_ema50_pct"),
                breadth_ema50_change_10d=signal.get("breadth_ema50_change_10d"),
                gate_accepted=bool(decision.accepted),
                gate_reason=str(signal["paper_v2_gate"]),
                execution_disposition=("pending_simulation" if decision.accepted else "not_gate_accepted"),
                executed_trade_key=None,
            )

        gate_kwargs: dict[str, Any] = {"quality_universe": quality_universe}
        if collect_decision_ledger:
            gate_kwargs["decision_observer"] = observe_gate_decision
        accepted, rejected = gate.apply(candidate_signals, **gate_kwargs)
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
    if collect_decision_ledger:
        rejected_by_key = {
            _candidate_key(rejected.trade.symbol, rejected.trade.signal_date): rejected.reason
            for rejected in getattr(result, "rejected_trades", ())
            if rejected.trade.signal_date is not None
        }
        executed_by_key = {
            _candidate_key(trade.symbol, trade.signal_date): trade
            for trade in result.executed_trades
            if trade.signal_date is not None
        }
        for candidate_key, decision in tuple(gate_decisions.items()):
            if not decision.gate_accepted:
                continue
            if candidate_key in executed_by_key:
                gate_decisions[candidate_key] = replace(
                    decision,
                    execution_disposition="executed",
                    executed_trade_key=candidate_key,
                )
            elif candidate_key in rejected_by_key:
                gate_decisions[candidate_key] = replace(
                    decision,
                    execution_disposition=str(rejected_by_key[candidate_key]),
                )
            else:
                gate_decisions[candidate_key] = replace(
                    decision,
                    execution_disposition="reason_unavailable",
                )
    metrics = calculate_metrics(result.executed_trades, config)
    metrics.update(calculate_portfolio_metrics(result.equity_curve, final_equity=result.final_equity))
    # ``calculate_portfolio_metrics`` measures from the first recorded equity
    # event.  Frozen-WFO folds must instead measure from this invocation's
    # supplied, potentially chained capital.
    metrics["total_return_pct"] = (
        result.final_equity / parity.initial_cash - 1.0
    ) * 100.0
    gross_profits = [
        trade.net_pnl for trade in result.executed_trades if trade.net_pnl > 0
    ]
    gross_losses = [
        trade.net_pnl for trade in result.executed_trades if trade.net_pnl < 0
    ]
    gross_profit_amount = float(sum(gross_profits))
    gross_loss_amount = float(abs(sum(gross_losses)))
    profit_factor_amount = (
        gross_profit_amount / gross_loss_amount
        if gross_loss_amount > 0
        else (math.inf if gross_profit_amount > 0 else 0.0)
    )
    metrics.update(
        {
            "gross_profit": gross_profit_amount,
            "gross_loss": gross_loss_amount,
            "profit_factor": float(profit_factor_amount),
            "gross_trading_pnl": float(
                sum(trade.gross_pnl for trade in result.executed_trades)
            ),
            "net_trading_pnl": float(
                sum(trade.net_pnl for trade in result.executed_trades)
            ),
            "total_buy_commission": float(
                sum(trade.buy_commission for trade in result.executed_trades)
            ),
            "total_sell_commission": float(
                sum(trade.sell_commission for trade in result.executed_trades)
            ),
            "total_sell_tax": float(
                sum(trade.sell_tax for trade in result.executed_trades)
            ),
            "total_transaction_cost": float(
                sum(trade.total_transaction_cost for trade in result.executed_trades)
            ),
        }
    )
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
    if collect_decision_ledger:
        metrics["decision_ledger"] = tuple(
            sorted(gate_decisions.values(), key=lambda row: (row.signal_date, row.symbol, row.candidate_key))
        )
    return result.executed_trades, metrics, result.equity_curve
