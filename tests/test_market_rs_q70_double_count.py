from __future__ import annotations

import pytest

from research.audit_market_rs_q70_double_count import _audit


def test_market_rs_moves_base_score_and_q70():
    result = _audit()

    scores = result["signal_score"].tolist()
    qualities = result["quality_q70"].tolist()

    # The current scoring.py gives RS a stepped contribution.
    assert scores == [77, 85, 89, 93, 97, 97]

    # Q70 also consumes relative_strength_20d directly, so quality
    # should rise monotonically across this synthetic RS grid.
    assert all(
        later >= earlier
        for earlier, later in zip(qualities, qualities[1:])
    )


def test_q70_has_a_direct_rs_effect():
    result = _audit()

    delta = result["quality_q70"].iloc[-1] - result["quality_q70"].iloc[0]

    assert delta > 0.0


def test_audit_does_not_modify_production_contract():
    result = _audit()

    assert list(result.columns) == [
        "relative_strength_20d",
        "signal_score",
        "quality_q70",
        "q70_delta_vs_previous",
    ]
    assert len(result) == 6
