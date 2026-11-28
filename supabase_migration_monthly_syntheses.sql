-- Phase 6.1: monthly_syntheses — Tier-3 hierarchical memory.
--
-- LLM-generated monthly macro syntheses per theme, rolled up from the
-- Tier-2 daily theme summaries (theme_summaries). Feeds Sage's macro
-- context for multi-month questions (consumption lands in Phase 6.2).
--
-- Cadence (core/monthly_synthesis.py): completed months are synthesized
-- once (idempotent); the CURRENT month is re-synthesized every
-- monthly_refresh_days (default 14); a month whose row was written
-- mid-month regenerates once after rollover so the final rollup covers
-- the full month. Runs best-effort in BackgroundRefresher + the
-- backfill_monthly_syntheses.py CLI — NOT pg_cron (external LLM calls).
--
-- NOTE: fixes the Future.md §4 draft bug — UNIQUE on year_month alone
-- would permit only one row per month; the natural key is
-- (year_month, theme_name). Its index also covers year_month lookups.
--
-- updated_at is stamped explicitly by the client on every upsert
-- (DEFAULT NOW() only fires on INSERT); the staleness rules depend on it.
--
-- No inline SQL backfill: rollups require LLM calls, so the backfill is
-- `python backfill_monthly_syntheses.py` (same runner as the pipeline).
--
-- Idempotent: safe to re-run.

CREATE TABLE IF NOT EXISTS monthly_syntheses (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  year_month VARCHAR(7) NOT NULL,               -- e.g. '2026-09'
  theme_name VARCHAR(255) NOT NULL,
  macro_summary TEXT NOT NULL,
  key_milestones JSONB NOT NULL DEFAULT '[]'::jsonb,
  source_run_count INT NOT NULL DEFAULT 0,      -- daily briefs fed to the rollup
  generation_source VARCHAR(100),               -- e.g. 'qwen:qwen3.8-flash' (audit)
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  UNIQUE (year_month, theme_name)
);

ALTER TABLE monthly_syntheses ENABLE ROW LEVEL SECURITY;

-- Rows are upserted (INSERT ... ON CONFLICT DO UPDATE) on refresh, so the
-- policy must allow UPDATE too — FOR ALL, like app_settings.
DROP POLICY IF EXISTS "monthly_syntheses_public_access" ON monthly_syntheses;
CREATE POLICY "monthly_syntheses_public_access" ON monthly_syntheses
  FOR ALL USING (true) WITH CHECK (true);

GRANT SELECT, INSERT, UPDATE ON monthly_syntheses TO anon;

-- Verify
SELECT year_month, theme_name, source_run_count, updated_at
FROM monthly_syntheses ORDER BY year_month DESC, theme_name;