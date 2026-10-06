from __future__ import annotations

"""Canonical Q70/V3 strategy contracts and fail-closed enforcement (B5-C, B6).

Owner decisions: docs/audit/2026-10-06-owner-contract-resolution.md.

* The canonical **signal** contract of each strategy is a reviewed snapshot,
  checked into ``config/strategy_contracts/canonical_signal_v1.json``.
* The canonical **execution** contract is the explicit owner-approved value
  list below (``CANONICAL_EXECUTION``). Only the listed paths are enforced.
* Values that are recorded but deliberately NOT enforced (ADTV cap, dormant
  lifecycle limits, dormant fixed-fraction values, trailing multiplier while
  trailing is off, ranking, regime caps overlay) are listed in
  ``RECORDED_NOT_ENFORCED`` so nothing is a hidden default.

Enforcement is one function, :func:`enforce_strategy_contract`, called at the
two strategy-runtime mutation boundaries: the canonical scan
(``app.strategy_scan.run_strategy_scan``, before ``run_scan``) and the paper
lifecycle (``scripts.run_paper_lifecycle.main``, before the first paper-store
write). It never edits configuration; drift fails closed with the exact
field paths.

Limitation (unchanged from B1): behavior-defining code literals are declared
constants inside the signal contract; editing such a literal without updating
the declared block is not detected here.
"""

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from quantlab.identity import canonical_identity_value, canonical_json
from quantlab.strategy_identity import StrategyIdentityV3, describe_regime_portfolio_policy


CONTRACT_VERSION = "canonical-2026-10-06.v1"
CANONICAL_SIGNAL_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "strategy_contracts"
    / "canonical_signal_v1.json"
)

STRATEGY_IDENTITY_V3_RECORDED = "STRATEGY_IDENTITY_V3_RECORDED"
CONTRACT_MATCHED = "CONTRACT_MATCHED"
CONTRACT_DEVIATION = "CONTRACT_DEVIATION"
REGIME_CAPS_V1 = "REGIME_CAPS_V1"
UNNAMED_REGIME_OVERLAY = "UNNAMED_REGIME_OVERLAY"
NO_REGIME_OVERLAY = "NO_REGIME_OVERLAY"

# Owner-approved execution contract (identical for Q70_FROZEN and
# V3_BREADTH_40_60). Paths address StrategyIdentityV3.execution_contract.
CANONICAL_EXECUTION: Mapping[str, Any] = {
    # Scanner signal-level policy (TradingPolicy as constructed by the scanner).
    "signal_levels.entry_model": "hybrid",
    "signal_levels.exit_model": "atr",
    "signal_levels.execution_timing": "next_open",
    "signal_levels.stop_atr_multiplier": 2.0,
    "signal_levels.target_atr_multiplier": 5.0,
    "signal_levels.maximum_holding_days": 20,
    "signal_levels.position_sizer": "atr_risk",
    "signal_levels.risk_per_trade_pct": 1.0,
    "signal_levels.sell_tax_rate": 0.001,
    # Executor (fill, sizing, limits, costs).
    "executor.position_sizer": "atr_risk",
    "executor.risk_per_trade_pct": 1.0,
    "executor.atr_stop_multiplier": 2.0,
    "executor.target_atr_multiplier": 5.0,
    "executor.maximum_orders_per_scan": 3,
    "executor.lot_size": 100,
    "executor.commission_rate": 0.0015,
    "executor.slippage_bps": 5.0,
    "executor.maximum_position_pct": 20.0,
    "executor.maximum_gross_exposure_pct": 80.0,
    "executor.maximum_open_positions": 10,
    "executor.maximum_daily_loss_pct": 3.0,
    "executor.minimum_cash_buffer_pct": 5.0,
    "executor.sell_tax_rate": 0.001,
    # Executor fill-level policy stored on each new position.
    "executor_fill_policy.stop_atr_multiplier": 2.0,
    "executor_fill_policy.target_atr_multiplier": 5.0,
    "executor_fill_policy.maximum_holding_days": 20,
    # Lifecycle exits.
    "lifecycle.trailing_enabled": False,
}

RECORDED_NOT_ENFORCED: Mapping[str, str] = {
    "signal_levels.fixed_fraction_pct": "NOT_APPLICABLE while position_sizer=atr_risk",
    "signal_levels.trailing_atr_multiplier": "NOT_APPLICABLE while trailing is disabled",
    "executor.fixed_fraction_pct": "NOT_APPLICABLE while position_sizer=atr_risk",
    "executor.maximum_order_adtv20_pct": "CONFIGURABLE_BUT_RECORDED",
    "executor_fill_policy.trailing_atr_multiplier": "NOT_APPLICABLE while trailing is disabled",
    "lifecycle.risk_limits": "NOT_APPLICABLE: lifecycle submits sells only; limits are dormant",
    "regime_portfolio": "execution overlay, recorded by name (REGIME_CAPS_V1)",
    "ranking": "recorded; ranking behavior is not changed in this phase",
}


class StrategyContractError(RuntimeError):
    """The runtime strategy contract could not be verified (fail closed)."""


class StrategyContractMismatch(StrategyContractError):
    """The runtime strategy contract deviates from the canonical contract."""

    def __init__(self, comparison: "ContractComparison") -> None:
        self.comparison = comparison
        details = "; ".join(
            f"{item.path}: expected {item.expected!r}, got {item.actual!r}"
            for item in comparison.deviations[:12]
        )
        more = len(comparison.deviations) - 12
        super().__init__(
            f"{comparison.strategy} runtime deviates from canonical contract "
            f"{comparison.contract_version} ({len(comparison.deviations)} field(s)): "
            f"{details}{f'; … {more} more' if more > 0 else ''}. "
            "No signal, paper or Telegram side effect was performed. Fix the "
            "configuration (e.g. .env) rather than the contract."
        )


@dataclass(frozen=True, slots=True)
class Deviation:
    path: str
    expected: Any
    actual: Any

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "expected": self.expected, "actual": self.actual}


@dataclass(frozen=True, slots=True)
class ContractComparison:
    strategy: str
    contract_version: str
    status: str
    deviations: tuple[Deviation, ...]
    regime_overlay: str

    @property
    def deviation_paths(self) -> tuple[str, ...]:
        return tuple(item.path for item in self.deviations)

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "contract_version": self.contract_version,
            "status": self.status,
            "deviations": [item.as_dict() for item in self.deviations],
            "regime_overlay": self.regime_overlay,
        }


@lru_cache(maxsize=1)
def _canonical_signals() -> Mapping[str, Any]:
    payload = json.loads(CANONICAL_SIGNAL_PATH.read_text(encoding="utf-8"))
    if payload.get("contract_version") != CONTRACT_VERSION:
        raise StrategyContractError(
            f"canonical signal file version {payload.get('contract_version')!r} "
            f"does not match {CONTRACT_VERSION!r}"
        )
    return payload["strategies"]


def canonical_signal_contract(strategy: str) -> dict[str, Any]:
    strategies = _canonical_signals()
    if strategy not in strategies:
        raise StrategyContractError(f"no canonical contract for strategy {strategy!r}")
    return json.loads(canonical_json(strategies[strategy]).decode("utf-8"))


def canonical_signal_identity(strategy: str, contract_version: str) -> str | None:
    """Canonical ``signal_identity`` for a strategy, or None if the contract
    version is not this one (older/newer records are then not re-judged)."""
    if contract_version != CONTRACT_VERSION:
        return None
    from quantlab.strategy_identity import build_strategy_identity

    return build_strategy_identity(
        strategy=strategy,
        signal_contract=canonical_signal_contract(strategy),
        execution_contract=None,
    ).signal_identity


def _normalized(value: Any) -> Any:
    return json.loads(canonical_json(canonical_identity_value(value)).decode("utf-8"))


def _diff(expected: Any, actual: Any, path: str) -> list[Deviation]:
    if isinstance(expected, dict) and isinstance(actual, dict):
        deviations: list[Deviation] = []
        for key in sorted(set(expected) | set(actual)):
            child = f"{path}.{key}" if path else key
            if key not in expected:
                deviations.append(Deviation(child, None, actual[key]))
            elif key not in actual:
                deviations.append(Deviation(child, expected[key], None))
            else:
                deviations.extend(_diff(expected[key], actual[key], child))
        return deviations
    if _same(expected, actual):
        return []
    return [Deviation(path, expected, actual)]


def _same(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return isinstance(expected, bool) and isinstance(actual, bool) and expected == actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return float(expected) == float(actual)
    return expected == actual


def _lookup(contract: Mapping[str, Any], path: str) -> Any:
    value: Any = contract
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return _MISSING
        value = value[part]
    return value


_MISSING = "<missing>"


def regime_overlay_label(regime_portfolio: Any) -> str:
    if regime_portfolio is None:
        return NO_REGIME_OVERLAY
    from backtesting.regime_policy import RegimePortfolioPolicy

    if _normalized(regime_portfolio) == _normalized(
        describe_regime_portfolio_policy(RegimePortfolioPolicy())
    ):
        return REGIME_CAPS_V1
    return UNNAMED_REGIME_OVERLAY


def compare_to_canonical(identity: StrategyIdentityV3) -> ContractComparison:
    """Field-level comparison of an identity with the canonical contract."""
    if identity.execution_contract is None:
        raise StrategyContractError("execution contract is required for contract comparison")
    signal = _normalized(identity.signal_contract)
    execution = _normalized(identity.execution_contract)
    deviations = _diff(canonical_signal_contract(identity.strategy), signal, "signal")
    for path, expected in CANONICAL_EXECUTION.items():
        actual = _lookup(execution, path)
        if not _same(expected, actual):
            deviations.append(Deviation(f"execution.{path}", expected, actual))
    return ContractComparison(
        strategy=identity.strategy,
        contract_version=CONTRACT_VERSION,
        status=CONTRACT_MATCHED if not deviations else CONTRACT_DEVIATION,
        deviations=tuple(deviations),
        regime_overlay=regime_overlay_label(execution.get("regime_portfolio")),
    )


def enforce_strategy_contract(
    strategy: str,
    *,
    collector: Callable[[], StrategyIdentityV3] | None = None,
) -> tuple[StrategyIdentityV3, ContractComparison]:
    """Collect the runtime identity and fail closed on any enforced deviation.

    Unlike B2 shadow mode, an identity that cannot be built is itself a
    failure: the contract cannot be verified, so nothing may run.
    """
    if collector is None:
        from quantlab.strategy_identity_runtime import collect_production_strategy_identity

        def collector() -> StrategyIdentityV3:
            return collect_production_strategy_identity(strategy)

    try:
        identity = collector()
    except StrategyContractError:
        raise
    except Exception as exc:
        raise StrategyContractError(
            f"{strategy}: strategy identity could not be verified "
            f"({type(exc).__name__}: {exc}); refusing to run."
        ) from exc
    if identity.strategy != strategy:
        raise StrategyContractError(
            f"identity was built for {identity.strategy}, not {strategy}"
        )
    comparison = compare_to_canonical(identity)
    prints = identity.fingerprints()
    print(
        "Strategy contract: "
        f"{strategy} {comparison.status} ({comparison.contract_version}) "
        f"strategy={str(prints['strategy_identity_v3'])[:12]} "
        f"signal={prints['signal_identity'][:12]} "
        f"execution={str(prints['execution_identity'])[:12]} "
        f"overlay={comparison.regime_overlay}"
    )
    if comparison.status != CONTRACT_MATCHED:
        raise StrategyContractMismatch(comparison)
    return identity, comparison


def rebuild_identity(identity: StrategyIdentityV3) -> StrategyIdentityV3:
    """Recompute every fingerprint from the identity's own contracts.

    A StrategyIdentityV3 carrying stale or edited fingerprints (fingerprints
    that its contracts do not produce) is rejected rather than trusted.
    """
    from quantlab.strategy_identity import build_strategy_identity

    if identity.execution_contract is None:
        raise StrategyContractError("execution contract is required")
    rebuilt = build_strategy_identity(
        strategy=identity.strategy,
        signal_contract=identity.signal_contract,
        execution_contract=identity.execution_contract,
    )
    if rebuilt.fingerprints() != identity.fingerprints():
        raise StrategyContractError(
            "identity fingerprints do not match its behavioral contracts (stale or edited)"
        )
    return rebuilt


def evidence_attestation_fields(identity: StrategyIdentityV3 | None) -> dict[str, Any]:
    """Additive fields for a NEW prospective evidence record (B5-C).

    Fingerprints are recomputed from the identity's contracts and the contract
    status is DERIVED here against the canonical contract; callers cannot
    supply a status, comparison or fingerprint of their own. The contracts are
    included so the evidence record can re-derive and verify all of it.
    """
    if identity is None:
        return {}
    rebuilt = rebuild_identity(identity)
    comparison = compare_to_canonical(rebuilt)
    return {
        **rebuilt.fingerprints(),
        "strategy_contract_version": comparison.contract_version,
        "strategy_contract_status": comparison.status,
        "strategy_contract_deviations": comparison.deviation_paths,
        "execution_overlay": comparison.regime_overlay,
        "strategy_signal_contract": _normalized(rebuilt.signal_contract),
        "strategy_execution_contract": _normalized(rebuilt.execution_contract),
    }


__all__ = (
    "CANONICAL_EXECUTION",
    "CONTRACT_DEVIATION",
    "CONTRACT_MATCHED",
    "CONTRACT_VERSION",
    "ContractComparison",
    "Deviation",
    "RECORDED_NOT_ENFORCED",
    "REGIME_CAPS_V1",
    "STRATEGY_IDENTITY_V3_RECORDED",
    "StrategyContractError",
    "StrategyContractMismatch",
    "canonical_signal_contract",
    "canonical_signal_identity",
    "compare_to_canonical",
    "enforce_strategy_contract",
    "evidence_attestation_fields",
    "rebuild_identity",
)
