from __future__ import annotations

"""Read-only adapters from canonical persisted research outputs to evidence inputs.

The adapters deliberately preserve a source's limits.  A source without
portfolio snapshots is never converted into estimated portfolio weights, and a
source without completed trades never gains synthetic transaction costs.
"""

import csv
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

import pandas as pd

from core.paths import PROJECT_ROOT, resolve_market_database_path
from quantlab.catalog import build_market_data_snapshot
from quantlab.features import FeatureRegistry, FeatureRequest, builtin_definitions
from quantlab.identity import canonical_identity_value, canonical_json


CONTRACT = "quantlab.portfolio_risk_evidence_sources"
VERSION = "v1"
DEFAULT_PHASE6_ROOT = PROJECT_ROOT / "research_results/quantlab_portfolio_construction_2018-08-07_2026-09-17"
DEFAULT_FROZEN_Q70_ROOT = PROJECT_ROOT / "research_results/frozen_q70_paired_full_2018_2026_v4_instrumented"
DEFAULT_FROZEN_Q70_ARM = "database_coverage_50_history_5_staleness"
_HISTORICAL_MARKET_REGIME_REQUEST = FeatureRequest(
    "historical_market_regime",
    "v1",
    {"benchmark_symbol": "VNINDEX"},
)
_HISTORICAL_MARKET_REGIME_UNIVERSE_IDENTITY = "historical_market_reference:VNINDEX"


def _identity(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _finite(value: Any, *, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be numeric") from error
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite")
    return number


class PersistedEvidenceSourceClassification(str, Enum):
    CANONICAL = "CANONICAL"
    LEGACY = "LEGACY"
    DERIVED = "DERIVED"
    UNSUITABLE = "UNSUITABLE"


@dataclass(frozen=True, slots=True)
class PersistedPortfolioEvidenceSource:
    """One immutable, source-specific evaluator input bundle.

    ``observations`` is intentionally a tuple of independently materialized
    mappings.  The evidence evaluator consumes it without reading source files
    or a database again.
    """

    source_name: str
    classification: PersistedEvidenceSourceClassification
    strategy_identity: str
    source_identity: str
    start_date: str
    end_date: str
    observations: tuple[Mapping[str, Any], ...]
    provenance: Mapping[str, Any]
    limitations: tuple[str, ...]
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.source_name.strip() or not self.strategy_identity.strip() or not self.source_identity.strip():
            raise ValueError("source name, strategy identity, and source identity are required")
        if pd.Timestamp(self.start_date) > pd.Timestamp(self.end_date):
            raise ValueError("source start date cannot follow source end date")
        object.__setattr__(self, "observations", tuple(self.observations))
        object.__setattr__(self, "provenance", _freeze(self.provenance))
        object.__setattr__(self, "limitations", tuple(self.limitations))
        object.__setattr__(self, "identity", _identity({
            "contract": (CONTRACT, VERSION),
            "source_name": self.source_name,
            "classification": self.classification,
            "strategy_identity": self.strategy_identity,
            "source_identity": self.source_identity,
            "date_range": (self.start_date, self.end_date),
            "observation_count": len(self.observations),
            "provenance": self.provenance,
            "limitations": self.limitations,
        }))

    def evaluator_arguments(self) -> dict[str, Any]:
        """Return fresh DataFrames so a caller cannot mutate cached source data."""
        observations: list[dict[str, Any]] = []
        for stored in self.observations:
            item = dict(stored)
            item["weights"] = dict(item["weights"])
            item["daily_returns"] = item["daily_returns"].copy(deep=True)
            benchmark = item.get("benchmark_returns")
            if benchmark is not None:
                item["benchmark_returns"] = benchmark.copy(deep=True)
            item["trades"] = tuple(dict(trade) if isinstance(trade, Mapping) else trade for trade in item.get("trades", ()))
            item["provenance"] = dict(item["provenance"])
            observations.append(item)
        return {
            "source_name": self.source_name,
            "observations": tuple(observations),
            "source_status": (
                "EVALUABLE" if self.classification is PersistedEvidenceSourceClassification.CANONICAL
                else "PARTIALLY_EVALUABLE"
            ),
        }


def _load_json(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"required persisted artifact is missing: {path}")
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON artifact: {path}") from error
    if not isinstance(content, Mapping):
        raise ValueError(f"JSON artifact must contain an object: {path}")
    return content


def _csv_rows(path: Path) -> tuple[dict[str, str], ...]:
    if not path.is_file():
        raise FileNotFoundError(f"required persisted artifact is missing: {path}")
    with path.open(encoding="utf-8", newline="") as handle:
        return tuple(dict(row) for row in csv.DictReader(handle))


def _return_series(frame: pd.DataFrame, sessions: pd.DatetimeIndex) -> pd.Series:
    if frame.empty:
        return pd.Series(index=sessions, dtype="float64")
    closes = frame.assign(time=pd.to_datetime(frame["time"])).set_index("time")["close"].astype(float)
    return closes.reindex(sessions).pct_change(fill_method=None)


def _project_historical_market_regimes(
    *,
    snapshot: Any,
    observation_dates: Iterable[str],
) -> tuple[Mapping[str, str], Mapping[str, Any]]:
    """Project the frozen, causal VNINDEX regime feature through the last date.

    This intentionally projects only ``historical_market_regime@v1``.  It does
    not synthesize PaperV2 market state because that would additionally require
    a provenance-equivalent historical breadth membership context.
    """
    requested_dates = tuple(sorted({pd.Timestamp(value).date().isoformat() for value in observation_dates}))
    if not requested_dates:
        return MappingProxyType({}), MappingProxyType({
            "status": "NOT_EVALUABLE",
            "reason": "no persisted Phase 6 observation dates",
            "feature_request": _HISTORICAL_MARKET_REGIME_REQUEST.canonical(),
        })
    through_date = requested_dates[-1]
    registry = FeatureRegistry(builtin_definitions())
    result = registry.compute(
        _HISTORICAL_MARKET_REGIME_REQUEST,
        snapshot,
        ("VNINDEX",),
        through_date=through_date,
        universe_identity=_HISTORICAL_MARKET_REGIME_UNIVERSE_IDENTITY,
    )
    if "VNINDEX" not in result.available_symbols:
        raise ValueError("VNINDEX is unavailable for historical regime projection")
    frame = result.frame_for("VNINDEX")
    labels_by_date = {
        pd.Timestamp(time).date().isoformat(): str(label)
        for time, label in frame.loc[:, ("time", "Market_Regime")].itertuples(index=False, name=None)
    }
    missing_dates = tuple(date for date in requested_dates if date not in labels_by_date)
    labels = MappingProxyType({
        date: labels_by_date.get(date, "UNAVAILABLE")
        for date in requested_dates
    })
    provenance = MappingProxyType({
        "status": "EVALUABLE" if not missing_dates else "PARTIALLY_EVALUABLE",
        "label_kind": "DERIVED_CURRENT_CANONICAL_MARKET_REGIME",
        "logic_authority": "historical_market_regime@v1",
        "runtime_parity_authority": "strategy.market_regime.prepare_market_regime_history",
        "feature_request": _HISTORICAL_MARKET_REGIME_REQUEST.canonical(),
        "feature_identity": result.computation_identity.feature_identity.sha256,
        "computation_identity": result.computation_identity.sha256,
        "benchmark_symbol": "VNINDEX",
        "causal_data_cutoff": through_date,
        "observation_date_alignment": "exact VNINDEX session only",
        "projected_observation_count": len(labels),
        "unavailable_observation_dates": missing_dates,
        "warnings": (
            "regime labels are derived from the current canonical market database through each fixed historical cutoff",
            "Phase 6 did not persist original regime labels, so this projection is not a provenance-identical Phase 6 artifact field",
            "PaperV2 breadth-dependent market state is intentionally not synthesized by this source adapter",
        ),
    })
    return labels, provenance


def _phase6_positions(rows: Iterable[Mapping[str, str]]) -> Mapping[tuple[str, int], tuple[tuple[str, float], ...]]:
    grouped: dict[tuple[str, int], list[tuple[str, float]]] = {}
    for row in rows:
        session = pd.Timestamp(row["session_date"]).date().isoformat()
        budget = int(row["requested_budget"])
        symbol = row["symbol"].strip().upper()
        if not symbol or symbol == "VNINDEX":
            raise ValueError("Phase 6 positions must contain non-benchmark symbols")
        grouped.setdefault((session, budget), []).append((symbol, _finite(row["weight"], field_name="position weight")))
    normalized: dict[tuple[str, int], tuple[tuple[str, float], ...]] = {}
    for key, items in grouped.items():
        ordered = tuple(sorted(items))
        if len({symbol for symbol, _ in ordered}) != len(ordered):
            raise ValueError(f"duplicate Phase 6 position symbol for {key}")
        normalized[key] = ordered
    return MappingProxyType(normalized)


def load_phase6_portfolio_evidence(
    *,
    phase6_root: str | Path = DEFAULT_PHASE6_ROOT,
    database_path: str | Path | None = None,
    requested_budget: int,
) -> PersistedPortfolioEvidenceSource:
    """Adapt one frozen Phase 6 portfolio budget into causal diagnostic inputs.

    One observation is emitted per persisted daily portfolio snapshot.  Weights
    and cash come directly from Phase 6; market returns are exact historical
    close-to-close returns through that snapshot's date, with no forward fill.
    """
    root = Path(phase6_root).resolve()
    manifest_path = root / "portfolio_construction_manifest.json"
    daily_path = root / "portfolio_construction_by_date.csv"
    positions_path = root / "portfolio_positions.csv"
    manifest = _load_json(manifest_path)
    if not manifest.get("completed") or manifest.get("construction_contract") != "quantlab.neutral_portfolio_construction":
        raise ValueError("Phase 6 source is incomplete or incompatible")
    if requested_budget not in {int(value) for value in manifest.get("budgets", ())}:
        raise ValueError(f"requested budget is absent from Phase 6 source: {requested_budget}")
    daily_rows = _csv_rows(daily_path)
    position_rows = _csv_rows(positions_path)
    expected = int(manifest.get("artifacts", {}).get("portfolio_construction_by_date.csv", -1))
    if len(daily_rows) != expected:
        raise ValueError("Phase 6 daily row count does not match its manifest")
    positions = _phase6_positions(position_rows)
    selected_rows = tuple(
        row for row in daily_rows
        if int(row["requested_budget"]) == requested_budget
    )
    if not selected_rows:
        raise ValueError(f"Phase 6 source contains no budget {requested_budget} rows")
    keys = [(pd.Timestamp(row["session_date"]).date().isoformat(), requested_budget) for row in selected_rows]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate Phase 6 daily portfolio snapshots")
    symbols = tuple(sorted({symbol for key in keys for symbol, _ in positions.get(key, ())} | {"VNINDEX"}))
    canonical_database = resolve_market_database_path(database_path)
    snapshot = build_market_data_snapshot(canonical_database)
    through_date = max(key[0] for key in keys)
    bundle = snapshot.load_ohlcv(symbols, through_date=through_date)
    regimes_by_date, regime_provenance = _project_historical_market_regimes(
        snapshot=snapshot,
        observation_dates=(key[0] for key in keys),
    )
    benchmark_frame = bundle.frame_for("VNINDEX")
    benchmark_sessions = pd.DatetimeIndex(
        pd.to_datetime(benchmark_frame.get("time", pd.Series(dtype="datetime64[ns]")))
    ).sort_values().unique()
    returns = {symbol: _return_series(bundle.frame_for(symbol), benchmark_sessions) for symbol in symbols}
    observations: list[Mapping[str, Any]] = []
    source_provenance = {
        "classification": "DATABASE_COVERAGE",
        "source_kind": "CANONICAL_PHASE6_PORTFOLIO_CONSTRUCTION",
        "strategy_identity": f"{manifest.get('candidate_source', 'UNKNOWN')}/{manifest.get('weighting_policies', ['UNKNOWN'])[0]}",
        "phase6_result_identity": str(manifest["result_identity"]),
        "phase6_specification_fingerprint": str(manifest["specification_fingerprint"]),
        "market_snapshot_id": snapshot.snapshot_id,
        "market_database_path": str(snapshot.canonical_db_path),
        "market_available_start": snapshot.first_session_date,
        "market_available_end": snapshot.last_session_date,
        "historical_market_regime": dict(regime_provenance),
        "warnings": (
            "database coverage is not historical VN100 membership",
            "sector mapping is unavailable; all unmapped exposure remains UNKNOWN",
            "no completed-trade history is present in Phase 6 construction artifacts",
            *tuple(regime_provenance["warnings"]),
        ),
    }
    for row, key in zip(selected_rows, keys, strict=True):
        session, budget = key
        items = positions.get(key, ())
        selected_count = int(row["selected_count"])
        gross_weight = _finite(row["gross_weight"], field_name="gross weight")
        cash_weight = _finite(row["cash_weight"], field_name="cash weight")
        if selected_count != len(items):
            raise ValueError(f"Phase 6 selected count does not match positions for {key}")
        if not math.isclose(sum(weight for _, weight in items), gross_weight, abs_tol=1e-10):
            raise ValueError(f"Phase 6 position weights do not reconcile for {key}")
        if not math.isclose(gross_weight + cash_weight, 1.0, abs_tol=1e-10):
            raise ValueError(f"Phase 6 cash and gross weight do not reconcile for {key}")
        window = benchmark_sessions[benchmark_sessions <= pd.Timestamp(session)][-60:]
        observations.append(MappingProxyType({
            "observation_date": session,
            "weights": MappingProxyType(dict(items)),
            "daily_returns": pd.DataFrame({symbol: returns[symbol].reindex(window) for symbol, _ in items}, index=window),
            "benchmark_returns": returns["VNINDEX"].reindex(window),
            "regime": regimes_by_date[session],
            "trades": (),
            "sector_by_symbol": None,
            "provenance": source_provenance,
            "portfolio_identity": row["identity"],
            "requested_budget": budget,
            "gross_weight": gross_weight,
            "cash_weight": cash_weight,
        }))
    period = manifest.get("period", {})
    start_date = str(period.get("start_date", min(key[0] for key in keys)))
    end_date = str(period.get("end_date", max(key[0] for key in keys)))
    source_identity = _identity({
        "manifest": _file_hash(manifest_path), "daily": _file_hash(daily_path), "positions": _file_hash(positions_path),
        "result_identity": manifest["result_identity"], "budget": requested_budget, "market_snapshot": snapshot.snapshot_id,
        "historical_market_regime": dict(regime_provenance),
    })
    return PersistedPortfolioEvidenceSource(
        source_name=f"phase6_{manifest.get('candidate_source', 'UNKNOWN').lower()}_budget_{requested_budget}",
        classification=PersistedEvidenceSourceClassification.CANONICAL,
        strategy_identity=str(source_provenance["strategy_identity"]), source_identity=source_identity,
        start_date=start_date, end_date=end_date, observations=tuple(observations), provenance=source_provenance,
        limitations=(
            "frozen intended portfolio weights/cash are evaluated; this source has no realized portfolio PnL or executions",
            "market-regime labels are a derived current-canonical projection, not original persisted Phase 6 labels",
            "breadth-dependent PaperV2 market state is not reconstructed because it was not persisted with provenance-equivalent breadth context",
            "sector evidence is UNKNOWN because no canonical point-in-time sector map is persisted",
            "liquidity/capacity remains unavailable because value-unit provenance is absent",
        ),
    )


def load_frozen_q70_cost_evidence(
    *,
    frozen_q70_root: str | Path = DEFAULT_FROZEN_Q70_ROOT,
    arm: str = DEFAULT_FROZEN_Q70_ARM,
) -> PersistedPortfolioEvidenceSource:
    """Adapt completed frozen-Q70 trades for cost evidence only.

    The artifact has executed trades but no authoritative daily portfolio/cash
    snapshot.  The emitted observation therefore has no positions: concentration,
    correlation, beta, sector and liquidity evidence remain unavailable while the
    existing downstream cost calculation can use authentic completed trades.
    """
    root = Path(frozen_q70_root).resolve()
    manifest_path = root / "experiment_manifest.json"
    manifest = _load_json(manifest_path)
    arm_root = root / arm
    trade_path = arm_root / "trade_level_oos.csv"
    summary_path = arm_root / "summary.csv"
    trades_rows = _csv_rows(trade_path)
    summary_rows = _csv_rows(summary_path)
    if len(summary_rows) != 1:
        raise ValueError("frozen-Q70 arm summary must contain exactly one row")
    if arm not in set(manifest.get("arms", ())):
        raise ValueError(f"frozen-Q70 arm is absent from experiment manifest: {arm}")
    normalized_trades: list[Mapping[str, Any]] = []
    for row in trades_rows:
        entry_price = _finite(row["entry_price"], field_name="entry price")
        exit_price = _finite(row["exit_price"], field_name="exit price")
        quantity = _finite(row["quantity"], field_name="quantity")
        net_pnl = _finite(row["net_pnl"], field_name="net PnL")
        persisted_cost = _finite(row["total_transaction_cost"], field_name="transaction cost")
        normalized_trades.append(MappingProxyType({
            "entry_price": entry_price, "exit_price": exit_price, "quantity": quantity,
            # Persisted net PnL already includes commission/tax; adding it back
            # preserves the artifact's gross post-slippage PnL for hypothetical
            # downstream total-cost scenarios.
            "gross_pnl": net_pnl + persisted_cost,
            "trade_identity": "|".join((row["fold"], row["symbol"], row["entry_date"], row["exit_date"])),
        }))
    folds = tuple(manifest.get("folds", ()))
    if not folds:
        raise ValueError("frozen-Q70 experiment has no fold definitions")
    start_date = min(str(fold["test_start"]) for fold in folds)
    end_date = max(str(fold["test_end"]) for fold in folds)
    policy = str(summary_rows[0].get("policy_fingerprint", "")).strip()
    if not policy:
        raise ValueError("frozen-Q70 arm has no policy fingerprint")
    source_provenance = {
        "classification": "DATABASE_COVERAGE",
        "source_kind": "DERIVED_FROZEN_Q70_COMPLETED_TRADES_COST_ONLY",
        "strategy_identity": policy,
        "experiment_manifest_sha256": _file_hash(manifest_path),
        "trade_artifact_sha256": _file_hash(trade_path),
        "summary_artifact_sha256": _file_hash(summary_path),
        "warnings": (
            "database coverage is not historical VN100 membership",
            "completed trades do not provide authoritative daily portfolio weights or cash",
            "cost sensitivity is descriptive and applies hypothetical total costs to persisted turnover",
        ),
    }
    source_identity = _identity({
        "manifest": _file_hash(manifest_path), "trades": _file_hash(trade_path), "summary": _file_hash(summary_path),
        "arm": arm, "policy": policy,
    })
    observation = MappingProxyType({
        "observation_date": end_date,
        "weights": MappingProxyType({}),
        "daily_returns": pd.DataFrame(index=pd.DatetimeIndex([])),
        "benchmark_returns": None,
        "trades": tuple(normalized_trades),
        "sector_by_symbol": None,
        "provenance": source_provenance,
    })
    return PersistedPortfolioEvidenceSource(
        source_name=f"frozen_q70_{arm}_cost_only",
        classification=PersistedEvidenceSourceClassification.DERIVED,
        strategy_identity=policy, source_identity=source_identity,
        start_date=start_date, end_date=end_date, observations=(observation,), provenance=source_provenance,
        limitations=(
            "cost-only source: no authoritative daily portfolio position/cash snapshots were persisted",
            "concentration, correlation, beta, drawdown interaction, sector and liquidity evidence are not evaluable from this source",
            "this historical database-coverage experiment is not historical VN100 membership",
        ),
    )


__all__ = [
    "CONTRACT", "VERSION", "DEFAULT_PHASE6_ROOT", "DEFAULT_FROZEN_Q70_ROOT", "DEFAULT_FROZEN_Q70_ARM",
    "PersistedEvidenceSourceClassification", "PersistedPortfolioEvidenceSource",
    "load_phase6_portfolio_evidence", "load_frozen_q70_cost_evidence",
]
