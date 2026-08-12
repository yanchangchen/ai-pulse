import os
import json
import logging
from pathlib import Path

# Load credentials if present
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

logging.basicConfig(level=logging.WARNING)

def preview_sample():
    raw_articles = fetch_all_news()
    themed_articles = classify_articles(raw_articles)

    info_prefix = "ℹ️ *Non-LLM Extractive Summary: Generated deterministically using lead sentence extraction because live LLM synthesis is paused.*"

    sample_output = {}

    for theme in THEME_ORDER:
        articles = themed_articles.get(theme, [])
        summary = generate_non_llm_theme_summary(theme, articles)
        orig = summary.get("what_is_happening", "")
        summary["what_is_happening"] = f"{info_prefix}\n\n{orig}"
        sample_output[theme] = summary

    # Write preview JSON for easy inspection
    with open("scratch/sample_output_preview.json", "w", encoding="utf-8") as f:
        json.dump(sample_output, f, indent=2, ensure_ascii=False)

    print("PREVIEW_GENERATED_SUCCESSFULLY")

if __name__ == "__main__":
    preview_sample()
