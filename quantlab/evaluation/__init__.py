"""Pure descriptive candidate-factor evaluation contracts and helpers."""

from importlib import import_module


_EXPORT_MODULES = {
    "CandidateFactorOutcomeEvaluationResult": ".contracts",
    "DailyFactorOutcomeEvaluation": ".contracts",
    "DESCRIPTIVE_WARNING": ".contracts",
    "FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1": ".contracts",
    "FactorHorizonEvaluationSummary": ".contracts",
    "FactorOutcomeEvaluationSpec": ".contracts",
    "evaluate_candidate_factor_outcomes": ".factor_outcomes",
    "TemporalBlock": ".temporal_stability_contracts",
    "FactorTemporalStabilitySpec": ".temporal_stability_contracts",
    "FactorBlockStabilityResult": ".temporal_stability_contracts",
    "FactorHorizonTemporalStabilitySummary": ".temporal_stability_contracts",
    "CandidateFactorTemporalStabilityResult": ".temporal_stability_contracts",
    "FROZEN_Q70_VOLUME_RSI_TEMPORAL_STABILITY_V1": ".temporal_stability_contracts",
    "evaluate_candidate_factor_temporal_stability": ".temporal_stability",
    "PanelFactorDirection": ".panel_factor_contracts",
    "PanelFactorEvaluationSpec": ".panel_factor_contracts",
    "PanelDailyFactorEvaluation": ".panel_factor_contracts",
    "PanelFactorHorizonSummary": ".panel_factor_contracts",
    "PointInTimePanelFactorEvaluationResult": ".panel_factor_contracts",
    "NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1": ".panel_factor_contracts",
    "evaluate_point_in_time_panel_factors": ".panel_factor_analysis",
    "PanelTemporalBlock": ".panel_factor_temporal_stability",
    "PanelTemporalDirection": ".panel_factor_temporal_stability",
    "PanelFactorTemporalStabilitySpec": ".panel_factor_temporal_stability",
    "PanelFactorBlockStability": ".panel_factor_temporal_stability",
    "PanelFactorTemporalStabilitySummary": ".panel_factor_temporal_stability",
    "PanelFactorTemporalStabilityResult": ".panel_factor_temporal_stability",
    "NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V1": ".panel_factor_temporal_stability",
    "NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V2": ".panel_factor_temporal_stability",
    "evaluate_panel_factor_temporal_stability": ".panel_factor_temporal_stability",
}

__all__ = tuple(_EXPORT_MODULES)


def __getattr__(name: str):
    try:
        module_name = _EXPORT_MODULES[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted((*globals(), *__all__))
