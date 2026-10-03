from __future__ import annotations

"""Read-only CafeF RAW monitoring over already-retained source archives."""

from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Mapping, Sequence

from quantlab.cafef_manual_eod import (
    AdjustmentClaim,
    ArchiveEvidence,
    CafeFRow,
    expected_venue_members,
    validate_cafef_archive,
)


MONITOR_CONTRACT = "QUANTCTL_CAFEF_READ_ONLY_MONITOR_V1"
UNKNOWN = "UNKNOWN"


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _normalized_hash(value: str, field: str) -> str:
    normalized = str(value).strip().lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError(f"{field} must be a SHA-256 digest")
    return normalized


def _archive(
    path: Path,
    *,
    session: date,
    expected_sha256: str,
) -> ArchiveEvidence:
    expected = _normalized_hash(expected_sha256, "expected archive SHA256")
    actual = _file_sha256(path)
    if actual != expected:
        raise ValueError(f"archive SHA256 mismatch: expected {expected}, got {actual}")
    return validate_cafef_archive(
        path,
        session=session,
        adjustment_claim=AdjustmentClaim.RAW,
        source_identity="EXTERNALLY_RETAINED_CAFEF_RAW_SOURCE_CLAIM",
        venue_members=expected_venue_members(session, AdjustmentClaim.RAW),
    )


def _database_sha256(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(path)
    return _file_sha256(path)


def _canonical_reference(
    path: Path,
) -> tuple[str, dict[str, dict[str, Decimal | int]], tuple[str, ...]]:
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        latest = connection.execute(
            """
            SELECT MAX(date(time)) FROM prices
            WHERE UPPER(TRIM(symbol)) = 'VNINDEX'
            """
        ).fetchone()[0]
        if latest is None:
            raise ValueError("canonical prices table contains no VNINDEX reference session")
        universe = tuple(
            str(row[0])
            for row in connection.execute(
                """
                SELECT DISTINCT UPPER(TRIM(symbol)) FROM prices
                WHERE UPPER(TRIM(symbol)) != 'VNINDEX'
                ORDER BY UPPER(TRIM(symbol))
                """
            )
        )
        rows = connection.execute(
            """
            SELECT UPPER(TRIM(symbol)),open,high,low,close,volume
            FROM prices
            WHERE date(time)=? AND UPPER(TRIM(symbol)) != 'VNINDEX'
            ORDER BY UPPER(TRIM(symbol))
            """,
            (latest,),
        ).fetchall()
    result: dict[str, dict[str, Decimal | int]] = {}
    for symbol, open_, high, low, close, volume in rows:
        if symbol in result:
            raise ValueError(f"duplicate canonical latest-session symbol: {symbol}")
        result[str(symbol)] = {
            "open": Decimal(str(open_)),
            "high": Decimal(str(high)),
            "low": Decimal(str(low)),
            "close": Decimal(str(close)),
            "volume": int(volume),
        }
    if not universe:
        raise ValueError("canonical database contains no non-index symbols")
    return str(latest), result, universe


def _rows(evidence: ArchiveEvidence) -> dict[str, CafeFRow]:
    return {row.symbol: row for row in evidence.rows}


def _member_identity(evidence: ArchiveEvidence) -> list[dict[str, object]]:
    return [
        {
            "venue": member.venue,
            "name": member.member_name,
            "sha256": member.content_sha256,
            "bytes": member.byte_length,
            "rows": member.row_count,
        }
        for member in evidence.members
    ]


def _row_values(row: CafeFRow) -> dict[str, Decimal | int]:
    return {
        "open": Decimal(str(row.open)),
        "high": Decimal(str(row.high)),
        "low": Decimal(str(row.low)),
        "close": Decimal(str(row.close)),
        "volume": int(row.volume),
    }


def _parity(
    source: Mapping[str, CafeFRow],
    reference: Mapping[str, Mapping[str, Decimal | int]],
    universe: Sequence[str],
    *,
    price_tolerance: Decimal,
) -> dict[str, object]:
    exact = tolerance_only = 0
    differences: list[dict[str, object]] = []
    volume_mismatches: list[dict[str, object]] = []
    source_missing = sorted(set(universe) - set(source))
    reference_missing = sorted(set(universe) - set(reference))
    for symbol in universe:
        if symbol not in source or symbol not in reference:
            continue
        incoming = _row_values(source[symbol])
        stored = reference[symbol]
        symbol_differences: list[dict[str, object]] = []
        for field in ("open", "high", "low", "close"):
            difference = Decimal(incoming[field]) - Decimal(stored[field])
            if difference != 0:
                symbol_differences.append({
                    "symbol": symbol,
                    "field": field,
                    "canonical": str(stored[field]),
                    "cafef": str(incoming[field]),
                    "difference": str(difference),
                    "within_tolerance": abs(difference) <= price_tolerance,
                })
        volume_equal = incoming["volume"] == stored["volume"]
        if not volume_equal:
            volume_mismatches.append({
                "symbol": symbol,
                "canonical": int(stored["volume"]),
                "cafef": int(incoming["volume"]),
            })
        differences.extend(symbol_differences)
        if not symbol_differences and volume_equal:
            exact += 1
        elif volume_equal and all(item["within_tolerance"] for item in symbol_differences):
            tolerance_only += 1
    status = "PASS"
    if source_missing or reference_missing:
        status = "INCOMPLETE"
    elif differences or volume_mismatches:
        status = "REVIEW_REQUIRED"
    return {
        "status": status,
        "exact_ohlcv_matches": exact,
        "tolerance_only_matches": tolerance_only,
        "source_missing": source_missing,
        "reference_missing": reference_missing,
        "price_differences": differences,
        "volume_mismatches": volume_mismatches,
        "price_tolerance": str(price_tolerance),
    }


def _adjacent_flags(
    previous: Mapping[str, CafeFRow],
    current: Mapping[str, CafeFRow],
    universe: Sequence[str],
    *,
    price_threshold_pct: Decimal,
    volume_multiple: Decimal,
) -> list[dict[str, object]]:
    flags: list[dict[str, object]] = []
    inverse_volume_multiple = Decimal(1) / volume_multiple
    for symbol in universe:
        if symbol not in previous or symbol not in current:
            continue
        old = _row_values(previous[symbol])
        new = _row_values(current[symbol])
        if Decimal(old["close"]) > 0:
            change_pct = (Decimal(new["close"]) / Decimal(old["close"]) - 1) * 100
            if abs(change_pct) >= price_threshold_pct:
                flags.append({
                    "symbol": symbol,
                    "kind": "PRICE_CHANGE",
                    "status": "REVIEW_REQUIRED",
                    "previous_close": str(old["close"]),
                    "current_close": str(new["close"]),
                    "change_pct": str(change_pct.quantize(Decimal("0.000001"))),
                    "explanation": UNKNOWN,
                })
        old_volume = Decimal(int(old["volume"]))
        new_volume = Decimal(int(new["volume"]))
        if old_volume == 0:
            if new_volume > 0:
                flags.append({
                    "symbol": symbol,
                    "kind": "VOLUME_CHANGE",
                    "status": "REVIEW_REQUIRED",
                    "previous_volume": 0,
                    "current_volume": int(new_volume),
                    "multiple": UNKNOWN,
                    "explanation": UNKNOWN,
                })
        else:
            ratio = new_volume / old_volume
            if ratio >= volume_multiple or ratio <= inverse_volume_multiple:
                flags.append({
                    "symbol": symbol,
                    "kind": "VOLUME_CHANGE",
                    "status": "REVIEW_REQUIRED",
                    "previous_volume": int(old_volume),
                    "current_volume": int(new_volume),
                    "multiple": str(ratio.quantize(Decimal("0.000001"))),
                    "explanation": UNKNOWN,
                })
    return sorted(flags, key=lambda value: (str(value["symbol"]), str(value["kind"])))


def build_monitoring_report(
    *,
    archive_path: str | Path,
    session: date,
    expected_sha256: str,
    canonical_database: str | Path,
    previous_archive_path: str | Path | None = None,
    previous_session: date | None = None,
    previous_expected_sha256: str | None = None,
    expected_universe_size: int = 100,
    price_tolerance: Decimal = Decimal("0.0005"),
    price_threshold_pct: Decimal = Decimal("5"),
    volume_multiple: Decimal = Decimal("5"),
) -> dict[str, object]:
    """Build deterministic monitoring evidence without changing either input."""
    if expected_universe_size <= 0:
        raise ValueError("expected universe size must be positive")
    if price_tolerance < 0 or price_threshold_pct <= 0 or volume_multiple <= 1:
        raise ValueError("monitoring thresholds are invalid")
    archive_path = Path(archive_path).resolve()
    canonical_database = Path(canonical_database).resolve()
    database_before = _database_sha256(canonical_database)
    current = _archive(archive_path, session=session, expected_sha256=expected_sha256)
    current_rows = _rows(current)
    canonical_session, canonical_rows, universe = _canonical_reference(canonical_database)

    previous = None
    if previous_archive_path is not None:
        if previous_session is None or previous_expected_sha256 is None:
            raise ValueError("previous archive requires session and expected SHA256")
        if previous_session >= session:
            raise ValueError("previous archive session must precede current session")
        previous = _archive(
            Path(previous_archive_path).resolve(),
            session=previous_session,
            expected_sha256=previous_expected_sha256,
        )
    elif previous_session is not None or previous_expected_sha256 is not None:
        raise ValueError("previous archive path is missing")

    current_missing = sorted(set(universe) - set(current_rows))
    outside_universe = sorted(set(current_rows) - set(universe))
    coverage_status = (
        "PASS"
        if len(universe) == expected_universe_size and not current_missing
        else "INCOMPLETE"
    )
    parity_source = None
    parity_source_session = None
    if session.isoformat() == canonical_session:
        parity_source = current_rows
        parity_source_session = session.isoformat()
    elif previous is not None and previous.session.isoformat() == canonical_session:
        parity_source = _rows(previous)
        parity_source_session = previous.session.isoformat()
    parity = (
        {
            "status": UNKNOWN,
            "reference_session": canonical_session,
            "source_session": None,
            "reason": "NO_ARCHIVE_FOR_CANONICAL_REFERENCE_SESSION",
        }
        if parity_source is None
        else {
            "reference_session": canonical_session,
            "source_session": parity_source_session,
            **_parity(
                parity_source,
                canonical_rows,
                universe,
                price_tolerance=price_tolerance,
            ),
        }
    )
    previous_rows = {} if previous is None else _rows(previous)
    flags = [] if previous is None else _adjacent_flags(
        previous_rows,
        current_rows,
        universe,
        price_threshold_pct=price_threshold_pct,
        volume_multiple=volume_multiple,
    )
    adjacent_previous_missing = (
        [] if previous is None else sorted(set(universe) - set(previous_rows))
    )
    adjacent_current_missing = (
        [] if previous is None else sorted(set(universe) - set(current_rows))
    )
    adjacent_status = UNKNOWN
    if previous is not None:
        if adjacent_previous_missing or adjacent_current_missing:
            adjacent_status = "INCOMPLETE"
        else:
            adjacent_status = "REVIEW_REQUIRED" if flags else "PASS"
    database_after = _database_sha256(canonical_database)
    if database_before != database_after:
        raise RuntimeError("canonical database changed during monitoring")

    base: dict[str, object] = {
        "contract": MONITOR_CONTRACT,
        "mode": "READ_ONLY_SOURCE_MONITOR",
        "archive": {
            "session": session.isoformat(),
            "sha256": current.zip_sha256,
            "bytes": current.zip_byte_length,
            "rows": len(current.rows),
            "members": _member_identity(current),
            "adjustment_claim": "RAW_SOURCE_CLAIM",
        },
        "previous_archive": (
            {"status": "MISSING", "session": None, "sha256": None}
            if previous is None
            else {
                "status": "PRESENT",
                "session": previous.session.isoformat(),
                "sha256": previous.zip_sha256,
                "rows": len(previous.rows),
            }
        ),
        "canonical": {
            "database_sha256_before": database_before,
            "database_sha256_after": database_after,
            "reference_session": canonical_session,
            "reference_missing": sorted(set(universe) - set(canonical_rows)),
        },
        "integrity": {
            "archive_structure": "PASS",
            "ohlcv": "PASS",
            "duplicate_symbol_session": "PASS",
        },
        "universe": {
            "source": "ALL_CANONICAL_NON_INDEX_SYMBOLS",
            "expected_size": expected_universe_size,
            "actual_size": len(universe),
            "symbols": list(universe),
            "symbols_sha256": sha256("\n".join(universe).encode("utf-8")).hexdigest(),
            "covered": len(set(universe) & set(current_rows)),
            "missing": current_missing,
            "outside_universe_count": len(outside_universe),
            "outside_universe_sha256": sha256(
                "\n".join(outside_universe).encode("utf-8")
            ).hexdigest(),
            "status": coverage_status,
        },
        "same_session_parity": parity,
        "adjacent_monitoring": {
            "status": adjacent_status,
            "price_threshold_pct": str(price_threshold_pct),
            "volume_multiple": str(volume_multiple),
            "previous_missing": adjacent_previous_missing,
            "current_missing": adjacent_current_missing,
            "flags": flags,
        },
        "evidence_status": {
            "download_success": "NOT_EVALUATED_OFFLINE_INPUT",
            "publication_completion": UNKNOWN,
            "venue_completed_session": UNKNOWN,
            "symbol_session_lifecycle": UNKNOWN,
            "adjustment_provenance": UNKNOWN,
            "corporate_action_explanations": UNKNOWN,
            "operational_admission": "NOT_EVALUATED",
            "research_eligible": False,
        },
    }
    base["report_identity"] = sha256(_canonical_json(base).encode("utf-8")).hexdigest()
    return base


def render_monitoring_markdown(report: Mapping[str, object]) -> str:
    archive = report["archive"]
    universe = report["universe"]
    parity = report["same_session_parity"]
    adjacent = report["adjacent_monitoring"]
    evidence = report["evidence_status"]
    assert isinstance(archive, Mapping)
    assert isinstance(universe, Mapping)
    assert isinstance(parity, Mapping)
    assert isinstance(adjacent, Mapping)
    assert isinstance(evidence, Mapping)
    flags = adjacent["flags"]
    assert isinstance(flags, list)
    lines = [
        "# CafeF read-only EOD source monitor",
        "",
        f"Report identity: `{report['report_identity']}`",
        f"Archive session: `{archive['session']}`",
        f"Archive SHA256: `{archive['sha256']}`",
        f"Archive/OHLCV integrity: `{report['integrity']['archive_structure']}` / `{report['integrity']['ohlcv']}`",
        f"Universe coverage: `{universe['status']}` ({universe['covered']}/{universe['actual_size']})",
        f"Same-session parity: `{parity['status']}`",
        f"Adjacent monitoring: `{adjacent['status']}`",
        "",
        "## Monitoring flags",
        "",
    ]
    if flags:
        lines.extend(
            f"- `{item['symbol']}` `{item['kind']}`: explanation `{item['explanation']}`"
            for item in flags
        )
    else:
        lines.append("- None")
    lines.extend((
        "",
        "## Evidence status",
        "",
        *(f"- `{key}`: `{value}`" for key, value in evidence.items()),
        "",
    ))
    return "\n".join(lines)


def write_monitoring_report(report: Mapping[str, object], output_directory: str | Path) -> Path:
    target = Path(output_directory).resolve()
    target.mkdir(parents=True, exist_ok=False)
    json_bytes = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    markdown_bytes = (render_monitoring_markdown(report) + "\n").encode("utf-8")
    for name, value in (("report.json", json_bytes), ("report.md", markdown_bytes)):
        temporary = target / f".{name}.tmp"
        temporary.write_bytes(value)
        temporary.replace(target / name)
    return target


__all__ = [
    "MONITOR_CONTRACT",
    "build_monitoring_report",
    "render_monitoring_markdown",
    "write_monitoring_report",
]
