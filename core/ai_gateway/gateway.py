"""
Model Gateway - Routes tasks to models with fallback, validation, and provenance.
"""
import asyncio
import time
import json
import jsonschema
import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from .contracts import (
    AITaskRequest,
    AITaskResult,
    TaskType,
    QualityLevel,
    ErrorType,
    Provenance,
    ValidationResult,
)
from .providers import GeminiProvider, OllamaCloudProvider
from .providers.base import ProviderAdapter
from .deterministic import (
    rule_categorise,
    extractive_summarise,
    keyword_extract,
    statistical_projection,
)
from config.settings import get_ollama_num_ctx

logger = logging.getLogger(__name__)


@dataclass
class ModelHealth:
    """Tracks health state of a model."""
    provider: str
    model: str
    available: bool = True
    error_rate: float = 0.0
    consecutive_failures: int = 0
    last_check: float = 0
    status: str = "healthy"  # healthy, degraded, unavailable


class ModelGateway:
    """Main gateway for executing AI tasks with routing, fallback, and provenance."""

    def __init__(self, routing_config: Optional[Dict] = None):
        self.routing_config = routing_config or self._default_routing_config()
        self.providers: Dict[str, ProviderAdapter] = {}
        self.health: Dict[str, ModelHealth] = {}
        self._init_providers()

    def _default_routing_config(self) -> Dict:
        """Default routing policy configuration."""
        return {
            "model_registry": {
                "gemini-3.5-flash-lite": {
                    "provider": "google",
                    "model": "gemini-3.5-flash-lite",
                    "tasks": ["categorise", "extract", "summarise"],
                    "cost_class": "low",
                    "priority": 1,
                    "thinking_level": "minimal",
                },
                "gemini-3.5-flash": {
                    "provider": "google",
                    "model": "gemini-3.5-flash",
                    "tasks": ["categorise", "extract", "summarise", "synthesise"],
                    "cost_class": "medium",
                    "priority": 2,
                    "thinking_level": "low",
                },
                "gemini-3.6-flash": {
                    "provider": "google",
                    "model": "gemini-3.6-flash",
                    "tasks": ["summarise", "synthesise", "project"],
                    "cost_class": "medium",
                    "priority": 3,
                    "thinking_level": "medium",
                },
                "nemotron-3-super": {
                    "provider": "ollama",
                    "model": "nemotron-3-super:cloud",
                    "tasks": ["summarise", "synthesise", "project", "extract"],
                    "cost_class": "medium",
                    "priority": 2,
                    "context_window": 262144,
                },
                "gpt-oss-120b": {
                    "provider": "ollama",
                    "model": "gpt-oss:120b-cloud",
                    "tasks": ["summarise", "synthesise", "project", "extract"],
                    "cost_class": "medium",
                    "priority": 3,
                    "context_window": 131072,
                },
            },
            "routing_policies": {
                "categorise": {
                    "primary": "gemini-3.5-flash-lite",
                    "fallback": [
                        "gemini-3.5-flash",
                        "nemotron-3-super",
                        "gpt-oss-120b",
                    ],
                    "deterministic_fallback": True,
                },
                "extract": {
                    "primary": "gemini-3.5-flash-lite",
                    "fallback": [
                        "gemini-3.5-flash",
                        "nemotron-3-super",
                        "gpt-oss-120b",
                    ],
                    "deterministic_fallback": True,
                },
                "summarise": {
                    "primary": "gemini-3.6-flash",
                    "fallback": [
                        "nemotron-3-super",
                        "gpt-oss-120b",
                        "gemini-3.5-flash",
                    ],
                    "deterministic_fallback": True,
                },
                "synthesise": {
                    "primary": "gemini-3.6-flash",
                    "fallback": [
                        "nemotron-3-super",
                        "gpt-oss-120b",
                        "gemini-3.5-flash",
                    ],
                    "deterministic_fallback": True,
                },
                "project": {
                    "primary": "gemini-3.6-flash",
                    "fallback": [
                        "nemotron-3-super",
                        "gpt-oss-120b",
                        "gemini-3.5-flash",
                    ],
                    "deterministic_fallback": True,
                },
            },
        }

    def _init_providers(self):
        """Initialize provider adapters from config and environment."""
        import os

        # Google Gemini
        gemini_key = os.getenv("GEMINI_API_KEY")
        if gemini_key:
            try:
                for name, cfg in self.routing_config["model_registry"].items():
                    if cfg["provider"] == "google":
                        self.providers[name] = GeminiProvider(
                            api_key=gemini_key,
                            model=cfg["model"],
                            thinking_level=cfg.get("thinking_level", "low"),
                        )
                        self.health[name] = ModelHealth(
                            provider="google", model=cfg["model"]
                        )
            except ImportError as e:
                logger.warning(f"Gemini provider not available: {e}")

        # Ollama Cloud
        ollama_key = os.getenv("OLLAMA_API_KEY")
        ollama_url = os.getenv("OLLAMA_BASE_URL", "https://api.ollama.com")
        if ollama_key:
            for name, cfg in self.routing_config["model_registry"].items():
                if cfg["provider"] == "ollama":
                    self.providers[name] = OllamaCloudProvider(
                        base_url=ollama_url,
                        api_key=ollama_key,
                        model=cfg["model"],
                    )
                    self.health[name] = ModelHealth(
                        provider="ollama", model=cfg["model"]
                    )

        logger.info(f"Initialized providers: {list(self.providers.keys())}")

    def _get_routing_policy(self, task: TaskType) -> Dict:
        return self.routing_config["routing_policies"].get(task.value, {})

    def _build_candidate_chain(self, task: TaskType) -> List[str]:
        """Build ordered list of model candidates for a task."""
        policy = self._get_routing_policy(task)
        candidates = [policy.get("primary")] + policy.get("fallback", [])
        return [c for c in candidates if c in self.providers]

    def _check_context_fit(self, model_key: str, input_tokens: int) -> bool:
        """Check if model's context window can accommodate the input."""
        provider = self.providers.get(model_key)
        if not provider:
            return False
        caps = provider.get_capabilities()
        context_window = caps.get("context_window", 4096)
        max_input = int(context_window * 0.6)  # Reserve 40% for output + system
        return input_tokens <= max_input

    def _is_healthy(self, model_key: str) -> bool:
        h = self.health.get(model_key)
        return h is not None and h.status != "unavailable"

    def _classify_error(self, error: Exception) -> ErrorType:
        err_str = str(error).lower()
        if any(x in err_str for x in ["timeout", "429", "500", "502", "503", "504", "connection"]):
            return ErrorType.RETRYABLE
        if any(x in err_str for x in ["auth", "401", "403", "invalid key", "unsupported"]):
            return ErrorType.NON_RETRYABLE
        if any(x in err_str for x in ["json", "schema", "validation", "parse", "empty"]):
            return ErrorType.OUTPUT_FAILURE
        return ErrorType.RETRYABLE

    def _record_failure(self, model_key: str):
        h = self.health.get(model_key)
        if h:
            h.consecutive_failures += 1
            if h.consecutive_failures >= 3:
                h.status = "degraded"
            if h.consecutive_failures >= 5:
                h.status = "unavailable"

    def _record_success(self, model_key: str):
        h = self.health.get(model_key)
        if h:
            h.consecutive_failures = 0
            h.status = "healthy"

    def _build_prompt(self, request: AITaskRequest) -> str:
        """Build task-specific prompt."""
        prompts = {
            TaskType.CATEGORISE: f"""Categorise this article into one of the AI Pulse themes:
Agentic Systems & DevTools
Frontier Models & Benchmarks
Hardware/Compute/LLMOps
Enterprise Strategy & ROI
Governance/Safety/Policy
AI Security & Trust
AI-Assisted Software Engineering

Article:
{request.input}

Return JSON: {{"category": "theme name"}}""",
            TaskType.SUMMARISE: f"Summarise this content:\n{request.input}",
            TaskType.EXTRACT: f"Extract key entities, metrics, and signals from:\n{request.input}",
            TaskType.PROJECT: f"Project future trends from these signals:\n{request.input}",
            TaskType.SYNTHESISE: f"Synthesise these documents:\n{request.input}",
        }
        return prompts.get(request.task, request.input)

    def _get_schema(self, task: TaskType) -> Optional[Dict]:
        """Get JSON schema for structured output."""
        schemas = {
            TaskType.CATEGORISE: {
                "type": "object",
                "properties": {"category": {"type": "string"}},
                "required": ["category"],
            },
            TaskType.EXTRACT: {
                "type": "object",
                "properties": {
                    "entities": {"type": "array", "items": {"type": "string"}},
                    "metrics": {"type": "array", "items": {"type": "string"}},
                    "signals": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["entities", "metrics", "signals"],
            },
        }
        return schemas.get(task)

    async def execute(self, request: AITaskRequest) -> AITaskResult:
        """Execute a task with routing, retry, fallback, and provenance."""
        task = request.task
        policy = self._get_routing_policy(task)
        candidates = self._build_candidate_chain(task)
        input_tokens = request.estimated_input_tokens()

        start_time = time.time()
        last_error = None
        fallback_used = False
        fallback_from = None
        attempts = 0

        for model_key in candidates:
            if not self._is_healthy(model_key):
                logger.debug(f"Skipping unhealthy model: {model_key}")
                continue

            if not self._check_context_fit(model_key, input_tokens):
                logger.debug(
                    f"Skipping {model_key}: input {input_tokens} tokens exceeds context budget"
                )
                continue

            provider = self.providers[model_key]
            max_attempts = 2

            for attempt in range(max_attempts):
                attempts += 1
                try:
                    result = await self._execute_with_validation(
                        provider, request, model_key
                    )

                    # Success
                    self._record_success(model_key)
                    health = self.health[model_key]
                    provenance = Provenance(
                        task=task.value,
                        method="llm",
                        provider=health.provider,
                        model=health.model,
                        latency_ms=int((time.time() - start_time) * 1000),
                        attempts=attempts,
                        fallback_used=fallback_used,
                        fallback_from=fallback_from,
                        validation=ValidationResult(status="valid"),
                        correlation_id=request.correlation_id,
                    )
                    return AITaskResult.success(result, provenance)

                except Exception as e:
                    error_type = self._classify_error(e)
                    last_error = e

                    if error_type == ErrorType.RETRYABLE and attempt < max_attempts - 1:
                        await asyncio.sleep(2 ** attempt)  # exponential backoff
                        continue

                    if error_type in (ErrorType.RETRYABLE, ErrorType.OUTPUT_FAILURE):
                        # Try next model in fallback chain
                        self._record_failure(model_key)
                        if not fallback_used:
                            fallback_from = model_key
                        fallback_used = True
                        break

                    # Non-retryable - don't try other models
                    self._record_failure(model_key)
                    break

        # All LLMs failed - deterministic fallback
        if policy.get("deterministic_fallback", False) and request.allow_deterministic_fallback:
            return await self._deterministic_fallback(request, last_error, fallback_used, fallback_from, start_time)

        # Complete failure
        provenance = Provenance(
            task=task.value,
            method="failed",
            latency_ms=int((time.time() - start_time) * 1000),
            attempts=attempts,
            fallback_used=fallback_used,
            fallback_from=fallback_from,
            correlation_id=request.correlation_id,
            error=str(last_error),
        )
        return AITaskResult.failure(str(last_error), provenance)

    async def _execute_with_validation(
        self, provider: ProviderAdapter, request: AITaskRequest, model_key: str
    ) -> Any:
        prompt = self._build_prompt(request)
        schema = self._get_schema(request.task)

        if schema and request.schema:
            # Merge with request schema
            schema = {**schema, **request.schema}

        gen_kwargs = {
            "temperature": getattr(request, "temperature", 0.3),
            "max_tokens": getattr(request, "max_tokens", 2000),
        }

        if schema:
            response = await provider.generate_structured(prompt, schema, **gen_kwargs)
            data = json.loads(response.get("json", response.get("text", "{}")))
        else:
            response = await provider.generate(prompt, **gen_kwargs)
            data = response.get("text", "")

        # Validate against schema if provided
        if schema:
            jsonschema.validate(data, schema)

        return data

    async def _deterministic_fallback(
        self,
        request: AITaskRequest,
        error: Exception,
        fallback_used: bool,
        fallback_from: Optional[str],
        start_time: float,
    ) -> AITaskResult:
        """Execute deterministic fallback based on task type."""
        task = request.task

        if task == TaskType.SUMMARISE:
            result = extractive_summarise(request.input)
        elif task == TaskType.CATEGORISE:
            result = rule_categorise(request.input)
        elif task == TaskType.EXTRACT:
            result = keyword_extract(request.input)
        elif task == TaskType.PROJECT:
            # Try to extract numeric series from input
            import re
            numbers = [float(n) for n in re.findall(r"\d+\.?\d*", request.input)]
            result = statistical_projection(numbers) if numbers else {"trend": "unknown", "method": "deterministic"}
        elif task == TaskType.SYNTHESISE:
            result = extractive_summarise(request.input, max_sentences=8)
        else:
            result = {"error": "No deterministic fallback available", "original_error": str(error)}

        provenance = Provenance(
            task=task.value,
            method="deterministic",
            provider=None,
            model=None,
            latency_ms=int((time.time() - start_time) * 1000),
            attempts=1,
            fallback_used=fallback_used,
            fallback_from=fallback_from,
            correlation_id=request.correlation_id,
            error=f"LLM failed: {error}; used deterministic fallback",
        )
        return AITaskResult.success(result, provenance)

    async def health_check_all(self) -> Dict[str, Dict]:
        """Check health of all providers."""
        results = {}
        for name, provider in self.providers.items():
            results[name] = await provider.health_check()
        return results

    def get_model_info(self) -> Dict[str, Dict]:
        """Get capabilities of all registered models."""
        return {name: p.get_capabilities() for name, p in self.providers.items()}


# Global gateway instance
_gateway: Optional[ModelGateway] = None


def get_gateway() -> ModelGateway:
    """Get or create the global ModelGateway instance."""
    global _gateway
    if _gateway is None:
        _gateway = ModelGateway()
    return _gateway