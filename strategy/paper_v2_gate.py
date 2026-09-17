from __future__ import annotations

"""Paper V2 gate with observational Sector RS 60D telemetry.

Sector RS is deliberately NOT part of QUALITY_FEATURES and therefore cannot
change the Q70 score or gate decision in this version.
"""

from dataclasses import dataclass
from typing import Any, Iterable
import math

import numpy as np

from core.sector import fetch_sector_mapping
from core.universe import get_vn100_symbols
from strategy.relative_strength_v2 import (
    DEFAULT_SECTOR_RS_PERIOD,
    calculate_sector_relative_strength,
)


QUALITY_FEATURES = (
    "score",
    "relative_strength_20d",
    "adx",
)

STRATEGY_VERSION = "Q70_FROZEN"

SECTOR_RS_PERIOD = DEFAULT_SECTOR_RS_PERIOD


def _num(value: Any) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return x if math.isfinite(x) else float("nan")


def _pct_rank(values: np.ndarray, value: float) -> float:
    values = values[np.isfinite(values)]
    if not math.isfinite(value) or values.size == 0:
        return 0.5
    return float(np.searchsorted(np.sort(values), value, side="right") / values.size)


def classify_state(signal: dict[str, Any]) -> str:
    regime = str(signal.get("regime", "")).upper()
    breadth = _num(signal.get("breadth_ema50_pct"))
    breadth_change = _num(signal.get("breadth_ema50_change_10d"))

    if regime == "BEAR":
        return "BEAR"

    if regime == "BULL":
        if math.isfinite(breadth) and math.isfinite(breadth_change):
            if breadth < 50.0 and breadth_change < 0.0:
                return "DIVERGENT_BULL"
            if breadth >= 70.0 and breadth_change >= 0.0:
                return "HEALTHY_BULL"
        return "FRAGILE_BULL"

    if regime == "SIDEWAY":
        if math.isfinite(breadth) and math.isfinite(breadth_change):
            if breadth >= 60.0 and breadth_change > 0.0:
                return "RECOVERY"

    return "NEUTRAL"


@dataclass(frozen=True)
class GateDecision:
    accepted: bool
    quality: float
    state: str
    reason: str


class PaperV2QualityGate:
    def __init__(
        self,
        threshold: float = 0.70,
        *,
        sector_mapping: dict[str, str] | None = None,
        sector_universe_symbols: Iterable[str] | None = None,
    ) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError("threshold must be in (0, 1]")

        self.threshold = float(threshold)
        self.sector_mapping = sector_mapping
        self.sector_universe_symbols = (
            tuple(sector_universe_symbols)
            if sector_universe_symbols is not None
            else None
        )

    def _sector_mapping(self) -> dict[str, str]:
        if self.sector_mapping is None:
            self.sector_mapping = fetch_sector_mapping()
        return self.sector_mapping

    def _add_sector_rs_telemetry(self, signal: dict[str, Any]) -> dict[str, Any]:
        symbol = str(signal.get("symbol", "")).strip().upper()
        date = signal.get("date")

        enriched = dict(signal)

        if not symbol or date is None:
            enriched["sector_rs_60d_available"] = False
            enriched["sector_rs_60d"] = None
            return enriched

        try:
            result = calculate_sector_relative_strength(
                symbol,
                self._sector_mapping(),
                period=SECTOR_RS_PERIOD,
                as_of_date=date,
                universe_symbols=self.sector_universe_symbols,
            )
        except Exception as exc:
            # Telemetry must never change or break the gate.
            enriched["sector_rs_60d_available"] = False
            enriched["sector_rs_60d"] = None
            enriched["sector_rs_60d_error"] = str(exc)
            return enriched

        enriched["sector_rs_60d_available"] = bool(result.get("available"))
        enriched["sector_rs_60d"] = (
            None
            if result.get("relative_strength") is None
            else round(float(result["relative_strength"]), 6)
        )
        enriched["sector_rs_60d_stock_return"] = result.get("stock_return")
        enriched["sector_rs_60d_sector_return"] = result.get("benchmark_return")
        enriched["sector_rs_60d_sector"] = result.get("sector")
        enriched["sector_rs_60d_universe_size"] = result.get(
            "sector_universe_size",
            0,
        )
        enriched["sector_rs_60d_eligible_peers"] = result.get(
            "sector_eligible_count",
            0,
        )

        return enriched

    def score_components(
        self,
        signal: dict[str, Any],
        *,
        quality_universe: Iterable[dict[str, Any]],
    ) -> dict[str, float]:
        universe = list(quality_universe)

        refs = {
            feature: np.asarray(
                [_num(row.get(feature)) for row in universe],
                dtype=float,
            )
            for feature in QUALITY_FEATURES
        }

        return {
            feature: _pct_rank(
                refs[feature],
                _num(signal.get(feature)),
            )
            for feature in QUALITY_FEATURES
        }

    def score_cross_section(
        self,
        signals: Iterable[dict[str, Any]],
    ) -> dict[str, float]:
        rows = list(signals)
        refs = {
            feature: np.asarray(
                [_num(row.get(feature)) for row in rows],
                dtype=float,
            )
            for feature in QUALITY_FEATURES
        }

        scores: dict[str, float] = {}
        for row in rows:
            symbol = str(row.get("symbol", "")).strip().upper()
            if not symbol:
                continue

            components = [
                _pct_rank(refs[feature], _num(row.get(feature)))
                for feature in QUALITY_FEATURES
            ]
            scores[symbol] = float(np.mean(components))

        return scores

    def decide(
        self,
        signal: dict[str, Any],
        *,
        quality: float,
    ) -> GateDecision:
        state = classify_state(signal)

        if state == "BEAR":
            return GateDecision(False, quality, state, "BEAR")

        if state == "DIVERGENT_BULL":
            return GateDecision(
                False,
                quality,
                state,
                "DIVERGENT_BULL",
            )

        if quality < self.threshold:
            return GateDecision(
                False,
                quality,
                state,
                f"quality<{self.threshold:.2f}",
            )

        return GateDecision(
            True,
            quality,
            state,
            f"Q{self.threshold:.2f}_PASS",
        )

    def apply(
        self,
        signals: list[dict[str, Any]],
        *,
        quality_universe: Iterable[dict[str, Any]] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        universe = (
            list(quality_universe)
            if quality_universe is not None
            else list(signals)
        )
        quality_scores = self.score_cross_section(universe)
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []

        for signal in signals:
            symbol = str(signal.get("symbol", "")).strip().upper()
            quality = quality_scores.get(symbol, 0.0)
            decision = self.decide(signal, quality=quality)

            enriched = self._add_sector_rs_telemetry(signal)
            enriched["strategy_version"] = STRATEGY_VERSION
            enriched["paper_v2_quality"] = round(quality, 6)
            enriched["paper_v2_state"] = decision.state
            enriched["paper_v2_gate"] = decision.reason

            if decision.accepted:
                accepted.append(enriched)
            else:
                rejected.append(enriched)

        return accepted, rejected
