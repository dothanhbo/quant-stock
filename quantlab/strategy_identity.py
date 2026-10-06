from __future__ import annotations

"""Strategy Identity v3: pure builder (design: docs/audit/2026-10-06-strategy-identity-v3-design.md).

Strategy identity answers "which behavioral rules produced these decisions?".
It is deliberately separate from *run* identity (git commit, database paths
and hashes, data cutoff, store, cash, timestamps), which lives in run
provenance such as ``quantctl.runtime_configuration`` v2.

Everything in this module is pure: functions take explicit inputs, never read
``os.environ``, files, databases or providers, and import no strategy or
storage modules. Contracts are built from an explicit allowlist, so values
that are not named here (paths, cash, Telegram/provider secrets, ...) cannot
enter an identity.

Three SHA-256 fingerprints over ``quantlab.identity.canonical_json``:

* ``signal_identity``    – which signals are produced (eligibility, features,
                           regime, thresholds, entry model, quality gate,
                           V3 breadth exposure);
* ``execution_identity`` – how signals are traded (timing, levels, exits,
                           sizing, limits, regime caps, ranking, costs);
* ``strategy_identity_v3`` – the whole contract.

Shadow mode only (Phase 3B): nothing here enforces, rejects or pins anything.
Values are recorded *as constructed*. Owner-unresolved questions (Q1–Q6 of
the design) are recorded as observed values, never decided here.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from quantlab.identity import canonical_identity_value, canonical_json


CONTRACT = "quant.strategy_identity"
VERSION = "v3"
SIGNAL_CONTRACT = "quant.strategy_identity.signal"
EXECUTION_CONTRACT = "quant.strategy_identity.execution"

# Code literals that define behavior but are not exposed as parameters or
# module constants. They are *declared* here (transcribed from the code on
# 2026-10-06) rather than introspected. Limitation: editing one of these
# literals in code does not change an identity unless this block and its
# version are updated too; run provenance (git commit) still captures it.
DECLARED_CODE_CONSTANTS_VERSION = "2026-10-06.v1"
DECLARED_FEATURE_CONSTANTS: Mapping[str, Any] = MappingProxyType(
    {
        "source": "strategy/indicators.py",
        "ema_spans": (10, 20, 50),
        "ema_adjust": False,
        "rsi_period": 14,
        "atr_period": 14,
        "adx_period": 14,
        "wilder_smoothing": "ewm_alpha_1_over_period",
        "volume_ma_window": 20,
        "volume_breakout_window": 5,
        "donchian_high_window": 20,
        "donchian_excludes_current_bar": True,
        "return_3d_periods": 3,
    }
)
DECLARED_REGIME_CLASSIFIER: Mapping[str, Any] = MappingProxyType(
    {
        "source": "strategy/market_regime.py",
        "benchmark": "VNINDEX",
        "ema_fast": 50,
        "ema_slow": 200,
        "ema_slope_lag": 10,
        "return_window": 20,
        "bull_min_return_pct": -2.0,
        "min_sessions": 200,
        "labels": ("BULL", "SIDEWAY", "BEAR", "UNKNOWN"),
    }
)
DECLARED_GATE_STATE_RULES: Mapping[str, Any] = MappingProxyType(
    {
        "source": "strategy/paper_v2_gate.py:classify_state",
        "bear": "regime==BEAR",
        "divergent_bull": {"regime": "BULL", "breadth_lt": 50.0, "change_lt": 0.0},
        "healthy_bull": {"regime": "BULL", "breadth_ge": 70.0, "change_ge": 0.0},
        "recovery": {"regime": "SIDEWAY", "breadth_ge": 60.0, "change_gt": 0.0},
        "missing_breadth": "BULL->FRAGILE_BULL, other->NEUTRAL",
        "rejected_states": ("BEAR", "DIVERGENT_BULL"),
    }
)
DECLARED_MARKET_STATE: Mapping[str, Any] = MappingProxyType(
    {
        "source": "strategy/market_state.py",
        "breadth_measure": "pct_universe_close_above_ema50",
        "ema_span": 50,
        "change_window": 10,
    }
)
# Regime keys that the signal path consumes. ``rr_ratio`` and
# ``atr_stop_multiplier`` are deliberately excluded: production overwrites
# signal levels with ``TradingPolicy.calculate_levels`` (design class E).
SIGNAL_REGIME_THRESHOLD_KEYS = (
    "min_score",
    "min_adx",
    "min_volume_ratio",
    "min_relative_strength",
    "max_distance_ema20",
    "max_return_3d",
    "watchlist_margin",
)
REGIMES = ("BULL", "SIDEWAY", "BEAR", "UNKNOWN")
# Allowlisted executor fields. Class C values (database_path, initial_cash)
# and the operational ``enabled`` flag are intentionally absent.
EXECUTION_CONFIG_KEYS = (
    "position_sizer",
    "risk_per_trade_pct",
    "atr_stop_multiplier",
    "target_atr_multiplier",
    "fixed_fraction_pct",
    "maximum_orders_per_scan",
    "lot_size",
    "commission_rate",
    "slippage_bps",
    "maximum_position_pct",
    "maximum_gross_exposure_pct",
    "maximum_open_positions",
    "maximum_daily_loss_pct",
    "minimum_cash_buffer_pct",
    "sell_tax_rate",
    "maximum_order_adtv20_pct",
)
TRADING_POLICY_KEYS = (
    "entry_model",
    "exit_model",
    "execution_timing",
    "stop_atr_multiplier",
    "target_atr_multiplier",
    "trailing_atr_multiplier",
    "maximum_holding_days",
    "position_sizer",
    "risk_per_trade_pct",
    "fixed_fraction_pct",
    "sell_tax_rate",
)
LIFECYCLE_RISK_LIMIT_KEYS = (
    "maximum_position_pct",
    "maximum_gross_exposure_pct",
    "maximum_open_positions",
    "maximum_daily_loss_pct",
    "minimum_cash_buffer_pct",
)


class StrategyIdentityError(ValueError):
    """A contract value is missing, unsupported or non-finite."""


def _fingerprint(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise StrategyIdentityError(f"{name} must be numeric") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise StrategyIdentityError(f"{name} must be finite")
    return number


def _pick(source: Any, keys: Iterable[str], *, name: str) -> dict[str, Any]:
    """Read allowlisted attributes/keys; a missing key is an error, never a default."""
    picked: dict[str, Any] = {}
    for key in keys:
        if isinstance(source, Mapping):
            if key not in source:
                raise StrategyIdentityError(f"{name}.{key} is missing")
            value = source[key]
        else:
            if not hasattr(source, key):
                raise StrategyIdentityError(f"{name}.{key} is missing")
            value = getattr(source, key)
        if isinstance(value, float):
            value = _finite(value, f"{name}.{key}")
        picked[key] = value
    return picked


# --------------------------------------------------------------------------
# Normalizers (pure introspection of explicitly supplied objects)
# --------------------------------------------------------------------------


def describe_entry_model(model: Any) -> dict[str, Any]:
    """Normalize the constructed entry model by duck-typed public attributes.

    Supports the three production entry models without importing them. The
    model is described as constructed; nothing is enforced.
    """
    if model is None:
        raise StrategyIdentityError("entry model is missing")
    kind = type(model).__name__
    if kind == "HybridTrendDonchianEntryModel":
        return {
            "model": "hybrid_trend_donchian",
            "name": str(model.name),
            "mode": str(model.mode),
            "trend_weight": _finite(model.trend_weight, "trend_weight"),
            "donchian_weight": _finite(model.donchian_weight, "donchian_weight"),
            "min_hybrid_score": int(model.min_hybrid_score),
            "use_regime_thresholds": bool(model.use_regime_thresholds),
            "require_hybrid_score": bool(model.require_hybrid_score),
            "trend_model": describe_entry_model(model.trend_model),
            "donchian_model": describe_entry_model(model.donchian_model),
        }
    if kind == "DonchianBreakoutEntryModel":
        described = {"model": "donchian_breakout", "name": str(model.name)}
        described.update(
            _pick(
                model,
                (
                    "min_adx",
                    "min_volume_ratio",
                    "min_relative_strength",
                    "max_distance_ema20",
                    "max_return_3d",
                    "require_volume_breakout",
                    "use_adx",
                    "use_volume",
                    "use_relative_strength",
                    "use_ema_filter",
                    "use_distance_filter",
                    "use_overheated_filter",
                    "use_volume_breakout_score",
                    "use_regime_thresholds",
                ),
                name="donchian_model",
            )
        )
        described["regime_threshold_fields"] = tuple(sorted(model.regime_threshold_fields))
        return described
    if kind == "TrendStrategyV1":
        described = {"model": "trend_v1", "name": str(model.name)}
        described.update(
            _pick(
                model,
                ("use_trend_filter", "use_adx", "use_volume", "use_relative_strength"),
                name="trend_model",
            )
        )
        return described
    raise StrategyIdentityError(f"unsupported entry model: {kind}")


def describe_regime_thresholds(regime_configs: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Consumed regime thresholds only (normalized values, not YAML bytes)."""
    described: dict[str, Any] = {}
    for regime in REGIMES:
        if regime not in regime_configs:
            raise StrategyIdentityError(f"regime thresholds for {regime} are missing")
        values = _pick(
            regime_configs[regime], SIGNAL_REGIME_THRESHOLD_KEYS, name=f"regimes.{regime}"
        )
        described[regime] = {key: _finite(value, f"regimes.{regime}.{key}") for key, value in values.items()}
    return described


def describe_regime_portfolio_policy(policy: Any) -> dict[str, Any] | None:
    if policy is None:
        return None

    def rule(item: Any) -> dict[str, Any]:
        return {
            "allow_new_positions": bool(item.allow_new_positions),
            "max_positions": int(item.max_positions),
            "max_portfolio_heat_pct": (
                None
                if item.max_portfolio_heat_pct is None
                else _finite(item.max_portfolio_heat_pct, "max_portfolio_heat_pct")
            ),
        }

    return {
        "rules": {str(key): rule(value) for key, value in sorted(policy.rules.items())},
        "unknown_rule": rule(policy.unknown_rule),
    }


# --------------------------------------------------------------------------
# Contract builders
# --------------------------------------------------------------------------


def build_signal_contract(
    *,
    eligibility: Mapping[str, Any],
    entry_model: Any,
    regime_configs: Mapping[str, Mapping[str, Any]],
    rsi_min: Any,
    rsi_max: Any,
    quality_threshold: Any,
    quality_features: Iterable[str],
    quality_universe: str,
    market_state_min_history_sessions: Any,
    breadth_exposure: Mapping[str, Any] | None,
) -> dict[str, Any]:
    features = tuple(str(item) for item in quality_features)
    if not features:
        raise StrategyIdentityError("quality features are missing")
    return {
        "contract": SIGNAL_CONTRACT,
        "eligibility": dict(eligibility),
        "features": dict(DECLARED_FEATURE_CONSTANTS),
        "regime_classifier": dict(DECLARED_REGIME_CLASSIFIER),
        "regime_thresholds": describe_regime_thresholds(regime_configs),
        "entry": {
            "model": describe_entry_model(entry_model),
            "rsi_min": _finite(rsi_min, "rsi_min"),
            "rsi_max": _finite(rsi_max, "rsi_max"),
        },
        "quality_gate": {
            "features": features,
            "feature_weighting": "equal_mean",
            "percentile_method": "searchsorted_right_over_finite",
            "missing_feature_rank": 0.5,
            "universe": str(quality_universe),
            "threshold": _finite(quality_threshold, "quality_threshold"),
            "state_rules": dict(DECLARED_GATE_STATE_RULES),
        },
        "market_state": {
            **dict(DECLARED_MARKET_STATE),
            "min_history_sessions": int(market_state_min_history_sessions),
        },
        "breadth_exposure": None if breadth_exposure is None else dict(breadth_exposure),
        "declared_code_constants_version": DECLARED_CODE_CONSTANTS_VERSION,
    }


def build_execution_contract(
    *,
    signal_level_policy: Any,
    executor_config: Any,
    lifecycle_risk_limits: Mapping[str, Any],
    lifecycle_trailing_enabled: bool,
    regime_portfolio_policy: Any,
    ranking: Iterable[str],
    executor_fill_policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Execution contract as currently constructed (observed, not enforced).

    Records the scanner signal-level policy, the executor fill-level policy
    inputs and the lifecycle risk limits side by side, so a disagreement
    between them (e.g. design Q5, open-position limit 10 vs 5) is visible in
    the identity instead of being resolved here.
    """
    return {
        "contract": EXECUTION_CONTRACT,
        "signal_levels": _pick(signal_level_policy, TRADING_POLICY_KEYS, name="signal_level_policy"),
        "executor": _pick(executor_config, EXECUTION_CONFIG_KEYS, name="executor_config"),
        "executor_fill_policy": _pick(
            executor_fill_policy,
            (
                "stop_atr_multiplier",
                "target_atr_multiplier",
                "trailing_atr_multiplier",
                "maximum_holding_days",
            ),
            name="executor_fill_policy",
        ),
        "lifecycle": {
            "risk_limits": _pick(lifecycle_risk_limits, LIFECYCLE_RISK_LIMIT_KEYS, name="lifecycle_risk_limits"),
            "trailing_enabled": bool(lifecycle_trailing_enabled),
        },
        "regime_portfolio": describe_regime_portfolio_policy(regime_portfolio_policy),
        "ranking": tuple(str(item) for item in ranking),
    }


@dataclass(frozen=True, slots=True)
class StrategyIdentityV3:
    strategy: str
    signal_contract: Mapping[str, Any]
    execution_contract: Mapping[str, Any] | None
    signal_identity: str
    execution_identity: str | None
    strategy_identity_v3: str | None
    block_fingerprints: Mapping[str, str]

    def fingerprints(self) -> dict[str, str | None]:
        return {
            "strategy_identity_v3": self.strategy_identity_v3,
            "signal_identity": self.signal_identity,
            "execution_identity": self.execution_identity,
        }

    def as_dict(self) -> dict[str, Any]:
        return json.loads(
            canonical_json(
                canonical_identity_value(
                    {
                        "contract": CONTRACT,
                        "version": VERSION,
                        "strategy": self.strategy,
                        "signal": self.signal_contract,
                        "execution": self.execution_contract,
                        **self.fingerprints(),
                        "block_fingerprints": self.block_fingerprints,
                    }
                )
            ).decode("utf-8")
        )


def combined_strategy_identity(
    strategy: str,
    signal_identity: str,
    execution_identity: str,
) -> str:
    """``strategy_identity_v3`` from its parts (lets a stored record be checked)."""
    return _fingerprint(
        {
            "contract": CONTRACT,
            "version": VERSION,
            "strategy": str(strategy),
            "signal_identity": str(signal_identity),
            "execution_identity": str(execution_identity),
        }
    )


def build_strategy_identity(
    *,
    strategy: str,
    signal_contract: Mapping[str, Any],
    execution_contract: Mapping[str, Any] | None,
) -> StrategyIdentityV3:
    """Hash normalized contracts. ``execution_contract=None`` → signal only.

    A research run that only reproduces the signal path records
    ``signal_identity`` and leaves the execution/strategy identities empty
    instead of inventing an execution contract.
    """
    if not str(strategy).strip():
        raise StrategyIdentityError("strategy is required")
    signal = canonical_identity_value(dict(signal_contract))
    execution = None if execution_contract is None else canonical_identity_value(dict(execution_contract))
    signal_identity = _fingerprint(signal)
    execution_identity = None if execution is None else _fingerprint(execution)
    strategy_identity = (
        None
        if execution is None
        else combined_strategy_identity(str(strategy), signal_identity, execution_identity)
    )
    blocks = {f"signal.{key}": _fingerprint(value) for key, value in signal.items() if key != "contract"}
    if execution is not None:
        blocks.update(
            {f"execution.{key}": _fingerprint(value) for key, value in execution.items() if key != "contract"}
        )
    return StrategyIdentityV3(
        strategy=str(strategy),
        signal_contract=signal,
        execution_contract=execution,
        signal_identity=signal_identity,
        execution_identity=execution_identity,
        strategy_identity_v3=strategy_identity,
        block_fingerprints=blocks,
    )


__all__ = (
    "CONTRACT",
    "DECLARED_CODE_CONSTANTS_VERSION",
    "StrategyIdentityError",
    "StrategyIdentityV3",
    "VERSION",
    "build_execution_contract",
    "build_signal_contract",
    "build_strategy_identity",
    "combined_strategy_identity",
    "describe_entry_model",
    "describe_regime_portfolio_policy",
    "describe_regime_thresholds",
)
