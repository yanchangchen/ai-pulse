"""Tests for core/sage_pipeline.py (Phase 7 deep-research orchestrator).

All offline: the gateway is an AsyncMock routed by task type, hybrid_search
is patched, and settings are pinned. The conftest autouse fixtures keep
Supabase/tracing offline.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from core.ai_gateway.contracts import AITaskResult, Provenance, TaskType
from core.sage_pipeline import chat_with_sage_deep, _parse_json_object

_SETTINGS = {
    "sage_deep_research_enabled": True,
    "sage_deep_max_subqueries": 3,
    "sage_deep_max_revisions": 1,
}

_CTX = {
    "context": (
        "=== WIKI CONTEXT (chronological, time-decayed resolution) ===\n"
        "[Sage context: 5 runs across 3 dates]\nblock\n"
        "=== END WIKI CONTEXT ===\n\n"
        "=== ARTICLE EVIDENCE (ranked matches: hybrid retrieval or keyword fallback) ===\n\n"
        "- old evidence\n=== END ARTICLE EVIDENCE ==="
    ),
    "articles": [{"title": "old", "link": "https://old/0", "_date": "2026-05-01",
                  "theme_name": "T", "source_name": "S", "snippet": "old"}],
    "run_count": 5, "date_count": 3, "date_range": "2026-05-01 → 2026-06-30",
    "article_count": 1, "tier3_block_count": 2, "tier3_months": ["2026-05", "2026-06"],
}

_MESSAGES = [{"role": "user", "content": "How did X evolve?"}]


def _hit(link, title="A"):
    return {
        "title": title, "summary": "summary text", "theme_name": "T",
        "source_name": "S", "link": link, "_date": "2026-05-02",
        "_rrf_score": 0.5, "_retrieval": "hybrid", "chunk_text": "chunk",
    }


def _prov(task):
    return Provenance(task=task, method="llm", provider="qwen",
                      model="qwen3.8-flash", trace_id="ab" * 16)


def _ok(text, task):
    return AITaskResult.success(text, _prov(task))


def _fail(task):
    return AITaskResult.failure("boom", Provenance(task=task, method="failed",
                                                   error="boom"))


def _analyst_response(request):
    if "REVISION REQUEST" in request.input:
        return _ok("REVISED ANSWER", "sage_analyst")
    return _ok("DRAFT ANSWER", "sage_analyst")


def _gateway(responses):
    """responses: {task_value: AITaskResult | callable(request) -> result}."""
    gw = MagicMock()

    async def _execute(request):
        r = responses[request.task.value]
        return r(request) if callable(r) else r

    gw.execute = AsyncMock(side_effect=_execute)
    return gw


def _default_responses():
    return {
        "sage_scout": _ok('{"sub_queries": ["q1 entities", "q2 dates"]}', "sage_scout"),
        "sage_analyst": _analyst_response,
        "sage_factcheck": _ok('{"unsupported_claims": []}', "sage_factcheck"),
    }


def _run(responses=None, hits=None, settings=None, ctx=None):
    gw = _gateway(responses or _default_responses())
    hybrid = MagicMock(return_value=hits if hits is not None else [_hit("https://x/1")])
    with patch("core.sage_pipeline.get_gateway", return_value=gw), \
         patch("core.retrieval.hybrid_search", hybrid), \
         patch("config.settings.get_sage_settings", return_value=settings or _SETTINGS):
        out = chat_with_sage_deep(
            _MESSAGES, ctx or _CTX, supabase=MagicMock(),
        )
    return out, gw, hybrid


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def test_parse_json_object_variants():
    assert _parse_json_object('{"a": 1}') == {"a": 1}
    assert _parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json_object('noise {"a": 1} noise') == {"a": 1}
    assert _parse_json_object("no json") is None
    assert _parse_json_object("[1,2]") is None
    assert _parse_json_object("{broken") is None


# ---------------------------------------------------------------------------
# Happy path + metadata
# ---------------------------------------------------------------------------

def test_deep_success_metadata_and_shared_correlation():
    out, gw, hybrid = _run()
    assert out["response"] == "DRAFT ANSWER"
    pipe = out["pipeline"]
    assert pipe["mode"] == "deep"
    assert pipe["sub_queries"] == ["q1 entities", "q2 dates"]
    assert pipe["revised"] is False
    assert pipe["factcheck_ok"] is True
    assert pipe["unsupported_claims"] == []
    stage_names = [s["stage"] for s in pipe["stages"]]
    assert stage_names[0] == "scout"
    assert stage_names.count("retrieval") == 2
    assert "analyst_draft" in stage_names and "factcheck" in stage_names
    # One shared correlation_id across every agent call.
    cids = {c.args[0].correlation_id for c in gw.execute.call_args_list}
    assert len(cids) == 1 and pipe["correlation_id"] in cids
    # Retrieval ran per sub-query.
    assert hybrid.call_count == 2
    assert hybrid.call_args_list[0].args[0] == "q1 entities"
    # Context rebuilt: wiki narrative kept, evidence AUGMENTED — the
    # eval-validated fast-path articles stay first, scout finds append.
    assert "=== END WIKI CONTEXT ===" in out["context"]
    assert "https://x/1" in out["context"]
    assert "https://old/0" in out["context"]  # base evidence preserved
    assert out["articles"][0]["link"] == "https://old/0"
    assert out["provenance"]["model"] == "qwen3.8-flash"


def test_subqueries_capped():
    responses = _default_responses()
    responses["sage_scout"] = _ok(
        '{"sub_queries": ["a", "b", "c", "d", "e"]}', "sage_scout")
    out, _, hybrid = _run(responses=responses)
    assert out["pipeline"]["sub_queries"] == ["a", "b", "c"]
    assert hybrid.call_count == 3


def test_evidence_deduped_by_link():
    hybrid_hits = [_hit("https://x/1"), _hit("https://x/1", title="dup")]
    out, _, _ = _run(hits=hybrid_hits)
    # base (old) + one deduped scout find
    assert len(out["articles"]) == 2
    assert sum(1 for a in out["articles"] if a["link"] == "https://x/1") == 1


def test_base_evidence_survives_scout_finds():
    # Regression: replacing the fast-path evidence regressed faithfulness
    # (gate run 1: 0.950 -> 0.863). The base must always survive.
    out, _, _ = _run(hits=[_hit("https://new/1"), _hit("https://new/2")])
    links = [a["link"] for a in out["articles"]]
    assert links[0] == "https://old/0"
    assert "https://new/1" in links


# ---------------------------------------------------------------------------
# Per-stage degradation
# ---------------------------------------------------------------------------

def test_scout_failure_falls_back_to_question():
    responses = _default_responses()
    responses["sage_scout"] = _fail("sage_scout")
    out, _, hybrid = _run(responses=responses)
    assert out["pipeline"]["sub_queries"] == ["How did X evolve?"]
    assert out["response"] == "DRAFT ANSWER"
    assert hybrid.call_args_list[0].args[0] == "How did X evolve?"


def test_scout_malformed_json_falls_back():
    responses = _default_responses()
    responses["sage_scout"] = _ok("sure, here are queries: a and b", "sage_scout")
    out, _, _ = _run(responses=responses)
    assert out["pipeline"]["sub_queries"] == ["How did X evolve?"]


def test_retrieval_exception_keeps_original_context():
    gw = _gateway(_default_responses())
    hybrid = MagicMock(side_effect=RuntimeError("index down"))
    with patch("core.sage_pipeline.get_gateway", return_value=gw), \
         patch("core.retrieval.hybrid_search", hybrid), \
         patch("config.settings.get_sage_settings", return_value=_SETTINGS):
        out = chat_with_sage_deep(_MESSAGES, _CTX, supabase=MagicMock())
    assert out["context"] == _CTX["context"]
    assert out["articles"] == _CTX["articles"]
    assert out["response"] == "DRAFT ANSWER"


def test_factcheck_flags_trigger_one_bounded_revision():
    responses = _default_responses()
    responses["sage_factcheck"] = _ok(
        '{"unsupported_claims": [{"claim": "X did Y", "reason": "not in context"}]}',
        "sage_factcheck")
    out, gw, _ = _run(responses=responses)
    assert out["response"] == "REVISED ANSWER"
    assert out["pipeline"]["revised"] is True
    assert len(out["pipeline"]["unsupported_claims"]) == 1
    analyst_calls = [c for c in gw.execute.call_args_list
                     if c.args[0].task == TaskType.SAGE_ANALYST]
    assert len(analyst_calls) == 2  # draft + exactly one revision
    assert "REVISION REQUEST" in analyst_calls[1].args[0].input
    assert "X did Y" in analyst_calls[1].args[0].input


def test_revision_failure_keeps_draft():
    responses = _default_responses()
    responses["sage_factcheck"] = _ok(
        '{"unsupported_claims": [{"claim": "bad", "reason": "r"}]}', "sage_factcheck")

    def _analyst(request):
        if "REVISION REQUEST" in request.input:
            return _fail("sage_analyst")
        return _ok("DRAFT ANSWER", "sage_analyst")

    responses["sage_analyst"] = _analyst
    out, _, _ = _run(responses=responses)
    assert out["response"] == "DRAFT ANSWER"
    assert out["pipeline"]["revised"] is False


def test_factcheck_outage_publishes_draft_and_records_it():
    responses = _default_responses()
    responses["sage_factcheck"] = _fail("sage_factcheck")
    out, _, _ = _run(responses=responses)
    assert out["response"] == "DRAFT ANSWER"
    assert out["pipeline"]["factcheck_ok"] is False
    assert out["pipeline"]["revised"] is False


def test_factcheck_unparseable_is_not_a_clean_check():
    responses = _default_responses()
    responses["sage_factcheck"] = _ok("I think it is fine.", "sage_factcheck")
    out, _, _ = _run(responses=responses)
    assert out["pipeline"]["factcheck_ok"] is False
    assert out["response"] == "DRAFT ANSWER"


def test_analyst_failure_surfaces_notice():
    responses = _default_responses()
    responses["sage_analyst"] = _fail("sage_analyst")
    out, _, _ = _run(responses=responses)
    assert "Sage Notice" in out["response"] or "trouble connecting" in out["response"]
    assert out["provenance"] is not None
    assert out["pipeline"]["mode"] == "deep"


def test_kill_switch_delegates_to_fast_path():
    settings = dict(_SETTINGS, sage_deep_research_enabled=False)
    with patch("core.sage_pipeline.chat_with_sage",
               return_value={"response": "fast", "provenance": None}) as fast, \
         patch("config.settings.get_sage_settings", return_value=settings):
        out = chat_with_sage_deep(_MESSAGES, _CTX, supabase=MagicMock())
    fast.assert_called_once()
    assert out["response"] == "fast"
    assert out["pipeline"] == {"mode": "fast", "reason": "kill_switch"}


def test_agent_tasks_and_no_deterministic_fallback():
    out, gw, _ = _run()
    tasks = {c.args[0].task for c in gw.execute.call_args_list}
    assert tasks == {TaskType.SAGE_SCOUT, TaskType.SAGE_ANALYST, TaskType.SAGE_FACTCHECK}
    for c in gw.execute.call_args_list:
        assert c.args[0].allow_deterministic_fallback is False