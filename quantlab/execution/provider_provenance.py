from __future__ import annotations

"""Immutable, local-evidence-only provider contract resolution for Phase 11D."""

from dataclasses import dataclass, field
from hashlib import sha256
from types import MappingProxyType
from typing import Mapping

from quantlab.identity import canonical_identity_value, canonical_json


CONTRACT = "quantlab.execution.provider_price_provenance"
VERSION = "v1"
PHASE11C_RESULT_IDENTITY = "4fb274958f452884fb6321245d3adaba011e052a8d05c34eb076f785cb7af9b0"
PHASE11C_DATABASE_SHA256 = "38c4c423590824d66452cfc169c8083fd13ec9e272ae9db7a58d36cbe91c4c6b"

EvidenceState = str
VERIFIED = "VERIFIED"
PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
AMBIGUOUS = "AMBIGUOUS"
UNAVAILABLE = "UNAVAILABLE"
_VALID_STATES = {VERIFIED, PARTIALLY_VERIFIED, AMBIGUOUS, UNAVAILABLE}


@dataclass(frozen=True, slots=True)
class ProvenanceConclusion:
    state: EvidenceState
    conclusion: str
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.state not in _VALID_STATES:
            raise ValueError(f"unsupported evidence state: {self.state}")
        if not self.conclusion.strip() or not self.evidence:
            raise ValueError("a provenance conclusion requires a statement and evidence")
        object.__setattr__(self, "evidence", tuple(self.evidence))


@dataclass(frozen=True, slots=True)
class ProviderWriter:
    path: str
    provider: str
    interval: str
    persistence_function: str


@dataclass(frozen=True, slots=True)
class ProviderProvenanceResult:
    contract: str
    version: str
    provider_name: str
    provider_version: str
    provider_module_paths: tuple[str, ...]
    provider_module_sha256: Mapping[str, str]
    current_unit: ProvenanceConclusion
    historical_unit: ProvenanceConclusion
    current_adjustment: ProvenanceConclusion
    historical_adjustment: ProvenanceConclusion
    equity_ohlc_scale_divisor: int | None
    index_ohlc_scale_divisor: int | None
    price_rounding_decimals: int | None
    adjustment_caller_selectable: bool
    writers: tuple[ProviderWriter, ...]
    writer_paths_equivalent: bool
    mixed_historical_provenance_possible: bool
    phase11c_result_identity: str
    phase11c_database_sha256: str
    current_database_sha256: str
    database_provenance_columns: tuple[str, ...]
    corporate_action_tables: tuple[str, ...]
    next_action: str
    no_network: bool = True
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        if self.phase11c_result_identity != PHASE11C_RESULT_IDENTITY:
            raise ValueError("Phase 11C result identity mismatch")
        if self.phase11c_database_sha256 != PHASE11C_DATABASE_SHA256:
            raise ValueError("Phase 11C database identity mismatch")
        if self.current_database_sha256 != self.phase11c_database_sha256:
            raise ValueError("current market database differs from frozen Phase 11C database")
        if self.no_network is not True:
            raise ValueError("Phase 11D provenance audit must be explicitly no-network")
        if self.next_action not in {
            "LOCAL_EVIDENCE_SUFFICIENT",
            "BOUNDED_PROVIDER_VERIFICATION_REQUIRED",
            "HISTORICAL_PROVENANCE_MIGRATION_REQUIRED",
            "MONETARY_EXECUTION_RESEARCH_DEFERRED",
        }:
            raise ValueError("unsupported next-action classification")
        writers = tuple(sorted(self.writers, key=lambda row: row.path))
        paths = tuple(sorted(self.provider_module_paths))
        hashes = dict(sorted(self.provider_module_sha256.items()))
        if set(paths) != set(hashes):
            raise ValueError("every inspected provider module must have a source hash")
        object.__setattr__(self, "writers", writers)
        object.__setattr__(self, "provider_module_paths", paths)
        object.__setattr__(self, "provider_module_sha256", MappingProxyType(hashes))
        object.__setattr__(self, "database_provenance_columns", tuple(sorted(self.database_provenance_columns)))
        object.__setattr__(self, "corporate_action_tables", tuple(sorted(self.corporate_action_tables)))
        payload = {
            "contract": [self.contract, self.version],
            "provider": [self.provider_name, self.provider_version],
            "provider_module_paths": paths,
            "provider_module_sha256": hashes,
            "conclusions": {
                name: {"state": item.state, "conclusion": item.conclusion, "evidence": item.evidence}
                for name, item in (
                    ("current_unit", self.current_unit),
                    ("historical_unit", self.historical_unit),
                    ("current_adjustment", self.current_adjustment),
                    ("historical_adjustment", self.historical_adjustment),
                )
            },
            "transform": [self.equity_ohlc_scale_divisor, self.index_ohlc_scale_divisor, self.price_rounding_decimals],
            "adjustment_caller_selectable": self.adjustment_caller_selectable,
            "writers": [
                {"path": w.path, "provider": w.provider, "interval": w.interval, "persistence_function": w.persistence_function}
                for w in writers
            ],
            "writer_paths_equivalent": self.writer_paths_equivalent,
            "mixed_historical_provenance_possible": self.mixed_historical_provenance_possible,
            "phase11c": [self.phase11c_result_identity, self.phase11c_database_sha256],
            "current_database_sha256": self.current_database_sha256,
            "database_provenance_columns": self.database_provenance_columns,
            "corporate_action_tables": self.corporate_action_tables,
            "next_action": self.next_action,
            "no_network": self.no_network,
        }
        object.__setattr__(self, "identity", sha256(canonical_json(canonical_identity_value(payload))).hexdigest())


def build_provider_provenance_result(
    *,
    provider_version: str,
    provider_module_paths: tuple[str, ...],
    provider_module_sha256: Mapping[str, str],
    current_unit: ProvenanceConclusion,
    historical_unit: ProvenanceConclusion,
    current_adjustment: ProvenanceConclusion,
    historical_adjustment: ProvenanceConclusion,
    equity_ohlc_scale_divisor: int | None,
    index_ohlc_scale_divisor: int | None,
    price_rounding_decimals: int | None,
    adjustment_caller_selectable: bool,
    writers: tuple[ProviderWriter, ...],
    writer_paths_equivalent: bool,
    mixed_historical_provenance_possible: bool,
    phase11c_result_identity: str = PHASE11C_RESULT_IDENTITY,
    phase11c_database_sha256: str = PHASE11C_DATABASE_SHA256,
    current_database_sha256: str = PHASE11C_DATABASE_SHA256,
    database_provenance_columns: tuple[str, ...] = (),
    corporate_action_tables: tuple[str, ...] = (),
    next_action: str = "HISTORICAL_PROVENANCE_MIGRATION_REQUIRED",
) -> ProviderProvenanceResult:
    """Create a deterministic result from explicit local evidence; never calls a provider."""
    if not provider_version.strip():
        raise ValueError("installed provider version is required")
    return ProviderProvenanceResult(
        CONTRACT, VERSION, "vnstock.KBS.Quote.history", provider_version,
        provider_module_paths, provider_module_sha256, current_unit, historical_unit,
        current_adjustment, historical_adjustment, equity_ohlc_scale_divisor,
        index_ohlc_scale_divisor, price_rounding_decimals,
        adjustment_caller_selectable, writers, writer_paths_equivalent,
        mixed_historical_provenance_possible, phase11c_result_identity,
        phase11c_database_sha256, current_database_sha256,
        database_provenance_columns, corporate_action_tables, next_action,
    )
