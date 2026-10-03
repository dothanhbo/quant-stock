from __future__ import annotations

"""Offline-only recorder for synthetic market-data observations.

This module deliberately has no transport, database, admission, or production
updater dependency.  It records supplied fixture bytes and normalized rows; it
does not decide whether a session is completed or data is admissible.
"""

import argparse
from copy import deepcopy
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping
from uuid import uuid4

from quantlab.completed_session import normalized_price_row_fingerprint


CONTRACT_VERSION = "D4B2.7B_OBSERVATION_ONLY_V1"
TRANSPORT_DISABLED = "DISABLED"
SYNTHETIC_PROVENANCE = "SYNTHETIC_RECORDED_FIXTURE"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ObservationContractError(ValueError):
    """The offline observation input is incomplete or unsafe."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _text(value: object, field: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ObservationContractError(f"{field} must be non-empty")
    return normalized


def _utc_timestamp(value: object, field: str) -> str:
    try:
        parsed = datetime.fromisoformat(_text(value, field).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ObservationContractError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ObservationContractError(f"{field} must include a UTC offset")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_provenance(value: object, field: str, *, normalized: bool) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ObservationContractError(f"{field} must be an object")
    result = {
        "kind": _text(value.get("kind"), f"{field}.kind"),
        "source_reference": _text(
            value.get("source_reference"), f"{field}.source_reference"
        ),
    }
    if result["kind"] != SYNTHETIC_PROVENANCE:
        raise ObservationContractError(
            f"{field}.kind must be {SYNTHETIC_PROVENANCE} in offline mode"
        )
    if normalized:
        result["transform_reference"] = _text(
            value.get("transform_reference"), f"{field}.transform_reference"
        )
    return result


def _validate_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "contract_version",
        "transport",
        "symbol_to_venue",
        "calendar_snapshot",
        "publication_delay_seconds",
        "observations",
    }
    missing = sorted(required.difference(contract))
    if missing:
        raise ObservationContractError(f"missing required fields: {', '.join(missing)}")
    if contract["contract_version"] != CONTRACT_VERSION:
        raise ObservationContractError("unsupported contract_version")

    transport = contract["transport"]
    if not isinstance(transport, Mapping) or transport.get("mode") != TRANSPORT_DISABLED:
        raise ObservationContractError("network transport must be explicitly DISABLED")

    delay = contract["publication_delay_seconds"]
    if delay is not None and (isinstance(delay, bool) or not isinstance(delay, int) or delay < 0):
        raise ObservationContractError(
            "publication_delay_seconds must be null or a non-negative integer"
        )

    raw_mapping = contract["symbol_to_venue"]
    if not isinstance(raw_mapping, Mapping) or not raw_mapping:
        raise ObservationContractError("symbol_to_venue must be a non-empty object")
    symbol_to_venue = {
        _text(symbol, "symbol_to_venue symbol").upper(): _text(venue, "venue").upper()
        for symbol, venue in raw_mapping.items()
    }

    calendar = contract["calendar_snapshot"]
    if not isinstance(calendar, Mapping):
        raise ObservationContractError("calendar_snapshot must be an object")
    calendar_required = {
        "snapshot_id", "evidence_kind", "content_sha256", "source_references", "sessions"
    }
    calendar_missing = sorted(calendar_required.difference(calendar))
    if calendar_missing:
        raise ObservationContractError(
            f"calendar_snapshot missing: {', '.join(calendar_missing)}"
        )
    if calendar["evidence_kind"] != "SYNTHETIC_FIXTURE":
        raise ObservationContractError("offline calendar evidence must be SYNTHETIC_FIXTURE")
    digest = str(calendar["content_sha256"]).strip().lower()
    if not _SHA256.fullmatch(digest):
        raise ObservationContractError("calendar_snapshot.content_sha256 must be SHA-256")
    references = calendar["source_references"]
    if not isinstance(references, list) or not references:
        raise ObservationContractError("calendar_snapshot.source_references must be non-empty")
    for reference in references:
        _text(reference, "calendar source reference")
    sessions = calendar["sessions"]
    if not isinstance(sessions, Mapping) or not sessions:
        raise ObservationContractError("calendar_snapshot.sessions must be non-empty")

    observations = contract["observations"]
    if not isinstance(observations, list) or not observations:
        raise ObservationContractError("observations must be a non-empty list")
    seen_ids: set[str] = set()
    records: list[dict[str, Any]] = []
    for index, observation in enumerate(observations):
        if not isinstance(observation, Mapping):
            raise ObservationContractError(f"observations[{index}] must be an object")
        observation_id = _text(observation.get("observation_id"), "observation_id")
        if observation_id in seen_ids:
            raise ObservationContractError("observation_id values must be unique")
        seen_ids.add(observation_id)
        symbol = _text(observation.get("symbol"), "symbol").upper()
        venue = _text(observation.get("venue"), "venue").upper()
        if symbol_to_venue.get(symbol) != venue:
            raise ObservationContractError(f"venue does not match explicit mapping for {symbol}")
        target_session = _text(observation.get("target_session"), "target_session")
        try:
            session_date = date.fromisoformat(target_session)
        except ValueError as exc:
            raise ObservationContractError("target_session must be YYYY-MM-DD") from exc
        calendar_entry = sessions.get(target_session)
        if not isinstance(calendar_entry, Mapping):
            raise ObservationContractError(
                f"calendar snapshot has no explicit entry for {target_session}"
            )
        calendar_venues = calendar_entry.get("venues")
        if not isinstance(calendar_venues, list) or venue not in {
            str(item).strip().upper() for item in calendar_venues
        }:
            raise ObservationContractError(
                f"calendar snapshot does not cover {venue} on {target_session}"
            )

        raw_payload = observation.get("raw_payload")
        if not isinstance(raw_payload, str):
            raise ObservationContractError("raw_payload must be an exact string")
        raw_provenance = _validate_provenance(
            observation.get("raw_provenance"), "raw_provenance", normalized=False
        )
        normalized_provenance = _validate_provenance(
            observation.get("normalized_provenance"),
            "normalized_provenance",
            normalized=True,
        )
        row = observation.get("normalized_row")
        if not isinstance(row, Mapping):
            raise ObservationContractError("normalized_row must be an object")
        row_symbol = _text(row.get("symbol"), "normalized_row.symbol").upper()
        row_session = _text(row.get("session"), "normalized_row.session")
        if row_symbol != symbol or row_session != target_session:
            raise ObservationContractError("normalized row identity must match observation")
        try:
            fingerprint = normalized_price_row_fingerprint(
                symbol=row_symbol,
                session=session_date,
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ObservationContractError("normalized_row must contain valid OHLCV") from exc
        observed_at = _utc_timestamp(observation.get("observed_at"), "observed_at")
        records.append({
            "observation_id": observation_id,
            "symbol": symbol,
            "venue": venue,
            "target_session": target_session,
            "observed_at": observed_at,
            "raw_payload": raw_payload,
            "raw_payload_sha256": _sha256_bytes(raw_payload.encode("utf-8")),
            "raw_provenance": raw_provenance,
            "normalized_row": dict(row),
            "normalized_row_fingerprint": fingerprint,
            "normalized_provenance": normalized_provenance,
        })

    validated = deepcopy(dict(contract))
    validated["transport"] = {"mode": TRANSPORT_DISABLED}
    validated["symbol_to_venue"] = symbol_to_venue
    validated["calendar_snapshot"] = deepcopy(dict(calendar))
    validated["observations"] = records
    return validated


def record_offline_observations(
    contract: Mapping[str, Any], output_root: Path | str
) -> Path:
    """Validate and record synthetic observations in a unique evidence directory."""
    validated = _validate_contract(contract)
    now = datetime.now(timezone.utc)
    created_at = now.isoformat().replace("+00:00", "Z")
    run_id = f"offline-{now:%Y%m%dT%H%M%S}-{uuid4().hex}"
    output_directory = Path(output_root).resolve() / run_id
    output_directory.mkdir(parents=True, exist_ok=False)

    observations = validated.pop("observations")
    contract_bytes = _canonical_bytes(validated) + b"\n"
    observations_bytes = b"".join(_canonical_bytes(item) + b"\n" for item in observations)
    (output_directory / "contract_snapshot.json").write_bytes(contract_bytes)
    (output_directory / "observations.jsonl").write_bytes(observations_bytes)

    comparisons: list[dict[str, Any]] = []
    identities = sorted({(item["symbol"], item["target_session"]) for item in observations})
    for symbol, target_session in identities:
        fingerprints = [
            item["normalized_row_fingerprint"]
            for item in observations
            if item["symbol"] == symbol and item["target_session"] == target_session
        ]
        comparisons.append({
            "symbol": symbol,
            "target_session": target_session,
            "observation_count": len(fingerprints),
            "fingerprints": fingerprints,
            "distinct_fingerprint_count": len(set(fingerprints)),
            "all_identical": len(set(fingerprints)) == 1 if len(fingerprints) >= 2 else None,
        })

    result = {
        "contract_version": CONTRACT_VERSION,
        "mode": "OBSERVATION_ONLY",
        "run_id": run_id,
        "created_at": created_at,
        "transport_mode": TRANSPORT_DISABLED,
        "publication_delay_seconds": validated["publication_delay_seconds"],
        "calendar_snapshot_id": validated["calendar_snapshot"]["snapshot_id"],
        "calendar_snapshot_fingerprint": _sha256_bytes(
            _canonical_bytes(validated["calendar_snapshot"])
        ),
        "contract_snapshot_sha256": _sha256_bytes(contract_bytes),
        "observations_sha256": _sha256_bytes(observations_bytes),
        "observation_count": len(observations),
        "comparisons": comparisons,
        "admission_decision": None,
        "completed_session_status": None,
        "completed_session_inferred": False,
        "database_mutation_attempted": False,
    }
    (output_directory / "result.json").write_bytes(_canonical_bytes(result) + b"\n")
    return output_directory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record synthetic observations offline")
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    arguments = parser.parse_args(argv)
    contract = json.loads(arguments.contract.read_text(encoding="utf-8"))
    output_directory = record_offline_observations(contract, arguments.output_root)
    print(output_directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
