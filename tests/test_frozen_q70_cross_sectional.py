from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from quantlab.alpha.frozen_q70 import FrozenQ70EvaluationRow, score_frozen_q70_batch
from quantlab.features import PointInTimeUniverseContext


DAY = "2024-01-02"


def _context() -> PointInTimeUniverseContext:
    return PointInTimeUniverseContext.static(["AAA", "BBB", "FAIL"], [DAY])


def _row(symbol: str, *, score=50, rs=5, adx=20, passed=True, key=None):
    return FrozenQ70EvaluationRow(symbol, DAY, score, rs, adx, passed, key or symbol)


def test_percentiles_ties_reference_rows_and_threshold_parity() -> None:
    result = score_frozen_q70_batch([_row("AAA", score=50, rs=0, adx=0), _row("BBB", score=100, rs=100, adx=100), _row("FAIL", score=100, rs=100, adx=100, passed=False)], signal_date=DAY, market_state="HEALTHY_BULL", universe_context=_context())
    decision = next(item for item in result.decisions if item.symbol == "AAA")
    assert decision.component_percentiles["score"] == pytest.approx(1 / 3)
    assert not decision.accepted and decision.reason == "quality<0.70"
    assert result.counts["eligible_reference_rows"] == 3 and result.counts["base_entry_candidates"] == 2
    assert next(item for item in result.reference_records if item.symbol == "FAIL").reason == "reference_only"


@pytest.mark.parametrize(("state", "reason"), [("BEAR", "BEAR"), ("DIVERGENT_BULL", "DIVERGENT_BULL"), ("NEUTRAL", "quality<0.70"), ("UNKNOWN", "quality<0.70")])
def test_state_policy_reasons_and_nonfinite_values(state: str, reason: str) -> None:
    result = score_frozen_q70_batch([_row("AAA", score=float("nan"), rs=float("inf"), adx=float("-inf"))], signal_date=DAY, market_state=state, universe_context=_context())
    decision = result.decisions[0]
    assert decision.component_percentiles == {"score": .5, "relative_strength_20d": .5, "adx": .5}
    assert decision.reason == reason


def test_ineligible_vnindex_mixed_dates_identity_and_import_safety(tmp_path: Path) -> None:
    context = _context()
    result = score_frozen_q70_batch([_row("AAA")], signal_date=DAY, market_state="NEUTRAL", universe_context=context)
    changed = PointInTimeUniverseContext.from_memberships(universe_mode="static", memberships={DAY: ("AAA",)})
    changed_result = score_frozen_q70_batch([_row("AAA", score=51)], signal_date=DAY, market_state="NEUTRAL", universe_context=changed)
    assert result.batch_identity != changed_result.batch_identity
    with pytest.raises(ValueError, match="non-benchmark"):
        _row("VNINDEX")
    with pytest.raises(ValueError, match="share"):
        score_frozen_q70_batch([_row("AAA"), FrozenQ70EvaluationRow("BBB", "2024-01-03", 1, 1, 1, True)], signal_date=DAY, market_state="NEUTRAL", universe_context=context)
    missing = tmp_path / "not-created.db"
    completed = subprocess.run([sys.executable, "-c", "import quantlab.alpha"], cwd=Path(__file__).resolve().parents[1], env=dict(os.environ, MARKET_DATABASE_PATH=str(missing)), capture_output=True, text=True)
    assert completed.returncode == 0 and not missing.exists()
