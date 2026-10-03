from __future__ import annotations

"""Explicit DataFrame adapter for D4B2 shadow evaluation only."""

from collections.abc import Sequence
from datetime import date

import pandas as pd

from quantlab.completed_session import CompletedSessionResult
from quantlab.operational_admission import IngestionIntent, SymbolIdentityState
from quantlab.preupdate_market_data_guard import (
    AdjustmentBasis,
    BoundaryEvidence,
    CoverageMetadata,
    RevisionEvidence,
)
from quantlab.transactional_market_data import (
    AttributionState,
    ImmutableArchiveIdentity,
    PreparedPriceBatch,
    PreparedPriceRow,
)


def prepare_dataframe_price_batch(
    frame: pd.DataFrame,
    *,
    operation_id: str,
    symbol: str,
    requested_start: date,
    requested_end: date,
    coverage: CoverageMetadata,
    provider_identity: str,
    endpoint_identity: str,
    package_name: str,
    package_version: str,
    source_verification_state: AttributionState,
    source_references: Sequence[str],
    price_unit: str,
    price_unit_verification_state: AttributionState = AttributionState.UNKNOWN,
    price_unit_references: Sequence[str] = (),
    claimed_adjustment_basis: AdjustmentBasis = AdjustmentBasis.UNKNOWN,
    adjustment_verification_state: AttributionState = AttributionState.UNKNOWN,
    adjustment_evidence_references: Sequence[str] = (),
    ingestion_intent: IngestionIntent = IngestionIntent.INCREMENTAL_UPDATE,
    symbol_identity_state: SymbolIdentityState = SymbolIdentityState.UNCERTAIN,
    symbol_identity_references: Sequence[str] = (),
    completed_through: date | None = None,
    completed_session_result: CompletedSessionResult | None = None,
    corporate_action_verification_state: AttributionState = AttributionState.UNKNOWN,
    archive: ImmutableArchiveIdentity | None = None,
    revision_evidence: Sequence[RevisionEvidence] = (),
    boundary_evidence: Sequence[BoundaryEvidence] = (),
) -> PreparedPriceBatch:
    """Normalize an already-returned DataFrame without claiming raw HTTP bytes."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("candidate DataFrame must be non-empty")
    data = frame.copy(deep=True)
    data.columns = [str(value).strip().lower() for value in data.columns]
    required = ("symbol", "time", "open", "high", "low", "close", "volume")
    missing = tuple(value for value in required if value not in data.columns)
    if missing:
        raise ValueError(f"candidate DataFrame missing columns: {', '.join(missing)}")

    normalized_symbol = str(symbol).strip().upper()
    data["symbol"] = data["symbol"].astype(str).str.strip().str.upper()
    if normalized_symbol == "" or not data["symbol"].eq(normalized_symbol).all():
        raise ValueError("candidate DataFrame symbols do not match the requested symbol")
    parsed_time = pd.to_datetime(data["time"], errors="coerce")
    if parsed_time.isna().any():
        raise ValueError("candidate DataFrame contains invalid sessions")
    if getattr(parsed_time.dt, "tz", None) is not None:
        raise ValueError("candidate sessions must be timezone-naive completed dates")
    data["time"] = parsed_time.dt.date
    if data["time"].duplicated().any():
        raise ValueError("candidate DataFrame contains duplicate sessions")
    for column in ("open", "high", "low", "close", "volume"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    if data[["open", "high", "low", "close", "volume"]].isna().any(axis=None):
        raise ValueError("candidate DataFrame contains non-numeric OHLCV")

    rows = tuple(
        PreparedPriceRow(
            row.symbol,
            row.time,
            row.open,
            row.high,
            row.low,
            row.close,
            row.volume,
        )
        for row in data.sort_values("time").itertuples(index=False)
    )
    return PreparedPriceBatch(
        operation_id=operation_id,
        symbol=normalized_symbol,
        requested_start=requested_start,
        requested_end=requested_end,
        rows=rows,
        coverage=coverage,
        provider_identity=provider_identity,
        endpoint_identity=endpoint_identity,
        package_name=package_name,
        package_version=package_version,
        source_verification_state=source_verification_state,
        source_references=tuple(source_references),
        price_unit=price_unit,
        price_unit_verification_state=price_unit_verification_state,
        price_unit_references=tuple(price_unit_references),
        claimed_adjustment_basis=claimed_adjustment_basis,
        adjustment_verification_state=adjustment_verification_state,
        adjustment_evidence_references=tuple(adjustment_evidence_references),
        archive=archive,
        revision_evidence=tuple(revision_evidence),
        boundary_evidence=tuple(boundary_evidence),
        ingestion_intent=ingestion_intent,
        symbol_identity_state=symbol_identity_state,
        symbol_identity_references=tuple(symbol_identity_references),
        completed_through=completed_through,
        completed_session_result=completed_session_result,
        corporate_action_verification_state=corporate_action_verification_state,
    )


__all__ = ["prepare_dataframe_price_batch"]
