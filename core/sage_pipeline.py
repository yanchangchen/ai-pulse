"""Sage deep-research pipeline (Phase 7 — multi-agent synthesis, Pillar E).

Three agents answer one question, every call traced as a nested span under
a single ``sage.pipeline`` parent (one Langfuse trace per reply) and grouped
in ``llm_calls`` by one shared correlation_id:

    Scout (sage_scout)        question + scope -> 2-3 focused sub-queries
      -> hybrid retrieval per sub-query (span: sage.retrieval), merged/deduped
    Analyst (sage_analyst)    persona + assembled context -> draft answer
    Fact-Checker (sage_factcheck)  draft vs context -> unsupported claims
    Analyst revision          bounded (1): cite-or-drop the flagged claims

Degradation is per-stage, never pipeline-fatal: Scout failure falls back to
the original question as the single sub-query; retrieval failures keep the
fast-path evidence; a Fact-Checker outage publishes the draft (an outage
must not block answers, and must not masquerade as a clean check — the
pipeline metadata records it); a failed revision keeps the draft.  Only an
Analyst failure surfaces a notice string, exactly like the fast path.

The fast path (``core.sage_agent.chat_with_sage``) is untouched; the UI
toggle chooses between them per session.  See docs/SAGE.md.
"""

import asyncio
import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional

from core.ai_gateway.contracts import AITaskRequest, TaskType
from core.ai_gateway.gateway import get_gateway
from core.ai_gateway.tracing import pipeline_span
from core.logger import setup_logger
from core.sage_agent import (
    ARTICLE_BUDGET_SHARE,
    ARTICLE_TOP_K,
    SAGE_SYSTEM_PROMPT,
    _all_quota_failures,
    _ENGINE_UNAVAILABLE_MSG,
    _render_article_section,
    _short_failure,
    chat_with_sage,
)

logger = setup_logger(__name__)

_WIKI_END_MARKER = "=== END WIKI CONTEXT ==="
_DEFAULT_MAX_CHARS = 40_000

_SCOUT_SYSTEM = (
    "You are Sage Scout, a retrieval planner for an AI-news intelligence archive. "
    "Given a user question and the archive scope, produce 2-3 focused search queries "
    "that together cover the question's facets (entities, dates, comparisons). "
    "Each query must be self-contained: name the entities and date ranges explicitly. "
    'Output ONLY a JSON object: {"sub_queries": ["...", "..."]}. No commentary.'
)

_FACTCHECK_SYSTEM = (
    "You are Sage Fact-Checker. Validate EVERY factual statement in the DRAFT answer "
    "against the CONTEXT it was grounded in. A statement is unsupported when the "
    "context does not explicitly contain it (names, dates, numbers, causal claims) — "
    "AND when it exaggerates, generalizes, or adds framing (causal, diplomatic, "
    "strategic) beyond what the context explicitly states. An article existing in "
    "the context does not support claims that go beyond its text. "
    "Judge ONLY against the context — never use outside knowledge. "
    'Output ONLY a JSON object: {"unsupported_claims": [{"claim": "...", "reason": "..."}]}. '
    "Return an empty list when the draft is fully supported. No commentary."
)


def _last_user_question(messages: List[Dict[str, str]]) -> str:
    for msg in reversed(messages or []):
        if msg.get("role") == "user":
            return str(msg.get("content", ""))
    return ""


def _parse_json_object(text: str) -> Optional[Dict]:
    """Defensive JSON extraction: fenced block, then first-{…last-} slice."""
    text = str(text or "")
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fence.group(1) if fence else None
    if candidate is None:
        first, last = text.find("{"), text.rfind("}")
        if 0 <= first < last:
            candidate = text[first:last + 1]
    if not candidate:
        return None
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, TypeError):
        return None


def _conversation_block(messages: List[Dict[str, str]]) -> str:
    parts = []
    for msg in messages or []:
        role = "User" if msg.get("role") == "user" else "Sage"
        parts.append(f"{role}: {msg.get('content', '')}")
    return "\n\n".join(parts) + "\n\nSage:"


async def _exec_agent(
    task: TaskType, prompt: str, temperature: float, max_tokens: int,
    correlation_id: str, stage: str,
):
    """One gateway call; returns (result, latency_ms). Result None on error."""
    request = AITaskRequest(
        task=task,
        input=prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        allow_deterministic_fallback=False,
        correlation_id=correlation_id,
        metadata={"caller": "sage_pipeline", "stage": stage},
    )
    t0 = time.perf_counter()
    try:
        result = await get_gateway().execute(request)
    except Exception as exc:
        logger.warning("Sage pipeline stage %s failed: %s", stage, exc)
        return None, int((time.perf_counter() - t0) * 1000)
    return result, int((time.perf_counter() - t0) * 1000)


def _failure_notice(result) -> Dict[str, Any]:
    """Mirror chat_with_sage's failure notices for a failed Analyst call."""
    prov = result.provenance
    error = prov.error or str(result.result)
    reasons = prov.failure_reasons or {}
    if _all_quota_failures(reasons) or (
        not reasons and ("429" in error or "quota" in error.lower())
    ):
        response = (
            "⚠️ *Sage Notice: All engines are quota-limited right now (HTTP 429). "
            "Please check API quotas or try again later.*"
        )
    elif reasons:
        details = "; ".join(f"{m}: {_short_failure(r)}" for m, r in reasons.items())
        response = f"⚠️ *Sage Notice: All engines failed.* {details[:500]}. Please try again in a moment."
    else:
        response = _ENGINE_UNAVAILABLE_MSG
    return {"response": response, "provenance": prov.to_dict()}


def _merge_evidence(
    base_articles: List[Dict], per_query: List[List[Dict]], budget: int,
) -> tuple:
    """AUGMENT the fast-path evidence with sub-query hits — never replace it.

    The fast path's milestone-biased evidence is eval-validated grounding;
    replacing it with sub-query hits regressed faithfulness (first Phase 7
    gate run: 0.950 -> 0.863, embellished narratives around new articles).
    Base articles come first (already ranked), scout finds append until the
    budget/TOP_K caps. Dedupe by link (else title+date). Base rows are the
    trimmed render dicts (``snippet``), so map snippet back to ``summary``
    for the renderer. Returns (section_text, rendered_list).
    """
    seen = set()
    merged: List[Dict] = []
    base = [
        {**a, "summary": a.get("summary") or a.get("snippet", "")}
        for a in (base_articles or [])
    ]
    for a in base + [a for hits in per_query for a in (hits or [])]:
        key = a.get("link") or (a.get("title", ""), a.get("_date", ""))
        if key in seen:
            continue
        seen.add(key)
        merged.append(a)
    # Allow base + scout finds; the char budget is the real trimmer.
    return _render_article_section(merged[:ARTICLE_TOP_K * 2], budget)


def chat_with_sage_deep(
    messages: List[Dict[str, str]],
    wiki_context,
    supabase=None,
    theme_filter: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    source_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the 3-agent deep-research pipeline; returns the fast-path contract
    (``response`` / ``provenance``) plus ``pipeline`` metadata and ``articles``
    (the merged evidence list for the UI popover).

    ``wiki_context`` is the dict from ``build_wiki_context`` (the tier-aware
    narrative is reused as-is; only the Tier-1 evidence section is rebuilt
    from the Scout's sub-queries).
    """
    from config.settings import get_sage_settings
    settings = get_sage_settings()
    question = _last_user_question(messages)

    if not settings.get("sage_deep_research_enabled", True):
        out = chat_with_sage(messages, wiki_context)
        out["pipeline"] = {"mode": "fast", "reason": "kill_switch"}
        return out

    ctx = wiki_context if isinstance(wiki_context, dict) else {"context": str(wiki_context)}
    context_str = ctx.get("context", "")
    correlation_id = str(uuid.uuid4())[:8]
    max_subqueries = int(settings.get("sage_deep_max_subqueries", 3))
    max_revisions = int(settings.get("sage_deep_max_revisions", 1))
    stages: List[Dict] = []
    sub_queries: List[str] = []
    claims: List[Dict] = []
    factcheck_ok = True
    revised = False
    articles = ctx.get("articles") or []

    with pipeline_span("sage.pipeline", {
        "sage.mode": "deep",
        "sage.question": question[:200],
        "sage.correlation_id": correlation_id,
    }) as parent_span:
        # ---- Stage 1: Scout — sub-query generation ----
        scope_bits = []
        if ctx.get("date_range"):
            scope_bits.append(f"archive window: {ctx['date_range']}")
        if ctx.get("tier3_months"):
            scope_bits.append(f"monthly rollups available: {', '.join(ctx['tier3_months'])}")
        if theme_filter:
            scope_bits.append(f"theme focus: {theme_filter}")
        scope = "; ".join(scope_bits) or "full archive"
        scout_prompt = (
            f"System: {_SCOUT_SYSTEM}\n\nUser: QUESTION: {question}\n"
            f"SCOPE: {scope}\n"
            f"Produce {max_subqueries} or fewer sub-queries as JSON."
        )
        result, latency = asyncio.run(_exec_agent(
            TaskType.SAGE_SCOUT, scout_prompt, 0.3, 400, correlation_id, "scout",
        ))
        scout_ok = bool(result and result.is_success() and result.provenance.method == "llm")
        if scout_ok:
            parsed = _parse_json_object(str(result.result))
            sq = (parsed or {}).get("sub_queries")
            if isinstance(sq, list):
                sub_queries = [str(s).strip() for s in sq if str(s).strip()][:max_subqueries]
        if not sub_queries:
            sub_queries = [question]  # degradation: the question itself
        stages.append({"stage": "scout", "ok": scout_ok, "latency_ms": latency,
                       "model": result.provenance.model if result else None})

        # ---- Stage 2: retrieval per sub-query (nested spans) ----
        per_query: List[List[Dict]] = []
        if supabase is not None:
            from core.retrieval import hybrid_search
            for sq in sub_queries:
                with pipeline_span("sage.retrieval", {"sage.sub_query": sq[:200]}):
                    t0 = time.perf_counter()
                    try:
                        hits = hybrid_search(
                            sq, supabase=supabase, theme_filter=theme_filter,
                            date_from=date_from, date_to=date_to,
                            source_filter=source_filter, top_k=ARTICLE_TOP_K,
                        )
                    except Exception as exc:
                        logger.warning("Deep retrieval failed for %r: %s", sq, exc)
                        hits = []
                    per_query.append(hits or [])
                    stages.append({"stage": "retrieval", "ok": bool(hits),
                                   "latency_ms": int((time.perf_counter() - t0) * 1000),
                                   "sub_query": sq, "hits": len(hits or [])})
        if any(per_query):
            section, rendered = _merge_evidence(
                ctx.get("articles") or [], per_query,
                int(_DEFAULT_MAX_CHARS * ARTICLE_BUDGET_SHARE),
            )
            if section:
                # Rebuild the context: keep the tier-aware wiki narrative,
                # swap the evidence section for the merged sub-query hits.
                wiki_part = context_str.split(_WIKI_END_MARKER)[0] + _WIKI_END_MARKER
                context_str = wiki_part + "\n\n" + section
                articles = rendered

        # ---- Stage 3: Analyst draft ----
        # Phase 8: same improvement-loop prompt additions as the fast path
        # (exemplars + guard mandate), so both modes learn identically.
        persona = SAGE_SYSTEM_PROMPT
        if settings.get("sage_strict_grounding"):
            persona += (
                "\n- STRICT GROUNDING MANDATE (auto-engaged: recent faithfulness dipped): "
                "every claim must be explicitly supported by the context below — cite it "
                "or drop it. Do NOT extrapolate, generalize, or add framing the sources "
                "do not state."
            )
        from core.sage_exemplars import build_exemplar_block
        exemplars = build_exemplar_block() if settings.get("sage_exemplars_enabled", True) else ""
        exemplar_block = (exemplars + "\n\n") if exemplars else ""
        analyst_prompt = (
            f"{persona}\n\n{exemplar_block}{context_str}\n\n---\n\n"
            f"{_conversation_block(messages)}"
        )
        _draft_temp = 0.4
        if settings.get("sage_strict_grounding"):
            from core.sage_guard import TARGET_TEMP
            _draft_temp = min(_draft_temp, TARGET_TEMP)
        result, latency = asyncio.run(_exec_agent(
            TaskType.SAGE_ANALYST, analyst_prompt, _draft_temp, 3000, correlation_id, "analyst_draft",
        ))
        if result is None:
            return {"response": _ENGINE_UNAVAILABLE_MSG, "provenance": None,
                    "pipeline": {"mode": "deep", "stages": stages,
                                 "correlation_id": correlation_id}}
        if not (result.is_success() and isinstance(result.result, str) and result.result.strip()):
            out = _failure_notice(result)
            out["pipeline"] = {"mode": "deep", "stages": stages,
                               "correlation_id": correlation_id}
            return out
        draft = result.result.strip()
        final_prov = result.provenance
        stages.append({"stage": "analyst_draft", "ok": True, "latency_ms": latency,
                       "model": result.provenance.model})

        # ---- Stage 4: Fact-Checker ----
        fc_prompt = (
            f"System: {_FACTCHECK_SYSTEM}\n\nUser: --- CONTEXT ---\n{context_str}\n"
            f"--- END CONTEXT ---\n\n--- DRAFT ---\n{draft}\n--- END DRAFT ---"
        )
        fc_result, fc_latency = asyncio.run(_exec_agent(
            TaskType.SAGE_FACTCHECK, fc_prompt, 0.1, 900, correlation_id, "factcheck",
        ))
        fc_ok = bool(fc_result and fc_result.is_success()
                     and fc_result.provenance.method == "llm")
        if fc_ok:
            parsed = _parse_json_object(str(fc_result.result))
            raw_claims = (parsed or {}).get("unsupported_claims")
            if isinstance(raw_claims, list):
                claims = [
                    {"claim": str(c.get("claim", "")), "reason": str(c.get("reason", ""))}
                    for c in raw_claims if isinstance(c, dict) and c.get("claim")
                ]
            elif parsed is None:
                factcheck_ok = False  # unparseable verdict — recorded, not hidden
        else:
            factcheck_ok = False  # checker outage — publish draft, record it
        stages.append({"stage": "factcheck", "ok": fc_ok, "latency_ms": fc_latency,
                       "model": fc_result.provenance.model if fc_result else None,
                       "unsupported_claims": len(claims)})

        # ---- Stage 5: bounded revision (cite-or-drop) ----
        final = draft
        if claims and max_revisions > 0:
            claims_text = "\n".join(
                f"- {c['claim']} (reason: {c['reason']})" for c in claims
            )
            revision_prompt = (
                f"{analyst_prompt}\n\n{draft}\n\n"
                "--- REVISION REQUEST ---\n"
                "The fact-checker flagged these statements as unsupported by the context:\n"
                f"{claims_text}\n"
                "Rewrite the FULL answer in the same format. For EACH flagged statement: "
                "DROP IT ENTIRELY unless the context explicitly supports it — do not "
                "rephrase, soften, or generalize a flagged claim into a new assertion. "
                "Keep every unflagged, supported claim with its citation. "
                "Never invent facts, dates, or URLs."
            )
            rev_result, rev_latency = asyncio.run(_exec_agent(
                TaskType.SAGE_ANALYST, revision_prompt, 0.3, 3000,
                correlation_id, "analyst_revision",
            ))
            rev_ok = bool(rev_result and rev_result.is_success()
                          and isinstance(rev_result.result, str) and rev_result.result.strip())
            if rev_ok:
                final = rev_result.result.strip()
                final_prov = rev_result.provenance
                revised = True
            stages.append({"stage": "analyst_revision", "ok": rev_ok,
                           "latency_ms": rev_latency,
                           "model": rev_result.provenance.model if rev_result else None})

        parent_span.set_attribute("sage.sub_queries", json.dumps(sub_queries)[:500])
        parent_span.set_attribute("sage.unsupported_claims", len(claims))
        parent_span.set_attribute("sage.revised", revised)
        parent_span.set_attribute("sage.factcheck_ok", factcheck_ok)

    return {
        "response": final,
        "provenance": final_prov.to_dict(),
        "articles": articles,
        # The exact assembled context the final answer was grounded in —
        # the chat-time faithfulness judge must see THIS, not the fast-path
        # context (the evidence section was rebuilt from sub-queries).
        "context": context_str,
        "pipeline": {
            "mode": "deep",
            "sub_queries": sub_queries,
            "unsupported_claims": claims,
            "factcheck_ok": factcheck_ok,
            "revised": revised,
            "stages": stages,
            "correlation_id": correlation_id,
            "trace_id": final_prov.trace_id,
        },
    }