-- supabase_migration_custom_keywords.sql
-- Adds the custom_keywords table used as the cloud persistence layer for
-- theme keyword overlays. Falls back to config/custom_keywords.json when
-- Supabase is unavailable.

CREATE TABLE IF NOT EXISTS custom_keywords (
    theme_name VARCHAR(255) PRIMARY KEY,
    keywords JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_custom_keywords_updated_at
    ON custom_keywords (updated_at DESC);

ALTER TABLE custom_keywords ENABLE ROW LEVEL SECURITY;

-- Public read access for the Streamlit app
CREATE POLICY "Public read access for custom_keywords"
    ON custom_keywords FOR SELECT
    USING (true);

-- Public write access for the Streamlit app (no auth in this single-user app)
CREATE POLICY "Public write access for custom_keywords"
    ON custom_keywords FOR ALL
    USING (true)
    WITH CHECK (true);
