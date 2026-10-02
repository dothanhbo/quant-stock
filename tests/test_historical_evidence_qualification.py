from __future__ import annotations

import csv
import json
from pathlib import Path

from quantctl.historical_qualification import (
    D1_REPORT_PATH,
    D2_REPORT_PATH,
    HISTORICAL_EVIDENCE_QUALIFICATIONS,
    qualify_historical_limitations,
)
from quantctl.research_home import EvidenceState, inspect_research_home_catalog


def _write_factor_artifact(root: Path) -> None:
    artifact = (
        root
        / "research_results"
        / "quantlab_neutral_panel_factor_evaluation_2018-08-07_2026-09-17"
    )
    artifact.mkdir(parents=True)
    (artifact / "experiment_manifest.json").write_text(
        json.dumps({
            "completed": True,
            "evaluation": {"result_identity": "factor-result-identity"},
            "requested_bounds": {"end_date": "2026-09-17"},
            "limitations": ["original measured limitation"],
        }),
        encoding="utf-8",
    )
    with (artifact / "factor_summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("factor", "horizon_sessions", "outcome_field", "identity"),
        )
        writer.writeheader()
        writer.writerow({
            "factor": "adx_14",
            "horizon_sessions": "20",
            "outcome_field": "stock_forward_return_pct",
            "identity": "numeric-evidence-identity",
        })


def test_qualification_is_fail_closed_ordered_and_idempotent() -> None:
    original = ("original measured limitation", "LEGACY_UNVERIFIED")

    qualified = qualify_historical_limitations(original)

    assert qualified[0] == "original measured limitation"
    assert qualified.count("LEGACY_UNVERIFIED") == 1
    assert qualified == qualify_historical_limitations(qualified)
    assert all(value in qualified for value in HISTORICAL_EVIDENCE_QUALIFICATIONS)
    assert D1_REPORT_PATH in qualified[-1]
    assert D2_REPORT_PATH in qualified[-1]


def test_research_home_qualifies_history_without_changing_identity(tmp_path: Path) -> None:
    _write_factor_artifact(tmp_path)

    catalog = inspect_research_home_catalog(root=tmp_path)
    factor = next(item for item in catalog.artifacts if item.key == "factor_evidence")

    assert factor.state is EvidenceState.AVAILABLE
    assert factor.result_identity == "factor-result-identity"
    assert factor.as_of == "2026-09-17"
    assert factor.limitations[0] == "original measured limitation"
    assert "LEGACY_UNVERIFIED" in factor.limitations
    assert "PRICE_ADJUSTMENT_UNKNOWN" in factor.limitations
    assert "CORPORATE_ACTION_PROVENANCE_UNAVAILABLE" in factor.limitations
    assert "TICKER_IDENTITY_HISTORY_UNAVAILABLE" in factor.limitations


def test_missing_historical_artifact_remains_unavailable_not_zero(tmp_path: Path) -> None:
    catalog = inspect_research_home_catalog(root=tmp_path)
    factor = next(item for item in catalog.artifacts if item.key == "factor_evidence")

    assert factor.state is EvidenceState.UNAVAILABLE
    assert factor.result_identity is None
    assert factor.as_of is None
    assert factor.limitations == ()
