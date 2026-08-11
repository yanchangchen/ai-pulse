"""
Updates the current run (dated 2026-08-11 11:04:58) in Supabase and history.json
with high-quality LexRank & Luhn non-LLM summaries across all 7 strategic themes.
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
logger = logging.getLogger("update_run_non_llm")


def update_target_run():
    logger.info("Updating current run (2026-08-11 11:04:58) with non-LLM summaries...")
    supabase = get_supabase_manager()

    target_ts_substring = "2026-08-11 11:04:58"
    target_date = "2026-08-11"

    # 1. Update Supabase
    if supabase.is_available():
        try:
            # Find run ID in trend_runs
            runs_res = supabase.client.table("trend_runs").select("*").execute()
            runs = runs_res.data or []
            target_run_id = None

            for r in runs:
                if target_ts_substring in r.get("run_timestamp", "") or r.get("run_date") == target_date:
                    target_run_id = r.get("id")
                    logger.info("Found target trend_run ID in Supabase: %s (%s)", target_run_id, r.get("run_timestamp"))
                    break

            if not target_run_id and runs:
                target_run_id = runs[0].get("id")
                logger.info("Using latest trend_run ID: %s (%s)", target_run_id, runs[0].get("run_timestamp"))

            if target_run_id:
                # Fetch existing summaries
                sum_res = supabase.client.table("theme_summaries").select("*").eq("run_id", target_run_id).execute()
                theme_summaries = sum_res.data or []

                for s_rec in theme_summaries:
                    rec_id = s_rec.get("id")
                    theme_name = s_rec.get("theme_name")

                    # Fetch articles for this theme & run
                    art_res = supabase.client.table("articles").select("*").eq("run_id", target_run_id).eq("theme_name", theme_name).execute()
                    articles = art_res.data or []

                    if not articles:
                        art_res_fallback = supabase.client.table("articles").select("*").eq("theme_name", theme_name).limit(15).execute()
                        articles = art_res_fallback.data or []

                    if articles:
                        non_llm_sum = generate_non_llm_theme_summary(theme_name, articles)
                        supabase.client.table("theme_summaries").update({
                            "what_is_happening": non_llm_sum["what_is_happening"],
                            "engineering_tradeoffs": non_llm_sum["engineering_tradeoffs"],
                            "product_impact": non_llm_sum["product_impact"],
                            "why_it_matters": non_llm_sum["why_it_matters"],
                            "what_to_watch": non_llm_sum["what_to_watch"]
                        }).eq("id", rec_id).execute()
                        logger.info("Updated Supabase theme summary for '%s'", theme_name)

        except Exception as e:
            logger.error("Error updating Supabase target run: %s", e)

    # 2. Update local history.json
    history_runs = load_full_history()
    if isinstance(history_runs, dict):
        for ts, run in history_runs.items():
            if target_ts_substring in ts or target_date in ts or ts == list(history_runs.keys())[-1]:
                logger.info("Updating local history.json run '%s' with non-LLM summaries...", ts)
                summaries = run.get("summaries", {})
                full_articles = run.get("full_articles", []) or run.get("articles", [])
                themed_articles = run.get("themed_articles", {})

                for theme in summaries.keys():
                    articles = themed_articles.get(theme, [])
                    if not articles and full_articles:
                        articles = [a for a in full_articles if a.get("theme") == theme]

                    if articles:
                        non_llm_sum = generate_non_llm_theme_summary(theme, articles)
                        summaries[theme] = non_llm_sum

        with open("history.json", "w", encoding="utf-8") as f:
            json.dump(history_runs, f, indent=2, ensure_ascii=False)
        logger.info("Saved non-LLM summaries to history.json.")

    print("[SUCCESS] Current run 11/08/2026 11:04:58 successfully updated with Non-LLM Extractive summaries.")


if __name__ == "__main__":
    update_target_run()
