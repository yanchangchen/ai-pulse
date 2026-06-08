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
        "You are an AI news classifier. Classify this AI news item into exactly one of these seven themes:\n"
        "- Agentic Systems & DevTools\n"
        "- Frontier Models & Benchmarks\n"
        "- Hardware, Compute & LLMOps\n"
        "- Enterprise Strategy & ROI\n"
        "- Governance, Safety & Policy\n"
        "- AI Security & Trust\n"
        "- AI-Assisted Software Engineering\n\n"
        "CRITICAL INSTRUCTION: You must choose the single closest and most relevant category from the list above. "
        "Under no circumstances should you return anything other than the exact theme name (e.g. do not say 'ambiguous', 'other', or 'none').\n\n"
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


def find_closest_theme(title: str, summary: str) -> str:
    """Find the closest theme for an article when standard classification fails.
    
    Performs a relaxed check for theme keyword matches (case-insensitive, ignoring word boundaries
    if needed) and maps it to the category with the highest relevance score.
    """
    text = f"{title} {summary}".lower()
    
    # Calculate overlap scores based on the defined theme keywords
    scores = {theme: 0 for theme in THEMES}
    for theme_name, theme_data in THEMES.items():
        for keyword, weight in theme_data["keywords"].items():
            if keyword.lower() in text:
                scores[theme_name] += weight
                
    # If any score is > 0, return the theme with the highest score
    if any(score > 0 for score in scores.values()):
        return max(scores, key=scores.get)
        
    # If no keywords matched, try matching individual words from the theme names
    for theme_name in THEMES:
        words = re.findall(r'\w+', theme_name.lower())
        for word in words:
            if len(word) > 3 and word in text:
                scores[theme_name] += 1
                
    if any(score > 0 for score in scores.values()):
        return max(scores, key=scores.get)
        
    # Ultimate default if no keywords or theme words are found in the title/summary
    return "Agentic Systems & DevTools"


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
        batch_size = 20  # Increased batch size
        llm = _get_llm()

        for i in range(0, len(ollama_needed), batch_size):
            batch = ollama_needed[i:i + batch_size]
            
            # Create a simple lookup for the prompt
            batch_items = [f"ID {idx}: {a['title']}" for idx, a in enumerate(batch)]
            items_text = "\n".join(batch_items)

            system_prompt = (
                "You are an AI news classifier. You must categorize articles into exactly one of these seven themes:\n"
                "- Agentic Systems & DevTools\n"
                "- Frontier Models & Benchmarks\n"
                "- Hardware, Compute & LLMOps\n"
                "- Enterprise Strategy & ROI\n"
                "- Governance, Safety & Policy\n"
                "- AI Security & Trust\n"
                "- AI-Assisted Software Engineering\n\n"
                "CRITICAL INSTRUCTIONS:\n"
                "1. You must categorize every single article provided. Do not skip or omit any article.\n"
                "2. If an article does not fit a category perfectly or is ambiguous, choose the closest and most relevant one from the 7 themes above. Under no circumstances should you return 'Other', 'Unknown', or omit it.\n"
                "3. You must return a valid JSON object mapping every single provided ID to its theme name.\n"
                "Example: {\"ID 0\": \"Frontier Models & Benchmarks\", \"ID 1\": \"AI Security & Trust\"}"
            )

            prompt = f"Classify these articles:\n\n{items_text}"

            try:
                result = llm.generate(prompt, system=system_prompt, temperature=0.1, max_tokens=1000)
                
                # Attempt to parse JSON from the response
                import json
                # Handle cases where LLM adds extra text around JSON
                json_match = re.search(r'\{.*\}', result, re.DOTALL)
                if json_match:
                    mapping = json.loads(json_match.group(0))
                    
                    for idx, article in enumerate(batch):
                        id_key = f"ID {idx}"
                        theme_name = mapping.get(id_key)
                        
                        if theme_name:
                            # Clean and validate the theme name
                            for valid_theme in THEMES.keys():
                                if valid_theme.lower() in theme_name.lower():
                                    article['theme'] = valid_theme
                                    keyword_classified.append(article)
                                    break
                else:
                    truncated_result = result[:200] + "..." if len(result) > 200 else result
                    logger.warning("LLM response did not contain JSON: %s", truncated_result)

            except Exception as exc:
                logger.error("Batch JSON Ollama classification error: %s", exc)

    # Final cleanup: Assign closest theme for anything missed instead of a blind default
    for article in ollama_needed:
        if 'theme' not in article:
            article['theme'] = find_closest_theme(article['title'], article['summary'])
            keyword_classified.append(article)

    # Group by theme
    themed_articles: Dict[str, List[Dict]] = {theme: [] for theme in THEMES.keys()}

    for article in keyword_classified:
        theme = article.get('theme', 'Agentic Systems & DevTools')
        if theme in themed_articles:
            themed_articles[theme].append(article)

    # Log counts
    for theme, arts in themed_articles.items():
        logger.info("Theme '%s': %d articles", theme, len(arts))

    return themed_articles


def get_theme_counts(themed_articles: Dict[str, List[Dict]]) -> Dict[str, int]:
    """Get article counts per theme."""
    return {theme: len(articles) for theme, articles in themed_articles.items()}
