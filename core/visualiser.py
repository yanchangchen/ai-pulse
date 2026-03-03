"""
Word cloud visualisation module for AI Pulse.
Generates word clouds and trending topics for each theme.
"""

import re
from collections import Counter
from typing import List, Dict, Tuple
import matplotlib.pyplot as plt
from wordcloud import WordCloud, STOPWORDS
import numpy as np

from config.themes import THEMES, THEME_COLORS, THEME_ORDER

# Custom stopwords specific to AI news
CUSTOM_STOPWORDS = {
    "ai", "artificial", "intelligence", "says", "new", "week", "year",
    "also", "will", "can", "use", "using", "used", "one", "two", "three",
    "first", "last", "said", "according", "would", "could", "may", "might",
    "just", "like", "get", "make", "made", "know", "think", "see", "come",
    "look", "want", "give", "take", "tell", "try", "call", "need", "feel",
    "become", "back", "still", "well", "even", "really", "way", "thing",
    "things", "people", "time", "day", "days", "today", "yesterday",
    "report", "reports", "news", "article", "post", "blog", "says"
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
    """Extract top N words from text, excluding stopwords."""
    words = text.split()

    # Filter stopwords
    filtered_words = [
        word for word in words
        if word not in ALL_STOPWORDS and len(word) > 2
    ]

    # Count frequencies
    word_counts = Counter(filtered_words)

    return word_counts.most_common(n)


def generate_wordcloud(theme_name: str, articles: List[Dict]):
    """Generate a word cloud for a theme from its articles."""
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
        return fig

    except Exception as e:
        print(f"Error generating word cloud for {theme_name}: {str(e)}")
        return None


def generate_all_wordclouds(themed_articles: Dict[str, List[Dict]]) -> Dict[str, plt.Figure]:
    """Generate word clouds for all themes."""
    wordclouds = {}

    for theme in THEME_ORDER:
        articles = themed_articles.get(theme, [])
        fig = generate_wordcloud(theme, articles)
        if fig:
            wordclouds[theme] = fig

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


def create_word_frequency_chart(top_words: List[Tuple[str, int]], theme_name: str):
    """Create a horizontal bar chart of word frequencies."""
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
    return fig
