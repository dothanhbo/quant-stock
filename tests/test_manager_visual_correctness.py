from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from manager.formatting import (
    UNAVAILABLE,
    format_fraction_percent,
    format_ic,
    format_percent,
    format_percentage_points,
    format_vnd,
)
from manager.charts import grouped_bar_chart
from manager.pages.compare import _paired_block_chart_rows
from manager.pages.forward_evidence import _paper_display_mode, _paper_equity_chart_rows
from manager.pages.portfolio_risk import _cost_chart_rows, _risk_profile_rows
from quantctl.evidence_compare import (
    BlockPairEvidence,
    ComparableEvidence,
    ComparisonFamily,
    ComparisonState,
    EvidenceComparison,
)
from quantctl.portfolio_risk import CostSensitivityPoint, RiskProfile


def _comparison() -> EvidenceComparison:
    evidence_a = ComparableEvidence("ADX", "a", None, 0.1, 0.6, 95.0, 1.2, "MIXED")
    evidence_b = ComparableEvidence("RSI", "b", None, -0.1, 0.4, 90.0, -0.8, "MIXED")
    return EvidenceComparison(
        ComparisonState.AVAILABLE,
        ComparisonFamily.NEUTRAL_FACTORS,
        "adx_14",
        "rsi_14",
        5,
        "excess_forward_return_pct_points",
        "whole_period",
        evidence_a,
        evidence_b,
        (
            BlockPairEvidence("early_2018_2020", 0.1, -0.1, 1.2, -0.8, 95.0, 90.0),
            BlockPairEvidence("recent_2025_2026", 0.2, 0.05, 0.7, 0.1, 92.0, 91.0),
        ),
        0.2,
        2.0,
        (),
        None,
        (),
        (),
        (),
        (),
        (Path("summary.csv"),),
        ("identity",),
        (),
        "persisted comparison",
    )


def test_schema_bound_numeric_formatters_do_not_infer_units_from_magnitude() -> None:
    assert format_fraction_percent(0.98) == "98.00%"
    assert format_percent(0.98) == "0.98%"
    assert format_percentage_points(0.98) == "0.980 pp"
    assert format_ic(0.012345) == "0.0123"
    assert format_vnd(100_000_000.0) == "100,000,000 VND"
    assert format_percent(float("nan")) == UNAVAILABLE


def test_compare_block_chart_rows_are_grouped_long_form_with_short_period_labels() -> None:
    rows = _paired_block_chart_rows(_comparison(), metric="ic")

    assert len(rows) == 4
    assert tuple(rows[0]) == ("Block", "Candidate", "Mean rank IC")
    assert {row["Candidate"] for row in rows} == {"A · ADX", "B · RSI"}
    assert {row["Block"] for row in rows} == {"2018–20", "2025–26"}
    assert all("A" not in row and "B" not in row for row in rows)


def test_risk_profile_projection_keeps_incompatible_units_in_separate_series() -> None:
    profiles = (RiskProfile(5, 0.20, 0.31, 0.82, 0.41, 4.2),)

    volatility = _risk_profile_rows(profiles, field="annualized_volatility", label="Volatility (%)", scale=100.0)
    beta = _risk_profile_rows(profiles, field="benchmark_beta", label="Beta")

    assert volatility == ({"Budget": "Top 5", "Volatility (%)": 20.0},)
    assert beta == ({"Budget": "Top 5", "Beta": 0.82},)
    assert set(volatility[0]).isdisjoint({"Beta", "Correlation", "Contributors"})


def test_cost_chart_uses_only_actual_persisted_basis_point_categories() -> None:
    points = (
        CostSensitivityPoint(0, 0.30, 0.30, 0.0, "contract", "a"),
        CostSensitivityPoint(25, 0.30, 0.275, 0.025, "contract", "b"),
    )

    rows = _cost_chart_rows(points)

    assert tuple(dict.fromkeys(row["Hypothetical cost rate"] for row in rows)) == ("0 bps", "25 bps")
    assert {row["Series"] for row in rows} == {"Gross excess return", "Net excess return"}


def test_single_paper_observation_is_snapshot_and_equity_mapping_is_exact() -> None:
    point = SimpleNamespace(session="2026-09-30", equity=100_000_000.0)
    model = SimpleNamespace(points=(point,))

    assert _paper_display_mode(model) == "SNAPSHOT"
    assert _paper_equity_chart_rows(model.points) == (
        {"Session": "2026-09-30", "Series": "Equity", "Value (VND)": 100_000_000.0},
    )


def test_missing_paper_observations_remain_empty_not_zero() -> None:
    assert _paper_display_mode(SimpleNamespace(points=())) == "EMPTY"


def test_grouped_chart_spec_binds_offset_zero_baseline_and_tooltip_precision() -> None:
    chart = grouped_bar_chart(
        _paired_block_chart_rows(_comparison(), metric="ic"),
        category="Block",
        series="Candidate",
        value="Mean rank IC",
        y_title="Mean rank IC",
        value_format=".4f",
    ).to_dict()

    assert chart["encoding"]["xOffset"]["field"] == "Candidate"
    assert chart["encoding"]["y"]["scale"]["zero"] is True
    assert chart["encoding"]["tooltip"][2]["format"] == ".4f"
