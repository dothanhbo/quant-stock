"""Strategy-neutral point-in-time observation panels."""

from .contracts import (
    POINT_IN_TIME_OBSERVATION_INDEX_V1,
    ObservationAvailability,
    ObservationSessionAudit,
    PointInTimeObservationIndex,
    PointInTimeObservationIndexSpec,
)
from .observation_index import build_point_in_time_observation_index
from .feature_contracts import (
    FeatureFieldSpec,
    FeatureValueAvailability,
    PointInTimeFeaturePanel,
    PointInTimeFeaturePanelSpec,
)
from .feature_panel import attach_features_to_observation_index
from .neutral_features import (
    NEUTRAL_RESEARCH_FEATURE_PANEL_V1,
    NEUTRAL_RESEARCH_NUMERIC_FEATURES_V1,
    build_neutral_research_feature_panel,
    prepare_neutral_research_feature_source,
)
from .outcome_contracts import (
    POINT_IN_TIME_FORWARD_OUTCOMES_5_10_20_V1,
    PanelForwardOutcomeAvailability,
    PointInTimeOutcomePanel,
    PointInTimeOutcomePanelSpec,
)
from .outcome_panel import build_point_in_time_outcome_panel
from .research_dataset_contracts import (
    POINT_IN_TIME_NEUTRAL_RESEARCH_DATASET_V1,
    PointInTimeResearchDataset,
    PointInTimeResearchDatasetSpec,
    ResearchDatasetUse,
)
from .research_dataset import build_point_in_time_research_dataset

__all__ = [
    "ObservationAvailability",
    "ObservationSessionAudit",
    "PointInTimeObservationIndexSpec",
    "PointInTimeObservationIndex",
    "POINT_IN_TIME_OBSERVATION_INDEX_V1",
    "build_point_in_time_observation_index",
    "FeatureValueAvailability",
    "FeatureFieldSpec",
    "PointInTimeFeaturePanelSpec",
    "PointInTimeFeaturePanel",
    "attach_features_to_observation_index",
    "NEUTRAL_RESEARCH_NUMERIC_FEATURES_V1",
    "NEUTRAL_RESEARCH_FEATURE_PANEL_V1",
    "prepare_neutral_research_feature_source",
    "build_neutral_research_feature_panel",
    "PanelForwardOutcomeAvailability",
    "PointInTimeOutcomePanelSpec",
    "PointInTimeOutcomePanel",
    "POINT_IN_TIME_FORWARD_OUTCOMES_5_10_20_V1",
    "build_point_in_time_outcome_panel",
    "ResearchDatasetUse",
    "PointInTimeResearchDatasetSpec",
    "PointInTimeResearchDataset",
    "POINT_IN_TIME_NEUTRAL_RESEARCH_DATASET_V1",
    "build_point_in_time_research_dataset",
]
