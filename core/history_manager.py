"""History and memory manager for AI Pulse.
Handles persisting summaries to JSON (for parsing), memory.md (for context/wiki), and Supabase (cloud).
"""

import hashlib
import json
import logging
import streamlit as st
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from config.settings import DUPLICATE_RUN_WINDOW_MINUTES

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Paths
ROOT_DIR = Path(__file__).resolve().parent.parent
HISTORY_JSON = ROOT_DIR / "history.json"
MEMORY_MD = ROOT_DIR / "memory.md"

# In-memory history cache to optimize disk I/O performance
_history_cache: Optional[Dict] = None


def compute_run_fingerprint(full_articles: List[Dict]) -> str:
    """Return a stable SHA-256 fingerprint of the article set.

    Uses ``content_hash`` when available; falls back to a hash of the
    article's title + link.
    """
    if not full_articles:
        return "empty"
    hashes = []
    for article in full_articles:
        content_hash = article.get("content_hash")
        if not content_hash:
            title = article.get("title", "") or ""
            link = article.get("link", "") or ""
            content_hash = hashlib.md5(f"{link}{title}".encode()).hexdigest()
        hashes.append(str(content_hash))
    return hashlib.sha256("".join(sorted(hashes)).encode()).hexdigest()


def _latest_local_run_timestamp_and_fingerprint() -> tuple:
    """Return (timestamp, fingerprint) of the latest local run, or (None, None)."""
    try:
        history = load_full_history()
        if not history:
            return None, None
        latest_ts = sorted(history.keys(), reverse=True)[0]
        entry = history[latest_ts]
        return latest_ts, entry.get("article_fingerprint")
    except Exception:
        return None, None


def is_duplicate_run(fingerprint: str) -> bool:
    """Return True if the same article set was persisted within the configured window."""
    if not fingerprint or fingerprint == "empty":
        return False

    window = timedelta(minutes=DUPLICATE_RUN_WINDOW_MINUTES)

    # Check Supabase first (shared across workers)
    try:
        from core.supabase_client import get_supabase_manager
        supabase = get_supabase_manager()
        if supabase.is_available():
            latest = supabase.get_latest_run()
            if latest:
                latest_fp = latest.get("article_fingerprint")
                latest_ts = latest.get("run_timestamp")
                if latest_fp and latest_ts:
                    run_time = datetime.strptime(latest_ts, "%Y-%m-%d %H:%M:%S")
                    if latest_fp == fingerprint and (datetime.now() - run_time) <= window:
                        logger.info("Duplicate run fingerprint detected in Supabase (within %s minutes)",
                                    DUPLICATE_RUN_WINDOW_MINUTES)
                        return True
    except Exception as e:
        logger.debug("Supabase duplicate check failed: %s", e)

    # Fall back to local history
    latest_ts, latest_fp = _latest_local_run_timestamp_and_fingerprint()
    if latest_fp and latest_ts:
        try:
            run_time = datetime.strptime(latest_ts, "%Y-%m-%d %H:%M:%S")
            if latest_fp == fingerprint and (datetime.now() - run_time) <= window:
                logger.info("Duplicate run fingerprint detected in local history (within %s minutes)",
                            DUPLICATE_RUN_WINDOW_MINUTES)
                return True
        except Exception:
            pass

    return False


def save_run_to_history(
    summaries: Dict[str, Dict[str, str]],
    article_counts: Dict[str, int],
    full_articles: List[Dict],
    themed_articles: Dict[str, List[Dict]],
    article_fingerprint: Optional[str] = None,
) -> None:
    """Save the current summaries and full state to both JSON and Markdown history."""
    global _history_cache
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    date_key = datetime.now().strftime("%Y-%m-%d")

    # 1. Update JSON History — store the latest N runs for context
    # Full historical data lives in Supabase; history.json is a local cache
    # that keeps enough runs for get_recent_context() to work.
    MAX_LOCAL_RUNS = 5
    history_data = load_full_history()
    history_data[timestamp] = {
        "date": date_key,
        "summaries": summaries,
        "counts": article_counts,
        "full_articles": full_articles,
        "themed_articles": themed_articles,
        "article_fingerprint": article_fingerprint,
    }

    # Trim to most recent N runs
    if len(history_data) > MAX_LOCAL_RUNS:
        sorted_keys = sorted(history_data.keys(), reverse=True)
        history_data = {k: history_data[k] for k in sorted_keys[:MAX_LOCAL_RUNS]}

    with open(HISTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(history_data, f, indent=2, ensure_ascii=False)

    # Invalidate cache so it is reloaded from disk next time
    _history_cache = None

    # 2. Update memory.md (Append-only Wiki)
    new_entry = f"\n## ⚡ AI Pulse Run: {timestamp}\n"
    for theme, summary in summaries.items():
        count = article_counts.get(theme, 0)
        src = summary.get("_source", "unknown")
        new_entry += f"### {theme} ({count} articles)\n"
        new_entry += f"*Source:* `{src}`\n\n"
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
    _save_to_supabase(
        timestamp, date_key, summaries, article_counts,
        full_articles, themed_articles, article_fingerprint,
    )

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
    themed_articles: Dict[str, List[Dict]],
    article_fingerprint: Optional[str] = None,
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
            total_articles=len(full_articles),
            article_fingerprint=article_fingerprint,
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
