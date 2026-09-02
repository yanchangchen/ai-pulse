"""
LLM summarisation module for AI Pulse.
Generates theme summaries using the Model Gateway with routing, fallback, and provenance.
"""

import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Optional

from config.themes import THEMES, THEME_ORDER
from core.llm_client import LLMClient, LLMClientError
from core.gemini_client import GeminiClient, GeminiClientError, GeminiQuotaError
from core.ai_gateway import ModelGateway, get_gateway, AITaskRequest, TaskType
import core.history_manager as history_manager
from core.history_manager import get_recent_context, save_run_to_history

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Shared LLM client instance (initialised lazily) - kept for backward compat
_llm: Optional[LLMClient] = None

# After this many consecutive empty-response failures within a single run,
# treat the LLM as degraded and route the remaining themes through the
# non-LLM extractive fallback.  Set conservatively — one transient blip
# should not poison the rest of the run.
_EMPTY_FAIL_DEGRADE_THRESHOLD = 2


def _get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm


def _with_provenance(summary: Dict[str, str], source: str, **log_fields) -> Dict[str, str]:
    """Attach machine-readable provenance (`_source`, `_generation_log`) to a summary dict.

    `source` is a short token the UI uses to render a provenance chip — one of:
    * `"extractive_fallback"` — non-LLM path (Ollama quota exceeded, empty response, transport error)
    * `"ollama:<model>"`     — live Ollama synthesis
    * `"gemini:<model>"`     — on-demand Gemini synthesis
    """
    summary["_source"] = source
    log: Dict[str, object] = {
        "source": source,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    log.update(log_fields)
    summary["_generation_log"] = log
    return summary


def _provenance_to_dict(provenance) -> Dict:
    """Convert Provenance object to dict for storage."""
    if hasattr(provenance, 'to_dict'):
        return provenance.to_dict()
    elif isinstance(provenance, dict):
        return provenance
    else:
        return {"source": str(provenance)}


async def generate_theme_summary_gateway(
    theme_name: str,
    articles: List[Dict],
) -> Dict[str, str]:
    """Generate a comprehensive summary for a theme using the Model Gateway."""

    if not articles:
        summary = {
            "what_is_happening": "No articles found for this theme in the past two weeks.",
            "why_it_matters": "Limited coverage this week.",
            "what_to_watch": "Check back next week for updates.",
            "further_reading": ""
        }
        return _with_provenance(
            summary, source="gateway:no_articles",
            model="none", article_count=0,
            note="empty_article_pool",
        )

    # Retrieve memory context
    past_context = get_recent_context(theme_name)

    if len(articles) < 3:
        summary = {
            "what_is_happening": f"Limited coverage this week with only {len(articles)} articles found. {past_context}",
            "engineering_tradeoffs": "Limited news signal this week.",
            "product_impact": "Limited news signal this week.",
            "why_it_matters": "This theme has fewer articles this week, possibly indicating lower activity or a quiet period.",
            "what_to_watch": "Monitor for upcoming announcements and developments.",
            "further_reading": ""
        }
        return _with_provenance(
            summary, source="gateway:limited_coverage",
            model="none", article_count=len(articles),
            note="fewer_than_3_articles",
        )

    # Rank articles by relevance to the theme, then format.
    from config.settings import (
        MAX_ARTICLES_PER_SUMMARY,
        OLLAMA_NUM_CTX,
        CHARS_PER_TOKEN,
        INPUT_BUDGET_FRACTION,
    )
    ranked_articles = _rank_articles_by_relevance(
        articles, theme_name, MAX_ARTICLES_PER_SUMMARY,
    )
    char_budget = int(OLLAMA_NUM_CTX * CHARS_PER_TOKEN * INPUT_BUDGET_FRACTION)
    formatted_articles = format_articles_for_prompt(
        ranked_articles, char_budget=char_budget,
    )

    num_articles = len(ranked_articles)
    if num_articles > 30:
        signal_instruction = (
            f"Write a comprehensive, detailed factual narrative of 6–10 sentences synthesizing the key trends from these {num_articles} developments. "
            "Do NOT use bullet points. Lead with the single most significant theme or development. "
            "When presenting key developments, cite the source name in parentheses next to the fact, model, or release (e.g. '(Anthropic)' or '(Databricks)'). "
            "End with a synthesis of the broader directional shift this signals. Target 180–250 words."
        )
    elif num_articles > 15:
        signal_instruction = (
            f"Write a detailed factual narrative of 5–8 sentences synthesizing these {num_articles} developments. "
            "Do NOT use bullet points. Lead with the single most significant theme or development. "
            "When presenting key developments, cite the source name in parentheses next to the fact, model, or release (e.g. '(Anthropic)' or '(Databricks)'). "
            "End with a sentence on the broader directional shift this signals. Target 120–180 words."
        )
    else:
        signal_instruction = (
            "Write 4–6 sentences as a tight factual narrative — no bullet points. Lead with the single most significant development. "
            "When presenting key developments, cite the source name in parentheses next to the fact, model, or release (e.g. '(Anthropic)' or '(Databricks)'). "
            "End with a sentence on the broader directional shift this signals. Target 80–120 words."
        )

    user_prompt = f"""You are analyzing AI news summaries from the past two weeks, focused on the theme: {theme_name}

--- SOURCE ARTICLES ---
{formatted_articles}

{f"--- PRIOR CONTEXT ---\n{past_context}" if past_context else ""}
--- END OF SOURCES ---

Produce a rigorous technical intelligence brief using the exact structure below.

---

## 1. WHAT IS HAPPENING
{signal_instruction}
{"Explicitly call out what is NEW or EVOLVED since the prior brief." if past_context else ""}

## 2. ENGINEERING TRADEOFFS & BLUEPRINT
*Audience: AI Engineers*
Write 4–6 sentences as flowing prose. Cover: architectural patterns, API changes, performance parameters (latency / memory / cost), open-weight licenses, or framework upgrades. Close with the core technical tradeoff a practitioner must weigh. Target 80–120 words.

## 3. PRODUCT IMPACT & FEASIBILITY
*Audience: Product Managers*
Write 4–6 sentences as flowing prose. Address: speed-to-market, pricing margins, integration overhead, safety/compliance risks, and competitor capability shifts. Close with a direct verdict: is this production-ready for enterprise use, and under what conditions? Target 80–120 words.

## 4. ACTIONABLE WATCHLIST
List exactly 3–5 items. Each item must follow this format:
  - **[Item]** — one sentence explaining why it matters and when to act.

Focus on: upcoming API breaking changes, benchmark releases, regulatory deadlines, or high-signal research papers. Every watchlist item MUST reference a specific upcoming event, deadline, release, or research paper found in the SOURCE ARTICLES — do not invent entity names or generic industry trends.

## 5. STRATEGIC FURTHER READING
List exactly 5 articles from the sources above. You MUST select the most significant articles from the SOURCE ARTICLES, prioritizing those you referenced or drew from in Section 1 (What is Happening) and Section 2 (Engineering Tradeoffs). Format each entry as:
  - **[Article Title]** | [Source] | [URL]
    *Why read this:* one sentence stating the concrete technical or product takeaway.

IMPORTANT: Copy each URL VERBATIM from the URL field of the matching article in the SOURCE ARTICLES. Never paraphrase, shorten, or invent URLs.

If you would otherwise produce an empty section, say so explicitly rather than omitting it.
"""

    system_prompt = """You are an expert AI engineering analyst and product strategist writing for senior tech leaders.

Your role is to cut through noise and surface what actually matters — architectural shifts, API stability, performance tradeoffs, and product feasibility.

Writing style rules:
- Be precise, direct, and technically rigorous
- No hype, buzzwords, or vague generalizations
- Write prose sections in flowing paragraphs
- Use bullet points only in sections 4 and 5
- Bold key terms, model names, and company names on first mention
- Never start two consecutive sentences with the same word
"""

    from config.settings import get_summariser_settings
    sum_settings = get_summariser_settings()

    if sum_settings.get("strict_faithfulness_mode"):
        system_prompt += (
            "\n- STRICT FAITHFULNESS MANDATE: Every claim must be explicitly supported by the provided SOURCE ARTICLES. "
            "Do NOT extrapolate, infer unmentioned facts, or invent details."
        )

    # Use the Model Gateway for summarisation
    gateway = get_gateway()

    # Build input with system prompt prepended
    full_prompt = f"System: {system_prompt}\n\nUser: {user_prompt}"

    request = AITaskRequest(
        task=TaskType.SUMMARISE,
        input=full_prompt,
        temperature=float(sum_settings.get("temperature", 0.3)),
        max_tokens=int(sum_settings.get("max_tokens", 2200)),
    )

    try:
        result = await gateway.execute(request)

        if result.is_success():
            # Parse the result - it should be the structured summary
            parsed = _parse_summary_sections(str(result.result))

            # Extract provenance info
            prov = result.provenance
            source_str = f"{prov.provider}:{prov.model}" if prov.provider else f"deterministic:{prov.task}"

            return _with_provenance(
                parsed,
                source=source_str,
                model=prov.model or "deterministic",
                article_count=len(articles),
                note="live_synthesis" if prov.method == "llm" else "deterministic_fallback",
                latency_ms=prov.latency_ms,
                attempts=prov.attempts,
                fallback_used=prov.fallback_used,
                provenance=prov.to_dict(),
            )
        else:
            raise Exception(result.provenance.error or "Unknown error")

    except Exception as exc:
        logger.error("Error generating summary for %s: %s", theme_name, exc)
        summary = {
            "what_is_happening": f"Error generating summary: {exc}",
            "engineering_tradeoffs": "Unable to analyze due to error.",
            "product_impact": "Unable to analyze due to error.",
            "why_it_matters": "Unable to analyze at this time.",
            "what_to_watch": "Please try again later.",
            "further_reading": ""
        }
        return _with_provenance(
            summary, source="gateway:error",
            model="none", article_count=len(articles),
            note="gateway_error",
            error=str(exc),
        )


def _rank_articles_by_relevance(
    articles: List[Dict],
    theme_name: str,
    top_n: int,
) -> List[Dict]:
    """Sort articles by relevance to the theme using the same scoring as the
    extractive fallback, then return the top-n.  Ties break by insertion order
    so the LLM sees a stable corpus across runs.

    Re-using ``non_llm_summariser._score_article_relevance`` keeps the Ollama,
    Gemini, and extractive paths aligned on what counts as a high-signal article.
    """
    from core.non_llm_summariser import _score_article_relevance
    theme_kws = THEMES.get(theme_name, {}).get("keywords")
    if not theme_kws:
        return list(articles[:top_n])
    scored = [
        (idx, _score_article_relevance(a, theme_kws))
        for idx, a in enumerate(articles)
    ]
    # Sort by score desc, then by original index asc (stable)
    scored.sort(key=lambda x: (-x[1], x[0]))
    return [articles[idx] for idx, _ in scored[:top_n]]


def format_articles_for_prompt(articles: List[Dict], char_budget: Optional[int] = None) -> str:
    """Format articles for the summarisation prompt.

    Each article is capped at ~500 chars (title + 300-char summary + source +
    link).  If `char_budget` is provided, articles are appended in order
    until the budget is reached — any tail articles are dropped so the
    joined text stays inside the LLM's input window.

    Callers should derive `char_budget` from the model's num_ctx
    (see config.settings.CHARS_PER_TOKEN and INPUT_BUDGET_FRACTION).
    """
    formatted: List[str] = []

    for i, article in enumerate(articles, 1):
        title = article.get('title', 'Untitled')
        summary = article.get('summary', '')[:300]  # Limit length
        source = article.get('source_name', 'Unknown')
        link = article.get('link', '')

        block_parts = [f"{i}. {title}", f"   Source: {source}"]
        if summary:
            block_parts.append(f"   Summary: {summary}")
        if link:
            block_parts.append(f"   URL: {link}")
        block_parts.append("")  # trailing blank line
        block = "\n".join(block_parts)

        if char_budget is not None:
            # Reserve one extra char for the join-newline we'll add.
            projected = sum(len(p) + 1 for p in formatted) + len(block)
            if projected > char_budget:
                break

        formatted.append(block)

    return "\n".join(formatted)


def generate_theme_summary(
    theme_name: str,
    articles: List[Dict],
) -> Dict[str, str]:
    """Generate a comprehensive summary for a theme using the LLM client."""

    if not articles:
        summary = {
            "what_is_happening": "No articles found for this theme in the past two weeks.",
            "why_it_matters": "Limited coverage this week.",
            "what_to_watch": "Check back next week for updates.",
            "further_reading": ""
        }
        return _with_provenance(
            summary, source="ollama:no_articles",
            model=LLMClient().model, article_count=0,
            note="empty_article_pool",
        )

    # Retrieve memory context
    past_context = get_recent_context(theme_name)

    if len(articles) < 3:
        summary = {
            "what_is_happening": f"Limited coverage this week with only {len(articles)} articles found. {past_context}",
            "engineering_tradeoffs": "Limited news signal this week.",
            "product_impact": "Limited news signal this week.",
            "why_it_matters": "This theme has fewer articles this week, possibly indicating lower activity or a quiet period.",
            "what_to_watch": "Monitor for upcoming announcements and developments.",
            "further_reading": ""
        }
        return _with_provenance(
            summary, source="ollama:limited_coverage",
            model=LLMClient().model, article_count=len(articles),
            note="fewer_than_3_articles",
        )

    # Rank articles by relevance to the theme, then format.  Cap at
    # MAX_ARTICLES_PER_SUMMARY and then truncate further to stay inside
    # the LLM's input budget.
    from config.settings import (
        MAX_ARTICLES_PER_SUMMARY,
        OLLAMA_NUM_CTX,
        CHARS_PER_TOKEN,
        INPUT_BUDGET_FRACTION,
    )
    ranked_articles = _rank_articles_by_relevance(
        articles, theme_name, MAX_ARTICLES_PER_SUMMARY,
    )
    char_budget = int(OLLAMA_NUM_CTX * CHARS_PER_TOKEN * INPUT_BUDGET_FRACTION)
    formatted_articles = format_articles_for_prompt(
        ranked_articles, char_budget=char_budget,
    )

    num_articles = len(ranked_articles)
    if num_articles > 30:
        signal_instruction = (
            f"Write a comprehensive, detailed factual narrative of 6–10 sentences synthesizing the key trends from these {num_articles} developments. "
            "Do NOT use bullet points. Lead with the single most significant theme or development. "
            "When presenting key developments, cite the source name in parentheses next to the fact, model, or release (e.g. '(Anthropic)' or '(Databricks)'). "
            "End with a synthesis of the broader directional shift this signals. Target 180–250 words."
        )
    elif num_articles > 15:
        signal_instruction = (
            f"Write a detailed factual narrative of 5–8 sentences synthesizing these {num_articles} developments. "
            "Do NOT use bullet points. Lead with the single most significant theme or development. "
            "When presenting key developments, cite the source name in parentheses next to the fact, model, or release (e.g. '(Anthropic)' or '(Databricks)'). "
            "End with a sentence on the broader directional shift this signals. Target 120–180 words."
        )
    else:
        signal_instruction = (
            "Write 4–6 sentences as a tight factual narrative — no bullet points. Lead with the single most significant development. "
            "When presenting key developments, cite the source name in parentheses next to the fact, model, or release (e.g. '(Anthropic)' or '(Databricks)'). "
            "End with a sentence on the broader directional shift this signals. Target 80–120 words."
        )

    user_prompt = f"""You are analyzing AI news summaries from the past two weeks, focused on the theme: {theme_name}

--- SOURCE ARTICLES ---
{formatted_articles}

{f"--- PRIOR CONTEXT ---\n{past_context}" if past_context else ""}
--- END OF SOURCES ---

Produce a rigorous technical intelligence brief using the exact structure below.

---

## 1. WHAT IS HAPPENING
{signal_instruction}
{"Explicitly call out what is NEW or EVOLVED since the prior brief." if past_context else ""}

## 2. ENGINEERING TRADEOFFS & BLUEPRINT
*Audience: AI Engineers*
Write 4–6 sentences as flowing prose. Cover: architectural patterns, API changes, performance parameters (latency / memory / cost), open-weight licenses, or framework upgrades. Close with the core technical tradeoff a practitioner must weigh. Target 80–120 words.

## 3. PRODUCT IMPACT & FEASIBILITY
*Audience: Product Managers*
Write 4–6 sentences as flowing prose. Address: speed-to-market, pricing margins, integration overhead, safety/compliance risks, and competitor capability shifts. Close with a direct verdict: is this production-ready for enterprise use, and under what conditions? Target 80–120 words.

## 4. ACTIONABLE WATCHLIST
List exactly 3–5 items. Each item must follow this format:
  - **[Item]** — one sentence explaining why it matters and when to act.

Focus on: upcoming API breaking changes, benchmark releases, regulatory deadlines, or high-signal research papers. Every watchlist item MUST reference a specific upcoming event, deadline, release, or research paper found in the SOURCE ARTICLES — do not invent entity names or generic industry trends.

## 5. STRATEGIC FURTHER READING
List exactly 5 articles from the sources above. You MUST select the most significant articles from the SOURCE ARTICLES, prioritizing those you referenced or drew from in Section 1 (What is Happening) and Section 2 (Engineering Tradeoffs). Format each entry as:
  - **[Article Title]** | [Source] | [URL]
    *Why read this:* one sentence stating the concrete technical or product takeaway.

IMPORTANT: Copy each URL VERBATIM from the URL field of the matching article in the SOURCE ARTICLES. Never paraphrase, shorten, or invent URLs.

If you would otherwise produce an empty section, say so explicitly rather than omitting it.
"""

    system_prompt = """You are an expert AI engineering analyst and product strategist writing for senior tech leaders.

Your role is to cut through noise and surface what actually matters — architectural shifts, API stability, performance tradeoffs, and product feasibility.

Writing style rules:
- Be precise, direct, and technically rigorous
- No hype, buzzwords, or vague generalizations
- Write prose sections in flowing paragraphs
- Use bullet points only in sections 4 and 5
- Bold key terms, model names, and company names on first mention
- Never start two consecutive sentences with the same word
"""

    from config.settings import get_summariser_settings
    sum_settings = get_summariser_settings()

    if sum_settings.get("strict_faithfulness_mode"):
        system_prompt += (
            "\n- STRICT FAITHFULNESS MANDATE: Every claim must be explicitly supported by the provided SOURCE ARTICLES. "
            "Do NOT extrapolate, infer unmentioned facts, or invent details."
        )

    try:
        llm = _get_llm()
        content = llm.generate(
            user_prompt,
            system=system_prompt,
            temperature=float(sum_settings.get("temperature", 0.3)),
            max_tokens=int(sum_settings.get("max_tokens", 2200)),
        )

        parsed = _parse_summary_sections(content)
        return _with_provenance(
            parsed, source=f"ollama:{llm.model}",
            model=llm.model, article_count=len(articles),
            note="live_synthesis",
        )

    except LLMClientError as exc:
        logger.error("Error generating summary for %s: %s", theme_name, exc)
        summary = {
            "what_is_happening": f"Error generating summary: {exc}",
            "engineering_tradeoffs": "Unable to analyze due to error.",
            "product_impact": "Unable to analyze due to error.",
            "why_it_matters": "Unable to analyze at this time.",
            "what_to_watch": "Please try again later.",
            "further_reading": ""
        }
        return _with_provenance(
            summary, source="ollama:error",
            model=LLMClient().model, article_count=len(articles),
            note="llm_client_error",
            error=str(exc),
        )


def _parse_summary_sections(content: str) -> Dict[str, str]:
    """Parse structured markdown response into summary dictionary sections.

    Preserves newline structure so that bullet lists, paragraph breaks,
    and markdown formatting survive into the UI.
    """
    import re

    # Clean markdown wrapper if LLM returned it
    content = content.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()

    # Map of keyword fragments → internal section key
    _SECTION_MARKERS = [
        ('WHAT IS HAPPENING',           'what_is_happening'),
        ('THE SIGNAL',                  'what_is_happening'),
        ('SIGNAL',                      'what_is_happening'),
        ('ENGINEERING TRADEOFFS',       'engineering_tradeoffs'),
        ('ENGINEERING BLUEPRINT',       'engineering_tradeoffs'),
        ('TECHNICAL BLUEPRINT',         'engineering_tradeoffs'),
        ('ENGINEERING',                 'engineering_tradeoffs'),
        ('TECHNICAL',                   'engineering_tradeoffs'),
        ('TRADEOFF',                    'engineering_tradeoffs'),
        ('BLUEPRINT',                   'engineering_tradeoffs'),
        ('PRODUCT FEASIBILITY',         'product_impact'),
        ('PRODUCT IMPACT',              'product_impact'),
        ('PRODUCT',                     'product_impact'),
        ('FEASIBILITY',                 'product_impact'),
        ('WHY IT MATTERS',              'why_it_matters'),
        ('SIGNIFICANCE',                'why_it_matters'),
        ('ACTIONABLE WATCHLIST',        'what_to_watch'),
        ('WHAT TO WATCH',               'what_to_watch'),
        ('WATCHLIST',                   'what_to_watch'),
        ('WATCH',                       'what_to_watch'),
        ('OUTLOOK',                     'what_to_watch'),
        ('STRATEGIC FURTHER READING',   'further_reading'),
        ('FURTHER READING',             'further_reading'),
        ('READING',                     'further_reading'),
        ('CITED SOURCES',               'further_reading'),
        ('SOURCES',                     'further_reading'),
        ('REFERENCES',                  'further_reading'),
    ]

    sections: Dict[str, str] = {}
    current_section: Optional[str] = None
    current_content: List[str] = []

    def _flush():
        """Save accumulated lines to the current section."""
        nonlocal current_section, current_content
        if current_section is not None:
            # Strip leading/trailing blank lines but preserve internal structure
            text = '\n'.join(current_content).strip()
            sections[current_section] = text
            current_content = []

    for raw_line in content.split('\n'):
        stripped = raw_line.strip()
        upper = stripped.upper()

        # A line is considered a header if it starts with markdown heading, bold/italic markers,
        # or matches numbered patterns (e.g., "1. What is Happening") with few words.
        is_header = False
        if stripped.startswith('#') or (stripped.startswith('**') and stripped.endswith('**')):
            is_header = True
        elif re.match(r'^\d+\.\s+[A-Za-z]', stripped) and len(stripped.split()) < 7:
            is_header = True
        elif stripped.isupper() and len(stripped) < 40 and len(stripped) > 2:
            is_header = True

        matched_section = None
        if is_header:
            for marker, key in _SECTION_MARKERS:
                if marker in upper:
                    matched_section = key
                    break

        if matched_section is not None:
            _flush()
            current_section = matched_section
            # If the heading line itself has trailing prose after a ':', keep it
            if ':' in stripped:
                after_colon = stripped.split(':', 1)[-1].strip()
                # Ignore if only a markdown header residue (e.g. "##")
                if after_colon and not after_colon.startswith('#') and not after_colon.endswith('**'):
                    current_content.append(after_colon)
        elif current_section is not None:
            # Skip sub-header markers like "*Audience: AI Engineers*"
            if stripped.startswith('*Audience'):
                continue
            # Keep the line (including blank lines to preserve paragraphs)
            current_content.append(raw_line.rstrip())

    _flush()

    engineering_txt = sections.get('engineering_tradeoffs', '').strip()
    product_txt = sections.get('product_impact', '').strip()

    why_it_matters_composite = sections.get('why_it_matters', '').strip()
    if not why_it_matters_composite and (engineering_txt or product_txt):
        parts = []
        if engineering_txt:
            parts.append(f"**Engineering Blueprint:** {engineering_txt}")
        if product_txt:
            parts.append(f"**Product Feasibility:** {product_txt}")
        why_it_matters_composite = "\n\n".join(parts)

    if not why_it_matters_composite:
        why_it_matters_composite = "Unable to generate significance analysis."

    return {
        "what_is_happening": sections.get('what_is_happening', 'Unable to generate summary.'),
        "engineering_tradeoffs": engineering_txt if engineering_txt else "No engineering tradeoffs analyzed.",
        "product_impact": product_txt if product_txt else "No product impact analyzed.",
        "why_it_matters": why_it_matters_composite,
        "what_to_watch": sections.get('what_to_watch', 'No specific items to watch.'),
        "further_reading": sections.get('further_reading', '')
    }


def generate_gemini_theme_summary(
    theme_name: str,
    articles: List[Dict],
    model: Optional[str] = None
) -> Dict[str, str]:
    """
    Generate an on-demand deep dive summary for a specific theme using Google Gemini API.
    Available exclusively in the Deep Dive view on user request.
    """
    from config.settings import MAX_ARTICLES_PER_GEMINI_SUMMARY

    if not articles:
        summary = {
            "what_is_happening": f"No articles found for {theme_name} in the past two weeks.",
            "engineering_tradeoffs": "Limited news signal this week.",
            "product_impact": "Limited news signal this week.",
            "why_it_matters": "No articles available for Gemini synthesis.",
            "what_to_watch": "Check back next week for updates.",
            "further_reading": ""
        }
        return _with_provenance(
            summary, source="gemini:no_articles",
            model=model or "gemini-default",
            article_count=0, note="empty_article_pool",
        )

    past_context = get_recent_context(theme_name)
    client = GeminiClient()
    chosen_model = model or client.default_model

    # Re-rank by theme relevance so Gemini sees the most on-theme articles
    # rather than the first N that fell out of the fetcher.
    ranked_articles = _rank_articles_by_relevance(
        articles, theme_name, MAX_ARTICLES_PER_GEMINI_SUMMARY,
    )
    char_budget = 45000  # Gemini supports massive context windows, expand to fit more articles
    formatted_articles = format_articles_for_prompt(
        ranked_articles, char_budget=char_budget,
    )

    num_articles = len(ranked_articles)
    if num_articles > 30:
        signal_instruction = (
            f"Write a comprehensive, detailed factual narrative of 6–10 sentences synthesizing the key trends from these {num_articles} developments. "
            "Do NOT use bullet points. Lead with the single most significant theme or development. "
            "When presenting key developments, cite the source name in parentheses next to the fact, model, or release (e.g. '(Anthropic)' or '(Databricks)'). "
            "End with a synthesis of the broader directional shift this signals. Target 180–250 words."
        )
    elif num_articles > 15:
        signal_instruction = (
            f"Write a detailed factual narrative of 5–8 sentences synthesizing these {num_articles} developments. "
            "Do NOT use bullet points. Lead with the single most significant theme or development. "
            "When presenting key developments, cite the source name in parentheses next to the fact, model, or release (e.g. '(Anthropic)' or '(Databricks)'). "
            "End with a sentence on the broader directional shift this signals. Target 120–180 words."
        )
    else:
        signal_instruction = (
            "Write 4–6 sentences as a tight factual narrative — no bullet points. Lead with the single most significant development. "
            "When presenting key developments, cite the source name in parentheses next to the fact, model, or release (e.g. '(Anthropic)' or '(Databricks)'). "
            "End with a sentence on the broader directional shift this signals. Target 80–120 words."
        )

    user_prompt = f"""You are analyzing AI news summaries from the past two weeks, focused on the theme: {theme_name}

--- SOURCE ARTICLES ---
{formatted_articles}

{f"--- PRIOR CONTEXT ---\n{past_context}" if past_context else ""}
--- END OF SOURCES ---

Produce a rigorous technical intelligence brief using the exact structure below.

---

## 1. WHAT IS HAPPENING
{signal_instruction}
{"Explicitly call out what is NEW or EVOLVED since the prior brief." if past_context else ""}

## 2. ENGINEERING TRADEOFFS & BLUEPRINT
*Audience: AI Engineers*
Write 4–6 sentences as flowing prose. Cover: architectural patterns, API changes, performance parameters (latency / memory / cost), open-weight licenses, or framework upgrades. Close with the core technical tradeoff a practitioner must weigh. Target 80–120 words.

## 3. PRODUCT IMPACT & FEASIBILITY
*Audience: Product Managers*
Write 4–6 sentences as flowing prose. Address: speed-to-market, pricing margins, integration overhead, safety/compliance risks, and competitor capability shifts. Close with a direct verdict: is this production-ready for enterprise use, and under what conditions? Target 80–120 words.

## 4. ACTIONABLE WATCHLIST
List exactly 3–5 items. Each item must follow this format:
  - **[Item]** — one sentence explaining why it matters and when to act.

Focus on: upcoming API breaking changes, benchmark releases, regulatory deadlines, or high-signal research papers. Every watchlist item MUST reference a specific upcoming event, deadline, release, or research paper found in the SOURCE ARTICLES — do not invent entity names or generic industry trends.

## 5. STRATEGIC FURTHER READING
List exactly 5 articles from the sources above. You MUST select the most significant articles from the SOURCE ARTICLES, prioritizing those you referenced or drew from in Section 1 (What is Happening) and Section 2 (Engineering Tradeoffs). Format each entry as:
  - **[Article Title]** | [Source] | [URL]
    *Why read this:* one sentence stating the concrete technical or product takeaway.

IMPORTANT: Copy each URL VERBATIM from the URL field of the matching article in the SOURCE ARTICLES. Never paraphrase, shorten, or invent URLs.

If you would otherwise produce an empty section, say so explicitly rather than omitting it.
"""

    system_prompt = """You are an expert AI engineering analyst and product strategist writing for senior tech leaders.

Your role is to cut through noise and surface what actually matters — architectural shifts, API stability, performance tradeoffs, and product feasibility.

Writing style rules:
- Be precise, direct, and technically rigorous
- No hype, buzzwords, or vague generalizations
- Write prose sections in flowing paragraphs
- Use bullet points only in sections 4 and 5
- Bold key terms, model names, and company names on first mention
- Never start two consecutive sentences with the same word
- STRICT FAITHFULNESS MANDATE: Every claim must be explicitly supported by the provided SOURCE ARTICLES. Do NOT extrapolate, infer unmentioned facts, or invent details.
"""

    content = client.generate_content(
        prompt=user_prompt,
        system_instruction=system_prompt,
        model=chosen_model,
        temperature=0.2,
        max_output_tokens=6144,
        timeout=60
    )

    parsed = _parse_summary_sections(content)
    badge = f"✨ *Gemini {chosen_model} Synthesized Brief:* "
    if badge not in parsed["what_is_happening"]:
        parsed["what_is_happening"] = f"{badge}\n\n{parsed['what_is_happening']}"

    return _with_provenance(
        parsed, source=f"gemini:{chosen_model}",
        model=chosen_model, article_count=len(articles),
        note="live_synthesis",
    )


def _get_existing_article_hashes(theme_name: str) -> set:
    """
    Get set of content_hash values for articles already summarized in this theme.
    Uses Supabase to check for existing articles.
    
    Args:
        theme_name: Name of the theme
    
    Returns:
        Set of content_hash strings for existing articles
    """
    try:
        from core.supabase_client import get_supabase_manager
        supabase = get_supabase_manager()
        if not supabase.is_available():
            return set()
        
        response = supabase.client.table("articles").select("content_hash").eq(
            "theme_name", theme_name
        ).execute()
        
        if response.data:
            return {row["content_hash"] for row in response.data if row.get("content_hash")}
        return set()
    except Exception as e:
        logger.debug(f"Could not fetch existing article hashes for {theme_name}: {e}")
        return set()


def _extract_last_summaries(last_run: Optional[Dict]) -> Dict:
    if not last_run:
        return {}
    if "data" in last_run and isinstance(last_run["data"], dict):
        return last_run["data"].get("summaries", {})
    return last_run.get("summaries", {})


def extractive_theme_summary(theme_name: str, articles: List[Dict]) -> Dict[str, str]:
    """Non-LLM Extractive Summarisation algorithm.
    Extracts top news items using LexRank sentence centrality and Luhn keyword scoring.
    Executes in <10ms with 0 LLM API calls and 0% hallucination risk.

    Provenance: the returned dict carries `_source = "extractive_fallback"`
    so the UI can render an honest chip above the brief.
    """
    from core.non_llm_summariser import generate_non_llm_theme_summary
    summary = generate_non_llm_theme_summary(theme_name, articles)
    return _with_provenance(
        summary, source="extractive_fallback",
        model=None, article_count=len(articles),
        note="non_llm_extractive",
    )


def generate_all_summaries(
    themed_articles: Dict[str, List[Dict]],
    full_articles: Optional[List[Dict]] = None
) -> Dict[str, Dict[str, str]]:
    """
    Generate summaries for all themes in THEME_ORDER using the Model Gateway.
    Handles quota, fallback, and provenance tracking.
    """
    # Run the async version
    return asyncio.run(_generate_all_summaries_async(themed_articles, full_articles))


async def _generate_all_summaries_async(
    themed_articles: Dict[str, List[Dict]],
    full_articles: Optional[List[Dict]] = None
) -> Dict[str, Dict[str, str]]:
    """Async implementation of summary generation using Model Gateway."""
    summaries: Dict[str, Dict[str, str]] = {}
    article_counts = {}

    # Check user-selected summariser mode from session state
    user_mode = None
    try:
        import streamlit as st
        user_mode = st.session_state.get("summariser_mode")
    except Exception:
        pass

    # The gateway handles quota/health internally, but we still check user mode
    for theme in THEME_ORDER:
        articles = themed_articles.get(theme, [])
        article_counts[theme] = len(articles)

        # Force Non-LLM Extractive if user selected "Non-LLM Extractive Only" mode
        if user_mode == "⚡ Non-LLM Extractive Only":
            logger.info("User selected Non-LLM Extractive Only mode. Generating LexRank/Luhn summary for %s", theme)
            summaries[theme] = extractive_theme_summary(theme, articles)
            continue

        existing_hashes = _get_existing_article_hashes(theme)
        new_articles = [
            a for a in articles
            if not a.get("content_hash") or a.get("content_hash") not in existing_hashes
        ]

        if not new_articles and articles:
            logger.info("Skipping LLM summary for %s: all %d articles already summarized",
                       theme, len(articles))
            skipped_summary = {
                "what_is_happening": f"No new articles this period. ({len(articles)} existing articles in database)",
                "engineering_tradeoffs": "Refer to previous summaries.",
                "product_impact": "Refer to previous summaries.",
                "why_it_matters": "No new developments to report.",
                "what_to_watch": "Monitor for new articles.",
                "further_reading": ""
            }
            summaries[theme] = _with_provenance(
                skipped_summary,
                source="gateway:skipped",
                model="none", article_count=len(articles),
                note="no_new_articles_skip",
            )
            continue

        logger.info("Generating summary for %s (%d new articles out of %d total)",
                   theme, len(new_articles), len(articles))

        try:
            summary = await generate_theme_summary_gateway(theme, new_articles if new_articles else articles)
            summaries[theme] = summary
        except Exception as exc:
            logger.error("Summary generation failed for %s: %s", theme, exc)
            # The gateway handles fallback internally, but if it fails completely:
            info_prefix = (
                "⚠️ *Non-LLM Extractive Summary: Live LLM synthesis failed "
                "(empty response / timeout), so this brief was compiled "
                "deterministically from the article pool using LexRank & Luhn "
                "extractive NLP.*"
            )
            extractive = extractive_theme_summary(theme, articles)
            orig_text = extractive.get("what_is_happening", "")
            if info_prefix not in orig_text:
                extractive["what_is_happening"] = f"{info_prefix}\n\n{orig_text}"
            summaries[theme] = extractive

    # Save to memory/wiki
    try:
        save_run_to_history(summaries, article_counts, full_articles, themed_articles)
    except Exception as e:
        logger.error("Failed to save history: %s", e)

    return summaries


def parse_further_reading(further_reading_text: str) -> List[Dict]:
    """Parse the further reading section into structured data."""
    articles: List[Dict] = []

    if not further_reading_text:
        return articles

    # Split by bullet points or numbered items
    lines = further_reading_text.split('\n')

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Remove bullet points
        if line.startswith('-') or line.startswith('*'):
            line = line[1:].strip()

        # Try to parse: Title | Source | URL | Why
        parts = line.split('|')
        if len(parts) >= 3:
            articles.append({
                'title': parts[0].strip(),
                'source': parts[1].strip(),
                'url': parts[2].strip(),
                'reason': parts[3].strip() if len(parts) > 3 else ''
            })

    return articles


def ensure_extractive_summary(s: dict, supabase=None, run_id: str = "", theme: str = "", articles: list = None) -> dict:
    """If ANY historical summary contains legacy quota warning text or older extractive phrasing,
    dynamically generate a fresh non-LLM extractive summary using the per-article LexRank algorithm.
    Applies universally across all runs and themes.
    """
    if not s:
        return s

    text = s.get("what_is_happening", "")
    legacy_indicators = [
        "Ollama Cloud weekly quota limit reached",
        "Unable to generate new summary",
        "Live LLM synthesis paused",
        "quota limit reached",
        "Compiled deterministically using LexRank & Luhn",
        "live LLM quota paused",
        "Extractive summary unavailable",
    ]

    if any(ind.lower() in text.lower() for ind in legacy_indicators):
        if not articles and supabase and run_id and theme:
            articles = supabase.get_articles_for_run(run_id, theme) or []

        if articles:
            info_prefix = "ℹ️ *Non-LLM Extractive Summary: Generated deterministically using lead sentence extraction because live LLM synthesis was paused.*"
            extractive = extractive_theme_summary(theme, articles)
            orig_text = extractive.get("what_is_happening", "")
            extractive["what_is_happening"] = f"{info_prefix}\n\n{orig_text}"
            return extractive

    return s

