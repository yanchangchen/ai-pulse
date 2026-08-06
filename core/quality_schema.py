"""
Supabase DDL and helpers for the quality_evaluations table.

The canonical schema is in `supabase_schema.sql` (run that in the Supabase SQL
editor for new projects).  This module exposes the DDL as a constant so the
page can show it to users for copy-paste, and a small helper to fetch the
evaluation history once the table exists.
"""

from typing import List, Dict, Optional

QUALITY_EVALUATIONS_DDL = """
CREATE TABLE IF NOT EXISTS quality_evaluations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lookback_days INT NOT NULL,
    runs_evaluated JSONB NOT NULL,
    threshold FLOAT NOT NULL,
    classifier_score FLOAT NOT NULL,
    faithfulness_score FLOAT NOT NULL,
    uniqueness_score FLOAT NOT NULL,
    grounding_score FLOAT DEFAULT 1.0,
    structural_compliance_score FLOAT DEFAULT 1.0,
    coverage_score FLOAT DEFAULT 1.0,
    temporal_coherence_score FLOAT DEFAULT 1.0,
    per_theme_classifier JSONB,
    recommendations JSONB,
    raw_metrics JSONB
);

CREATE INDEX IF NOT EXISTS idx_quality_evaluations_generated_at
    ON quality_evaluations (generated_at DESC);

ALTER TABLE quality_evaluations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Public read access for quality_evaluations"
    ON quality_evaluations FOR SELECT
    USING (true);
"""

# DDL for the keyword_suggestions table that backs the new feature
# added in commit 965b112.  Run supabase_migration_keywords.sql once to
# create it on existing projects.
KEYWORD_SUGGESTIONS_DDL = """
CREATE TABLE IF NOT EXISTS keyword_suggestions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    evaluation_id UUID REFERENCES quality_evaluations(id) ON DELETE SET NULL,
    kind VARCHAR(32) NOT NULL,           -- 'theme_keyword' | 'watchlist_term'
    theme_name VARCHAR(255),             -- NULL for watchlist
    term TEXT NOT NULL,
    suggested_weight INT,                -- 1..3 for theme_keyword, NULL for watchlist
    reason TEXT,
    frequency INT,                       -- raw count from visualiser
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    UNIQUE(evaluation_id, kind, theme_name, term)
);

CREATE INDEX IF NOT EXISTS idx_keyword_suggestions_status_generated
    ON keyword_suggestions (status, generated_at DESC);

ALTER TABLE keyword_suggestions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Public read access for keyword_suggestions"
    ON keyword_suggestions FOR SELECT
    USING (true);
"""


def fetch_quality_evaluations(supabase, limit: int = 12) -> List[Dict]:
    """Fetch recent quality_evaluations rows, newest first.

    Returns an empty list if Supabase is unavailable or the table does not
    exist yet.  Never raises — callers should treat empty list as "no eval
    history yet".
    """
    if supabase is None or not supabase.is_available():
        return []
    try:
        response = supabase.client.table("quality_evaluations") \
            .select("*") \
            .order("generated_at", desc=True) \
            .limit(limit) \
            .execute()
        return response.data if response.data else []
    except Exception:
        return []


def insert_quality_evaluation(supabase, payload: Dict) -> Optional[Dict]:
    """Insert a new quality_evaluations row.  Returns the inserted record or
    None on failure (including the case where the table doesn't exist yet).
    """
    if supabase is None or not supabase.is_available():
        return None
    try:
        response = supabase.client.table("quality_evaluations") \
            .insert(payload) \
            .execute()
        if response.data:
            return response.data[0]
        return None
    except Exception:
        return None


def has_evaluation_this_iso_week(supabase) -> bool:
    """Return True if any quality_evaluations row exists with generated_at in
    the current ISO calendar week.  Used by the WeeklyEvaluator to avoid
    double-firing.
    """
    if supabase is None or not supabase.is_available():
        return False
    try:
        from datetime import datetime, timezone, timedelta

        # Compute start of current ISO week (Monday 00:00 UTC)
        now = datetime.now(timezone.utc)
        iso_weekday = now.isocalendar().weekday  # 1..7, Mon=1
        week_start = (now - timedelta(days=iso_weekday - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        response = supabase.client.table("quality_evaluations") \
            .select("id") \
            .gte("generated_at", week_start.isoformat()) \
            .limit(1) \
            .execute()
        return bool(response.data)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# keyword_suggestions table
# ---------------------------------------------------------------------------


def insert_keyword_suggestions(
    supabase,
    suggestions: List[Dict],
    evaluation_id: Optional[str] = None,
) -> List[Dict]:
    """Bulk-insert keyword suggestions.  Returns the inserted records or
    an empty list on failure (including when the table does not exist yet).

    Each suggestion is a dict with at least ``kind`` and ``term``.  Optional
    fields: ``theme_name``, ``suggested_weight`` (1..3), ``reason``,
    ``frequency``.  The caller is expected to have already deduped against
    existing theme keywords / watchlist terms.
    """
    if supabase is None or not supabase.is_available():
        return []
    if not suggestions:
        return []
    rows = []
    for s in suggestions:
        row = {
            "kind": s.get("kind", "theme_keyword"),
            "theme_name": s.get("theme_name"),
            "term": s.get("term", "").strip(),
            "suggested_weight": s.get("suggested_weight"),
            "reason": s.get("reason"),
            "frequency": s.get("frequency"),
        }
        if not row["term"]:
            continue
        if evaluation_id:
            row["evaluation_id"] = evaluation_id
        rows.append(row)
    if not rows:
        return []
    try:
        response = supabase.client.table("keyword_suggestions") \
            .insert(rows) \
            .execute()
        return response.data if response.data else []
    except Exception:
        return []


def fetch_recent_keyword_suggestions(supabase, limit: int = 50) -> List[Dict]:
    """Fetch recent keyword_suggestions rows, newest first.  Returns an
    empty list if Supabase is unavailable or the table doesn't exist yet.
    """
    if supabase is None or not supabase.is_available():
        return []
    try:
        response = supabase.client.table("keyword_suggestions") \
            .select("*") \
            .order("generated_at", desc=True) \
            .limit(limit) \
            .execute()
        return response.data if response.data else []
    except Exception:
        return []
