from __future__ import annotations

"""Deterministic, secret-free snapshot of the effective operational config."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from core.paths import PROJECT_ROOT
from quantlab.identity import canonical_identity_value, canonical_json


CONTRACT = "quantctl.runtime_configuration"
VERSION = "v2"


def _canonical_path(path: str | Path) -> str:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return str(candidate.resolve())


def _bool_environment(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float_environment(name: str, default: float) -> float:
    value = os.getenv(name)
    return default if value is None else float(value)


def _int_environment(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None else int(value)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class RuntimeConfigurationSnapshot:
    payload: Mapping[str, Any]
    fingerprint: str

    def __post_init__(self) -> None:
        normalized = canonical_identity_value(self.payload)
        object.__setattr__(self, "payload", _freeze(normalized))
        expected = sha256(canonical_json(normalized)).hexdigest()
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("runtime configuration fingerprint does not match payload")
        object.__setattr__(self, "fingerprint", expected)

    @property
    def strategy_identity(self) -> str:
        return str(self.payload["strategy_identity"])

    @property
    def paper_store_id(self) -> str:
        return str(self.payload["paper_store"]["store_id"])

    def as_dict(self) -> dict[str, Any]:
        return json.loads(
            canonical_json(canonical_identity_value(self.payload)).decode("utf-8")
        )


def resolve_runtime_configuration() -> RuntimeConfigurationSnapshot:
    """Resolve operational configuration without secrets or state mutation."""
    from config.paper_store import effective_paper_strategy, resolve_active_paper_store
    from config.trading_policy import TradingPolicy
    from core.paths import resolve_market_database_path
    from execution.signal_executor import PaperExecutionConfig

    policy = TradingPolicy.from_env()
    execution = PaperExecutionConfig.from_env()
    store = resolve_active_paper_store()
    execution_payload = asdict(execution)
    execution_payload["database_path"] = _canonical_path(execution.database_path)
    # Preserve the exact operational parser used by run_paper_lifecycle.
    # Other lifecycle booleans use the broader helper, but this legacy flag
    # has historically treated only the literal value "true" as disabling.
    v2_disable_trailing = (
        os.getenv("PAPER_V2_DISABLE_TRAILING", "").lower() == "true"
    )
    payload: dict[str, Any] = {
        "contract": CONTRACT,
        "version": VERSION,
        "strategy_identity": effective_paper_strategy(),
        "market_database_path": _canonical_path(resolve_market_database_path()),
        "paper_store": {
            "store_id": store.store_id,
            "strategy_identity": store.strategy_identity,
            "database_path": _canonical_path(store.database_path),
            "override_variable": store.override_variable,
        },
        "trading_policy": asdict(policy),
        "paper_execution": execution_payload,
        # The lifecycle has a deliberately narrower default open-position
        # limit than the scanner's PaperExecutionConfig.  Keep both values in
        # the one canonical runtime fingerprint so an evidence record never
        # misattributes lifecycle behavior to scanner defaults.
        "paper_lifecycle": {
            "initial_cash": _float_environment("PAPER_INITIAL_CASH", 100_000_000),
            "commission_rate": _float_environment("PAPER_COMMISSION_RATE", 0.0015),
            "slippage_bps": _float_environment("PAPER_SLIPPAGE_BPS", 5.0),
            "sell_tax_rate": policy.sell_tax_rate,
            "risk_limits": {
                "maximum_position_pct": _float_environment("PAPER_MAX_POSITION_PCT", 20.0),
                "maximum_gross_exposure_pct": _float_environment("PAPER_MAX_EXPOSURE_PCT", 80.0),
                "maximum_open_positions": _int_environment("PAPER_MAX_OPEN_POSITIONS", 5),
                "maximum_daily_loss_pct": _float_environment("PAPER_MAX_DAILY_LOSS_PCT", 3.0),
                "minimum_cash_buffer_pct": _float_environment("PAPER_MIN_CASH_BUFFER_PCT", 5.0),
            },
            "exit": {
                "enable_trailing_stop": not v2_disable_trailing,
                "default_trailing_atr_multiplier": (
                    None if v2_disable_trailing else policy.trailing_atr_multiplier
                ),
            },
        },
        "lifecycle": {
            "paper_v2_disable_trailing": v2_disable_trailing,
            "paper_disable_trailing": _bool_environment("PAPER_DISABLE_TRAILING"),
            "paper_v2_enabled": _bool_environment("PAPER_V2_ENABLED"),
            "paper_v2_quality_threshold": os.getenv("PAPER_V2_QUALITY_THRESHOLD", "0.70"),
            "paper_v3_enabled": _bool_environment("PAPER_V3_ENABLED"),
            "paper_v3_quality_threshold": os.getenv("PAPER_V3_QUALITY_THRESHOLD", "0.70"),
        },
    }
    canonical = canonical_identity_value(payload)
    fingerprint = sha256(canonical_json(canonical)).hexdigest()
    return RuntimeConfigurationSnapshot(dict(canonical), fingerprint)


__all__ = (
    "CONTRACT",
    "VERSION",
    "RuntimeConfigurationSnapshot",
    "resolve_runtime_configuration",
)
