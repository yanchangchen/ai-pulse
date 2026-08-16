"""
Sage — the Memory Wiki conversational agent for AI Pulse.

Sage is a thoughtful AI research analyst who answers questions about
AI trends grounded in the data stored in the Memory Wiki (Supabase
theme summaries and articles).  Every response follows a strict format:

1. Factual, chronological answer with run-date citations.
2. Sage's own assessment of the trend.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List, Optional

from core.llm_client import LLMClient
from core.history_manager import load_full_history
from core.logger import setup_logger

logger = setup_logger(__name__)

# ---------------------------------------------------------------------------
# Sage persona
# ---------------------------------------------------------------------------

SAGE_SYSTEM_PROMPT = """\
You are Sage, a quietly brilliant AI research analyst embedded inside AI Pulse — \
an intelligence dashboard that tracks the AI industry through curated news runs.

Your personality:
- Thoughtful, precise, and calm.  You speak with the authority of a senior \
  researcher who has been watching the AI landscape for years.
- You draw connections between themes that others miss.
- You are never verbose for the sake of it.  Every sentence earns its place.

Your response format (follow this strictly):
1. **Factual Account** — Answer the user's question chronologically, citing \
   the specific run date and theme for every claim.  Use the format \
   "[Theme · Run YYYY-MM-DD]" for citations.  Start with when you *first* \
   observed the trend and walk forward in time.
2. **Sage's Assessment** — After the factual account, add a section titled \
   "**My read on this:**" where you give your own synthesis, opinion, or \
   prediction based on the patterns you see.

Grounding rules:
- You MUST only reference information present in the WIKI CONTEXT below.
- If the context does not contain enough data to answer, say so explicitly — \
  never fabricate or guess.
- When the user asks "when did you first see" something, scan the context \
  chronologically and cite the earliest run date where it appears.
- Refer to yourself as "I" and to the user as "you".
"""

SAGE_INTRO = (
    "I'm Sage. I've been watching the AI landscape so you don't have to miss "
    "the signals. Ask me anything about what I've observed."
)


# ---------------------------------------------------------------------------
# Relevance scoring
# ---------------------------------------------------------------------------

# Common English words to ignore when scoring relevance
_SCORING_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "before", "after", "above", "below", "and", "but", "or", "not", "no",
    "if", "then", "so", "that", "this", "it", "its", "what", "which",
    "who", "whom", "how", "when", "where", "why", "all", "each", "every",
    "both", "few", "more", "most", "other", "some", "such", "than", "too",
    "very", "just", "about", "also", "any", "i", "me", "my", "you", "your",
    "we", "our", "they", "them", "their", "he", "she", "his", "her",
})


def _tokenise(text: str) -> List[str]:
    """Split text into lowercase alpha tokens, dropping stopwords."""
    return [
        w for w in re.findall(r"[a-z]+", text.lower())
        if w not in _SCORING_STOPWORDS and len(w) > 2
    ]


def score_summary_relevance(summary_block: Dict, question_keywords: List[str]) -> float:
    """Score a summary block by keyword overlap with the user's question.

    Returns a float >= 0.  Higher = more relevant.
    """
    if not question_keywords:
        return 0.0

    text_parts = [
        summary_block.get("what_is_happening", ""),
        summary_block.get("why_it_matters", ""),
        summary_block.get("what_to_watch", ""),
        summary_block.get("theme_name", ""),
    ]
    block_tokens = Counter(_tokenise(" ".join(text_parts)))

    score = 0.0
    for kw in question_keywords:
        score += block_tokens.get(kw, 0)
    return score


# ---------------------------------------------------------------------------
# First-appearance annotation
# ---------------------------------------------------------------------------

def annotate_first_appearances(ordered_summaries: List[Dict]) -> List[Dict]:
    """Walk summaries in chronological order and flag the first time each
    theme appears with a ``_first_appearance`` boolean key.

    ``ordered_summaries`` must already be sorted by ``run_timestamp`` ASC.
    Returns the same list, mutated in place for efficiency.
    """
    seen_themes: set = set()
    for s in ordered_summaries:
        theme = s.get("theme_name", "")
        if theme not in seen_themes:
            s["_first_appearance"] = True
            seen_themes.add(theme)
        else:
            s["_first_appearance"] = False
    return ordered_summaries


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def build_wiki_context(
    supabase,
    question: str,
    theme_filter: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    source_filter: Optional[str] = None,
    history: Optional[Dict] = None,
    max_chars: int = 12_000,
) -> str:
    """Assemble a relevance-ranked, chronologically ordered context string
    from wiki data for Sage to ground its answers on.

    Priority logic:
    1. Fetch all cross-run summaries from Supabase (or local history fallback).
    2. Score each summary block by keyword overlap with the question.
    3. Always include the most recent run as an anchor.
    4. Take the top-N scored blocks until ``max_chars`` is reached.
    5. Annotate first appearances per theme.
    """
    question_kws = _tokenise(question)

    # ------- Fetch summaries -------
    summaries: List[Dict] = []

    if supabase and supabase.is_available():
        raw = supabase.get_summaries_across_runs(
            theme_filter=theme_filter,
            date_from=date_from,
            date_to=date_to,
            source_filter=source_filter,
            limit=200,
        )
        if raw:
            summaries = raw

    # Local fallback
    if not summaries:
        if history is None:
            history = load_full_history()
        if history:
            for ts in sorted(history.keys()):
                entry = history[ts]
                run_date = entry.get("date", ts[:10])
                for theme_name, summary in entry.get("summaries", {}).items():
                    if theme_filter and theme_name != theme_filter:
                        continue
                    summaries.append({
                        "run_timestamp": ts,
                        "run_date": run_date,
                        "theme_name": theme_name,
                        "what_is_happening": summary.get("what_is_happening", ""),
                        "why_it_matters": summary.get("why_it_matters", ""),
                        "what_to_watch": summary.get("what_to_watch", ""),
                        "article_count": entry.get("counts", {}).get(theme_name, 0),
                    })

    if not summaries:
        return "[No wiki data available for the selected filters.]"

    from core.design_system import sanitize_summary_html

    # ------- Score & select -------
    for s in summaries:
        s["what_is_happening"] = sanitize_summary_html(s.get("what_is_happening", ""))
        s["_relevance"] = score_summary_relevance(s, question_kws)

    # Ensure the most recent run is always included (anchor)
    latest_ts = max(s["run_timestamp"] for s in summaries)
    anchor_blocks = [s for s in summaries if s["run_timestamp"] == latest_ts]

    # Remaining blocks sorted by relevance (descending), then chronologically
    other_blocks = [s for s in summaries if s["run_timestamp"] != latest_ts]
    other_blocks.sort(key=lambda s: (-s["_relevance"], s["run_timestamp"]))

    selected = anchor_blocks + other_blocks

    # ------- Annotate first appearances -------
    selected.sort(key=lambda s: s["run_timestamp"])
    annotate_first_appearances(selected)

    # ------- Format context string with char budget -------
    lines: List[str] = ["=== WIKI CONTEXT (chronological) ===\n"]
    char_count = len(lines[0])

    for s in selected:
        first_tag = " [FIRST APPEARANCE]" if s.get("_first_appearance") else ""
        block = (
            f"--- [{s['theme_name']}] · Run {s['run_date']}{first_tag} "
            f"({s.get('article_count', '?')} articles) ---\n"
            f"What happened: {s.get('what_is_happening', 'N/A')}\n"
            f"Significance: {s.get('why_it_matters', 'N/A')}\n"
            f"Watchlist: {s.get('what_to_watch', 'N/A')}\n\n"
        )
        if char_count + len(block) > max_chars:
            break
        lines.append(block)
        char_count += len(block)

    lines.append("=== END WIKI CONTEXT ===")
    return "".join(lines)


# ---------------------------------------------------------------------------
# Chat interface
# ---------------------------------------------------------------------------

def chat_with_sage(
    llm_client: LLMClient,
    messages: List[Dict[str, str]],
    wiki_context: str,
    gemini_client: Optional[Any] = None,
    gemini_model: Optional[str] = None,
) -> str:
    """Build a multi-turn prompt and call the LLM with automatic Google Gemini fallback.

    ``messages`` is a list of ``{"role": "user"|"assistant", "content": "..."}``
    dicts representing the conversation so far (the latest user message is the
    last element).

    Returns Sage's text response.
    """
    from core.gemini_client import GeminiClient, GeminiQuotaError, GeminiClientError

    # Build the full system prompt with wiki context injected
    system = f"{SAGE_SYSTEM_PROMPT}\n\n{wiki_context}"

    # Build conversational prompt from message history
    prompt_parts: List[str] = []
    for msg in messages:
        role_label = "User" if msg["role"] == "user" else "Sage"
        prompt_parts.append(f"{role_label}: {msg['content']}")

    prompt = "\n\n".join(prompt_parts) + "\n\nSage:"

    # 1. Attempt primary Ollama LLM if quota is not flagged
    quota_active = False
    try:
        from core.llm_client import LLMClient as _LLMClient
        quota_active = _LLMClient.is_quota_exceeded()
    except Exception:
        pass

    if not quota_active:
        try:
            response = llm_client.generate(
                prompt=prompt,
                system=system,
                temperature=0.4,
                max_tokens=2000,
            )
            if response and response.strip():
                return response.strip()
        except Exception as e:
            logger.warning("Sage primary LLM failed (%s). Attempting Gemini fallback...", e)

    # 2. Seamless Fallback to Google Gemini
    g_client = gemini_client if gemini_client is not None else GeminiClient()
    if g_client.is_configured():
        target_model = gemini_model or g_client.default_model
        try:
            logger.info("Sage querying Google Gemini fallback with model '%s'...", target_model)
            gemini_resp = g_client.generate_content(
                prompt=prompt,
                system_instruction=system,
                model=target_model,
                temperature=0.4,
                max_output_tokens=2000,
                timeout=30,
            )
            if gemini_resp and gemini_resp.strip():
                return gemini_resp.strip()
        except GeminiQuotaError as q_err:
            logger.warning("Sage Gemini fallback hit quota: %s", q_err)
            return (
                f"⚠️ *Sage Notice: Primary LLM quota is paused, and Google Gemini quota limit (HTTP 429) "
                f"was reached for model `{target_model}`. Please consider checking API quota or selecting another model ID.*"
            )
        except Exception as g_err:
            logger.error("Sage Gemini fallback failed: %s", g_err)

    return (
        "I'm having trouble connecting to my analysis engine right now. "
        "Please try again in a moment."
    )
