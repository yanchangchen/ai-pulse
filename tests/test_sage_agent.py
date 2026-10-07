"""Tests for the Sage agent module — relevance scoring, first-appearance
annotation, context building, and gateway-routed chat."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.ai_gateway.contracts import AITaskResult, Provenance, TaskType
from core.sage_agent import (
    _tokenise,
    score_summary_relevance,
    annotate_first_appearances,
    build_wiki_context,
    chat_with_sage,
    SAGE_SYSTEM_PROMPT,
    SAGE_INTRO,
)


def _gateway_result(
    status: str = "success",
    payload=None,
    error: str = None,
    fallback_used: bool = False,
) -> AITaskResult:
    """Build an AITaskResult shaped like a sage_chat gateway response."""
    provenance = Provenance(
        task="sage_chat",
        method="llm" if status == "success" else "failed",
        provider="ollama",
        model="nemotron-3-ultra:cloud",
        latency_ms=1234,
        fallback_used=fallback_used,
        error=error,
    )
    if status == "success":
        return AITaskResult.success(payload, provenance)
    return AITaskResult.failure(error or "boom", provenance)


def _mock_gateway(result: AITaskResult) -> MagicMock:
    """A gateway stand-in whose execute() is an async mock returning ``result``."""
    gateway = MagicMock()
    gateway.execute = AsyncMock(return_value=result)
    return gateway


# ---------------------------------------------------------------------------
# Tokeniser
# ---------------------------------------------------------------------------

class TestTokenise:
    def test_basic_tokenisation(self):
        tokens = _tokenise("What are the biggest trends in agentic AI?")
        assert "biggest" in tokens
        assert "trends" in tokens
        assert "agentic" in tokens

    def test_stopwords_removed(self):
        tokens = _tokenise("the is a an for with")
        assert tokens == []

    def test_short_words_removed(self):
        tokens = _tokenise("AI is ok but MCP is key")
        # 'ok' has len 2, 'is' is stopword, 'ai' has len 2
        assert "mcp" in tokens
        assert "key" in tokens

    def test_empty_string(self):
        assert _tokenise("") == []


# ---------------------------------------------------------------------------
# Relevance scoring
# ---------------------------------------------------------------------------

class TestScoreSummaryRelevance:
    def test_high_overlap(self):
        block = {
            "what_is_happening": "Agentic systems are being adopted widely.",
            "why_it_matters": "Agents automate complex workflows.",
            "what_to_watch": "Watch for agentic framework consolidation.",
            "theme_name": "Agentic Systems",
        }
        kws = _tokenise("What are the latest agentic system trends?")
        score = score_summary_relevance(block, kws)
        assert score > 0

    def test_zero_overlap(self):
        block = {
            "what_is_happening": "Quantum computing advances.",
            "why_it_matters": "Qubits are more stable.",
            "what_to_watch": "Error correction.",
            "theme_name": "Quantum",
        }
        kws = _tokenise("agentic system trends")
        score = score_summary_relevance(block, kws)
        assert score == 0.0

    def test_empty_keywords(self):
        block = {"what_is_happening": "anything", "why_it_matters": "", "what_to_watch": "", "theme_name": ""}
        assert score_summary_relevance(block, []) == 0.0


# ---------------------------------------------------------------------------
# First-appearance annotation
# ---------------------------------------------------------------------------

class TestAnnotateFirstAppearances:
    def test_flags_first_and_subsequent(self):
        summaries = [
            {"theme_name": "AI Models", "run_timestamp": "2025-07-01"},
            {"theme_name": "AI Models", "run_timestamp": "2025-07-08"},
            {"theme_name": "Agentic Systems", "run_timestamp": "2025-07-08"},
            {"theme_name": "AI Models", "run_timestamp": "2025-07-15"},
        ]
        result = annotate_first_appearances(summaries)
        assert result[0]["_first_appearance"] is True   # AI Models first
        assert result[1]["_first_appearance"] is False   # AI Models second
        assert result[2]["_first_appearance"] is True    # Agentic first
        assert result[3]["_first_appearance"] is False   # AI Models third

    def test_empty_list(self):
        assert annotate_first_appearances([]) == []

    def test_single_entry(self):
        result = annotate_first_appearances([{"theme_name": "X", "run_timestamp": "2025-01-01"}])
        assert result[0]["_first_appearance"] is True


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

class TestBuildWikiContext:
    def test_local_fallback_produces_context(self):
        """When Supabase is unavailable, local history is used."""
        mock_supabase = MagicMock()
        mock_supabase.is_available.return_value = False

        history = {
            "2025-07-01 10:00:00": {
                "date": "2025-07-01",
                "summaries": {
                    "AI Models": {
                        "what_is_happening": "GPT-5 announced.",
                        "why_it_matters": "Major capability jump.",
                        "what_to_watch": "Benchmark results.",
                    }
                },
                "counts": {"AI Models": 5},
            }
        }

        result = build_wiki_context(
            supabase=mock_supabase,
            question="What happened with AI models?",
            history=history,
        )
        context = result["context"]

        assert "GPT-5 announced" in context
        assert "WIKI CONTEXT" in context
        assert "Run 2025-07-01" in context

    def test_empty_history_returns_no_data_message(self):
        mock_supabase = MagicMock()
        mock_supabase.is_available.return_value = False

        result = build_wiki_context(
            supabase=mock_supabase,
            question="anything",
            history={},
        )
        assert "No wiki data" in result["context"]
        assert result["run_count"] == 0
        assert result["date_count"] == 0

    def test_max_chars_respected(self):
        mock_supabase = MagicMock()
        mock_supabase.is_available.return_value = False

        # Create a history with many long summaries
        history = {}
        for i in range(50):
            ts = f"2025-07-{i+1:02d} 10:00:00"
            history[ts] = {
                "date": f"2025-07-{i+1:02d}",
                "summaries": {
                    "AI Models": {
                        "what_is_happening": "X " * 200,
                        "why_it_matters": "Y " * 200,
                        "what_to_watch": "Z " * 200,
                    }
                },
                "counts": {"AI Models": 10},
            }

        result = build_wiki_context(
            supabase=mock_supabase,
            question="anything",
            history=history,
            max_chars=2000,
        )
        # Allow slack for the metadata wrapper lines.
        assert len(result["context"]) <= 3000

    def test_supabase_path(self):
        """When Supabase returns data, it is used."""
        mock_supabase = MagicMock()
        mock_supabase.is_available.return_value = True
        mock_supabase.get_summaries_across_runs.return_value = [
            {
                "run_id": "abc",
                "run_timestamp": "2025-07-01 10:00:00",
                "run_date": "2025-07-01",
                "theme_name": "Agentic Systems",
                "what_is_happening": "Agent frameworks emerging.",
                "why_it_matters": "Automation potential.",
                "what_to_watch": "LangGraph vs CrewAI.",
                "article_count": 8,
            }
        ]

        result = build_wiki_context(
            supabase=mock_supabase,
            question="agentic systems trends",
        )

        assert "Agent frameworks emerging" in result["context"]
        assert "Agentic Systems" in result["context"]
        assert result["run_count"] == 1
        assert result["date_count"] == 1

    def test_return_dict_shape(self):
        """build_wiki_context returns a dict with all 4 expected keys."""
        mock_supabase = MagicMock()
        mock_supabase.is_available.return_value = True
        mock_supabase.get_summaries_across_runs.return_value = [
            {
                "run_id": "x",
                "run_timestamp": "2025-07-01 10:00:00",
                "run_date": "2025-07-01",
                "theme_name": "AI Models",
                "what_is_happening": "Something happened.",
                "why_it_matters": "It matters.",
                "what_to_watch": "Watch this.",
                "article_count": 3,
            }
        ]

        result = build_wiki_context(supabase=mock_supabase, question="anything")

        assert isinstance(result, dict)
        assert set(result.keys()) == {
            "context", "run_count", "date_count", "date_range", "article_count",
        }
        assert isinstance(result["context"], str)
        assert isinstance(result["run_count"], int)
        assert isinstance(result["date_count"], int)
        assert isinstance(result["date_range"], str)
        assert isinstance(result["article_count"], int)

    def test_all_dates_in_window_are_represented(self):
        """With many dates and large blocks, every date must still appear in
        the context — older dates via condensed blocks, not dropped entirely."""
        mock_supabase = MagicMock()
        mock_supabase.is_available.return_value = True

        summaries = []
        for i in range(30):
            date = f"2025-07-{i+1:02d}"
            summaries.append({
                "run_id": f"r{i}",
                "run_timestamp": f"{date} 10:00:00",
                "run_date": date,
                "theme_name": "AI Models",
                "what_is_happening": ("A very long sentence. " * 20),
                "why_it_matters": ("Significance. " * 20),
                "what_to_watch": ("Watchlist item. " * 20),
                "article_count": 10,
            })
        mock_supabase.get_summaries_across_runs.return_value = summaries

        result = build_wiki_context(
            supabase=mock_supabase,
            question="AI models",
            max_chars=40_000,
        )

        # Every date from the 30-day window must be mentioned in the context.
        context = result["context"]
        for i in range(30):
            date = f"2025-07-{i+1:02d}"
            assert date in context, f"Date {date} missing from context"
        assert result["date_count"] == 30
        assert result["run_count"] == 30

    def test_recent_runs_get_full_blocks_older_get_condensed(self):
        """Recent-tier dates (last 2) use full 3-field blocks; older-tier
        dates use condensed (only what_is_happening, truncated)."""
        mock_supabase = MagicMock()
        mock_supabase.is_available.return_value = True

        summaries = []
        for i in range(10):
            date = f"2025-08-{i+1:02d}"
            summaries.append({
                "run_id": f"r{i}",
                "run_timestamp": f"{date} 10:00:00",
                "run_date": date,
                "theme_name": "AI Models",
                "what_is_happening": ("Event " + date + ". ") * 5,
                "why_it_matters": ("Significance " + date + ". ") * 5,
                "what_to_watch": ("Watch " + date + ". ") * 5,
                "article_count": 10,
            })
        mock_supabase.get_summaries_across_runs.return_value = summaries

        result = build_wiki_context(
            supabase=mock_supabase,
            question="AI models",
            max_chars=40_000,
        )
        context = result["context"]

        # Full blocks have "Significance:" label; condensed blocks do not.
        # RECENT_DETAIL_COUNT=2 → exactly 2 Significance lines.
        assert context.count("Significance:") == 2
        # Condensed blocks carry the ", condensed)" tag → 8 for older dates.
        assert context.count(", condensed)") == 8

    def test_condense_text_truncates_at_sentence_boundary(self):
        from core.sage_agent import _condense_text

        long_text = "First sentence. " * 100  # very long
        condensed = _condense_text(long_text, limit=100)
        assert len(condensed) <= 101  # up to 100 chars + ellipsis
        assert condensed.endswith("…")
        # Must not break mid-word at the truncation point.
        assert "…" in condensed

    def test_condense_text_short_text_unchanged(self):
        from core.sage_agent import _condense_text

        short = "Hello world."
        assert _condense_text(short, limit=100) == short


# ---------------------------------------------------------------------------
# Tier-1 article evidence (Phase 2.1)
# ---------------------------------------------------------------------------

def _article_row(
    title="Article title",
    summary="Some article summary.",
    theme="Frontier Models & Benchmarks",
    source="OpenAI Blog",
    published="2025-07-01T10:00:00",
    link="https://example.com/a",
):
    """A row shaped like Supabase articles / history themed_articles entries."""
    return {
        "run_id": "r1",
        "theme_name": theme,
        "title": title,
        "summary": summary,
        "source_name": source,
        "link": link,
        "published_at": published,
    }


def _summary_row(date="2025-07-01", theme="AI Models"):
    return {
        "run_id": "r1",
        "run_timestamp": f"{date} 10:00:00",
        "run_date": date,
        "theme_name": theme,
        "what_is_happening": "Models keep improving.",
        "why_it_matters": "Capability jumps matter.",
        "what_to_watch": "Benchmarks.",
        "article_count": 4,
    }


def _mock_supabase(articles=None, summaries=None):
    """Supabase stand-in returning explicit lists (or None when unset)."""
    mock = MagicMock()
    mock.is_available.return_value = True
    mock.get_summaries_across_runs.return_value = summaries
    mock.get_articles_for_context.return_value = articles
    return mock


class TestArticleContext:
    def test_article_section_rendered_after_wiki(self):
        articles = [_article_row(
            title="Claude 4.5 Opus released",
            summary="Anthropic ships Claude 4.5 Opus with major gains.",
        )]
        supabase = _mock_supabase(articles=articles, summaries=[_summary_row()])

        result = build_wiki_context(
            supabase=supabase, question="What happened with Claude releases?"
        )

        context = result["context"]
        assert "WIKI CONTEXT" in context
        assert "=== ARTICLE EVIDENCE" in context
        assert "Claude 4.5 Opus released" in context
        assert "[Frontier Models & Benchmarks · OpenAI Blog · 2025-07-01]" in context
        assert "Anthropic ships Claude" in context
        # Wiki narrative first, Tier-1 evidence after.
        assert context.index("=== END WIKI CONTEXT ===") < context.index("=== ARTICLE EVIDENCE")
        assert result["article_count"] == 1

    def test_keyword_match_ranks_above_non_match(self):
        articles = [
            _article_row(title="Unrelated storage story", summary="Disks got cheaper."),
            _article_row(title="Claude 4.5 Opus released", summary="Anthropic ships Claude."),
        ]
        supabase = _mock_supabase(articles=articles, summaries=[_summary_row()])

        result = build_wiki_context(
            supabase=supabase, question="What happened with Claude releases?"
        )

        context = result["context"]
        assert context.index("Claude 4.5 Opus released") < context.index("Unrelated storage story")

    def test_top_k_cap(self):
        articles = [
            _article_row(title=f"Article {i}", summary=f"Summary {i} claude release.", published=f"2025-07-{i+1:02d}T09:00:00")
            for i in range(30)
        ]
        supabase = _mock_supabase(articles=articles, summaries=[_summary_row()])

        result = build_wiki_context(supabase=supabase, question="claude release")

        from core.sage_agent import ARTICLE_TOP_K
        assert result["article_count"] == ARTICLE_TOP_K

    def test_no_keyword_match_falls_back_to_newest(self):
        # Question tokens match nothing → selection degrades to newest-first.
        articles = [
            _article_row(title=f"Story {i}", summary="filler text.", published=f"2025-07-{i+1:02d}T09:00:00")
            for i in range(5)
        ]
        supabase = _mock_supabase(articles=articles, summaries=[_summary_row()])

        result = build_wiki_context(supabase=supabase, question="zzz qqq")

        context = result["context"]
        assert result["article_count"] == 5
        # Within the article section, newest (2025-07-05) renders before the
        # oldest (2025-07-01).  (Compare inside the section: the wiki part
        # above also mentions dates.)
        section = context.split("=== ARTICLE EVIDENCE", 1)[1]
        assert section.index("2025-07-05") < section.index("2025-07-01")

    def test_article_budget_is_capped(self):
        from core.sage_agent import _render_article_section, ARTICLE_BUDGET_SHARE

        articles = [
            _article_row(title=f"Article {i}", summary="word " * 500, published=f"2025-07-{i+1:02d}T09:00:00")
            for i in range(20)
        ]
        section, rendered = _render_article_section(articles, int(10_000 * ARTICLE_BUDGET_SHARE))

        assert len(section) <= int(10_000 * ARTICLE_BUDGET_SHARE)
        assert rendered < 20  # budget stopped the render before all 20

    def test_total_budget_respected_with_articles(self):
        articles = [
            _article_row(title=f"Article {i}", summary="word " * 300, published=f"2025-07-{i+1:02d}T09:00:00")
            for i in range(20)
        ]
        summaries = [_summary_row(date=f"2025-06-{i+1:02d}") for i in range(20)]
        supabase = _mock_supabase(articles=articles, summaries=summaries)

        result = build_wiki_context(supabase=supabase, question="claude", max_chars=2000)
        # Allow the same metadata slack as test_max_chars_respected.
        assert len(result["context"]) <= 3000

    def test_non_list_supabase_return_is_ignored(self):
        # A bare MagicMock (default return) must never crash the builder or
        # leak an article section — this is what keeps every pre-2.1 test
        # green once real keys/rows exist.
        mock = MagicMock()
        mock.is_available.return_value = True
        mock.get_summaries_across_runs.return_value = [_summary_row()]
        mock.get_articles_for_context.return_value = MagicMock()  # not a list

        # history={} also pins the local article fallback off, so the real
        # history.json on this machine cannot leak in.
        result = build_wiki_context(supabase=mock, question="anything", history={})

        assert "ARTICLE EVIDENCE" not in result["context"]
        assert result["article_count"] == 0

    def test_local_fallback_uses_themed_articles(self):
        mock_supabase = MagicMock()
        mock_supabase.is_available.return_value = False

        history = {
            "2025-07-01 10:00:00": {
                "date": "2025-07-01",
                "summaries": {
                    "AI Models": {
                        "what_is_happening": "GPT-5 announced.",
                        "why_it_matters": "Major capability jump.",
                        "what_to_watch": "Benchmark results.",
                    }
                },
                "counts": {"AI Models": 5},
                "themed_articles": {
                    "AI Models": [
                        {
                            "title": "GPT-5 launch coverage",
                            "summary": "OpenAI launched GPT-5 today.",
                            "source_name": "OpenAI Blog",
                            "link": "https://example.com/gpt5",
                            # No published date → falls back to the run date.
                        }
                    ]
                },
            }
        }

        result = build_wiki_context(
            supabase=mock_supabase, question="What happened with GPT-5?", history=history
        )

        context = result["context"]
        assert "=== ARTICLE EVIDENCE" in context
        assert "GPT-5 launch coverage" in context
        assert "[AI Models · OpenAI Blog · 2025-07-01]" in context
        assert result["article_count"] == 1

    def test_summaries_empty_articles_present(self):
        # history={} pins the local fallback off so the real history.json on
        # this machine can never leak into the test.
        supabase = _mock_supabase(articles=[_article_row()], summaries=None)

        result = build_wiki_context(
            supabase=supabase, question="claude release", history={}
        )

        context = result["context"]
        assert "ARTICLE EVIDENCE" in context
        assert "WIKI CONTEXT" not in context
        assert result["run_count"] == 0
        assert result["article_count"] == 1

    def test_local_fallback_respects_date_window(self):
        mock_supabase = MagicMock()
        mock_supabase.is_available.return_value = False

        history = {
            "2025-06-01 10:00:00": {
                "date": "2025-06-01",
                "summaries": {},
                "counts": {},
                "themed_articles": {"AI Models": [_article_row(title="Old June article", published="")],
                },
            },
            "2025-07-01 10:00:00": {
                "date": "2025-07-01",
                "summaries": {},
                "counts": {},
                "themed_articles": {"AI Models": [_article_row(title="July article", published="")]},
            },
        }

        result = build_wiki_context(
            supabase=mock_supabase,
            question="anything",
            history=history,
            date_from="2025-06-15",
            date_to="2025-07-15",
        )

        assert "July article" in result["context"]
        assert "Old June article" not in result["context"]


# ---------------------------------------------------------------------------
# Gateway-routed chat
# ---------------------------------------------------------------------------

class TestChatWithSage:
    def test_success_returns_response_and_provenance(self):
        messages = [{"role": "user", "content": "What's happening?"}]
        context = "=== WIKI CONTEXT ===\nSome data.\n=== END WIKI CONTEXT ==="

        gateway = _mock_gateway(_gateway_result(payload="Sage says hello."))
        with patch("core.sage_agent.get_gateway", return_value=gateway):
            result = chat_with_sage(messages, context)

        assert result["response"] == "Sage says hello."
        assert result["provenance"]["model"] == "nemotron-3-ultra:cloud"
        assert result["provenance"]["task"] == "sage_chat"

    def test_request_routed_as_sage_chat_without_deterministic_fallback(self):
        messages = [{"role": "user", "content": "What's happening?"}]

        gateway = _mock_gateway(_gateway_result(payload="ok"))
        with patch("core.sage_agent.get_gateway", return_value=gateway):
            chat_with_sage(messages, "context")

        request = gateway.execute.call_args.args[0]
        assert request.task is TaskType.SAGE_CHAT
        assert request.allow_deterministic_fallback is False
        assert request.temperature == 0.4
        assert request.max_tokens == 3000

    def test_prompt_contains_persona_context_and_conversation(self):
        messages = [{"role": "user", "content": "What's happening?"}]
        context = "=== WIKI CONTEXT ===\nSome data.\n=== END WIKI CONTEXT ==="

        gateway = _mock_gateway(_gateway_result(payload="ok"))
        with patch("core.sage_agent.get_gateway", return_value=gateway):
            chat_with_sage(messages, context)

        prompt = gateway.execute.call_args.args[0].input
        assert "You are Sage" in prompt
        assert "WIKI CONTEXT" in prompt
        assert "What's happening?" in prompt
        assert prompt.rstrip().endswith("Sage:")

    def test_multi_turn_prompt(self):
        messages = [
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Follow-up question"},
        ]

        gateway = _mock_gateway(_gateway_result(payload="Follow-up answer."))
        with patch("core.sage_agent.get_gateway", return_value=gateway):
            chat_with_sage(messages, "context")

        prompt = gateway.execute.call_args.args[0].input
        assert "First question" in prompt
        assert "First answer" in prompt
        assert "Follow-up question" in prompt

    def test_dict_wiki_context_form_is_unwrapped(self):
        messages = [{"role": "user", "content": "anything"}]
        wiki_ctx = {"context": "WIKI CONTEXT BODY", "run_count": 1,
                    "date_count": 1, "date_range": "2025-07-01"}

        gateway = _mock_gateway(_gateway_result(payload="ok"))
        with patch("core.sage_agent.get_gateway", return_value=gateway):
            chat_with_sage(messages, wiki_ctx)

        prompt = gateway.execute.call_args.args[0].input
        assert "WIKI CONTEXT BODY" in prompt

    def test_gateway_failure_returns_error_message(self):
        messages = [{"role": "user", "content": "anything"}]

        gateway = _mock_gateway(_gateway_result(status="failed", error="all models down"))
        with patch("core.sage_agent.get_gateway", return_value=gateway):
            result = chat_with_sage(messages, "context")

        assert "trouble connecting" in result["response"]
        assert result["provenance"]["error"] == "all models down"

    def test_quota_error_returns_quota_notice(self):
        messages = [{"role": "user", "content": "anything"}]

        gateway = _mock_gateway(_gateway_result(status="failed", error="HTTP 429 quota exceeded"))
        with patch("core.sage_agent.get_gateway", return_value=gateway):
            result = chat_with_sage(messages, "context")

        assert "429" in result["response"]
        assert "trouble connecting" not in result["response"]

    def test_gateway_exception_returns_error_message(self):
        messages = [{"role": "user", "content": "anything"}]

        gateway = MagicMock()
        gateway.execute = AsyncMock(side_effect=RuntimeError("event loop exploded"))
        with patch("core.sage_agent.get_gateway", return_value=gateway):
            result = chat_with_sage(messages, "context")

        assert "trouble connecting" in result["response"]
        assert result["provenance"] is None

    def test_empty_success_response_treated_as_failure(self):
        messages = [{"role": "user", "content": "anything"}]

        gateway = _mock_gateway(_gateway_result(payload="   "))
        with patch("core.sage_agent.get_gateway", return_value=gateway):
            result = chat_with_sage(messages, "context")

        assert "trouble connecting" in result["response"]

    def test_success_after_fallback_reports_fallback(self):
        messages = [{"role": "user", "content": "anything"}]

        gateway = _mock_gateway(_gateway_result(payload="Recovered via fallback.", fallback_used=True))
        with patch("core.sage_agent.get_gateway", return_value=gateway):
            result = chat_with_sage(messages, "context")

        assert result["response"] == "Recovered via fallback."
        assert result["provenance"]["fallback_used"] is True


# ---------------------------------------------------------------------------
# Persona constants
# ---------------------------------------------------------------------------

class TestPersonaConstants:
    def test_system_prompt_exists(self):
        assert len(SAGE_SYSTEM_PROMPT) > 100
        assert "Sage" in SAGE_SYSTEM_PROMPT
        assert "chronologically" in SAGE_SYSTEM_PROMPT

    def test_intro_exists(self):
        assert len(SAGE_INTRO) > 20
        assert "Sage" in SAGE_INTRO
