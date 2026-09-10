"""
Theme classification module for AI Pulse.
Classifies articles into 7 strategic themes using a 4-pass waterfall pipeline:
1. Pass 1: Exact weighted keyword matching (keyword_classify)
2. Pass 2: TF-IDF Cosine Similarity matching (tfidf_classify)
3. Pass 3: Batched LLM classification via Model Gateway (with fallback & provenance)
4. Pass 4: Soft-match heuristic fallback (find_closest_theme)

In "deterministic" mode, Pass 3 is skipped — articles that miss Gates 1+2 go
directly to Gate 4.  The mode is stored in ``config/custom_settings.json``
under ``classification_mode`` ("hybrid" | "deterministic").
"""

import re
import json
import logging
import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from config.themes import THEMES
from core.llm_client import LLMClient, LLMClientError
from core.tfidf_classifier import tfidf_classify
from core.ai_gateway import ModelGateway, get_gateway, AITaskRequest, TaskType

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Shared LLM client instance (initialised lazily) - kept for backward compat
_llm: Optional[LLMClient] = None

# Global store for latest classification gate metrics
_last_gate_stats: Dict[str, int] = {
    "gate_1_keyword": 0,
    "gate_2_tfidf": 0,
    "gate_3_llm": 0,
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


def should_suggest_deterministic_mode() -> bool:
    """Return True if recent gate stats suggest Gate 3 catch rate is consistently
    below the auto-disable threshold, meaning deterministic classification may
    be sufficient without LLM assistance."""
    from config.settings import get_classification_settings
    settings = get_classification_settings()
    history = settings.get("gate_stats_history", [])
    threshold = settings.get("gate3_auto_disable_threshold", 0.05)
    # Check last 5 hybrid-mode runs
    hybrid_runs = [h for h in history if h.get("mode") == "hybrid"][-5:]
    if len(hybrid_runs) < 3:
        return False
    return all(h.get("gate3_rate", 1.0) < threshold for h in hybrid_runs)


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


async def classify_with_gateway(title: str, summary: str) -> Optional[str]:
    """Use the Model Gateway to classify a single article with fallback & provenance."""
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

    gateway = get_gateway()
    request = AITaskRequest(
        task=TaskType.CATEGORISE,
        input=prompt,
        temperature=0.1,
        max_tokens=50,
    )

    try:
        result = await gateway.execute(request)
        if result.is_success():
            theme = str(result.result).strip()
            valid_themes = list(THEMES.keys())
            for valid_theme in valid_themes:
                if valid_theme.lower() in theme.lower():
                    return valid_theme
    except Exception as exc:
        logger.error("Gateway classification error: %s", exc)

    return None


def classify_with_ollama(title: str, summary: str) -> Optional[str]:
    """Synchronous wrapper for backward compatibility."""
    return asyncio.run(classify_with_gateway(title, summary))


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


def classify_articles(articles: List[Dict], skip_llm: Optional[bool] = None) -> Dict[str, List[Dict]]:
    """Classify all articles into strategic themes using a 4-pass waterfall:
    - Pass 1: Weighted Keyword Matching
    - Pass 2: TF-IDF Cosine Similarity
    - Pass 3: Batched LLM Classifier (skipped in deterministic mode)
    - Pass 4: Soft-Match Heuristic Fallback

    Args:
        articles: List of article dicts with 'title' and 'summary' keys.
        skip_llm: Override for whether to skip Gate 3 (LLM). When None,
            reads from ``classification_mode`` in custom_settings.json
            (``"deterministic"`` → skip, ``"hybrid"`` → include).

    Returns:
        Dictionary with theme names as keys and lists of articles as values.
    """
    global _last_gate_stats

    from config.settings import get_classification_settings, update_classification_settings

    cls_settings = get_classification_settings()
    if skip_llm is None:
        skip_llm = (cls_settings.get("classification_mode") == "deterministic")

    gate_counts = {
        "gate_1_keyword": 0,
        "gate_2_tfidf": 0,
        "gate_3_llm": 0,
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
    # Pass 3: Batched LLM Classification via Model Gateway (skippable)
    # ------------------------------------------------------------------
    unmatched_after_pass3: List[Dict] = []

    if skip_llm:
        logger.info(
            "Classification mode=deterministic: skipping Gate 3 (LLM) for %d articles",
            len(unmatched_after_pass2),
        )
        unmatched_after_pass3 = unmatched_after_pass2
    elif unmatched_after_pass2:
        batch_size = 20

        async def _classify_batch(batch: List[Dict]) -> int:
            """Classify a batch using the Model Gateway."""
            gateway = get_gateway()
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

            request = AITaskRequest(
                task=TaskType.CATEGORISE,
                input=prompt,
                system=system_prompt,
                temperature=0.1,
                max_tokens=1000,
            )

            try:
                result = await gateway.execute(request)
                if result.is_success():
                    mapping = result.result
                    if isinstance(mapping, str):
                        json_match = re.search(r'\{.*\}', mapping, re.DOTALL)
                        if json_match:
                            mapping = json.loads(json_match.group(0))

                    classified = 0
                    for idx, article in enumerate(batch):
                        id_key = f"ID {idx}"
                        theme_name = mapping.get(id_key)
                        if theme_name:
                            for valid_theme in THEMES.keys():
                                if valid_theme.lower() in theme_name.lower():
                                    article['theme'] = valid_theme
                                    article['gate'] = 3
                                    article['gate_provenance'] = result.provenance.to_dict() if hasattr(result.provenance, 'to_dict') else {}
                                    classified += 1
                                    break
                    return classified
            except Exception as exc:
                logger.error("Batch gateway classification error: %s", exc)
            return 0

        # Run all batches
        for i in range(0, len(unmatched_after_pass2), batch_size):
            batch = unmatched_after_pass2[i:i + batch_size]
            classified_count = asyncio.run(_classify_batch(batch))
            gate_counts["gate_3_llm"] += classified_count

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
        "Classification complete [%d total]. Pass 1 (Keywords): %d, Pass 2 (TF-IDF): %d, Pass 3 (Gateway): %d, Pass 4 (Heuristic): %d%s",
        len(articles),
        gate_counts["gate_1_keyword"],
        gate_counts["gate_2_tfidf"],
        gate_counts["gate_3_llm"],
        gate_counts["gate_4_heuristic"],
        " [DETERMINISTIC MODE]" if skip_llm else "",
    )

    # ------------------------------------------------------------------
    # Persist gate stats to custom_settings.json
    # ------------------------------------------------------------------
    gate3_rate = gate_counts["gate_3_llm"] / max(gate_counts["total"], 1)
    history = cls_settings.get("gate_stats_history", [])
    history.append({
        "timestamp": datetime.now().isoformat(),
        "gate_1_keyword": gate_counts["gate_1_keyword"],
        "gate_2_tfidf": gate_counts["gate_2_tfidf"],
        "gate_3_llm": gate_counts["gate_3_llm"],
        "gate_4_heuristic": gate_counts["gate_4_heuristic"],
        "total": gate_counts["total"],
        "gate3_rate": round(gate3_rate, 4),
        "mode": "deterministic" if skip_llm else "hybrid",
    })
    # Cap at 20 entries
    history = history[-20:]
    try:
        update_classification_settings(gate_stats_history=history)
    except Exception as exc:
        logger.warning("Failed to persist gate stats: %s", exc)

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


# ---------------------------------------------------------------------------
# Heuristic Keyword Auto-Improvement
# ---------------------------------------------------------------------------

# Minimum number of Gate 3/4 articles in a single run that must contain a
# candidate term before it is auto-applied to the theme's keyword dict.
AUTO_APPLY_MIN_ARTICLES = 3

# Terms shorter than this are never suggested (too generic / noisy).
MIN_TERM_LENGTH = 4

# Stopwords specific to keyword extraction (supplements the general set).
_KEYWORD_STOPWORDS = frozenset({
    "also", "about", "after", "another", "because", "between", "both",
    "could", "every", "first", "from", "have", "into", "just", "made",
    "many", "more", "most", "much", "must", "other", "over", "some",
    "such", "than", "that", "their", "them", "then", "there", "these",
    "this", "those", "through", "under", "very", "were", "what", "when",
    "where", "which", "while", "will", "with", "would", "year",
    "week", "month", "time", "report", "according", "company", "said",
    "announced", "released", "launch", "launches", "launching",
})


def extract_keyword_suggestions_from_run(
    themed_articles: Dict[str, List[Dict]],
) -> Dict[str, Any]:
    """Analyse articles classified by Gate 3 or Gate 4 and extract candidate
    keywords that are missing from the assigned theme's keyword dict.

    This is a **purely heuristic** function — no LLM calls.  It keeps the
    auto-improvement loop deterministic, matching the goal of reducing LLM
    dependency for classification.

    Returns a dict with keys:
      - ``auto_applied``: list of ``{theme, term, weight, article_count}``
        that were auto-applied via ``add_keywords_to_theme()``.
      - ``pending``: list of the same shape for suggestions below the
        auto-apply threshold (stored to Supabase for UI review).
    """
    from collections import Counter
    from config.themes import THEMES, add_keywords_to_theme

    # Build a reverse index: which themes already own which keywords?
    all_existing_keywords: Dict[str, str] = {}  # keyword_lower → theme_name
    for theme_name, theme_data in THEMES.items():
        for kw in theme_data["keywords"]:
            all_existing_keywords[kw.lower()] = theme_name

    # Collect candidate terms per theme from Gate 3/4 articles
    # theme_name → Counter of candidate terms
    theme_candidates: Dict[str, Counter] = {}

    for theme_name, articles in themed_articles.items():
        if theme_name not in THEMES:
            continue
        theme_existing = {k.lower() for k in THEMES[theme_name]["keywords"]}
        # Track per-article term presence, not total occurrences
        theme_terms_by_article: List[set] = []

        for article in articles:
            gate = article.get("gate", 1)
            if gate not in (3, 4):
                continue  # Only analyse articles that fell through to LLM/heuristic

            title = article.get("title", "")
            summary = article.get("summary", "")
            text = f"{title} {summary}"

            # Extract candidate terms: alpha tokens of length >= MIN_TERM_LENGTH
            tokens = [
                w for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]+", text)
                if len(w) >= MIN_TERM_LENGTH
                and w.lower() not in _KEYWORD_STOPWORDS
                and w.lower() not in theme_existing
            ]
            # Also extract bigrams (two-word phrases)
            words = text.split()
            for i in range(len(words) - 1):
                w1, w2 = words[i].strip(".,;:!?()[]"), words[i + 1].strip(".,;:!?()[]")
                if (len(w1) >= 3 and len(w2) >= 3
                        and w1[0].isupper() and w2[0].isupper()
                        and w1.lower() not in _KEYWORD_STOPWORDS
                        and w2.lower() not in _KEYWORD_STOPWORDS):
                    bigram = f"{w1} {w2}"
                    if bigram.lower() not in theme_existing:
                        tokens.append(bigram)

            # Count each term once per article
            article_terms = {t.lower() for t in tokens}
            theme_terms_by_article.append(article_terms)

        # Count how many articles contain each term
        term_article_counts: Counter = Counter()
        for article_terms in theme_terms_by_article:
            for term in article_terms:
                term_article_counts[term] += 1

        if term_article_counts:
            theme_candidates[theme_name] = term_article_counts

    # Classify candidates into auto-applied vs pending
    auto_applied: List[Dict] = []
    pending: List[Dict] = []

    for theme_name, counter in theme_candidates.items():
        for term, count in counter.most_common(20):
            # Skip if the term already belongs to another theme
            if term in all_existing_keywords and all_existing_keywords[term] != theme_name:
                continue

            suggestion = {
                "theme": theme_name,
                "term": term,
                "weight": 2,  # default weight for auto-discovered keywords
                "article_count": count,
            }

            if count >= AUTO_APPLY_MIN_ARTICLES:
                auto_applied.append(suggestion)
            elif count >= 2:
                # At least 2 articles — worth reviewing but not auto-applied
                pending.append(suggestion)

    # Auto-apply high-confidence keywords
    applied_terms: Dict[str, Dict[str, int]] = {}  # theme → {term: weight}
    for s in auto_applied:
        theme = s["theme"]
        term = s["term"]
        weight = s["weight"]
        if theme not in applied_terms:
            applied_terms[theme] = {}
        applied_terms[theme][term] = weight

    for theme, keywords in applied_terms.items():
        add_keywords_to_theme(theme, keywords)
        # Update the reverse index so later suggestions don't conflict
        for kw in keywords:
            all_existing_keywords[kw.lower()] = theme

    # Store suggestions to Supabase (graceful degradation if unavailable)
    _store_keyword_suggestions(auto_applied, pending)

    logger.info(
        "Keyword auto-improvement: %d auto-applied, %d pending review",
        len(auto_applied), len(pending),
    )

    return {"auto_applied": auto_applied, "pending": pending}


def _store_keyword_suggestions(
    auto_applied: List[Dict],
    pending: List[Dict],
) -> None:
    """Store keyword suggestions to Supabase for the Quality Evaluation UI."""
    try:
        from core.quality_schema import insert_keyword_suggestions
        from core.supabase_client import get_supabase_manager

        supabase = get_supabase_manager()
        if not supabase.is_available():
            return

        rows = []
        for s in auto_applied:
            rows.append({
                "kind": "theme_keyword",
                "theme_name": s["theme"],
                "term": s["term"],
                "suggested_weight": s["weight"],
                "reason": f"Auto-discovered: appeared in {s['article_count']} Gate 3/4 articles",
                "status": "auto_applied",
            })
        for s in pending:
            rows.append({
                "kind": "theme_keyword",
                "theme_name": s["theme"],
                "term": s["term"],
                "suggested_weight": s["weight"],
                "reason": f"Candidate: appeared in {s['article_count']} Gate 3/4 articles",
                "status": "pending",
            })

        if rows:
            insert_keyword_suggestions(supabase, rows)

    except ImportError:
        logger.debug("quality_schema or supabase_client not available, skipping suggestion storage")
    except Exception as exc:
        logger.warning("Failed to store keyword suggestions to Supabase: %s", exc)
