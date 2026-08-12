import os
import json
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

from config.themes import THEME_ORDER
from core.supabase_client import get_supabase_manager
from core.non_llm_summariser import generate_non_llm_theme_summary
from core.history_manager import load_full_history

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backfill_runs")

def run_backfill():
    supabase = get_supabase_manager()
    logger.info("Supabase available: %s", supabase.is_available())

    info_prefix = "ℹ️ *Non-LLM Extractive Summary: Generated deterministically using lead sentence extraction because live LLM synthesis is paused.*"

    backfilled_supabase_count = 0

    if supabase.is_available():
        try:
            res = supabase.client.table("trend_runs").select("*").execute()
            all_runs = res.data or []
            logger.info("Found %d total runs in Supabase for backfill check.", len(all_runs))

            for run in all_runs:
                run_id = run.get("id")
                summaries = supabase.get_summaries_for_run(run_id) or []

                for s in summaries:
                    s_id = s.get("id")
                    theme = s.get("theme_name")
                    what = s.get("what_is_happening", "")

                    # Check if this is a non-LLM summary
                    if "Non-LLM Extractive Summary" in what or "LexRank & Luhn" in what or "live LLM quota" in what or "lead sentence extraction" in what:
                        articles = supabase.get_articles_for_run(run_id, theme) or []
                        fresh_summary = generate_non_llm_theme_summary(theme, articles)
                        orig_signal = fresh_summary.get("what_is_happening", "")

                        if info_prefix not in orig_signal:
                            fresh_signal = f"{info_prefix}\n\n{orig_signal}"
                        else:
                            fresh_signal = orig_signal

                        update_data = {
                            "what_is_happening": fresh_signal,
                            "engineering_tradeoffs": fresh_summary.get("engineering_tradeoffs", ""),
                            "product_impact": fresh_summary.get("product_impact", ""),
                            "why_it_matters": fresh_summary.get("why_it_matters", ""),
                            "what_to_watch": fresh_summary.get("what_to_watch", "")
                        }

                        supabase.client.table("theme_summaries").update(update_data).eq("id", s_id).execute()
                        backfilled_supabase_count += 1

        except Exception as e:
            logger.error("Error backfilling Supabase runs: %s", e)

    # Backfill local history.json
    history = load_full_history()
    backfilled_local_count = 0

    if isinstance(history, dict):
        for run_ts, run_data in history.items():
            if not isinstance(run_data, dict):
                continue
            summaries_dict = run_data.get("summaries", {})
            full_articles = run_data.get("full_articles", [])
            themed_articles = run_data.get("themed_articles", {})

            for theme, s_data in summaries_dict.items():
                what = s_data.get("what_is_happening", "")
                if "Non-LLM Extractive Summary" in what or "LexRank & Luhn" in what or "live LLM quota" in what or "lead sentence extraction" in what:
                    articles = themed_articles.get(theme, [])
                    if not articles and full_articles:
                        articles = [a for a in full_articles if a.get("theme") == theme]

                    fresh_summary = generate_non_llm_theme_summary(theme, articles)
                    orig_signal = fresh_summary.get("what_is_happening", "")

                    if info_prefix not in orig_signal:
                        fresh_signal = f"{info_prefix}\n\n{orig_signal}"
                    else:
                        fresh_signal = orig_signal

                    s_data["what_is_happening"] = fresh_signal
                    s_data["engineering_tradeoffs"] = fresh_summary.get("engineering_tradeoffs", "")
                    s_data["product_impact"] = fresh_summary.get("product_impact", "")
                    s_data["why_it_matters"] = fresh_summary.get("why_it_matters", "")
                    s_data["what_to_watch"] = fresh_summary.get("what_to_watch", "")
                    backfilled_local_count += 1

        with open("history.json", "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)

    print(f"\n[SUCCESS] Memory Wiki Non-LLM Backfill Complete!")
    print(f" - Supabase theme summaries updated: {backfilled_supabase_count}")
    print(f" - Local history summaries updated: {backfilled_local_count}")

if __name__ == "__main__":
    run_backfill()
