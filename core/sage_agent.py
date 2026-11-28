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
import calendar
import re
from collections import Counter
from datetime import datetime, timezone
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
- The WIKI CONTEXT may contain monthly macro rollup blocks ("Month YYYY-MM \
  (macro rollup)") covering older periods.  Use them for the multi-month arc \
  and cite them as "[Theme · Month YYYY-MM]".  Milestone lines inside a \
  rollup carry real dates and sometimes a Link line — when a claim rests on \
  a milestone, cite its date and attach the link as a Markdown hyperlink, \
  exactly as for article evidence.
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

# --- Tier-3 monthly rollups (Phase 6.2) ---
# Older completed months with monthly_syntheses rows render as one macro
# block per theme instead of per-date condensed blocks; the current month
# always stays Tier-2 (its rollup can be up to monthly_refresh_days stale).
MIN_TIER3_BLOCK = 400          # skip a month block if its quota is below this


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
    published = row.get("published_at") or row.get("published_date") or row.get("run_date") or ""
    date = str(published)[:10] if published else (run_date or "")
    out = {
        "theme_name": row.get("theme_name", ""),
        "title": row.get("title", ""),
        "summary": row.get("summary", "") or "",
        "source_name": row.get("source_name", ""),
        "link": _safe_link(row.get("link")),
        "_date": date,
    }
    # Phase 5: carry hybrid-retrieval provenance through to selection/render.
    for key in ("_rrf_score", "_retrieval", "chunk_text"):
        if key in row:
            out[key] = row[key]
    return out


def _fetch_article_candidates(
    supabase,
    history: Optional[Dict],
    theme_filter: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    source_filter: Optional[str],
    question: str = "",
    hybrid_query: Optional[str] = None,
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
        # Phase 5.1/5.2/5.3: hybrid retrieval (dense + FTS, RRF fusion,
        # temporal bounds + decay) replaces the keyword-overlap scorer when
        # it returns results. Any failure or empty result falls through to
        # the Phase 2.3 FTS path below, then to the local window.
        if question and question.strip():
            try:
                from config.settings import get_retrieval_settings
                from core.supabase_client import SupabaseManager
                # Only the real client implements the Phase 5 RPCs; stub/mock
                # clients keep the legacy FTS path below.
                if (
                    isinstance(supabase, SupabaseManager)
                    and get_retrieval_settings().get("hybrid_retrieval_enabled", True)
                ):
                    from core.retrieval import hybrid_search
                    # Phase 6.2: the milestone-biased query (question + top
                    # rollup milestones) drives the hybrid leg only; the FTS
                    # fallback below keeps the raw question.
                    fused = hybrid_search(
                        hybrid_query or question,
                        supabase=supabase,
                        theme_filter=theme_filter,
                        date_from=date_from,
                        date_to=date_to,
                        source_filter=source_filter,
                        top_k=ARTICLE_TOP_K,
                    )
                    if fused:
                        return [_normalise_article(a) for a in fused]
            except Exception as e:
                logger.warning("Hybrid retrieval failed, falling back to FTS: %s", e)
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
    # Phase 5: hybrid results arrive already fused, decayed, and ranked —
    # re-scoring them by keyword overlap would undo the retrieval ordering.
    if any("_rrf_score" in a for a in candidates):
        return candidates[:ARTICLE_TOP_K]
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

    header = "=== ARTICLE EVIDENCE (ranked matches: hybrid retrieval or keyword fallback) ===\n\n"
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
# Tier-3 monthly rollups (Phase 6.2)
# ---------------------------------------------------------------------------

def _fetch_monthly_rollups(supabase, theme_filter: Optional[str]) -> List[Dict]:
    """Fetch monthly_syntheses rows for the Tier-3 path, sanitised.

    Guarded like the hybrid leg: only the real SupabaseManager implements
    the table, so MagicMock stubs in the legacy tests never reach it.
    Returns [] on any failure / pre-migration / non-list response.
    """
    try:
        from config.settings import get_sage_settings
        from core.supabase_client import SupabaseManager
        if not get_sage_settings().get("sage_tier3_enabled", True):
            return []
        if not (supabase and isinstance(supabase, SupabaseManager) and supabase.is_available()):
            return []
        rows = supabase.get_monthly_syntheses()
        if not isinstance(rows, list):
            return []
    except Exception as e:
        logger.debug("monthly_syntheses fetch failed, Tier-3 inactive: %s", e)
        return []

    from core.design_system import sanitize_summary_html
    out: List[Dict] = []
    for r in rows:
        if theme_filter and r.get("theme_name") != theme_filter:
            continue
        row = dict(r)
        row["macro_summary"] = sanitize_summary_html(row.get("macro_summary", "") or "")
        milestones = []
        for m in (row.get("key_milestones") or []):
            if not isinstance(m, dict):
                continue
            mm = dict(m)
            mm["milestone"] = sanitize_summary_html(str(mm.get("milestone", "") or ""))
            mm["source_link"] = _safe_link(mm.get("source_link"))
            milestones.append(mm)
        row["key_milestones"] = milestones
        out.append(row)
    return out


def _month_end(year_month: str) -> str:
    """Last day of a 'YYYY-MM' month as 'YYYY-MM-DD'."""
    year, month = int(year_month[:4]), int(year_month[5:7])
    return f"{year_month}-{calendar.monthrange(year, month)[1]:02d}"


def _eligible_rollup_months(
    rollups: List[Dict],
    eff_from: Optional[str],
    eff_to: Optional[str],
    recent_dates: set,
    today: Optional[Any] = None,
) -> List[str]:
    """Months eligible for Tier-3 rendering, ascending.

    A month qualifies iff it has >=1 rollup row, is COMPLETED (strictly
    before the current month — the current month's rollup can be up to
    monthly_refresh_days stale, so it always stays Tier-2), is FULLY
    covered by the effective bounds (None = open), and contains none of
    the recent-detail dates (the newest month in any window keeps its
    full/condensed Tier-2 detail — no double representation).
    """
    now = today or datetime.now(timezone.utc).date()
    current_month = now.strftime("%Y-%m")
    recent_months = {d[:7] for d in recent_dates}
    months = sorted({r.get("year_month", "") for r in rollups if r.get("year_month")})
    eligible: List[str] = []
    for m in months:
        if m >= current_month or m in recent_months:
            continue
        if eff_from and f"{m}-01" < eff_from[:10]:
            continue
        if eff_to and _month_end(m) > eff_to[:10]:
            continue
        eligible.append(m)
    return eligible


def _score_rollup(row: Dict, question_kws: List[str]) -> float:
    """Relevance of a rollup: macro prose + milestone sentences vs question."""
    milestone_text = " ".join(
        str(m.get("milestone", "")) for m in (row.get("key_milestones") or [])
        if isinstance(m, dict)
    )
    return score_summary_relevance(
        {
            "what_is_happening": f"{row.get('macro_summary', '')} {milestone_text}",
            "theme_name": row.get("theme_name", ""),
        },
        question_kws,
    )


def _format_month_block(row: Dict, settings: Dict) -> str:
    """Render one Tier-3 month block (macro prose + dated milestones)."""
    first_tag = " [FIRST APPEARANCE]" if row.get("_first_appearance") else ""
    macro = _condense_text(
        row.get("macro_summary", "N/A"),
        int(settings.get("sage_tier3_block_char_limit", 1200)),
    )
    lines = [
        f"--- [{row.get('theme_name', '')}] · Month {row.get('year_month', '')}{first_tag} "
        f"(macro rollup, {row.get('source_run_count', '?')} runs) ---",
        f"Macro: {macro}",
    ]
    milestones = (row.get("key_milestones") or [])[: int(settings.get("sage_tier3_milestones_per_block", 3))]
    if milestones:
        lines.append("Milestones:")
        for m in milestones:
            date_str = str(m.get("date") or "")
            sentence = _condense_text(str(m.get("milestone", "")), 200)
            prefix = f"- {date_str} — " if date_str else "- "
            lines.append(f"{prefix}{sentence}")
            link = m.get("source_link")
            if link:
                lines.append(f"  Link: {link}")
    return "\n".join(lines) + "\n\n"


def _coverage_rollups(
    rollups: List[Dict], eligible_months: List[str], question_kws: List[str],
) -> List[Dict]:
    """Top-scoring rollup per eligible month (the coverage guarantee set),
    ordered by score desc. Used both for the milestone-biased query and as
    round 1 of budgeted selection."""
    best: Dict[str, Dict] = {}
    for r in rollups:
        m = r.get("year_month", "")
        if m not in eligible_months:
            continue
        score = _score_rollup(r, question_kws)
        if m not in best or score > best[m]["_rollup_score"]:
            best[m] = {**r, "_rollup_score": score}
    return sorted(best.values(), key=lambda r: (-r["_rollup_score"], r["year_month"]))


def _select_rollups(
    rollups: List[Dict],
    eligible_months: List[str],
    question_kws: List[str],
    budget: int,
    settings: Dict,
) -> Tuple[List[Dict], int]:
    """Budgeted Tier-3 selection: coverage first, then relevance fill.

    Round 1 renders each eligible month's top rollup (ascending month) so
    every covered month is represented when budget allows; round 2 fills
    the remainder globally by relevance, capped at
    sage_tier3_max_blocks_per_month per month. Returns (selected rows with
    _formatted, chars used).
    """
    if budget < MIN_TIER3_BLOCK or not eligible_months:
        return [], 0
    coverage = _coverage_rollups(rollups, eligible_months, question_kws)
    selected: List[Dict] = []
    used = 0
    seen: set = set()
    per_month: Dict[str, int] = {}

    def _try_add(row: Dict) -> bool:
        nonlocal used
        key = (row.get("year_month"), row.get("theme_name"))
        if key in seen:
            return False
        text = _format_month_block(row, settings)
        if used + len(text) > budget:
            return False  # strict fit — never emit a block over budget
        seen.add(key)
        per_month[row["year_month"]] = per_month.get(row["year_month"], 0) + 1
        selected.append({**row, "_formatted": text})
        used += len(text)
        return True

    for row in sorted(coverage, key=lambda r: r["year_month"]):
        _try_add(row)

    max_per_month = int(settings.get("sage_tier3_max_blocks_per_month", 2))
    scored = [
        {**r, "_rollup_score": _score_rollup(r, question_kws)}
        for r in rollups if r.get("year_month") in eligible_months
    ]
    scored.sort(key=lambda r: (-r["_rollup_score"], r["year_month"], r.get("theme_name", "")))
    for row in scored:
        if per_month.get(row["year_month"], 0) >= max_per_month:
            continue
        _try_add(row)

    selected.sort(key=lambda r: (r["year_month"], r.get("theme_name", "")))
    return selected, used


def _strip_temporal_tokens(text: str) -> str:
    """Remove ISO dates and 4-digit years so milestone text appended to the
    hybrid query cannot poison parse_temporal_bounds' window extraction."""
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", text)
    return re.sub(r"\b(?:19|20)\d{2}\b", "", text)


def _build_milestone_query(question: str, coverage: List[Dict], settings: Dict) -> str:
    """Milestone-biased hybrid query (Phase 6.2 drill-down): the question
    plus the top milestones of the most relevant coverage rollups. Zero
    extra retrieval calls; the hybrid leg only — FTS keeps the raw question.
    """
    if not coverage:
        return question
    top_n = int(settings.get("sage_tier3_query_rollups", 3))
    sentences: List[str] = []
    for row in coverage[:top_n]:
        for m in (row.get("key_milestones") or [])[:2]:
            s = str(m.get("milestone", "")).strip()
            if s:
                sentences.append(_strip_temporal_tokens(s))
    if not sentences:
        return question
    tail = _condense_text(
        " ".join(sentences), int(settings.get("sage_tier3_query_char_limit", 600))
    )
    return f"{question}\nRelated milestone evidence: {tail}" if tail else question


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

    Three grounding layers are packed into the ``max_chars`` budget:
      - Tier-2 wiki summaries, split across two tiers:
        * Recent tier (last ``RECENT_DETAIL_COUNT`` dates): full 3-field blocks.
        * Older tier (all earlier dates): condensed blocks (only
          ``what_is_happening``, truncated to ``CONDENSED_CHAR_LIMIT`` chars
          at sentence boundary).  This guarantees every date in the window
          is represented rather than the latest run's themes greedily
          consuming the whole budget.
      - Tier-3 monthly rollups (Phase 6.2): older COMPLETED months with
        ``monthly_syntheses`` rows render as one macro block per theme
        (prose + dated milestones with real links) instead of per-date
        condensed blocks, extending macro reach without raising ``limit``.
        The current month always stays Tier-2 (its rollup can be stale);
        months without rollups keep condensed blocks.  Active only for the
        real Supabase client, no source filter, and >=1 eligible month
        (the full-coverage + recent-exclusion rules keep single-month
        windows unchanged); ``sage_tier3_enabled`` is the kill switch.
      - Tier-1 article evidence (Phase 2.1): a bounded candidate window is
        fetched and the top matches rendered as compact briefs within an
        ``ARTICLE_BUDGET_SHARE`` slice.  When Tier 3 is active the hybrid
        leg receives a milestone-biased query (question + top rollup
        milestones — the 6.2 drill-down); the FTS fallback keeps the raw
        question.  Unused article budget flows back to the wiki section.

    Returns:
        A dict ``{"context": str, "run_count": int, "date_count": int,
        "date_range": str, "article_count": int, "articles": List[Dict],
        "tier3_block_count": int, "tier3_months": List[str]}`` so the caller
        can surface how far Sage actually reached.  The ``articles`` list
        (Phase 2.2) carries the exact briefs rendered into the context —
        title, source, date, sanitised link, snippet — so the UI can show an
        evidence popover per reply.
    """
    from collections import defaultdict
    from config.settings import get_sage_settings
    question_kws = _tokenise(question)
    settings = get_sage_settings()

    # ------- Tier-3 activation & effective temporal bounds -------
    # Rollups aggregate across sources, so a source-scoped question must
    # not get cross-source macro content.  MagicMock stubs (legacy tests)
    # fail the isinstance guard and keep the exact pre-6.2 path.
    tier3_active = False
    try:
        from core.supabase_client import SupabaseManager
        tier3_active = (
            bool(settings.get("sage_tier3_enabled", True))
            and supabase is not None
            and isinstance(supabase, SupabaseManager)
            and supabase.is_available()
            and not source_filter
        )
    except Exception:
        tier3_active = False

    eff_from, eff_to = date_from, date_to
    if tier3_active:
        # Intersect UI bounds with the question's own temporal expression
        # (same rule hybrid_search applies internally — idempotent there).
        try:
            from core.retrieval import parse_temporal_bounds
            parsed_from, parsed_to = parse_temporal_bounds(question)
            froms = [x for x in (date_from, parsed_from) if x]
            tos = [x for x in (date_to, parsed_to) if x]
            eff_from = max(froms)[:10] if froms else None
            eff_to = min(tos)[:10] if tos else None
        except Exception as e:
            logger.debug("Question temporal-bounds parse failed: %s", e)

    # ------- Fetch Tier-2 summaries (effective bounds) -------
    summaries: List[Dict] = []

    if supabase and supabase.is_available():
        raw = supabase.get_summaries_across_runs(
            theme_filter=theme_filter,
            date_from=eff_from,
            date_to=eff_to,
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
                if eff_from and run_date < eff_from[:10]:
                    continue
                if eff_to and run_date > eff_to[:10]:
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

    # ------- Sanitise, score, group by date (chronological ASC) -------
    by_date: Dict[str, List[Dict]] = defaultdict(list)
    dates: List[str] = []
    recent_dates: set = set()
    if summaries:
        from core.design_system import sanitize_summary_html
        for s in summaries:
            s["what_is_happening"] = sanitize_summary_html(s.get("what_is_happening", ""))
            s["why_it_matters"] = sanitize_summary_html(s.get("why_it_matters", ""))
            s["what_to_watch"] = sanitize_summary_html(s.get("what_to_watch", ""))
            s["_relevance"] = score_summary_relevance(s, question_kws)
        for s in summaries:
            by_date[s["run_date"]].append(s)
        dates = sorted(by_date.keys())
        recent_dates = set(dates[-RECENT_DETAIL_COUNT:])

    # ------- Tier-3 candidates & activation -------
    # Eligible months come from the rollup TABLE (not the fetched Tier-2
    # dates): the limit-truncated Tier-2 window may not reach them at all —
    # extending that reach is the point of Tier 3.  Activation needs >=1
    # eligible month; the full-coverage + recent-dates-exclusion rules
    # already keep single-month windows (Current Month / Last 30 Days /
    # one-month questions) unchanged.  Do NOT gate on the fetched window's
    # month span: a Full Archive fetch truncated to one month by `limit`
    # is precisely the case Tier 3 must extend (6.2 gate finding).
    rollups: List[Dict] = []
    eligible_months: List[str] = []
    coverage: List[Dict] = []
    if tier3_active:
        rollups = _fetch_monthly_rollups(supabase, theme_filter)
        if rollups:
            eligible_months = _eligible_rollup_months(rollups, eff_from, eff_to, recent_dates)
            if eligible_months:
                coverage = _coverage_rollups(rollups, eligible_months, question_kws)

    # ------- Fetch Tier-1 article candidates & render within budget -------
    # The raw question goes to SQL (websearch_to_tsquery does its own
    # parsing); the tokenised keywords are for client-side scoring.  The
    # milestone-biased query (Phase 6.2) drives the hybrid leg only.
    hybrid_query = _build_milestone_query(question, coverage, settings) if coverage else None
    article_candidates = _fetch_article_candidates(
        supabase, history, theme_filter, eff_from, eff_to, source_filter, question,
        hybrid_query=hybrid_query,
    )
    selected_articles = _select_articles(article_candidates, question_kws)
    article_section, article_list = _render_article_section(
        selected_articles, int(max_chars * ARTICLE_BUDGET_SHARE)
    )
    article_count = len(article_list)
    # Wiki gets whatever budget the article section didn't consume; with no
    # articles found it keeps the full budget (existing behaviour).
    wiki_max = max_chars - len(article_section)

    # ------- Split budget: recent / Tier-3 / condensed older -------
    recent_dates_list = [d for d in dates if d in recent_dates]
    older_dates_all = [d for d in dates if d not in recent_dates]

    recent_budget = int(wiki_max * RECENT_BUDGET_SHARE) if dates else 0
    older_budget = wiki_max - recent_budget

    eligible_set = set(eligible_months)
    selected_rollups: List[Dict] = []
    tier3_chars = 0
    if eligible_months:
        prospective_older = [d for d in older_dates_all if d[:7] not in eligible_set]
        tier3_budget = (
            older_budget if not prospective_older
            else int(older_budget * float(settings.get("sage_tier3_budget_share", 0.6)))
        )
        selected_rollups, tier3_chars = _select_rollups(
            rollups, eligible_months, question_kws, tier3_budget, settings
        )
    # Only months that ACTUALLY rendered lose their condensed blocks — a
    # budget-starved Tier-3 selection must not silently drop Tier-2 content.
    rendered_months = {r["year_month"] for r in selected_rollups}
    older_dates_list = [d for d in older_dates_all if d[:7] not in rendered_months]
    condensed_budget = older_budget - tier3_chars
    tier3_months = sorted(rendered_months)

    per_recent_date = recent_budget // max(len(recent_dates_list), 1)
    per_older_date = condensed_budget // max(len(older_dates_list), 1)

    # ------- Early exits (no Tier-2 data) -------
    if not summaries and not selected_rollups:
        if article_section:
            return {
                "context": article_section,
                "run_count": 0, "date_count": 0, "date_range": "",
                "article_count": article_count, "articles": article_list,
                "tier3_block_count": 0, "tier3_months": [],
            }
        return {
            "context": "[No wiki data available for the selected filters.]",
            "run_count": 0, "date_count": 0, "date_range": "",
            "article_count": 0, "articles": [],
            "tier3_block_count": 0, "tier3_months": [],
        }

    # Minimum block sizes used as a guard: if a date's per-date quota is smaller
    # than this, we skip that date rather than emit a half-formed block.
    MIN_FULL_BLOCK = 800
    MIN_CONDENSED_BLOCK = 200

    selected: List[Dict] = []
    char_count = 0

    for date in dates:
        if date not in recent_dates and date[:7] in rendered_months:
            continue  # this month is represented by its Tier-3 block(s)
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
    # Phase 5.4: with the theme_first_seen table available, a block is a
    # first appearance only when its run date IS the theme's true archive
    # first-seen date — not merely the earliest block in this window (the
    # old scan mislabelled every window's oldest run). Offline / pre-migration
    # databases keep the legacy window scan.
    selected.sort(key=lambda s: s["run_timestamp"])
    first_seen_map: Dict[str, Dict] = {}
    if supabase and supabase.is_available():
        try:
            fetched = supabase.get_theme_first_seen()
            if isinstance(fetched, dict):
                first_seen_map = fetched
        except Exception as e:
            logger.debug("theme_first_seen lookup failed, using window scan: %s", e)
    if first_seen_map:
        for s in selected:
            fs = first_seen_map.get(s.get("theme_name", ""))
            s["_first_appearance"] = bool(
                fs and s.get("run_date", "") == str(fs.get("first_seen_date", ""))[:10]
            )
    else:
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

    # Tier-3 month blocks: FIRST APPEARANCE when the theme's true archive
    # first-seen month IS this rollup's month (no window-scan equivalent —
    # rollups span the whole month by construction).
    for r in selected_rollups:
        fs = first_seen_map.get(r.get("theme_name", ""))
        r["_first_appearance"] = bool(
            fs and str(fs.get("first_seen_date", ""))[:7] == r.get("year_month")
        )
        r["_formatted"] = _format_month_block(r, settings)

    # ------- Assemble output with metadata header -------
    date_range_str = (
        f"{dates[0]} → {dates[-1]}" if len(dates) > 1 else (dates[0] if dates else "")
    )
    run_count = len(summaries)
    date_count = len(dates)
    rollup_clause = ""
    if tier3_months:
        rollup_range = (
            f"{tier3_months[0]} → {tier3_months[-1]}"
            if len(tier3_months) > 1 else tier3_months[0]
        )
        rollup_clause = f" · {len(selected_rollups)} monthly rollups ({rollup_range})"
    metadata = (
        f"[Sage context: {run_count} runs across {date_count} dates, "
        f"{date_range_str}{rollup_clause}]\n\n"
    )

    # Chronological merge: Tier-2 blocks sort by run_timestamp; month blocks
    # key on 'YYYY-MM-00' so a rollup precedes any later-month run block.
    # Eligible months contain no Tier-2 blocks, so there is no interleaving
    # conflict within a month.
    merged: List[Tuple[str, str]] = [(s["run_timestamp"], s["_formatted"]) for s in selected]
    merged += [(r["year_month"] + "-00", r["_formatted"]) for r in selected_rollups]
    merged.sort(key=lambda t: t[0])

    lines: List[str] = ["=== WIKI CONTEXT (chronological, time-decayed resolution) ===\n", metadata]
    for _, text in merged:
        lines.append(text)
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
        "tier3_block_count": len(selected_rollups),
        "tier3_months": tier3_months,
    }


# ---------------------------------------------------------------------------
# Chat interface
# ---------------------------------------------------------------------------

_ENGINE_UNAVAILABLE_MSG = (
    "I'm having trouble connecting to my analysis engine right now. "
    "Please try again in a moment."
)

_QUOTA_MARKERS = (
    "429",
    "quota",
    "resource_exhausted",
    "rate limit",
    "usage limit",
)


def _all_quota_failures(reasons: Dict[str, str]) -> bool:
    """True only when every failed model reported a quota/rate-limit error."""
    if not reasons:
        return False
    return all(
        any(marker in str(reason).lower() for marker in _QUOTA_MARKERS)
        for reason in reasons.values()
    )


def _short_failure(reason: str) -> str:
    """Condense a gateway failure reason for the Sage notice."""
    text = str(reason)
    # Drop the leading "retryable: " / "non_retryable: " classification token.
    if ":" in text:
        text = text.split(":", 1)[1]
    text = " ".join(text.split())
    if len(text) > 90:
        text = text[:87].rstrip() + "..."
    return text


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
    fallback chain (Qwen Cloud primary, Gemini fallbacks), and a
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

    # Phase 8: improvement-loop prompt additions (exemplars + guard).
    from config.settings import get_sage_settings
    from core.sage_exemplars import build_exemplar_block
    _sage_settings = get_sage_settings()
    persona = SAGE_SYSTEM_PROMPT
    if _sage_settings.get("sage_strict_grounding"):
        persona += (
            "\n- STRICT GROUNDING MANDATE (auto-engaged: recent faithfulness dipped): "
            "every claim must be explicitly supported by the context below — cite it "
            "or drop it. Do NOT extrapolate, generalize, or add framing the sources "
            "do not state."
        )
    exemplars = build_exemplar_block() if _sage_settings.get("sage_exemplars_enabled", True) else ""
    exemplar_block = (exemplars + "\n\n") if exemplars else ""

    # Build the full system prompt with wiki context injected
    system = f"{persona}\n\n{exemplar_block}{context_str}"

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

    # Guard strict mode caps temperature (lower randomness while the
    # rolling faithfulness baseline is dipped).
    _temp = 0.4
    if _sage_settings.get("sage_strict_grounding"):
        from core.sage_guard import TARGET_TEMP
        _temp = min(_temp, TARGET_TEMP)

    request = AITaskRequest(
        task=TaskType.SAGE_CHAT,
        input=full_prompt,
        temperature=_temp,
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
    # for sage_chat).  The last error alone is misleading: a Gemini 429
    # following a Qwen timeout does not mean every engine was quota-limited.
    # Use the gateway's per-model failure map to report what actually broke.
    error = result.provenance.error or str(result.result)
    reasons = result.provenance.failure_reasons or {}
    logger.warning(
        "Sage gateway failed on all models: %s (reasons: %s)", error, reasons
    )
    if _all_quota_failures(reasons) or (
        not reasons and ("429" in error or "quota" in error.lower())
    ):
        return {
            "response": (
                "⚠️ *Sage Notice: All engines are quota-limited right now (HTTP 429). "
                "Please check API quotas or try again later.*"
            ),
            "provenance": result.provenance.to_dict(),
        }
    if reasons:
        details = "; ".join(
            f"{model}: {_short_failure(reason)}"
            for model, reason in reasons.items()
        )
        return {
            "response": (
                "⚠️ *Sage Notice: All engines failed.* "
                f"{details[:500]}. Please try again in a moment."
            ),
            "provenance": result.provenance.to_dict(),
        }
    return {"response": _ENGINE_UNAVAILABLE_MSG, "provenance": result.provenance.to_dict()}
