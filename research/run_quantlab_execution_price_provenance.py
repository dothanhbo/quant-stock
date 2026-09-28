from __future__ import annotations

"""Create a read-only audit of price units and corporate-action provenance."""

import argparse
import csv
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.paths import resolve_market_database_path
from quantlab.catalog import build_market_data_snapshot
from quantlab.execution.price_provenance import assess_price_provenance
from quantlab.identity import canonical_identity_value, canonical_json


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "research_results/quantlab_execution_price_provenance_2018_2026"
PHASE11A_RESULT = "ae1ad7157c598172b335085ea60693bb3cb95ce1471a4a74982d0fdfed9737d7"
PHASE11A_SPEC = "a9a0ec5bb3d8c424fc935ee439e5463ad134bfd80e800e17a7d983d58ccd9685"
PHASE11A_FRICTION = "c78a3a933b0d0bcad3b4d4e442176004721fd56451e78be09724d8231d06ae07"
PHASE11A_MANIFEST_SHA256 = "43e66e08948c4f3e69f5479a1584d72d061692461719ad16ded3ba627e1ed878"
PHASE11B_RESULT = "7363f62b313672b04fa5e2e83ab958bb103701d58ccd538ff500e3d8e093d669"
PHASE11B_SPEC = "a383c24c950753b792c30fbd62010a777d7c3388d755014e5bbf4c28659a2b71"
PHASE11B_MANIFEST_SHA256 = "7df0b35f22ad555c3c2120cf1b0b470672314457cd6ae1ea8c6ca35151cb2171"
PHASE11B_TARGETS_SHA256 = "bbdc2ae5823f3cee33929b3bd0a82480be1b0af67cc2269f377a1e8e89496769"
PRICE_PARITY_ARTIFACT = PROJECT_ROOT / "research_results/market_price_parity_audit.json"
FILES = (
    "execution_price_provenance_capability.csv",
    "execution_price_provenance_manifest.json",
    "execution_price_provenance_report.md",
)
SOURCE_FILES = (
    "core/database.py", "scripts/update_data.py", "scripts/backfill_market_data.py",
    "scripts/audit_market_price_parity.py", "execution/signal_executor.py",
    "execution/position_updater.py", "execution/lifecycle_manager.py",
    "services/notification_formatter.py", "backtesting/engine.py",
)


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_database_summary(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        tables = tuple(row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ))
        schemas = {
            table: tuple(row[1] for row in connection.execute(f'PRAGMA table_info("{table}")'))
            for table in tables
        }
        price_columns = schemas.get("prices", ())
        if not {"symbol", "time", "open", "high", "low", "close", "volume"}.issubset(price_columns):
            raise ValueError("canonical prices table does not have the expected OHLCV columns")
        row_count, symbols, first_date, last_date = connection.execute(
            "SELECT COUNT(*), COUNT(DISTINCT UPPER(TRIM(symbol))), MIN(date(time)), MAX(date(time)) FROM prices"
        ).fetchone()
        latest_vnindex = connection.execute(
            "SELECT date(time), close, COUNT(*) FROM prices WHERE UPPER(TRIM(symbol))='VNINDEX' AND date(time)=(SELECT MAX(date(time)) FROM prices WHERE UPPER(TRIM(symbol))='VNINDEX') GROUP BY date(time), close ORDER BY date(time) DESC LIMIT 1"
        ).fetchone()
        latest_date = connection.execute("SELECT MAX(date(time)) FROM prices").fetchone()[0]
        latest_symbol_count = connection.execute(
            "SELECT COUNT(DISTINCT UPPER(TRIM(symbol))) FROM prices WHERE date(time)=?", (latest_date,)
        ).fetchone()[0]
        duplicates = connection.execute(
            "SELECT COUNT(*) FROM (SELECT UPPER(TRIM(symbol)), date(time) FROM prices GROUP BY 1,2 HAVING COUNT(*)>1)"
        ).fetchone()[0]
        equity_closes = sorted(float(row[0]) for row in connection.execute(
            "SELECT close FROM prices WHERE UPPER(TRIM(symbol))<>'VNINDEX' AND close IS NOT NULL AND close>0"
        ) if row[0] is not None)
        latest_valid_vnindex = connection.execute(
            "SELECT MAX(date(time)) FROM prices WHERE UPPER(TRIM(symbol))='VNINDEX' AND close IS NOT NULL AND close>0"
        ).fetchone()[0]
        provenance_fields = tuple(sorted(
            column for column in price_columns
            if any(token in column.lower() for token in ("source", "provider", "adjust", "action", "scale", "unit"))
        ))
        action_tables = tuple(sorted(
            table for table in tables
            if any(token in table.lower() for token in ("corporate", "dividend", "split", "rights", "bonus", "action"))
        ))
        duplicate_vnindex_latest = connection.execute(
            "SELECT COUNT(*) FROM prices WHERE UPPER(TRIM(symbol))='VNINDEX' AND date(time)=?", (latest_valid_vnindex,)
        ).fetchone()[0] if latest_valid_vnindex else 0
        q = lambda p: None if not equity_closes else equity_closes[min(len(equity_closes)-1, int((len(equity_closes)-1)*p))]
        return {
            "tables": tables, "table_schemas": schemas, "row_count": row_count,
            "distinct_normalized_symbols": symbols, "first_date": first_date,
            "database_latest_date": last_date, "latest_date": latest_date,
            "latest_symbol_count": latest_symbol_count, "duplicate_normalized_symbol_dates": duplicates,
            "latest_vnindex_row": latest_vnindex,
            "latest_valid_vnindex_close_date": latest_valid_vnindex,
            "latest_vnindex_row_count_for_latest_valid_date": duplicate_vnindex_latest,
            "equity_close_distribution": {
                "observation_count": len(equity_closes), "min": equity_closes[0] if equity_closes else None,
                "p01": q(.01), "median": q(.5), "p99": q(.99), "max": equity_closes[-1] if equity_closes else None,
            },
            "persisted_provider_adjustment_or_unit_fields": provenance_fields,
            "corporate_action_related_tables": action_tables,
        }
    finally:
        connection.close()


def _csv_value(value: Any) -> Any:
    return "" if value is None else value


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def run_price_provenance_audit(*, database_path: str | Path | None = None, output_root: str | Path = DEFAULT_OUTPUT_ROOT):
    db_path = resolve_market_database_path(database_path)
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"output directory already exists: {output}")
    if output in {PROJECT_ROOT, PROJECT_ROOT / "research_results"} or output == db_path or db_path in output.parents:
        raise ValueError("output root must be a dedicated audit directory outside the market database")
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    snapshot = build_market_data_snapshot(db_path)
    database_summary = _read_database_summary(db_path)
    missing_sources = [name for name in SOURCE_FILES if not (PROJECT_ROOT / name).is_file()]
    if missing_sources:
        raise FileNotFoundError(f"required provenance source missing: {missing_sources[0]}")
    source_identities = {name: _hash_file(PROJECT_ROOT / name) for name in SOURCE_FILES}
    source_identities.update({
        "phase11a_result_identity": PHASE11A_RESULT,
        "phase11a_specification_fingerprint": PHASE11A_SPEC,
        "phase11a_friction_fingerprint": PHASE11A_FRICTION,
        "phase11a_manifest_sha256": PHASE11A_MANIFEST_SHA256,
        "phase11b_result_identity": PHASE11B_RESULT,
        "phase11b_specification_fingerprint": PHASE11B_SPEC,
        "phase11b_manifest_sha256": PHASE11B_MANIFEST_SHA256,
        "phase11b_target_observations_sha256": PHASE11B_TARGETS_SHA256,
        "prior_price_parity_audit_sha256": _hash_file(PRICE_PARITY_ARTIFACT),
    })
    source_identities.update({
        "market_snapshot_id": snapshot.snapshot_id,
        "market_logical_content_fingerprint": snapshot.logical_content_fingerprint,
        "market_database_sha256": _hash_file(db_path),
    })
    result = assess_price_provenance(source_identities=source_identities)
    prior_price_parity = json.loads(PRICE_PARITY_ARTIFACT.read_text(encoding="utf-8"))
    capability_rows = [asdict(item) for item in result.execution_capabilities]
    target_database_fingerprint = _hash_file(db_path)
    manifest: dict[str, Any] = {
        "contract": result.contract_name, "version": result.contract_version,
        "result_identity": result.identity,
        "phase11a": {"manifest_sha256": PHASE11A_MANIFEST_SHA256, "result_identity": PHASE11A_RESULT, "specification_fingerprint": PHASE11A_SPEC, "friction_fingerprint": PHASE11A_FRICTION},
        "phase11b": {"result_identity": PHASE11B_RESULT, "specification_fingerprint": PHASE11B_SPEC, "manifest_sha256": PHASE11B_MANIFEST_SHA256, "target_observations_sha256": PHASE11B_TARGETS_SHA256},
        "market": {"database_path": str(db_path), "database_sha256": target_database_fingerprint, "snapshot_id": snapshot.snapshot_id, "logical_content_fingerprint": snapshot.logical_content_fingerprint, **database_summary},
        "price_units": {
            "storage_unit_state": result.storage_unit_state,
            "storage_unit_evidence": list(result.storage_unit_evidence),
            "display_unit": result.display_unit, "operational_unit": result.operational_unit,
            "execution_notional_unit": result.execution_notional_unit,
            "vnd_conversion_formula_if_convention_is_correct": "stored_equity_price * 1000 = VND per share",
            "vnd_conversion_scale": result.vnd_conversion_scale,
            "vnd_conversion_verified": result.vnd_conversion_verified,
        },
        "existing_price_parity_evidence": {
            "artifact_path": str(PRICE_PARITY_ARTIFACT.relative_to(PROJECT_ROOT)),
            "artifact_sha256": source_identities["prior_price_parity_audit_sha256"],
            "status": prior_price_parity.get("status"),
            "sample_date": prior_price_parity.get("latest_market_date"),
            "sample_symbol_count": prior_price_parity.get("symbols_checked"),
            "raw_price_range": prior_price_parity.get("raw_close_range"),
            "claimed_scale": prior_price_parity.get("expected_price_scale_to_vnd"),
            "paper_scale": prior_price_parity.get("paper_executor_price_scale"),
            "scope_limit": "consumer-scale agreement and plausible sample median only; no independent provider reference or adjusted-vs-raw verification",
        },
        "adjustment": {"state": result.adjustment_state, "evidence": list(result.adjustment_evidence)},
        "corporate_action_capabilities": dict(result.corporate_action_capabilities),
        "execution_capabilities": capability_rows,
        "monetary_capacity_gate": result.monetary_capacity_gate,
        "phase11b_reconciliation": list(result.phase11b_reconciliation),
        "unsupported_claims": list(result.unsupported_claims),
        "source_identities": dict(result.source_identities),
        "no_network": True, "database_opened_read_only": True,
        "artifacts": {}, "completed": True,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:
        _write_csv(temporary / FILES[0], capability_rows)
        report_lines = [
            "# Phase 11C Canonical Price Unit and Corporate Action Provenance Audit", "",
            f"**Result:** `{result.identity}`", "",
            f"**Storage unit:** `{result.storage_unit_state}`. **Adjustment:** `{result.adjustment_state}`.", "",
            f"**Monetary capacity:** `{result.monetary_capacity_gate}`.", "",
            f"Existing parity artifact: `{prior_price_parity.get('latest_market_date')}`, {prior_price_parity.get('symbols_checked')} symbols, scale {prior_price_parity.get('expected_price_scale_to_vnd')}; it is a plausibility/consumer-convention check, not provider-unit or adjustment proof.", "",
            "## Evidence", "",
            *[f"- {item}" for item in result.storage_unit_evidence],
            *[f"- {item}" for item in result.adjustment_evidence], "",
            "## Database facts", "",
            f"- Rows: {database_summary['row_count']}; normalized symbols: {database_summary['distinct_normalized_symbols']}; period: {database_summary['first_date']} to {database_summary['database_latest_date']}.",
            f"- OHLCV table has no provider, adjustment, unit, or corporate-action fields. Related action tables found: {len(database_summary['corporate_action_related_tables'])}.",
            f"- Positive equity close distribution (stored values): {database_summary['equity_close_distribution']}.", "",
            "## Execution capabilities", "",
            "| Operation | State | Reason |", "|---|---|---|",
            *[f"| {item.operation} | {item.state} | {item.reason} |" for item in result.execution_capabilities], "",
            "## Phase 11B reconciliation", "",
            *[f"- {item}" for item in result.phase11b_reconciliation], "",
            "No raw-price, adjusted-price, VND-notional, corporate-action-safe holdings, traded-value, fill, or executable-PnL claim is made.", "",
        ]
        (temporary / FILES[2]).write_text("\n".join(report_lines), encoding="utf-8", newline="\n")
        manifest["artifacts"] = {
            FILES[0]: {"rows": len(capability_rows), "sha256": _hash_file(temporary / FILES[0])},
            FILES[2]: {"rows": len(report_lines), "sha256": _hash_file(temporary / FILES[2])},
        }
        (temporary / FILES[1]).write_bytes(canonical_json(canonical_identity_value(manifest)) + b"\n")
        temporary.replace(output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return result, database_summary, output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-path", type=Path)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args(argv)
    result, summary, output = run_price_provenance_audit(database_path=args.database_path, output_root=args.output_root)
    print(f"Phase 11C audit: {result.monetary_capacity_gate}; identity={result.identity}; rows={summary['row_count']}; output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
