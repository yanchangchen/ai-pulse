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
logger = logging.getLogger("purge_runs")

# Targets to purge
TARGET_TIMESTAMPS = [
    "23:34:29",
    "23:10:42",
    "23:10:21",
    "22:35:44",
    "22:25:28",
    "22:20:47",
]

def main():
    supabase = get_supabase_manager()
    logger.info("Supabase available: %s", supabase.is_available())

    purged_supabase_runs = []

    if supabase.is_available():
        try:
            res = supabase.client.table("trend_runs").select("*").execute()
            all_runs = res.data or []
            logger.info("Found %d total runs in Supabase.", len(all_runs))

            for run in all_runs:
                run_id = run.get("id")
                ts = str(run.get("run_timestamp", ""))
                created = str(run.get("created_at", ""))

                match = any(t in ts or t in created for t in TARGET_TIMESTAMPS)
                if match:
                    logger.info("Purging Supabase run ID '%s' (timestamp: %s)...", run_id, ts)

                    # Delete child records first
                    try:
                        supabase.client.table("theme_summaries").delete().eq("run_id", run_id).execute()
                    except Exception as e:
                        logger.warning("Error deleting theme_summaries for run %s: %s", run_id, e)

                    try:
                        supabase.client.table("articles").delete().eq("run_id", run_id).execute()
                    except Exception as e:
                        logger.warning("Error deleting articles for run %s: %s", run_id, e)

                    try:
                        supabase.client.table("trend_metrics").delete().eq("run_id", run_id).execute()
                    except Exception as e:
                        pass

                    try:
                        supabase.client.table("quality_evaluations").delete().eq("run_id", run_id).execute()
                    except Exception as e:
                        pass

                    # Delete parent run
                    supabase.client.table("trend_runs").delete().eq("id", run_id).execute()
                    purged_supabase_runs.append(f"{run_id} ({ts})")

        except Exception as e:
            logger.error("Error purging Supabase runs: %s", e)

    # 2. Purge from local history.json if present
    history = load_full_history()
    purged_local_keys = []

    if isinstance(history, dict):
        keys_to_delete = []
        for ts, run in history.items():
            if any(t in str(ts) for t in TARGET_TIMESTAMPS):
                keys_to_delete.append(ts)

        for k in keys_to_delete:
            del history[k]
            purged_local_keys.append(k)

        if keys_to_delete:
            with open("history.json", "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
            logger.info("Saved updated history.json after deleting %d runs.", len(keys_to_delete))

    print(f"\n[SUMMARY] Purge Execution Complete:")
    print(f" - Supabase runs deleted: {len(purged_supabase_runs)} -> {purged_supabase_runs}")
    print(f" - Local history runs deleted: {len(purged_local_keys)} -> {purged_local_keys}")

if __name__ == "__main__":
    main()
