from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json

import pytest

from quantlab.ingestion_provenance import (
    EXPLICIT_INGESTION_MANIFEST,
    LEGACY_UNVERIFIED,
    AdjustmentMode,
    CorporateActionCoverage,
    CorporateActionProvenance,
    LabelExclusionReason,
    MarketDataIngestionManifest,
    NormalizationSpecification,
    PriceUnitSemantics,
    ProspectiveLabelObservation,
    SecurityIdentityState,
    SecurityReference,
    VerificationState,
    assess_prospective_label_eligibility,
    classify_record_provenance,
)


_HASH_A = "a" * 64
_HASH_B = "b" * 64


def _normalization() -> NormalizationSpecification:
    return NormalizationSpecification(
        version="save_price_data_v1",
        required_columns=("time", "open", "high", "low", "close", "volume", "symbol"),
        symbol_normalization="strip_then_uppercase",
        session_normalization="parse_then_iso_date",
        duplicate_policy="keep_last_by_symbol_session",
        numeric_coercion="pandas_to_numeric_errors_coerce_then_reject_invalid",
        price_scale_transform="none",
    )


def _unit(*, verified: bool = True) -> PriceUnitSemantics:
    return PriceUnitSemantics(
        unit="provider_native_price_unit",
        verification_state=VerificationState.VERIFIED if verified else VerificationState.UNKNOWN,
        verification_basis=("provider specification section 2",) if verified else (),
        source_references=("provider-spec-sha256:abc",) if verified else (),
    )


def _security(*, verified: bool = True, security_id: str = "SEC-AAA") -> SecurityReference:
    return SecurityReference(
        symbol="aaa",
        canonical_security_id=security_id if verified else None,
        identity_state=(
            SecurityIdentityState.VERIFIED_STABLE if verified else SecurityIdentityState.UNKNOWN
        ),
        source_references=("security-master:2026-10-02",) if verified else (),
        limitations=("ticker history before source coverage is not asserted",),
    )


def _corporate_actions(*, complete: bool = True) -> CorporateActionProvenance:
    return CorporateActionProvenance(
        coverage=(
            CorporateActionCoverage.VERIFIED_COMPLETE if complete else CorporateActionCoverage.UNKNOWN
        ),
        coverage_start=date(2026, 1, 1) if complete else None,
        coverage_end=date(2026, 12, 31) if complete else None,
        covered_event_types=("splits", "rights", "stock_dividends", "ticker_changes") if complete else (),
        source_references=("corporate-action-ledger:2026-v1",) if complete else (),
        limitations=() if complete else ("no attributable corporate-action source",),
    )


def _manifest(
    *,
    start: date = date(2026, 1, 1),
    end: date = date(2026, 1, 31),
    adjustment: AdjustmentMode = AdjustmentMode.VERIFIED_RAW,
    verified_unit: bool = True,
    complete_actions: bool = True,
    verified_security: bool = True,
    security_id: str = "SEC-AAA",
    retrieval: datetime = datetime(2026, 2, 1, 8, 30, tzinfo=timezone.utc),
) -> MarketDataIngestionManifest:
    verified_adjustment = adjustment is not AdjustmentMode.UNKNOWN
    return MarketDataIngestionManifest(
        provider_identity="KBS",
        endpoint_identity="Quote.history:1D",
        package_name="vnstock",
        package_version="future-pinned-version",
        retrieval_timestamp_utc=retrieval,
        security=_security(verified=verified_security, security_id=security_id),
        requested_start=start,
        requested_end=end,
        returned_start=start,
        returned_end=end,
        row_count=21,
        raw_data_content_sha256=_HASH_A,
        normalized_data_content_sha256=_HASH_B,
        normalization=_normalization(),
        input_price_unit=_unit(verified=verified_unit),
        stored_price_unit=_unit(verified=verified_unit),
        adjustment_mode=adjustment,
        adjustment_verification_basis=("provider adjustment contract",) if verified_adjustment else (),
        adjustment_source_references=("provider-spec-sha256:def",) if verified_adjustment else (),
        corporate_actions=_corporate_actions(complete=complete_actions),
        ingestion_succeeded=True,
        ohlcv_structurally_valid=True,
        raw_archive_reference="immutable://raw/batch-1",
    )


def _observation(
    manifest: MarketDataIngestionManifest,
    **changes,
) -> ProspectiveLabelObservation:
    values = {
        "observation_id": "AAA:2026-01-20:H5",
        "symbol": "AAA",
        "feature_window_start": date(2026, 1, 1),
        "formation_session": date(2026, 1, 20),
        "maturity_session": date(2026, 1, 27),
        "required_feature_sessions": 27,
        "observed_feature_sessions": 27,
        "feature_session_coverage_verified": True,
        "feature_input_manifests": (manifest,),
        "formation_manifest": manifest,
        "maturity_manifest": manifest,
        "exact_maturity_price_available": True,
    }
    values.update(changes)
    return ProspectiveLabelObservation(**values)


def test_manifest_identity_and_serialization_are_deterministic_and_utc() -> None:
    offset = timezone(timedelta(hours=7))
    first = _manifest(retrieval=datetime(2026, 2, 1, 15, 30, tzinfo=offset))
    second = _manifest(retrieval=datetime(2026, 2, 1, 8, 30, tzinfo=timezone.utc))

    assert first.ingestion_identity == second.ingestion_identity
    assert first.retrieval_timestamp_utc == datetime(2026, 2, 1, 8, 30, tzinfo=timezone.utc)
    payload = json.loads(first.to_json_bytes())
    assert payload["retrieval_timestamp_utc"] == "2026-02-01T08:30:00Z"
    assert payload["ingestion_identity"] == first.ingestion_identity


def test_manifest_requires_aware_timestamp_and_attributable_verification() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _manifest(retrieval=datetime(2026, 2, 1, 8, 30))
    with pytest.raises(ValueError, match="price-unit semantics require basis"):
        PriceUnitSemantics("VND", VerificationState.VERIFIED)
    with pytest.raises(ValueError, match="adjustment mode requires basis"):
        replace(
            _manifest(adjustment=AdjustmentMode.UNKNOWN),
            adjustment_mode=AdjustmentMode.VERIFIED_RAW,
        )
    with pytest.raises(ValueError, match="corporate-action coverage requires"):
        CorporateActionProvenance(CorporateActionCoverage.VERIFIED_COMPLETE)


def test_success_structure_and_economic_validity_are_separate_states() -> None:
    unknown = _manifest(
        adjustment=AdjustmentMode.UNKNOWN,
        verified_unit=False,
        complete_actions=False,
        verified_security=False,
    )
    assert unknown.ingestion_succeeded is True
    assert unknown.ohlcv_structurally_valid is True
    assert unknown.economically_valid_return_semantics is False
    assert unknown.corporate_action_safe is False
    assert unknown.as_dict()["adjustment"]["mode"] == "UNKNOWN"

    with pytest.raises(ValueError, match="successful non-empty ingestion"):
        replace(_manifest(), ingestion_succeeded=False)


def test_verified_prospective_observation_is_eligible_with_explicit_denominator() -> None:
    manifest = _manifest()
    result = assess_prospective_label_eligibility((_observation(manifest),))

    assert result.formation_observation_denominator == 1
    assert result.provenance_eligible_numerator == 1
    assert dict(result.exclusion_reason_counts) == {}
    assert result.assessments[0].eligible is True
    assert result.assessments[0].ingestion_identities == (manifest.ingestion_identity,)


def test_unknown_semantics_and_incomplete_actions_fail_closed_without_hiding_counts() -> None:
    manifest = _manifest(
        adjustment=AdjustmentMode.UNKNOWN,
        verified_unit=False,
        complete_actions=False,
        verified_security=False,
    )
    result = assess_prospective_label_eligibility((_observation(manifest),))
    reasons = set(result.assessments[0].exclusion_reasons)

    assert result.formation_observation_denominator == 1
    assert result.provenance_eligible_numerator == 0
    assert LabelExclusionReason.FEATURE_PRICE_UNIT_UNVERIFIED in reasons
    assert LabelExclusionReason.FEATURE_ADJUSTMENT_UNVERIFIED in reasons
    assert LabelExclusionReason.RETURN_ADJUSTMENT_UNVERIFIED in reasons
    assert LabelExclusionReason.CORPORATE_ACTION_COVERAGE_INCOMPLETE in reasons
    assert LabelExclusionReason.SECURITY_IDENTITY_UNVERIFIED in reasons
    assert sum(result.exclusion_reason_counts.values()) == len(reasons)


def test_warmup_exact_maturity_and_retrospective_substitution_are_explicit() -> None:
    manifest = _manifest()
    result = assess_prospective_label_eligibility((
        _observation(
            manifest,
            observed_feature_sessions=26,
            feature_session_coverage_verified=False,
            exact_maturity_price_available=False,
            retrospective_evidence_substitution=True,
        ),
    ))
    reasons = set(result.assessments[0].exclusion_reasons)
    assert {
        LabelExclusionReason.FEATURE_WARMUP_INCOMPLETE,
        LabelExclusionReason.FEATURE_SESSION_COVERAGE_UNVERIFIED,
        LabelExclusionReason.EXACT_MATURITY_PRICE_MISSING,
        LabelExclusionReason.RETROSPECTIVE_EVIDENCE_SUBSTITUTION,
    } <= reasons


def test_security_and_adjustment_discontinuities_are_rejected() -> None:
    raw = _manifest()
    adjusted_other_security = _manifest(
        adjustment=AdjustmentMode.VERIFIED_ADJUSTED,
        security_id="SEC-OTHER",
    )
    result = assess_prospective_label_eligibility((
        _observation(raw, maturity_manifest=adjusted_other_security),
    ))
    reasons = set(result.assessments[0].exclusion_reasons)
    assert LabelExclusionReason.RETURN_ADJUSTMENT_INCONSISTENT in reasons
    assert LabelExclusionReason.SECURITY_IDENTITY_MISMATCH in reasons


def test_range_and_price_unit_discontinuities_are_rejected() -> None:
    manifest = _manifest()
    different_unit = replace(
        manifest,
        input_price_unit=replace(_unit(), unit="VND"),
        stored_price_unit=replace(_unit(), unit="VND"),
        requested_start=date(2026, 1, 21),
        returned_start=date(2026, 1, 21),
    )
    result = assess_prospective_label_eligibility((
        _observation(
            manifest,
            feature_input_manifests=(manifest, different_unit),
            formation_manifest=different_unit,
            maturity_manifest=different_unit,
        ),
    ))
    reasons = set(result.assessments[0].exclusion_reasons)
    assert LabelExclusionReason.FEATURE_PRICE_UNIT_INCONSISTENT in reasons
    assert LabelExclusionReason.FORMATION_SESSION_NOT_COVERED in reasons


def test_batch_identity_is_order_invariant_and_binds_exclusions() -> None:
    manifest = _manifest()
    eligible = _observation(manifest)
    excluded = _observation(
        manifest,
        observation_id="AAA:2026-01-21:H5",
        formation_session=date(2026, 1, 21),
        exact_maturity_price_available=False,
    )
    forward = assess_prospective_label_eligibility((eligible, excluded))
    reversed_result = assess_prospective_label_eligibility((excluded, eligible))
    changed = assess_prospective_label_eligibility((eligible,))

    assert forward.identity == reversed_result.identity
    assert forward.assessments == reversed_result.assessments
    assert forward.identity != changed.identity
    assert forward.formation_observation_denominator == 2
    assert forward.provenance_eligible_numerator == 1
    with pytest.raises(TypeError):
        forward.exclusion_reason_counts["x"] = 1


def test_historical_rows_are_never_upgraded_by_a_retrospective_manifest() -> None:
    manifest = _manifest()
    assert classify_record_provenance(None, manifest_captured_at_ingestion=False) == LEGACY_UNVERIFIED
    assert classify_record_provenance(manifest, manifest_captured_at_ingestion=False) == LEGACY_UNVERIFIED
    assert classify_record_provenance(manifest, manifest_captured_at_ingestion=True) == EXPLICIT_INGESTION_MANIFEST
