from __future__ import annotations

from typing import Any

from strategy.paper_v2_gate import PaperV2QualityGate


class PaperV2Scanner:
    """Apply the V2 quality/state gate to the production scan output."""

    VERSION = "Q70_FROZEN"

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

        stats = dict(stats)
        stats["paper_v2_rejected"] = rejected
        stats["paper_v2_quality_threshold"] = self.gate.threshold
        stats["paper_v2_version"] = self.VERSION
        stats["paper_v2_quality_universe"] = len(quality_universe)
        stats["paper_v2_quality_scored"] = len(
            self.gate.score_cross_section(quality_universe)
        )

        return accepted, stats