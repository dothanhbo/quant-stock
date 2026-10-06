from __future__ import annotations

"""Collect Strategy Identity v3 from the runtime as constructed (shadow only).

This module reads the live objects and the current process environment the
same way the production code does (``TradingPolicy.from_env``,
``PaperExecutionConfig.from_env``, the lifecycle's literal-``"true"`` trailing
flag, ...) and hands explicit values to the pure builder in
``quantlab.strategy_identity``. It never writes ``os.environ``, never opens a
database and never raises into the caller through :func:`shadow_strategy_identity`.

Nothing here enforces a value. In particular the Q70 entry model is recorded
exactly as constructed (``hybrid`` or ``trend``); pinning it is an owner
decision (design §14 Q1) and out of scope.
"""

from dataclasses import dataclass
from dataclasses import replace
import os
from typing import Any, Callable, Mapping

from quantlab.strategy_identity import (
    StrategyIdentityV3,
    build_execution_contract,
    build_signal_contract,
    build_strategy_identity,
)


Q70 = "Q70_FROZEN"
V3 = "V3_BREADTH_40_60"
# Observed production scanner ordering (strategy/scanner.py sort_key, desc).
PRODUCTION_SCAN_RANKING = ("score", "relative_strength_20d", "volume_ratio", "adx")
SAME_SESSION_EVALUATIONS = "same_session_evaluations"


@dataclass(frozen=True, slots=True)
class ShadowIdentityResult:
    """Outcome of an observational identity computation.

    ``status`` is ``COMPLETE`` or ``INCOMPLETE``. An incomplete result carries
    a diagnostic instead of an identity; it never alters the run.
    """

    status: str
    identity: StrategyIdentityV3 | None = None
    diagnostic: str | None = None

    def fingerprints(self) -> dict[str, str | None]:
        if self.identity is None:
            return {
                "strategy_identity_v3": None,
                "signal_identity": None,
                "execution_identity": None,
            }
        return self.identity.fingerprints()

    def summary(self) -> str:
        if self.identity is None:
            return f"Strategy identity v3 (shadow): INCOMPLETE — {self.diagnostic}"
        prints = self.identity.fingerprints()
        entry = self.identity.signal_contract["entry"]["model"]["model"]
        return (
            "Strategy identity v3 (shadow): "
            f"{self.identity.strategy} strategy={str(prints['strategy_identity_v3'])[:12]} "
            f"signal={prints['signal_identity'][:12]} "
            f"execution={str(prints['execution_identity'])[:12]} "
            f"entry={entry}"
        )


def _breadth_contract(strategy: str) -> dict[str, Any] | None:
    if strategy != V3:
        return None
    from strategy.breadth_exposure import (
        BREADTH_FULL_EXPOSURE_PCT,
        BREADTH_NEUTRAL_EXPOSURE_PCT,
        V3_BREADTH_VERSION,
        breadth_exposure_multiplier,
    )

    # Multipliers are observed from the policy function at its boundaries.
    return {
        "version": V3_BREADTH_VERSION,
        "measure": "breadth_ema50_pct",
        "full_exposure_min_pct": float(BREADTH_FULL_EXPOSURE_PCT),
        "half_exposure_min_pct": float(BREADTH_NEUTRAL_EXPOSURE_PCT),
        "multiplier_at_full": breadth_exposure_multiplier(BREADTH_FULL_EXPOSURE_PCT),
        "multiplier_at_half": breadth_exposure_multiplier(BREADTH_NEUTRAL_EXPOSURE_PCT),
        "multiplier_below_half": breadth_exposure_multiplier(
            BREADTH_NEUTRAL_EXPOSURE_PCT - 1e-9
        ),
        "multiplier_missing": breadth_exposure_multiplier(None),
        "applied_to": "order_quantity",
    }


def _env_float(environ: Mapping[str, str], name: str, default: float) -> float:
    value = environ.get(name)
    return default if value is None else float(value)


def _env_int(environ: Mapping[str, str], name: str, default: int) -> int:
    value = environ.get(name)
    return default if value is None else int(value)


def _lifecycle_risk_limits(environ: Mapping[str, str]) -> dict[str, Any]:
    # Mirrors scripts/run_paper_lifecycle.py RiskLimits construction.
    return {
        "maximum_position_pct": _env_float(environ, "PAPER_MAX_POSITION_PCT", 20.0),
        "maximum_gross_exposure_pct": _env_float(environ, "PAPER_MAX_EXPOSURE_PCT", 80.0),
        "maximum_open_positions": _env_int(environ, "PAPER_MAX_OPEN_POSITIONS", 5),
        "maximum_daily_loss_pct": _env_float(environ, "PAPER_MAX_DAILY_LOSS_PCT", 3.0),
        "minimum_cash_buffer_pct": _env_float(environ, "PAPER_MIN_CASH_BUFFER_PCT", 5.0),
    }


def collect_production_strategy_identity(
    strategy: str,
    *,
    signal_policy: Any = None,
    entry_model: Any = None,
    quality_threshold: float | None = None,
) -> StrategyIdentityV3:
    """Build the identity of the production scan/paper runtime as constructed.

    ``signal_policy``/``entry_model`` should be the scanner's own objects when
    available (``strategy.scanner.TRADING_POLICY`` / ``.strategy``); otherwise
    they are constructed from the current environment exactly as the scanner
    would. Reads the environment; never writes it.
    """
    if strategy not in (Q70, V3):
        raise ValueError(f"unknown strategy identity: {strategy}")
    from backtesting.regime_policy import RegimePortfolioPolicy
    from config.strategy_loader import COMMON_CONFIG, REGIME_CONFIGS
    from config.trading_policy import TradingPolicy
    from execution.signal_executor import PaperExecutionConfig
    from strategy.market_state import MIN_HISTORY_SESSIONS
    from strategy.paper_v2_gate import QUALITY_FEATURES

    if quality_threshold is None:
        from app.strategy_scan import Q70_QUALITY_THRESHOLD

        quality_threshold = Q70_QUALITY_THRESHOLD
    policy = signal_policy if signal_policy is not None else TradingPolicy.from_env()
    model = entry_model if entry_model is not None else policy.build_entry_model()
    executor_config = PaperExecutionConfig.from_env()
    # The executor's fill-level policy (execution/signal_executor.py __init__).
    executor_policy = replace(
        TradingPolicy.from_env(),
        stop_atr_multiplier=executor_config.atr_stop_multiplier,
        target_atr_multiplier=executor_config.target_atr_multiplier,
    )
    environ = dict(os.environ)
    signal = build_signal_contract(
        eligibility={
            "path": "production_scanner",
            "universe": "scanner_vn100",
            "min_data_rows": int(COMMON_CONFIG["min_data_rows"]),
            "relative_strength_period": int(COMMON_CONFIG.get("rs_period", 20)),
            "relative_strength_benchmark": "VNINDEX",
        },
        entry_model=model,
        regime_configs=REGIME_CONFIGS,
        rsi_min=COMMON_CONFIG["rsi_min"],
        rsi_max=COMMON_CONFIG["rsi_max"],
        quality_threshold=quality_threshold,
        quality_features=QUALITY_FEATURES,
        quality_universe=SAME_SESSION_EVALUATIONS,
        market_state_min_history_sessions=MIN_HISTORY_SESSIONS,
        breadth_exposure=_breadth_contract(strategy),
    )
    execution = build_execution_contract(
        signal_level_policy=policy,
        executor_config=executor_config,
        lifecycle_risk_limits=_lifecycle_risk_limits(environ),
        # Same literal-"true" parser as scripts/run_paper_lifecycle.py.
        lifecycle_trailing_enabled=not (
            environ.get("PAPER_V2_DISABLE_TRAILING", "").lower() == "true"
        ),
        regime_portfolio_policy=RegimePortfolioPolicy(),
        ranking=PRODUCTION_SCAN_RANKING,
        executor_fill_policy=executor_policy,
    )
    return build_strategy_identity(
        strategy=strategy,
        signal_contract=signal,
        execution_contract=execution,
    )


def collect_frozen_research_signal_identity(
    *,
    entry_model: Any,
    quality_threshold: float,
    quality_features: Any,
    universe_mode: str,
    warmup_bars: int,
) -> StrategyIdentityV3:
    """Signal identity of the frozen Q70 research evaluator (signal only)."""
    from config.strategy_loader import COMMON_CONFIG, REGIME_CONFIGS
    from strategy.market_state import MIN_HISTORY_SESSIONS

    signal = build_signal_contract(
        eligibility={
            "path": "frozen_q70_research",
            "universe": str(universe_mode),
            "warmup_bars": int(warmup_bars),
        },
        entry_model=entry_model,
        regime_configs=REGIME_CONFIGS,
        rsi_min=COMMON_CONFIG["rsi_min"],
        rsi_max=COMMON_CONFIG["rsi_max"],
        quality_threshold=quality_threshold,
        quality_features=quality_features,
        quality_universe=SAME_SESSION_EVALUATIONS,
        market_state_min_history_sessions=MIN_HISTORY_SESSIONS,
        breadth_exposure=None,
    )
    return build_strategy_identity(strategy=Q70, signal_contract=signal, execution_contract=None)


def shadow_strategy_identity(
    collector: Callable[[], StrategyIdentityV3],
) -> ShadowIdentityResult:
    """Run ``collector`` observationally: any failure becomes a diagnostic."""
    try:
        return ShadowIdentityResult("COMPLETE", collector())
    except Exception as exc:  # shadow mode must never change the run
        return ShadowIdentityResult("INCOMPLETE", None, f"{type(exc).__name__}: {exc}")


__all__ = (
    "PRODUCTION_SCAN_RANKING",
    "ShadowIdentityResult",
    "collect_frozen_research_signal_identity",
    "collect_production_strategy_identity",
    "shadow_strategy_identity",
)
