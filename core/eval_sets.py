"""Eval-set registry: discoverable, git-tracked evaluation question sets.

Every eval set lives in ``eval_sets/*.json`` at the project root (tracked in
git — the previous home, ``data/``, is gitignored runtime storage and lost the
original Phase 3 question set). The UI page ``pages/9_Eval_Sets.py`` renders
any set found here, so adding a new eval set is a matter of dropping in a JSON
file with the schema below.

Schema (minimal contract):
    {
      "id":          str  — unique slug,
      "name":        str  — display name,
      "description": str  — what the set measures and how it was curated,
      "kind":        str  — e.g. "retrieval", "faithfulness", "qa",
      "metric":      str  — e.g. "Recall@k",
      "created":     str  — ISO date,
      "runner":      str  — optional command that runs the eval,
      "results_key": str  — optional app_settings key holding latest results,
      "questions":   [    — items; each needs "question", plus ground truth:
        {"question": str,
         "expected_keywords": [str],   # retrieval-style sets
         "answer": str,                # QA-style sets (future)
         "k": int, "target_title": str, "notes": str, ...}
      ]
    }

Nothing here imports Streamlit — pure loading/validation/pagination helpers so
tests and ``core/retrieval_eval.py`` can use them without a UI context.
"""
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

EVAL_SETS_DIR = Path(__file__).resolve().parent.parent / "eval_sets"

DEFAULT_PAGE_SIZE = 20


def _validate(data: Dict, source: Path) -> Optional[Dict]:
    """Return the set dict when it satisfies the minimal contract, else None."""
    if not isinstance(data, dict):
        logger.warning("Eval set %s is not a JSON object — skipped", source)
        return None
    questions = data.get("questions")
    if not data.get("id") or not data.get("name") or not isinstance(questions, list) or not questions:
        logger.warning("Eval set %s missing id/name/questions — skipped", source)
        return None
    for i, q in enumerate(questions):
        if not isinstance(q, dict) or not q.get("question"):
            logger.warning("Eval set %s item %d has no question — skipped set", source, i)
            return None
    return data


def discover_eval_sets(directory: Optional[Path] = None) -> List[Dict]:
    """Load every valid ``*.json`` eval set, sorted by display name."""
    d = directory or EVAL_SETS_DIR
    if not d.is_dir():
        return []
    sets: List[Dict] = []
    for path in sorted(d.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Could not read eval set %s: %s", path, e)
            continue
        valid = _validate(data, path)
        if valid is not None:
            valid["_source"] = str(path)
            sets.append(valid)
    sets.sort(key=lambda s: str(s.get("name", "")).lower())
    return sets


def load_eval_set(set_id: str, directory: Optional[Path] = None) -> Optional[Dict]:
    """Load one eval set by its ``id`` slug (None when unknown)."""
    for s in discover_eval_sets(directory):
        if s.get("id") == set_id:
            return s
    return None


def paginate(items: List, page: int, page_size: int = DEFAULT_PAGE_SIZE) -> Tuple[List, int, int]:
    """Clamp ``page`` to valid range and return (page_items, page, total_pages).

    Pages are 1-based. An empty list yields one (empty) page so the UI always
    has a stable render path.
    """
    page_size = max(1, int(page_size))
    total_pages = max(1, (len(items) + page_size - 1) // page_size)
    page = max(1, min(int(page), total_pages))
    start = (page - 1) * page_size
    return items[start:start + page_size], page, total_pages


def get_latest_results(eval_set: Dict) -> Optional[Dict]:
    """Best-effort fetch of the latest run snapshot from ``app_settings``.

    Returns None when the set declares no ``results_key``, Supabase is
    unavailable, or no snapshot exists yet — the UI degrades to showing the
    questions without scores.
    """
    key = eval_set.get("results_key")
    if not key:
        return None
    try:
        from core.supabase_client import get_supabase_manager

        manager = get_supabase_manager()
        if not manager or not manager.is_available():
            return None
        return manager.get_app_setting(key)
    except Exception as e:
        logger.debug("Could not fetch eval results %s: %s", key, e)
        return None