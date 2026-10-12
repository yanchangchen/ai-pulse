-- supabase_migration_fts_articles.sql
-- Phase 2.3: proper full-text search on articles for Sage's Tier-1
-- evidence retrieval.
--
-- Until now Sage's article candidates were a bounded newest-first window
-- (300 rows, no keyword matching in SQL) scored client-side in
-- core/sage_agent.py.  That window hid relevant-but-older articles: if a
-- keyword match sat outside the newest 300 rows, Sage never saw it.
--
-- This migration moves keyword *matching* into Postgres FTS:
--   * search_vector  — generated tsvector over title (weight A) and
--     summary (weight B), English config.  Weight A on the title keeps the
--     existing "title counts double" client-side scoring semantics in SQL.
--     GENERATED ALWAYS ... STORED backfills every existing row automatically.
--   * idx_articles_search_vector — GIN index, so '@@' matches are index
--     lookups instead of table scans.
--   * match_articles(query, ...) — RPC returning keyword-matching rows
--     ranked by ts_rank, newest first within equal rank.  PostgREST's
--     built-in fts/wfts filter cannot ORDER BY ts_rank, so a function is
--     the standard Supabase pattern for ranked retrieval.
--
-- Invoker rights (not SECURITY DEFINER): anon already holds SELECT on
-- articles through the articles_read policy, so the function only needs
-- EXECUTE granted.  An empty query folds to an empty tsquery, matching no
-- rows — core/supabase_client.py treats that as "fall through to the
-- newest-first window", so Sage always has some Tier-1 evidence.

ALTER TABLE articles ADD COLUMN IF NOT EXISTS search_vector tsvector
  GENERATED ALWAYS AS (
    setweight(to_tsvector('english', coalesce(title, '')), 'A')
    || setweight(to_tsvector('english', coalesce(summary, '')), 'B')
  ) STORED;

CREATE INDEX IF NOT EXISTS idx_articles_search_vector
  ON articles USING GIN (search_vector);

CREATE OR REPLACE FUNCTION match_articles(
  query text,
  theme_filter text DEFAULT NULL,
  date_from date DEFAULT NULL,
  date_to date DEFAULT NULL,
  source_filter text DEFAULT NULL,
  result_limit int DEFAULT 100
)
RETURNS TABLE (
  id uuid,
  run_id uuid,
  theme_name text,
  title text,
  summary text,
  source_name text,
  link text,
  published_at timestamptz,
  rank float
)
LANGUAGE sql
STABLE
AS $$
  SELECT a.id, a.run_id, a.theme_name, a.title, a.summary, a.source_name,
         a.link, a.published_at,
         ts_rank(a.search_vector, websearch_to_tsquery('english', query)) AS rank
  FROM articles a
  WHERE a.search_vector @@ websearch_to_tsquery('english', query)
    AND (theme_filter IS NULL OR a.theme_name = theme_filter)
    AND (date_from IS NULL OR a.published_at >= date_from)
    AND (date_to IS NULL OR a.published_at <= date_to)
    AND (source_filter IS NULL OR a.source_name = source_filter)
  ORDER BY rank DESC, a.published_at DESC NULLS LAST
  LIMIT result_limit;
$$;

GRANT EXECUTE ON FUNCTION match_articles(text, text, date, date, text, int) TO anon;
