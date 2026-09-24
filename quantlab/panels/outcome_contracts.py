from __future__ import annotations

"""Immutable, future-looking outcome-panel contracts for research evaluation."""

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import math
from types import MappingProxyType
from typing import Any, Mapping

import pandas as pd

from quantlab.identity import canonical_identity_value, canonical_json

from .contracts import ObservationSessionAudit


OUTCOME_PANEL_CONTRACT = "quantlab.point_in_time_forward_outcome_panel"
OUTCOME_PANEL_VERSION = "v1"
OUTCOME_BASE_COLUMNS = (
    "session_date",
    "symbol",
    "benchmark_symbol",
    "membership_set_hash",
    "member_ordinal",
)
BENCHMARK_SESSION_METHOD = "hth_stored_benchmark_session_strictly_after_observation_v1"
RETURN_FORMULAS = (
    "stock_forward_return_pct=(stock_target_close/stock_signal_close-1)*100",
    "benchmark_forward_return_pct=(benchmark_target_close/benchmark_signal_close-1)*100",
    "excess_forward_return_pct_points=stock_forward_return_pct-benchmark_forward_return_pct",
)
PERMITTED_USE = "research_evaluation_only"


class PanelForwardOutcomeAvailability(str, Enum):
    AVAILABLE = "AVAILABLE"
    OBSERVATION_MARKET_ROW_MISSING = "OBSERVATION_MARKET_ROW_MISSING"
    MISSING_STOCK_SIGNAL_CLOSE = "MISSING_STOCK_SIGNAL_CLOSE"
    NONPOSITIVE_STOCK_SIGNAL_CLOSE = "NONPOSITIVE_STOCK_SIGNAL_CLOSE"
    CENSORED_AFTER_DATA_END = "CENSORED_AFTER_DATA_END"
    MISSING_STOCK_TARGET_CLOSE = "MISSING_STOCK_TARGET_CLOSE"
    NONPOSITIVE_STOCK_TARGET_CLOSE = "NONPOSITIVE_STOCK_TARGET_CLOSE"
    MISSING_BENCHMARK_SIGNAL_CLOSE = "MISSING_BENCHMARK_SIGNAL_CLOSE"
    NONPOSITIVE_BENCHMARK_SIGNAL_CLOSE = "NONPOSITIVE_BENCHMARK_SIGNAL_CLOSE"
    MISSING_BENCHMARK_TARGET_CLOSE = "MISSING_BENCHMARK_TARGET_CLOSE"
    NONPOSITIVE_BENCHMARK_TARGET_CLOSE = "NONPOSITIVE_BENCHMARK_TARGET_CLOSE"


OUTCOME_AVAILABILITY_PRECEDENCE = (
    PanelForwardOutcomeAvailability.OBSERVATION_MARKET_ROW_MISSING.value,
    PanelForwardOutcomeAvailability.MISSING_STOCK_SIGNAL_CLOSE.value,
    PanelForwardOutcomeAvailability.NONPOSITIVE_STOCK_SIGNAL_CLOSE.value,
    PanelForwardOutcomeAvailability.MISSING_BENCHMARK_SIGNAL_CLOSE.value,
    PanelForwardOutcomeAvailability.NONPOSITIVE_BENCHMARK_SIGNAL_CLOSE.value,
    PanelForwardOutcomeAvailability.CENSORED_AFTER_DATA_END.value,
    PanelForwardOutcomeAvailability.MISSING_BENCHMARK_TARGET_CLOSE.value,
    PanelForwardOutcomeAvailability.NONPOSITIVE_BENCHMARK_TARGET_CLOSE.value,
    PanelForwardOutcomeAvailability.MISSING_STOCK_TARGET_CLOSE.value,
    PanelForwardOutcomeAvailability.NONPOSITIVE_STOCK_TARGET_CLOSE.value,
    PanelForwardOutcomeAvailability.AVAILABLE.value,
)


def _horizon_columns(horizon: int) -> tuple[str, ...]:
    return (
        f"target_session_{horizon}",
        f"stock_forward_return_{horizon}_pct",
        f"benchmark_forward_return_{horizon}_pct",
        f"excess_forward_return_{horizon}_pct_points",
        f"outcome_{horizon}__availability",
    )


def _validated_horizons(values: tuple[int, ...]) -> tuple[int, ...]:
    horizons = tuple(values)
    if not horizons or any(
        isinstance(item, bool) or not isinstance(item, int) or item <= 0
        for item in horizons
    ):
        raise ValueError("outcome horizons must be positive integers")
    if len(set(horizons)) != len(horizons):
        raise ValueError("outcome horizons must be unique")
    return horizons


@dataclass(frozen=True, slots=True)
class PointInTimeOutcomePanelSpec:
    name: str
    version: str
    horizons: tuple[int, ...]
    benchmark_session_method: str = BENCHMARK_SESSION_METHOD
    return_formulas: tuple[str, ...] = RETURN_FORMULAS
    availability_precedence: tuple[str, ...] = OUTCOME_AVAILABILITY_PRECEDENCE
    future_looking: bool = True
    permitted_use: str = PERMITTED_USE
    output_columns: tuple[str, ...] = field(init=False)
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        name, version = str(self.name).strip(), str(self.version).strip()
        if not name or not version:
            raise ValueError("outcome-panel spec name and version are required")
        horizons = _validated_horizons(tuple(self.horizons))
        if self.benchmark_session_method != BENCHMARK_SESSION_METHOD:
            raise ValueError("unsupported benchmark-session outcome method")
        if tuple(self.return_formulas) != RETURN_FORMULAS:
            raise ValueError("unsupported forward-return formulas")
        if tuple(self.availability_precedence) != OUTCOME_AVAILABILITY_PRECEDENCE:
            raise ValueError("unsupported outcome availability precedence")
        if self.future_looking is not True or self.permitted_use != PERMITTED_USE:
            raise ValueError("outcome panels are restricted to future-looking research evaluation")
        columns = list(OUTCOME_BASE_COLUMNS)
        for horizon in horizons:
            columns.extend(_horizon_columns(horizon))
        columns.extend((
            "requested_horizon_count",
            "available_horizon_count",
            "fully_labeled_outcome_row",
        ))
        payload = {
            "contract": {"name": OUTCOME_PANEL_CONTRACT, "version": OUTCOME_PANEL_VERSION},
            "name": name,
            "version": version,
            "horizons": list(horizons),
            "benchmark_session_method": self.benchmark_session_method,
            "return_formulas": list(self.return_formulas),
            "availability_precedence": list(self.availability_precedence),
            "output_columns": columns,
            "future_looking": True,
            "permitted_use": PERMITTED_USE,
        }
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "horizons", horizons)
        object.__setattr__(self, "return_formulas", tuple(self.return_formulas))
        object.__setattr__(self, "availability_precedence", tuple(self.availability_precedence))
        object.__setattr__(self, "output_columns", tuple(columns))
        object.__setattr__(self, "fingerprint", sha256(canonical_json(payload)).hexdigest())


POINT_IN_TIME_FORWARD_OUTCOMES_5_10_20_V1 = PointInTimeOutcomePanelSpec(
    name="point_in_time_forward_outcomes_5_10_20",
    version="v1",
    horizons=(5, 10, 20),
)


def _immutable(value: Any) -> bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, tuple):
        return all(_immutable(item) for item in value)
    if isinstance(value, MappingProxyType):
        return all(isinstance(key, str) and _immutable(item) for key, item in value.items())
    return False


def _identity_scalar(value: Any) -> Any:
    if value is None or value is pd.NA or pd.isna(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    return value


def outcome_content_identity(
    *,
    observation_content_identity: str,
    frame: pd.DataFrame,
    spec: PointInTimeOutcomePanelSpec,
    batch_size: int = 2_048,
) -> str:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
        raise ValueError("outcome content hash batch_size must be a positive integer")
    digest = sha256()
    digest.update(b'{"observation_content_identity":')
    digest.update(canonical_json(observation_content_identity))
    digest.update(b',"rows":[')
    first = True
    for offset in range(0, len(frame), batch_size):
        for row in frame.iloc[offset:offset + batch_size].itertuples(index=False, name=None):
            if not first:
                digest.update(b",")
            content = {
                name: _identity_scalar(value)
                for name, value in zip(spec.output_columns, row)
            }
            digest.update(canonical_json(canonical_identity_value(content)))
            first = False
    digest.update(b"]}")
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class PointInTimeOutcomePanel:
    spec: PointInTimeOutcomePanelSpec
    observation_index_identity: str
    observation_content_identity: str
    snapshot_id: str
    universe_membership_identity: str
    benchmark_symbol: str
    requested_start_date: str
    requested_through_date: str
    session_audit: tuple[ObservationSessionAudit, ...]
    metadata: Mapping[str, Any]
    _frame: pd.DataFrame = field(repr=False, compare=False)
    _observation_keys: tuple[tuple[Any, ...], ...] = field(repr=False, compare=False)
    _benchmark_session_dates: tuple[str, ...] = field(repr=False, compare=False)
    observation_row_count: int = field(init=False)
    per_horizon_status_counts: Mapping[int, Mapping[PanelForwardOutcomeAvailability, int]] = field(init=False)
    per_horizon_available_counts: Mapping[int, int] = field(init=False)
    fully_labeled_row_count: int = field(init=False)
    partially_labeled_row_count: int = field(init=False)
    zero_available_row_count: int = field(init=False)
    outcome_content_identity: str = field(init=False)
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.spec, PointInTimeOutcomePanelSpec):
            raise TypeError("spec must be PointInTimeOutcomePanelSpec")
        for name in (
            "observation_index_identity", "observation_content_identity", "snapshot_id",
            "universe_membership_identity", "benchmark_symbol", "requested_start_date",
            "requested_through_date",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.metadata, MappingProxyType) or not _immutable(self.metadata):
            raise ValueError("outcome-panel metadata must be immutable")
        if self.metadata.get("future_looking") is not True or self.metadata.get("permitted_use") != PERMITTED_USE:
            raise ValueError("outcome-panel metadata must retain the research-only boundary")
        audits = tuple(self.session_audit)
        if not audits:
            raise ValueError("outcome panel requires observation session audits")
        frame = self._frame.copy(deep=True)
        if tuple(frame.columns) != self.spec.output_columns:
            raise ValueError("unsupported outcome-panel output schema")
        keys = tuple(
            tuple(row)
            for row in frame.loc[:, OUTCOME_BASE_COLUMNS].itertuples(index=False, name=None)
        )
        if keys != tuple(self._observation_keys):
            raise ValueError("outcome construction mutated observation keys or row order")
        benchmark = self.benchmark_symbol.strip().upper()
        if not frame.empty and not bool(frame["benchmark_symbol"].astype(str).eq(benchmark).all()):
            raise ValueError("outcome rows do not match the observation benchmark")
        benchmark_sessions = tuple(self._benchmark_session_dates)
        if benchmark_sessions != tuple(sorted(set(benchmark_sessions))):
            raise ValueError("benchmark sessions must be uniquely chronological")
        benchmark_positions = {session: index for index, session in enumerate(benchmark_sessions)}
        statuses = tuple(PanelForwardOutcomeAvailability)
        counts_by_horizon: dict[int, Mapping[PanelForwardOutcomeAvailability, int]] = {}
        available_by_horizon: dict[int, int] = {}
        available_per_row = pd.Series([0] * len(frame), dtype="int64")
        for horizon in self.spec.horizons:
            target_column = f"target_session_{horizon}"
            stock_column = f"stock_forward_return_{horizon}_pct"
            benchmark_column = f"benchmark_forward_return_{horizon}_pct"
            excess_column = f"excess_forward_return_{horizon}_pct_points"
            status_column = f"outcome_{horizon}__availability"
            raw_status = frame[status_column].astype(str)
            unsupported = sorted(set(raw_status).difference(item.value for item in statuses))
            if unsupported:
                raise ValueError(f"unsupported outcome availability for horizon {horizon}: {unsupported}")
            is_available = raw_status.eq(PanelForwardOutcomeAvailability.AVAILABLE.value)
            available_per_row += is_available.astype("int64")
            numeric = frame.loc[:, [stock_column, benchmark_column, excess_column]]
            if numeric.loc[is_available].isna().any(axis=None):
                raise ValueError("available outcomes require all return values")
            if numeric.loc[~is_available].notna().any(axis=None):
                raise ValueError("unavailable outcomes must not emit return values")
            if frame.loc[is_available, target_column].isna().any():
                raise ValueError("available outcomes require a target session")
            for row_index, row in frame.iterrows():
                target = row[target_column]
                if pd.isna(target):
                    continue
                signal = str(row["session_date"])
                target_text = str(target)
                if target_text <= signal:
                    raise ValueError("outcome targets must be strictly after observation sessions")
                if signal not in benchmark_positions or target_text not in benchmark_positions:
                    raise ValueError("outcome target is not on the loaded benchmark calendar")
                if benchmark_positions[target_text] - benchmark_positions[signal] != horizon:
                    raise ValueError("outcome target does not match the requested benchmark-session horizon")
                if is_available.loc[row_index]:
                    stock_return = float(row[stock_column])
                    benchmark_return = float(row[benchmark_column])
                    excess_return = float(row[excess_column])
                    if not all(math.isfinite(item) for item in (stock_return, benchmark_return, excess_return)):
                        raise ValueError("available outcomes must contain finite returns")
                    if not math.isclose(
                        excess_return, stock_return - benchmark_return,
                        rel_tol=1e-12, abs_tol=1e-12,
                    ):
                        raise ValueError("excess outcome does not reconcile")
            horizon_counts = MappingProxyType({
                status: int(raw_status.eq(status.value).sum()) for status in statuses
            })
            if sum(horizon_counts.values()) != len(frame):
                raise ValueError("per-horizon outcome statuses do not reconcile")
            counts_by_horizon[horizon] = horizon_counts
            available_by_horizon[horizon] = int(is_available.sum())
        if not frame["requested_horizon_count"].astype(int).eq(len(self.spec.horizons)).all():
            raise ValueError("requested-horizon row diagnostics are inconsistent")
        if not frame["available_horizon_count"].astype(int).equals(available_per_row):
            raise ValueError("available-horizon row diagnostics are inconsistent")
        fully = available_per_row.eq(len(self.spec.horizons))
        if not frame["fully_labeled_outcome_row"].astype(bool).equals(fully):
            raise ValueError("fully-labeled row diagnostics are inconsistent")
        partial = available_per_row.gt(0) & ~fully
        zero = available_per_row.eq(0)
        expected_status_metadata = {
            str(horizon): {
                status.value: counts_by_horizon[horizon][status] for status in statuses
            }
            for horizon in self.spec.horizons
        }
        expected_available_metadata = {
            str(horizon): available_by_horizon[horizon] for horizon in self.spec.horizons
        }
        metadata_checks = {
            "observation_row_count": len(frame),
            "horizons": self.spec.horizons,
            "fully_labeled_row_count": int(fully.sum()),
            "partially_labeled_row_count": int(partial.sum()),
            "zero_available_row_count": int(zero.sum()),
            "first_observation_session": audits[0].session_date,
            "last_observation_session": audits[-1].session_date,
            "observation_index_identity": self.observation_index_identity,
            "snapshot_id": self.snapshot_id,
            "universe_membership_identity": self.universe_membership_identity,
            "benchmark_symbol": benchmark,
            "requested_start_date": self.requested_start_date,
            "requested_through_date": self.requested_through_date,
        }
        for name, expected in metadata_checks.items():
            if self.metadata.get(name) != expected:
                raise ValueError(f"outcome-panel metadata is inconsistent: {name}")
        if dict(self.metadata.get("per_horizon_available_counts", {})) != expected_available_metadata:
            raise ValueError("outcome-panel available-count metadata is inconsistent")
        raw_status_metadata = self.metadata.get("per_horizon_status_counts", {})
        actual_status_metadata = {
            str(horizon): dict(raw_status_metadata.get(str(horizon), {}))
            for horizon in self.spec.horizons
        }
        if actual_status_metadata != expected_status_metadata:
            raise ValueError("outcome-panel status-count metadata is inconsistent")
        last_snapshot_session = self.metadata.get("last_snapshot_session_used_for_labeling")
        if not isinstance(last_snapshot_session, str) or not last_snapshot_session.strip():
            raise ValueError("outcome-panel labeling boundary metadata is missing")
        content_identity = outcome_content_identity(
            observation_content_identity=self.observation_content_identity,
            frame=frame,
            spec=self.spec,
        )
        payload = {
            "contract": {"name": OUTCOME_PANEL_CONTRACT, "version": OUTCOME_PANEL_VERSION},
            "observation_index_identity": self.observation_index_identity,
            "snapshot_id": self.snapshot_id,
            "universe_membership_identity": self.universe_membership_identity,
            "benchmark_symbol": benchmark,
            "requested_start_date": self.requested_start_date,
            "requested_through_date": self.requested_through_date,
            "specification_fingerprint": self.spec.fingerprint,
            "outcome_content_identity": content_identity,
            "observation_row_count": len(frame),
            "per_horizon_available_counts": {
                str(horizon): available_by_horizon[horizon] for horizon in self.spec.horizons
            },
            "fully_labeled_row_count": int(fully.sum()),
            "partially_labeled_row_count": int(partial.sum()),
            "zero_available_row_count": int(zero.sum()),
            "metadata": self.metadata,
        }
        object.__setattr__(self, "benchmark_symbol", benchmark)
        object.__setattr__(self, "session_audit", audits)
        object.__setattr__(self, "_frame", frame)
        object.__setattr__(self, "_observation_keys", keys)
        object.__setattr__(self, "_benchmark_session_dates", benchmark_sessions)
        object.__setattr__(self, "observation_row_count", len(frame))
        object.__setattr__(self, "per_horizon_status_counts", MappingProxyType(counts_by_horizon))
        object.__setattr__(self, "per_horizon_available_counts", MappingProxyType(available_by_horizon))
        object.__setattr__(self, "fully_labeled_row_count", int(fully.sum()))
        object.__setattr__(self, "partially_labeled_row_count", int(partial.sum()))
        object.__setattr__(self, "zero_available_row_count", int(zero.sum()))
        object.__setattr__(self, "outcome_content_identity", content_identity)
        object.__setattr__(self, "identity", sha256(canonical_json(canonical_identity_value(payload))).hexdigest())

    @property
    def frame(self) -> pd.DataFrame:
        return self._frame.copy(deep=True)
