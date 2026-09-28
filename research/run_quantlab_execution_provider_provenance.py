from __future__ import annotations

"""Resolve the locally installed KBS Quote.history contract without network access."""

import argparse
import ast
import csv
from dataclasses import asdict
from hashlib import sha256
import importlib.metadata
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
from quantlab.execution.provider_provenance import (
    AMBIGUOUS,
    PARTIALLY_VERIFIED,
    PHASE11C_DATABASE_SHA256,
    PHASE11C_RESULT_IDENTITY,
    UNAVAILABLE,
    ProvenanceConclusion,
    ProviderWriter,
    build_provider_provenance_result,
)
from quantlab.identity import canonical_identity_value, canonical_json


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "research_results/quantlab_execution_provider_provenance_2018_2026"
FILES = (
    "execution_provider_provenance_capability.csv",
    "execution_provider_provenance_manifest.json",
    "execution_provider_provenance_report.md",
)
WRITER_PATHS = ("scripts/update_data.py", "scripts/backfill_market_data.py")
INSPECTED_PROVIDER_MODULES = (
    "vnstock/api/quote.py",
    "vnstock/explorer/kbs/quote.py",
    "vnstock/explorer/kbs/const.py",
)
HISTORICAL_REQUIREMENT = "vnstock"  # requirements.txt has no version constraint.


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _provider_distribution() -> tuple[str, Path]:
    distribution = importlib.metadata.distribution("vnstock")
    version = distribution.version
    module_path = Path(distribution.locate_file("vnstock/explorer/kbs/quote.py")).resolve()
    return version, module_path.parent.parent.parent.parent


def _history_ast(source: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "history":
            return node
    raise ValueError("installed KBS Quote.history implementation was not found")


def inspect_installed_kbs_contract(package_root: str | Path) -> dict[str, Any]:
    """Inspect local Python source text only; this never imports vnstock or calls a provider."""
    root = Path(package_root).resolve()
    module_paths = {name: root / name for name in INSPECTED_PROVIDER_MODULES}
    missing = [str(path) for path in module_paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"installed provider source is incomplete: {missing[0]}")
    api_source = module_paths["vnstock/api/quote.py"].read_text(encoding="utf-8")
    kbs_source = module_paths["vnstock/explorer/kbs/quote.py"].read_text(encoding="utf-8")
    const_source = module_paths["vnstock/explorer/kbs/const.py"].read_text(encoding="utf-8")
    history = _history_ast(kbs_source)
    body = ast.get_source_segment(kbs_source, history) or ""
    divisor_verified = "df[col] = df[col] / 1000" in body and "'derivative', 'index'" in body
    defaults = dict(zip((arg.arg for arg in history.args.args[-len(history.args.defaults):]), history.args.defaults)) if history.args.defaults else {}
    rounding_default = isinstance(defaults.get("floating"), ast.Constant) and defaults["floating"].value == 2
    rounding_verified = "df[col] = df[col].round(floating)" in body and rounding_default
    mapping_verified = all(f"'{raw}': '{normalized}'" in const_source for raw, normalized in (
        ("t", "time"), ("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"), ("v", "volume"),
    ))
    quote_history_api = _history_ast(api_source)
    api_body = ast.get_source_segment(api_source, quote_history_api) or ""
    provider_delegation_verified = "self._delegate_to_provider(M.HISTORY" in api_body
    arguments = {arg.arg.lower() for arg in history.args.args + history.args.kwonlyargs}
    adjustment_selectors = sorted(arg for arg in arguments if any(term in arg for term in ("adjust", "raw_price", "price_mode")))
    adjustment_transformation_present = any(term in body.lower() for term in ("adjustment_factor", "split_adjust", "dividend_adjust"))
    price_source_fields = tuple(raw for raw in ("t", "o", "h", "l", "c", "v") if f"'{raw}'" in const_source)
    return {
        "module_paths": {name: str(path) for name, path in module_paths.items()},
        "module_sha256": {name: _sha256_file(path) for name, path in module_paths.items()},
        "equity_ohlc_divisor": 1000 if divisor_verified else None,
        "index_ohlc_divisor": 1 if divisor_verified else None,
        "rounding_decimals": 2 if rounding_verified else None,
        "mapping_verified": mapping_verified,
        "provider_delegation_verified": provider_delegation_verified,
        "adjustment_selectors": adjustment_selectors,
        "adjustment_transformation_present": adjustment_transformation_present,
        "mapped_response_fields": price_source_fields,
    }


def _inspect_writers(project_root: Path) -> tuple[tuple[ProviderWriter, ...], bool, bool, tuple[str, ...]]:
    writers: list[ProviderWriter] = []
    writer_hashes: dict[str, str] = {}
    for relative in WRITER_PATHS:
        path = project_root / relative
        if not path.is_file():
            raise FileNotFoundError(f"canonical ingestion writer is missing: {relative}")
        text = path.read_text(encoding="utf-8")
        writer_hashes[relative] = _sha256_file(path)
        uses_kbs = 'source="KBS"' in text or 'DATA_SOURCE = "KBS"' in text
        uses_history = ".history(" in text and 'interval="1D"' in text
        uses_save = "save_price_data(" in text
        provider = "KBS" if uses_kbs and uses_history else "UNRESOLVED"
        persistence = "core.database.save_price_data" if uses_save else "UNRESOLVED"
        writers.append(ProviderWriter(relative, provider, "1D" if uses_history else "UNRESOLVED", persistence))
    equivalent = bool(writers) and all(
        (row.provider, row.interval, row.persistence_function)
        == (writers[0].provider, writers[0].interval, writers[0].persistence_function)
        and row.provider == "KBS" and row.persistence_function == "core.database.save_price_data"
        for row in writers
    )
    # Current writer agreement cannot prove every historical row came from the current version.
    return tuple(writers), equivalent, True, tuple(sorted(writer_hashes))


def _database_schema_evidence(path: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        tables = tuple(row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ))
        columns: set[str] = set()
        for table in tables:
            escaped = table.replace('"', '""')
            columns.update(str(row[1]).lower() for row in connection.execute(f'PRAGMA table_info("{escaped}")'))
        provenance_columns = tuple(sorted(
            name for name in columns
            if any(token in name for token in ("provider", "source", "unit", "scale", "adjust", "action"))
        ))
        action_tables = tuple(sorted(
            table for table in tables
            if any(token in table.lower() for token in ("corporate", "dividend", "split", "rights", "bonus", "action"))
        ))
        return provenance_columns, action_tables
    finally:
        connection.close()


def collect_provider_provenance(*, project_root: Path, database_path: Path):
    version, package_root = _provider_distribution()
    provider = inspect_installed_kbs_contract(package_root)
    requirement_lines = (project_root / "requirements.txt").read_text(encoding="utf-8").splitlines()
    vnstock_lines = tuple(line.strip() for line in requirement_lines if line.strip().lower().startswith("vnstock"))
    pinned = any(any(op in line for op in ("==", "~=", "<=", ">=", "<", ">")) for line in vnstock_lines)
    lock_names = ("poetry.lock", "uv.lock", "Pipfile.lock", "requirements-lock.txt", "requirements.lock")
    existing_locks = tuple(name for name in lock_names if (project_root / name).is_file())
    writers, equivalent, mixed_possible, writer_paths = _inspect_writers(project_root)
    db_sha = _sha256_file(database_path)
    provenance_columns, action_tables = _database_schema_evidence(database_path)
    evidence_sources = {
        "requirements.txt": _sha256_file(project_root / "requirements.txt"),
        "scripts/update_data.py": _sha256_file(project_root / "scripts/update_data.py"),
        "scripts/backfill_market_data.py": _sha256_file(project_root / "scripts/backfill_market_data.py"),
        "core/database.py": _sha256_file(project_root / "core/database.py"),
    }
    evidence_sources.update({f"provider:{name}": value for name, value in provider["module_sha256"].items()})
    current_unit_state = PARTIALLY_VERIFIED if provider["equity_ohlc_divisor"] == 1000 and provider["mapping_verified"] else AMBIGUOUS
    current_unit = ProvenanceConclusion(
        current_unit_state,
        "Current KBS adapter maps OHLC response fields and divides equity/ETF OHLC by 1000; source-field monetary unit is not labelled in the inspected code.",
        (
            "KBS history maps response keys o/h/l/c/v to open/high/low/close/volume.",
            "For assets other than derivative and index, KBS history divides each OHLC value by 1000 before returning it; index/derivative values bypass that division.",
            "The inspected adapter comments describe the scale operation but do not declare the upstream response field's monetary denomination; no external response was requested.",
        ),
    )
    historical_unit = ProvenanceConclusion(
        PARTIALLY_VERIFIED if equivalent else AMBIGUOUS,
        "Tracked update and backfill writers currently use the same KBS daily Quote.history path, but historical package/version provenance is not recorded.",
        (
            "scripts/update_data.py and scripts/backfill_market_data.py both call Quote(source='KBS').history(interval='1D') and persist via save_price_data.",
            f"requirements.txt provider declaration: {vnstock_lines!r}; version-pinned={pinned}.",
            f"Local lockfiles found: {existing_locks!r}; no persisted per-row provider/version metadata is present in the prices schema." if not provenance_columns else f"Local lockfiles found: {existing_locks!r}; provenance-like columns are {provenance_columns!r}.",
        ),
    )
    current_adjustment = ProvenanceConclusion(
        AMBIGUOUS,
        "The inspected current KBS path exposes no adjustment selector or local adjustment-factor transformation; whether the upstream endpoint returns raw or adjusted history is not established.",
        (
            f"KBS Quote.history parameters include no adjustment selector: {provider['adjustment_selectors']!r}.",
            f"No adjustment-factor transformation token was found in the inspected KBS history implementation: {provider['adjustment_transformation_present']!r}.",
            "Absence of a local adjustment transformation does not prove whether KBS response values are raw or already adjusted.",
        ),
    )
    historical_adjustment = ProvenanceConclusion(
        UNAVAILABLE,
        "Historical adjustment semantics cannot be assigned to all persisted rows because ingestion version/mode and provider adjustment provenance were not persisted.",
        (
            "The local requirements declaration does not pin vnstock and no lockfile was found.",
            "The canonical prices schema contains no adjustment marker/factor, and no corporate-action table was found." if not action_tables else f"The database has action-related tables {action_tables!r}, but no historical row-level adjustment provenance was established.",
            "Current installed adapter behavior is not treated as evidence for all past ingestion runs.",
        ),
    )
    result = build_provider_provenance_result(
        provider_version=version,
        provider_module_paths=tuple(provider["module_paths"].values()),
        provider_module_sha256={provider["module_paths"][name]: digest for name, digest in provider["module_sha256"].items()},
        current_unit=current_unit,
        historical_unit=historical_unit,
        current_adjustment=current_adjustment,
        historical_adjustment=historical_adjustment,
        equity_ohlc_scale_divisor=provider["equity_ohlc_divisor"],
        index_ohlc_scale_divisor=provider["index_ohlc_divisor"],
        price_rounding_decimals=provider["rounding_decimals"],
        adjustment_caller_selectable=bool(provider["adjustment_selectors"]),
        writers=writers,
        writer_paths_equivalent=equivalent,
        mixed_historical_provenance_possible=mixed_possible,
        current_database_sha256=db_sha,
        database_provenance_columns=provenance_columns,
        corporate_action_tables=action_tables,
        next_action="HISTORICAL_PROVENANCE_MIGRATION_REQUIRED",
    )
    return result, {
        "requirements_provider_lines": vnstock_lines,
        "requirements_provider_version_pinned": pinned,
        "lockfiles_found": existing_locks,
        "source_file_sha256": evidence_sources,
        "provider_response_fields": provider["mapped_response_fields"],
        "provider_delegation_verified": provider["provider_delegation_verified"],
        "provider_mapping_verified": provider["mapping_verified"],
        "writer_paths": writer_paths,
    }


def _capability_rows(result) -> list[dict[str, Any]]:
    capabilities = (
        ("current_same_series_relative_price_calculation", "SUPPORTED", "Common scale cancels in ratios."),
        ("current_theoretical_share_quantity", "PARTIALLY_SUPPORTED", "Arithmetic is possible; absolute share count still depends on unresolved source-unit denomination."),
        ("current_vnd_order_notional", "PARTIALLY_SUPPORTED", "Adapter scaling is known but upstream monetary-unit semantics and adjustment semantics are not independently established."),
        ("current_daily_monetary_capacity_proxy", "UNSUPPORTED", "No verified monetary unit/traded-value evidence; do not treat close times volume as canonical turnover."),
        ("historical_same_series_relative_price_calculation", "SUPPORTED", "Dimensionless ratios remain scale invariant for a consistently stored series."),
        ("historical_theoretical_share_quantity", "PARTIALLY_SUPPORTED", "No historical provider-version/unit provenance for all persisted rows."),
        ("historical_vnd_order_notional", "PARTIALLY_SUPPORTED", "Historical provider/version and adjustment provenance are absent."),
        ("historical_daily_monetary_capacity_proxy", "UNSUPPORTED", "Historical monetary scale and traded-value semantics are not verified."),
        ("historical_sequential_share_holdings", "UNSUPPORTED", "Historical corporate-action/quantity provenance is not persisted."),
        ("historical_executable_pnl", "UNSUPPORTED", "Raw/adjusted semantics, corporate actions and executable fill evidence remain incomplete."),
    )
    return [{"capability": name, "state": state, "basis": reason} for name, state, reason in capabilities]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def run_provider_provenance_audit(*, database_path: str | Path | None = None, output_root: str | Path = DEFAULT_OUTPUT_ROOT):
    db_path = resolve_market_database_path(database_path)
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"output directory already exists: {output}")
    if output in {PROJECT_ROOT, PROJECT_ROOT / "research_results"} or output == db_path or db_path in output.parents:
        raise ValueError("output root must be a dedicated audit directory separate from the market database")
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    result, evidence = collect_provider_provenance(project_root=PROJECT_ROOT, database_path=db_path)
    rows = _capability_rows(result)
    conclusions = {
        name: {"state": item.state, "conclusion": item.conclusion, "evidence": list(item.evidence)}
        for name, item in (
            ("current_unit", result.current_unit),
            ("historical_unit", result.historical_unit),
            ("current_adjustment", result.current_adjustment),
            ("historical_adjustment", result.historical_adjustment),
        )
    }
    manifest: dict[str, Any] = {
        "contract": result.contract,
        "version": result.version,
        "result_identity": result.identity,
        "phase11c": {
            "result_identity": result.phase11c_result_identity,
            "canonical_market_database_sha256": result.phase11c_database_sha256,
            "reconciliation": "Phase 11C conclusions are frozen; this result adds current local provider-source evidence only.",
        },
        "provider": {
            "name": result.provider_name,
            "installed_version": result.provider_version,
            "inspected_module_paths": list(result.provider_module_paths),
            "inspected_module_sha256": dict(result.provider_module_sha256),
            "equity_ohlc_scale_divisor": result.equity_ohlc_scale_divisor,
            "index_ohlc_scale_divisor": result.index_ohlc_scale_divisor,
            "price_rounding_decimals": result.price_rounding_decimals,
            "adjustment_caller_selectable": result.adjustment_caller_selectable,
            "response_fields": list(evidence["provider_response_fields"]),
            "delegation_verified_from_source": evidence["provider_delegation_verified"],
        },
        "conclusions": conclusions,
        "historical_version_provenance": {
            "requirements_declaration": list(evidence["requirements_provider_lines"]),
            "version_pinned": evidence["requirements_provider_version_pinned"],
            "lockfiles_found": list(evidence["lockfiles_found"]),
            "per_row_provider_version_metadata": False,
        },
        "writer_reconciliation": {
            "writers": [asdict(writer) for writer in result.writers],
            "paths_equivalent_today": result.writer_paths_equivalent,
            "mixed_historical_provenance_possible": result.mixed_historical_provenance_possible,
            "explanation": "Current writer agreement does not identify the version/provider semantics that created old rows.",
        },
        "market_database": {
            "path": str(db_path),
            "sha256": result.current_database_sha256,
            "provenance_like_columns": list(result.database_provenance_columns),
            "corporate_action_tables": list(result.corporate_action_tables),
            "opened_read_only": True,
        },
        "local_evidence_source_sha256": evidence["source_file_sha256"],
        "capability_reassessment": rows,
        "next_action": result.next_action,
        "no_network_or_provider_calls": True,
        "artifacts": {},
        "completed": True,
    }
    report = [
        "# Phase 11D — Provider Contract and Local Price-Provenance Resolution", "",
        f"**Result identity:** `{result.identity}`", "",
        "## Current versus historical conclusions", "",
        "| Question | State | Conclusion |", "|---|---|---|",
        *[f"| {name} | {item.state} | {item.conclusion} |" for name, item in (
            ("Current unit", result.current_unit), ("Historical unit", result.historical_unit),
            ("Current adjustment", result.current_adjustment), ("Historical adjustment", result.historical_adjustment),
        )], "",
        "## Installed provider contract", "",
        f"- Installed vnstock version: `{result.provider_version}`.",
        f"- KBS maps response fields `{', '.join(evidence['provider_response_fields'])}`; equity/ETF OHLC is divided by {result.equity_ohlc_scale_divisor}, while index/derivative OHLC bypasses that division.",
        "- The code establishes the returned transformation, not the upstream response fields' named denomination.",
        "- No adjustment selector or local adjustment-factor transform is present in the inspected KBS `history` path; upstream raw-versus-adjusted meaning remains unestablished.", "",
        "## Historical provenance and writers", "",
        f"- Current writer paths equivalent: `{result.writer_paths_equivalent}`.",
        f"- Historical mixed provenance remains possible: `{result.mixed_historical_provenance_possible}`.",
        f"- requirements.txt pins vnstock: `{evidence['requirements_provider_version_pinned']}`; lockfiles found: `{list(evidence['lockfiles_found'])}`.",
        "- Current writer parity cannot prove historical package versions or row-level source/adjustment semantics.", "",
        "## Frozen Phase 11C capabilities", "",
        "No Phase 11C artifact or capability gate is rewritten. Current adapter evidence does not promote historical monetary, capacity, holdings, or executable-PnL claims.", "",
        "## Smallest evidence-justified next action", "",
        f"`{result.next_action}` — historical monetary execution claims require provider/version and corporate-action provenance attached to the data that is ingested; current source inspection cannot recover it for old rows.", "",
        "No network/provider call was made. The canonical market database was opened read-only for schema metadata and hashed; no database or ingestion data was modified.", "",
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:
        _write_csv(temporary / FILES[0], rows)
        (temporary / FILES[2]).write_text("\n".join(report), encoding="utf-8", newline="\n")
        manifest["artifacts"] = {
            FILES[0]: {"rows": len(rows), "sha256": _sha256_file(temporary / FILES[0])},
            FILES[2]: {"rows": len(report), "sha256": _sha256_file(temporary / FILES[2])},
        }
        (temporary / FILES[1]).write_bytes(canonical_json(canonical_identity_value(manifest)) + b"\n")
        temporary.replace(output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return result, evidence, output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-path", type=Path)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args(argv)
    result, _, output = run_provider_provenance_audit(database_path=args.database_path, output_root=args.output_root)
    print(f"Phase 11D: EXECUTION_PROVIDER_PROVENANCE_PARTIAL; identity={result.identity}; output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
