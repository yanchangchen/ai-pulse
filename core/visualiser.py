"""
Word cloud visualisation module for AI Pulse.
Generates word clouds and trending topics for each theme.

Returns image bytes (PNG) instead of raw Figure objects so that results
are JSON-serialisable and safely cacheable.
"""

import io
import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend — no GUI needed
import matplotlib.pyplot as plt
import numpy as np
from wordcloud import WordCloud, STOPWORDS

from config.themes import THEMES, THEME_COLORS, THEME_ORDER

# Custom stopwords specific to AI news
# Custom stopwords specific to AI news (low-signal verbs, time indicators, generic prose)
CUSTOM_STOPWORDS = {
    # Time indicators
    "today", "yesterday", "tomorrow", "week", "weeks", "month", "months",
    "year", "years", "day", "days", "daily", "weekly", "monthly", "yearly",
    "now", "recent", "recently", "latest", "past", "future", "new", "first",
    "next", "last", "earlier", "ago", "currently", "soon", "upcoming",

    # Common verbs & gerunds
    "use", "using", "used", "uses", "make", "makes", "made", "making",
    "build", "builds", "building", "built", "create", "creates", "created", "creating",
    "launch", "launches", "launched", "launching", "release", "releases", "released", "releasing",
    "announce", "announces", "announced", "announcing", "show", "shows", "showed", "showing",
    "provide", "provides", "provided", "providing", "help", "helps", "helped", "helping",
    "need", "needs", "needed", "needing", "want", "wants", "wanted", "wanting",
    "work", "works", "worked", "working", "run", "runs", "ran", "running",
    "include", "includes", "included", "including", "find", "finds", "found", "finding",
    "take", "takes", "took", "taken", "taking", "give", "gives", "gave", "given", "giving",
    "come", "comes", "came", "coming", "go", "goes", "went", "going",
    "say", "says", "said", "saying", "get", "gets", "got", "getting",
    "think", "thinks", "thought", "thinking", "see", "sees", "saw", "seeing",
    "know", "knows", "knew", "knowing", "look", "looks", "looked", "looking",
    "tell", "tells", "told", "telling", "try", "tries", "tried", "trying",
    "call", "calls", "called", "calling", "feel", "feels", "felt", "feeling",
    "become", "becomes", "became", "becoming", "allow", "allows", "allowed", "allowing",
    "enable", "enables", "enabled", "enabling", "support", "supports", "supported", "supporting",
    "lead", "leads", "led", "leading", "bring", "brings", "brought", "bringing",
    "set", "sets", "setting", "put", "puts", "putting", "keep", "keeps", "kept", "keeping",
    "start", "starts", "started", "starting", "stop", "stops", "stopped", "stopping",

    # Generic high-frequency prose words
    "ai", "artificial", "intelligence", "also", "will", "can", "one", "two", "three", "four", "five",
    "according", "would", "could", "may", "might", "must", "should", "shall",
    "just", "like", "well", "even", "really", "way", "thing", "things", "people",
    "time", "report", "reports", "news", "article", "articles", "post", "posts", "blog", "blogs",
    "many", "much", "more", "most", "some", "such", "than", "other", "others", "another",
    "part", "parts", "company", "companies", "system", "systems", "tech", "technology",
    "high", "low", "big", "small", "good", "better", "best", "bad", "worse", "worst"
}

# Combine with default stopwords
ALL_STOPWORDS = STOPWORDS.union(CUSTOM_STOPWORDS)

# Theme color palettes
THEME_COLOR_MAPS = {
    "AI Applications & Architecture": "Blues",
    "AI Models": "Purples",
    "AI Infrastructure": "Oranges",
    "AI Companies & Business": "Greens",
    "AI in Government & Policy": "Reds"
}


def canonicalize_word(word: str) -> str:
    """Normalize a word into its canonical singular form to combine variants.
    e.g., 'agents' -> 'agent', 'models' -> 'model', 'benchmarks' -> 'benchmark'
    """
    word = word.lower().strip()
    if len(word) <= 3:
        return word

    if word.endswith("ies") and len(word) > 5:
        return word[:-3] + "y"
    elif word.endswith("es") and word[:-2].endswith(("sh", "ch", "ss", "x", "z")):
        return word[:-2]
    elif word.endswith("s") and not word.endswith(("ss", "us", "is", "os", "as")):
        return word[:-1]

    return word


def preprocess_text(text: str) -> str:
    """Clean and preprocess text for word cloud."""
    # Convert to lowercase
    text = text.lower()

    # Remove URLs
    text = re.sub(r'http\S+|www\S+', '', text)

    # Remove special characters but keep spaces
    text = re.sub(r'[^a-zA-Z\s]', ' ', text)

    # Remove extra whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    return text


def extract_top_words(text: str, n: int = 20) -> List[Tuple[str, int]]:
    """Extract top N words from text, excluding stopwords and combining singular/plural variants."""
    words = text.split()

    canonical_words = []
    for word in words:
        w_clean = word.lower().strip()
        if w_clean in ALL_STOPWORDS or len(w_clean) <= 2:
            continue
        c_word = canonicalize_word(w_clean)
        if c_word not in ALL_STOPWORDS and len(c_word) > 2:
            canonical_words.append(c_word)

    word_counts = Counter(canonical_words)
    return word_counts.most_common(n)


def _fig_to_bytes(fig: plt.Figure) -> bytes:
    """Render a matplotlib Figure to PNG bytes and close it."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def generate_wordcloud(theme_name: str, articles: List[Dict]) -> Optional[bytes]:
    """Generate a word cloud PNG for a theme from its articles.

    Returns PNG image bytes or None if there is nothing to render.
    """
    if not articles:
        return None

    # Combine all titles and summaries
    combined_text = ""
    for article in articles:
        title = article.get('title', '')
        summary = article.get('summary', '')
        combined_text += f"{title} {summary} "

    # Preprocess
    cleaned_text = preprocess_text(combined_text)

    if not cleaned_text.strip():
        return None

    # Get theme color
    color_map = THEME_COLOR_MAPS.get(theme_name, "Blues")

    # Create word cloud
    try:
        wordcloud = WordCloud(
            width=800,
            height=400,
            background_color='white',
            stopwords=ALL_STOPWORDS,
            colormap=color_map,
            max_words=100,
            min_font_size=10,
            max_font_size=100,
            random_state=42
        ).generate(cleaned_text)

        # Create matplotlib figure
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.imshow(wordcloud, interpolation='bilinear')
        ax.axis('off')
        ax.set_title(f"{theme_name}", fontsize=14, fontweight='bold', pad=10)

        plt.tight_layout()
        return _fig_to_bytes(fig)

    except Exception as e:
        print(f"Error generating word cloud for {theme_name}: {str(e)}")
        return None


def generate_all_wordclouds(themed_articles: Dict[str, List[Dict]]) -> Dict[str, bytes]:
    """Generate word cloud PNGs for all themes."""
    wordclouds: Dict[str, bytes] = {}

    for theme in THEME_ORDER:
        articles = themed_articles.get(theme, [])
        img = generate_wordcloud(theme, articles)
        if img:
            wordclouds[theme] = img

    return wordclouds


def get_top_words_for_theme(theme_name: str, articles: List[Dict], n: int = 20) -> List[Tuple[str, int]]:
    """Get top N trending words for a theme."""
    if not articles:
        return []

    combined_text = ""
    for article in articles:
        title = article.get('title', '')
        summary = article.get('summary', '')
        combined_text += f"{title} {summary} "

    cleaned_text = preprocess_text(combined_text)
    return extract_top_words(cleaned_text, n)


def create_word_frequency_chart(top_words: List[Tuple[str, int]], theme_name: str) -> Optional[bytes]:
    """Create a horizontal bar chart of word frequencies as PNG bytes."""
    if not top_words:
        return None

    words = [w[0] for w in top_words]
    counts = [w[1] for w in top_words]

    # Get theme color
    theme_color = THEME_COLORS.get(theme_name, '#1f77b4')

    fig, ax = plt.subplots(figsize=(10, 6))

    y_pos = np.arange(len(words))
    ax.barh(y_pos, counts, color=theme_color, alpha=0.8)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(words)
    ax.invert_yaxis()  # Labels read top-to-bottom
    ax.set_xlabel('Frequency')
    ax.set_title(f"Top {len(words)} Trending Words: {theme_name}", fontsize=12, fontweight='bold')

    plt.tight_layout()
    return _fig_to_bytes(fig)
