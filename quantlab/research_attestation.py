from __future__ import annotations

"""B5-A: research attestation of frozen Q70 artifacts to Strategy Identity v3.

An attestation links a legacy research artifact (its original label,
fingerprint files and file hashes, all preserved untouched) to a
Strategy Identity v3 ``signal_identity`` that is RECONSTRUCTED from the
repository code plus the artifact's own recorded parameters.

It states, claim by claim, what code and artifact prove and what they do not.
It never claims equivalence with any deployed paper runtime, never rewrites
the artifact, and is stored in a separate append-only JSONL registry.

Usage (read-only on the artifact; ``--append`` writes only the registry)::

    python -m quantlab.research_attestation --artifact research_results/<dir> [--append]
"""

import argparse
import re
from hashlib import sha256
import inspect
import json
from pathlib import Path
from typing import Any, Mapping

from core.paths import PROJECT_ROOT
from quantlab.identity import canonical_identity_value, canonical_json


ATTESTATION_CONTRACT = "quant.strategy_identity.research_attestation"
# v2 (2026-10-06 review P2-1): claims validated against persisted sources.
# v3 (second review P2-2): EVERY persisted source in the artifact (all JSON
# blocks including aliases such as policy/frozen_q70 and parity/paper_parity,
# and the per-arm summary.csv / root comparison.csv rows) is checked for every
# claim alias; contradictions refuse the attestation; persisted
# trailing_enabled is checked; execution_timing is claimed and reported
# UNPROVEN when no source records it. v1/v2 lines stay (append-only).
# v4 (third review P2-3/P2-4): evidence is SEMANTICALLY SCOPED per claim
# (allowed file category + block path + key/column + value type); same-named
# keys elsewhere are ignored; every occurrence is validated with type-aware
# equality (bool never equals 1), independent of CSV row order.
ATTESTATION_VERSION = "v4"
RECONSTRUCTED_FROM_CODE_AND_ARTIFACT = "RECONSTRUCTED_FROM_CODE_AND_ARTIFACT"
DEFAULT_REGISTRY_PATH = PROJECT_ROOT / "research" / "attestations" / "strategy_identity_v3_research.jsonl"
REGISTRY_RECORD_ID = "frozen-q70-historical-policy-v1"
LEGACY_POLICY_LABEL = "Q70_FROZEN/hybrid_trend_donchian/ATR2x5/fixed"
_ARM_UNIVERSE_MODES = {
    "database_coverage": "database_coverage",
    "legacy_current_vn100": "legacy_current_vn100_retroactive",
}


class ResearchAttestationError(ValueError):
    """The artifact does not support the attestation (nothing is written)."""


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchAttestationError(f"unreadable artifact file: {path.name}") from exc


def _arm_universe_mode(arm: str) -> str:
    for prefix, mode in _ARM_UNIVERSE_MODES.items():
        if arm.startswith(prefix):
            return mode
    raise ResearchAttestationError(f"unknown research arm universe: {arm}")


def _registry_reference(registry_path: Path) -> dict[str, Any] | None:
    if not registry_path.is_file():
        return None
    registry = _read_json(registry_path)
    for record in registry.get("records", ()):
        if record.get("hypothesis_id") == REGISTRY_RECORD_ID:
            return {
                "hypothesis_id": REGISTRY_RECORD_ID,
                "status": record.get("status"),
                "factor_policy_reference": record.get("factor_policy_reference"),
                "specification_fingerprint": record.get("specification_fingerprint"),
                "identity": record.get("identity"),
            }
    return None


# ---------------------------------------------------------------------------
# Semantically scoped evidence (third review P2-3/P2-4)
# ---------------------------------------------------------------------------
# Every claim lists the ONLY artifact contexts that may prove or contradict it:
# a file category, a block path inside it, the key/column, and the value type.
# A same-named key anywhere else (benchmark settings, example configs,
# unrelated CSV columns) is not evidence. All listed aliases are checked
# (policy and frozen_q70; parity and paper_parity; ...), never just the first.
#
# File categories:
#   manifest      experiment_manifest.json (artifact root)
#   fingerprint   <arm>/policy_fingerprint.json
#   summary       <arm>/summary.csv (strategy output row)
#   comparison    comparison.csv (artifact root, row of this arm)
#   decisions     <arm>/candidate_decision_oos.csv (strategy decision ledger)
_STRATEGY_POLICY_BLOCKS = ("policy", "frozen_q70")
_EXECUTION_BLOCKS = ("paper_execution", "parity", "paper_parity")


def _in_blocks(blocks: tuple[str, ...], key: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(("fingerprint", (block, key)) for block in blocks)


# claim, claimed value, value type, core signal claim?, scoped sources
_CLAIM_SPECS: tuple[tuple[str, Any, str, bool, tuple[tuple[str, tuple[str, ...]], ...]], ...] = (
    ("entry_model", "hybrid_trend_donchian", "str", True,
     _in_blocks(_STRATEGY_POLICY_BLOCKS, "entry_model")),
    ("entry_model_constructed_as_hybrid_trend_context", True, "bool", True, ()),
    ("quality_enabled", True, "bool", True,
     _in_blocks(_STRATEGY_POLICY_BLOCKS, "quality_enabled")),
    ("quality_threshold", 0.70, "number", True, (
        ("manifest", ("q70_threshold",)),
        *_in_blocks(_STRATEGY_POLICY_BLOCKS, "quality_threshold"),
        ("decisions", ("quality_threshold",)),
    )),
    ("policy_label", LEGACY_POLICY_LABEL, "str", False, (
        ("manifest", ("policy_fingerprint",)),
        ("summary", ("policy_fingerprint",)),
        ("comparison", ("policy_fingerprint",)),
    )),
    ("stop_atr_multiplier", 2.0, "number", False, (
        *_in_blocks(_STRATEGY_POLICY_BLOCKS, "stop_atr_multiplier"),
        *_in_blocks(_EXECUTION_BLOCKS, "atr_stop_multiplier"),
    )),
    ("target_atr_multiplier", 5.0, "number", False, (
        *_in_blocks(_STRATEGY_POLICY_BLOCKS, "target_atr_multiplier"),
        *_in_blocks(_EXECUTION_BLOCKS, "target_atr_multiplier"),
    )),
    ("trailing_enabled", False, "bool", False, (
        ("manifest", ("trailing_enabled",)),
        ("fingerprint", ("trailing_enabled",)),
        *_in_blocks(_STRATEGY_POLICY_BLOCKS + _EXECUTION_BLOCKS, "trailing_enabled"),
    )),
    ("execution_timing", "next_open", "str", False, (
        ("manifest", ("execution_timing",)),
        ("fingerprint", ("execution_timing",)),
        *_in_blocks(_EXECUTION_BLOCKS, "execution_timing"),
    )),
    ("position_sizer", "atr_risk", "str", False, _in_blocks(_EXECUTION_BLOCKS, "position_sizer")),
    ("risk_per_trade_pct", 1.0, "number", False,
     _in_blocks(_EXECUTION_BLOCKS, "risk_per_trade_pct")),
    ("maximum_orders_per_scan", 3, "number", False,
     _in_blocks(_EXECUTION_BLOCKS, "maximum_orders_per_scan")),
    ("maximum_open_positions", 10, "number", False, (
        *_in_blocks(_STRATEGY_POLICY_BLOCKS, "max_open_positions"),
        *_in_blocks(_EXECUTION_BLOCKS, "maximum_open_positions"),
    )),
    ("commission_rate", 0.0015, "number", False, _in_blocks(_EXECUTION_BLOCKS, "commission_rate")),
    ("slippage_bps", 5.0, "number", False, _in_blocks(_EXECUTION_BLOCKS, "slippage_bps")),
    ("sell_tax_rate", 0.001, "number", False, _in_blocks(_EXECUTION_BLOCKS, "sell_tax_rate")),
)

_CSV_TRUE = {"true", "True", "TRUE"}
_CSV_FALSE = {"false", "False", "FALSE"}


def _parse_csv_cell(text: str) -> Any:
    """Type-preserving CSV parse: bool only for true/false words, never 1/0."""
    value = str(text).strip()
    if value in _CSV_TRUE:
        return True
    if value in _CSV_FALSE:
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _typed_match(value_type: str, claimed: Any, value: Any) -> bool:
    """Type-aware equality: bool never equals 1/0, numbers never accept bools."""
    if value_type == "bool":
        return type(value) is bool and value is claimed
    if value_type == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and float(value) == float(claimed)
        )
    if value_type == "str":
        return isinstance(value, str) and value == claimed
    raise ValueError(f"unknown claim value type {value_type!r}")


def _csv_rows(path: Path, *, arm: str | None) -> list[dict[str, str]]:
    import csv

    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if arm is not None:
        rows = [row for row in rows if row.get("arm") == arm]
    return rows


def _scoped_observations(
    *,
    directory: Path,
    arm_dir: Path,
    arm: str,
    manifest: Mapping[str, Any],
    fingerprint: Mapping[str, Any],
    sources: tuple[tuple[str, tuple[str, ...]], ...],
) -> list[tuple[str, Any]]:
    """Every value at the claim's allowed contexts, one entry per occurrence."""
    observed: list[tuple[str, Any]] = []
    csv_files = {
        "summary": (arm_dir / "summary.csv", None, f"{arm}/summary.csv"),
        "comparison": (directory / "comparison.csv", arm, "comparison.csv"),
        "decisions": (arm_dir / "candidate_decision_oos.csv", None, f"{arm}/candidate_decision_oos.csv"),
    }
    for category, path in sources:
        if category in {"manifest", "fingerprint"}:
            node: Any = manifest if category == "manifest" else fingerprint
            label = "experiment_manifest.json" if category == "manifest" else f"{arm}/policy_fingerprint.json"
            for part in path:
                node = node.get(part, _MISSING) if isinstance(node, Mapping) else _MISSING
            if node is not _MISSING:
                observed.append((f"{label}:{'.'.join(path)}", node))
        else:
            file_path, row_arm, label = csv_files[category]
            (column,) = path
            for index, row in enumerate(_csv_rows(file_path, arm=row_arm)):
                if column in row and row[column] is not None and str(row[column]).strip() != "":
                    observed.append((f"{label}[{index}]:{column}", _parse_csv_cell(row[column])))
    return observed


_MISSING = object()


def _validated_claims(
    *,
    observations: Mapping[str, list[tuple[str, Any]]],
    code: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate each claim against EVERY occurrence in its allowed contexts.

    Each observed value is checked individually with type-aware equality (no
    de-duplication, so row order and bool/int equality cannot change the
    result). PROVEN needs at least one occurrence and no mismatch; any
    mismatch is a contradiction (the caller refuses); none is UNPROVEN.
    """
    claims: list[dict[str, Any]] = []
    contradictions: list[str] = []
    for name, claimed, value_type, core, _sources in _CLAIM_SPECS:
        observed = list(observations.get(name, ()))
        observed.extend((f"code:{label}", value) for label, value in sorted(code.get(name, {}).items()))
        bad = [(label, value) for label, value in observed if not _typed_match(value_type, claimed, value)]
        contradictions.extend(
            f"{name}: {label}={value!r} (claim requires {value_type} {claimed!r})"
            for label, value in sorted(bad, key=lambda item: (item[0], repr(item[1])))
        )
        proven = bool(observed) and not bad
        claims.append(
            {
                "claim": name,
                "expected": claimed,
                "value_type": value_type,
                "core": core,
                "proven": proven,
                "status": "PROVEN" if proven else ("CONTRADICTED" if observed else "UNPROVEN"),
                # Distinct source locations (CSV row indices collapsed).
                "sources": sorted({re.sub(r"\[\d+\]", "", label) for label, _ in observed}),
            }
        )
    return claims, contradictions


def build_frozen_q70_research_attestation(
    artifact_dir: str | Path,
    *,
    root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    """Build (do not store) one attestation per arm of a frozen Q70 artifact."""
    from backtesting import frozen_q70_evaluator as evaluator
    from config.strategy_config import Q70_FROZEN
    from quantlab.strategy_contract import canonical_signal_contract
    from quantlab.strategy_identity import build_strategy_identity
    from quantlab.strategy_identity_runtime import collect_frozen_research_signal_identity
    from strategy.hybrid_trend_donchian_entry import HybridTrendDonchianEntryModel
    from strategy.paper_v2_gate import QUALITY_FEATURES

    directory = Path(artifact_dir)
    if not directory.is_absolute():
        directory = root / directory
    manifest_path = directory / "experiment_manifest.json"
    if not manifest_path.is_file():
        raise ResearchAttestationError("artifact has no experiment_manifest.json")
    manifest = _read_json(manifest_path)
    files = sorted(item for item in directory.rglob("*") if item.is_file())
    file_hashes = {
        item.relative_to(directory).as_posix(): _sha256_file(item) for item in files
    }

    evaluator_source = inspect.getsource(evaluator.run_frozen_q70_backtest)
    warmup_default = (
        inspect.signature(evaluator.run_frozen_q70_backtest).parameters["warmup_bars"].default
    )
    code_uses_hybrid = "HybridTrendDonchianEntryModel()" in evaluator_source
    code_threshold = float(evaluator._Q70_THRESHOLD)
    code_trailing_disabled = '"trailing_enabled": False' in inspect.getsource(evaluator)
    canonical_q70 = canonical_signal_contract("Q70_FROZEN")
    canonical_blocks = build_strategy_identity(
        strategy="Q70_FROZEN", signal_contract=canonical_q70, execution_contract=None
    ).block_fingerprints

    arms: list[dict[str, Any]] = []
    for arm in manifest.get("arms", ()):
        arm_dir = directory / str(arm)
        fingerprint_path = arm_dir / "policy_fingerprint.json"
        if not fingerprint_path.is_file():
            raise ResearchAttestationError(f"arm {arm} has no policy_fingerprint.json")
        recorded = _read_json(fingerprint_path)
        universe_mode = _arm_universe_mode(str(arm))
        observations = {
            name: _scoped_observations(
                directory=directory,
                arm_dir=arm_dir,
                arm=str(arm),
                manifest=manifest,
                fingerprint=recorded,
                sources=sources,
            )
            for name, _claimed, _type, _core, sources in _CLAIM_SPECS
        }
        claims, contradictions = _validated_claims(
            observations=observations,
            code={
                "entry_model": {"config.strategy_config.Q70_FROZEN.entry_model": Q70_FROZEN.entry_model},
                "entry_model_constructed_as_hybrid_trend_context": {
                    "frozen_q70_evaluator constructs HybridTrendDonchianEntryModel()": code_uses_hybrid,
                },
                "quality_enabled": {"config.strategy_config.Q70_FROZEN.quality_enabled": Q70_FROZEN.quality_enabled},
                "quality_threshold": {"frozen_q70_evaluator._Q70_THRESHOLD": code_threshold},
                **(
                    {"trailing_enabled": {"frozen_q70_evaluator metrics trailing_enabled": False}}
                    if code_trailing_disabled
                    else {}
                ),
            },
        )
        if contradictions:
            raise ResearchAttestationError(
                f"arm {arm}: persisted evidence contradicts claims: "
                + "; ".join(contradictions)
                + "; nothing attested"
            )
        unproven_core = [
            item["claim"] for item in claims if item["core"] and not item["proven"]
        ]
        if unproven_core:
            raise ResearchAttestationError(
                f"arm {arm}: core signal claims lack evidence {unproven_core}; nothing attested"
            )
        # Every recorded threshold source agreed with the claim (else refused).
        threshold = float(next(item for item in claims if item["claim"] == "quality_threshold")["expected"])
        identity = collect_frozen_research_signal_identity(
            entry_model=HybridTrendDonchianEntryModel(),
            quality_threshold=threshold,
            quality_features=QUALITY_FEATURES,
            universe_mode=universe_mode,
            warmup_bars=int(warmup_default),
        )
        differing = sorted(
            key
            for key, value in identity.block_fingerprints.items()
            if canonical_blocks.get(key) != value
        )
        arms.append(
            {
                "arm": str(arm),
                "legacy_identity": {
                    "policy_label": manifest.get("policy_fingerprint"),
                    "policy_fingerprint_file_sha256": file_hashes[
                        fingerprint_path.relative_to(directory).as_posix()
                    ],
                },
                "reconstructed": {
                    "label": RECONSTRUCTED_FROM_CODE_AND_ARTIFACT,
                    "signal_identity": identity.signal_identity,
                    "block_fingerprints": dict(identity.block_fingerprints),
                    "eligibility": dict(identity.signal_contract["eligibility"]),
                    "blocks_differing_from_canonical_production_q70": differing,
                },
                "proven_claims": claims,
            }
        )

    attestation: dict[str, Any] = {
        "contract": ATTESTATION_CONTRACT,
        "version": ATTESTATION_VERSION,
        "label": RECONSTRUCTED_FROM_CODE_AND_ARTIFACT,
        "strategy": "Q70_FROZEN",
        "scope": "signal_only",
        "artifact": {
            "path": directory.relative_to(root).as_posix() if directory.is_relative_to(root) else directory.name,
            "latest_vnindex_date": manifest.get("latest_vnindex_date"),
            "file_sha256": file_hashes,
            "manifest_sha256": file_hashes["experiment_manifest.json"],
        },
        "legacy_registry_reference": _registry_reference(
            root / "research" / "alpha_hypotheses" / "registry_v1.json"
        ),
        "arms": arms,
        "reconstruction_inputs": {
            "warmup_bars": int(warmup_default),
            "warmup_source": "evaluator default; the archived runner passes no override",
            "quality_features": list(QUALITY_FEATURES),
        },
        "not_proven": [
            "Equivalence with any deployed/historical paper runtime (not claimed).",
            "Code version that produced the artifact: the manifest records no commit; "
            "reconstruction uses the current repository code.",
            "Declared code literals (indicator windows, regime classifier, gate state "
            "cutoffs, scoring sub-weights) are declared, not introspected.",
            "config/strategy.yaml content at run time is supported by git history "
            "(unchanged since 2026-07-30), not by the artifact itself.",
            "Research execution is NOT paper execution: no REGIME_CAPS_V1 overlay, "
            "ranking by signal_score with generation-order ties, simulator fills; "
            "only signal_identity is attested.",
        ],
    }
    attestation["attestation_id"] = sha256(
        canonical_json(canonical_identity_value(attestation))
    ).hexdigest()
    return attestation


def append_attestation(
    attestation: Mapping[str, Any],
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
) -> bool:
    """Append once to the JSONL registry; never rewrites existing lines.

    Returns False when an identical attestation is already present. A
    different attestation for the same artifact is appended as a new line
    (history is kept, not replaced).
    """
    path = Path(registry_path)
    attestation_id = str(attestation["attestation_id"])
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line).get("attestation_id") == attestation_id:
                return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical_json(canonical_identity_value(dict(attestation))).decode("utf-8") + "\n")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="B5-A research attestation (Strategy Identity v3)")
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY_PATH))
    parser.add_argument("--append", action="store_true")
    args = parser.parse_args(argv)
    attestation = build_frozen_q70_research_attestation(args.artifact)
    print(json.dumps(attestation, indent=2, sort_keys=True, ensure_ascii=False))
    if args.append:
        created = append_attestation(attestation, args.registry)
        print(f"registry {'appended' if created else 'unchanged (already attested)'}: {args.registry}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
