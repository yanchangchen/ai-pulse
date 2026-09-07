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
from typing import Dict, List, Optional, Any

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
# Context budgeting constants
# ---------------------------------------------------------------------------

# How many of the most recent dates get full 3-field briefs. All older dates
# are included in condensed form (only what_is_happening, truncated).
RECENT_DETAIL_COUNT = 2

# Character ceiling for condensed older-date blocks. ~300 chars ≈ 2 sentences
# of the most information-dense factual lead.
CONDENSED_CHAR_LIMIT = 300

# Share of the total char budget reserved for the recent tier. The older tier
# gets the remainder.
RECENT_BUDGET_SHARE = 0.5


# ---------------------------------------------------------------------------
# Condense / format helpers
# ---------------------------------------------------------------------------

def _condense_text(text: str, limit: int = CONDENSED_CHAR_LIMIT) -> str:
    """Truncate ``text`` at the last sentence boundary within ``limit`` chars,
    appending ``…`` if truncated. Returns the original text unchanged when it
    already fits."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    head = text[:limit]
    # Seek the last sentence terminator (., !, ?) followed by whitespace or EOL.
    import re
    last_break = max(
        (m.end() for m in re.finditer(r"[.!?](?:\s|$)", head)),
        default=limit,
    )
    if last_break <= 0 or last_break > limit:
        last_break = limit
    return head[:last_break].rstrip() + "…"


def _format_full_block(block: Dict) -> str:
    """Render a full 3-field block for a recent-tier date."""
    first_tag = " [FIRST APPEARANCE]" if block.get("_first_appearance") else ""
    return (
        f"--- [{block['theme_name']}] · Run {block['run_date']}{first_tag} "
        f"({block.get('article_count', '?')} articles) ---\n"
        f"What happened: {block.get('what_is_happening', 'N/A')}\n"
        f"Significance: {block.get('why_it_matters', 'N/A')}\n"
        f"Watchlist: {block.get('what_to_watch', 'N/A')}\n\n"
    )


def _format_condensed_block(block: Dict) -> str:
    """Render a condensed single-field block for an older-tier date."""
    first_tag = " [FIRST APPEARANCE]" if block.get("_first_appearance") else ""
    condensed = _condense_text(block.get("what_is_happening", "N/A"))
    return (
        f"--- [{block['theme_name']}] · Run {block['run_date']}{first_tag} "
        f"({block.get('article_count', '?')} articles, condensed) ---\n"
        f"What happened: {condensed}\n\n"
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
    max_chars: int = 40_000,
) -> Dict[str, Any]:
    """Assemble a per-date-budgeted, time-decayed context string from wiki data.

    The total ``max_chars`` budget is split across two tiers:
      - Recent tier (last ``RECENT_DETAIL_COUNT`` dates): full 3-field blocks.
      - Older tier (all earlier dates): condensed blocks (only ``what_is_happening``,
        truncated to ``CONDENSED_CHAR_LIMIT`` chars at sentence boundary).

    This guarantees every date in the window is represented rather than the
    latest run's themes greedily consuming the whole budget.

    Returns:
        A dict ``{"context": str, "run_count": int, "date_count": int, "date_range": str}``
        so the caller can surface how far Sage actually reached.
    """
    from collections import defaultdict
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
        return {
            "context": "[No wiki data available for the selected filters.]",
            "run_count": 0,
            "date_count": 0,
            "date_range": "",
        }

    from core.design_system import sanitize_summary_html

    # ------- Score & sanitise -------
    for s in summaries:
        s["what_is_happening"] = sanitize_summary_html(s.get("what_is_happening", ""))
        s["why_it_matters"] = sanitize_summary_html(s.get("why_it_matters", ""))
        s["what_to_watch"] = sanitize_summary_html(s.get("what_to_watch", ""))
        s["_relevance"] = score_summary_relevance(s, question_kws)

    # ------- Group by date, sort chronologically ascending -------
    by_date: Dict[str, List[Dict]] = defaultdict(list)
    for s in summaries:
        by_date[s["run_date"]].append(s)

    dates = sorted(by_date.keys())
    if not dates:
        return {
            "context": "[No wiki data available for the selected filters.]",
            "run_count": 0,
            "date_count": 0,
            "date_range": "",
        }

    # ------- Split budget between recent & older tiers -------
    recent_dates = set(dates[-RECENT_DETAIL_COUNT:])
    recent_dates_list = [d for d in dates if d in recent_dates]
    older_dates_list = [d for d in dates if d not in recent_dates]

    recent_budget = int(max_chars * RECENT_BUDGET_SHARE)
    older_budget = max_chars - recent_budget

    per_recent_date = recent_budget // max(len(recent_dates_list), 1)
    per_older_date = older_budget // max(len(older_dates_list), 1)

    # Minimum block sizes used as a guard: if a date's per-date quota is smaller
    # than this, we skip that date rather than emit a half-formed block.
    MIN_FULL_BLOCK = 800
    MIN_CONDENSED_BLOCK = 200

    selected: List[Dict] = []
    char_count = 0

    for date in dates:
        blocks = by_date[date]
        # Within each date, rank themes by relevance so the most relevant
        # themes survive when the per-date quota is tight.
        blocks.sort(key=lambda b: (-b["_relevance"], b["theme_name"]))

        is_recent = date in recent_dates
        quota = per_recent_date if is_recent else per_older_date
        min_block = MIN_FULL_BLOCK if is_recent else MIN_CONDENSED_BLOCK
        formatter = _format_full_block if is_recent else _format_condensed_block

        if quota < min_block:
            # Date's quota too small — skip it rather than emit a partial block.
            continue

        date_char_used = 0
        for b in blocks:
            block_text = formatter(b)
            block_len = len(block_text)
            if date_char_used + block_len > quota and date_char_used > 0:
                # This date's quota is spent; stop adding themes for this date.
                break
            b["_first_appearance"] = False  # placeholder; annotation pass follows
            selected.append({**b, "_formatted": block_text})
            date_char_used += block_len
            char_count += block_len
            if char_count >= max_chars:
                break
        if char_count >= max_chars:
            break

    # ------- Annotate first appearances across selected blocks -------
    selected.sort(key=lambda s: s["run_timestamp"])
    seen_themes: set = set()
    for s in selected:
        theme = s.get("theme_name", "")
        if theme not in seen_themes:
            s["_first_appearance"] = True
            seen_themes.add(theme)
        else:
            s["_first_appearance"] = False
    # Re-render formatted text with the first-appearance tag now set.
    for s in selected:
        is_recent = s["run_date"] in recent_dates
        formatter = _format_full_block if is_recent else _format_condensed_block
        s["_formatted"] = formatter(s)

    # ------- Assemble output with metadata header -------
    date_range_str = (
        f"{dates[0]} → {dates[-1]}" if len(dates) > 1 else (dates[0] if dates else "")
    )
    run_count = len(summaries)
    date_count = len(dates)
    metadata = (
        f"[Sage context: {run_count} runs across {date_count} dates, "
        f"{date_range_str}]\n\n"
    )

    lines: List[str] = ["=== WIKI CONTEXT (chronological, time-decayed resolution) ===\n", metadata]
    for s in selected:
        lines.append(s["_formatted"])
    lines.append("=== END WIKI CONTEXT ===")

    context_str = "".join(lines)

    return {
        "context": context_str,
        "run_count": run_count,
        "date_count": date_count,
        "date_range": date_range_str,
    }


# ---------------------------------------------------------------------------
# Chat interface
# ---------------------------------------------------------------------------

def chat_with_sage(
    llm_client: LLMClient,
    messages: List[Dict[str, str]],
    wiki_context,  # Union[str, Dict] — dict from build_wiki_context or legacy str
    gemini_client: Optional[Any] = None,
    gemini_model: Optional[str] = None,
) -> str:
    """Build a multi-turn prompt and call the LLM with automatic Google Gemini fallback.

    ``messages`` is a list of ``{"role": "user"|"assistant", "content": "..."}``
    dicts representing the conversation so far (the latest user message is the
    last element).

    ``wiki_context`` is either a string (legacy) or the dict returned by
    ``build_wiki_context`` (preferred). When a dict is passed, the actual
    context text is read from its ``"context"`` key.

    Returns Sage's text response.
    """
    from core.gemini_client import GeminiClient, GeminiQuotaError, GeminiClientError

    # Accept both the legacy string form and the new dict form from
    # build_wiki_context.
    context_str = (
        wiki_context["context"] if isinstance(wiki_context, dict) else wiki_context
    )

    # Build the full system prompt with wiki context injected
    system = f"{SAGE_SYSTEM_PROMPT}\n\n{context_str}"

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
                max_tokens=3000,
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
                max_output_tokens=3000,
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
