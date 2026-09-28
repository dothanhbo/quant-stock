from __future__ import annotations

"""Publish causal Phase 11B formation-to-next-session execution evidence."""

import argparse
import csv
from dataclasses import fields
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quantlab.catalog import build_market_data_snapshot
from quantlab.execution import (
    CAPITAL_SEMANTICS,
    CAUSAL_EXECUTION_TIMING_CAPACITY_V1,
    PARTICIPATION_INTERPRETATION,
    DailyExecutionMarketEvidence,
    FrozenExecutionTargetPortfolio,
    FrozenExecutionTargetPosition,
    evaluate_execution_timing_capacity,
)
from quantlab.execution.timing_capacity import (
    EXPECTED_PHASE11A_FRICTION,
    EXPECTED_PHASE11A_RESULT,
    EXPECTED_PHASE11A_SPEC,
    EXPECTED_PHASE6_RESULT,
    EXPECTED_PHASE6_SPEC,
)
from quantlab.identity import canonical_identity_value, canonical_json


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PHASE11A_ROOT = PROJECT_ROOT / "research_results/quantlab_execution_foundation"
DEFAULT_PHASE6_ROOT = PROJECT_ROOT / "research_results/quantlab_portfolio_construction_2018-08-07_2026-09-17"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "research_results/quantlab_execution_timing_capacity_2018-08-07_2026-09-17"
EXPECTED_PHASE6_MANIFEST_SHA256 = "70677377bb4c6f8feaa4774b519ca72b3b1a05d4fb4760fb9e7ed8d915879cba"
EXPECTED_PHASE6_DAILY_SHA256 = "461d058cd7ac13ca32eb164e3070fda4bb12202e451e221d8009a19f2f5da1a4"
EXPECTED_PHASE6_POSITIONS_SHA256 = "62ce0c7be3b48903498bc72a064d34bf0e81c483d049d14e8efe72991c5c870c"
FILES = (
    "execution_timing_capacity_by_target.csv",
    "execution_timing_capacity_summary.csv",
    "execution_timing_capacity_by_block.csv",
    "execution_timing_capacity_manifest.json",
    "execution_timing_capacity_report.md",
)


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024): digest.update(chunk)
    return digest.hexdigest()


def _require_hash(path: Path, expected: str, label: str) -> None:
    actual = _file_hash(path)
    if actual != expected: raise ValueError(f"{label} SHA-256 mismatch: expected {expected}, got {actual}")


def _load_phase11a(root: Path) -> tuple[dict[str, Any], str]:
    path = root / "execution_contract_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not manifest.get("completed") or manifest.get("result_identity") != EXPECTED_PHASE11A_RESULT or manifest.get("specification_fingerprint") != EXPECTED_PHASE11A_SPEC or manifest.get("friction_specification_fingerprint") != EXPECTED_PHASE11A_FRICTION:
        raise ValueError("canonical Phase 11A provenance mismatch")
    for name, metadata in manifest.get("artifacts", {}).items(): _require_hash(root / name, metadata["sha256"], f"Phase 11A {name}")
    return manifest, _file_hash(path)


def _load_phase6(root: Path) -> tuple[tuple[FrozenExecutionTargetPortfolio, ...], dict[str, Any], dict[str, str]]:
    manifest_path = root / "portfolio_construction_manifest.json"
    daily_path = root / "portfolio_construction_by_date.csv"; positions_path = root / "portfolio_positions.csv"
    _require_hash(manifest_path, EXPECTED_PHASE6_MANIFEST_SHA256, "Phase 6 manifest")
    _require_hash(daily_path, EXPECTED_PHASE6_DAILY_SHA256, "Phase 6 daily artifact")
    _require_hash(positions_path, EXPECTED_PHASE6_POSITIONS_SHA256, "Phase 6 positions artifact")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("completed") or manifest.get("result_identity") != EXPECTED_PHASE6_RESULT or manifest.get("specification_fingerprint") != EXPECTED_PHASE6_SPEC or manifest.get("candidate_source") != "ADX_ONLY" or manifest.get("weighting_policies") != ["EQUAL_WEIGHT"] or manifest.get("budgets") != [5, 10, 20]:
        raise ValueError("canonical Phase 6 provenance mismatch")
    grouped: dict[str, list[FrozenExecutionTargetPosition]] = {}
    with positions_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            grouped.setdefault(row["portfolio_identity"], []).append(FrozenExecutionTargetPosition(row["symbol"], float(row["weight"]), row["position_identity"]))
    portfolios: list[FrozenExecutionTargetPortfolio] = []
    with daily_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["candidate_source"] != "ADX_ONLY" or row["weighting_policy"] != "EQUAL_WEIGHT": raise ValueError("unexpected Phase 6 portfolio policy")
            positions = tuple(grouped.get(row["identity"], ()))
            if len(positions) != int(row["selected_count"]): raise ValueError("Phase 6 position count does not reconcile")
            portfolios.append(FrozenExecutionTargetPortfolio(row["session_date"], int(row["requested_budget"]), positions, row["identity"]))
    if len(portfolios) != int(manifest["artifacts"]["portfolio_construction_by_date.csv"]): raise ValueError("Phase 6 daily row count mismatch")
    sources = {"phase6_manifest_sha256": EXPECTED_PHASE6_MANIFEST_SHA256, "phase6_daily_sha256": EXPECTED_PHASE6_DAILY_SHA256, "phase6_positions_sha256": EXPECTED_PHASE6_POSITIONS_SHA256}
    return tuple(portfolios), manifest, sources


def _market_evidence(snapshot, symbols: tuple[str, ...], start_date: str) -> tuple[DailyExecutionMarketEvidence, ...]:
    bundle = snapshot.load_ohlcv(("VNINDEX", *symbols), start_date=start_date, through_date=snapshot.last_session_date)
    rows: list[DailyExecutionMarketEvidence] = []
    for symbol in bundle.available_symbols:
        frame = bundle.frame_for(symbol)
        for row in frame.itertuples(index=False):
            rows.append(DailyExecutionMarketEvidence(
                symbol, row.time.strftime("%Y-%m-%d"),
                None if row.open != row.open else float(row.open),
                None if row.close != row.close else float(row.close),
                None if row.volume != row.volume else float(row.volume),
            ))
    return tuple(rows)


def _rows(items: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [{field.name: (getattr(item, field.name).value if isinstance(getattr(item, field.name), Enum) else "" if getattr(item, field.name) is None else getattr(item, field.name)) for field in fields(item)} for item in items]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows: raise ValueError(f"cannot write empty artifact: {path.name}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]), lineterminator="\n"); writer.writeheader(); writer.writerows(rows)


def _report(result) -> str:
    shown = lambda value: "—" if value is None else f"{value:.10g}"
    lines = [
        "# Phase 11B Causal Execution Timing and Capacity Evidence", "",
        "Target-acquisition evidence only. Phase 6 does not persist achieved share holdings, realized cash, or fills, so this is not rebalance-order evidence.", "",
        "Next-session open is `NEXT_SESSION_OPEN_REFERENCE`, never a fill or realized execution price.", "",
        f"Capital semantics: `{CAPITAL_SEMANTICS}`.", "", f"Participation semantics: `{PARTICIPATION_INTERPRETATION}`.", "",
        "| Budget | Targets | Timing available | Capacity available | Mean gap | Median gap | Median participation coefficient |", "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in result.summaries:
        lines.append(f"| {item.requested_budget} | {item.observation_count} | {item.timing_available_count} | {item.capacity_available_count} | {shown(item.mean_gap_decimal)} | {shown(item.median_gap_decimal)} | {shown(item.median_participation_pct_per_normalized_equity_unit)} |")
    lines.extend(("", "No fill, slippage, spread, impact, commission, tax, lot, tick, price-band, settlement, or executable-PnL model is present.", "", "## Limitations", ""))
    lines.extend(f"- {item}" for item in result.limitations); lines.append("")
    return "\n".join(lines)


def run_execution_timing_capacity(
    *, phase11a_root: str | Path = DEFAULT_PHASE11A_ROOT,
    phase6_root: str | Path = DEFAULT_PHASE6_ROOT,
    database_path: str | Path | None = None,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
):
    phase11a, phase6, output = Path(phase11a_root).resolve(), Path(phase6_root).resolve(), Path(output_root).resolve()
    if output.exists(): raise FileExistsError(f"output directory already exists: {output}")
    if output in {PROJECT_ROOT, PROJECT_ROOT/"research_results", phase11a, phase6}: raise ValueError("output root must be a dedicated Phase 11B directory")
    manifest11a, hash11a = _load_phase11a(phase11a); portfolios, manifest6, phase6_sources = _load_phase6(phase6)
    symbols = tuple(sorted({position.symbol for item in portfolios for position in item.positions}))
    snapshot = build_market_data_snapshot(database_path)
    market = _market_evidence(snapshot, symbols, manifest6["period"]["start_date"])
    sources = {
        "phase11a_result_identity": manifest11a["result_identity"], "phase11a_specification_fingerprint": manifest11a["specification_fingerprint"],
        "phase11a_friction_fingerprint": manifest11a["friction_specification_fingerprint"],
        "phase6_result_identity": manifest6["result_identity"], "phase6_specification_fingerprint": manifest6["specification_fingerprint"],
    }
    result = evaluate_execution_timing_capacity(portfolios, market, source_identities=sources, market_snapshot_identity=snapshot.snapshot_id, market_logical_content_fingerprint=snapshot.logical_content_fingerprint)
    output.parent.mkdir(parents=True, exist_ok=True); temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:
        artifact_rows = {FILES[0]: _rows(result.observations), FILES[1]: _rows(result.summaries), FILES[2]: _rows(result.block_summaries)}
        for name, rows in artifact_rows.items(): _write_csv(temporary/name, rows)
        (temporary/FILES[4]).write_text(_report(result), encoding="utf-8", newline="\n")
        artifacts = {name: {"rows": len(rows), "sha256": _file_hash(temporary/name)} for name, rows in artifact_rows.items()}
        artifacts[FILES[4]] = {"rows": 1, "sha256": _file_hash(temporary/FILES[4])}
        manifest = {
            "contract": result.contract_name, "version": result.contract_version, "specification_fingerprint": result.specification_fingerprint, "result_identity": result.identity,
            "phase11a": {"manifest_sha256": hash11a, "result_identity": manifest11a["result_identity"], "specification_fingerprint": manifest11a["specification_fingerprint"], "friction_fingerprint": manifest11a["friction_specification_fingerprint"]},
            "phase6": {**phase6_sources, "result_identity": manifest6["result_identity"], "specification_fingerprint": manifest6["specification_fingerprint"], "candidate_source": "ADX_ONLY", "weighting": "EQUAL_WEIGHT", "budgets": [5,10,20]},
            "market": {"database_path": str(snapshot.canonical_db_path), "snapshot_id": snapshot.snapshot_id, "logical_content_fingerprint": snapshot.logical_content_fingerprint, "last_session_date": snapshot.last_session_date},
            "order_state_semantics": "TARGET_ACQUISITION_NOTIONAL_NOT_REBALANCE_ORDER_NOTIONAL",
            "order_state_reason": "Phase 6 has target weights and weight turnover but no achieved share holdings, realized cash, or fill path",
            "capital_semantics": CAPITAL_SEMANTICS, "normalized_portfolio_equity": 1.0,
            "formation_reference": "exact same-date close available by formation date",
            "next_session": "first VNINDEX session strictly after formation; exact date only; no t+2 fallback or nearest-open substitution",
            "next_open_semantics": "NEXT_SESSION_OPEN_REFERENCE_NOT_FILL_PRICE",
            "gap_formula": "next_session_open / formation_reference_close - 1",
            "adverse_gap_formula": "BUY=gap; SELL=-gap; signed and untruncated",
            "participation_formula": "absolute theoretical target-acquisition shares per normalized equity unit / exact next-session daily volume * 100",
            "participation_interpretation": PARTICIPATION_INTERPRETATION,
            "unavailable_capabilities": ["fill_probability","partial_fill","realized_execution_price","spread_cost","market_impact","queue_priority","limit_order_behavior","VWAP_TWAP","broker_latency","canonical_historical_lot_tick_price_band_settlement_corporate_actions"],
            "no_fill_model": True, "no_cost_model": True, "no_ranking_or_threshold": True,
            "artifacts": artifacts, "limitations": list(result.limitations), "completed": True,
        }
        (temporary/FILES[3]).write_bytes(canonical_json(canonical_identity_value(manifest))+b"\n"); temporary.replace(output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True); raise
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--phase11a-root", type=Path, default=DEFAULT_PHASE11A_ROOT); parser.add_argument("--phase6-root", type=Path, default=DEFAULT_PHASE6_ROOT); parser.add_argument("--database-path", type=Path); parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT); args = parser.parse_args(argv)
    result = run_execution_timing_capacity(phase11a_root=args.phase11a_root, phase6_root=args.phase6_root, database_path=args.database_path, output_root=args.output_root)
    print(f"Phase 11B complete: {len(result.observations)} target-acquisition observations")
    return 0


if __name__ == "__main__": raise SystemExit(main())
