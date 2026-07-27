-- supabase_migration_keywords.sql
-- Adds the keyword_suggestions table used by the Quality Evaluation page.
-- Run once in the Supabase SQL Editor on existing projects.

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