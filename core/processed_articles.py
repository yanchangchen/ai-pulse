"""
Track which articles have already been included in a summary prompt.

Provides a persistent store keyed by (theme_name, content_hash).  Supabase is
preferred when available; otherwise a local JSON file is used.
"""

import hashlib
import json
import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
LOCAL_PROCESSED_FILE = ROOT_DIR / "data" / "processed_articles.json"
LOCAL_TIMES_SEEN_FILE = ROOT_DIR / "data" / "times_seen.json"


def _ensure_data_dir() -> None:
    LOCAL_PROCESSED_FILE.parent.mkdir(parents=True, exist_ok=True)


def _load_local_processed() -> Dict[str, List[str]]:
    """Load processed hashes from local JSON fallback."""
    _ensure_data_dir()
    if not LOCAL_PROCESSED_FILE.exists():
        return {}
    try:
        with open(LOCAL_PROCESSED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.warning("Failed to read local processed file: %s", e)
    return {}


def _save_local_processed(data: Dict[str, List[str]]) -> None:
    """Save processed hashes to local JSON fallback."""
    _ensure_data_dir()
    try:
        with open(LOCAL_PROCESSED_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning("Failed to write local processed file: %s", e)


def article_hash(article: Dict) -> str:
    """Return a stable hash for an article.

    Prefer the existing ``content_hash``; fall back to MD5(title:link).
    """
    content_hash = article.get("content_hash")
    if content_hash:
        return str(content_hash)
    title = article.get("title", "") or ""
    link = article.get("link", "") or ""
    return hashlib.md5(f"{link}{title}".encode()).hexdigest()


def get_processed_hashes(theme_name: str) -> Set[str]:
    """Return the set of already-processed content hashes for a theme."""
    # Prefer Supabase when available
    try:
        from core.supabase_client import get_supabase_manager
        supabase = get_supabase_manager()
        if supabase.is_available():
            return supabase.get_processed_hashes(theme_name)
    except Exception as e:
        logger.debug("Supabase processed lookup failed: %s", e)

    data = _load_local_processed()
    return set(data.get(theme_name, []))


def mark_processed(theme_name: str, content_hashes: Iterable[str]) -> None:
    """Mark a list of content hashes as processed for a theme."""
    if not content_hashes:
        return

    # Supabase first
    try:
        from core.supabase_client import get_supabase_manager
        supabase = get_supabase_manager()
        if supabase.is_available():
            supabase.mark_processed(theme_name, list(content_hashes))
            return
    except Exception as e:
        logger.debug("Supabase processed write failed: %s", e)

    # Local fallback
    try:
        data = _load_local_processed()
        existing = set(data.get(theme_name, []))
        existing.update(content_hashes)
        data[theme_name] = list(existing)
        _save_local_processed(data)
    except Exception as e:
        logger.warning("Failed to mark processed for %s: %s", theme_name, e)


def filter_unprocessed(theme_name: str, articles: List[Dict]) -> List[Dict]:
    """Return only the articles that have not been processed for the theme."""
    processed = get_processed_hashes(theme_name)
    if not processed:
        return list(articles)
    return [a for a in articles if article_hash(a) not in processed]


# ---------------------------------------------------------------------------
# Phase 4.3 — fair drain: times-seen counter (runs classified-but-not-summarised)
# ---------------------------------------------------------------------------

def _load_local_times_seen() -> Dict[str, Dict[str, int]]:
    """Load the local times-seen fallback ({theme: {hash: count}})."""
    _ensure_data_dir()
    if not LOCAL_TIMES_SEEN_FILE.exists():
        return {}
    try:
        with open(LOCAL_TIMES_SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.warning("Failed to read local times-seen file: %s", e)
    return {}


def _save_local_times_seen(data: Dict[str, Dict[str, int]]) -> None:
    _ensure_data_dir()
    try:
        with open(LOCAL_TIMES_SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning("Failed to write local times-seen file: %s", e)


def get_times_seen(theme_name: str) -> Dict[str, int]:
    """Return {content_hash: runs_seen_unprocessed} for waiting articles.

    Supabase preferred (``articles.times_seen``, Phase 4.3 migration); local
    JSON fallback otherwise. Only positive counts are returned — a zero means
    the article is not waiting, and callers treat missing keys as 0.
    """
    try:
        from core.supabase_client import get_supabase_manager
        supabase = get_supabase_manager()
        if supabase.is_available():
            return supabase.get_times_seen(theme_name)
    except Exception as e:
        logger.debug("Supabase times-seen lookup failed: %s", e)

    return {
        h: int(n)
        for h, n in _load_local_times_seen().get(theme_name, {}).items()
        if int(n) > 0
    }


def record_run_seen(
    theme_name: str,
    waiting_hashes: Iterable[str],
    included_hashes: Iterable[str],
) -> None:
    """Advance the fair-drain counter after a run's inclusion set is known.

    ``waiting_hashes`` (classified, unprocessed, not included this run) gain
    +1; ``included_hashes`` reset to 0 — they leave the backlog for good.
    Supabase first via the ``record_article_seen`` RPC (UPDATE-only, so it
    never inserts partial article rows); local JSON fallback otherwise.
    """
    waiting = [h for h in set(waiting_hashes) if h]
    included = [h for h in set(included_hashes) if h]
    if not waiting and not included:
        return

    try:
        from core.supabase_client import get_supabase_manager
        supabase = get_supabase_manager()
        if supabase.is_available():
            supabase.record_run_seen(theme_name, waiting, included)
            return
    except Exception as e:
        logger.debug("Supabase times-seen write failed: %s", e)

    try:
        data = _load_local_times_seen()
        counts = data.get(theme_name, {})
        for h in waiting:
            counts[h] = int(counts.get(h, 0)) + 1
        for h in included:
            counts.pop(h, None)
        if counts:
            data[theme_name] = counts
        else:
            data.pop(theme_name, None)
        _save_local_times_seen(data)
    except Exception as e:
        logger.warning("Failed to record times-seen for %s: %s", theme_name, e)
