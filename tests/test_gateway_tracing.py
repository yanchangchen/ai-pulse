"""Tests for the env-gated OTel span tracing from the Model Gateway
(Phase 0.3, Pillar F): core/ai_gateway/tracing.py.

All tests are offline: providers are fakes and the real OTLP exporter is
never built.  The conftest autouse fixture pins tracing off; the "enabled"
tests re-enable it with an in-memory span exporter and fake keys.
"""

import asyncio
import sys
from unittest.mock import MagicMock, patch

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode

from core.ai_gateway import tracing
from core.ai_gateway.contracts import AITaskRequest, Provenance, TaskType
from core.ai_gateway.gateway import ModelGateway, ModelHealth

from tests.test_llm_call_log import FakeProvider, _gateway_with


def _enable_in_memory(monkeypatch) -> InMemorySpanExporter:
    """Point the tracing singleton at an in-memory exporter (no network)."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracing._set_provider(provider)
    monkeypatch.setattr(tracing, "is_tracing_enabled", lambda: True)
    return exporter


# ---------------------------------------------------------------------------
# Gating: no keys / no opentelemetry -> silent no-op
# ---------------------------------------------------------------------------

class TestGating:
    def test_configure_false_without_keys(self, monkeypatch):
        monkeypatch.setattr("config.settings.LANGFUSE_PUBLIC_KEY", "")
        monkeypatch.setattr("config.settings.LANGFUSE_SECRET_KEY", "")
        tracing.reset_tracing()
        assert tracing._configure() is False
        assert tracing.get_tracer() is None

    def test_noop_span_when_disabled(self):
        gw = _gateway_with(FakeProvider(usage={"input_tokens": 1, "output_tokens": 2}))
        result = asyncio.run(gw.execute(AITaskRequest(task=TaskType.SAGE_CHAT, input="hi")))
        assert result.is_success()
        assert result.provenance.trace_id is None

    def test_missing_otel_package_degrades_to_noop(self, monkeypatch):
        # A None entry in sys.modules makes the in-function
        # `from opentelemetry...` import raise ImportError.
        monkeypatch.setitem(
            sys.modules, "opentelemetry.exporter.otlp.proto.http.trace_exporter", None
        )
        monkeypatch.setattr("config.settings.LANGFUSE_PUBLIC_KEY", "pk-lf-fake")
        monkeypatch.setattr("config.settings.LANGFUSE_SECRET_KEY", "sk-lf-fake")
        tracing.reset_tracing()
        assert tracing._configure() is False
        assert tracing.get_tracer() is None

    def test_config_failure_is_not_memoized(self, monkeypatch, caplog):
        # Regression (diagnosed 2026-10-12): an app process ran all day with
        # tracing silently off because the FIRST _configure() failure was
        # cached for the process lifetime at DEBUG level.  A *failed*
        # configuration (import hiccup, transient env problem) must return
        # False for the call but leave the gate un-memoized so the next
        # gateway call retries — and the failure must be a WARNING, visible
        # in the terminal.
        monkeypatch.setitem(
            sys.modules, "opentelemetry.exporter.otlp.proto.http.trace_exporter", None
        )
        monkeypatch.setattr("config.settings.LANGFUSE_PUBLIC_KEY", "pk-lf-fake")
        monkeypatch.setattr("config.settings.LANGFUSE_SECRET_KEY", "sk-lf-fake")
        tracing.reset_tracing()

        # _resolve_enabled is the real gate (conftest stubs only the public
        # is_tracing_enabled).
        assert tracing._resolve_enabled() is False
        assert tracing._tracing_enabled is None  # NOT memoized
        assert tracing._last_config_error is not None

        # The failure logs at WARNING so a dark process is diagnosable, and
        # the next call retries instead of staying dark.
        import logging as _logging
        with caplog.at_level(_logging.WARNING, logger="core.ai_gateway.tracing"):
            assert tracing._resolve_enabled() is False
        assert any("retry" in r.message for r in caplog.records)

    def test_config_success_is_memoized(self, monkeypatch):
        # Definitive verdicts (keys present + pipeline built; keys absent)
        # ARE memoized — no repeated configure attempts per gateway call.
        monkeypatch.setattr("config.settings.LANGFUSE_PUBLIC_KEY", "")
        monkeypatch.setattr("config.settings.LANGFUSE_SECRET_KEY", "")
        tracing.reset_tracing()

        assert tracing._resolve_enabled() is False
        assert tracing._tracing_enabled is False  # cached: keys absent
        assert tracing._last_config_error is None

    def test_span_errors_never_break_execute(self, monkeypatch):
        # Even a completely broken tracer object must not fail the task.
        class BrokenTracer:
            def start_as_current_span(self, *a, **kw):
                raise RuntimeError("tracer exploded")

        monkeypatch.setattr(tracing, "is_tracing_enabled", lambda: True)
        tracing._set_provider(MagicMock())
        tracing._tracer = BrokenTracer()
        gw = _gateway_with(FakeProvider())
        result = asyncio.run(gw.execute(AITaskRequest(task=TaskType.SAGE_CHAT, input="hi")))
        assert result.is_success()


# ---------------------------------------------------------------------------
# Enabled tracing: span shape, attributes, trace_id linkage
# ---------------------------------------------------------------------------

class TestEnabledTracing:
    def test_success_span_attributes_and_trace_id(self, monkeypatch):
        exporter = _enable_in_memory(monkeypatch)
        gw = _gateway_with(
            FakeProvider(usage={"input_tokens": 123, "output_tokens": 45})
        )
        result = asyncio.run(gw.execute(AITaskRequest(task=TaskType.SAGE_CHAT, input="hello")))

        assert result.is_success()
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "gateway.sage_chat"
        attrs = span.attributes
        assert attrs["langfuse.observation.type"] == "generation"
        assert attrs["gen_ai.request.model"] == "fake:cloud"
        assert attrs["gen_ai.response.model"] == "fake:cloud"
        assert attrs["gen_ai.usage.input_tokens"] == 123
        assert attrs["gen_ai.usage.output_tokens"] == 45
        assert attrs["langfuse.observation.input"] == "hello"
        assert attrs["langfuse.observation.output"] == "ok."
        assert attrs["langfuse.observation.metadata.provider"] == "ollama"
        assert attrs["langfuse.observation.metadata.method"] == "llm"
        assert attrs["gen_ai.request.temperature"] == 0.3

        expected = f"{span.get_span_context().trace_id:032x}"
        assert result.provenance.trace_id == expected
        assert len(expected) == 32

    def test_trace_id_flows_to_llm_calls_row(self, monkeypatch):
        exporter = _enable_in_memory(monkeypatch)
        gw = _gateway_with(FakeProvider())
        # record_call fires INSIDE _execute_inner, before the wrapper could
        # stamp anything after the fact — so snapshot trace_id at call time
        # (comparing the same mutated Provenance object later would pass
        # even if the row was logged without it).
        at_call_time = {}
        def _snapshot(prov, **kw):
            at_call_time["trace_id"] = prov.trace_id

        with patch(
            "core.ai_gateway.call_log.record_call", side_effect=_snapshot
        ) as mock_record:
            result = asyncio.run(
                gw.execute(AITaskRequest(task=TaskType.SAGE_CHAT, input="hi"))
            )
        assert mock_record.call_count == 1
        assert at_call_time["trace_id"] == result.provenance.trace_id
        assert at_call_time["trace_id"] is not None

    def test_failure_span_status_error(self, monkeypatch):
        exporter = _enable_in_memory(monkeypatch)
        gw = _gateway_with(FakeProvider(error=RuntimeError("401 unauthorized")))
        result = asyncio.run(gw.execute(AITaskRequest(task=TaskType.SAGE_CHAT, input="hi")))

        assert not result.is_success()
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.status.status_code == StatusCode.ERROR
        assert "unauthorized" in span.attributes["error.message"]
        # The trace is still linked to the failed call's row.
        assert result.provenance.trace_id == f"{span.get_span_context().trace_id:032x}"

    def test_long_input_is_truncated(self, monkeypatch):
        exporter = _enable_in_memory(monkeypatch)
        gw = _gateway_with(FakeProvider())
        result = asyncio.run(
            gw.execute(AITaskRequest(task=TaskType.SAGE_CHAT, input="x" * 5000))
        )
        assert result.is_success()
        span = exporter.get_finished_spans()[0]
        traced = span.attributes["langfuse.observation.input"]
        assert len(traced) == tracing._MAX_TEXT_CHARS + len(tracing._TRUNCATION_MARKER)
        assert traced.endswith(tracing._TRUNCATION_MARKER)

    def test_deterministic_fallback_is_plain_span(self, monkeypatch):
        exporter = _enable_in_memory(monkeypatch)
        # Categorise with no working providers -> deterministic fallback.
        gw = ModelGateway(routing_config={
            "model_registry": {"m1": {"provider": "ollama", "model": "f", "cost_class": "low"}},
            "routing_policies": {
                "categorise": {"primary": "m1", "fallback": [], "deterministic_fallback": True}
            },
        })
        gw.providers = {"m1": FakeProvider(error=RuntimeError("500 boom"))}
        gw.health = {"m1": ModelHealth(provider="ollama", model="f")}
        result = asyncio.run(gw.execute(AITaskRequest(task=TaskType.CATEGORISE, input="Some article text about AI")))

        assert result.is_success()
        assert result.provenance.method == "deterministic"
        span = exporter.get_finished_spans()[0]
        # Not a generation — no model was involved.
        assert "langfuse.observation.type" not in span.attributes


# ---------------------------------------------------------------------------
# Provenance trace_id roundtrip
# ---------------------------------------------------------------------------

class TestProvenanceTraceId:
    def test_roundtrip(self):
        prov = Provenance(task="sage_chat", method="llm", trace_id="a" * 32)
        d = prov.to_dict()
        assert d["trace_id"] == "a" * 32
        assert Provenance.from_dict(d).trace_id == "a" * 32

    def test_default_none(self):
        d = Provenance(task="x", method="llm").to_dict()
        assert d["trace_id"] is None
