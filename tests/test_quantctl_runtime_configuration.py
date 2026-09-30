from __future__ import annotations

import json
from pathlib import Path

from config.paper_store import V3_STRATEGY_IDENTITY
from quantctl.runtime_configuration import resolve_runtime_configuration


def test_equivalent_runtime_configuration_has_stable_secret_free_fingerprint(monkeypatch) -> None:
    monkeypatch.setenv("PAPER_V2_DATABASE_PATH", "data/paper-v2-runtime.db")
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    first = resolve_runtime_configuration()
    second = resolve_runtime_configuration()

    assert first.fingerprint == second.fingerprint
    serialized = json.dumps(first.as_dict(), sort_keys=True)
    assert "TELEGRAM_TOKEN" not in serialized
    assert "paper-v2-runtime.db" in serialized
    assert first.payload["market_database_path"].endswith("data\\market.db") or first.payload["market_database_path"].endswith("data/market.db")


def test_material_runtime_policy_change_changes_fingerprint(monkeypatch) -> None:
    baseline = resolve_runtime_configuration()
    monkeypatch.setenv("PAPER_MAX_OPEN_POSITIONS", "7")
    changed = resolve_runtime_configuration()

    assert baseline.fingerprint != changed.fingerprint


def test_runtime_fingerprint_binds_actual_lifecycle_risk_defaults(monkeypatch) -> None:
    monkeypatch.delenv("PAPER_MAX_OPEN_POSITIONS", raising=False)

    snapshot = resolve_runtime_configuration()

    # Scanner and lifecycle currently have intentionally distinct defaults;
    # provenance must preserve both rather than mislabel the lifecycle as 10.
    assert snapshot.payload["paper_execution"]["maximum_open_positions"] == 10
    assert snapshot.payload["paper_lifecycle"]["risk_limits"]["maximum_open_positions"] == 5


def test_runtime_fingerprint_preserves_literal_true_trailing_contract(monkeypatch) -> None:
    monkeypatch.setenv("PAPER_V2_DISABLE_TRAILING", "1")

    snapshot = resolve_runtime_configuration()

    assert snapshot.payload["paper_lifecycle"]["exit"]["enable_trailing_stop"] is True
    assert snapshot.payload["paper_lifecycle"]["exit"]["default_trailing_atr_multiplier"] is not None


def test_strategy_and_store_identity_are_bound_to_runtime_fingerprint(monkeypatch) -> None:
    monkeypatch.setenv("PAPER_STRATEGY_VERSION", V3_STRATEGY_IDENTITY)
    monkeypatch.setenv("PAPER_V3_DATABASE_PATH", "data/custom-v3-runtime.db")
    snapshot = resolve_runtime_configuration()

    assert snapshot.strategy_identity == V3_STRATEGY_IDENTITY
    assert snapshot.paper_store_id == "v3-breadth-40-60"
    assert snapshot.payload["paper_store"]["database_path"].endswith("custom-v3-runtime.db")
