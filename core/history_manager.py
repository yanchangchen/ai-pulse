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
    skipped_per_theme = {
        theme: summaries.get(theme, {}).get("skipped_articles", [])
        for theme in summaries
    }
    history_data[timestamp] = {
        "date": date_key,
        "summaries": summaries,
        "counts": article_counts,
        "full_articles": full_articles,
        "themed_articles": themed_articles,
        "skipped_per_theme": skipped_per_theme,
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
        inc_count = summary.get("included_count", count)
        src = summary.get("_source", "unknown")
        if inc_count is not None and inc_count < count:
            new_entry += f"### {theme} ({inc_count} of {count} articles reflected)\n"
        else:
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
    parsed = _parse_run_timestamp(last_run["timestamp"])
    if parsed is None:
        return None
    # Cloud timestamps are timezone-aware; cache-expiry math compares against
    # a naive ``datetime.now()``, so normalise to local naive time.
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


# ---------------------------------------------------------------------------
# Cloud-first run loading
# ---------------------------------------------------------------------------

def _parse_run_timestamp(value) -> Optional[datetime]:
    """Parse either the local ``YYYY-MM-DD HH:MM:SS`` format or Supabase's
    ISO-8601 ``run_timestamp`` value."""
    if not value:
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _run_timestamp_key(value) -> float:
    """Sortable epoch for local/ISO timestamps (naive values use local time)."""
    parsed = _parse_run_timestamp(value)
    try:
        return parsed.timestamp() if parsed else 0.0
    except (OSError, OverflowError, ValueError):
        return 0.0


def _local_timestamp_label(value) -> str:
    """Normalise a cloud timestamp to the local-history key format."""
    parsed = _parse_run_timestamp(value)
    if parsed is None:
        return str(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _run_data_from_supabase(supabase, run: Dict) -> Dict:
    """Build a local-history-shaped run dict from Supabase rows.

    Supabase stores provenance as ``generation_source`` / ``generation_log``
    columns, while the rest of the app consumes the summariser's ``_source`` /
    ``_generation_log`` keys.  Map them back so Deep Dive and Overview render
    the same provenance chips and coverage captions as a local run.
    """
    run_id = run["id"]
    raw_summaries = supabase.get_summaries_by_run(run_id) or {}
    articles = supabase.get_articles_for_run(run_id) or []

    summaries: Dict[str, Dict] = {}
    for theme, row in raw_summaries.items():
        gen_log = row.get("generation_log")
        if isinstance(gen_log, str):
            try:
                gen_log = json.loads(gen_log)
            except Exception:
                gen_log = {}
        if not isinstance(gen_log, dict):
            gen_log = {}

        summary = {
            "what_is_happening": row.get("what_is_happening", ""),
            "engineering_tradeoffs": row.get("engineering_tradeoffs", ""),
            "product_impact": row.get("product_impact", ""),
            "why_it_matters": row.get("why_it_matters", ""),
            "what_to_watch": row.get("what_to_watch", ""),
            "further_reading": row.get("further_reading", ""),
            "_source": row.get("generation_source"),
            "_generation_log": gen_log,
        }
        # Deep Dive reads these top-level for its coverage caption.
        for key in (
            "included_count",
            "total_articles",
            "skipped_count",
            "skipped_hashes",
            "skipped_articles",
        ):
            if key in gen_log:
                summary[key] = gen_log[key]
        summaries[theme] = summary

    themed_articles: Dict[str, List[Dict]] = {}
    for row in articles:
        article = dict(row)
        theme = article.get("theme_name") or article.get("theme") or "Uncategorized"
        article.setdefault("theme", theme)
        if not article.get("source") and article.get("source_name"):
            article["source"] = article["source_name"]
        if not article.get("published_date") and article.get("published_at"):
            article["published_date"] = article["published_at"]
        themed_articles.setdefault(theme, []).append(article)

    counts = {theme: len(items) for theme, items in themed_articles.items()}
    return {
        "date": run.get("run_date") or _local_timestamp_label(run.get("run_timestamp"))[:10],
        "summaries": summaries,
        "counts": counts,
        "full_articles": articles,
        "themed_articles": themed_articles,
        "article_fingerprint": run.get("article_fingerprint"),
    }


def _cache_run_locally(timestamp: str, data: Dict) -> None:
    """Write a cloud run into ``history.json`` so later loads are local."""
    global _history_cache
    try:
        history_data = load_full_history()
        history_data[timestamp] = data
        MAX_LOCAL_RUNS = 5
        if len(history_data) > MAX_LOCAL_RUNS:
            latest = sorted(history_data.keys(), key=_run_timestamp_key, reverse=True)
            history_data = {k: history_data[k] for k in latest[:MAX_LOCAL_RUNS]}
        with open(HISTORY_JSON, "w", encoding="utf-8") as f:
            json.dump(history_data, f, indent=2, ensure_ascii=False)
        _history_cache = None
    except Exception as exc:
        logger.debug("Could not cache cloud run locally: %s", exc)


def load_latest_run_data() -> Optional[Dict]:
    """Return the freshest run, preferring Supabase when local history lags.

    Background runs persist to Supabase first-class, but ``history.json`` is
    only written by the process that completed the run.  A local app can
    therefore hold a months-old cache while Supabase has today's run — which
    made Deep Dive render stale "Unknown source" summaries.  This helper
    loads the cloud run when it is newer and caches it locally for next time.
    """
    local = get_last_run()

    try:
        from core.supabase_client import get_supabase_manager

        supabase = get_supabase_manager()
        if supabase.is_available():
            cloud_run = supabase.get_latest_run()
            if cloud_run:
                local_ts = local.get("timestamp") if local else None
                cloud_ts = cloud_run.get("run_timestamp")
                if (
                    not local_ts
                    or _run_timestamp_key(cloud_ts) > _run_timestamp_key(local_ts)
                ):
                    data = _run_data_from_supabase(supabase, cloud_run)
                    if data.get("summaries") or data.get("full_articles"):
                        cache_ts = _local_timestamp_label(cloud_ts)
                        _cache_run_locally(cache_ts, data)
                        logger.info(
                            "Loaded run %s from Supabase (%s); local history was %s.",
                            cloud_run.get("id"), cache_ts, local_ts or "missing",
                        )
                        return {"timestamp": cache_ts, "data": data, "source": "supabase"}
    except Exception as exc:
        logger.warning("Cloud run load failed; using local history: %s", exc)

    return local


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
