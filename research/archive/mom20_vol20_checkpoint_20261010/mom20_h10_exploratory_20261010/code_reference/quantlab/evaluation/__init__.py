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
    "PanelFactorRedundancySpec": ".panel_factor_redundancy",
    "DailyFactorPairCorrelation": ".panel_factor_redundancy",
    "FactorPairBlockCorrelation": ".panel_factor_redundancy",
    "FactorPairRedundancySummary": ".panel_factor_redundancy",
    "PanelFactorRedundancyResult": ".panel_factor_redundancy",
    "NEUTRAL_PANEL_FACTOR_REDUNDANCY_V1": ".panel_factor_redundancy",
    "evaluate_panel_factor_redundancy": ".panel_factor_redundancy",
    "IncrementalFactorHypothesis": ".panel_factor_incremental_analysis",
    "PanelFactorIncrementalAnalysisSpec": ".panel_factor_incremental_analysis",
    "DailyIncrementalFactorEvaluation": ".panel_factor_incremental_analysis",
    "IncrementalFactorBlockEvaluation": ".panel_factor_incremental_analysis",
    "IncrementalFactorEvaluationSummary": ".panel_factor_incremental_analysis",
    "PanelFactorIncrementalAnalysisResult": ".panel_factor_incremental_analysis",
    "NEUTRAL_PANEL_INCREMENTAL_FACTOR_ANALYSIS_V1": ".panel_factor_incremental_analysis",
    "evaluate_panel_factor_incremental_analysis": ".panel_factor_incremental_analysis",
    "CompositeFactorWeight": ".panel_composite_analysis",
    "NeutralCompositePolicy": ".panel_composite_analysis",
    "PanelCompositeAnalysisSpec": ".panel_composite_analysis",
    "DailyCompositeEvaluation": ".panel_composite_analysis",
    "CompositeBlockEvaluation": ".panel_composite_analysis",
    "CompositeEvaluationSummary": ".panel_composite_analysis",
    "PanelCompositeAnalysisResult": ".panel_composite_analysis",
    "NEUTRAL_ADX_RSI_COMPOSITE_COMPARISON_V1": ".panel_composite_analysis",
    "evaluate_panel_composites": ".panel_composite_analysis",
    "PanelSelectionPolicy": ".panel_policy_selection_diagnostics",
    "PanelSelectionDiagnosticsSpec": ".panel_policy_selection_diagnostics",
    "DailyPolicySelectionDiagnostics": ".panel_policy_selection_diagnostics",
    "PolicySelectionTurnoverSummary": ".panel_policy_selection_diagnostics",
    "PolicySelectionOverlapSummary": ".panel_policy_selection_diagnostics",
    "PanelPolicySelectionDiagnosticsResult": ".panel_policy_selection_diagnostics",
    "NEUTRAL_ADX_RSI_SELECTION_DIAGNOSTICS_V1": ".panel_policy_selection_diagnostics",
    "evaluate_panel_policy_selection_diagnostics": ".panel_policy_selection_diagnostics",
    "rank_panel_policy_candidates": ".panel_policy_selection_diagnostics",
    "ResearchDecision": ".research_decision_gate",
    "EvidenceStatus": ".research_decision_gate",
    "ResearchDecisionGateSpec": ".research_decision_gate",
    "ResearchDecisionEvidenceInput": ".research_decision_gate",
    "DecisionEvidence": ".research_decision_gate",
    "CandidateResearchDecision": ".research_decision_gate",
    "ResearchDecisionGateResult": ".research_decision_gate",
    "NEUTRAL_RESEARCH_DECISION_GATE_V1": ".research_decision_gate",
    "load_phase59b_decision_evidence": ".research_decision_gate",
    "evaluate_research_decision_gate": ".research_decision_gate",
    "EvidenceSourceStatus": ".portfolio_risk_evidence",
    "EvidenceStrength": ".portfolio_risk_evidence",
    "EvidenceMetricSummary": ".portfolio_risk_evidence",
    "EvidenceCostSummary": ".portfolio_risk_evidence",
    "DrawdownAssociation": ".portfolio_risk_evidence",
    "RegimeEvidenceSummary": ".portfolio_risk_evidence",
    "PolicyImplication": ".portfolio_risk_evidence",
    "PortfolioRiskEvidenceSourceResult": ".portfolio_risk_evidence",
    "PortfolioRiskEvidenceResult": ".portfolio_risk_evidence",
    "evaluate_portfolio_risk_evidence": ".portfolio_risk_evidence",
    "evaluate_portfolio_risk_evidence_sources": ".portfolio_risk_evidence",
    "PersistedEvidenceSourceClassification": ".portfolio_risk_evidence_sources",
    "PersistedPortfolioEvidenceSource": ".portfolio_risk_evidence_sources",
    "load_phase6_portfolio_evidence": ".portfolio_risk_evidence_sources",
    "load_frozen_q70_cost_evidence": ".portfolio_risk_evidence_sources",
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
