from __future__ import annotations

"""Offline, denial-only packet adapter for explicit D4B2 shadow callers.

The caller must supply the SHA-256 of an independently reviewed packet version,
not compute a new trust pin from the file being loaded. Hash integrity does not
establish historical availability. This adapter never returns TRADING_CONFIRMED
and does not wire any production writer or infer a lifecycle interval.
"""

from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re

from quantlab.completed_session import SymbolSessionEvidence, SymbolSessionStatus


def load_symbol_session_packet(
    packet_path: Path,
    *,
    repository_root: Path,
    expected_packet_sha256: str,
    symbol: str,
    venue: str,
    target_session: date,
) -> SymbolSessionEvidence:
    """Map one reviewed NOT_TRADING session; invalid evidence becomes UNKNOWN.

    Invalid evidence has no attributable source references. The existing
    completed-session gate consequently returns UNRESOLVED, before publication
    fixtures can admit a row. A reason is retained in source_identity for receipts.
    """
    symbol, venue = symbol.strip().upper(), venue.strip().upper()
    root = repository_root.resolve()

    def unknown(reason: str) -> SymbolSessionEvidence:
        return SymbolSessionEvidence(
            symbol, venue, target_session, SymbolSessionStatus.UNKNOWN,
            "packet-unresolved:" + reason,
            "unverified-packet-sha256:" + str(expected_packet_sha256), (),
        )

    if not isinstance(expected_packet_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_packet_sha256):
        return unknown("PACKET_PIN_INVALID")
    try:
        raw = Path(packet_path).read_bytes()
        if sha256(raw).hexdigest() != expected_packet_sha256:
            return unknown("PACKET_HASH_MISMATCH")
        packet = json.loads(raw)
        mapping = packet["symbol_session_schema_mapping"]
        identity = (symbol, venue, target_session.isoformat())
        if (
            (packet["symbol"], packet["venue"], packet["session_date"]) != identity
            or (mapping["symbol"], mapping["venue"], mapping["target_session"]) != identity
        ):
            return unknown("PACKET_IDENTITY_MISMATCH")
        if mapping["status"] != "NOT_TRADING" or packet["trading_status"] != "NOT_TRADING":
            return unknown("DENIAL_MAPPING_REQUIRED")
        if (
            packet["contract"] != "d4-one-symbol-session-evidence/v1"
            or packet["research_eligible"] is not False
            or packet["operational_eligible"] is not False
            or packet["point_in_time"]["historical_availability_established"] is not False
            or packet["point_in_time"]["as_of_session_admissible"] is not False
        ):
            return unknown("PACKET_SCOPE_INVALID")

        def verify_snapshot(record: dict) -> None:
            original = record["snapshot_path"]
            relocated = packet["checkpoint"]["snapshot_paths"].get(original, original)
            path = (root / relocated.replace("\\", "/")).resolve()
            if not path.is_relative_to(root):
                raise ValueError("snapshot outside repository")
            content = path.read_bytes()
            if (
                record["snapshot_kind"] != "ORIGINAL_HTTP_RESPONSE_BODY"
                or len(content) != record["byte_length"]
                or sha256(content).hexdigest() != record["sha256"]
            ):
                raise ValueError("snapshot integrity mismatch")
            for nested in record.values():
                if isinstance(nested, dict) and "snapshot_path" in nested:
                    verify_snapshot(nested)

        sources = packet["sources"]
        if not sources:
            return unknown("SOURCE_EVIDENCE_MISSING")
        for source in sources:
            verify_snapshot(source)
        bundle = sha256(json.dumps(
            sorted(source["sha256"] for source in sources), separators=(",", ":")
        ).encode()).hexdigest()
        references = tuple(mapping["source_references"])
        known_refs = {source["url"] + "#sha256=" + source["sha256"] for source in sources}
        if not references or not set(references) <= known_refs or mapping["snapshot_identity"] != "sha256:" + bundle:
            return unknown("SOURCE_BINDING_INVALID")
        return SymbolSessionEvidence(
            symbol, venue, target_session, SymbolSessionStatus.NOT_TRADING,
            mapping["source_identity"], mapping["snapshot_identity"],
            references + ("evidence-packet:sha256:" + expected_packet_sha256,),
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return unknown("PACKET_OR_SNAPSHOT_INVALID_OR_MISSING")
