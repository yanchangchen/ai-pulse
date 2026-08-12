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

from core.supabase_client import get_supabase_manager
from core.history_manager import load_full_history

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("check_db_backfill")

def check_targets():
    supabase = get_supabase_manager()
    logger.info("Supabase available: %s", supabase.is_available())

    target_phrase = "Non-LLM Extractive Summary"

    if supabase.is_available():
        res = supabase.client.table("theme_summaries").select("*").execute()
        records = res.data or []
        logger.info("Found %d total theme_summaries in Supabase database.", len(records))
        matching_supabase = []
        for r in records:
            signal = r.get("what_is_happening", "") or ""
            why = r.get("why_it_matters", "") or ""
            if target_phrase in signal or target_phrase in why:
                matching_supabase.append(r)
        logger.info("Found %d Supabase records matching '%s'.", len(matching_supabase), target_phrase)
        for m in matching_supabase:
            logger.info("Supabase Match: ID=%s RunID=%s Theme=%s", m.get("id"), m.get("run_id"), m.get("theme_name"))

    history = load_full_history()
    if isinstance(history, dict):
        logger.info("Found %d total runs in local history.json.", len(history))
        for ts, run in history.items():
            summaries = run.get("summaries", {})
            for theme, sum_data in summaries.items():
                if isinstance(sum_data, dict):
                    signal = sum_data.get("what_is_happening", "") or ""
                    why = sum_data.get("why_it_matters", "") or ""
                    if target_phrase in signal or target_phrase in why or "Quota" in signal or "quota" in signal:
                        logger.info("Local Match: Run TS=%s Theme=%s Signal Snippet: %s", ts, theme, signal[:100])

if __name__ == "__main__":
    check_targets()
