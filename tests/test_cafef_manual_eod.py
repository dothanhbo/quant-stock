from __future__ import annotations

import ast
from datetime import date
from hashlib import sha256
import io
from pathlib import Path
import sqlite3
import zipfile

import pytest

from quantlab.cafef_manual_eod import (
    AdjustmentClaim,
    ManualInputError,
    ShadowSafetyError,
    apply_archive_to_shadow,
    create_disposable_clone,
    expected_venue_members,
    prepare_manual_batches,
    run_manual_shadow,
    validate_cafef_archive,
)


SESSION = date(2026, 10, 2)
SOURCE = "CafeF manual download (source claim)"
ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "quantlab" / "cafef_manual_eod.py"


def _csv(*rows: str) -> bytes:
    return (
        "\ufeff<Ticker>,<DTYYYYMMDD>,<Open>,<High>,<Low>,<Close>,<Volume>\n"
        + "\n".join(rows) + "\n"
    ).encode("utf-8")


def _archive(
    path: Path,
    *,
    claim: AdjustmentClaim = AdjustmentClaim.RAW,
    hnx_rows: tuple[str, ...] = ("AAA,20261002,10,11,9,10.5,1000",),
    hsx_rows: tuple[str, ...] = ("BBB,20261002,20,21,19,20.5,2000",),
    upcom_rows: tuple[str, ...] = ("CCC,20261002,30,31,29,30.5,3000",),
) -> dict[str, str]:
    mapping = expected_venue_members(SESSION, claim)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(mapping["HSX"], _csv(*hsx_rows))
        archive.writestr(mapping["HNX"], _csv(*hnx_rows))
        archive.writestr(mapping["UPCOM"], _csv(*upcom_rows))
    return mapping


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE prices(
                symbol TEXT NOT NULL, time TEXT NOT NULL,
                open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                close REAL NOT NULL, volume REAL NOT NULL,
                UNIQUE(symbol, time)
            )
            """
        )
        connection.execute(
            "INSERT INTO prices VALUES('AAA','2026-10-01',10,11,9,10,900)"
        )
        connection.commit()


def _validate(path: Path, mapping: dict[str, str], claim=AdjustmentClaim.RAW):
    return validate_cafef_archive(
        path,
        session=SESSION,
        adjustment_claim=claim,
        source_identity=SOURCE,
        venue_members=mapping,
    )


def test_module_has_no_provider_network_or_production_imports() -> None:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    forbidden = {
        "vnstock", "requests", "urllib3", "socket", "update_data", "save_price_data",
    }
    assert imports.isdisjoint(forbidden)


def test_valid_fixture_is_content_addressed_and_unverified(tmp_path: Path) -> None:
    path = tmp_path / "raw.zip"
    mapping = _archive(path)
    evidence = _validate(path, mapping)
    assert evidence.zip_sha256 == sha256(path.read_bytes()).hexdigest()
    assert len(evidence.rows) == 3
    assert {item.venue for item in evidence.members} == {"HSX", "HNX", "UPCOM"}
    assert evidence.adjustment_verified is False
    batches = prepare_manual_batches(evidence)
    assert all(batch.completed_session_result is None for batch in batches)
    assert all(batch.source_verification_state.value == "UNKNOWN" for batch in batches)
    assert all(batch.adjustment_verification_state.value == "UNKNOWN" for batch in batches)


def test_corrupted_zip_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.zip"
    path.write_bytes(b"not a zip")
    with pytest.raises(ManualInputError, match="malformed"):
        _validate(path, expected_venue_members(SESSION, AdjustmentClaim.RAW))


def test_invalid_ohlcv_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "raw.zip"
    mapping = _archive(path, hnx_rows=("AAA,20261002,10,9,8,10,1000",))
    with pytest.raises(ManualInputError, match="invalid OHLCV"):
        _validate(path, mapping)


def test_duplicate_ticker_session_across_venues_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "raw.zip"
    duplicate = "AAA,20261002,10,11,9,10,1000"
    mapping = _archive(path, hnx_rows=(duplicate,), hsx_rows=(duplicate,))
    with pytest.raises(ManualInputError, match="duplicate ticker/session"):
        _validate(path, mapping)


def test_adjustment_mismatch_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "adjusted.zip"
    adjusted_mapping = _archive(path, claim=AdjustmentClaim.ADJUSTED)
    with pytest.raises(ManualInputError, match="adjustment claim"):
        _validate(path, adjusted_mapping, claim=AdjustmentClaim.RAW)


def test_raw_and_adjusted_claims_cannot_share_a_clone(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.zip"
    adjusted_path = tmp_path / "adjusted.zip"
    raw_mapping = _archive(raw_path, claim=AdjustmentClaim.RAW)
    adjusted_mapping = _archive(adjusted_path, claim=AdjustmentClaim.ADJUSTED)
    source = tmp_path / "source.db"
    _database(source)
    clone = create_disposable_clone(source, tmp_path / "runs")
    apply_archive_to_shadow(clone.database, _validate(raw_path, raw_mapping))
    adjusted = _validate(adjusted_path, adjusted_mapping, AdjustmentClaim.ADJUSTED)
    with pytest.raises(ShadowSafetyError, match="different source or adjustment series"):
        apply_archive_to_shadow(clone.database, adjusted)


def test_missing_or_inconsistent_venue_mapping_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "raw.zip"
    mapping = _archive(path)
    mapping.pop("UPCOM")
    with pytest.raises(ManualInputError, match="explicitly contain"):
        _validate(path, mapping)


def test_repeated_import_reuses_receipts_without_price_writes(tmp_path: Path) -> None:
    archive = tmp_path / "raw.zip"
    mapping = _archive(archive)
    source = tmp_path / "source.db"
    _database(source)
    clone = create_disposable_clone(source, tmp_path / "runs")
    evidence = _validate(archive, mapping)
    first = apply_archive_to_shadow(clone.database, evidence)
    second = apply_archive_to_shadow(clone.database, evidence)
    assert first.accepted_rows == second.accepted_rows == 0
    assert first.staged_rows == second.staged_rows == 2
    assert first.rejected_rows == second.rejected_rows == 1
    assert first.replayed_rows == 0
    assert second.replayed_rows == 3
    with sqlite3.connect(clone.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM market_ingestion_receipts").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM market_ingestion_staging").fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM market_ingestion_staging WHERE research_eligible != 0"
        ).fetchone()[0] == 0


def test_one_batch_transaction_rolls_back(tmp_path: Path) -> None:
    archive = tmp_path / "raw.zip"
    mapping = _archive(archive)
    evidence = _validate(archive, mapping)
    source = tmp_path / "source.db"
    _database(source)
    clone = create_disposable_clone(source, tmp_path / "runs")

    def fail_first(symbol: str, stage: str) -> None:
        if symbol == "AAA" and stage == "after_rejection_or_staging":
            raise RuntimeError("injected failure")

    with pytest.raises(RuntimeError, match="injected failure"):
        apply_archive_to_shadow(clone.database, evidence, failure_injector=fail_first)
    with sqlite3.connect(clone.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM market_ingestion_receipts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM market_ingestion_staging").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 1


def test_canonical_database_and_archive_remain_byte_identical(tmp_path: Path) -> None:
    archive = tmp_path / "raw.zip"
    mapping = _archive(archive)
    source = tmp_path / "canonical.db"
    _database(source)
    source_before = sha256(source.read_bytes()).hexdigest()
    archive_before = sha256(archive.read_bytes()).hexdigest()
    result = run_manual_shadow(
        archive,
        canonical_database=source,
        output_root=tmp_path / "runs",
        session=SESSION,
        adjustment_claim=AdjustmentClaim.RAW,
        source_identity=SOURCE,
        venue_members=mapping,
    )
    assert result.accepted_rows == 0
    assert result.source_database_sha256_before == source_before
    assert result.source_database_sha256_after == source_before
    assert sha256(source.read_bytes()).hexdigest() == source_before
    assert sha256(archive.read_bytes()).hexdigest() == archive_before
    assert Path(result.clone_database).parent != source.parent
    clone = Path(result.clone_database)
    moved = clone.with_name("market.closed-handle-check.db")
    clone.rename(moved)
    moved.rename(clone)
