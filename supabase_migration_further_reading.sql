-- Migration: persist the "Strategic Further Reading" section
--
-- Why: core/evaluator.py's grounding judge verifies further_reading
-- citations against source articles, and the structural compliance judge
-- requires 5 sections.  Neither column existed on theme_summaries, so
-- grounding was vacuously 1.0 and structural compliance was capped at
-- 87.5%.  The summariser already produces summary["further_reading"];
-- this migration lets save_theme_summary() persist it.
--
-- Idempotent: safe to run multiple times, on fresh or old deployments.
-- Run AFTER supabase_schema.sql (or standalone — the table must exist).

ALTER TABLE theme_summaries
  ADD COLUMN IF NOT EXISTS further_reading TEXT NOT NULL DEFAULT '';
