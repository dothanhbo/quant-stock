from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import pandas as pd


_SYMBOL_COLUMNS = ("symbol", "ticker", "code", "stock_code")
_SECTOR_COLUMNS = (
    "sector",
    "industry",
    "industry_name",
    "industry_name_en",
    "industry_name_vn",
    "icb_name",
)


def normalize_sector_mapping(data: pd.DataFrame) -> dict[str, str]:
    """Normalize Vnstock sector reference data to {SYMBOL: SECTOR}.

    The upstream schema can vary by endpoint/source, so this adapter accepts
    a small set of known symbol/sector column aliases and fails explicitly
    when the required fields are absent.
    """
    if data is None or data.empty:
        return {}

    symbol_column = next(
        (column for column in _SYMBOL_COLUMNS if column in data.columns),
        None,
    )
    sector_column = next(
        (column for column in _SECTOR_COLUMNS if column in data.columns),
        None,
    )

    if symbol_column is None or sector_column is None:
        raise ValueError(
            "Sector reference data must contain symbol and sector columns; "
            f"received columns={list(data.columns)!r}"
        )

    result: dict[str, str] = {}
    for symbol, sector in data[[symbol_column, sector_column]].itertuples(
        index=False,
        name=None,
    ):
        if pd.isna(symbol) or pd.isna(sector):
            continue

        normalized_symbol = str(symbol).strip().upper()
        normalized_sector = str(sector).strip()

        if not normalized_symbol or not normalized_sector:
            continue

        result[normalized_symbol] = normalized_sector

    return result


def fetch_sector_mapping(
    provider: Callable[[], pd.DataFrame] | None = None,
) -> dict[str, str]:
    """Fetch the current symbol -> sector mapping from Vnstock.

    Vnstock v4 exposes industry classification through its Unified UI.
    The provider is injectable so tests never require a network/API call.
    """
    if provider is None:
        from vnstock import Reference

        reference = Reference()
        provider = reference.industry.sectors

    return normalize_sector_mapping(provider())


def get_symbol_sector(
    symbol: str,
    mapping: Mapping[str, str],
) -> str | None:
    """Return the normalized sector for one symbol."""
    return mapping.get(str(symbol).strip().upper())


def get_sector_symbols(
    sector: str,
    mapping: Mapping[str, str],
) -> tuple[str, ...]:
    """Return symbols belonging to a sector in deterministic order."""
    target = str(sector).strip()
    return tuple(
        sorted(
            symbol
            for symbol, value in mapping.items()
            if value == target
        )
    )
