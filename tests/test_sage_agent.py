"""Tests for the Sage agent module — relevance scoring, first-appearance
annotation, context building, and chat prompt assembly."""

import pytest
from unittest.mock import MagicMock, patch

from core.sage_agent import (
    _tokenise,
    score_summary_relevance,
    annotate_first_appearances,
    build_wiki_context,
    chat_with_sage,
    SAGE_SYSTEM_PROMPT,
    SAGE_INTRO,
)


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

        context = build_wiki_context(
            supabase=mock_supabase,
            question="What happened with AI models?",
            history=history,
        )

        assert "GPT-5 announced" in context
        assert "WIKI CONTEXT" in context
        assert "Run 2025-07-01" in context

    def test_empty_history_returns_no_data_message(self):
        mock_supabase = MagicMock()
        mock_supabase.is_available.return_value = False

        context = build_wiki_context(
            supabase=mock_supabase,
            question="anything",
            history={},
        )
        assert "No wiki data" in context

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

        context = build_wiki_context(
            supabase=mock_supabase,
            question="anything",
            history=history,
            max_chars=2000,
        )
        assert len(context) <= 2500  # some slack for the wrapper lines

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

        context = build_wiki_context(
            supabase=mock_supabase,
            question="agentic systems trends",
        )

        assert "Agent frameworks emerging" in context
        assert "Agentic Systems" in context


# ---------------------------------------------------------------------------
# Chat prompt assembly
# ---------------------------------------------------------------------------

class TestChatWithSage:
    def test_calls_llm_generate(self):
        mock_llm = MagicMock()
        mock_llm.generate.return_value = "Sage says hello."

        messages = [{"role": "user", "content": "What's happening?"}]
        context = "=== WIKI CONTEXT ===\nSome data.\n=== END WIKI CONTEXT ==="

        result = chat_with_sage(mock_llm, messages, context)

        assert result == "Sage says hello."
        mock_llm.generate.assert_called_once()
        call_kwargs = mock_llm.generate.call_args
        assert "Sage" in call_kwargs.kwargs["system"]
        assert "WIKI CONTEXT" in call_kwargs.kwargs["system"]
        assert "What's happening?" in call_kwargs.kwargs["prompt"]

    def test_multi_turn_prompt(self):
        mock_llm = MagicMock()
        mock_llm.generate.return_value = "Follow-up answer."

        messages = [
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Follow-up question"},
        ]

        result = chat_with_sage(mock_llm, messages, "context")
        prompt = mock_llm.generate.call_args.kwargs["prompt"]
        assert "First question" in prompt
        assert "First answer" in prompt
        assert "Follow-up question" in prompt

    def test_llm_failure_returns_error_message(self):
        mock_llm = MagicMock()
        mock_llm.is_quota_exceeded.return_value = False
        mock_llm.generate.side_effect = Exception("LLM down")

        mock_gemini = MagicMock()
        mock_gemini.is_configured.return_value = False

        messages = [{"role": "user", "content": "anything"}]
        result = chat_with_sage(mock_llm, messages, "context", gemini_client=mock_gemini)

        assert "trouble connecting" in result

    def test_sage_fallback_to_gemini_on_quota_exceeded(self):
        from core.llm_client import LLMClient
        LLMClient.mark_quota_exceeded("test quota")

        mock_llm = MagicMock()
        mock_gemini = MagicMock()
        mock_gemini.is_configured.return_value = True
        mock_gemini.default_model = "gemini-3.7-flash"
        mock_gemini.generate_content.return_value = "Sage grounded answer from Gemini fallback."

        messages = [{"role": "user", "content": "What happened with Claude 3.7?"}]
        result = chat_with_sage(mock_llm, messages, "context", gemini_client=mock_gemini)

        assert result == "Sage grounded answer from Gemini fallback."
        mock_gemini.generate_content.assert_called_once()
        # Verify primary LLM was bypassed because quota was exceeded
        mock_llm.generate.assert_not_called()

    def test_sage_fallback_to_gemini_on_primary_exception(self):
        mock_llm = MagicMock()
        mock_llm.is_quota_exceeded.return_value = False
        mock_llm.generate.side_effect = Exception("Ollama HTTP 500")

        mock_gemini = MagicMock()
        mock_gemini.is_configured.return_value = True
        mock_gemini.default_model = "gemini-3.7-flash"
        mock_gemini.generate_content.return_value = "Recovered response via Gemini."

        messages = [{"role": "user", "content": "Tell me about reasoning models"}]
        result = chat_with_sage(mock_llm, messages, "context", gemini_client=mock_gemini)

        assert result == "Recovered response via Gemini."
        mock_gemini.generate_content.assert_called_once()


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
