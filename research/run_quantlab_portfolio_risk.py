from __future__ import annotations

"""Publish causal Phase 10A diagnostics for frozen Phase 6 portfolios."""

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
from quantlab.identity import canonical_identity_value, canonical_json
from quantlab.portfolio import (
    NEUTRAL_PORTFOLIO_RISK_V1,
    FrozenRiskPortfolio,
    FrozenRiskPosition,
    PortfolioRiskResult,
    evaluate_portfolio_risk,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PHASE6_ROOT = PROJECT_ROOT / "research_results/quantlab_portfolio_construction_2018-08-07_2026-09-17"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "research_results/quantlab_portfolio_risk_2018-08-07_2026-09-17"
FILES = (
    "portfolio_risk_by_date.csv",
    "portfolio_risk_summary.csv",
    "portfolio_risk_component_contributions.csv",
    "portfolio_risk_manifest.json",
    "portfolio_risk_report.md",
)


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(items: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [{field.name: (getattr(item, field.name).value if isinstance(getattr(item, field.name), Enum) else "" if getattr(item, field.name) is None else getattr(item, field.name)) for field in fields(item)} for item in items]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty artifact: {path.name}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]), lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def _load_phase6(root: Path) -> tuple[tuple[FrozenRiskPortfolio, ...], dict[str, str], dict[str, Any]]:
    manifest_path = root / "portfolio_construction_manifest.json"
    daily_path = root / "portfolio_construction_by_date.csv"
    positions_path = root / "portfolio_positions.csv"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("completed") or manifest.get("construction_contract") != "quantlab.neutral_portfolio_construction":
        raise ValueError("Phase 6 manifest is incomplete or incompatible")
    positions: dict[tuple[str, int], list[FrozenRiskPosition]] = {}
    with positions_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            key = (row["session_date"], int(row["requested_budget"]))
            positions.setdefault(key, []).append(FrozenRiskPosition(row["symbol"], float(row["weight"]), row["position_identity"]))
    portfolios: list[FrozenRiskPortfolio] = []
    with daily_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            key = (row["session_date"], int(row["requested_budget"]))
            portfolios.append(FrozenRiskPortfolio(
                row["session_date"], key[1], row["weighting_policy"], tuple(positions.get(key, ())),
                float(row["gross_weight"]), float(row["cash_weight"]), row["identity"],
            ))
    expected = int(manifest["artifacts"]["portfolio_construction_by_date.csv"])
    if len(portfolios) != expected:
        raise ValueError("Phase 6 daily row count does not match its manifest")
    source = {
        "phase6_manifest_sha256": _file_hash(manifest_path),
        "phase6_result_identity": str(manifest["result_identity"]),
        "phase6_specification_fingerprint": str(manifest["specification_fingerprint"]),
        "phase6_daily_sha256": _file_hash(daily_path),
        "phase6_positions_sha256": _file_hash(positions_path),
    }
    return tuple(portfolios), source, manifest


def _report(result: PortfolioRiskResult) -> str:
    lines = [
        "# Phase 10A Neutral Portfolio Risk Diagnostics", "",
        "Descriptive diagnostics only. No portfolio was ranked, optimized, filtered, or reweighted.", "",
        "Sector evidence: `SECTOR_EVIDENCE_UNAVAILABLE` (no canonical point-in-time sector history).", "",
        "| Budget | Scope | Observations | Covariance defined | Median annualized volatility | Median pairwise correlation | Median beta |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    shown = lambda value: "—" if value is None else f"{value:.8f}"
    for item in result.summaries:
        lines.append(f"| {item.requested_budget} | {item.scope_name} | {item.observation_count} | {item.covariance_defined_count} | {shown(item.median_annualized_portfolio_volatility)} | {shown(item.median_pairwise_correlation)} | {shown(item.median_benchmark_beta)} |")
    lines.extend(("", "## Limitations", "")); lines.extend(f"- {item}" for item in result.limitations); lines.append("")
    return "\n".join(lines)


def run_portfolio_risk(
    *,
    phase6_root: str | Path = DEFAULT_PHASE6_ROOT,
    database_path: str | Path | None = None,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> PortfolioRiskResult:
    phase6, output = Path(phase6_root).resolve(), Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"output directory already exists: {output}")
    if output in {PROJECT_ROOT, PROJECT_ROOT / "research_results", phase6}:
        raise ValueError("output root must be a new dedicated Phase 10A directory")
    portfolios, sources, phase6_manifest = _load_phase6(phase6)
    frozen = tuple((item.session_date, item.requested_budget, tuple((position.symbol, position.weight) for position in item.positions)) for item in portfolios)
    snapshot = build_market_data_snapshot(database_path)
    result = evaluate_portfolio_risk(portfolios, snapshot, source_identities=sources)
    if frozen != tuple((item.session_date, item.requested_budget, tuple((position.symbol, position.weight) for position in item.positions)) for item in portfolios):
        raise ValueError("Phase 10A mutated frozen Phase 6 portfolios")
    rows = {
        FILES[0]: _rows(result.observations), FILES[1]: _rows(result.summaries),
        FILES[2]: _rows(result.component_contributions),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:
        for name, values in rows.items(): _write_csv(temporary / name, values)
        (temporary / FILES[4]).write_text(_report(result), encoding="utf-8", newline="\n")
        artifacts = {name: {"rows": len(values), "sha256": _file_hash(temporary / name)} for name, values in rows.items()}
        artifacts[FILES[4]] = {"rows": 1, "sha256": _file_hash(temporary / FILES[4])}
        manifest = {
            "contract": result.contract_name, "version": result.contract_version,
            "specification_fingerprint": result.specification_fingerprint,
            "result_identity": result.identity, "source_identities": dict(result.source_identities),
            "source_phase6_manifest_identity": sources["phase6_manifest_sha256"],
            "source_phase6_result_identity": phase6_manifest["result_identity"],
            "market": {"database_path": str(snapshot.canonical_db_path), "snapshot_id": snapshot.snapshot_id, "logical_content_fingerprint": snapshot.logical_content_fingerprint},
            "risk_specification": {
                "trailing_market_sessions": NEUTRAL_PORTFOLIO_RISK_V1.trailing_market_sessions,
                "minimum_return_observations": NEUTRAL_PORTFOLIO_RISK_V1.minimum_return_observations,
                "return_definition": "exact consecutive VNINDEX-session close-to-close decimal; no forward fill",
                "covariance_definition": "complete-case sample covariance, ddof=1",
                "annualization_factor": NEUTRAL_PORTFOLIO_RISK_V1.annualization_factor,
                "benchmark": NEUTRAL_PORTFOLIO_RISK_V1.benchmark_symbol,
            },
            "sector_evidence_state": "SECTOR_EVIDENCE_UNAVAILABLE",
            "artifacts": artifacts, "limitations": list(result.limitations),
            "no_future_outcomes_used": True, "no_portfolio_optimization_or_ranking": True,
            "completed": True,
        }
        (temporary / FILES[3]).write_bytes(canonical_json(canonical_identity_value(manifest)) + b"\n")
        temporary.replace(output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True); raise
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase6-root", type=Path, default=DEFAULT_PHASE6_ROOT)
    parser.add_argument("--database-path", type=Path)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args(argv)
    result = run_portfolio_risk(phase6_root=args.phase6_root, database_path=args.database_path, output_root=args.output_root)
    print(f"Phase 10A complete: {len(result.observations)} observations, {len(result.component_contributions)} component rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
