"""
Sage — the Memory Wiki conversational agent for AI Pulse.

Sage is a thoughtful AI research analyst who answers questions about
AI trends grounded in the data stored in the Memory Wiki (Supabase
theme summaries and articles).  Every response follows a strict format:

1. Factual, chronological answer with run-date citations.
2. Sage's own assessment of the trend.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from typing import Dict, List, Optional, Any, Tuple

from core.ai_gateway.contracts import AITaskRequest, AITaskResult, TaskType
from core.ai_gateway.gateway import get_gateway
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
1. **Direct Answer** — Open with one to three sentences that answer the \
   question head-on: the headline takeaway, no preamble.  For a narrow \
   factual question, the reply can be just this step plus citations.
2. **Evidence** — Support the answer with the facts, cited per claim.  For \
   trend-shaped questions, give the account chronologically — start with \
   when you *first* observed the trend and walk forward in time, using the \
   format "[Theme · Run YYYY-MM-DD]" for run-level citations.
3. **Sage's Assessment** — Close with a section titled "**My read on this:**" \
   where you give your own synthesis, opinion, or prediction based on the \
   patterns you see.

Grounding rules:
- You MUST only reference information present in the WIKI CONTEXT below.
- The context may also contain an ARTICLE EVIDENCE section of individual \
  article briefs, pre-filtered for relevance to this question.  Use it for \
  specific claims (models, numbers, events, with hyperlinks); use the WIKI \
  CONTEXT for the multi-run arc.  When a claim rests on a specific article, \
  cite it as "[Theme · Source · Date]".  If the brief carries a source link, \
  attach it to the citation as a Markdown hyperlink, e.g. "[OpenAI Blog · \
  2026-09-12](https://openai.com/...)" so the reader can open the original \
  article.  Only use links that appear in the context — never invent URLs.
- If the context does not contain enough data to answer, say so explicitly — \
  never fabricate or guess.  When the evidence covers only part of the \
  question, give that partial answer and state its scope (which dates, \
  themes, or sources the context actually covers) rather than refusing.
- Match the answer's shape to the question: a direct fact gets a short \
  direct answer; a trend gets its arc over time; a comparison contrasts \
  the two things explicitly.
- If runs disagree or a trend reverses across dates, surface the change \
  with its dates — never smooth it away or silently keep only the newest.
- Prefer concrete numbers, names, and dates from the context over vague \
  characterisations.
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

# --- Tier-1 article evidence (Phase 2.1) ---
# Sage also grounds answers in individual articles: a bounded, newest-first
# candidate window is fetched (SQL filters only), scored client-side against
# the question's keywords, and the top matches rendered as compact briefs
# after the wiki section.  Each brief carries its source link (Phase 2.2)
# so Sage can cite articles as Markdown hyperlinks, and the rendered list
# flows back to the UI for the evidence popover.
ARTICLE_CANDIDATE_LIMIT = 300  # max rows fetched before client-side scoring
ARTICLE_TOP_K = 15             # max article briefs in the context
ARTICLE_BUDGET_SHARE = 0.3     # fraction of max_chars for the article section
ARTICLE_SNIPPET_CHARS = 300    # per-article summary snippet ceiling
ARTICLE_MIN_BLOCK = 200        # skip the section if the budget is below this


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


def score_article_relevance(article: Dict, question_keywords: List[str]) -> float:
    """Score an article by keyword overlap with the user's question.

    Title tokens count double (titles are dense signal); summary tokens
    count once.  Returns a float >= 0.  Higher = more relevant.
    """
    if not question_keywords:
        return 0.0

    title_tokens = Counter(_tokenise(article.get("title", "")))
    summary_tokens = Counter(_tokenise(article.get("summary", "")))

    score = 0.0
    for kw in question_keywords:
        score += 2 * title_tokens.get(kw, 0)
        score += summary_tokens.get(kw, 0)
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
# Tier-1 article retrieval (Phase 2.1)
# ---------------------------------------------------------------------------

def _safe_link(link: Any) -> Optional[str]:
    """Return ``link`` only when it is a well-formed http(s) URL.

    Scraped article data is untrusted input: anything that isn't a plain
    http(s) URL (``javascript:``, ``data:``, malformed strings) becomes
    ``None`` so it can never reach the prompt or render as a Markdown
    hyperlink in the UI.
    """
    if isinstance(link, str):
        stripped = link.strip()
        if stripped.lower().startswith(("http://", "https://")):
            return stripped
    return None


def _normalise_article(row: Dict, run_date: str = "") -> Dict:
    """Normalise a Supabase row or history.json article into one shape.

    ``_date`` is the best available YYYY-MM-DD (published_at, then
    published_date, then the run date).  ``link`` is sanitised through
    ``_safe_link`` so only real http(s) URLs flow into the context and the
    UI evidence list (Phase 2.2 citations).
    """
    published = row.get("published_at") or row.get("published_date") or ""
    date = published[:10] if published else (run_date or "")
    return {
        "theme_name": row.get("theme_name", ""),
        "title": row.get("title", ""),
        "summary": row.get("summary", "") or "",
        "source_name": row.get("source_name", ""),
        "link": _safe_link(row.get("link")),
        "_date": date,
    }


def _fetch_article_candidates(
    supabase,
    history: Optional[Dict],
    theme_filter: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    source_filter: Optional[str],
    question: str = "",
) -> List[Dict]:
    """Fetch the candidate pool of articles for Tier-1 evidence.

    Supabase path first: with a question, keyword matching runs in Postgres
    FTS (``match_articles``, Phase 2.3) — every matching article ranked by
    ``ts_rank`` is a candidate, not just the newest window; the RPC degrades
    to a bounded newest-first window when nothing matches or the function is
    missing.  Local fallback flattens ``themed_articles`` from history.json
    runs inside the date window (mirroring the summary fallback's
    string-compare date filtering) — no Postgres, no FTS, so its candidates
    rely on client-side scoring in ``_select_articles`` either way.
    """
    candidates: List[Dict] = []

    if supabase and supabase.is_available():
        raw = supabase.get_articles_for_context(
            theme_filter=theme_filter,
            date_from=date_from,
            date_to=date_to,
            source_filter=source_filter,
            limit=ARTICLE_CANDIDATE_LIMIT,
            query=question,
        )
        # isinstance guard: a non-list (e.g. a bare Mock or an error sentinel)
        # means "no Supabase articles", never a crash.
        if isinstance(raw, list) and raw:
            return [_normalise_article(a) for a in raw]

    # Local fallback
    if history is None:
        history = load_full_history()
    if not history:
        return []
    for ts in sorted(history.keys()):
        entry = history[ts]
        run_date = entry.get("date", ts[:10])
        if date_from and run_date < date_from[:10]:
            continue
        if date_to and run_date > date_to[:10]:
            continue
        for theme_name, articles in entry.get("themed_articles", {}).items():
            if theme_filter and theme_name != theme_filter:
                continue
            for a in articles:
                if source_filter and a.get("source_name") != source_filter:
                    continue
                # themed_articles rows carry the theme as the dict key,
                # not as a field — inject it.
                candidates.append(_normalise_article({**a, "theme_name": theme_name}, run_date))
    return candidates


def _select_articles(candidates: List[Dict], question_kws: List[str]) -> List[Dict]:
    """Score candidates against the question and keep the top matches.

    Sorted by (score desc, date desc) then capped at ``ARTICLE_TOP_K``.  When
    nothing matches any keyword (all scores 0) the same sort degrades to
    newest-first, so Sage always gets some Tier-1 recency evidence.
    """
    if not candidates:
        return []
    for a in candidates:
        a["_score"] = score_article_relevance(a, question_kws)
    ranked = sorted(candidates, key=lambda a: (a["_score"], a["_date"]), reverse=True)
    return ranked[:ARTICLE_TOP_K]


def _render_article_section(articles: List[Dict], budget: int) -> Tuple[str, List[Dict]]:
    """Render the selected article briefs within ``budget`` chars.

    Each brief carries its source link (Phase 2.2) so Sage can cite articles
    as Markdown hyperlinks; ``link`` was already sanitised to an http(s) URL
    (or None) by ``_normalise_article``.

    Returns ``(section_text, rendered_articles)`` — the list holds one
    trimmed dict per brief that actually made it into the section
    (theme_name / title / source_name / _date / link / snippet), feeding the
    UI evidence popover; ``("", [])`` when there is nothing to render or the
    budget is below ``ARTICLE_MIN_BLOCK``.
    """
    if not articles or budget < ARTICLE_MIN_BLOCK:
        return "", []

    header = "=== ARTICLE EVIDENCE (top keyword matches, newest first) ===\n\n"
    footer = "\n=== END ARTICLE EVIDENCE ==="
    used = len(header) + len(footer)
    blocks: List[str] = []
    rendered: List[Dict] = []
    for a in articles:
        snippet = _condense_text(a.get("summary", ""), ARTICLE_SNIPPET_CHARS)
        block = f"- [{a.get('theme_name', '')} · {a.get('source_name', '')} · {a.get('_date', '')}] {a.get('title', '')}"
        if snippet:
            block += f" — {snippet}"
        link = a.get("link")
        if link:
            block += f"\n  Link: {link}"
        block += "\n"
        if used + len(block) > budget and blocks:
            # Budget spent; keep the higher-ranked briefs already collected.
            break
        blocks.append(block)
        rendered.append({
            "theme_name": a.get("theme_name", ""),
            "title": a.get("title", ""),
            "source_name": a.get("source_name", ""),
            "_date": a.get("_date", ""),
            "link": link,
            "snippet": snippet,
        })
        used += len(block)

    if not blocks:
        return "", []
    return header + "".join(blocks) + footer, rendered


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
    limit: int = 300,
) -> Dict[str, Any]:
    """Assemble a per-date-budgeted, time-decayed context string from wiki data.

    Two grounding layers are packed into the ``max_chars`` budget:
      - Tier-2 wiki summaries, split across two tiers:
        * Recent tier (last ``RECENT_DETAIL_COUNT`` dates): full 3-field blocks.
        * Older tier (all earlier dates): condensed blocks (only
          ``what_is_happening``, truncated to ``CONDENSED_CHAR_LIMIT`` chars
          at sentence boundary).  This guarantees every date in the window
          is represented rather than the latest run's themes greedily
          consuming the whole budget.
      - Tier-1 article evidence (Phase 2.1): a bounded, newest-first
        candidate window is fetched and the top keyword matches against the
        question are rendered as compact briefs, within an
        ``ARTICLE_BUDGET_SHARE`` slice of the budget.  Whatever the article
        section doesn't use flows back to the wiki section.

    Returns:
        A dict ``{"context": str, "run_count": int, "date_count": int,
        "date_range": str, "article_count": int, "articles": List[Dict]}``
        so the caller can surface how far Sage actually reached.  The
        ``articles`` list (Phase 2.2) carries the exact briefs rendered into
        the context — title, source, date, sanitised link, snippet — so the
        UI can show an evidence popover per reply.
    """
    from collections import defaultdict
    question_kws = _tokenise(question)

    # ------- Fetch Tier-1 article candidates & render within budget -------
    # The raw question goes to SQL (websearch_to_tsquery does its own
    # parsing); the tokenised keywords are for client-side scoring.
    article_candidates = _fetch_article_candidates(
        supabase, history, theme_filter, date_from, date_to, source_filter, question
    )
    selected_articles = _select_articles(article_candidates, question_kws)
    article_section, article_list = _render_article_section(
        selected_articles, int(max_chars * ARTICLE_BUDGET_SHARE)
    )
    article_count = len(article_list)
    # Wiki gets whatever budget the article section didn't consume; with no
    # articles found it keeps the full budget (existing behaviour).
    wiki_max = max_chars - len(article_section)

    # ------- Fetch summaries -------
    summaries: List[Dict] = []

    if supabase and supabase.is_available():
        raw = supabase.get_summaries_across_runs(
            theme_filter=theme_filter,
            date_from=date_from,
            date_to=date_to,
            source_filter=source_filter,
            limit=limit,
            prefer_recent=True,
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
                if date_from and run_date < date_from[:10]:
                    continue
                if date_to and run_date > date_to[:10]:
                    continue
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

    if not summaries and not article_section:
        return {
            "context": "[No wiki data available for the selected filters.]",
            "run_count": 0,
            "date_count": 0,
            "date_range": "",
            "article_count": 0,
            "articles": [],
        }
    if not summaries:
        # Summaries empty but article evidence exists — render it alone.
        return {
            "context": article_section,
            "run_count": 0,
            "date_count": 0,
            "date_range": "",
            "article_count": article_count,
            "articles": article_list,
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
        if article_section:
            return {
                "context": article_section,
                "run_count": 0,
                "date_count": 0,
                "date_range": "",
                "article_count": article_count,
                "articles": article_list,
            }
        return {
            "context": "[No wiki data available for the selected filters.]",
            "run_count": 0,
            "date_count": 0,
            "date_range": "",
            "article_count": 0,
            "articles": [],
        }

    # ------- Split budget between recent & older tiers -------
    recent_dates = set(dates[-RECENT_DETAIL_COUNT:])
    recent_dates_list = [d for d in dates if d in recent_dates]
    older_dates_list = [d for d in dates if d not in recent_dates]

    recent_budget = int(wiki_max * RECENT_BUDGET_SHARE)
    older_budget = wiki_max - recent_budget

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
            if char_count >= wiki_max:
                break
        if char_count >= wiki_max:
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

    # Tier-1 evidence rides after the Tier-2 narrative.
    if article_section:
        lines.append("\n\n")
        lines.append(article_section)

    context_str = "".join(lines)

    return {
        "context": context_str,
        "run_count": run_count,
        "date_count": date_count,
        "date_range": date_range_str,
        "article_count": article_count,
        "articles": article_list,
    }


# ---------------------------------------------------------------------------
# Chat interface
# ---------------------------------------------------------------------------

_ENGINE_UNAVAILABLE_MSG = (
    "I'm having trouble connecting to my analysis engine right now. "
    "Please try again in a moment."
)


def chat_with_sage(
    messages: List[Dict[str, str]],
    wiki_context,  # Union[str, Dict] — dict from build_wiki_context or legacy str
) -> Dict[str, Any]:
    """Build a multi-turn prompt and generate Sage's reply through the Model
    Gateway (``TaskType.SAGE_CHAT``).

    ``messages`` is a list of ``{"role": "user"|"assistant", "content": "..."}``
    dicts representing the conversation so far (the latest user message is the
    last element).

    ``wiki_context`` is either a string (legacy) or the dict returned by
    ``build_wiki_context`` (preferred). When a dict is passed, the actual
    context text is read from its ``"context"`` key.

    Routing through the gateway gives Sage the same machinery as the
    summariser/classifier: per-model health tracking, retries, an ordered
    fallback chain (Ollama primary, Ollama + Gemini fallbacks), and a
    ``Provenance`` record for every call.

    Returns:
        ``{"response": str, "provenance": dict | None}`` — the provenance dict
        (from ``Provenance.to_dict()``) lets the UI caption which engine
        answered; it is ``None`` only when the gateway call could not be made
        at all.
    """
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

    # Providers take a single prompt (no separate system channel), so the
    # persona + wiki context are folded in ahead of the conversation —
    # EVALUATE-style verbatim prompting.
    full_prompt = f"{system}\n\n---\n\n{prompt}"

    request = AITaskRequest(
        task=TaskType.SAGE_CHAT,
        input=full_prompt,
        temperature=0.4,
        max_tokens=3000,
        allow_deterministic_fallback=False,
        metadata={"caller": "sage_agent"},
    )

    try:
        result: AITaskResult = asyncio.run(get_gateway().execute(request))
    except Exception as e:
        logger.error("Sage gateway execution error: %s", e)
        return {"response": _ENGINE_UNAVAILABLE_MSG, "provenance": None}

    if result.is_success() and isinstance(result.result, str) and result.result.strip():
        logger.info(
            "Sage replied via %s/%s (%.1fs, %d attempt(s)%s)",
            result.provenance.provider,
            result.provenance.model,
            result.provenance.latency_ms / 1000,
            result.provenance.attempts,
            " — fallback" if result.provenance.fallback_used else "",
        )
        return {
            "response": result.result.strip(),
            "provenance": result.provenance.to_dict(),
        }

    # Total failure across the fallback chain (no deterministic fallback
    # for sage_chat). Distinguish quota exhaustion so the user knows to
    # check API quotas rather than just retry.
    error = result.provenance.error or str(result.result)
    logger.warning("Sage gateway failed on all models: %s", error)
    if "429" in error or "quota" in error.lower():
        return {
            "response": (
                "⚠️ *Sage Notice: All engines are quota-limited right now (HTTP 429). "
                "Please check API quotas or try again later.*"
            ),
            "provenance": result.provenance.to_dict(),
        }
    return {"response": _ENGINE_UNAVAILABLE_MSG, "provenance": result.provenance.to_dict()}
