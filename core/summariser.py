"""
LLM summarisation module for AI Pulse.
Generates theme summaries using the shared LLM client.
"""

import logging
from typing import Dict, List, Optional

from config.themes import THEMES, THEME_ORDER
from core.llm_client import LLMClient, LLMClientError
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


def format_articles_for_prompt(articles: List[Dict]) -> str:
    """Format articles for the summarisation prompt."""
    formatted = []

    for i, article in enumerate(articles, 1):
        title = article.get('title', 'Untitled')
        summary = article.get('summary', '')[:300]  # Limit length
        source = article.get('source_name', 'Unknown')
        link = article.get('link', '')

        formatted.append(f"{i}. {title}")
        formatted.append(f"   Source: {source}")
        if summary:
            formatted.append(f"   Summary: {summary}")
        if link:
            formatted.append(f"   URL: {link}")
        formatted.append("")

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

    # Format articles for the prompt
    formatted_articles = format_articles_for_prompt(articles[:15])  # Limit to 15 articles

    user_prompt = f"""Here are AI news summaries from the past two weeks, all related to {theme_name}:

{formatted_articles}

{past_context if past_context else ""}

Provide a rigorous, technical intelligence brief. Avoid vague generalizations, marketing fluff, and hype. 

Provide exactly this structure:
1. WHAT IS HAPPENING: [3-5 sentence factual summary of the key developments. If there is past context provided, highlight what is NEW or what has EVOLVED since then.]
2. ENGINEERING TRADEOFFS & BLUEPRINT: [3-5 sentences specifically for AI Engineers. Detail the architectural patterns, APIs, performance parameters (latency/memory/cost), open-weight licenses, or framework upgrades introduced here. What technical challenges do they solve?]
3. PRODUCT IMPACT & FEASIBILITY: [3-5 sentences specifically for Product Managers. How does this impact speed-to-market, pricing margins, integration overhead, safety/compliance risks, or competitor capabilities? Is it ready for enterprise production?]
4. ACTIONABLE WATCHLIST: [3-5 specific, bulleted items highlighting upcoming API changes, benchmark reviews, regulatory deadlines, or open research papers to track immediately.]
5. STRATEGIC FURTHER READING: [5 most insightful articles with one-sentence explanation each, formatted exactly as:
   - Article Title | Source | URL | Why read this (concrete technical/product takeaway)]

Be precise, avoid hype. Focus on signal over noise. Write in clear, direct language for a technically sophisticated audience."""

    system_prompt = """You are an expert AI engineering analyst and product strategist writing for tech leaders. 
Be highly precise, avoid hype and buzzwords. Focus on real architectural shifts, API stability, performance tradeoffs, and product feasibility."""

    try:
        llm = _get_llm()
        content = llm.generate(
            user_prompt,
            system=system_prompt,
            temperature=0.3,
            max_tokens=1500,
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


def generate_all_summaries(
    themed_articles: Dict[str, List[Dict]],
    full_articles: List[Dict]
) -> Dict[str, Dict[str, str]]:
    """Generate summaries for all themes and persist to history.
    
    Optimization: Only generates summaries for themes that have NEW articles
    (articles not already summarized in Supabase).
    """
    summaries: Dict[str, Dict[str, str]] = {}
    article_counts = {}

    for theme in THEME_ORDER:
        articles = themed_articles.get(theme, [])
        article_counts[theme] = len(articles)
        
        # NEW: Check if articles are already summarized
        existing_hashes = _get_existing_article_hashes(theme)
        new_articles = [
            a for a in articles 
            if not a.get("content_hash") or a.get("content_hash") not in existing_hashes
        ]
        
        if not new_articles and articles:
            logger.info("Skipping LLM summary for %s: all %d articles already summarized", 
                       theme, len(articles))
            # Use a cached summary indicating no new articles
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
        
        # Generate summary using new articles only (for better signal)
        summary = generate_theme_summary(theme, new_articles if new_articles else articles)
        summaries[theme] = summary

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
