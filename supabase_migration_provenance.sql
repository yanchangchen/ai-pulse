-- Migration: Persist summary generation provenance to Supabase
-- Lets the Memory Wiki + dashboards display whether a brief was produced by:
--   * ollama:<model>             — live Ollama synthesis
--   * gemini:<model>             — on-demand Gemini synthesis
--   * extractive_fallback        — non-LLM LexRank/Luhn summary (quota, transport, empty response)
--   * ollama:no_articles         — empty article pool placeholder
--   * ollama:limited_coverage    — fewer than 3 articles placeholder
--   * ollama:error               — LLM client error stub
--   * ollama:no_new_articles_skip — every article already covered by an earlier run
--
-- Generation log is stored as JSONB so we can extend _generation_log with new
-- fields (latency_ms, prompt_tokens, …) without further migrations.
--
-- The Python write path silently retries without these columns if the table
-- is on the legacy schema, so this migration is safe to apply retroactively
-- and harmless to skip — new rows just lose provenance.

ALTER TABLE theme_summaries
    ADD COLUMN IF NOT EXISTS generation_source TEXT,
    ADD COLUMN IF NOT EXISTS generation_log JSONB;

-- Backfill a sentinel for any rows written before the migration.
UPDATE theme_summaries
   SET generation_source = 'extractive_fallback'
 WHERE generation_source IS NULL;

-- Index for cross-run provenance analytics (e.g. "how many of the last 30
-- runs were LLM-synthesised vs. fallback?").
CREATE INDEX IF NOT EXISTS idx_theme_summaries_generation_source
    ON theme_summaries(generation_source);

-- Verify
SELECT column_name, data_type
  FROM information_schema.columns
 WHERE table_name = 'theme_summaries'
   AND column_name IN ('generation_source', 'generation_log')
 ORDER BY column_name;

SELECT 'Provenance columns added to theme_summaries' AS status;
