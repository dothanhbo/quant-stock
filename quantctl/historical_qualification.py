from __future__ import annotations

from collections.abc import Iterable


D1_REPORT_PATH = (
    "research/data_integrity_audit/"
    "run_20261002T075232Z_f771b314/incident_report.md"
)
D2_REPORT_PATH = (
    "research/data_integrity_audit/"
    "run_20261002T080507Z_d2_f771b314/d2_report.md"
)

HISTORICAL_EVIDENCE_QUALIFICATIONS = (
    "LEGACY_UNVERIFIED",
    "PRICE_ADJUSTMENT_UNKNOWN",
    "CORPORATE_ACTION_PROVENANCE_UNAVAILABLE",
    "TICKER_IDENTITY_HISTORY_UNAVAILABLE",
    (
        "Historical OHLCV includes unresolved D1/D2 integrity incidents and "
        "broad equity-session dates missing from the local VNINDEX calendar; "
        f"evidence: {D1_REPORT_PATH}; {D2_REPORT_PATH}."
    ),
)


def qualify_historical_limitations(limitations: Iterable[str]) -> tuple[str, ...]:
    """Append fail-closed provenance qualifications without changing evidence."""

    normalized = tuple(str(value).strip() for value in limitations if str(value).strip())
    return tuple(dict.fromkeys((*normalized, *HISTORICAL_EVIDENCE_QUALIFICATIONS)))


__all__ = [
    "D1_REPORT_PATH",
    "D2_REPORT_PATH",
    "HISTORICAL_EVIDENCE_QUALIFICATIONS",
    "qualify_historical_limitations",
]
