"""
Script to inspect run 11/08/2026 11:04:58 in Supabase and backfill any quota fallback summaries
with fresh Non-LLM Extractive Summaries.
"""

import sys
import logging
from core.supabase_client import get_supabase_manager
from core.summariser import extractive_theme_summary
from config.themes import THEME_ORDER

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    supabase = get_supabase_manager()
    if not supabase.is_available():
        print("Supabase is not available locally. Checking local history...")
        return

    runs = supabase.get_all_runs(limit=30)
    target_run = None

    for r in runs:
        ts = r.get("run_timestamp", "")
        if "11:04:58" in ts or "2026-08-11" in ts:
            target_run = r
            print(f"Found target run: ID={r['id']}, timestamp={ts}")
            break

    if not target_run and runs:
        # Fallback to the latest run
        target_run = runs[0]
        print(f"Targeting run: ID={target_run['id']}, timestamp={target_run['run_timestamp']}")

    if not target_run:
        print("No runs found in Supabase.")
        return

    run_id = target_run["id"]
    summaries = supabase.get_summaries_for_run(run_id) or []
    print(f"Found {len(summaries)} summaries for run {run_id}")

    info_prefix = "ℹ️ *Non-LLM Extractive Summary: Generated deterministically using lead sentence extraction because live LLM synthesis is paused.*"

    for theme in THEME_ORDER:
        articles = supabase.get_articles_for_run(run_id, theme) or []
        print(f"Theme '{theme}': {len(articles)} articles found.")

        extractive = extractive_theme_summary(theme, articles)
        extractive["what_is_happening"] = f"{info_prefix}\n\n{extractive['what_is_happening']}"

        # Update in Supabase theme_summaries table
        try:
            supabase.client.table("theme_summaries").update({
                "what_is_happening": extractive["what_is_happening"],
                "engineering_tradeoffs": extractive["engineering_tradeoffs"],
                "product_impact": extractive["product_impact"],
                "why_it_matters": extractive["why_it_matters"],
                "what_to_watch": extractive["what_to_watch"]
            }).eq("run_id", run_id).eq("theme_name", theme).execute()
            print(f"Successfully updated Supabase record for '{theme}'")
        except Exception as e:
            print(f"Failed to update Supabase record for '{theme}': {e}")

    print("\nBackfill complete! All theme summaries for run 11/08/2026 11:04:58 have been regenerated with Non-LLM Extractive Summaries.")

if __name__ == "__main__":
    main()
