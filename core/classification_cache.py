"""
Cross-run cache of theme classifications, keyed by content_hash.

Gate 3 (LLM classification) is the only paid classification step, so
articles already classified in a previous run are answered from this
cache instead of re-querying the gateway.  Supabase is preferred — the
``articles`` table already persists content_hash -> theme_name (upserted
deduplicated on ``(content_hash, theme_name)``), so it doubles as the
cloud cache with no extra migration — and a local JSON file mirrors
Gate 3 results for offline runs.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Iterable, List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
LOCAL_CACHE_FILE = ROOT_DIR / "data" / "classification_cache.json"


def _ensure_data_dir() -> None:
    LOCAL_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)


def _load_local_cache() -> Dict[str, str]:
    """Load the local {content_hash: theme_name} mirror."""
    _ensure_data_dir()
    if not LOCAL_CACHE_FILE.exists():
        return {}
    try:
        with LOCAL_CACHE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
    except Exception as e:
        logger.warning("Failed to read local classification cache: %s", e)
    return {}


def _save_local_cache(data: Dict[str, str]) -> None:
    """Persist the local {content_hash: theme_name} mirror."""
    _ensure_data_dir()
    try:
        with LOCAL_CACHE_FILE.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning("Failed to write local classification cache: %s", e)


def lookup_themes(content_hashes: Iterable[str]) -> Dict[str, str]:
    """Return {content_hash: theme_name} for hashes classified before.

    Merges the local mirror with the Supabase ``articles`` table
    (Supabase wins on conflict — it is the authoritative store).
    """
    hashes = [h for h in content_hashes if h]
    if not hashes:
        return {}

    found = _load_local_cache()
    # Keep only requested hashes from the local mirror
    found = {h: found[h] for h in hashes if h in found}

    try:
        from core.supabase_client import get_supabase_manager
        supabase = get_supabase_manager()
        if supabase.is_available():
            found.update(supabase.get_classifications(hashes))
    except Exception as e:
        logger.debug("Supabase classification lookup failed: %s", e)

    return found


def mark_classified(classifications: Dict[str, str]) -> None:
    """Record Gate 3 classifications for future runs.

    Cloud persistence needs no action here: ``save_articles()`` upserts
    (content_hash, theme_name) into the ``articles`` table when the run
    is persisted.  The local mirror covers offline runs.
    """
    if not classifications:
        return
    data = _load_local_cache()
    data.update({h: t for h, t in classifications.items() if h and t})
    _save_local_cache(data)


def get_local_cache_size() -> int:
    """Return the number of entries in the local mirror (for diagnostics)."""
    return len(_load_local_cache())
