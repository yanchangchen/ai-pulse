"""
AI Gateway - Multi-model routing, fallback, and deterministic intelligence layer.
"""
from .contracts import AITaskRequest, AITaskResult, TaskType, QualityLevel, ErrorType, Provenance
from .gateway import ModelGateway, get_gateway
from .deterministic import (
    rule_categorise,
    extractive_summarise,
    keyword_extract,
    statistical_projection,
)

__all__ = [
    "AITaskRequest",
    "AITaskResult",
    "TaskType",
    "QualityLevel",
    "ErrorType",
    "Provenance",
    "ModelGateway",
    "get_gateway",
    "rule_categorise",
    "extractive_summarise",
    "keyword_extract",
    "statistical_projection",
]