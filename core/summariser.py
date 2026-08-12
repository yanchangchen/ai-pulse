"""
LLM summarisation module for AI Pulse.
Generates theme summaries using the shared LLM client.
"""

import logging
from typing import Dict, List, Optional

from config.themes import THEMES, THEME_ORDER
from core.llm_client import LLMClient, LLMClientError
import core.history_manager as history_manager
from core.history_manager import get_recent_context, save_run_to_history

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Shared LLM client instance (initialised lazily)
_llm: Optional[LLMClient] = None


def _get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm


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
        return {
            "what_is_happening": "No articles found for this theme in the past two weeks.",
            "why_it_matters": "Limited coverage this week.",
            "what_to_watch": "Check back next week for updates.",
            "further_reading": ""
        }

    # Retrieve memory context
    past_context = get_recent_context(theme_name)

    if len(articles) < 3:
        return {
            "what_is_happening": f"Limited coverage this week with only {len(articles)} articles found. {past_context}",
            "engineering_tradeoffs": "Limited news signal this week.",
            "product_impact": "Limited news signal this week.",
            "why_it_matters": "This theme has fewer articles this week, possibly indicating lower activity or a quiet period.",
            "what_to_watch": "Monitor for upcoming announcements and developments.",
            "further_reading": ""
        }

    # Format articles for the prompt.  Cap at MAX_ARTICLES_PER_SUMMARY and
    # then truncate further to stay inside the LLM's input budget.
    from config.settings import (
        MAX_ARTICLES_PER_SUMMARY,
        OLLAMA_NUM_CTX,
        CHARS_PER_TOKEN,
        INPUT_BUDGET_FRACTION,
    )
    char_budget = int(OLLAMA_NUM_CTX * CHARS_PER_TOKEN * INPUT_BUDGET_FRACTION)
    formatted_articles = format_articles_for_prompt(
        articles[:MAX_ARTICLES_PER_SUMMARY], char_budget=char_budget,
    )

    user_prompt = f"""You are analyzing AI news summaries from the past two weeks, focused on the theme: {theme_name}

--- SOURCE ARTICLES ---
{formatted_articles}

{f"--- PRIOR CONTEXT ---\n{past_context}" if past_context else ""}
--- END OF SOURCES ---

Produce a rigorous technical intelligence brief using the exact structure below.

---

## 1. WHAT IS HAPPENING
Write 3–5 sentences as a tight factual narrative — no bullet points. Lead with the single most significant development. End with a sentence on the broader directional shift this signals.
{"Explicitly call out what is NEW or EVOLVED since the prior brief." if past_context else ""}

## 2. ENGINEERING TRADEOFFS & BLUEPRINT
*Audience: AI Engineers*
Write 3–5 sentences as flowing prose. Cover: architectural patterns, API changes, performance parameters (latency / memory / cost), open-weight licenses, or framework upgrades. Close with the core technical tradeoff a practitioner must weigh.

## 3. PRODUCT IMPACT & FEASIBILITY
*Audience: Product Managers*
Write 3–5 sentences as flowing prose. Address: speed-to-market, pricing margins, integration overhead, safety/compliance risks, and competitor capability shifts. Close with a direct verdict: is this production-ready for enterprise use, and under what conditions?

## 4. ACTIONABLE WATCHLIST
List exactly 3–5 items. Each item must follow this format:
  - **[Item]** — one sentence explaining why it matters and when to act.

Focus on: upcoming API breaking changes, benchmark releases, regulatory deadlines, or high-signal research papers.

## 5. STRATEGIC FURTHER READING
List exactly 5 articles from the sources above. Format each entry as:
  - **[Article Title]** | [Source] | [URL]
    *Why read this:* one sentence stating the concrete technical or product takeaway.
"""

    system_prompt = """You are an expert AI engineering analyst and product strategist writing for senior tech leaders.

Your role is to cut through noise and surface what actually matters — architectural shifts, API stability, performance tradeoffs, and product feasibility.

Writing style rules:
- Be precise, direct, and technically rigorous
- No hype, buzzwords, or vague generalizations
- Use short paragraphs (3–5 sentences max per section)
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
            max_tokens=int(sum_settings.get("max_tokens", 1500)),
        )

        # Parse the response
        sections: Dict[str, str] = {}
        current_section: Optional[str] = None
        current_content: List[str] = []

        for line in content.split('\n'):
            line = line.strip()

            if 'WHAT IS HAPPENING' in line.upper():
                if current_section:
                    sections[current_section] = ' '.join(current_content)
                current_section = 'what_is_happening'
                current_content = [line.split(':', 1)[-1].strip()] if ':' in line else []
            elif 'ENGINEERING TRADEOFFS' in line.upper() or 'ENGINEERING BLUEPRINT' in line.upper():
                if current_section:
                    sections[current_section] = ' '.join(current_content)
                current_section = 'engineering_tradeoffs'
                current_content = [line.split(':', 1)[-1].strip()] if ':' in line else []
            elif 'PRODUCT IMPACT' in line.upper() or 'PRODUCT FEASIBILITY' in line.upper():
                if current_section:
                    sections[current_section] = ' '.join(current_content)
                current_section = 'product_impact'
                current_content = [line.split(':', 1)[-1].strip()] if ':' in line else []
            elif 'WHY IT MATTERS' in line.upper():
                if current_section:
                    sections[current_section] = ' '.join(current_content)
                current_section = 'why_it_matters'
                current_content = [line.split(':', 1)[-1].strip()] if ':' in line else []
            elif 'ACTIONABLE WATCHLIST' in line.upper() or 'WHAT TO WATCH' in line.upper():
                if current_section:
                    sections[current_section] = ' '.join(current_content)
                current_section = 'what_to_watch'
                current_content = [line.split(':', 1)[-1].strip()] if ':' in line else []
            elif 'STRATEGIC FURTHER READING' in line.upper() or 'FURTHER READING' in line.upper():
                if current_section:
                    sections[current_section] = ' '.join(current_content)
                current_section = 'further_reading'
                current_content = [line.split(':', 1)[-1].strip()] if ':' in line else []
            elif line and current_section:
                current_content.append(line)

        # Don't forget the last section
        if current_section:
            sections[current_section] = ' '.join(current_content)

        # Build composite why_it_matters if it wasn't explicitly produced but tradeoffs/product impact were
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

    except LLMClientError as exc:
        logger.error("Error generating summary for %s: %s", theme_name, exc)
        return {
            "what_is_happening": f"Error generating summary: {exc}",
            "engineering_tradeoffs": "Unable to analyze due to error.",
            "product_impact": "Unable to analyze due to error.",
            "why_it_matters": "Unable to analyze at this time.",
            "what_to_watch": "Please try again later.",
            "further_reading": ""
        }


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
    """
    from core.non_llm_summariser import generate_non_llm_theme_summary
    return generate_non_llm_theme_summary(theme_name, articles)


def generate_all_summaries(
    themed_articles: Dict[str, List[Dict]],
    full_articles: Optional[List[Dict]] = None
) -> Dict[str, Dict[str, str]]:
    """
    Generate summaries for all themes in THEME_ORDER.
    Checks for LLM quota/rate limits and falls back to cached summaries or
    extractive non-LLM summaries when quota is exceeded.
    """
    summaries: Dict[str, Dict[str, str]] = {}
    article_counts = {}

    # Check user-selected summariser mode from session state
    user_mode = None
    try:
        import streamlit as st
        user_mode = st.session_state.get("summariser_mode")
    except Exception:
        pass

    for theme in THEME_ORDER:
        articles = themed_articles.get(theme, [])
        article_counts[theme] = len(articles)

        # Force Non-LLM Extractive if user selected "Non-LLM Extractive Only" mode
        if user_mode == "⚡ Non-LLM Extractive Only":
            logger.info("User selected Non-LLM Extractive Only mode. Generating LexRank/Luhn summary for %s", theme)
            summaries[theme] = extractive_theme_summary(theme, articles)
            continue

        # Check if LLM quota/rate limit was hit previously or on this run
        if LLMClient.is_quota_exceeded():
            logger.warning("LLM quota exceeded (HTTP 429). Initiating non-LLM extractive summarisation for %s", theme)
            info_prefix = "⚡ *Non-LLM Extractive Summary: Compiled deterministically using LexRank & Luhn extractive NLP (live LLM quota paused).* "
            extractive = extractive_theme_summary(theme, articles)
            orig_text = extractive.get("what_is_happening", "")
            if info_prefix not in orig_text:
                extractive["what_is_happening"] = f"{info_prefix}\n\n{orig_text}"
            summaries[theme] = extractive
            continue

        existing_hashes = _get_existing_article_hashes(theme)
        new_articles = [
            a for a in articles 
            if not a.get("content_hash") or a.get("content_hash") not in existing_hashes
        ]

        if not new_articles and articles:
            logger.info("Skipping LLM summary for %s: all %d articles already summarized", 
                       theme, len(articles))
            summaries[theme] = {
                "what_is_happening": f"No new articles this period. ({len(articles)} existing articles in database)",
                "engineering_tradeoffs": "Refer to previous summaries.",
                "product_impact": "Refer to previous summaries.",
                "why_it_matters": "No new developments to report.",
                "what_to_watch": "Monitor for new articles.",
                "further_reading": ""
            }
            continue

        logger.info("Generating summary for %s (%d new articles out of %d total)", 
                   theme, len(new_articles), len(articles))

        try:
            summary = generate_theme_summary(theme, new_articles if new_articles else articles)
            summaries[theme] = summary
        except Exception as exc:
            logger.error("Summary generation failed for %s: %s", theme, exc)
            if LLMClient.is_quota_exceeded():
                logger.warning("LLM quota exceeded during %s. Initiating non-LLM extractive summarisation.", theme)
                info_prefix = "ℹ️ *Non-LLM Extractive Summary: Generated deterministically using lead sentence extraction because live LLM synthesis is paused.*"
                extractive = extractive_theme_summary(theme, articles)
                orig_text = extractive.get("what_is_happening", "")
                if info_prefix not in orig_text:
                    extractive["what_is_happening"] = f"{info_prefix}\n\n{orig_text}"
                summaries[theme] = extractive
            else:
                summaries[theme] = {
                    "what_is_happening": f"Error generating summary: {exc}",
                    "engineering_tradeoffs": "Unable to analyze due to error.",
                    "product_impact": "Unable to analyze due to error.",
                    "why_it_matters": "Unable to analyze at this time.",
                    "what_to_watch": "Please try again later.",
                    "further_reading": ""
                }

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

