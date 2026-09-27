from __future__ import annotations

"""Static protocol loading and Phase 8 authorization verification."""

import csv
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from quantlab.identity import canonical_identity_value, canonical_json

from .contracts import ForwardProtocolActivation, ForwardValidationProtocol, identity


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_identity(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def load_protocol_spec(path: str | Path) -> ForwardValidationProtocol:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    protocol = ForwardValidationProtocol(
        protocol_version=payload["protocol_version"],
        source_phase8_manifest_identity=payload["source_phase8_manifest_identity"],
        source_phase8_manifest_sha256=payload["source_phase8_manifest_sha256"],
        source_phase8_result_identity=payload["source_phase8_result_identity"],
        selection_policy=payload["selection_policy"],
        selection_policy_identity=payload["selection_policy_identity"],
        weighting_policy=payload["weighting_policy"],
        budget=int(payload["budget"]),
        tracked_horizons=tuple(int(item) for item in payload["tracked_horizons"]),
        benchmark=payload["benchmark"],
        activation_market_session_boundary=payload["activation_market_session_boundary"],
        historical_research_boundary_source=payload["historical_research_boundary_source"],
        operational_activation_cutoff_semantics=payload["operational_activation_cutoff_semantics"],
        formation_rule=payload["formation_rule"],
        maturity_rule=payload["maturity_rule"],
        outcome_semantics=tuple(payload["outcome_semantics"]),
        no_backfill_invariant=payload["no_backfill_invariant"],
        construction_semantics=payload["construction_semantics"],
        immutability_rules=tuple(payload["immutability_rules"]),
    )
    recorded = payload.get("protocol_fingerprint")
    if recorded and recorded != protocol.protocol_fingerprint:
        raise ValueError("static protocol fingerprint mismatch")
    return protocol


def verify_phase8_authorization(
    protocol: ForwardValidationProtocol,
    phase8_root: str | Path,
) -> dict[str, str]:
    root = Path(phase8_root).resolve()
    manifest_path = root / "portfolio_research_synthesis_manifest.json"
    decisions_path = root / "portfolio_research_decisions.csv"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_hash = _file_hash(manifest_path)
    manifest_identity = _manifest_identity(manifest)
    if manifest_hash != protocol.source_phase8_manifest_sha256 or manifest_identity != protocol.source_phase8_manifest_identity:
        raise ValueError("Phase 8 manifest provenance does not match prospective protocol")
    if manifest.get("result_identity") != protocol.source_phase8_result_identity:
        raise ValueError("Phase 8 result identity does not match prospective protocol")
    artifact = manifest.get("artifacts", {}).get(decisions_path.name, {})
    if _file_hash(decisions_path) != artifact.get("sha256"):
        raise ValueError("Phase 8 decisions artifact SHA-256 mismatch")
    if manifest.get("selection_policy") != protocol.selection_policy or manifest.get("weighting_policy") != protocol.weighting_policy:
        raise ValueError("Phase 8 policy authorization mismatch")
    temporal = manifest.get("temporal_partition") or []
    if not temporal or temporal[-1].get("end_date") != protocol.activation_market_session_boundary:
        raise ValueError("activation boundary does not match frozen historical provenance")
    with decisions_path.open("r", encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle))
    authorized = tuple(sorted(
        (int(row["requested_budget"]), int(row["horizon_sessions"]))
        for row in rows if row["decision"] == "ADVANCE_TO_FORWARD_VALIDATION"
    ))
    expected = tuple((protocol.budget, horizon) for horizon in protocol.tracked_horizons)
    if authorized != expected:
        raise ValueError("Phase 8 authorized scenarios do not match prospective protocol")
    return {
        "phase8_manifest_sha256": manifest_hash,
        "phase8_manifest_identity": manifest_identity,
        "phase8_result_identity": manifest["result_identity"],
        "phase8_decisions_sha256": artifact["sha256"],
    }


def create_activation(
    protocol: ForwardValidationProtocol,
    *,
    activated_at_utc: str,
    confirmed_latest_completed_session: str,
) -> ForwardProtocolActivation:
    if confirmed_latest_completed_session < protocol.activation_market_session_boundary:
        raise ValueError("confirmed completed session predates historical activation boundary")
    payload = {
        "protocol_id": protocol.protocol_id,
        "protocol_fingerprint": protocol.protocol_fingerprint,
        "activated_at_utc": activated_at_utc,
        "activation_market_session_boundary": protocol.activation_market_session_boundary,
        "operational_start_after_session": confirmed_latest_completed_session,
        "source_phase8_manifest_identity": protocol.source_phase8_manifest_identity,
        "source_phase8_manifest_sha256": protocol.source_phase8_manifest_sha256,
        "selection_policy": protocol.selection_policy,
        "weighting_policy": protocol.weighting_policy,
        "budget": protocol.budget,
        "tracked_horizons": protocol.tracked_horizons,
        "benchmark": protocol.benchmark,
    }
    return ForwardProtocolActivation(
        protocol.protocol_id, protocol.protocol_fingerprint, protocol.protocol_version,
        activated_at_utc, protocol.activation_market_session_boundary,
        confirmed_latest_completed_session, protocol.source_phase8_manifest_identity,
        protocol.source_phase8_manifest_sha256, protocol.selection_policy,
        protocol.weighting_policy, protocol.budget, protocol.tracked_horizons,
        protocol.benchmark, identity(payload),
    )
