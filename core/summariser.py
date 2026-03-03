"""
LLM summarisation module for AI Pulse.
Generates theme summaries using Ollama.
"""

import logging
import requests
from typing import List, Dict

from config.themes import THEMES, THEME_ORDER

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Ollama Cloud configuration
OLLAMA_BASE_URL = "https://api.ollama.com"
DEFAULT_MODEL = "minimax-m2.5:cloud"
OLLAMA_API_KEY = "45beed49227f4ef5af146efb097df093.UN3XitWdoKXnweyM1t7fp6bP"


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
    model: str = DEFAULT_MODEL
) -> Dict[str, str]:
    """Generate a comprehensive summary for a theme using Ollama."""

    if not articles:
        return {
            "what_is_happening": "No articles found for this theme in the past two weeks.",
            "why_it_matters": "Limited coverage this week.",
            "what_to_watch": "Check back next week for updates.",
            "further_reading": ""
        }

    if len(articles) < 3:
        return {
            "what_is_happening": f"Limited coverage this week with only {len(articles)} articles found.",
            "why_it_matters": "This theme has fewer articles this week, possibly indicating lower activity or a quiet period.",
            "what_to_watch": "Monitor for upcoming announcements and developments.",
            "further_reading": ""
        }

    # Format articles for the prompt
    formatted_articles = format_articles_for_prompt(articles[:15])  # Limit to 15 articles

    user_prompt = f"""Here are AI news summaries from the past two weeks, all related to {theme_name}:

{formatted_articles}

Provide:
1. WHAT IS HAPPENING: [3-5 sentence factual summary of the key developments]
2. WHY IT MATTERS: [2-5 sentence explanation of significance and implications]
3. WHAT TO WATCH: [2-5 specific things worth investigating deeper, as bullet points]
4. FURTHER READING: [5 most insightful articles with one-sentence explanation each, formatted as:
   - Article Title | Source | URL | Why read this]

Be precise, avoid hype. Focus on signal over noise. Write in clear, direct language for a technically sophisticated audience."""

    system_prompt = """You are an expert AI analyst writing for a technically sophisticated audience
(intermediate to advanced). Be precise, avoid hype. Focus on signal over noise.
Write in clear, direct language."""

    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            headers={"Authorization": f"Bearer {OLLAMA_API_KEY}"},
            json={
                "model": model,
                "prompt": f"System: {system_prompt}\n\nUser: {user_prompt}",
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "num_predict": 1500
                }
            },
            timeout=120
        )

        if response.status_code == 200:
            result = response.json()
            content = result.get("response", "")
        else:
            logger.error(f"Ollama API error: {response.status_code} - {response.text}")
            return {
                "what_is_happening": f"Error calling Ollama API: {response.status_code}",
                "why_it_matters": "Unable to analyze at this time.",
                "what_to_watch": "Please try again later.",
                "further_reading": ""
            }

        # Parse the response
        sections = {}
        current_section = None
        current_content = []

        for line in content.split('\n'):
            line = line.strip()

            if 'WHAT IS HAPPENING' in line.upper():
                current_section = 'what_is_happening'
                current_content = [line.split(':', 1)[-1].strip()] if ':' in line else []
            elif 'WHY IT MATTERS' in line.upper():
                if current_section:
                    sections[current_section] = ' '.join(current_content)
                current_section = 'why_it_matters'
                current_content = [line.split(':', 1)[-1].strip()] if ':' in line else []
            elif 'WHAT TO WATCH' in line.upper():
                if current_section:
                    sections[current_section] = ' '.join(current_content)
                current_section = 'what_to_watch'
                current_content = [line.split(':', 1)[-1].strip()] if ':' in line else []
            elif 'FURTHER READING' in line.upper():
                if current_section:
                    sections[current_section] = ' '.join(current_content)
                current_section = 'further_reading'
                current_content = [line.split(':', 1)[-1].strip()] if ':' in line else []
            elif line and current_section:
                current_content.append(line)

        # Don't forget the last section
        if current_section:
            sections[current_section] = ' '.join(current_content)

        return {
            "what_is_happening": sections.get('what_is_happening', 'Unable to generate summary.'),
            "why_it_matters": sections.get('why_it_matters', 'Unable to generate analysis.'),
            "what_to_watch": sections.get('what_to_watch', 'No specific items to watch.'),
            "further_reading": sections.get('further_reading', '')
        }

    except Exception as e:
        logger.error(f"Error generating summary for {theme_name}: {str(e)}")
        return {
            "what_is_happening": f"Error generating summary: {str(e)}",
            "why_it_matters": "Unable to analyze at this time.",
            "what_to_watch": "Please try again later.",
            "further_reading": ""
        }


def generate_all_summaries(
    themed_articles: Dict[str, List[Dict]],
    api_key: str = None
) -> Dict[str, Dict[str, str]]:
    """Generate summaries for all themes."""

    # Get model from config or use default
    model = DEFAULT_MODEL

    summaries = {}

    for theme in THEME_ORDER:
        articles = themed_articles.get(theme, [])
        logger.info(f"Generating summary for {theme} ({len(articles)} articles)")

        summary = generate_theme_summary(theme, articles, model)
        summaries[theme] = summary

    return summaries


def parse_further_reading(further_reading_text: str) -> List[Dict]:
    """Parse the further reading section into structured data."""
    articles = []

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
