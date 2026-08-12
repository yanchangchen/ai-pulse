import os
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

# Load credentials from .streamlit/secrets.toml if present
secrets_path = Path(".streamlit/secrets.toml")
if secrets_path.exists():
    import toml
    try:
        sec = toml.load(secrets_path)
        if "SUPABASE_URL" in sec:
            os.environ["SUPABASE_URL"] = sec["SUPABASE_URL"]
        if "SUPABASE_KEY" in sec:
            os.environ["SUPABASE_KEY"] = sec["SUPABASE_KEY"]
    except Exception:
        pass

from config.themes import THEME_ORDER
from core.fetcher import fetch_all_news
from core.classifier import classify_articles
from core.non_llm_summariser import generate_non_llm_theme_summary
from core.history_manager import save_run_to_history
from core.supabase_client import get_supabase_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rerun_deepdive")


def rerun_deepdive():
    logger.info("Starting fresh data fetch across all RSS and Web sources...")
    raw_articles = fetch_all_news()
    logger.info("Fetched %d raw articles.", len(raw_articles))

    if not raw_articles:
        logger.error("No articles fetched!")
        return

    logger.info("Classifying articles into strategic themes...")
    themed_articles = classify_articles(raw_articles)

    full_articles = []
    article_counts = {}
    for theme, arts in themed_articles.items():
        article_counts[theme] = len(arts)
        full_articles.extend(arts)

    info_prefix = "ℹ️ *Non-LLM Extractive Summary: Generated deterministically using lead sentence extraction because live LLM synthesis is paused.*"
    summaries = {}

    logger.info("Generating fresh non-LLM summaries across all strategic themes...")
    for theme in THEME_ORDER:
        articles_for_theme = themed_articles.get(theme, [])
        logger.info("Theme '%s': %d articles", theme, len(articles_for_theme))

        fresh_sum = generate_non_llm_theme_summary(theme, articles_for_theme)
        orig_signal = fresh_sum.get("what_is_happening", "")
        if info_prefix not in orig_signal:
            fresh_sum["what_is_happening"] = f"{info_prefix}\n\n{orig_signal}"

        summaries[theme] = fresh_sum

    logger.info("Saving fresh run to history and Supabase...")
    saved_ts = save_run_to_history(
        summaries=summaries,
        article_counts=article_counts,
        full_articles=full_articles,
        themed_articles=themed_articles
    )

    print(f"\n[SUCCESS] Fresh Deep Dive Summaries Rerun Complete!")
    print(f" - Timestamp: {saved_ts}")
    print(f" - Total articles: {len(full_articles)}")
    for t in THEME_ORDER:
        print(f"   • {t}: {article_counts.get(t, 0)} articles")


if __name__ == "__main__":
    rerun_deepdive()
