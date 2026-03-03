"""
Theme classification module for AI Pulse.
Classifies articles into 5 thematic areas using keyword matching and Ollama.
"""

import re
import requests
from typing import List, Dict, Optional
import logging

from config.themes import THEMES

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Ollama Cloud configuration
OLLAMA_BASE_URL = "https://api.ollama.com"
DEFAULT_MODEL = "qwen3-coder:30b"
OLLAMA_API_KEY = "45beed49227f4ef5af146efb097df093.UN3XitWdoKXnweyM1t7fp6bP"


def keyword_classify(title: str, summary: str) -> Optional[str]:
    """Classify article using keyword matching."""
    text = f"{title} {summary}".lower()

    theme_scores = {}

    for theme_name, theme_data in THEMES.items():
        score = 0
        for keyword in theme_data["keywords"]:
            # Use word boundary matching for better accuracy
            pattern = r'\b' + re.escape(keyword.lower()) + r'\b'
            matches = re.findall(pattern, text)
            score += len(matches)

        if score > 0:
            theme_scores[theme_name] = score

    if not theme_scores:
        return None

    # Return theme with highest score
    return max(theme_scores, key=theme_scores.get)


def classify_with_ollama(title: str, summary: str, model: str = DEFAULT_MODEL) -> Optional[str]:
    """Use Ollama API to classify an article."""
    prompt = f"""Classify this AI news item into exactly one of these themes:
[AI Applications & Architecture, AI Models, AI Infrastructure, AI Companies & Business, AI in Government & Policy].

Return only the theme name, nothing else.

Title: {title}
Summary: {summary[:500]}"""

    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "num_predict": 50
                }
            },
            headers={
                "Authorization": f"Bearer {OLLAMA_API_KEY}"
            },
            timeout=60
        )

        if response.status_code == 200:
            result = response.json()
            theme = result.get("response", "").strip()

            # Validate theme name
            valid_themes = list(THEMES.keys())
            for valid_theme in valid_themes:
                if valid_theme.lower() in theme.lower():
                    return valid_theme

        return None

    except Exception as e:
        logger.error(f"Ollama classification error: {str(e)}")
        return None


def classify_articles(articles: List[Dict], api_key: str = None) -> Dict[str, List[Dict]]:
    """Classify all articles into themes.

    Returns:
        Dictionary with theme names as keys and lists of articles as values.
    """
    # Get model from config or use default
    model = DEFAULT_MODEL

    # First pass: keyword classification
    keyword_classified = []
    ollama_needed = []

    for article in articles:
        theme = keyword_classify(article['title'], article['summary'])

        if theme:
            article['theme'] = theme
            keyword_classified.append(article)
        else:
            ollama_needed.append(article)

    logger.info(f"Keyword classified: {len(keyword_classified)}, need Ollama: {len(ollama_needed)}")

    # Second pass: Ollama classification for unmatched articles
    if ollama_needed:
        # Batch articles for more efficient API calls
        batch_size = 10
        for i in range(0, len(ollama_needed), batch_size):
            batch = ollama_needed[i:i+batch_size]

            # Classify batch together
            titles_summaries = "\n\n".join([
                f"Title: {a['title']}\nSummary: {a['summary'][:300]}"
                for a in batch
            ])

            prompt = f"""Classify each of these AI news items into exactly one of these themes:
[AI Applications & Architecture, AI Models, AI Infrastructure, AI Companies & Business, AI in Government & Policy].

Return the theme for each item on its own line, in the same order. Only return the theme name, nothing else.

{titles_summaries}"""

            try:
                response = requests.post(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    headers={"Authorization": f"Bearer {OLLAMA_API_KEY}"},
                    json={
                        "model": model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.1,
                            "num_predict": 200
                        }
                    },
                    timeout=120
                )

                if response.status_code == 200:
                    result = response.json()
                    themes = result.get("response", "").strip().split('\n')

                    for j, theme in enumerate(themes):
                        if j < len(batch):
                            # Find matching theme
                            valid_themes = list(THEMES.keys())
                            for valid_theme in valid_themes:
                                if valid_theme.lower() in theme.lower():
                                    batch[j]['theme'] = valid_theme
                                    keyword_classified.append(batch[j])
                                    break
                            else:
                                # Default to first theme if no match
                                batch[j]['theme'] = "AI Applications & Architecture"
                                keyword_classified.append(batch[j])

            except Exception as e:
                logger.error(f"Batch Ollama classification error: {str(e)}")
                # Fall back: assign all to first theme
                for a in batch:
                    a['theme'] = "AI Applications & Architecture"
                    keyword_classified.append(a)

    # For articles still without theme, assign to first theme
    for article in ollama_needed:
        if 'theme' not in article:
            article['theme'] = "AI Applications & Architecture"
            keyword_classified.append(article)

    # Group by theme
    themed_articles = {theme: [] for theme in THEMES.keys()}

    for article in keyword_classified:
        theme = article.get('theme', 'AI Applications & Architecture')
        if theme in themed_articles:
            themed_articles[theme].append(article)

    # Log counts
    for theme, arts in themed_articles.items():
        logger.info(f"Theme '{theme}': {len(arts)} articles")

    return themed_articles


def get_theme_counts(themed_articles: Dict[str, List[Dict]]) -> Dict[str, int]:
    """Get article counts per theme."""
    return {theme: len(articles) for theme, articles in themed_articles.items()}
