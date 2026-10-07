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
    # LLM-as-judge evaluation calls (core/evaluator.py).  Routed
    # Gemini-first with no prompt template and no default output schema:
    # judges send fully-formed prompts and parse responses themselves,
    # and a deterministic "judge verdict" would be meaningless, so the
    # evaluate policy disables the deterministic fallback.
    EVALUATE = "evaluate"
    # Sage conversational replies (core/sage_agent.py).  Like EVALUATE,
    # prompts are fully formed (persona + wiki context + conversation)
    # and pass through verbatim with no output schema; a deterministic
    # substitute for Sage's voice would be fabrication, so the policy
    # disables the deterministic fallback.
    SAGE_CHAT = "sage_chat"


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
    # Optional model-key pin (Model Gateway registry key).  When set and
    # configured, the gateway routes this request to exactly that model —
    # no cross-model fallback.  Used by the evaluation judges so a whole
    # evaluation runs on one user-chosen model.
    preferred_model: Optional[str] = None
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    metadata: Dict = field(default_factory=dict)
    temperature: float = 0.3
    max_tokens: int = 2000
    # OTel trace id, stamped by the gateway tracing wrapper as soon as the
    # span opens (before routing starts) so every Provenance constructed
    # during execution — and the llm_calls row snapshotted from it — links
    # back to the trace.  None when tracing is disabled.
    trace_id: Optional[str] = None

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
    # Token usage as reported by the provider (None when the provider does
    # not report usage, or for deterministic-fallback results).  Persisted
    # to the Supabase ``llm_calls`` table by the gateway call log so cost
    # accounting is durable.
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    # OpenTelemetry trace id (32-char hex) when gateway tracing is enabled
    # (core/ai_gateway/tracing.py, Phase 0.3); links the durable llm_calls
    # row back to its trace in Langfuse.  None when tracing is off.
    trace_id: Optional[str] = None

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
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "trace_id": self.trace_id,
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
            tokens_in=data.get("tokens_in"),
            tokens_out=data.get("tokens_out"),
            trace_id=data.get("trace_id"),
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