-- supabase_migration_llm_calls.sql
-- Adds the llm_calls observability log: every completed Model Gateway
-- execution (LLM success, deterministic fallback, total failure) is
-- mirrored here by core/ai_gateway/call_log.py with provider-reported
-- token usage, latency, cost class, and correlation id.
--
-- This is the project's durable token/cost accounting. Free-tier LLM
-- observability backends (Langfuse Cloud Hobby, Phase 0.3) retain traces
-- for ~30 days; llm_calls keeps the permanent record so upgrade decisions
-- are data-driven rather than speculative. trace_id stays NULL until the
-- OpenTelemetry integration links trace ids into this table.
--
-- Rows are append-only; nothing else in the app reads or writes this table
-- except the gateway call log and (optionally) usage dashboards.

CREATE TABLE IF NOT EXISTS llm_calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    task_type VARCHAR(50) NOT NULL,     -- 'sage_chat' | 'summarise' | 'classify'... (TaskType values)
    provider VARCHAR(50),               -- 'ollama' | 'google' | NULL (deterministic/failed)
    model VARCHAR(100),                 -- e.g. 'nemotron-3-ultra:cloud'
    tokens_in INT,                      -- provider-reported prompt tokens (nullable)
    tokens_out INT,                     -- provider-reported completion tokens (nullable)
    latency_ms INT,
    cost_class VARCHAR(20),             -- 'low' | 'medium' (gateway registry label)
    correlation_id VARCHAR(40),         -- AITaskRequest 8-char correlation id
    trace_id VARCHAR(100),              -- OTel/Langfuse trace id (Phase 0.3; NULL for now)
    success BOOLEAN NOT NULL DEFAULT TRUE,
    error_text TEXT
);

CREATE INDEX IF NOT EXISTS idx_llm_calls_created_at
    ON llm_calls (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_llm_calls_task_type
    ON llm_calls (task_type);

ALTER TABLE llm_calls ENABLE ROW LEVEL SECURITY;

-- Public read access for the Streamlit app
CREATE POLICY "Public read access for llm_calls"
    ON llm_calls FOR SELECT
    USING (true);

-- Public write access for the Streamlit app (no auth in this single-user app)
CREATE POLICY "Public write access for llm_calls"
    ON llm_calls FOR ALL
    USING (true)
    WITH CHECK (true);
