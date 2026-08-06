"""
Theme classification module for AI Pulse.
Classifies articles into 7 strategic themes using a 4-pass waterfall pipeline:
1. Pass 1: Exact weighted keyword matching (keyword_classify)
2. Pass 2: TF-IDF Cosine Similarity matching (tfidf_classify)
3. Pass 3: Batched LLM classification (classify_with_ollama)
4. Pass 4: Soft-match heuristic fallback (find_closest_theme)
"""

import re
import json
import logging
from typing import Dict, List, Optional

from config.themes import THEMES
from core.llm_client import LLMClient, LLMClientError
from core.tfidf_classifier import tfidf_classify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Shared LLM client instance (initialised lazily)
_llm: Optional[LLMClient] = None

# Global store for latest classification gate metrics
_last_gate_stats: Dict[str, int] = {
    "gate_1_keyword": 0,
    "gate_2_tfidf": 0,
    "gate_3_ollama": 0,
    "gate_4_heuristic": 0,
    "total": 0,
}


def _get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm


def get_latest_gate_stats() -> Dict[str, int]:
    """Return gate distribution metrics for the most recent classification run."""
    return dict(_last_gate_stats)


def keyword_classify(title: str, summary: str) -> Optional[str]:
    """Classify article using weighted keyword matching.

    Keywords in config/themes.py map to integer weights.
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
    """Classify all articles into strategic themes using a 4-pass waterfall:
    - Pass 1: Weighted Keyword Matching
    - Pass 2: TF-IDF Cosine Similarity
    - Pass 3: Batched Ollama LLM Classifier
    - Pass 4: Soft-Match Heuristic Fallback

    Returns:
        Dictionary with theme names as keys and lists of articles as values.
    """
    global _last_gate_stats

    gate_counts = {
        "gate_1_keyword": 0,
        "gate_2_tfidf": 0,
        "gate_3_ollama": 0,
        "gate_4_heuristic": 0,
        "total": len(articles),
    }

    classified_list: List[Dict] = []
    unmatched_after_pass1: List[Dict] = []

    # ------------------------------------------------------------------
    # Pass 1: Exact Weighted Keyword Classification
    # ------------------------------------------------------------------
    for article in articles:
        theme = keyword_classify(article.get("title", ""), article.get("summary", ""))
        if theme:
            article["theme"] = theme
            article["gate"] = 1
            gate_counts["gate_1_keyword"] += 1
            classified_list.append(article)
        else:
            unmatched_after_pass1.append(article)

    # ------------------------------------------------------------------
    # Pass 2: TF-IDF Cosine Similarity Classification
    # ------------------------------------------------------------------
    unmatched_after_pass2: List[Dict] = []
    for article in unmatched_after_pass1:
        theme, sim_score, _ = tfidf_classify(
            article.get("title", ""),
            article.get("summary", ""),
            min_similarity=0.05,
        )
        if theme:
            article["theme"] = theme
            article["gate"] = 2
            article["tfidf_score"] = sim_score
            gate_counts["gate_2_tfidf"] += 1
            classified_list.append(article)
        else:
            unmatched_after_pass2.append(article)

    # ------------------------------------------------------------------
    # Pass 3: Batched Ollama LLM Classification for remaining items
    # ------------------------------------------------------------------
    unmatched_after_pass3: List[Dict] = []

    if unmatched_after_pass2:
        batch_size = 20
        llm = _get_llm()

        for i in range(0, len(unmatched_after_pass2), batch_size):
            batch = unmatched_after_pass2[i:i + batch_size]
            batch_items = [f"ID {idx}: {a.get('title', '')}" for idx, a in enumerate(batch)]
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
                "2. Choose the single closest theme name.\n"
                "3. Return a valid JSON object mapping ID to theme name: {\"ID 0\": \"Frontier Models & Benchmarks\"}"
            )

            prompt = f"Classify these articles:\n\n{items_text}"

            try:
                result = llm.generate(prompt, system=system_prompt, temperature=0.1, max_tokens=1000)
                json_match = re.search(r'\{.*\}', result, re.DOTALL)
                if json_match:
                    mapping = json.loads(json_match.group(0))
                    for idx, article in enumerate(batch):
                        id_key = f"ID {idx}"
                        theme_name = mapping.get(id_key)
                        if theme_name:
                            for valid_theme in THEMES.keys():
                                if valid_theme.lower() in theme_name.lower():
                                    article['theme'] = valid_theme
                                    article['gate'] = 3
                                    gate_counts["gate_3_ollama"] += 1
                                    classified_list.append(article)
                                    break
            except Exception as exc:
                logger.error("Batch JSON Ollama classification error: %s", exc)

    # Collect any items still unassigned after Pass 3
    for article in unmatched_after_pass2:
        if 'theme' not in article:
            unmatched_after_pass3.append(article)

    # ------------------------------------------------------------------
    # Pass 4: Soft-Match Heuristic Fallback
    # ------------------------------------------------------------------
    for article in unmatched_after_pass3:
        if 'theme' not in article:
            article['theme'] = find_closest_theme(article.get('title', ''), article.get('summary', ''))
            article['gate'] = 4
            gate_counts["gate_4_heuristic"] += 1
            classified_list.append(article)

    # Save metrics globally
    _last_gate_stats = gate_counts

    logger.info(
        "Classification complete [%d total]. Pass 1 (Keywords): %d, Pass 2 (TF-IDF): %d, Pass 3 (Ollama): %d, Pass 4 (Heuristic): %d",
        len(articles),
        gate_counts["gate_1_keyword"],
        gate_counts["gate_2_tfidf"],
        gate_counts["gate_3_ollama"],
        gate_counts["gate_4_heuristic"],
    )

    # Group by theme
    themed_articles: Dict[str, List[Dict]] = {theme: [] for theme in THEMES.keys()}
    for article in classified_list:
        theme = article.get('theme', 'Agentic Systems & DevTools')
        if theme in themed_articles:
            themed_articles[theme].append(article)

    return themed_articles


def get_theme_counts(themed_articles: Dict[str, List[Dict]]) -> Dict[str, int]:
    """Get article counts per theme."""
    return {theme: len(articles) for theme, articles in themed_articles.items()}
