"""
TF-IDF Cosine Similarity Classifier for AI Pulse.
Provides zero-dependency, sub-millisecond semantic classification of news articles
into the 7 strategic AI Pulse themes using Term Frequency - Inverse Document Frequency
and Cosine Similarity.
"""

import math
import re
from typing import Dict, List, Optional, Tuple, Set

from config.themes import THEMES


def _tokenize(text: str) -> List[str]:
    """Tokenize text into lowercase alphanumeric words."""
    if not text:
        return []
    return re.findall(r"\b[a-z0-9_\-]+\b", text.lower())


class TfidfThemeClassifier:
    """Pre-computes TF-IDF vector representations for the 7 themes in THEMES
    and classifies arbitrary articles via Cosine Similarity.
    """

    def __init__(self) -> None:
        self.themes: List[str] = list(THEMES.keys())
        self.vocabulary: Set[str] = set()
        self.theme_doc_tokens: Dict[str, List[str]] = {}
        self.idf: Dict[str, float] = {}
        self.theme_tfidf_norms: Dict[str, Dict[str, float]] = {}

        self._build_corpus()

    def _build_corpus(self) -> None:
        """Construct synthetic documents for each theme from weighted keywords
        and compute IDF + normalized TF-IDF unit vectors.
        """
        # Step 1: Build document token lists for each theme based on weighted keywords
        for theme_name, theme_data in THEMES.items():
            tokens: List[str] = []
            # Include theme name words repeatedly
            theme_name_tokens = _tokenize(theme_name)
            for _ in range(5):
                tokens.extend(theme_name_tokens)

            # Include keywords multiplied by their weight factor
            keywords = theme_data.get("keywords", {})
            for kw, weight in keywords.items():
                kw_tokens = _tokenize(kw)
                multiplier = max(1, int(weight * 3))
                for _ in range(multiplier):
                    tokens.extend(kw_tokens)

            self.theme_doc_tokens[theme_name] = tokens
            self.vocabulary.update(tokens)

        # Step 2: Compute IDF across the 7 theme documents
        # IDF(t) = log((N + 1) / (df(t) + 1)) + 1
        num_docs = len(self.themes)
        for term in self.vocabulary:
            doc_freq = sum(
                1 for tokens in self.theme_doc_tokens.values() if term in tokens
            )
            self.idf[term] = math.log((num_docs + 1) / (doc_freq + 1)) + 1.0

        # Step 3: Compute normalized TF-IDF unit vectors for each theme
        for theme_name, tokens in self.theme_doc_tokens.items():
            total_tokens = max(1, len(tokens))
            term_counts: Dict[str, int] = {}
            for t in tokens:
                term_counts[t] = term_counts.get(t, 0) + 1

            tfidf_vec: Dict[str, float] = {}
            mag_sq = 0.0
            for t, count in term_counts.items():
                tf = count / total_tokens
                val = tf * self.idf[t]
                tfidf_vec[t] = val
                mag_sq += val * val

            magnitude = math.sqrt(mag_sq) if mag_sq > 0 else 1.0
            # Unit vector
            self.theme_tfidf_norms[theme_name] = {
                t: v / magnitude for t, v in tfidf_vec.items()
            }

    def classify(
        self, title: str, summary: str, min_similarity: float = 0.05
    ) -> Tuple[Optional[str], float, Dict[str, float]]:
        """Classify a title + summary by vectorizing the text and computing
        cosine similarity against all theme vectors.

        Returns:
            (best_theme, best_score, score_dict)
            best_theme is None if best_score < min_similarity.
        """
        doc_tokens = _tokenize(f"{title} {summary}")
        if not doc_tokens:
            return None, 0.0, {t: 0.0 for t in self.themes}

        total_tokens = len(doc_tokens)
        term_counts: Dict[str, int] = {}
        for t in doc_tokens:
            term_counts[t] = term_counts.get(t, 0) + 1

        # Calculate TF-IDF for input doc
        doc_tfidf: Dict[str, float] = {}
        mag_sq = 0.0
        for t, count in term_counts.items():
            idf_val = self.idf.get(t, 1.0)
            tf = count / total_tokens
            val = tf * idf_val
            doc_tfidf[t] = val
            mag_sq += val * val

        doc_magnitude = math.sqrt(mag_sq) if mag_sq > 0 else 1.0
        doc_unit_vec = {t: v / doc_magnitude for t, v in doc_tfidf.items()}

        # Compute cosine similarity dot product against each theme unit vector
        scores: Dict[str, float] = {}
        for theme_name, theme_unit_vec in self.theme_tfidf_norms.items():
            dot_product = 0.0
            for term, val in doc_unit_vec.items():
                if term in theme_unit_vec:
                    dot_product += val * theme_unit_vec[term]
            scores[theme_name] = round(dot_product, 4)

        best_theme = max(scores, key=scores.get)
        best_score = scores[best_theme]

        if best_score < min_similarity:
            return None, best_score, scores

        return best_theme, best_score, scores


# Singleton instance
_classifier_instance: Optional[TfidfThemeClassifier] = None


def get_tfidf_classifier() -> TfidfThemeClassifier:
    """Return singleton TfidfThemeClassifier instance."""
    global _classifier_instance
    if _classifier_instance is None:
        _classifier_instance = TfidfThemeClassifier()
    return _classifier_instance


def tfidf_classify(
    title: str, summary: str, min_similarity: float = 0.05
) -> Tuple[Optional[str], float, Dict[str, float]]:
    """Convenience wrapper for TF-IDF classification."""
    return get_tfidf_classifier().classify(
        title, summary, min_similarity=min_similarity
    )
