from __future__ import annotations

import math


UNAVAILABLE = "UNAVAILABLE"
UNKNOWN = "UNKNOWN"
_TEMPORAL_BLOCK_LABELS = {
    "early_2018_2020": "2018–20",
    "middle_2021_2022": "2021–22",
    "middle_2023_2024": "2023–24",
    "recent_2025_2026": "2025–26",
}


def _finite(value: float | int | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def format_fraction_percent(value: float | None, *, digits: int = 2) -> str:
    """Format a schema-defined 0–1 fraction as a percentage."""
    number = _finite(value)
    return UNAVAILABLE if number is None else f"{number * 100.0:.{digits}f}%"


def format_percent(value: float | None, *, digits: int = 2) -> str:
    """Format a schema-defined 0–100 percentage without rescaling it."""
    number = _finite(value)
    return UNAVAILABLE if number is None else f"{number:.{digits}f}%"


def format_percentage_points(value: float | None, *, digits: int = 3) -> str:
    number = _finite(value)
    return UNAVAILABLE if number is None else f"{number:.{digits}f} pp"


def format_basis_points(value: float | int | None) -> str:
    number = _finite(value)
    return UNAVAILABLE if number is None else f"{number:g} bps"


def format_ic(value: float | None, *, digits: int = 4) -> str:
    number = _finite(value)
    return UNAVAILABLE if number is None else f"{number:.{digits}f}"


def format_ratio(value: float | None, *, digits: int = 3) -> str:
    """Format unitless beta/correlation-style values."""
    number = _finite(value)
    return UNAVAILABLE if number is None else f"{number:.{digits}f}"


def format_count(value: float | int | None, *, digits: int = 1) -> str:
    number = _finite(value)
    if number is None:
        return UNAVAILABLE
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.{digits}f}"


def format_number(value: float | None, *, digits: int = 4) -> str:
    number = _finite(value)
    return UNAVAILABLE if number is None else f"{number:.{digits}f}"


def format_vnd(value: float | None, *, digits: int = 0) -> str:
    number = _finite(value)
    return UNAVAILABLE if number is None else f"{number:,.{digits}f} VND"


def format_temporal_block(value: str) -> str:
    return _TEMPORAL_BLOCK_LABELS.get(value, value.replace("_", " ").title())


__all__ = (
    "UNAVAILABLE",
    "UNKNOWN",
    "format_basis_points",
    "format_count",
    "format_fraction_percent",
    "format_ic",
    "format_number",
    "format_percent",
    "format_percentage_points",
    "format_ratio",
    "format_temporal_block",
    "format_vnd",
)
