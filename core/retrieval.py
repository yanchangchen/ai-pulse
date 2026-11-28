"""Phase 5 — hybrid retrieval: dense + sparse legs, RRF fusion, temporal awareness.

Pipeline (Future.md Pillars A & C):

1. **Temporal parsing** (5.3): extract an explicit bounding box from the
   question ("last week", "August 2026", "between June and September").
   Explicit bounds constrain both legs; queries without them stay unbounded
   (historical questions must not be silently freshness-biased).
2. **Sparse leg** (live since Phase 2.3): ``match_articles`` Postgres FTS RPC,
   ranked by ``ts_rank``.
3. **Dense leg** (5.1): embed the question through the gateway's EMBEDDING
   route, then ``match_article_embeddings`` pgvector top-k. The RPC applies
   date/theme bounds *after* an over-fetched ANN scan — HNSW + WHERE filter
   can under-fill LIMIT (Pillar C gotcha), so the filter never rides the
   index scan directly.
4. **Reciprocal Rank Fusion** (5.2): ``score = sum 1/(rrf_k + rank)`` across
   legs, deduped by article id. Rank-based fusion needs no score
   normalisation between ts_rank and cosine similarity.
5. **Time-decay re-rank** (5.3): only for queries *without* explicit bounds,
   ``final = rrf * (floor + (1-floor) * exp(-age_days / halflife))`` — a
   bounded freshness preference (gate-tuned floor 0.9 / halflife 180d =>
   at most ~1.11x at equal RRF: a tie-break, not a filter, on a ~6-month
   archive).

Every leg degrades independently: no embeddings / RPC missing -> sparse-only;
FTS empty -> dense-only; both empty -> ``[]`` and Sage falls back to its
newest-first window / local client-side scoring. All knobs come from
``config.settings.get_retrieval_settings()``.
"""
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_PATTERN = "|".join(sorted(_MONTHS, key=len, reverse=True))


def _month_end(year: int, month: int) -> datetime:
    if month == 12:
        return datetime(year, 12, 31, tzinfo=timezone.utc)
    return datetime(year, month + 1, 1, tzinfo=timezone.utc) - timedelta(days=1)


def parse_temporal_bounds(
    query: str, now: Optional[datetime] = None
) -> Tuple[Optional[str], Optional[str]]:
    """Extract an explicit (date_from, date_to) bounding box from a question.

    Returns ISO date strings, or ``(None, None)`` when the query carries no
    temporal expression. Supported: "between <month> [year] and <month>
    [year]", "since <month> [year]", "<month> <year>", "in/during <year>",
    "last/past N days/weeks/months", "last week", "last month", "this
    month/week", "today", "yesterday". Rolling windows ("last week") use
    now-7d..now; calendar periods ("last month") use the previous calendar
    month. Deliberately conservative: unrecognised phrasing stays unbounded
    rather than guessing.
    """
    now = now or datetime.now(timezone.utc)
    q = (query or "").lower()

    def iso(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%d")

    # between <month> [year] and <month> [year]
    m = re.search(
        rf"between\s+({_MONTH_PATTERN})\.?\s*(\d{{4}})?\s+and\s+({_MONTH_PATTERN})\.?\s*(\d{{4}})?",
        q,
    )
    if m:
        m1, y1, m2, y2 = _MONTHS[m.group(1)], m.group(2), _MONTHS[m.group(3)], m.group(4)
        year1 = int(y1) if y1 else now.year
        year2 = int(y2) if y2 else year1
        start = datetime(year1, m1, 1, tzinfo=timezone.utc)
        end = _month_end(year2, m2)
        if end < start:  # "between November and February" crosses new year
            end = _month_end(year2 + 1, m2)
        return iso(start), iso(end)

    # since <month> [year]
    m = re.search(rf"since\s+({_MONTH_PATTERN})\.?\s*(\d{{4}})?", q)
    if m:
        month = _MONTHS[m.group(1)]
        year = int(m.group(2)) if m.group(2) else now.year
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        if start > now:
            start = datetime(year - 1, month, 1, tzinfo=timezone.utc)
        return iso(start), iso(now)

    # last/past N days/weeks/months
    m = re.search(r"(?:last|past)\s+(\d+)\s+(day|week|month)s?", q)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        days = n * {"day": 1, "week": 7, "month": 30}[unit]
        return iso(now - timedelta(days=days)), iso(now)

    # named rolling/calendar periods
    if re.search(r"\blast\s+week\b", q):
        return iso(now - timedelta(days=7)), iso(now)
    if re.search(r"\blast\s+month\b", q):
        first_this = now.replace(day=1)
        end_prev = first_this - timedelta(days=1)
        return iso(end_prev.replace(day=1)), iso(end_prev)
    if re.search(r"\bthis\s+month\b", q):
        return iso(now.replace(day=1)), iso(now)
    if re.search(r"\bthis\s+week\b", q):
        return iso(now - timedelta(days=now.weekday())), iso(now)
    if re.search(r"\btoday\b", q):
        return iso(now), iso(now)
    if re.search(r"\byesterday\b", q):
        return iso(now - timedelta(days=1)), iso(now - timedelta(days=1))

    # <month> <year>
    m = re.search(rf"\b({_MONTH_PATTERN})\.?\s+(\d{{4}})\b", q)
    if m:
        month, year = _MONTHS[m.group(1)], int(m.group(2))
        return iso(datetime(year, month, 1, tzinfo=timezone.utc)), iso(_month_end(year, month))

    # in/during <year>
    m = re.search(r"\b(?:in|during)\s+(\d{4})\b", q)
    if m:
        year = int(m.group(1))
        return f"{year}-01-01", f"{year}-12-31"

    return None, None


def rrf_fuse(
    sparse_rows: List[Dict],
    dense_rows: List[Dict],
    k: int = 60,
) -> List[Dict]:
    """Reciprocal Rank Fusion over the two legs, deduped by article id.

    Sparse rows key on ``id`` (the article id); dense rows on ``article_id``
    (their ``id`` is the embedding row). Each leg contributes 1/(k+rank)
    with 1-based ranks; a document found by both legs accumulates both
    contributions. ``chunk_text`` (the eval haystack) is preserved from the
    dense leg and synthesised from title+summary for sparse-only rows.
    """
    merged: Dict[str, Dict] = {}
    for rank, row in enumerate(sparse_rows or [], 1):
        aid = str(row.get("id") or row.get("article_id") or "")
        if not aid:
            continue
        entry = merged.setdefault(aid, dict(row))
        entry["_rrf_score"] = entry.get("_rrf_score", 0.0) + 1.0 / (k + rank)
        entry["_fts_rank"] = rank
    for rank, row in enumerate(dense_rows or [], 1):
        aid = str(row.get("article_id") or row.get("id") or "")
        if not aid:
            continue
        entry = merged.setdefault(aid, dict(row))
        entry["_rrf_score"] = entry.get("_rrf_score", 0.0) + 1.0 / (k + rank)
        entry["_dense_rank"] = rank
        entry["_dense_sim"] = row.get("similarity")
        if row.get("chunk_text"):
            entry["chunk_text"] = row["chunk_text"]
    for entry in merged.values():
        if not entry.get("chunk_text"):
            entry["chunk_text"] = f"{entry.get('title', '')} {entry.get('summary', '')}".strip()
    return sorted(merged.values(), key=lambda e: -e["_rrf_score"])


def _age_days(entry: Dict, now: datetime) -> Optional[float]:
    """Best-available article age in days; None when no date is parseable."""
    raw = entry.get("published_at") or entry.get("published_date") or entry.get("run_date") or ""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        try:
            dt = datetime.strptime(str(raw)[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 86400.0)


def hybrid_search(
    query: str,
    supabase=None,
    theme_filter: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    source_filter: Optional[str] = None,
    top_k: int = 15,
    settings: Optional[Dict] = None,
) -> List[Dict]:
    """Fuse sparse (FTS) + dense (pgvector) retrieval for a question.

    Returns up to ``top_k`` article dicts in final rank order, each carrying
    ``_rrf_score``, ``_retrieval`` provenance ("hybrid" / "dense" / "sparse"),
    and ``chunk_text``. Caller-supplied bounds (UI filters) intersect with
    bounds parsed from the question. Returns ``[]`` when Supabase is
    unavailable or both legs come back empty — the caller keeps its own
    fallback chain.
    """
    from config.settings import get_retrieval_settings

    settings = settings if settings is not None else get_retrieval_settings()
    if supabase is None:
        from core.supabase_client import get_supabase_manager
        supabase = get_supabase_manager()
    if not supabase or not supabase.is_available() or not (query or "").strip():
        return []

    parsed_from, parsed_to = parse_temporal_bounds(query)
    explicit = bool(date_from or date_to or parsed_from or parsed_to)
    eff_from = max(x for x in (date_from, parsed_from) if x) if (date_from or parsed_from) else None
    eff_to = min(x for x in (date_to, parsed_to) if x) if (date_to or parsed_to) else None

    # Sparse leg: FTS matches only (no newest-first window fallback here —
    # that belongs to Sage's outer fallback, not to a fusion leg).
    sparse: List[Dict] = []
    try:
        sparse = supabase.match_articles_fts(
            query,
            theme_filter=theme_filter,
            date_from=eff_from,
            date_to=eff_to,
            source_filter=source_filter,
            result_limit=int(settings.get("retrieval_sparse_candidates", 50)),
        ) or []
    except Exception as e:
        logger.warning("Sparse retrieval leg failed: %s", e)
    if not isinstance(sparse, list):
        sparse = []

    # Dense leg: embed the question, then ANN top-k with post-filtered bounds.
    dense: List[Dict] = []
    try:
        from core.embedding import embed_texts
        vectors, _model = embed_texts([query])
        if vectors:
            dense = supabase.match_article_embeddings(
                vectors[0],
                match_count=int(settings.get("retrieval_dense_candidates", 50)),
                date_from=eff_from,
                date_to=eff_to,
                theme_filter=theme_filter,
            ) or []
    except Exception as e:
        logger.warning("Dense retrieval leg failed: %s", e)
    if not isinstance(dense, list):
        dense = []

    if not sparse and not dense:
        return []

    fused = rrf_fuse(sparse, dense, k=int(settings.get("rrf_k", 60)))
    if source_filter:
        fused = [e for e in fused if e.get("source_name") == source_filter]

    # Time-decay re-rank only for unbounded queries: an explicit temporal
    # request ("what happened in June?") must not be freshness-biased.
    if not explicit:
        # Fallbacks match the gate-tuned _RETRIEVAL_DEFAULTS (0.9 / 180d):
        # decay is a bounded tie-break, not a filter (Phase 5 gate lesson).
        floor = float(settings.get("retrieval_time_decay_floor", 0.9))
        halflife = max(1.0, float(settings.get("retrieval_time_decay_halflife_days", 180.0)))
        now = datetime.now(timezone.utc)
        for entry in fused:
            age = _age_days(entry, now)
            if age is not None:
                entry["_rrf_score"] *= floor + (1.0 - floor) * math.exp(-age / halflife)
        fused.sort(key=lambda e: -e["_rrf_score"])

    provenance = "hybrid" if (sparse and dense) else ("dense" if dense else "sparse")
    for entry in fused:
        entry["_retrieval"] = provenance
    return fused[:top_k]
