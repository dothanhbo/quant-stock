from __future__ import annotations

from typing import Any

from strategy.breadth_exposure import (
    V3_BREADTH_VERSION,
    breadth_exposure_multiplier,
)
from strategy.paper_v2_gate import PaperV2QualityGate


class PaperV3Scanner:
    """V2 frozen signal gate plus market-breadth exposure control.

    V3 deliberately does not alter Q70, Donchian, entry timing, or exits.
    It only annotates accepted signals with a point-in-time exposure
    multiplier. Signals in the <40% breadth bucket are not queued for a new
    position and are retained in diagnostics.
    """

    VERSION = V3_BREADTH_VERSION

    def __init__(self, threshold: float = 0.70) -> None:
        self.gate = PaperV2QualityGate(threshold)

    def process(
        self,
        signals: list[dict[str, Any]],
        stats: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        quality_universe = stats.get("evaluations", [])
        accepted, rejected = self.gate.apply(
            signals,
            quality_universe=quality_universe,
        )

        v3_accepted: list[dict[str, Any]] = []
        breadth_blocked: list[dict[str, Any]] = []
        for signal in accepted:
            enriched = dict(signal)
            breadth = enriched.get("breadth_ema50_pct")
            multiplier = breadth_exposure_multiplier(breadth)
            enriched["strategy_version"] = self.VERSION
            enriched["breadth_exposure_multiplier"] = multiplier
            enriched["breadth_exposure_applied"] = True
            enriched["breadth_exposure_pct"] = multiplier * 100.0
            enriched["paper_v3_breadth_gate"] = (
                "FULL" if multiplier == 1.0
                else "HALF" if multiplier == 0.5
                else "BLOCK"
            )

            if multiplier <= 0.0:
                blocked = dict(enriched)
                blocked["paper_v3_breadth_gate"] = "BREADTH_BLOCK"
                breadth_blocked.append(blocked)
            else:
                v3_accepted.append(enriched)

        # Re-label V2 rejects as V3 telemetry without changing the underlying
        # Q70 reason.  They remain rejected for the same V2 reason.
        v3_rejected = []
        for signal in rejected:
            enriched = dict(signal)
            enriched["strategy_version"] = self.VERSION
            enriched["breadth_exposure_applied"] = False
            enriched["breadth_exposure_multiplier"] = None
            enriched["breadth_exposure_pct"] = None
            enriched["paper_v3_breadth_gate"] = "NOT_APPLICABLE"
            v3_rejected.append(enriched)

        stats = dict(stats)
        stats["paper_v3_version"] = self.VERSION
        stats["paper_v3_rejected"] = v3_rejected
        stats["paper_v3_breadth_blocked"] = breadth_blocked
        stats["paper_v3_full_exposure"] = sum(
            item["breadth_exposure_multiplier"] == 1.0
            for item in v3_accepted
        )
        stats["paper_v3_half_exposure"] = sum(
            item["breadth_exposure_multiplier"] == 0.5
            for item in v3_accepted
        )
        stats["paper_v3_zero_exposure"] = len(breadth_blocked)
        stats["paper_v3_quality_threshold"] = self.gate.threshold
        stats["paper_v3_quality_universe"] = len(quality_universe)
        stats["paper_v3_quality_scored"] = len(
            self.gate.score_cross_section(quality_universe)
        )

        return v3_accepted, stats
