-- supabase_migration_article_embeddings.sql
-- Phase 3: dense vector embeddings on articles (the storage half of Pillar A).
--
-- Sage's Tier-1 retrieval is currently keyword/FTS-only (match_articles over
-- search_vector).  That misses *conceptual* matches — a query about "reasoning
-- token overhead" never keyword-matches an article that discusses it under
-- different wording.  This migration adds the pgvector store those embeddings
-- land in; the *retrieval* half (hybrid RRF + temporal awareness) is Phase 5.
--
-- One embedding per article in v1: article summaries are <=1500 chars, so
-- chunking is deferred — UNIQUE(article_id) enforces that.  chunk_text records
-- the exact string that was embedded (title + summary) so embeddings are
-- re-derivable and auditable; embedding_model records which model produced the
-- vector, for future re-embed decisions.
--
-- embedding is vector(768), matching gemini-embedding-001 with
-- output_dimensionality=768 (see config/settings.py GEMINI_EMBEDDING_MODEL).
-- run_date is denormalized from trend_runs so the temporal bounding box
-- (Phase 5) can filter without a join.
--
-- match_article_embeddings is a minimal nearest-neighbour lookup needed only
-- for the Phase 3 hit-rate gate (core/retrieval_eval.py).  Phase 5 builds the
-- real hybrid retriever on top of it.  Invoker rights (not SECURITY DEFINER):
-- anon holds SELECT on article_embeddings through the read policy below, so the
-- function only needs EXECUTE granted.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS article_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    article_id UUID NOT NULL UNIQUE REFERENCES articles(id) ON DELETE CASCADE,
    run_date DATE NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector(768) NOT NULL,
    embedding_model VARCHAR(100) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_article_embeddings_hnsw
    ON article_embeddings USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_article_embeddings_run_date
    ON article_embeddings (run_date);

ALTER TABLE article_embeddings ENABLE ROW LEVEL SECURITY;

-- Public read access for the Streamlit app (Sage retrieval + the eval gate)
CREATE POLICY "Public read access for article_embeddings"
    ON article_embeddings FOR SELECT
    USING (true);

-- Public write access for the Streamlit app (ingest + backfill)
CREATE POLICY "Public write access for article_embeddings"
    ON article_embeddings FOR INSERT
    WITH CHECK (true);

GRANT SELECT, INSERT ON article_embeddings TO anon;

CREATE OR REPLACE FUNCTION match_article_embeddings(
  query_embedding vector(768),
  match_count int DEFAULT 10
)
RETURNS TABLE (
  id uuid,
  article_id uuid,
  chunk_text text,
  similarity float
)
LANGUAGE sql
STABLE
AS $$
  SELECT e.id, e.article_id, e.chunk_text,
         1 - (e.embedding <=> query_embedding) AS similarity
  FROM article_embeddings e
  ORDER BY e.embedding <=> query_embedding
  LIMIT match_count;
$$;

GRANT EXECUTE ON FUNCTION match_article_embeddings(vector, int) TO anon;

-- Articles that still need an embedding (the ingest step and the backfill both
-- drain this set; the LEFT JOIN keeps it resumable — an article drops out of
-- the result the moment its embedding lands).  Newest-first so the ingest
-- step's bounded limit covers freshly-saved articles before draining history.
-- run_date is coalesced from trend_runs so articles written before Phase 3
-- (no reliable run linkage) still get a sane date for the Phase 5 filters.
CREATE OR REPLACE FUNCTION get_unembedded_articles(limit_count int DEFAULT 200)
RETURNS TABLE (
  article_id uuid,
  run_date date,
  title text,
  summary text,
  content_hash text
)
LANGUAGE sql
STABLE
AS $$
  SELECT a.id AS article_id,
         COALESCE(tr.run_date, a.created_at::date) AS run_date,
         a.title,
         COALESCE(a.summary, '') AS summary,
         a.content_hash
  FROM articles a
  LEFT JOIN trend_runs tr ON a.run_id = tr.id
  LEFT JOIN article_embeddings e ON e.article_id = a.id
  WHERE e.article_id IS NULL
  ORDER BY a.created_at DESC
  LIMIT limit_count;
$$;

GRANT EXECUTE ON FUNCTION get_unembedded_articles(int) TO anon;