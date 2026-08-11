"""
Backfill script: Inspects Supabase Memory Wiki 'theme_summaries' table and local 'history.json',
finds entries where LLM summarization did not run or returned stub signals, and regenerates
high-quality LexRank signals (what_is_happening) WITHOUT modifying 'why_it_matters',
'engineering_tradeoffs', or other fields.
"""

import json
import os
import logging
from pathlib import Path

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

from core.supabase_client import get_supabase_manager
from core.history_manager import load_full_history
from core.non_llm_summariser import generate_non_llm_theme_summary

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backfill_signals")


def run_signal_backfill():
    logger.info("Checking Supabase and Memory Wiki for theme summaries requiring signal backfill...")
    supabase = get_supabase_manager()

    supabase_updated_count = 0

    # 1. Backfill Supabase 'theme_summaries' table
    if supabase.is_available():
        logger.info("Querying Supabase 'theme_summaries' table...")
        try:
            res = supabase.client.table("theme_summaries").select("*").execute()
            theme_summaries = res.data or []
            logger.info("Found %d total theme summary records in Supabase.", len(theme_summaries))

            for summary_rec in theme_summaries:
                rec_id = summary_rec.get("id")
                run_id = summary_rec.get("run_id")
                theme_name = summary_rec.get("theme_name")
                current_signal = summary_rec.get("what_is_happening", "")

                is_stub = (
                    not current_signal
                    or "Non-LLM Extractive Summary: Generated deterministically" in current_signal
                    or "Extractive summary unavailable" in current_signal
                    or "No articles available" in current_signal
                    or current_signal.startswith("Error generating summary")
                )

                if is_stub and run_id and theme_name:
                    # Fetch articles for this run & theme
                    art_res = supabase.client.table("articles").select("*").eq("run_id", run_id).eq("theme_name", theme_name).execute()
                    articles = art_res.data or []

                    if not articles:
                        # Fallback fetch recent articles for theme
                        art_res_all = supabase.client.table("articles").select("*").eq("theme_name", theme_name).limit(10).execute()
                        articles = art_res_all.data or []

                    if articles:
                        fresh_summary = generate_non_llm_theme_summary(theme_name, articles)
                        new_signal = fresh_summary["what_is_happening"]

                        # ONLY update what_is_happening (The Signal)
                        supabase.client.table("theme_summaries").update({
                            "what_is_happening": new_signal
                        }).eq("id", rec_id).execute()

                        supabase_updated_count += 1
                        logger.info("Updated Supabase signal for theme summary ID '%s' (%s)", rec_id, theme_name)

        except Exception as e:
            logger.error("Error during Supabase theme_summaries signal backfill: %s", e)

    # 2. Backfill local history.json
    history_runs = load_full_history()
    local_updated_count = 0

    if isinstance(history_runs, dict):
        for ts, run in history_runs.items():
            if not isinstance(run, dict):
                continue

            summaries = run.get("summaries", {})
            articles = run.get("full_articles", []) or run.get("articles", [])

            if not summaries or not isinstance(summaries, dict):
                continue

            for theme, sum_data in summaries.items():
                if not isinstance(sum_data, dict):
                    continue

                current_signal = sum_data.get("what_is_happening", "")
                is_stub = (
                    not current_signal
                    or "Non-LLM Extractive Summary: Generated deterministically" in current_signal
                    or "Extractive summary unavailable" in current_signal
                    or "No articles available" in current_signal
                    or current_signal.startswith("Error generating summary")
                )

                if is_stub and articles:
                    theme_articles = [a for a in articles if a.get("theme") == theme]
                    if not theme_articles:
                        theme_articles = articles[:5]

                    fresh_summary = generate_non_llm_theme_summary(theme, theme_articles)
                    # ONLY update what_is_happening
                    sum_data["what_is_happening"] = fresh_summary["what_is_happening"]
                    local_updated_count += 1

    if local_updated_count > 0:
        with open("history.json", "w", encoding="utf-8") as f:
            json.dump(history_runs, f, indent=2, ensure_ascii=False)
        logger.info("Updated local history.json with fresh non-LLM signals.")

    print(f"[SUCCESS] Signal Backfill Complete: {supabase_updated_count} Supabase records and {local_updated_count} local history records updated.")


if __name__ == "__main__":
    run_signal_backfill()
