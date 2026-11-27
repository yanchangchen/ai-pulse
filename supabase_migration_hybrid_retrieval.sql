-- Phase 5: hybrid retrieval + temporal awareness.
--
-- 1. match_article_embeddings gains article metadata (Sage evidence needs
--    title/source/link/date, not just chunk text) and optional date/theme
--    bounds. The bounds are applied AFTER an over-fetched ANN scan: an HNSW
--    index combined with a WHERE filter can return fewer rows than LIMIT
--    (Future.md Pillar C gotcha), so the ANN CTE fetches 4x match_count
--    unfiltered, then filters and limits. Drop+create because the return
--    type changes; the old 2-arg call still resolves via the new defaults.
--
-- 2. theme_first_seen (Phase 5.4): the archive's true first appearance per
--    theme, maintained at ingest (INSERT ... ON CONFLICT DO NOTHING), replacing
--    the render-time window scan in sage_agent.build_wiki_context. Backfilled
--    below from the existing archive.
--
-- Idempotent: safe to re-run.

DROP FUNCTION IF EXISTS match_article_embeddings(vector, int);

CREATE OR REPLACE FUNCTION match_article_embeddings(
  query_embedding vector(768),
  match_count int DEFAULT 10,
  date_from date DEFAULT NULL,
  date_to date DEFAULT NULL,
  theme_filter text DEFAULT NULL
)
RETURNS TABLE (
  id uuid,
  article_id uuid,
  chunk_text text,
  similarity float,
  title text,
  summary text,
  theme_name text,
  source_name text,
  link text,
  published_at timestamptz,
  run_date date
)
LANGUAGE sql
STABLE
AS $$
  WITH ann AS (
    SELECT e.id, e.article_id, e.chunk_text, e.run_date,
           1 - (e.embedding <=> query_embedding) AS similarity
    FROM article_embeddings e
    ORDER BY e.embedding <=> query_embedding
    LIMIT GREATEST(match_count * 4, 40)
  )
  SELECT ann.id, ann.article_id, ann.chunk_text, ann.similarity,
         a.title, COALESCE(a.summary, '') AS summary, a.theme_name,
         a.source_name, a.link, a.published_at, ann.run_date
  FROM ann
  JOIN articles a ON a.id = ann.article_id
  WHERE (date_from IS NULL OR ann.run_date >= date_from)
    AND (date_to IS NULL OR ann.run_date <= date_to)
    AND (theme_filter IS NULL OR a.theme_name = theme_filter)
  ORDER BY ann.similarity DESC
  LIMIT match_count;
$$;

GRANT EXECUTE ON FUNCTION match_article_embeddings(vector, int, date, date, text) TO anon;

-- ---------------------------------------------------------------------------
-- theme_first_seen (Phase 5.4)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS theme_first_seen (
  theme_name VARCHAR(255) PRIMARY KEY,
  first_seen_date DATE NOT NULL,
  first_seen_run_id UUID,
  evidence_summary TEXT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

ALTER TABLE theme_first_seen ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "theme_first_seen_read" ON theme_first_seen;
CREATE POLICY "theme_first_seen_read" ON theme_first_seen
  FOR SELECT USING (true);

DROP POLICY IF EXISTS "theme_first_seen_write" ON theme_first_seen;
CREATE POLICY "theme_first_seen_write" ON theme_first_seen
  FOR INSERT WITH CHECK (true);

GRANT SELECT, INSERT ON theme_first_seen TO anon;

-- Backfill: earliest archived run per theme (evidence = that run's narrative).
INSERT INTO theme_first_seen (theme_name, first_seen_date, first_seen_run_id, evidence_summary)
SELECT DISTINCT ON (ts.theme_name)
       ts.theme_name, tr.run_date, tr.id, LEFT(ts.what_is_happening, 500)
FROM theme_summaries ts
JOIN trend_runs tr ON ts.run_id = tr.id
WHERE tr.run_date IS NOT NULL
ORDER BY ts.theme_name, tr.run_date ASC
ON CONFLICT (theme_name) DO NOTHING;

-- Verify
SELECT theme_name, first_seen_date FROM theme_first_seen ORDER BY theme_name;
