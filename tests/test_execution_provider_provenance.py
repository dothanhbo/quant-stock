from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from quantlab.execution.provider_provenance import (
    AMBIGUOUS,
    PARTIALLY_VERIFIED,
    PHASE11C_DATABASE_SHA256,
    PHASE11C_RESULT_IDENTITY,
    UNAVAILABLE,
    ProvenanceConclusion,
    ProviderWriter,
    build_provider_provenance_result,
)
from research.run_quantlab_execution_provider_provenance import inspect_installed_kbs_contract


def _conclusion(state: str, name: str) -> ProvenanceConclusion:
    return ProvenanceConclusion(state, name, (f"local evidence: {name}",))


def _result(**overrides):
    values = {
        "provider_version": "4.0.2",
        "provider_module_paths": ("/site/vnstock/explorer/kbs/quote.py",),
        "provider_module_sha256": {"/site/vnstock/explorer/kbs/quote.py": "a" * 64},
        "current_unit": _conclusion(PARTIALLY_VERIFIED, "scale operation verified, denomination not labelled"),
        "historical_unit": _conclusion(PARTIALLY_VERIFIED, "writers match today; past versions unknown"),
        "current_adjustment": _conclusion(AMBIGUOUS, "raw-versus-adjusted not established"),
        "historical_adjustment": _conclusion(UNAVAILABLE, "no historical row-level metadata"),
        "equity_ohlc_scale_divisor": 1000,
        "index_ohlc_scale_divisor": 1,
        "price_rounding_decimals": 2,
        "adjustment_caller_selectable": False,
        "writers": (
            ProviderWriter("scripts/update_data.py", "KBS", "1D", "core.database.save_price_data"),
            ProviderWriter("scripts/backfill_market_data.py", "KBS", "1D", "core.database.save_price_data"),
        ),
        "writer_paths_equivalent": True,
        "mixed_historical_provenance_possible": True,
    }
    values.update(overrides)
    return build_provider_provenance_result(**values)


def test_current_and_historical_unit_conclusions_are_separate() -> None:
    result = _result()
    assert result.current_unit.state == PARTIALLY_VERIFIED
    assert result.historical_unit.state == PARTIALLY_VERIFIED
    assert result.current_unit != result.historical_unit


def test_current_adapter_evidence_does_not_automatically_verify_history() -> None:
    result = _result(
        current_unit=_conclusion("VERIFIED", "current local transformation is fully established"),
        historical_unit=_conclusion(UNAVAILABLE, "historical version cannot be attributed"),
    )
    assert result.current_unit.state == "VERIFIED"
    assert result.historical_unit.state == UNAVAILABLE


def test_unit_and_adjustment_conclusions_are_independent_and_fail_closed() -> None:
    result = _result(current_unit=_conclusion("VERIFIED", "transformation"))
    assert result.current_unit.state == "VERIFIED"
    assert result.current_adjustment.state == AMBIGUOUS
    assert result.adjustment_caller_selectable is False
    with pytest.raises(ValueError, match="unsupported evidence state"):
        ProvenanceConclusion("RAW", "unsupported", ("x",))


def test_currently_equivalent_writers_still_surface_mixed_historical_provenance() -> None:
    result = _result()
    assert result.writer_paths_equivalent is True
    assert result.mixed_historical_provenance_possible is True
    divergent = _result(writer_paths_equivalent=False, mixed_historical_provenance_possible=True)
    assert divergent.writer_paths_equivalent is False
    assert divergent.identity != result.identity


def test_result_identity_and_writer_order_are_deterministic_and_immutable() -> None:
    first = _result()
    second = _result(writers=tuple(reversed(first.writers)))
    changed = _result(current_unit=_conclusion(PARTIALLY_VERIFIED, "different source evidence"))
    assert first.identity == second.identity
    assert first.identity != changed.identity
    assert first.writers == tuple(sorted(first.writers, key=lambda row: row.path))
    with pytest.raises(TypeError):
        first.provider_module_sha256["x"] = "y"  # type: ignore[index]


def test_phase11c_identity_or_database_mismatch_fails_closed() -> None:
    with pytest.raises(ValueError, match="Phase 11C result identity mismatch"):
        _result(phase11c_result_identity="wrong")
    with pytest.raises(ValueError, match="Phase 11C database identity mismatch"):
        _result(phase11c_database_sha256="0" * 64)
    with pytest.raises(ValueError, match="current market database differs"):
        _result(current_database_sha256="f" * 64)
    assert PHASE11C_RESULT_IDENTITY.startswith("4fb274")
    assert len(PHASE11C_DATABASE_SHA256) == 64


def test_source_inspection_recognizes_scale_but_does_not_claim_adjustment_semantics(tmp_path: Path) -> None:
    package = tmp_path / "site-packages"
    api = package / "vnstock/api/quote.py"
    kbs = package / "vnstock/explorer/kbs/quote.py"
    const = package / "vnstock/explorer/kbs/const.py"
    for path in (api, kbs, const):
        path.parent.mkdir(parents=True, exist_ok=True)
    api.write_text(
        "class Quote:\n    def history(self, start=None, end=None, interval='1D', **kwargs):\n        return self._delegate_to_provider(M.HISTORY)\n",
        encoding="utf-8",
    )
    kbs.write_text(
        "class Quote:\n    def history(self, start=None, end=None, interval='1D', floating: Optional[int] = 2):\n"
        "        if self.asset_type not in ['derivative', 'index']:\n            df[col] = df[col] / 1000\n"
        "        df[col] = df[col].round(floating)\n",
        encoding="utf-8",
    )
    const.write_text(
        "_OHLC_MAP = {'t': 'time', 'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close', 'v': 'volume'}\n",
        encoding="utf-8",
    )
    inspected = inspect_installed_kbs_contract(package)
    assert inspected["equity_ohlc_divisor"] == 1000
    assert inspected["index_ohlc_divisor"] == 1
    assert inspected["rounding_decimals"] == 2
    assert inspected["adjustment_selectors"] == []
    assert inspected["adjustment_transformation_present"] is False
    assert inspected["mapped_response_fields"] == ("t", "o", "h", "l", "c", "v")


def test_import_and_evidence_inspection_never_import_provider_or_open_network(tmp_path: Path) -> None:
    code = (
        "import sys; import research.run_quantlab_execution_provider_provenance; "
        "assert not any(n == 'vnstock' or n.startswith('vnstock.') for n in sys.modules)"
    )
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr

    # Source inspection is local file I/O only; it cannot reach a provider API.
    result = _result()
    assert result.no_network is True

