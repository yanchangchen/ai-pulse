-- supabase_migration_app_settings.sql
-- Adds the app_settings key-value table used as the cloud persistence layer
-- for evaluation-driven configuration (summariser tuner + classification
-- settings from config/custom_settings.json, and the auto-remediation
-- pending-experiment state from data/auto_remediation_state.json).
--
-- Without this table those files are local-only: on Streamlit Community
-- Cloud every redeploy (git push) starts with a clean filesystem, so tuner
-- settings, classification mode, and rollback baselines reset to defaults.
-- With it, the app loads remote-first and writes through on every change,
-- falling back to the local files when Supabase is unavailable.

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,            -- 'custom_settings' | 'auto_remediation_state'
    value JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_app_settings_updated_at
    ON app_settings (updated_at DESC);

ALTER TABLE app_settings ENABLE ROW LEVEL SECURITY;

-- Public read access for the Streamlit app
CREATE POLICY "Public read access for app_settings"
    ON app_settings FOR SELECT
    USING (true);

-- Public write access for the Streamlit app (no auth in this single-user app)
CREATE POLICY "Public write access for app_settings"
    ON app_settings FOR ALL
    USING (true)
    WITH CHECK (true);
