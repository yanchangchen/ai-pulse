-- Migration: Track which articles have actually been summarised and store a
-- per-run fingerprint so near-duplicate runs can be skipped.

-- 1. Stable fingerprint for each run so we can suppress duplicates.
ALTER TABLE trend_runs
  ADD COLUMN IF NOT EXISTS article_fingerprint VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_trend_runs_fingerprint
  ON trend_runs(article_fingerprint);

-- 2. Global processed-articles ledger keyed by (theme_name, content_hash).
CREATE TABLE IF NOT EXISTS processed_articles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  theme_name VARCHAR(255) NOT NULL,
  content_hash VARCHAR(64) NOT NULL,
  processed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  UNIQUE(theme_name, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_processed_articles_theme_hash
  ON processed_articles(theme_name, content_hash);

-- Backfill missing content_hash values on existing articles so the processed
-- ledger and deduplication keep working.
-- NOTE: If your articles table already has duplicate (content_hash, theme_name)
-- rows, a plain UPDATE may violate the unique constraint.  Backfill only after
-- de-duplicating, or rely on the app to populate content_hash for new runs.
-- UPDATE articles
-- SET content_hash = MD5(CONCAT(link, title))
-- WHERE content_hash IS NULL;

-- Enable RLS and allow reads (writes use the service role key).
ALTER TABLE processed_articles ENABLE ROW LEVEL SECURITY;

CREATE POLICY "processed_articles_read" ON processed_articles
  FOR SELECT USING (true);

GRANT SELECT, INSERT, UPDATE ON processed_articles TO anon;

-- Verify
SELECT column_name, data_type
  FROM information_schema.columns
 WHERE table_name = 'trend_runs'
   AND column_name = 'article_fingerprint';

SELECT 'processed_articles table ready' AS status;
