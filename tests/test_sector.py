from __future__ import annotations

import pandas as pd
import pytest

from core.sector import (
    fetch_sector_mapping,
    get_sector_symbols,
    get_symbol_sector,
    normalize_sector_mapping,
)


def test_normalize_sector_mapping_returns_symbol_to_sector():
    data = pd.DataFrame(
        {
            "symbol": ["MBB", "ACB", "HPG", " FPT "],
            "industry_name": [
                "Banks",
                "Banks",
                "Basic Resources",
                "Technology",
            ],
        }
    )

    result = normalize_sector_mapping(data)

    assert result == {
        "MBB": "Banks",
        "ACB": "Banks",
        "HPG": "Basic Resources",
        "FPT": "Technology",
    }


def test_normalize_sector_mapping_skips_missing_values():
    data = pd.DataFrame(
        {
            "symbol": ["MBB", None, "HPG", "VNM"],
            "sector": ["Banks", "Banks", None, ""],
        }
    )

    result = normalize_sector_mapping(data)

    assert result == {"MBB": "Banks"}


def test_normalize_sector_mapping_accepts_known_column_aliases():
    data = pd.DataFrame(
        {
            "ticker": ["MBB"],
            "industry_name_vn": ["Ngân hàng"],
        }
    )

    assert normalize_sector_mapping(data) == {"MBB": "Ngân hàng"}


def test_normalize_sector_mapping_rejects_unknown_schema():
    data = pd.DataFrame(
        {
            "code": ["MBB"],
            "name": ["Banks"],
        }
    )

    with pytest.raises(ValueError, match="symbol and sector columns"):
        normalize_sector_mapping(data)


def test_fetch_sector_mapping_uses_injected_provider():
    data = pd.DataFrame(
        {
            "symbol": ["MBB", "HPG"],
            "sector": ["Banks", "Basic Resources"],
        }
    )

    calls = 0

    def provider():
        nonlocal calls
        calls += 1
        return data

    result = fetch_sector_mapping(provider)

    assert calls == 1
    assert result == {
        "MBB": "Banks",
        "HPG": "Basic Resources",
    }


def test_symbol_and_sector_helpers_are_deterministic():
    mapping = {
        "MBB": "Banks",
        "ACB": "Banks",
        "HPG": "Basic Resources",
    }

    assert get_symbol_sector(" mbb ", mapping) == "Banks"
    assert get_symbol_sector("VNM", mapping) is None
    assert get_sector_symbols("Banks", mapping) == ("ACB", "MBB")
    assert get_sector_symbols("Unknown", mapping) == ()
