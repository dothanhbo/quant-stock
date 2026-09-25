from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from quantlab.evaluation import (
    NEUTRAL_ADX_RSI_COMPOSITE_COMPARISON_V1,
    NEUTRAL_PANEL_INCREMENTAL_FACTOR_ANALYSIS_V1,
    NEUTRAL_PANEL_FACTOR_REDUNDANCY_V1,
    NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V2,
    NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1,
    PanelTemporalDirection,
)
from research import run_quantlab_neutral_panel_factor_evaluation as runner


def _evaluation(dates: tuple[str, ...]):
    spec = NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1
    summaries = []
    daily = []
    for factor in spec.factors:
        for horizon in spec.horizons:
            for outcome in spec.outcome_fields:
                daily_ids = tuple(
                    f"daily-{factor}-{horizon}-{outcome}-{signal_date}"
                    for signal_date in dates
                )
                summaries.append(SimpleNamespace(
                    factor=factor,
                    factor_direction="UNSPECIFIED",
                    horizon_sessions=horizon,
                    outcome_field=outcome,
                    total_signal_dates=len(dates),
                    dates_with_any_pairwise_finite_observations=len(dates),
                    ic_defined_date_count=len(dates),
                    ic_coverage_pct=100.0,
                    mean_daily_rank_ic=0.25,
                    median_daily_rank_ic=0.25,
                    population_std_daily_ic=0.0,
                    positive_ic_date_count=len(dates),
                    zero_ic_date_count=0,
                    negative_ic_date_count=0,
                    positive_ic_rate=1.0,
                    bucket_defined_date_count=len(dates),
                    bucket_coverage_pct=100.0,
                    mean_daily_high_minus_low_mean_spread=1.5,
                    median_daily_high_minus_low_mean_spread=1.5,
                    mean_daily_high_minus_low_median_spread=1.5,
                    median_daily_high_minus_low_median_spread=1.5,
                    positive_mean_spread_date_count=len(dates),
                    zero_mean_spread_date_count=0,
                    negative_mean_spread_date_count=0,
                    positive_mean_spread_rate=1.0,
                    average_low_bucket_size=1.0,
                    average_high_bucket_size=1.0,
                    total_eligible_observations=4,
                    average_eligible_cross_section_size=2.0,
                    factor_availability_coverage_pct=100.0,
                    outcome_availability_coverage_pct=100.0,
                    warnings=("descriptive_only",),
                    included_daily_identities=daily_ids,
                    identity=f"summary-{factor}-{horizon}-{outcome}",
                ))
                for signal_date, daily_identity in zip(dates, daily_ids, strict=True):
                    daily.append(SimpleNamespace(
                        factor=factor,
                        factor_direction="UNSPECIFIED",
                        horizon_sessions=horizon,
                        outcome_field=outcome,
                        signal_date=signal_date,
                        total_observation_count=2,
                        factor_available_count=2,
                        outcome_available_count=2,
                        factor_usable_count=2,
                        outcome_usable_count=2,
                        pairwise_finite_eligible_count=2,
                        excluded_for_factor_count=0,
                        excluded_for_outcome_count=0,
                        excluded_for_both_count=0,
                        factor_nonfinite_available_count=0,
                        outcome_nonfinite_available_count=0,
                        rank_ic=None,
                        ic_undefined_reason="fewer_than_minimum_pairwise_finite_observations",
                        low_bucket_count=0,
                        high_bucket_count=0,
                        high_minus_low_mean_spread=None,
                        high_minus_low_median_spread=None,
                        bucket_undefined_reason="fewer_than_minimum_pairwise_finite_observations",
                        identity=daily_identity,
                    ))
    return SimpleNamespace(
        source_dataset_identity="dataset-id",
        source_dataset_content_identity="dataset-content",
        specification_fingerprint=spec.fingerprint,
        summaries=tuple(summaries),
        daily_evaluations=tuple(daily),
        identity="evaluation-id",
    )


def _temporal(evaluation: Any):
    spec = NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V2
    blocks = []
    summaries = []
    daily_by_key = {
        (item.factor, item.horizon_sessions, item.outcome_field): []
        for item in evaluation.daily_evaluations
    }
    for item in evaluation.daily_evaluations:
        daily_by_key[(item.factor, item.horizon_sessions, item.outcome_field)].append(item)
    summary_index = 0
    for factor in spec.factors:
        for horizon in spec.horizons:
            for outcome in spec.outcome_fields:
                block_ids = []
                for block in spec.blocks:
                    included = tuple(
                        item.identity
                        for item in daily_by_key[(factor, horizon, outcome)]
                        if block.start_date <= item.signal_date <= block.end_date
                    )
                    identity = f"block-{factor}-{horizon}-{outcome}-{block.name}"
                    spread_value = 1.25 if included else None
                    block_ids.append(identity)
                    blocks.append(SimpleNamespace(
                        factor=factor,
                        horizon_sessions=horizon,
                        outcome_field=outcome,
                        block_name=block.name,
                        block_start_date=block.start_date,
                        block_end_date=block.end_date,
                        total_source_signal_dates=len(included),
                        dates_with_any_eligible_observations=len(included),
                        ic_defined_date_count=0,
                        ic_coverage_pct=0.0,
                        mean_daily_rank_ic=None,
                        median_daily_rank_ic=None,
                        population_std_daily_ic=None,
                        minimum_daily_ic=None,
                        maximum_daily_ic=None,
                        positive_ic_date_count=0,
                        zero_ic_date_count=0,
                        negative_ic_date_count=0,
                        positive_ic_rate=None,
                        spread_defined_date_count=len(included),
                        spread_coverage_pct=100.0 if included else 0.0,
                        mean_daily_mean_spread=spread_value,
                        median_daily_mean_spread=spread_value,
                        population_std_daily_mean_spread=(0.0 if included else None),
                        minimum_daily_mean_spread=spread_value,
                        maximum_daily_mean_spread=spread_value,
                        positive_spread_date_count=len(included),
                        zero_spread_date_count=0,
                        negative_spread_date_count=0,
                        positive_spread_rate=1.0 if included else None,
                        mean_daily_median_spread=spread_value,
                        median_daily_median_spread=spread_value,
                        average_low_bucket_size=1.0 if included else None,
                        average_high_bucket_size=1.0 if included else None,
                        average_eligible_observation_count=(
                            None if not included else 2.0
                        ),
                        minimum_eligible_observation_count=(
                            None if not included else 2
                        ),
                        maximum_eligible_observation_count=(
                            None if not included else 2
                        ),
                        average_factor_availability_coverage_pct=(
                            None if not included else 100.0
                        ),
                        average_outcome_availability_coverage_pct=(
                            None if not included else 100.0
                        ),
                        ic_review_eligible=False,
                        spread_review_eligible=False,
                        included_daily_identities=included,
                        warnings=("synthetic_temporal_block",),
                        ic_undefined_reason="no_defined_daily_ic",
                        spread_undefined_reason=(
                            None if included else "no_defined_daily_spread"
                        ),
                        identity=identity,
                    ))
                positive_support = summary_index == 0
                negative_support = summary_index == 1
                mismatch = summary_index == 2
                coverage_only = summary_index == 3
                supported = positive_support or negative_support
                coverage = supported or mismatch or coverage_only
                ic_direction = (
                    PanelTemporalDirection.POSITIVE
                    if positive_support or mismatch
                    else (
                        PanelTemporalDirection.NEGATIVE
                        if negative_support
                        else (
                            PanelTemporalDirection.MIXED
                            if coverage_only
                            else PanelTemporalDirection.UNDEFINED
                        )
                    )
                )
                spread_direction = (
                    PanelTemporalDirection.POSITIVE
                    if positive_support
                    else (
                        PanelTemporalDirection.NEGATIVE
                        if negative_support or mismatch
                        else (
                            PanelTemporalDirection.MIXED
                            if coverage_only
                            else PanelTemporalDirection.UNDEFINED
                        )
                    )
                )
                summary_ic = (
                    -0.1 if negative_support else (0.1 if positive_support or mismatch else None)
                )
                summary_spread = (
                    -1.0
                    if negative_support or mismatch
                    else (1.0 if positive_support else None)
                )
                summaries.append(SimpleNamespace(
                    factor=factor,
                    horizon_sessions=horizon,
                    outcome_field=outcome,
                    total_block_count=4,
                    ic_review_eligible_block_count=3 if coverage else 0,
                    spread_review_eligible_block_count=3 if coverage else 0,
                    positive_mean_ic_block_count=3 if positive_support or mismatch else 0,
                    zero_mean_ic_block_count=0,
                    negative_mean_ic_block_count=3 if negative_support else 0,
                    positive_mean_spread_block_count=3 if positive_support else 0,
                    zero_mean_spread_block_count=0,
                    negative_mean_spread_block_count=(
                        3 if negative_support or mismatch else 0
                    ),
                    mean_ic_across_block_means=summary_ic,
                    median_ic_across_block_means=summary_ic,
                    minimum_block_mean_ic=summary_ic,
                    maximum_block_mean_ic=summary_ic,
                    range_block_mean_ic=0.0 if summary_ic is not None else None,
                    mean_spread_across_block_means=summary_spread,
                    median_spread_across_block_means=summary_spread,
                    minimum_block_mean_spread=summary_spread,
                    maximum_block_mean_spread=summary_spread,
                    range_block_mean_spread=(
                        0.0 if summary_spread is not None else None
                    ),
                    largest_absolute_mean_ic_block_concentration=(
                        1.0 / 3.0 if summary_ic is not None else None
                    ),
                    largest_absolute_mean_spread_block_concentration=(
                        1.0 / 3.0 if summary_spread is not None else None
                    ),
                    all_blocks_positive_ic=False,
                    all_blocks_positive_spread=False,
                    all_blocks_negative_ic=negative_support,
                    all_blocks_negative_spread=negative_support,
                    ic_consistent_direction=ic_direction,
                    spread_consistent_direction=spread_direction,
                    ic_sign_flip_count=0,
                    spread_sign_flip_count=0,
                    coverage_sufficient_for_temporal_review=coverage,
                    directionally_consistent_ic=supported or mismatch,
                    directionally_consistent_spread=supported or mismatch,
                    descriptive_temporal_support=supported,
                    included_block_identities=tuple(block_ids),
                    warnings=("descriptive_only",),
                    identity=f"temporal-summary-{factor}-{horizon}-{outcome}",
                ))
                summary_index += 1
    return SimpleNamespace(
        source_result_identity=evaluation.identity,
        source_specification_fingerprint=evaluation.specification_fingerprint,
        temporal_specification_fingerprint=spec.fingerprint,
        contract_name="quantlab.panel_factor_temporal_stability",
        contract_version="v2",
        block_results=tuple(blocks),
        summaries=tuple(summaries),
        identity="temporal-result-id",
    )


def _redundancy(dataset: Any, dates: tuple[str, ...]):
    spec = NEUTRAL_PANEL_FACTOR_REDUNDANCY_V1
    daily = []
    blocks = []
    summaries = []
    for pair_index, (first, second) in enumerate(spec.pairs):
        pair_daily = []
        correlations = (0.75, -0.5) if pair_index == 0 else (None, None)
        for signal_date, correlation in zip(dates, correlations, strict=True):
            identity = f"redundancy-daily-{first}-{second}-{signal_date}"
            item = SimpleNamespace(
                source_dataset_identity=dataset.identity,
                source_bounded_content_identity="bounded-predictors-id",
                specification_fingerprint=spec.fingerprint,
                signal_date=signal_date,
                first_factor=first,
                second_factor=second,
                observation_count=20,
                pairwise_finite_count=(20 if correlation is not None else 2),
                pairwise_coverage_pct=(100.0 if correlation is not None else 10.0),
                spearman_correlation=correlation,
                absolute_spearman_correlation=(
                    None if correlation is None else abs(correlation)
                ),
                undefined_reason=(
                    None
                    if correlation is not None
                    else "fewer_than_minimum_pairwise_finite_observations"
                ),
                pairwise_sample_evidence_sha256=("a" if correlation is not None else "b") * 64,
                identity=identity,
            )
            daily.append(item)
            pair_daily.append(item)
        block_ids = []
        for block in spec.blocks:
            included = tuple(
                item.identity
                for item in pair_daily
                if block.start_date <= item.signal_date <= block.end_date
            )
            defined_values = tuple(
                item.spearman_correlation
                for item in pair_daily
                if item.identity in included and item.spearman_correlation is not None
            )
            block_identity = f"redundancy-block-{first}-{second}-{block.name}"
            block_ids.append(block_identity)
            included_hash = runner._identity_collection_sha256(included)
            mean = (
                None if not defined_values else sum(defined_values) / len(defined_values)
            )
            blocks.append(SimpleNamespace(
                source_dataset_identity=dataset.identity,
                source_bounded_content_identity="bounded-predictors-id",
                source_daily_result_identity="redundancy-daily-result-id",
                specification_fingerprint=spec.fingerprint,
                first_factor=first,
                second_factor=second,
                block_name=block.name,
                block_start_date=block.start_date,
                block_end_date=block.end_date,
                total_signal_date_count=len(included),
                minimum_sample_date_count=len(defined_values),
                defined_correlation_date_count=len(defined_values),
                correlation_coverage_pct=(
                    0.0 if not included else len(defined_values) / len(included) * 100.0
                ),
                mean_daily_correlation=mean,
                median_daily_correlation=mean,
                population_std_daily_correlation=(
                    0.625 if len(defined_values) == 2 else (
                        0.0 if len(defined_values) == 1 else None
                    )
                ),
                minimum_daily_correlation=(
                    None if not defined_values else min(defined_values)
                ),
                maximum_daily_correlation=(
                    None if not defined_values else max(defined_values)
                ),
                mean_daily_absolute_correlation=(
                    None
                    if not defined_values
                    else sum(abs(value) for value in defined_values) / len(defined_values)
                ),
                median_daily_absolute_correlation=(
                    None if not defined_values else 0.625
                ),
                positive_correlation_date_count=sum(value > 0 for value in defined_values),
                zero_correlation_date_count=sum(value == 0 for value in defined_values),
                negative_correlation_date_count=sum(value < 0 for value in defined_values),
                positive_correlation_rate=(
                    None
                    if not defined_values
                    else sum(value > 0 for value in defined_values) / len(defined_values)
                ),
                zero_correlation_rate=(
                    None
                    if not defined_values
                    else sum(value == 0 for value in defined_values) / len(defined_values)
                ),
                negative_correlation_rate=(
                    None
                    if not defined_values
                    else sum(value < 0 for value in defined_values) / len(defined_values)
                ),
                average_pairwise_finite_count=(
                    None if not included else (20.0 if defined_values else 2.0)
                ),
                median_pairwise_finite_count=(
                    None if not included else (20.0 if defined_values else 2.0)
                ),
                included_daily_identities=included,
                included_daily_identity_count=len(included),
                included_daily_identities_sha256=included_hash,
                warnings=("synthetic_redundancy_block",),
                identity=block_identity,
            ))
        included_daily = tuple(item.identity for item in pair_daily)
        included_daily_hash = runner._identity_collection_sha256(included_daily)
        block_tuple = tuple(block_ids)
        block_hash = runner._identity_collection_sha256(block_tuple)
        defined = tuple(
            item.spearman_correlation
            for item in pair_daily
            if item.spearman_correlation is not None
        )
        summaries.append(SimpleNamespace(
            source_dataset_identity=dataset.identity,
            source_bounded_content_identity="bounded-predictors-id",
            source_daily_result_identity="redundancy-daily-result-id",
            specification_fingerprint=spec.fingerprint,
            first_factor=first,
            second_factor=second,
            total_signal_date_count=len(dates),
            minimum_sample_date_count=len(defined),
            defined_correlation_date_count=len(defined),
            correlation_coverage_pct=len(defined) / len(dates) * 100.0,
            mean_daily_correlation=(None if not defined else sum(defined) / len(defined)),
            median_daily_correlation=(None if not defined else 0.125),
            population_std_daily_correlation=(None if not defined else 0.625),
            minimum_daily_correlation=(None if not defined else min(defined)),
            maximum_daily_correlation=(None if not defined else max(defined)),
            mean_daily_absolute_correlation=(
                None if not defined else sum(abs(value) for value in defined) / len(defined)
            ),
            median_daily_absolute_correlation=(None if not defined else 0.625),
            positive_correlation_date_count=sum(value > 0 for value in defined),
            zero_correlation_date_count=sum(value == 0 for value in defined),
            negative_correlation_date_count=sum(value < 0 for value in defined),
            positive_correlation_rate=(
                None if not defined else sum(value > 0 for value in defined) / len(defined)
            ),
            zero_correlation_rate=(
                None if not defined else sum(value == 0 for value in defined) / len(defined)
            ),
            negative_correlation_rate=(
                None if not defined else sum(value < 0 for value in defined) / len(defined)
            ),
            average_pairwise_finite_count=(None if not defined else 20.0),
            median_pairwise_finite_count=(None if not defined else 20.0),
            included_daily_identities=included_daily,
            included_daily_identity_count=len(included_daily),
            included_daily_identities_sha256=included_daily_hash,
            ordered_block_identities=block_tuple,
            ordered_block_identity_count=len(block_tuple),
            ordered_block_identities_sha256=block_hash,
            blocks_meeting_temporal_review_count=0,
            chronological_sign_flip_count=(0 if not defined else 1),
            largest_absolute_block_mean_concentration=(None if not defined else 1.0),
            minimum_block_mean_correlation=(None if not defined else 0.125),
            maximum_block_mean_correlation=(None if not defined else 0.125),
            range_block_mean_correlation=(None if not defined else 0.0),
            minimum_block_mean_absolute_daily_correlation=(
                None if not defined else 0.625
            ),
            maximum_block_mean_absolute_daily_correlation=(
                None if not defined else 0.625
            ),
            range_block_mean_absolute_daily_correlation=(
                None if not defined else 0.0
            ),
            all_blocks_positive=False,
            all_blocks_negative=False,
            warnings=("descriptive_only",),
            identity=f"redundancy-summary-{first}-{second}",
        ))
    return SimpleNamespace(
        contract_name="quantlab.panel_factor_redundancy",
        contract_version="v1",
        source_dataset_identity=dataset.identity,
        source_bounded_content_identity="bounded-predictors-id",
        specification_fingerprint=spec.fingerprint,
        source_daily_result_identity="redundancy-daily-result-id",
        daily_correlations=tuple(daily),
        block_correlations=tuple(blocks),
        summaries=tuple(summaries),
        identity="redundancy-result-id",
    )


def _incremental(dataset: Any, dates: tuple[str, ...]):
    spec = NEUTRAL_PANEL_INCREMENTAL_FACTOR_ANALYSIS_V1
    daily = []
    blocks = []
    summaries = []
    for combination_index, hypothesis in enumerate(spec.hypotheses):
        for horizon in spec.horizons:
            for outcome in spec.outcome_fields:
                combination_daily = []
                for date_index, signal_date in enumerate(dates):
                    raw = -0.5 if combination_index == 0 and date_index == 1 else 0.6
                    partial = -0.25 if combination_index == 0 and date_index == 1 else 0.4
                    reason = None
                    if combination_index == 1 and date_index == 0:
                        partial = None
                        reason = "rank_deficient_control_design"
                    identity = (
                        f"incremental-daily-{hypothesis.name}-{horizon}-{outcome}-{signal_date}"
                    )
                    item = SimpleNamespace(
                        source_dataset_identity=dataset.identity,
                        source_dataset_content_identity=dataset.content_identity,
                        source_bounded_content_identity="incremental-bounded-content-id",
                        specification_fingerprint=spec.fingerprint,
                        signal_date=signal_date,
                        hypothesis_name=hypothesis.name,
                        target_factor=hypothesis.target_factor,
                        control_factors=hypothesis.control_factors,
                        horizon_sessions=horizon,
                        outcome_field=outcome,
                        total_observation_count=20,
                        outcome_available_count=20,
                        listwise_finite_count=20,
                        listwise_coverage_pct=100.0,
                        raw_rank_ic=raw,
                        partial_rank_ic=partial,
                        absolute_raw_rank_ic=abs(raw),
                        absolute_partial_rank_ic=(None if partial is None else abs(partial)),
                        target_residual_population_std=2.5,
                        outcome_residual_population_std=(None if partial is None else 3.5),
                        control_design_rank=(1 + len(hypothesis.control_factors)),
                        expected_design_rank=(1 + len(hypothesis.control_factors)),
                        undefined_reason=reason,
                        sample_evidence_sha256=("a" if partial is not None else "b") * 64,
                        identity=identity,
                    )
                    daily.append(item)
                    combination_daily.append(item)
                block_ids = []
                for block in spec.blocks:
                    included = tuple(
                        item.identity
                        for item in combination_daily
                        if block.start_date <= item.signal_date <= block.end_date
                    )
                    included_items = tuple(
                        item for item in combination_daily if item.identity in included
                    )
                    raw_values = tuple(
                        item.raw_rank_ic for item in included_items
                        if item.raw_rank_ic is not None
                    )
                    partial_values = tuple(
                        item.partial_rank_ic for item in included_items
                        if item.partial_rank_ic is not None
                    )
                    block_identity = (
                        f"incremental-block-{hypothesis.name}-{horizon}-{outcome}-{block.name}"
                    )
                    block_ids.append(block_identity)
                    blocks.append(SimpleNamespace(
                        source_dataset_identity=dataset.identity,
                        source_bounded_content_identity="incremental-bounded-content-id",
                        source_daily_result_identity="incremental-daily-result-id",
                        specification_fingerprint=spec.fingerprint,
                        hypothesis_name=hypothesis.name,
                        target_factor=hypothesis.target_factor,
                        control_factors=hypothesis.control_factors,
                        horizon_sessions=horizon,
                        outcome_field=outcome,
                        block_name=block.name,
                        block_start_date=block.start_date,
                        block_end_date=block.end_date,
                        total_signal_date_count=len(included_items),
                        minimum_sample_date_count=len(included_items),
                        raw_rank_ic_defined_date_count=len(raw_values),
                        partial_rank_ic_defined_date_count=len(partial_values),
                        raw_rank_ic_coverage_pct=(100.0 if included_items else 0.0),
                        partial_rank_ic_coverage_pct=(
                            0.0 if not included_items
                            else len(partial_values) / len(included_items) * 100.0
                        ),
                        mean_daily_raw_rank_ic=(
                            None if not raw_values else sum(raw_values) / len(raw_values)
                        ),
                        median_daily_raw_rank_ic=(None if not raw_values else raw_values[0]),
                        population_std_daily_raw_rank_ic=(0.0 if raw_values else None),
                        minimum_daily_raw_rank_ic=(None if not raw_values else min(raw_values)),
                        maximum_daily_raw_rank_ic=(None if not raw_values else max(raw_values)),
                        mean_daily_partial_rank_ic=(
                            None if not partial_values else sum(partial_values) / len(partial_values)
                        ),
                        median_daily_partial_rank_ic=(
                            None if not partial_values else partial_values[0]
                        ),
                        population_std_daily_partial_rank_ic=(
                            0.0 if partial_values else None
                        ),
                        minimum_daily_partial_rank_ic=(
                            None if not partial_values else min(partial_values)
                        ),
                        maximum_daily_partial_rank_ic=(
                            None if not partial_values else max(partial_values)
                        ),
                        mean_absolute_daily_raw_rank_ic=(
                            None if not raw_values
                            else sum(abs(value) for value in raw_values) / len(raw_values)
                        ),
                        median_absolute_daily_raw_rank_ic=(
                            None if not raw_values else abs(raw_values[0])
                        ),
                        mean_absolute_daily_partial_rank_ic=(
                            None if not partial_values
                            else sum(abs(value) for value in partial_values) / len(partial_values)
                        ),
                        median_absolute_daily_partial_rank_ic=(
                            None if not partial_values else abs(partial_values[0])
                        ),
                        positive_partial_rank_ic_date_count=sum(
                            value > 0 for value in partial_values
                        ),
                        zero_partial_rank_ic_date_count=sum(value == 0 for value in partial_values),
                        negative_partial_rank_ic_date_count=sum(
                            value < 0 for value in partial_values
                        ),
                        positive_partial_rank_ic_rate=(
                            None if not partial_values
                            else sum(value > 0 for value in partial_values) / len(partial_values)
                        ),
                        zero_partial_rank_ic_rate=(
                            None if not partial_values
                            else sum(value == 0 for value in partial_values) / len(partial_values)
                        ),
                        negative_partial_rank_ic_rate=(
                            None if not partial_values
                            else sum(value < 0 for value in partial_values) / len(partial_values)
                        ),
                        average_listwise_finite_count=(20.0 if included_items else None),
                        median_listwise_finite_count=(20.0 if included_items else None),
                        mean_partial_minus_raw_rank_ic=(
                            None if not partial_values else -0.2
                        ),
                        median_partial_minus_raw_rank_ic=(
                            None if not partial_values else -0.2
                        ),
                        included_daily_identities=included,
                        included_daily_identity_count=len(included),
                        included_daily_identities_sha256=runner._identity_collection_sha256(included),
                        warnings=("descriptive_conditional_association_only",),
                        identity=block_identity,
                    ))
                daily_ids = tuple(item.identity for item in combination_daily)
                partial_values = tuple(
                    item.partial_rank_ic for item in combination_daily
                    if item.partial_rank_ic is not None
                )
                raw_values = tuple(item.raw_rank_ic for item in combination_daily)
                block_tuple = tuple(block_ids)
                summaries.append(SimpleNamespace(
                    source_dataset_identity=dataset.identity,
                    source_bounded_content_identity="incremental-bounded-content-id",
                    source_daily_result_identity="incremental-daily-result-id",
                    specification_fingerprint=spec.fingerprint,
                    hypothesis_name=hypothesis.name,
                    target_factor=hypothesis.target_factor,
                    control_factors=hypothesis.control_factors,
                    horizon_sessions=horizon,
                    outcome_field=outcome,
                    total_signal_date_count=len(dates),
                    minimum_sample_date_count=len(dates),
                    raw_rank_ic_defined_date_count=len(raw_values),
                    partial_rank_ic_defined_date_count=len(partial_values),
                    raw_rank_ic_coverage_pct=100.0,
                    partial_rank_ic_coverage_pct=len(partial_values) / len(dates) * 100.0,
                    mean_daily_raw_rank_ic=sum(raw_values) / len(raw_values),
                    median_daily_raw_rank_ic=raw_values[0],
                    population_std_daily_raw_rank_ic=0.0,
                    minimum_daily_raw_rank_ic=min(raw_values),
                    maximum_daily_raw_rank_ic=max(raw_values),
                    mean_daily_partial_rank_ic=sum(partial_values) / len(partial_values),
                    median_daily_partial_rank_ic=partial_values[0],
                    population_std_daily_partial_rank_ic=0.0,
                    minimum_daily_partial_rank_ic=min(partial_values),
                    maximum_daily_partial_rank_ic=max(partial_values),
                    mean_absolute_daily_raw_rank_ic=sum(abs(v) for v in raw_values) / len(raw_values),
                    median_absolute_daily_raw_rank_ic=abs(raw_values[0]),
                    mean_absolute_daily_partial_rank_ic=(
                        sum(abs(v) for v in partial_values) / len(partial_values)
                    ),
                    median_absolute_daily_partial_rank_ic=abs(partial_values[0]),
                    positive_raw_rank_ic_date_count=sum(value > 0 for value in raw_values),
                    zero_raw_rank_ic_date_count=sum(value == 0 for value in raw_values),
                    negative_raw_rank_ic_date_count=sum(value < 0 for value in raw_values),
                    positive_raw_rank_ic_rate=sum(value > 0 for value in raw_values) / len(raw_values),
                    zero_raw_rank_ic_rate=sum(value == 0 for value in raw_values) / len(raw_values),
                    negative_raw_rank_ic_rate=sum(value < 0 for value in raw_values) / len(raw_values),
                    positive_partial_rank_ic_date_count=sum(value > 0 for value in partial_values),
                    zero_partial_rank_ic_date_count=sum(value == 0 for value in partial_values),
                    negative_partial_rank_ic_date_count=sum(value < 0 for value in partial_values),
                    positive_partial_rank_ic_rate=sum(value > 0 for value in partial_values) / len(partial_values),
                    zero_partial_rank_ic_rate=sum(value == 0 for value in partial_values) / len(partial_values),
                    negative_partial_rank_ic_rate=sum(value < 0 for value in partial_values) / len(partial_values),
                    average_listwise_finite_count=20.0,
                    median_listwise_finite_count=20.0,
                    mean_partial_minus_raw_rank_ic=-0.2,
                    median_partial_minus_raw_rank_ic=-0.2,
                    mean_absolute_partial_minus_absolute_raw_rank_ic=-0.2,
                    ordered_block_identities=block_tuple,
                    ordered_block_identity_count=len(block_tuple),
                    ordered_block_identities_sha256=runner._identity_collection_sha256(block_tuple),
                    blocks_meeting_temporal_review_count=0,
                    chronological_partial_sign_flip_count=0,
                    all_blocks_positive_partial_rank_ic=False,
                    all_blocks_negative_partial_rank_ic=False,
                    minimum_block_mean_partial_rank_ic=(
                        None if not partial_values else min(partial_values)
                    ),
                    maximum_block_mean_partial_rank_ic=(
                        None if not partial_values else max(partial_values)
                    ),
                    range_block_mean_partial_rank_ic=(
                        None if not partial_values else max(partial_values) - min(partial_values)
                    ),
                    largest_absolute_block_mean_partial_rank_ic_concentration=1.0,
                    included_daily_identities=daily_ids,
                    included_daily_identity_count=len(daily_ids),
                    included_daily_identities_sha256=runner._identity_collection_sha256(daily_ids),
                    warnings=("descriptive_conditional_association_only",),
                    identity=f"incremental-summary-{hypothesis.name}-{horizon}-{outcome}",
                ))
    return SimpleNamespace(
        contract_name="quantlab.panel_factor_incremental_analysis",
        contract_version="v1",
        source_dataset_identity=dataset.identity,
        source_dataset_content_identity=dataset.content_identity,
        source_bounded_content_identity="incremental-bounded-content-id",
        source_observation_index_identity=dataset.observation_index_identity,
        source_observation_content_identity=dataset.observation_content_identity,
        source_feature_panel_identity=dataset.feature_panel_identity,
        source_feature_content_identity=dataset.feature_content_identity,
        source_outcome_panel_identity=dataset.outcome_panel_identity,
        source_outcome_content_identity=dataset.outcome_content_identity,
        specification_fingerprint=spec.fingerprint,
        source_daily_result_identity="incremental-daily-result-id",
        daily_evaluations=tuple(daily),
        block_evaluations=tuple(blocks),
        summaries=tuple(summaries),
        identity="incremental-result-id",
    )


def _composite(dataset: Any, dates: tuple[str, ...]):
    spec = NEUTRAL_ADX_RSI_COMPOSITE_COMPARISON_V1
    rank_patterns = ((0.30, -0.30, 0.10, -0.10), (None, 0.20, -0.20, 0.40))
    spread_patterns = ((2.0, -2.0, 1.0, -1.0), (None, 1.0, -1.0, 2.0))
    daily = []
    for date_index, signal_date in enumerate(dates):
        for policy_index, policy in enumerate(spec.policies):
            for horizon in spec.horizons:
                for outcome in spec.outcome_fields:
                    rank_ic = rank_patterns[date_index][policy_index]
                    spread = spread_patterns[date_index][policy_index]
                    daily.append(SimpleNamespace(
                        source_dataset_identity=dataset.identity,
                        source_dataset_content_identity=dataset.content_identity,
                        source_bounded_content_identity="composite-bounded-id",
                        specification_fingerprint=spec.fingerprint,
                        signal_date=signal_date,
                        policy_name=policy.name,
                        factor_weights=policy.factor_weights,
                        policy_fingerprint=policy.fingerprint,
                        horizon_sessions=horizon,
                        outcome_field=outcome,
                        total_observation_count=20,
                        outcome_available_count=20,
                        outcome_unavailable_count=0,
                        outcome_missing_or_nonfinite_available_count=0,
                        factor_missing_or_nonfinite_counts=MappingProxyType({
                            "adx_14": 0, "rsi_14": 0, "volume_ratio_20": 0,
                        }),
                        shared_listwise_finite_count=20,
                        shared_excluded_count=0,
                        shared_coverage_pct=100.0,
                        rank_ic=rank_ic,
                        absolute_rank_ic=None if rank_ic is None else abs(rank_ic),
                        low_bucket_count=6 if spread is not None else 0,
                        high_bucket_count=6 if spread is not None else 0,
                        low_bucket_mean_outcome=0.0 if spread is not None else None,
                        high_bucket_mean_outcome=spread,
                        low_bucket_median_outcome=0.0 if spread is not None else None,
                        high_bucket_median_outcome=spread,
                        high_minus_low_mean_spread=spread,
                        high_minus_low_median_spread=spread,
                        ic_undefined_reason=(None if rank_ic is not None else "fewer_than_minimum_shared_observations"),
                        spread_undefined_reason=(None if spread is not None else "fewer_than_minimum_shared_observations"),
                        shared_sample_evidence_sha256=f"sample-{signal_date}-{horizon}-{outcome}",
                        identity=f"composite-daily-{signal_date}-{policy.name}-{horizon}-{outcome}",
                    ))
    daily_map = {
        (item.signal_date, item.policy_name, item.horizon_sessions, item.outcome_field): item
        for item in daily
    }

    def aggregate_fields(included: tuple[Any, ...]) -> dict[str, Any]:
        ic = tuple(item.rank_ic for item in included if item.rank_ic is not None)
        spreads = tuple(
            item.high_minus_low_mean_spread
            for item in included if item.high_minus_low_mean_spread is not None
        )
        return {
            "total_signal_date_count": len(included),
            "minimum_sample_date_count": len(included),
            "ic_defined_date_count": len(ic),
            "ic_coverage_pct": 0.0 if not included else len(ic) / len(included) * 100.0,
            "spread_defined_date_count": len(spreads),
            "spread_coverage_pct": 0.0 if not included else len(spreads) / len(included) * 100.0,
            "mean_daily_rank_ic": None if not ic else sum(ic) / len(ic),
            "median_daily_rank_ic": None if not ic else sum(ic) / len(ic),
            "population_std_daily_rank_ic": 0.0 if ic else None,
            "minimum_daily_rank_ic": None if not ic else min(ic),
            "maximum_daily_rank_ic": None if not ic else max(ic),
            "mean_absolute_daily_rank_ic": None if not ic else sum(abs(x) for x in ic) / len(ic),
            "median_absolute_daily_rank_ic": None if not ic else sum(abs(x) for x in ic) / len(ic),
            "positive_ic_date_count": sum(x > 0 for x in ic),
            "zero_ic_date_count": sum(x == 0 for x in ic),
            "negative_ic_date_count": sum(x < 0 for x in ic),
            "positive_ic_rate": None if not ic else sum(x > 0 for x in ic) / len(ic),
            "zero_ic_rate": None if not ic else sum(x == 0 for x in ic) / len(ic),
            "negative_ic_rate": None if not ic else sum(x < 0 for x in ic) / len(ic),
            "mean_daily_mean_spread": None if not spreads else sum(spreads) / len(spreads),
            "median_daily_mean_spread": None if not spreads else sum(spreads) / len(spreads),
            "population_std_daily_mean_spread": 0.0 if spreads else None,
            "minimum_daily_mean_spread": None if not spreads else min(spreads),
            "maximum_daily_mean_spread": None if not spreads else max(spreads),
            "mean_daily_median_spread": None if not spreads else sum(spreads) / len(spreads),
            "median_daily_median_spread": None if not spreads else sum(spreads) / len(spreads),
            "positive_spread_date_count": sum(x > 0 for x in spreads),
            "zero_spread_date_count": sum(x == 0 for x in spreads),
            "negative_spread_date_count": sum(x < 0 for x in spreads),
            "positive_spread_rate": None if not spreads else sum(x > 0 for x in spreads) / len(spreads),
            "zero_spread_rate": None if not spreads else sum(x == 0 for x in spreads) / len(spreads),
            "negative_spread_rate": None if not spreads else sum(x < 0 for x in spreads) / len(spreads),
            "average_low_bucket_size": 6.0 if spreads else None,
            "average_high_bucket_size": 6.0 if spreads else None,
            "average_shared_cross_section_size": 20.0 if included else None,
            "median_shared_cross_section_size": 20.0 if included else None,
        }

    blocks = []
    summaries = []
    for policy in spec.policies:
        for horizon in spec.horizons:
            for outcome in spec.outcome_fields:
                items = tuple(
                    daily_map[(signal_date, policy.name, horizon, outcome)]
                    for signal_date in dates
                )
                policy_blocks = []
                for block in spec.blocks:
                    included = tuple(item for item in items if block.contains(item.signal_date))
                    ids = tuple(item.identity for item in included)
                    item = SimpleNamespace(
                        policy_name=policy.name, factor_weights=policy.factor_weights,
                        policy_fingerprint=policy.fingerprint, horizon_sessions=horizon,
                        outcome_field=outcome, block_name=block.name,
                        block_start_date=block.start_date, block_end_date=block.end_date,
                        **aggregate_fields(included),
                        included_daily_identity_count=len(ids),
                        included_daily_identities_sha256=runner._identity_collection_sha256(ids),
                        warnings=("synthetic_composite_block",),
                        identity=f"composite-block-{policy.name}-{horizon}-{outcome}-{block.name}",
                    )
                    blocks.append(item)
                    policy_blocks.append(item)
                ids = tuple(item.identity for item in items)
                block_ids = tuple(item.identity for item in policy_blocks)
                summaries.append(SimpleNamespace(
                    policy_name=policy.name, factor_weights=policy.factor_weights,
                    policy_fingerprint=policy.fingerprint, horizon_sessions=horizon,
                    outcome_field=outcome, **aggregate_fields(items),
                    included_daily_identity_count=len(ids),
                    included_daily_identities_sha256=runner._identity_collection_sha256(ids),
                    ordered_block_identities=block_ids,
                    blocks_meeting_ic_review_count=0, blocks_meeting_spread_review_count=0,
                    chronological_block_mean_ic_sign_flip_count=0,
                    chronological_block_mean_spread_sign_flip_count=0,
                    all_blocks_positive_ic=False, all_blocks_negative_ic=False,
                    all_blocks_positive_spread=False, all_blocks_negative_spread=False,
                    minimum_block_mean_ic=-0.3, maximum_block_mean_ic=0.4,
                    range_block_mean_ic=0.7, minimum_block_mean_spread=-2.0,
                    maximum_block_mean_spread=2.0, range_block_mean_spread=4.0,
                    largest_absolute_block_mean_ic_concentration=1.0,
                    largest_absolute_block_mean_spread_concentration=1.0,
                    warnings=("synthetic_composite_summary",),
                    identity=f"composite-summary-{policy.name}-{horizon}-{outcome}",
                ))

    def contrast_metrics(pairs: tuple[tuple[Any, Any], ...]) -> dict[str, Any]:
        ic = tuple(
            left.rank_ic - right.rank_ic for left, right in pairs
            if left.rank_ic is not None and right.rank_ic is not None
        )
        spreads = tuple(
            left.high_minus_low_mean_spread - right.high_minus_low_mean_spread
            for left, right in pairs
            if left.high_minus_low_mean_spread is not None
            and right.high_minus_low_mean_spread is not None
        )
        def fields(prefix: str, values: tuple[float, ...]) -> dict[str, Any]:
            return {
                f"paired_{prefix}_date_count": len(values),
                f"mean_daily_{prefix}_delta": None if not values else sum(values) / len(values),
                f"median_daily_{prefix}_delta": None if not values else sum(values) / len(values),
                f"positive_{prefix}_delta_count": sum(value > 0 for value in values),
                f"zero_{prefix}_delta_count": sum(value == 0 for value in values),
                f"negative_{prefix}_delta_count": sum(value < 0 for value in values),
                f"positive_{prefix}_delta_rate": None if not values else sum(value > 0 for value in values) / len(values),
                f"zero_{prefix}_delta_rate": None if not values else sum(value == 0 for value in values) / len(values),
                f"negative_{prefix}_delta_rate": None if not values else sum(value < 0 for value in values) / len(values),
            }
        return {**fields("ic", ic), **fields("spread", spreads)}

    contrast_blocks = []
    contrast_summaries = []
    for contrast in spec.contrasts:
        for horizon in spec.horizons:
            for outcome in spec.outcome_fields:
                pairs = tuple(
                    (
                        daily_map[(signal_date, contrast.variant_policy, horizon, outcome)],
                        daily_map[(signal_date, contrast.reference_policy, horizon, outcome)],
                    )
                    for signal_date in dates
                )
                block_ids = []
                for block in spec.blocks:
                    included = tuple(pair for pair in pairs if block.contains(pair[0].signal_date))
                    pair_ids = tuple(
                        runner._daily_pair_identity(left.identity, right.identity)
                        for left, right in included
                    )
                    identity = f"composite-contrast-block-{contrast.name}-{horizon}-{outcome}-{block.name}"
                    block_ids.append(identity)
                    contrast_blocks.append(SimpleNamespace(
                        contrast_name=contrast.name, variant_policy=contrast.variant_policy,
                        reference_policy=contrast.reference_policy,
                        horizon_sessions=horizon, outcome_field=outcome,
                        block_name=block.name, block_start_date=block.start_date,
                        block_end_date=block.end_date, **contrast_metrics(included),
                        included_daily_pair_identity_count=len(pair_ids),
                        included_daily_pair_identities_sha256=runner._identity_collection_sha256(pair_ids),
                        identity=identity,
                    ))
                contrast_summaries.append(SimpleNamespace(
                    contrast_name=contrast.name, variant_policy=contrast.variant_policy,
                    reference_policy=contrast.reference_policy,
                    horizon_sessions=horizon, outcome_field=outcome,
                    **contrast_metrics(pairs), ordered_block_identities=tuple(block_ids),
                    identity=f"composite-contrast-{contrast.name}-{horizon}-{outcome}",
                ))
    return SimpleNamespace(
        source_dataset_identity=dataset.identity,
        source_dataset_content_identity=dataset.content_identity,
        source_bounded_content_identity="composite-bounded-id",
        source_observation_index_identity=dataset.observation_index_identity,
        source_observation_content_identity=dataset.observation_content_identity,
        source_feature_panel_identity=dataset.feature_panel_identity,
        source_feature_content_identity=dataset.feature_content_identity,
        source_outcome_panel_identity=dataset.outcome_panel_identity,
        source_outcome_content_identity=dataset.outcome_content_identity,
        specification_fingerprint=spec.fingerprint,
        daily_evaluations=tuple(daily), block_evaluations=tuple(blocks),
        summaries=tuple(summaries), contrast_block_evaluations=tuple(contrast_blocks),
        contrast_summaries=tuple(contrast_summaries), identity="composite-result-id",
    )


def _install_pipeline(monkeypatch: pytest.MonkeyPatch, database: Path):
    dates = ("2021-01-04", "2021-01-05")
    keys = tuple((signal_date, symbol) for signal_date in dates for symbol in ("AAA", "BBB"))
    base = pd.DataFrame(keys, columns=("session_date", "symbol"))
    audits = tuple(
        SimpleNamespace(
            session_date=signal_date,
            membership_count=2,
            emitted_observation_row_count=2,
            available_row_count=2,
            missing_row_count=0,
        )
        for signal_date in dates
    )
    snapshot = SimpleNamespace(
        first_session_date="2020-01-02",
        last_session_date="2021-02-05",
        snapshot_id="snapshot-id",
        logical_content_fingerprint="snapshot-content",
    )
    coverage = SimpleNamespace(
        start_date="2020-01-02",
        end_date=dates[-1],
        effective_start_date="2020-01-02",
        effective_end_date=dates[-1],
    )
    universe = SimpleNamespace(membership_identity="universe-id")
    observation = SimpleNamespace(
        frame=base.copy(deep=True),
        session_audit=audits,
        total_membership_row_count=4,
        observation_row_count=4,
        identity="observation-id",
        content_identity="observation-content",
        effective_first_session_date=dates[0],
        effective_last_session_date=dates[-1],
        distinct_member_symbol_count=2,
        symbols=("AAA", "BBB"),
    )
    source = SimpleNamespace(
        computation_identity=SimpleNamespace(sha256="feature-computation-id"),
        metadata=MappingProxyType({}),
    )
    feature_frame = base.copy(deep=True)
    feature_frame["complete_feature_row"] = True
    feature_panel = SimpleNamespace(
        frame=feature_frame,
        observation_row_count=4,
        observation_index_identity="observation-id",
        identity="feature-panel-id",
        feature_content_identity="feature-content",
    )
    outcome_frame = base.copy(deep=True)
    outcome_frame["available_horizon_count"] = 3
    outcome_frame["fully_labeled_outcome_row"] = True
    outcome_panel = SimpleNamespace(
        frame=outcome_frame,
        observation_row_count=4,
        observation_index_identity="observation-id",
        identity="outcome-panel-id",
        outcome_content_identity="outcome-content",
    )
    dataset_frame_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def evaluation_frame():
        dataset_frame_calls.append(((), {}))
        return base.copy(deep=True)

    dataset = SimpleNamespace(
        evaluation_frame=evaluation_frame,
        session_audit=audits,
        observation_row_count=4,
        observation_index_identity="observation-id",
        observation_content_identity="observation-content",
        feature_panel_identity="feature-panel-id",
        feature_content_identity="feature-content",
        outcome_panel_identity="outcome-panel-id",
        outcome_content_identity="outcome-content",
        identity="dataset-id",
        content_identity="dataset-content",
        forbidden_predictor_columns=(),
    )
    evaluation = _evaluation(dates)
    temporal = _temporal(evaluation)
    redundancy = _redundancy(dataset, dates)
    incremental = _incremental(dataset, dates)
    composite = _composite(dataset, dates)
    calls: dict[str, list[tuple[tuple[Any, ...], dict[str, Any]]]] = {
        name: [] for name in (
            "snapshot", "coverage", "universe", "observation", "source",
            "features", "outcomes", "dataset", "redundancy", "evaluation", "temporal",
            "incremental",
            "composite",
        )
    }
    calls["dataset_frame"] = dataset_frame_calls

    def install(name: str, value: Any):
        def call(*args: Any, **kwargs: Any):
            calls[name].append((args, kwargs))
            return value
        return call

    monkeypatch.setattr(runner, "build_market_data_snapshot", install("snapshot", snapshot))
    monkeypatch.setattr(runner, "build_database_coverage_index", install("coverage", coverage))
    monkeypatch.setattr(
        runner,
        "PointInTimeUniverseContext",
        SimpleNamespace(from_coverage_index=install("universe", universe)),
    )
    monkeypatch.setattr(
        runner, "build_point_in_time_observation_index", install("observation", observation),
    )
    monkeypatch.setattr(
        runner, "prepare_neutral_research_feature_source", install("source", source),
    )
    monkeypatch.setattr(
        runner, "attach_features_to_observation_index", install("features", feature_panel),
    )
    monkeypatch.setattr(
        runner, "build_point_in_time_outcome_panel", install("outcomes", outcome_panel),
    )
    monkeypatch.setattr(
        runner, "build_point_in_time_research_dataset", install("dataset", dataset),
    )
    monkeypatch.setattr(
        runner,
        "evaluate_panel_factor_redundancy",
        install("redundancy", redundancy),
    )
    monkeypatch.setattr(
        runner, "evaluate_point_in_time_panel_factors", install("evaluation", evaluation),
    )
    monkeypatch.setattr(
        runner,
        "evaluate_panel_factor_temporal_stability",
        install("temporal", temporal),
    )
    monkeypatch.setattr(
        runner,
        "evaluate_panel_factor_incremental_analysis",
        install("incremental", incremental),
    )
    monkeypatch.setattr(
        runner,
        "evaluate_panel_composites",
        install("composite", composite),
    )
    return calls, SimpleNamespace(
        snapshot=snapshot,
        coverage=coverage,
        universe=universe,
        observation=observation,
        source=source,
        feature_panel=feature_panel,
        outcome_panel=outcome_panel,
        dataset=dataset,
        evaluation=evaluation,
        temporal=temporal,
        redundancy=redundancy,
        incremental=incremental,
        composite=composite,
        dates=dates,
    )


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **changes: Any):
    database = tmp_path / "market.db"
    if not database.exists():
        database.write_bytes(b"immutable-market-fixture")
    calls, objects = _install_pipeline(monkeypatch, database)
    arguments = {
        "database_path": database,
        "start_date": "2021-01-04",
        "end_date": "2021-01-05",
        "output_root": tmp_path / "result",
    }
    arguments.update(changes)
    result = runner.run_neutral_panel_factor_evaluation(**arguments)
    return database, calls, objects, result


def _read_csv(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        return tuple(reader.fieldnames or ()), list(reader)


def test_exact_one_time_orchestration_and_identity_propagation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, calls, objects, result = _run(monkeypatch, tmp_path)
    assert all(len(items) == 1 for items in calls.values())
    assert calls["coverage"][0][0][:2] == ("2020-01-02", "2021-01-05")
    assert calls["observation"][0][1]["start_date"] == "2021-01-04"
    assert calls["observation"][0][1]["through_date"] == "2021-01-05"
    assert calls["outcomes"][0][0] == (objects.observation, objects.snapshot)
    assert calls["redundancy"] == [((
        objects.dataset,
        NEUTRAL_PANEL_FACTOR_REDUNDANCY_V1,
    ), {})]
    assert calls["evaluation"][0][0][0] is objects.dataset
    assert calls["temporal"] == [((
        objects.evaluation,
        NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V2,
    ), {})]
    assert calls["incremental"] == [((
        objects.dataset,
        NEUTRAL_PANEL_INCREMENTAL_FACTOR_ANALYSIS_V1,
    ), {})]
    assert calls["composite"] == [((
        objects.dataset,
        NEUTRAL_ADX_RSI_COMPOSITE_COMPARISON_V1,
    ), {})]
    manifest = result["manifest"]
    assert manifest["observation_index"]["identity"] == "observation-id"
    assert manifest["features"]["computation_identity"] == "feature-computation-id"
    assert manifest["research_dataset"]["identity"] == "dataset-id"
    assert manifest["evaluation"]["result_identity"] == "evaluation-id"
    assert manifest["temporal_stability"]["source_evaluation_result_identity"] == "evaluation-id"
    assert result["temporal_stability"] is objects.temporal
    assert manifest["factor_redundancy"]["source_dataset_identity"] == "dataset-id"
    assert result["factor_redundancy"] is objects.redundancy
    assert manifest["factor_incremental_analysis"]["source_dataset_identity"] == "dataset-id"
    assert result["factor_incremental_analysis"] is objects.incremental
    assert manifest["composite_comparison"]["source_dataset_identity"] == "dataset-id"
    assert result["composite_comparison"] is objects.composite


def test_exact_artifacts_schemas_dimensions_order_and_complete_population(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, objects, result = _run(monkeypatch, tmp_path)
    output = result["output_root"]
    assert len(runner._REQUIRED_FILENAMES) == 20
    assert tuple(sorted(path.name for path in output.iterdir())) == tuple(
        sorted(runner._REQUIRED_FILENAMES)
    )
    summary_columns, summaries = _read_csv(output / "factor_summary.csv")
    daily_columns, daily = _read_csv(output / "factor_by_date.csv")
    redundancy_summary_columns, redundancy_summaries = _read_csv(
        output / "factor_redundancy_summary.csv",
    )
    redundancy_block_columns, redundancy_blocks = _read_csv(
        output / "factor_redundancy_by_block.csv",
    )
    redundancy_daily_columns, redundancy_daily = _read_csv(
        output / "factor_redundancy_by_date.csv",
    )
    incremental_summary_columns, incremental_summaries = _read_csv(
        output / "factor_incremental_summary.csv",
    )
    incremental_block_columns, incremental_blocks = _read_csv(
        output / "factor_incremental_by_block.csv",
    )
    incremental_daily_columns, incremental_daily = _read_csv(
        output / "factor_incremental_by_date.csv",
    )
    coverage_columns, coverage = _read_csv(output / "factor_coverage.csv")
    observation_columns, observations = _read_csv(output / "observation_counts_by_date.csv")
    temporal_summary_columns, temporal_summaries = _read_csv(
        output / "temporal_stability_summary.csv",
    )
    temporal_block_columns, temporal_blocks = _read_csv(
        output / "temporal_stability_by_block.csv",
    )
    temporal_coverage_columns, temporal_coverage = _read_csv(
        output / "temporal_coverage.csv",
    )
    composite_summary_columns, composite_summaries = _read_csv(
        output / "composite_policy_summary.csv",
    )
    composite_block_columns, composite_blocks = _read_csv(
        output / "composite_policy_by_block.csv",
    )
    composite_daily_columns, composite_daily = _read_csv(
        output / "composite_policy_by_date.csv",
    )
    contrast_summary_columns, contrast_summaries = _read_csv(
        output / "composite_contrast_summary.csv",
    )
    contrast_block_columns, contrast_blocks = _read_csv(
        output / "composite_contrast_by_block.csv",
    )
    assert summary_columns == runner._SUMMARY_COLUMNS
    assert daily_columns == runner._DAILY_COLUMNS
    assert coverage_columns == runner._COVERAGE_COLUMNS
    assert observation_columns == runner._OBSERVATION_COLUMNS
    assert temporal_summary_columns == runner._TEMPORAL_SUMMARY_COLUMNS
    assert temporal_block_columns == runner._TEMPORAL_BLOCK_COLUMNS
    assert temporal_coverage_columns == runner._TEMPORAL_COVERAGE_COLUMNS
    assert redundancy_summary_columns == runner._REDUNDANCY_SUMMARY_COLUMNS
    assert redundancy_block_columns == runner._REDUNDANCY_BLOCK_COLUMNS
    assert redundancy_daily_columns == runner._REDUNDANCY_DAILY_COLUMNS
    assert incremental_summary_columns == runner._INCREMENTAL_SUMMARY_COLUMNS
    assert incremental_block_columns == runner._INCREMENTAL_BLOCK_COLUMNS
    assert incremental_daily_columns == runner._INCREMENTAL_DAILY_COLUMNS
    assert composite_summary_columns == runner._COMPOSITE_SUMMARY_COLUMNS
    assert composite_block_columns == runner._COMPOSITE_BLOCK_COLUMNS
    assert composite_daily_columns == runner._COMPOSITE_DAILY_COLUMNS
    assert contrast_summary_columns == runner._COMPOSITE_CONTRAST_SUMMARY_COLUMNS
    assert contrast_block_columns == runner._COMPOSITE_CONTRAST_BLOCK_COLUMNS
    assert (len(summaries), len(daily), len(coverage), len(observations)) == (48, 96, 48, 2)
    assert (len(temporal_summaries), len(temporal_blocks), len(temporal_coverage)) == (
        48, 192, 48,
    )
    assert (len(redundancy_summaries), len(redundancy_blocks), len(redundancy_daily)) == (
        28, 112, len(objects.dates) * 28,
    )
    assert (
        len(incremental_summaries), len(incremental_blocks), len(incremental_daily),
    ) == (48, 192, len(objects.dates) * 48)
    assert (
        len(composite_summaries), len(composite_blocks), len(composite_daily),
        len(contrast_summaries), len(contrast_blocks),
    ) == (24, 96, len(objects.dates) * 24, 18, 72)
    assert [
        (
            row["signal_date"], row["policy_name"], int(row["horizon_sessions"]),
            row["outcome_field"],
        )
        for row in composite_daily
    ] == [
        (
            signal_date, policy.name, horizon, outcome,
        )
        for signal_date in objects.dates
        for policy in NEUTRAL_ADX_RSI_COMPOSITE_COMPARISON_V1.policies
        for horizon in NEUTRAL_ADX_RSI_COMPOSITE_COMPARISON_V1.horizons
        for outcome in NEUTRAL_ADX_RSI_COMPOSITE_COMPARISON_V1.outcome_fields
    ]
    assert len({
        (row["signal_date"], row["policy_name"], row["horizon_sessions"], row["outcome_field"])
        for row in composite_daily
    }) == len(composite_daily)
    expected_incremental_daily_keys = [
        (signal_date, hypothesis.name, str(horizon), outcome)
        for signal_date in objects.dates
        for hypothesis in NEUTRAL_PANEL_INCREMENTAL_FACTOR_ANALYSIS_V1.hypotheses
        for horizon in NEUTRAL_PANEL_INCREMENTAL_FACTOR_ANALYSIS_V1.horizons
        for outcome in NEUTRAL_PANEL_INCREMENTAL_FACTOR_ANALYSIS_V1.outcome_fields
    ]
    assert [
        (
            row["signal_date"], row["hypothesis_name"], row["horizon_sessions"],
            row["outcome_field"],
        )
        for row in incremental_daily
    ] == expected_incremental_daily_keys
    assert len({tuple(row.values()) for row in incremental_daily}) == len(incremental_daily)
    assert [row["signal_date"] for row in observations] == list(objects.dates)
    assert all(row["observation_row_count"] == "2" for row in observations)
    assert [
        (row["factor"], int(row["horizon_sessions"]), row["outcome_field"], row["signal_date"])
        for row in daily
    ] == [
        (item.factor, item.horizon_sessions, item.outcome_field, item.signal_date)
        for item in objects.evaluation.daily_evaluations
    ]
    assert [
        (row["signal_date"], row["first_factor"], row["second_factor"])
        for row in redundancy_daily
    ] == [
        (signal_date, first, second)
        for signal_date in objects.dates
        for first, second in NEUTRAL_PANEL_FACTOR_REDUNDANCY_V1.pairs
    ]
    assert [
        (row["first_factor"], row["second_factor"], row["block_name"])
        for row in redundancy_blocks
    ] == [
        (first, second, block.name)
        for first, second in NEUTRAL_PANEL_FACTOR_REDUNDANCY_V1.pairs
        for block in NEUTRAL_PANEL_FACTOR_REDUNDANCY_V1.blocks
    ]


def test_compact_identity_projection_nulls_json_and_no_evidence_payloads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, objects, result = _run(monkeypatch, tmp_path)
    output = result["output_root"]
    summary_columns, summaries = _read_csv(output / "factor_summary.csv")
    daily_columns, daily = _read_csv(output / "factor_by_date.csv")
    redundancy_summary_columns, redundancy_summaries = _read_csv(
        output / "factor_redundancy_summary.csv",
    )
    redundancy_block_columns, redundancy_blocks = _read_csv(
        output / "factor_redundancy_by_block.csv",
    )
    _, redundancy_daily = _read_csv(output / "factor_redundancy_by_date.csv")
    incremental_summary_columns, incremental_summaries = _read_csv(
        output / "factor_incremental_summary.csv",
    )
    incremental_block_columns, incremental_blocks = _read_csv(
        output / "factor_incremental_by_block.csv",
    )
    incremental_daily_columns, incremental_daily = _read_csv(
        output / "factor_incremental_by_date.csv",
    )
    composite_summary_columns, composite_summaries = _read_csv(
        output / "composite_policy_summary.csv",
    )
    composite_block_columns, composite_blocks = _read_csv(
        output / "composite_policy_by_block.csv",
    )
    composite_daily_columns, composite_daily = _read_csv(
        output / "composite_policy_by_date.csv",
    )
    contrast_summary_columns, contrast_summaries = _read_csv(
        output / "composite_contrast_summary.csv",
    )
    contrast_block_columns, _ = _read_csv(
        output / "composite_contrast_by_block.csv",
    )
    assert "included_daily_identities" not in summary_columns
    assert "eligible_evidence" not in daily_columns
    assert "observation_status_evidence" not in daily_columns
    expected = runner._identity_collection_sha256(
        objects.evaluation.summaries[0].included_daily_identities,
    )
    assert summaries[0]["included_daily_identity_count"] == "2"
    assert summaries[0]["included_daily_identities_sha256"] == expected
    assert daily[0]["rank_ic"] == ""
    assert daily[0]["high_minus_low_mean_spread"] == ""
    assert "included_daily_identities" not in redundancy_summary_columns
    assert "ordered_block_identities" not in redundancy_summary_columns
    assert "included_daily_identities" not in redundancy_block_columns
    assert not {
        "redundant", "independent", "selected", "rejected", "drop", "keep",
    }.intersection(redundancy_summary_columns)
    first_pair = NEUTRAL_PANEL_FACTOR_REDUNDANCY_V1.pairs[0]
    first_pair_rows = [
        row for row in redundancy_daily
        if (row["first_factor"], row["second_factor"]) == first_pair
    ]
    assert [row["spearman_correlation"] for row in first_pair_rows] == ["0.75", "-0.5"]
    assert [row["absolute_spearman_correlation"] for row in first_pair_rows] == [
        "0.75", "0.5",
    ]
    undefined = redundancy_daily[2]
    assert undefined["spearman_correlation"] == ""
    assert undefined["absolute_spearman_correlation"] == ""
    assert undefined["undefined_reason"] == (
        "fewer_than_minimum_pairwise_finite_observations"
    )
    assert redundancy_blocks[0]["included_daily_identity_count"] == "0"
    assert redundancy_blocks[0]["included_daily_identities_sha256"] == (
        runner._identity_collection_sha256(())
    )
    assert redundancy_summaries[0]["included_daily_identity_count"] == "2"
    assert redundancy_summaries[0]["block_identity_count"] == "4"
    assert redundancy_summaries[0]["included_daily_identities_sha256"] == (
        objects.redundancy.summaries[0].included_daily_identities_sha256
    )
    assert redundancy_summaries[0]["block_identities_sha256"] == (
        objects.redundancy.summaries[0].ordered_block_identities_sha256
    )
    assert "included_daily_identities" not in incremental_summary_columns
    assert "ordered_block_identities" not in incremental_summary_columns
    assert "included_daily_identities" not in incremental_block_columns
    assert "listwise_sample_evidence" not in incremental_daily_columns
    assert incremental_daily[0]["raw_rank_ic"] == "0.6"
    assert incremental_daily[0]["partial_rank_ic"] == "0.4"
    assert incremental_daily[48]["raw_rank_ic"] == "-0.5"
    assert incremental_daily[48]["partial_rank_ic"] == "-0.25"
    undefined = next(row for row in incremental_daily if row["partial_rank_ic"] == "")
    assert undefined["undefined_reason"] == "rank_deficient_control_design"
    assert undefined["absolute_partial_rank_ic"] == ""
    assert undefined["control_factors"] == '["adx_14"]'
    assert incremental_blocks[0]["included_daily_identity_count"] == "0"
    assert incremental_blocks[0]["included_daily_identities_sha256"] == (
        runner._identity_collection_sha256(())
    )
    assert incremental_summaries[0]["included_daily_identity_count"] == "2"
    assert incremental_summaries[0]["block_identity_count"] == "4"
    assert incremental_summaries[0]["included_daily_identities_sha256"] == (
        objects.incremental.summaries[0].included_daily_identities_sha256
    )
    assert incremental_summaries[0]["block_identities_sha256"] == (
        objects.incremental.summaries[0].ordered_block_identities_sha256
    )
    assert "ordered_block_identities" not in composite_summary_columns
    assert "included_daily_identities" not in composite_block_columns
    assert "ordered_block_identities" not in contrast_summary_columns
    forbidden_composite = {
        "winner", "recommended", "pass", "fail", "selected",
        "production_candidate", "optimized_weight", "production_policy",
    }
    assert not forbidden_composite.intersection(
        set(composite_summary_columns) | set(composite_block_columns)
        | set(composite_daily_columns) | set(contrast_summary_columns)
        | set(contrast_block_columns)
    )
    assert json.loads(composite_daily[0]["factor_weights"]) == [
        {"direction": "HIGHER_IS_BETTER", "factor": "adx_14", "weight": 1.0},
    ]
    assert composite_daily[0]["rank_ic"] == "0.3"
    assert composite_daily[24]["rank_ic"] == ""
    assert any(float(row["rank_ic"]) < 0 for row in composite_daily if row["rank_ic"])
    assert any(
        float(row["high_minus_low_mean_spread"]) < 0
        for row in composite_daily if row["high_minus_low_mean_spread"]
    )
    assert composite_blocks[0]["included_daily_identity_count"] == "0"
    assert composite_summaries[0]["included_daily_identity_count"] == "2"
    assert composite_summaries[0]["block_identity_count"] == "4"
    assert contrast_summaries[0]["block_identity_count"] == "4"
    assert any(
        float(row["mean_daily_ic_delta"]) > 0
        for row in contrast_summaries if row["mean_daily_ic_delta"]
    )
    assert any(
        float(row["mean_daily_ic_delta"]) < 0
        for row in contrast_summaries if row["mean_daily_ic_delta"]
    )
    forbidden_incremental = {
        "keep", "drop", "selected", "rejected", "improved", "degraded",
        "incremental_pass", "recommended_weight", "composite_role",
    }
    assert not forbidden_incremental.intersection(
        set(incremental_summary_columns)
        | set(incremental_block_columns)
        | set(incremental_daily_columns)
    )
    manifest_text = (output / "experiment_manifest.json").read_text(encoding="utf-8")
    assert all(token not in manifest_text for token in ("NaN", "Infinity", "<NA>"))
    assert json.loads(manifest_text)["completed"] is True
    redundancy_manifest = result["manifest"]["factor_redundancy"]
    assert redundancy_manifest["result_identity"] == "redundancy-result-id"
    assert redundancy_manifest["specification_fingerprint"] == (
        NEUTRAL_PANEL_FACTOR_REDUNDANCY_V1.fingerprint
    )
    assert redundancy_manifest["daily_record_count"] == len(objects.dates) * 28
    assert redundancy_manifest["factor_count"] == 8
    assert redundancy_manifest["canonical_pair_count"] == 28
    assert redundancy_manifest["temporal_block_count"] == 4
    assert redundancy_manifest["block_result_count"] == 112
    assert redundancy_manifest["dates_represented"] == list(objects.dates)
    assert redundancy_manifest["defined_daily_correlation_count"] == 2
    assert redundancy_manifest["undefined_daily_correlation_count"] == (
        len(objects.dates) * 28 - 2
    )
    assert set(result["manifest"]["artifacts"]) == set(runner._REQUIRED_FILENAMES)
    assert redundancy_manifest["limitations"] == list(runner._REDUNDANCY_LIMITATIONS)
    incremental_manifest = result["manifest"]["factor_incremental_analysis"]
    assert incremental_manifest["result_identity"] == "incremental-result-id"
    assert incremental_manifest["specification_fingerprint"] == (
        NEUTRAL_PANEL_INCREMENTAL_FACTOR_ANALYSIS_V1.fingerprint
    )
    assert incremental_manifest["hypothesis_count"] == 8
    assert incremental_manifest["horizon_count"] == 3
    assert incremental_manifest["outcome_count"] == 2
    assert incremental_manifest["summary_count"] == 48
    assert incremental_manifest["temporal_block_count"] == 4
    assert incremental_manifest["block_result_count"] == 192
    assert incremental_manifest["daily_record_count"] == len(objects.dates) * 48
    assert incremental_manifest["limitations"] == list(runner._INCREMENTAL_LIMITATIONS)
    composite_manifest = result["manifest"]["composite_comparison"]
    assert composite_manifest["result_identity"] == "composite-result-id"
    assert composite_manifest["specification_fingerprint"] == (
        NEUTRAL_ADX_RSI_COMPOSITE_COMPARISON_V1.fingerprint
    )
    assert composite_manifest["policy_summary_count"] == 24
    assert composite_manifest["policy_block_count"] == 96
    assert composite_manifest["daily_policy_record_count"] == len(objects.dates) * 24
    assert composite_manifest["contrast_summary_count"] == 18
    assert composite_manifest["contrast_block_count"] == 72
    assert (
        composite_manifest["ic_defined_policy_record_count"]
        + composite_manifest["ic_undefined_policy_record_count"]
    ) == len(objects.dates) * 24
    assert (
        composite_manifest["spread_defined_policy_record_count"]
        + composite_manifest["spread_undefined_policy_record_count"]
    ) == len(objects.dates) * 24
    assert composite_manifest["limitations"] == list(runner._COMPOSITE_LIMITATIONS)
    assumptions = (output / "assumptions.md").read_text(encoding="utf-8")
    assert "predictor projection" in assumptions
    assert "No redundancy threshold or factor-selection decision" in assumptions
    assert "Eight fixed hypotheses" in assumptions
    assert "No automatic factor-selection threshold" in assumptions
    assert "Four fixed higher-is-better policies" in assumptions
    assert "No optimization, winner selection" in assumptions


def test_temporal_projection_hashes_review_statuses_and_manifest_reconcile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, objects, result = _run(monkeypatch, tmp_path)
    output = result["output_root"]
    summary_columns, summaries = _read_csv(output / "temporal_stability_summary.csv")
    block_columns, blocks = _read_csv(output / "temporal_stability_by_block.csv")
    _, coverage = _read_csv(output / "temporal_coverage.csv")
    assert "included_block_identities" not in summary_columns
    assert "included_daily_identities" not in block_columns
    assert summaries[0]["block_identity_count"] == "4"
    assert summaries[0]["block_identities_sha256"] == (
        runner._identity_collection_sha256(
            objects.temporal.summaries[0].included_block_identities,
        )
    )
    assert blocks[0]["included_daily_identity_count"] == "0"
    assert blocks[0]["included_daily_identities_sha256"] == (
        runner._identity_collection_sha256(())
    )
    assert blocks[0]["mean_daily_rank_ic"] == ""
    assert "mean_daily_high_minus_low_mean_spread" not in block_columns
    assert blocks[1]["mean_daily_mean_spread"] == "1.25"
    assert summaries[0]["ic_consistent_direction"] == "POSITIVE"
    assert summaries[1]["ic_consistent_direction"] == "NEGATIVE"
    assert summaries[1]["all_blocks_negative_ic"] == "true"
    assert [row["review_status"] for row in coverage[:5]] == [
        "TEMPORAL_SUPPORT_POSITIVE",
        "TEMPORAL_SUPPORT_NEGATIVE",
        "DIRECTION_MISMATCH",
        "COVERAGE_ONLY",
        "INSUFFICIENT_COVERAGE",
    ]
    assert [
        (row["factor"], int(row["horizon_sessions"]), row["outcome_field"])
        for row in coverage
    ] == [
        (item.factor, item.horizon_sessions, item.outcome_field)
        for item in objects.temporal.summaries
    ]
    temporal_manifest = result["manifest"]["temporal_stability"]
    assert temporal_manifest["review_status_counts"] == {
        "TEMPORAL_SUPPORT_POSITIVE": 1,
        "TEMPORAL_SUPPORT_NEGATIVE": 1,
        "DIRECTION_MISMATCH": 1,
        "COVERAGE_ONLY": 1,
        "INSUFFICIENT_COVERAGE": 44,
    }
    assert temporal_manifest["block_result_count"] == 192
    assert temporal_manifest["summary_count"] == 48
    assert temporal_manifest["temporal_result_identity"] == "temporal-result-id"
    assert temporal_manifest["descriptive_flag_counts"]["all_blocks_negative_ic"] == 1
    assert temporal_manifest["descriptive_flag_counts"]["all_blocks_negative_spread"] == 1
    assert temporal_manifest["limitations"] == list(runner._TEMPORAL_LIMITATIONS)

    missing_spread = SimpleNamespace(**vars(objects.temporal.block_results[0]))
    delattr(missing_spread, "mean_daily_mean_spread")
    malformed = SimpleNamespace(**vars(objects.temporal))
    malformed.block_results = (missing_spread, *objects.temporal.block_results[1:])
    with pytest.raises(AttributeError, match="mean_daily_mean_spread"):
        runner._temporal_artifact_rows(malformed)


@pytest.mark.parametrize("corruption", ("source_identity", "dimensions"))
def test_temporal_corruption_aborts_overwrite_and_preserves_previous_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    corruption: str,
) -> None:
    database, _, _, first = _run(monkeypatch, tmp_path)
    output = first["output_root"]
    previous_manifest = (output / "experiment_manifest.json").read_bytes()
    _, objects = _install_pipeline(monkeypatch, database)
    if corruption == "source_identity":
        objects.temporal.source_result_identity = "wrong-source"
        message = "source result identity"
    else:
        objects.temporal.block_results = objects.temporal.block_results[:-1]
        message = "dimensions"
    with pytest.raises(ValueError, match=message):
        runner.run_neutral_panel_factor_evaluation(
            database_path=database,
            start_date="2021-01-04",
            end_date="2021-01-05",
            output_root=output,
            overwrite=True,
        )
    assert (output / "experiment_manifest.json").read_bytes() == previous_manifest
    assert not tuple(output.parent.glob(f".{output.name}.tmp-*"))


@pytest.mark.parametrize(
    ("corruption", "message"),
    (
        ("source_identity", "source identity"),
        ("pair_dimensions", "summary dimensions"),
        ("block_dimensions", "block dimensions"),
        ("ordering", "summary dimensions"),
        ("duplicate_daily", "daily pair/date dimensions"),
        ("block_membership", "block daily membership"),
    ),
)
def test_redundancy_corruption_aborts_overwrite_and_preserves_previous_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    corruption: str,
    message: str,
) -> None:
    database, _, _, first = _run(monkeypatch, tmp_path)
    output = first["output_root"]
    previous_manifest = (output / "experiment_manifest.json").read_bytes()
    _, objects = _install_pipeline(monkeypatch, database)
    if corruption == "source_identity":
        objects.redundancy.source_dataset_identity = "wrong-source"
    elif corruption == "pair_dimensions":
        objects.redundancy.summaries = objects.redundancy.summaries[:-1]
    elif corruption == "block_dimensions":
        objects.redundancy.block_correlations = (
            objects.redundancy.block_correlations[:-1]
        )
    elif corruption == "ordering":
        values = list(objects.redundancy.summaries)
        values[0], values[1] = values[1], values[0]
        objects.redundancy.summaries = tuple(values)
    elif corruption == "block_membership":
        block = objects.redundancy.block_correlations[0]
        block.included_daily_identities = ("wrong-daily-identity",)
        block.included_daily_identity_count = 1
        block.included_daily_identities_sha256 = runner._identity_collection_sha256(
            block.included_daily_identities,
        )
    else:
        values = list(objects.redundancy.daily_correlations)
        values[1] = values[0]
        objects.redundancy.daily_correlations = tuple(values)
    with pytest.raises(ValueError, match=message):
        runner.run_neutral_panel_factor_evaluation(
            database_path=database,
            start_date="2021-01-04",
            end_date="2021-01-05",
            output_root=output,
            overwrite=True,
        )
    assert (output / "experiment_manifest.json").read_bytes() == previous_manifest
    assert not tuple(output.parent.glob(f".{output.name}.tmp-*"))


@pytest.mark.parametrize(
    ("corruption", "message"),
    (
        ("source_identity", "source identity"),
        ("hypothesis_order", "summary dimensions"),
        ("dimensions", "daily dimensions"),
        ("duplicate_daily", "daily dimensions"),
        ("block_membership", "block provenance or daily membership"),
        ("control_order", "daily provenance or control order"),
    ),
)
def test_incremental_corruption_aborts_overwrite_and_preserves_previous_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    corruption: str,
    message: str,
) -> None:
    database, _, _, first = _run(monkeypatch, tmp_path)
    output = first["output_root"]
    previous_manifest = (output / "experiment_manifest.json").read_bytes()
    _, objects = _install_pipeline(monkeypatch, database)
    if corruption == "source_identity":
        objects.incremental.source_dataset_identity = "wrong-source"
    elif corruption == "hypothesis_order":
        values = list(objects.incremental.summaries)
        values[0], values[1] = values[1], values[0]
        objects.incremental.summaries = tuple(values)
    elif corruption == "dimensions":
        objects.incremental.daily_evaluations = objects.incremental.daily_evaluations[:-1]
    elif corruption == "duplicate_daily":
        values = list(objects.incremental.daily_evaluations)
        values[1] = values[0]
        objects.incremental.daily_evaluations = tuple(values)
    elif corruption == "block_membership":
        objects.incremental.block_evaluations[0].included_daily_identities = (
            "unknown-daily-identity",
        )
    else:
        target = next(
            item for item in objects.incremental.daily_evaluations
            if item.hypothesis_name == "volume_given_adx_rsi"
        )
        target.control_factors = tuple(reversed(target.control_factors))
    with pytest.raises(ValueError, match=message):
        runner.run_neutral_panel_factor_evaluation(
            database_path=database,
            start_date="2021-01-04",
            end_date="2021-01-05",
            output_root=output,
            overwrite=True,
        )
    assert (output / "experiment_manifest.json").read_bytes() == previous_manifest
    assert not tuple(output.parent.glob(f".{output.name}.tmp-*"))


@pytest.mark.parametrize(
    ("corruption", "message"),
    (
        ("source_identity", "source identity"),
        ("policy_order", "dimensions, keys, or canonical ordering"),
        ("weights", "policy weights"),
        ("dimensions", "dimensions, keys, or canonical ordering"),
        ("duplicate_daily", "dimensions, keys, or canonical ordering"),
        ("block_membership", "block membership"),
        ("contrast", "contrast policy references"),
    ),
)
def test_composite_corruption_aborts_overwrite_and_preserves_previous_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    corruption: str,
    message: str,
) -> None:
    database, _, _, first = _run(monkeypatch, tmp_path)
    output = first["output_root"]
    previous_manifest = (output / "experiment_manifest.json").read_bytes()
    _, objects = _install_pipeline(monkeypatch, database)
    if corruption == "source_identity":
        objects.composite.source_dataset_identity = "wrong-source"
    elif corruption == "policy_order":
        values = list(objects.composite.summaries)
        values[0], values[1] = values[1], values[0]
        objects.composite.summaries = tuple(values)
    elif corruption == "weights":
        objects.composite.daily_evaluations[0].factor_weights = (
            NEUTRAL_ADX_RSI_COMPOSITE_COMPARISON_V1.policies[1].factor_weights
        )
    elif corruption == "dimensions":
        objects.composite.block_evaluations = objects.composite.block_evaluations[:-1]
    elif corruption == "duplicate_daily":
        values = list(objects.composite.daily_evaluations)
        values[1] = values[0]
        objects.composite.daily_evaluations = tuple(values)
    elif corruption == "block_membership":
        objects.composite.block_evaluations[1].included_daily_identity_count = 99
    else:
        objects.composite.contrast_summaries[0].reference_policy = "RSI_ONLY"
    with pytest.raises(ValueError, match=message):
        runner.run_neutral_panel_factor_evaluation(
            database_path=database,
            start_date="2021-01-04",
            end_date="2021-01-05",
            output_root=output,
            overwrite=True,
        )
    assert (output / "experiment_manifest.json").read_bytes() == previous_manifest
    assert not tuple(output.parent.glob(f".{output.name}.tmp-*"))


def test_database_is_unchanged_and_no_network_or_current_vn100_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import socket

    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("network"))
    database, _, _, _ = _run(monkeypatch, tmp_path)
    assert database.read_bytes() == b"immutable-market-fixture"
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "get_vn100_symbols" not in source
    assert "Vnstock(" not in source


@pytest.mark.parametrize(
    "unsafe",
    (
        runner.PROJECT_ROOT,
        runner.PROJECT_ROOT / ".git",
        runner.PROJECT_ROOT / "data",
        runner.PROJECT_ROOT / "research",
        runner.PROJECT_ROOT / "research_results",
    ),
)
def test_existing_unsafe_and_cache_containment_rejection(
    tmp_path: Path,
    unsafe: Path,
) -> None:
    database = tmp_path / "market.db"
    database.write_bytes(b"db")
    with pytest.raises(ValueError, match="dedicated"):
        runner._validate_output_target(unsafe, database_path=database, overwrite=True)
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        runner._validate_output_target(existing, database_path=database, overwrite=False)
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="contain"):
        runner._resolved_cache_root(output / "cache", output=output, database=database)
    with pytest.raises(ValueError, match="contain"):
        runner._resolved_cache_root(tmp_path, output=output, database=database)


def test_overwrite_is_exact_and_failed_validation_preserves_previous_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    legacy_output = tmp_path / "result"
    legacy_output.mkdir()
    legacy_files = tuple(
        name for name in runner._REQUIRED_FILENAMES
        if not name.startswith("composite_")
    )
    assert len(legacy_files) == 15
    for filename in legacy_files:
        (legacy_output / filename).write_text("legacy-fifteen-file-target", encoding="utf-8")
    original_validate_emitted = runner._validate_emitted
    monkeypatch.setattr(
        runner,
        "_validate_emitted",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("injected legacy-target validation failure")
        ),
    )
    with pytest.raises(ValueError, match="legacy-target"):
        _run(monkeypatch, tmp_path, overwrite=True)
    assert tuple(sorted(path.name for path in legacy_output.iterdir())) == tuple(
        sorted(legacy_files)
    )
    assert all(
        path.read_text(encoding="utf-8") == "legacy-fifteen-file-target"
        for path in legacy_output.iterdir()
    )
    monkeypatch.setattr(runner, "_validate_emitted", original_validate_emitted)
    _, _, _, first = _run(monkeypatch, tmp_path, overwrite=True)
    output = first["output_root"]
    assert tuple(sorted(path.name for path in output.iterdir())) == tuple(
        sorted(runner._REQUIRED_FILENAMES)
    )
    assert all(
        path.read_text(encoding="utf-8") != "legacy-fifteen-file-target"
        for path in output.iterdir()
    )
    previous_manifest = (output / "experiment_manifest.json").read_bytes()
    _run(monkeypatch, tmp_path, overwrite=True)
    assert output.is_dir()
    assert not tuple(output.parent.glob(f".{output.name}.backup-*"))
    monkeypatch.setattr(
        runner,
        "_validate_emitted",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("injected validation failure")),
    )
    with pytest.raises(ValueError, match="injected"):
        _run(monkeypatch, tmp_path, overwrite=True)
    assert (output / "experiment_manifest.json").read_bytes() == previous_manifest
    assert not tuple(output.parent.glob(f".{output.name}.tmp-*"))


def test_cli_defaults_default_output_and_fresh_import_isolation(tmp_path: Path) -> None:
    arguments = runner._parser().parse_args([
        "--database-path", "data/market.db",
        "--start-date", "2021-01-04",
        "--end-date", "2021-01-05",
    ])
    assert arguments.benchmark_symbol == "VNINDEX"
    assert arguments.minimum_history_sessions == 50
    assert arguments.maximum_staleness_sessions == 5
    assert arguments.cache_root is None
    assert arguments.cache_codec == "npz_numeric_v1"
    assert runner.RUNNER_VERSION == "v5"
    expected = runner.PROJECT_ROOT / "research_results" / (
        "quantlab_neutral_panel_factor_evaluation_2021-01-04_2021-01-05"
    )
    assert runner._output_path(
        None, start_date="2021-01-04", end_date="2021-01-05",
    ) == expected.resolve()
    script = """
import json
import socket
import sqlite3
import sys
sqlite3.connect = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('database'))
socket.create_connection = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('network'))
import research.run_quantlab_neutral_panel_factor_evaluation
forbidden = {
    'backtesting.engine', 'backtesting.trade', 'backtesting.portfolio_simulator',
    'backtesting.walk_forward', 'backtesting.walk_forward_optimizer',
    'quantlab.candidates.frozen_q70', 'quantlab.alpha.frozen_q70',
    'strategy.paper_v2_scanner', 'vnstock',
}
print(json.dumps(sorted(forbidden.intersection(sys.modules))))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=runner.PROJECT_ROOT,
        env=dict(
            os.environ,
            MARKET_DATABASE_PATH=str(tmp_path / "must-not-exist.db"),
            PYTHONDONTWRITEBYTECODE="1",
        ),
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == []
    assert not (tmp_path / "must-not-exist.db").exists()
