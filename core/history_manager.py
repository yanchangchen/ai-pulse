"""History and memory manager for AI Pulse.
Handles persisting summaries to JSON (for parsing), memory.md (for context/wiki), and Supabase (cloud).
"""

import json
import os
import logging
import streamlit as st
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Paths
ROOT_DIR = Path(__file__).resolve().parent.parent
HISTORY_JSON = ROOT_DIR / "history.json"
MEMORY_MD = ROOT_DIR / "memory.md"

# In-memory history cache to optimize disk I/O performance
_history_cache: Optional[Dict] = None

def save_run_to_history(
    summaries: Dict[str, Dict[str, str]], 
    article_counts: Dict[str, int],
    full_articles: List[Dict],
    themed_articles: Dict[str, List[Dict]]
) -> None:
    """Save the current summaries and full state to both JSON and Markdown history."""
    global _history_cache
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    date_key = datetime.now().strftime("%Y-%m-%d")

    # 1. Update JSON History
    history_data = {}
    if HISTORY_JSON.exists():
        try:
            with open(HISTORY_JSON, "r", encoding="utf-8") as f:
                history_data = json.load(f)
        except Exception:
            history_data = {}

    history_data[timestamp] = {
        "date": date_key,
        "summaries": summaries,
        "counts": article_counts,
        "full_articles": full_articles,
        "themed_articles": themed_articles
    }

    with open(HISTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(history_data, f, indent=2, ensure_ascii=False)

    # Invalidate cache so it is reloaded from disk next time
    _history_cache = None

    # 2. Update memory.md (Append-only Wiki)
    new_entry = f"\n## ⚡ AI Pulse Run: {timestamp}\n"
    for theme, summary in summaries.items():
        count = article_counts.get(theme, 0)
        new_entry += f"### {theme} ({count} articles)\n"
        new_entry += f"**What is happening:** {summary.get('what_is_happening', '')}\n\n"
        new_entry += f"**Why it matters:** {summary.get('why_it_matters', '')}\n\n"
        new_entry += f"**Watch:** {summary.get('what_to_watch', '')}\n\n"
        new_entry += "---\n"

    if not MEMORY_MD.exists():
        with open(MEMORY_MD, "w", encoding="utf-8") as f:
            f.write("# AI Pulse - Memory Wiki\nTracking the evolution of AI developments.\n")

    with open(MEMORY_MD, "a", encoding="utf-8") as f:
        f.write(new_entry)
    
    # 3. Persist to Supabase (graceful degradation if unavailable)
    _save_to_supabase(timestamp, date_key, summaries, article_counts, full_articles, themed_articles)

def get_recent_context(theme_name: str, limit: int = 2) -> str:
    """Retrieve the most recent summaries for a theme to provide context to the LLM."""
    history_data = load_full_history()
    if not history_data:
        return ""

    try:
        # Sort by timestamp descending
        sorted_keys = sorted(history_data.keys(), reverse=True)
        
        context_parts = []
        for key in sorted_keys[:limit]:
            theme_summary = history_data[key]["summaries"].get(theme_name)
            if theme_summary:
                date = history_data[key]["date"]
                context_parts.append(f"On {date}: {theme_summary.get('what_is_happening')}")
        
        if context_parts:
            return "\nPrevious Context:\n" + "\n".join(context_parts)
    except Exception:
        pass
    
    return ""

def load_full_history() -> Dict:
    """Load the full history with in-memory caching."""
    global _history_cache
    if _history_cache is not None:
        return _history_cache

    if not HISTORY_JSON.exists():
        return {}
    try:
        with open(HISTORY_JSON, "r", encoding="utf-8") as f:
            _history_cache = json.load(f)
            return _history_cache
    except Exception:
        return {}

def get_last_run() -> Optional[Dict]:
    """Retrieve the absolute latest run data."""
    history = load_full_history()
    if not history:
        return None
    
    # Sort by timestamp descending
    latest_ts = sorted(history.keys(), reverse=True)[0]
    return {
        "timestamp": latest_ts,
        "data": history[latest_ts]
    }

def get_last_run_time() -> Optional[datetime]:
    """Get the datetime of the most recent run."""
    last_run = get_last_run()
    if not last_run:
        return None
    try:
        return datetime.strptime(last_run["timestamp"], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _save_to_supabase(
    timestamp: str,
    date_key: str,
    summaries: Dict[str, Dict[str, str]],
    article_counts: Dict[str, int],
    full_articles: List[Dict],
    themed_articles: Dict[str, List[Dict]]
) -> None:
    """Save run data to Supabase with graceful error handling."""
    try:
        from core.supabase_client import get_supabase_manager
        supabase = get_supabase_manager()
        
        if not supabase.is_available():
            logger.debug("Supabase not available, skipping cloud persistence")
            return
        
        # 1. Create trend run record
        run_record = supabase.save_trend_run(
            run_timestamp=timestamp,
            run_date=date_key,
            total_articles=len(full_articles)
        )
        
        if not run_record:
            logger.warning("Failed to create trend run record in Supabase")
            return
        
        run_id = run_record["id"]
        logger.info(f"Created trend run in Supabase: {run_id}")
        
        # 2. Save theme summaries
        for theme, summary in summaries.items():
            supabase.save_theme_summary(
                run_id=run_id,
                theme_name=theme,
                summary=summary,
                article_count=article_counts.get(theme, 0)
            )
        
        # 3. Save articles by theme
        for theme, articles in themed_articles.items():
            if articles:
                supabase.save_articles(run_id, theme, articles)
        
        # 4. Update sync metadata
        supabase.update_sync_metadata("last_sync_time", timestamp)
        supabase.update_sync_metadata("last_run_id", run_id)
        supabase.update_sync_metadata("sync_status", "success")
        
        logger.info(f"Successfully persisted run {run_id} to Supabase")
        
    except ImportError:
        logger.debug("supabase package not installed, skipping cloud persistence")
    except Exception as e:
        logger.error(f"Failed to persist to Supabase: {e}")
        # Gracefully degrade - file-based persistence already succeeded
