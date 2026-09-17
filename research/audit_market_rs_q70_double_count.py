from __future__ import annotations

"""Audit Market RS double-counting inside the V2 Q70 gate.

This is research-only. It does NOT modify production code.

Question:
    relative_strength_20d already contributes to the base signal score.
    Q70 then uses BOTH:
      1) the resulting score, and
      2) relative_strength_20d directly.
    How much does one change in Market RS move both layers?

Run from the repo root:
    py -m research.audit_market_rs_q70_double_count
"""

import numpy as np
import pandas as pd

from strategy.paper_v2_gate import PaperV2QualityGate
from strategy.scoring import calculate_score


RS_VALUES = (-5.0, 0.0, 3.0, 6.0, 10.0, 15.0)


def _base_latest() -> pd.Series:
    return pd.Series(
        {
            "close": 110.0,
            "EMA10": 105.0,
            "EMA20": 100.0,
            "EMA50": 95.0,
            "Vol_Ratio": 1.5,
            "RSI": 60.0,
            "ADX14": 30.0,
            "EMA20_Rising": True,
            "Breakout_20D": True,
            "Volume_Breakout_5D": True,
            "Green_Candle": True,
            "Close_Upper_Half": True,
            "Body_Ratio": 0.50,
        }
    )


def _reference_signals() -> list[dict]:
    """Independent frozen-style reference distribution for the audit.

    The exact V2 gate uses a cross-sectional percentile distribution.
    This synthetic distribution is only used to isolate the mechanism;
    it is not a performance claim.
    """
    rows: list[dict] = []
    for i in range(101):
        rows.append(
            {
                "symbol": f"REF{i:03d}",
                "score": float(40 + i * 0.6),
                "relative_strength_20d": float(-10 + i * 0.3),
                "adx": float(10 + i * 0.3),
                "volume_ratio": float(0.8 + i * 0.017),
            }
        )
    return rows


def _audit() -> pd.DataFrame:
    latest = _base_latest()
    refs = _reference_signals()
    gate = PaperV2QualityGate(threshold=0.70)

    rows = []

    for rs in RS_VALUES:
        score, _ = calculate_score(latest, rs)

        target = {
            "symbol": "TARGET",
            "score": score,
            "relative_strength_20d": rs,
            "adx": 30.0,
            "volume_ratio": 1.5,
        }

        quality = gate.score_cross_section(refs + [target])["TARGET"]

        rows.append(
            {
                "relative_strength_20d": rs,
                "signal_score": score,
                "quality_q70": quality,
                "q70_delta_vs_previous": np.nan,
            }
        )

    result = pd.DataFrame(rows)
    result["q70_delta_vs_previous"] = result["quality_q70"].diff()
    return result


def main() -> None:
    result = _audit()

    print("\n=== MARKET RS -> Q70 DOUBLE-COUNT AUDIT ===")
    print(
        "One synthetic candidate is held constant except for "
        "relative_strength_20d."
    )
    print(
        "Q70 uses score + relative_strength_20d + ADX + volume_ratio "
        "as separate features."
    )
    print()
    print(result.to_string(index=False, float_format=lambda x: f"{x:.4f}"))

    score_span = result["signal_score"].max() - result["signal_score"].min()
    q_span = result["quality_q70"].max() - result["quality_q70"].min()

    print("\nSUMMARY")
    print(f"Signal-score span across RS grid: {score_span:.1f} points")
    print(f"Q70 span across RS grid:          {q_span:.4f}")

    print("\nINTERPRETATION")
    print(
        "Market RS is represented twice in the current architecture: "
        "once inside the base signal score and once as its own Q70 feature."
    )
    print(
        "This audit quantifies mechanical contribution only; it does NOT "
        "show that the duplication is harmful or that a production change "
        "is justified."
    )
    print(
        "Next decision requires an OOS ablation of the Q70 architecture, "
        "not a policy change from this synthetic audit alone."
    )


if __name__ == "__main__":
    main()
