from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from strategy.breadth_exposure import breadth_exposure_multiplier
from strategy.paper_v2_gate import PaperV2QualityGate
from strategy.scanner import scan_all_symbols


@dataclass(slots=True, frozen=True)
class TelegramQuantAnalysis:
    evaluation: dict[str, Any]
    quality: float
    gate_state: str
    gate_reason: str
    q70_passed: bool
    breadth_exposure_multiplier: float | None
    breadth_exposure_pct: float | None
    breadth_gate: str
    market_config: dict[str, Any]
    market_state: dict[str, Any]
    universe_size: int
    fresh_count: int


def analyze_symbol(symbol: str) -> TelegramQuantAnalysis:
    symbol = str(symbol).strip().upper()

    signals, scan_stats = scan_all_symbols()

    evaluations = scan_stats.get("evaluations", [])
    if not evaluations:
        raise RuntimeError("Quant Engine không trả về evaluation universe.")

    target = next(
        (
            row
            for row in evaluations
            if str(row.get("symbol", "")).strip().upper() == symbol
        ),
        None,
    )

    if target is None:
        raise ValueError(
            f"Không tìm thấy {symbol} trong evaluation universe."
        )

    gate = PaperV2QualityGate(threshold=0.70)

    quality_scores = gate.score_cross_section(evaluations)
    quality = float(quality_scores.get(symbol, 0.0))

    decision = gate.decide(target, quality=quality)

    if not decision.accepted:
        multiplier = None
        breadth_gate = "NOT_APPLICABLE"
    else:
        breadth = target.get("breadth_ema50_pct")
        multiplier = breadth_exposure_multiplier(breadth)

        if multiplier >= 1.0:
            breadth_gate = "FULL"
        elif multiplier >= 0.5:
            breadth_gate = "HALF"
        else:
            breadth_gate = "BLOCK"

    return TelegramQuantAnalysis(
        evaluation=target,
        quality=quality,
        gate_state=decision.state,
        gate_reason=decision.reason,
        q70_passed=decision.accepted,
        breadth_exposure_multiplier=multiplier,
        breadth_exposure_pct=(multiplier * 100.0 if multiplier is not None else None),
        breadth_gate=breadth_gate,
        market_config=scan_stats.get("market_config", {}),
        market_state=scan_stats.get("market_state", {}),
        universe_size=int(scan_stats.get("total_symbols", len(evaluations))),
        fresh_count=int(scan_stats.get("fresh_count", len(evaluations))),
    )