-- Phase 4.3 — Fair drain / backlog carryover: times-seen counter on articles.
--
-- times_seen counts runs in which an article was classified but NOT included
-- in a theme summary (the waiting backlog). The summariser's relevance ranking
-- adds aging_boost_per_run * times_seen so long-waiting articles eventually
-- outrank fresh ones and drain within the 14-day fetch window. Reset to 0
-- when the article is finally included.
--
-- The counter is maintained by record_article_seen() rather than by the
-- save_articles() upsert: PostgREST upserts cannot express per-row increments,
-- and a partial-column upsert would insert title-less rows for articles whose
-- first save happens after summarisation. An UPDATE-only RPC also keeps the
-- increment atomic with the run.
--
-- Apply once against the project (idempotent):
--   supabase db push  /  SQL editor  /  MCP execute_sql

ALTER TABLE articles
    ADD COLUMN IF NOT EXISTS times_seen INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN articles.times_seen IS
    'Phase 4.3: runs classified-but-not-summarised (fair-drain aging boost); 0 once included.';

-- Partial index: only waiting rows matter to the ranking lookup.
CREATE INDEX IF NOT EXISTS idx_articles_times_seen
    ON articles (theme_name, times_seen)
    WHERE times_seen > 0;

CREATE OR REPLACE FUNCTION record_article_seen(
    p_theme TEXT,
    p_waiting TEXT[],
    p_included TEXT[]
) RETURNS VOID
LANGUAGE sql
SECURITY INVOKER
AS $$
    UPDATE articles
       SET times_seen = times_seen + 1
     WHERE theme_name = p_theme
       AND content_hash = ANY(p_waiting);

    UPDATE articles
       SET times_seen = 0
     WHERE theme_name = p_theme
       AND content_hash = ANY(p_included);
$$;