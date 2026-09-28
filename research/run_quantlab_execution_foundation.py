from __future__ import annotations

"""Publish the Phase 11A neutral execution capability foundation."""

import argparse
import csv
from dataclasses import fields
from enum import Enum
from hashlib import sha256
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quantlab.execution import build_execution_foundation_result
from quantlab.identity import canonical_identity_value, canonical_json


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "research_results/quantlab_execution_foundation"
FILES = (
    "execution_capability_matrix.csv",
    "execution_contract_manifest.json",
    "execution_foundation_report.md",
)


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024): digest.update(chunk)
    return digest.hexdigest()


def _rows(items: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [{field.name: (getattr(item, field.name).value if isinstance(getattr(item, field.name), Enum) else getattr(item, field.name)) for field in fields(item)} for item in items]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]), lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def _report(result) -> str:
    lines = [
        "# Phase 11A Neutral Execution Foundation", "",
        "Research contracts only. No executable order, broker fill, portfolio mutation, or production integration is authorized.", "",
        "## Pipeline boundary", "",
        "`FROZEN TARGET PORTFOLIO -> THEORETICAL ORDER INTENT -> MARKET CONSTRAINT TRANSFORMATION -> EXECUTABLE ORDER PLAN -> FRICTION MODEL -> EXECUTION RESULT`", "",
        "Phase 11A implements only theoretical order intent and descriptive daily-volume participation. Later layers remain explicit capability states.", "",
        "| Area | Capability | Evidence | Implementation |", "|---|---|---|---|",
    ]
    for item in result.capabilities:
        lines.append(f"| {item.area} | {item.capability} | {item.state.value} | {item.implementation_state.value} |")
    lines.extend(("", "## Price and timing", "", "Known formation-date or earlier prices are reference values only. The repository supports research-time next-session-open semantics, but Phase 11A does not assume a future open or realized fill.", "", "## Limitations", ""))
    lines.extend(f"- {item}" for item in result.limitations); lines.append("")
    return "\n".join(lines)


def run_execution_foundation(*, output_root: str | Path = DEFAULT_OUTPUT_ROOT):
    output = Path(output_root).resolve()
    if output.exists(): raise FileExistsError(f"output directory already exists: {output}")
    if output in {PROJECT_ROOT, PROJECT_ROOT / "research_results"}: raise ValueError("output root must be a dedicated Phase 11A directory")
    result = build_execution_foundation_result()
    rows = _rows(result.capabilities)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:
        _write_csv(temporary / FILES[0], rows)
        (temporary / FILES[2]).write_text(_report(result), encoding="utf-8", newline="\n")
        artifacts = {
            FILES[0]: {"rows": len(rows), "sha256": _file_hash(temporary / FILES[0])},
            FILES[2]: {"rows": 1, "sha256": _file_hash(temporary / FILES[2])},
        }
        manifest = {
            "contract": result.contract_name, "version": result.contract_version,
            "specification_fingerprint": result.specification_fingerprint,
            "friction_specification_fingerprint": result.friction_fingerprint,
            "result_identity": result.identity,
            "pipeline_layers": ["FROZEN_TARGET_PORTFOLIO", "THEORETICAL_ORDER_INTENT", "MARKET_CONSTRAINT_TRANSFORMATION", "EXECUTABLE_ORDER_PLAN", "FRICTION_MODEL", "EXECUTION_RESULT_OR_UNAVAILABLE_EVIDENCE"],
            "implemented_layers": ["THEORETICAL_ORDER_INTENT", "DESCRIPTIVE_DAILY_VOLUME_PARTICIPATION"],
            "historical_data_schema": {"frequency": "daily", "fields": ["open", "high", "low", "close", "volume"], "traded_value": "UNAVAILABLE", "adjustment_flag": "UNAVAILABLE"},
            "market_rule_states": {"lot_size": "CURRENT_OPERATIONAL_ONLY", "tick_size": "EVIDENCE_UNAVAILABLE", "price_band": "EVIDENCE_UNAVAILABLE", "tradability": "EVIDENCE_UNAVAILABLE"},
            "friction_states": {"commission_fee": "UNAVAILABLE_CANONICAL_HISTORY", "sell_side_tax": "UNAVAILABLE_CANONICAL_HISTORY", "spread_slippage": "UNAVAILABLE_MEASURED_HISTORY", "market_impact": "EVIDENCE_UNAVAILABLE"},
            "settlement_state": "PARTIAL_CURRENT_OPERATIONAL_ONLY_NO_HISTORICAL_SCHEDULE",
            "corporate_action_state": "EVIDENCE_UNAVAILABLE",
            "execution_price_provenance": "PARTIAL",
            "market_impact_model": "EVIDENCE_UNAVAILABLE",
            "no_operational_execution": True, "no_policy_ranking_or_optimization": True,
            "upstream_isolation": ["Phase 6 portfolios", "Phase 10 risk policies and artifacts", "Forward V1", "paper trading", "scanner", "Telegram", "production execution"],
            "repository_audit_classifications": {
                "execution_models_and_broker_interface": "B_STRATEGY_SPECIFIC_OPERATIONAL",
                "paper_broker_and_signal_executor": "D_PAPER_LIVE_OPERATIONAL",
                "backtest_cost_lot_and_fill_rules": "C_BACKTEST_ASSUMPTION",
                "market_snapshot_and_daily_ohlcv": "A_REUSABLE_NEUTRAL_INFRA",
                "legacy_breadth_participation_runner": "E_LEGACY_OR_DUPLICATE_WITH_REUSABLE_DESCRIPTIVE_PRIMITIVE",
                "historical_market_rules_and_microstructure": "G_EVIDENCE_UNAVAILABLE",
            },
            "artifacts": artifacts, "limitations": list(result.limitations), "completed": True,
        }
        (temporary / FILES[1]).write_bytes(canonical_json(canonical_identity_value(manifest)) + b"\n")
        temporary.replace(output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True); raise
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args(argv)
    result = run_execution_foundation(output_root=args.output_root)
    print(f"Phase 11A complete: {len(result.capabilities)} capability records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
