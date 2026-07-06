-- AI Pulse Supabase Database Schema
-- Run these SQL commands in your Supabase project dashboard
-- Navigate to: SQL Editor > New Query > Paste this content > Run

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Table 1: Trend Runs
-- Represents each ai-pulse execution/run
CREATE TABLE IF NOT EXISTS trend_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
  run_date DATE NOT NULL,
  total_articles INT NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  UNIQUE(run_timestamp)
);

-- Indexes for trend_runs
CREATE INDEX IF NOT EXISTS idx_trend_runs_run_date ON trend_runs(run_date DESC);
CREATE INDEX IF NOT EXISTS idx_trend_runs_created_at ON trend_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trend_runs_run_timestamp ON trend_runs(run_timestamp DESC);

-- Table 2: Theme Summaries
-- Individual theme summaries for each run
CREATE TABLE IF NOT EXISTS theme_summaries (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID NOT NULL REFERENCES trend_runs(id) ON DELETE CASCADE,
  theme_name VARCHAR(255) NOT NULL,
  what_is_happening TEXT NOT NULL,
  why_it_matters TEXT NOT NULL,
  what_to_watch TEXT NOT NULL,
  article_count INT NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  UNIQUE(run_id, theme_name)
);

-- Add engineering_tradeoffs / product_impact columns for the Quality
-- Evaluation faithfulness judge (core/evaluator.py).  Idempotent: safe to
-- run on a fresh table or on an older deployment that pre-dates these
-- columns.  Older rows will have NULL/empty, which the judge treats as
-- perfectly faithful (no claims made).
ALTER TABLE theme_summaries
  ADD COLUMN IF NOT EXISTS engineering_tradeoffs TEXT NOT NULL DEFAULT '';
ALTER TABLE theme_summaries
  ADD COLUMN IF NOT EXISTS product_impact TEXT NOT NULL DEFAULT '';

-- Indexes for theme_summaries
CREATE INDEX IF NOT EXISTS idx_theme_summaries_run_id ON theme_summaries(run_id);
CREATE INDEX IF NOT EXISTS idx_theme_summaries_theme_name ON theme_summaries(theme_name);
CREATE INDEX IF NOT EXISTS idx_theme_summaries_created_at ON theme_summaries(created_at DESC);

-- Table 3: Articles
-- Individual articles with theme classification
CREATE TABLE IF NOT EXISTS articles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID NOT NULL REFERENCES trend_runs(id) ON DELETE CASCADE,
  theme_name VARCHAR(255) NOT NULL,
  title VARCHAR(500) NOT NULL,
  summary TEXT,
  source_name VARCHAR(255) NOT NULL,
  link TEXT,
  published_at TIMESTAMP WITH TIME ZONE,
  content_hash VARCHAR(64),
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for articles
CREATE INDEX IF NOT EXISTS idx_articles_run_id ON articles(run_id);
CREATE INDEX IF NOT EXISTS idx_articles_theme_name ON articles(theme_name);
CREATE INDEX IF NOT EXISTS idx_articles_content_hash ON articles(content_hash);
CREATE INDEX IF NOT EXISTS idx_articles_created_at ON articles(created_at DESC);

-- Table 4: Sync Metadata
-- Tracks sync state and last update times
CREATE TABLE IF NOT EXISTS sync_metadata (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  key VARCHAR(255) UNIQUE NOT NULL,
  value TEXT,
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for sync_metadata
CREATE INDEX IF NOT EXISTS idx_sync_metadata_key ON sync_metadata(key);

-- Enable Row Level Security (RLS) for security
-- This allows read-only access to the data
ALTER TABLE trend_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE theme_summaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE articles ENABLE ROW LEVEL SECURITY;
ALTER TABLE sync_metadata ENABLE ROW LEVEL SECURITY;

-- Create policies for read-only access (public can read, but not write)
-- You can adjust these based on your security requirements

CREATE POLICY "trend_runs_read" ON trend_runs
  FOR SELECT
  USING (true);

CREATE POLICY "theme_summaries_read" ON theme_summaries
  FOR SELECT
  USING (true);

CREATE POLICY "articles_read" ON articles
  FOR SELECT
  USING (true);

CREATE POLICY "sync_metadata_read" ON sync_metadata
  FOR SELECT
  USING (true);

-- Grant permissions to authenticated users
-- Note: You may need to adjust these based on your Supabase service role key
GRANT SELECT ON trend_runs TO anon;
GRANT SELECT ON theme_summaries TO anon;
GRANT SELECT ON articles TO anon;
GRANT SELECT ON sync_metadata TO anon;

GRANT INSERT, UPDATE ON trend_runs TO anon;
GRANT INSERT, UPDATE ON theme_summaries TO anon;
GRANT INSERT, UPDATE ON articles TO anon;
GRANT INSERT, UPDATE ON sync_metadata TO anon;

-- Create views for common queries (optional but useful)

-- View: Latest run with theme counts
CREATE OR REPLACE VIEW latest_run_summary AS
SELECT
  tr.id,
  tr.run_timestamp,
  tr.run_date,
  tr.total_articles,
  COUNT(DISTINCT ts.theme_name) as theme_count,
  tr.created_at
FROM trend_runs tr
LEFT JOIN theme_summaries ts ON tr.id = ts.run_id
GROUP BY tr.id, tr.run_timestamp, tr.run_date, tr.total_articles, tr.created_at
ORDER BY tr.run_timestamp DESC
LIMIT 1;

-- View: Theme evolution (last 5 runs per theme)
CREATE OR REPLACE VIEW theme_evolution AS
WITH ranked_summaries AS (
  SELECT
    ts.theme_name,
    ts.run_id,
    tr.run_timestamp,
    ts.what_is_happening,
    ts.why_it_matters,
    ts.what_to_watch,
    ts.article_count,
    ROW_NUMBER() OVER (PARTITION BY ts.theme_name ORDER BY tr.run_timestamp DESC) as run_number
  FROM theme_summaries ts
  JOIN trend_runs tr ON ts.run_id = tr.id
)
SELECT * FROM ranked_summaries WHERE run_number <= 5;

-- View: Article statistics by theme
CREATE OR REPLACE VIEW article_stats_by_theme AS
SELECT
  theme_name,
  COUNT(*) as total_articles,
  COUNT(DISTINCT run_id) as runs_with_articles,
  COUNT(DISTINCT source_name) as unique_sources,
  MAX(published_at) as latest_article_date
FROM articles
GROUP BY theme_name
ORDER BY total_articles DESC;

-- Table 5: Quality Evaluations
-- Persists weekly / on-demand evaluation results from core/evaluator.py.
-- Three judge agents score (1) classifier correctness, (2) summary faithfulness,
-- (3) summary uniqueness against source articles and across runs.
CREATE TABLE IF NOT EXISTS quality_evaluations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lookback_days INT NOT NULL,
    runs_evaluated JSONB NOT NULL,           -- list of run_ids included
    threshold FLOAT NOT NULL,                -- 0..1, threshold used for this eval
    classifier_score FLOAT NOT NULL,         -- 0..1
    faithfulness_score FLOAT NOT NULL,       -- 0..1
    uniqueness_score FLOAT NOT NULL,         -- 0..1
    per_theme_classifier JSONB,              -- {theme_name: score}
    recommendations JSONB,                   -- list of human-readable strings
    raw_metrics JSONB                        -- full evaluation payload
);

CREATE INDEX IF NOT EXISTS idx_quality_evaluations_generated_at
    ON quality_evaluations (generated_at DESC);

-- Enable Row Level Security (read-only public access, same as other tables)
ALTER TABLE quality_evaluations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Public read access for quality_evaluations"
    ON quality_evaluations FOR SELECT
    USING (true);

-- End of schema setup
-- You can now use the ai-pulse app with Supabase persistence!
