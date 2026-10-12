-- supabase_migration_sage_evaluations.sql
-- Phase 2 gate: the Sage faithfulness judge's durable score store.
--
-- Every successful Sage reply is judged in the background (chat-time,
-- fire-and-forget daemon thread — see record_sage_faithfulness in
-- core/evaluator.py) against the exact wiki + article context that Sage was
-- given.  The context string and the answer coexist only at the moment of
-- reply, so judging happens right there and lands here as one row per judged
-- reply.  This table IS the Phase 2 baseline: per Future.md's recorded
-- sequencing decision there is no pre-2.1 baseline — judge scores start from
-- post-2.3, and Phase 3 retrieval changes get measured against this batch.
--
-- Only successful judgments insert rows: judge LLM outages and unparseable
-- responses are logged and skipped, never scored as zeros (same failure
-- semantics as the summary faithfulness judges) — a judge outage must not
-- masquerade as mass hallucination in the trend chart.
--
-- judge_model / answer_model are stored per row so scores stay comparable
-- across judge model changes and answer-engine changes.

CREATE TABLE IF NOT EXISTS sage_evaluations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    conversation_id TEXT,                -- sage_conversations id when a thread is active
    question TEXT NOT NULL,
    answer_text TEXT NOT NULL,
    score FLOAT NOT NULL,                -- 0..1: fraction of claims the context supports
    unsupported_claims JSONB,            -- judge-extracted list of unsupported claims
    judge_provider VARCHAR(50),
    judge_model VARCHAR(100),            -- model that judged (comparability)
    answer_model VARCHAR(100),           -- model that answered (the Sage engine chip twin)
    latency_ms INT                       -- judge LLM call latency
);

CREATE INDEX IF NOT EXISTS idx_sage_evaluations_created_at
    ON sage_evaluations (created_at DESC);

ALTER TABLE sage_evaluations ENABLE ROW LEVEL SECURITY;

-- Public read access for the Streamlit app
CREATE POLICY "Public read access for sage_evaluations"
    ON sage_evaluations FOR SELECT
    USING (true);

-- Public write access for the Streamlit app (no auth in this single-user app)
CREATE POLICY "Public write access for sage_evaluations"
    ON sage_evaluations FOR INSERT
    WITH CHECK (true);

GRANT SELECT, INSERT ON sage_evaluations TO anon;
