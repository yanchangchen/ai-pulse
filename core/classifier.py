"""
Theme classification module for AI Pulse.
Classifies articles into 5 thematic areas using weighted keyword matching
and falls back to the LLM client for ambiguous cases.
"""

import re
import logging
from typing import Dict, List, Optional

from config.themes import THEMES
from core.llm_client import LLMClient, LLMClientError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Shared LLM client instance (initialised lazily)
_llm: Optional[LLMClient] = None


def _get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm


def keyword_classify(title: str, summary: str) -> Optional[str]:
    """Classify article using weighted keyword matching.

    Keywords in config/themes.py now map to integer weights.
    The theme with the highest *weighted* score wins.
    """
    text = f"{title} {summary}".lower()

    theme_scores: Dict[str, int] = {}

    for theme_name, theme_data in THEMES.items():
        score = 0
        keywords = theme_data["keywords"]
        for keyword, weight in keywords.items():
            pattern = r'\b' + re.escape(keyword.lower()) + r'\b'
            matches = re.findall(pattern, text)
            score += len(matches) * weight

        if score > 0:
            theme_scores[theme_name] = score

    if not theme_scores:
        return None

    return max(theme_scores, key=theme_scores.get)


def classify_with_ollama(title: str, summary: str) -> Optional[str]:
    """Use the LLM client to classify a single article."""
    prompt = (
        "Classify this AI news item into exactly one of these themes:\n"
        "[AI Applications & Architecture, AI Models, AI Infrastructure, "
        "AI Companies & Business, AI in Government & Policy].\n\n"
        "Return only the theme name, nothing else.\n\n"
        f"Title: {title}\n"
        f"Summary: {summary[:500]}"
    )

    try:
        llm = _get_llm()
        theme = llm.generate(prompt, temperature=0.1, max_tokens=50).strip()

        valid_themes = list(THEMES.keys())
        for valid_theme in valid_themes:
            if valid_theme.lower() in theme.lower():
                return valid_theme

    except LLMClientError as exc:
        logger.error("Ollama classification error: %s", exc)

    return None


def classify_articles(articles: List[Dict]) -> Dict[str, List[Dict]]:
    """Classify all articles into themes.

    Returns:
        Dictionary with theme names as keys and lists of articles as values.
    """
    # First pass: keyword classification
    keyword_classified: List[Dict] = []
    ollama_needed: List[Dict] = []

    for article in articles:
        theme = keyword_classify(article['title'], article['summary'])

        if theme:
            article['theme'] = theme
            keyword_classified.append(article)
        else:
            ollama_needed.append(article)

    logger.info(
        "Keyword classified: %d, need Ollama: %d",
        len(keyword_classified),
        len(ollama_needed),
    )

    # Second pass: Ollama classification for unmatched articles (batched)
    if ollama_needed:
        batch_size = 10
        llm = _get_llm()

        for i in range(0, len(ollama_needed), batch_size):
            batch = ollama_needed[i:i + batch_size]

            titles_summaries = "\n\n".join(
                f"Title: {a['title']}\nSummary: {a['summary'][:300]}"
                for a in batch
            )

            prompt = (
                "Classify each of these AI news items into exactly one of these themes:\n"
                "[AI Applications & Architecture, AI Models, AI Infrastructure, "
                "AI Companies & Business, AI in Government & Policy].\n\n"
                "Return the theme for each item on its own line, in the same order. "
                "Only return the theme name, nothing else.\n\n"
                f"{titles_summaries}"
            )

            try:
                result = llm.generate(prompt, temperature=0.1, max_tokens=200)
                themes = result.strip().split('\n')

                for j, theme_line in enumerate(themes):
                    if j < len(batch):
                        valid_themes = list(THEMES.keys())
                        for valid_theme in valid_themes:
                            if valid_theme.lower() in theme_line.lower():
                                batch[j]['theme'] = valid_theme
                                keyword_classified.append(batch[j])
                                break
                        else:
                            batch[j]['theme'] = "AI Applications & Architecture"
                            keyword_classified.append(batch[j])

            except LLMClientError as exc:
                logger.error("Batch Ollama classification error: %s", exc)
                for a in batch:
                    a['theme'] = "AI Applications & Architecture"
                    keyword_classified.append(a)

    # For articles still without theme, assign default
    for article in ollama_needed:
        if 'theme' not in article:
            article['theme'] = "AI Applications & Architecture"
            keyword_classified.append(article)

    # Group by theme
    themed_articles: Dict[str, List[Dict]] = {theme: [] for theme in THEMES.keys()}

    for article in keyword_classified:
        theme = article.get('theme', 'AI Applications & Architecture')
        if theme in themed_articles:
            themed_articles[theme].append(article)

    # Log counts
    for theme, arts in themed_articles.items():
        logger.info("Theme '%s': %d articles", theme, len(arts))

    return themed_articles


def get_theme_counts(themed_articles: Dict[str, List[Dict]]) -> Dict[str, int]:
    """Get article counts per theme."""
    return {theme: len(articles) for theme, articles in themed_articles.items()}
