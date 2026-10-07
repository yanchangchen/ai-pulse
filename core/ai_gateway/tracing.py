"""OpenTelemetry span emission for the Model Gateway (Phase 0.3, Pillar F).

Every gateway execution is traced as one span and exported via OTLP to
Langfuse Cloud (see Future.md, Pillar F).  The span -> Langfuse mapping
follows the documented OTel attribute conventions: ``langfuse.observation.*``
for input/output/metadata, ``gen_ai.*`` semantic conventions for model and
token usage, so each successful LLM call renders as a *generation* in the
Langfuse UI with model, usage, and cost accounting.

Design constraints (mirroring ``call_log.py``):

- **Env-gated** — tracing activates only when both Langfuse keys resolve
  through ``config.settings`` AND ``opentelemetry`` imports cleanly.
  Otherwise every call is a no-op: no thread, no network, ``trace_id``
  stays ``None``, so offline runs and tests stay quiet.
- **Best-effort** — a tracing failure must never reduce availability.  The
  exporter runs on the ``BatchSpanProcessor`` background daemon thread, so
  the gateway is never blocked by the OTLP round trip, and every error in
  here is swallowed at DEBUG level.
- **Dedicated provider, not the global one** — ``trace.set_tracer_provider``
  is once-only process state; a private ``TracerProvider`` lets tests build
  isolated providers (in-memory exporter) alongside the real app.
"""
import logging
from contextlib import contextmanager
from typing import Optional

from .contracts import AITaskRequest, AITaskResult

logger = logging.getLogger(__name__)

# Cap the traced input/output so a 30k-char Sage prompt cannot bloat every
# trace (Langfuse Hobby meters units — the full text lives in llm history,
# the trace only needs enough to recognise the call).
_MAX_TEXT_CHARS = 2000
_TRUNCATION_MARKER = "… [truncated]"

_tracer = None
_provider = None
_tracing_enabled: Optional[bool] = None


def is_tracing_enabled() -> bool:
    """True when Langfuse keys are configured (and OTel importable)."""
    global _tracing_enabled
    if _tracing_enabled is None:
        _tracing_enabled = _configure()
    return _tracing_enabled


def _configure() -> bool:
    """Build the OTLP pipeline; return False (no-op mode) on any failure."""
    try:
        from config.settings import LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST

        if not LANGFUSE_PUBLIC_KEY or not LANGFUSE_SECRET_KEY:
            return False

        import base64

        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        auth = base64.b64encode(
            f"{LANGFUSE_PUBLIC_KEY}:{LANGFUSE_SECRET_KEY}".encode("utf-8")
        ).decode("ascii")

        provider = TracerProvider(
            resource=Resource.create({"service.name": "ai-pulse"})
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=f"{LANGFUSE_HOST}/api/public/otel/v1/traces",
                    headers={
                        "Authorization": f"Basic {auth}",
                        "x-langfuse-ingestion-version": "4",
                    },
                    # Keep the observability round trip strictly off the
                    # request path even when it fails (unreachable host,
                    # bad keys): time out quickly and drop the batch.
                    timeout=10,
                )
            )
        )
        _set_provider(provider)
        logger.info(
            f"Gateway tracing enabled: OTLP spans -> {LANGFUSE_HOST} (Langfuse)"
        )
        return True
    except Exception as e:
        # Missing opentelemetry packages or a broken env: no-op mode.
        logger.debug(f"Gateway tracing disabled: {e}")
        return False


def _set_provider(provider) -> None:
    """Install a tracer provider as this module's singleton (test hook)."""
    global _tracer, _provider
    _provider = provider
    _tracer = provider.get_tracer("ai-pulse.gateway")


def get_tracer():
    """Return the tracer, or ``None`` when tracing is disabled."""
    if not is_tracing_enabled():
        return None
    return _tracer


def reset_tracing() -> None:
    """Forget all tracing state so the next use re-resolves the env.

    Used by the test autouse fixture (tests/conftest.py) to guarantee that
    real Langfuse keys in ``secrets.toml`` never make a test export spans,
    the same rationale as the Supabase offline pin.
    """
    global _tracer, _provider, _tracing_enabled
    _tracer = None
    _provider = None
    _tracing_enabled = None


def flush() -> None:
    """Best-effort export of pending spans (short-lived scripts, backfills)."""
    if _provider is None:
        return
    try:
        _provider.force_flush(timeout_millis=5000)
    except Exception as e:
        logger.debug(f"tracing flush skipped: {e}")


class _NoOpSpan:
    """Stand-in span so gateway code stays uniform when tracing is off."""

    def set_attribute(self, key, value) -> None:
        pass

    def set_status(self, *args, **kwargs) -> None:
        pass

    def get_span_context(self):
        return None


@contextmanager
def task_span(request: AITaskRequest):
    """Open one span for a gateway execution (``gateway.<task>``).

    Yields a real OTel span when tracing is enabled, else a no-op span.
    """
    tracer = get_tracer()
    if tracer is None:
        yield _NoOpSpan()
        return
    try:
        with tracer.start_as_current_span(f"gateway.{request.task.value}") as span:
            # Known up-front; the rest is filled in by finalize_span().
            span.set_attribute(
                "langfuse.observation.metadata.correlation_id",
                request.correlation_id,
            )
            span.set_attribute(
                "gen_ai.request.temperature", request.temperature
            )
            span.set_attribute("gen_ai.request.max_tokens", request.max_tokens)
            yield span
    except Exception as e:
        # Observability must never fail the task it observes.
        logger.debug(f"tracing span dropped: {e}")
        yield _NoOpSpan()


def finalize_span(span, request: AITaskRequest, result: AITaskResult) -> None:
    """Set completion attributes on the span from the task result.

    Follows the Langfuse OTel attribute mapping so successful LLM calls
    render as generations with model + token usage.
    """
    try:
        prov = result.provenance
        if prov.method == "llm":
            span.set_attribute("langfuse.observation.type", "generation")
        if prov.provider:
            span.set_attribute("langfuse.observation.metadata.provider", prov.provider)
        if prov.model:
            span.set_attribute("gen_ai.request.model", prov.model)
            span.set_attribute("gen_ai.response.model", prov.model)
        if prov.tokens_in is not None:
            span.set_attribute("gen_ai.usage.input_tokens", prov.tokens_in)
        if prov.tokens_out is not None:
            span.set_attribute("gen_ai.usage.output_tokens", prov.tokens_out)
        span.set_attribute(
            "langfuse.observation.metadata.attempts", prov.attempts
        )
        span.set_attribute(
            "langfuse.observation.metadata.fallback_used", prov.fallback_used
        )
        span.set_attribute(
            "langfuse.observation.metadata.method", prov.method
        )
        if prov.latency_ms:
            span.set_attribute(
                "langfuse.observation.metadata.latency_ms", prov.latency_ms
            )
        span.set_attribute("langfuse.observation.input", _cap(request.input))
        if result.is_success() and result.result is not None:
            output = (
                result.result
                if isinstance(result.result, str)
                else repr(result.result)
            )
            span.set_attribute("langfuse.observation.output", _cap(output))
        if prov.error:
            from opentelemetry.trace import Status, StatusCode

            span.set_status(Status(StatusCode.ERROR, str(prov.error)[:_MAX_TEXT_CHARS]))
            span.set_attribute("error.message", _cap(str(prov.error)))
    except Exception as e:
        logger.debug(f"tracing finalize skipped: {e}")


def _cap(text: str) -> str:
    """Truncate traced text so huge prompts cannot bloat every span."""
    text = str(text)
    if len(text) <= _MAX_TEXT_CHARS:
        return text
    return text[:_MAX_TEXT_CHARS] + _TRUNCATION_MARKER


def span_trace_id(span) -> Optional[str]:
    """32-char lowercase hex trace id of the span, or ``None`` when off.

    This is the value persisted to ``llm_calls.trace_id`` so a durable row
    can be joined back to its (short-lived) Langfuse trace.
    """
    try:
        ctx = span.get_span_context()
        if ctx is None:
            return None
        return f"{ctx.trace_id:032x}"
    except Exception as e:
        logger.debug(f"trace_id extraction skipped: {e}")
        return None
