"""
AI Gateway Contracts - Task/Result schemas, enums, and data classes.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, List
import time
import uuid


class TaskType(Enum):
    """Logical AI tasks that the application can request."""
    CATEGORISE = "categorise"
    EXTRACT = "extract"
    SUMMARISE = "summarise"
    SYNTHESISE = "synthesise"
    PROJECT = "project"


class QualityLevel(Enum):
    """Quality requirement for the task."""
    LOW = "low"          # Fast, cheap, good enough
    STANDARD = "standard"  # Balanced
    HIGH = "high"        # Best quality, cost/latency secondary


class ErrorType(Enum):
    """Error classification for retry/fallback logic."""
    RETRYABLE = "retryable"           # Timeout, 429, 5xx, connection
    NON_RETRYABLE = "non_retryable"   # Auth, invalid request, policy
    OUTPUT_FAILURE = "output_failure" # Invalid JSON, schema mismatch, empty


@dataclass
class AITaskRequest:
    """Request for an AI task."""
    task: TaskType
    input: str
    schema: Optional[Dict] = None
    system: Optional[str] = None
    priority: str = "normal"
    quality_requirement: QualityLevel = QualityLevel.STANDARD
    latency_requirement: str = "normal"
    max_cost: Optional[float] = None
    allow_fallback: bool = True
    allow_deterministic_fallback: bool = True
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    metadata: Dict = field(default_factory=dict)
    temperature: float = 0.3
    max_tokens: int = 2000

    def estimated_input_tokens(self) -> int:
        """Rough token estimate (3 chars/token)."""
        return len(self.input) // 3


@dataclass
class ValidationResult:
    """Schema validation result."""
    status: str  # "valid", "invalid"
    errors: List[str] = field(default_factory=list)


@dataclass
class Provenance:
    """Complete provenance information for a result."""
    task: str
    method: str  # "llm" or "deterministic"
    provider: Optional[str] = None
    model: Optional[str] = None
    prompt_version: str = ""
    schema_version: str = ""
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ"))
    latency_ms: int = 0
    attempts: int = 1
    fallback_used: bool = False
    fallback_from: Optional[str] = None
    validation: Optional[ValidationResult] = None
    correlation_id: str = ""
    source_ids: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "task": self.task,
            "method": self.method,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "timestamp": self.timestamp,
            "latency_ms": self.latency_ms,
            "attempts": self.attempts,
            "fallback_used": self.fallback_used,
            "fallback_from": self.fallback_from,
            "validation": self.validation.__dict__ if self.validation else None,
            "correlation_id": self.correlation_id,
            "source_ids": self.source_ids,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Provenance":
        val = data.get("validation")
        validation = ValidationResult(**val) if val else None
        return cls(
            task=data.get("task", ""),
            method=data.get("method", ""),
            provider=data.get("provider"),
            model=data.get("model"),
            prompt_version=data.get("prompt_version", ""),
            schema_version=data.get("schema_version", ""),
            timestamp=data.get("timestamp", ""),
            latency_ms=data.get("latency_ms", 0),
            attempts=data.get("attempts", 1),
            fallback_used=data.get("fallback_used", False),
            fallback_from=data.get("fallback_from"),
            validation=validation,
            correlation_id=data.get("correlation_id", ""),
            source_ids=data.get("source_ids", []),
            error=data.get("error"),
        )

    def short_summary(self) -> str:
        """Human-readable one-liner for UI display."""
        if self.method == "deterministic":
            return f"Deterministic ({self.task})"
        provider = self.provider or "?"
        model = self.model or "?"
        fb = " (fallback)" if self.fallback_used else ""
        return f"{provider}/{model}{fb}"


@dataclass
class AITaskResult:
    """Result of an AI task execution."""
    status: str  # "success", "failed"
    result: Any
    provenance: Provenance

    def is_success(self) -> bool:
        return self.status == "success"

    def to_dict(self) -> Dict:
        return {
            "status": self.status,
            "result": self.result,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def success(
        cls,
        result: Any,
        provenance: Provenance,
    ) -> "AITaskResult":
        return cls(status="success", result=result, provenance=provenance)

    @classmethod
    def failure(
        cls,
        error: str,
        provenance: Provenance,
    ) -> "AITaskResult":
        provenance.error = error
        return cls(status="failed", result={"error": error}, provenance=provenance)