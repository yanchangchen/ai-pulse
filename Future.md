# Sage RAG Architecture & Long-Term Roadmap

This document outlines the architectural roadmap for evolving **Sage** from its current character-budgeted prompt-injection mechanism into a production-grade, multi-stage **Retrieval-Augmented Generation (RAG)** intelligence system.

> **Revision note (2026-10):** Roadmap re-reviewed and re-phased for smaller, independently verifiable increments. Added Pillar F (Observability — Langfuse/Phoenix), a Deployment & Operations section, and corrected §4 against the schema that actually exists today. Research sources listed at the end.

---

## 1. Executive Summary & Vision

Sage is designed to be the conversational analyst embedded in AI Pulse, answering questions about long-term AI trends, company moves, architectural shifts, and chronology.

As the memory archive expands across months and years of daily news runs, traditional context-window stuffing degrades due to:
1. **Context Window Saturation**: Even large-context LLMs suffer from "lost in the middle" degradation when processing 50+ runs.
2. **Coarse-Grained Retrieval**: Answering granular queries (e.g., *"What did Simon Willison say about GPT-6 Astra adapters?"*) requires article-level citations rather than high-level run summaries.
3. **Temporal Incoherence**: Standard semantic vector search ignores publication chronology, confusing the order in which breakthroughs occurred.

The long-term vision transforms Sage into a **Time-Aware, Multi-Stage Hierarchical RAG Agent** — with two enabling layers added in this revision:
4. **Observability first**: A 3-agent RAG pipeline cannot be debugged without traces. Every LLM call and retrieval stage must be traced and evaluated before Pillars A–E land.
5. **Decoupled operations**: Ingestion moves out of the Streamlit request thread into a scheduled job, so the UI becomes read-mostly and deployable on free hosting.

---

## 2. Core Architecture Blueprint

```
                      ┌──────────────────────────────┐
                      │          User Query          │
                      └──────────────┬───────────────┘
                                     │
                                     ▼
                ┌──────────────────────────────────────────┐
                │   Query Understanding & Routing Engine   │
                │  - Temporal entity extraction (dates)    │
                │  - Theme & keyword routing               │
                │  - Question intent (Timeline vs Deep)    │
                └────────────────────┬─────────────────────┘
                                     │
                    ┌────────────────┴────────────────┐
                    │                                 │
                    ▼                                 ▼
      ┌───────────────────────────┐     ┌───────────────────────────┐
      │   Dense Vector Search     │     │   Sparse Keyword (BM25)   │
      │  (pgvector / Gemini Embed)│     │   (Postgres tsvector/FTS) │
      └─────────────┬─────────────┘     └─────────────┬─────────────┘
                    │                                 │
                    └────────────────┬────────────────┘
                                     │
                                     ▼
                    ┌─────────────────────────────────┐
                    │      Reciprocal Rank Fusion     │
                    │   & Temporal Decay Re-ranking   │
                    └────────────────┬─────────────────┘
                                     │
                                     ▼
                    ┌─────────────────────────────────┐
                    │     Hierarchical Memory Assembly│
                    │  - Tier 3: Monthly Rollup       │
                    │  - Tier 2: Theme Summaries      │
                    │  - Tier 1: Cited Articles       │
                    └────────────────┬─────────────────┘
                                     │
                                     ▼
                    ┌─────────────────────────────────┐
                    │          Sage LLM Engine        │
                    │  (routed via core/ai_gateway)   │
                    │  - Chronological account        │
                    │  - Deep hyperlinks to sources   │
                    │  - Analyst synthesis            │
                    └────────────────┬─────────────────┘
                                     │
                                     ▼
                    ┌─────────────────────────────────┐
                    │  Observability (Pillar F)       │
                    │  OTel traces → Langfuse/Phoenix │
                    │  + durable llm_calls in Supabase│
                    └─────────────────────────────────┘
```

**Prerequisite fix:** Sage currently calls `core.llm_client.LLMClient` directly, bypassing `core/ai_gateway`. Routing Sage through the gateway is **Phase 0** — everything else (provenance, fallback, usage capture, tracing) builds on it.

---

## 3. Key Architectural Pillars

### Pillar A: Two-Stage Hybrid Retrieval (Dense + Sparse)
* **Dense Retrieval**:
  - Vector embeddings generated via Google Gemini embedding models (768-dim class) or local Ollama embeddings (`nomic-embed-text`). *Verify current model names/limits at implementation time — Google's embedding lineup rotates.*
  - Stored in Supabase using the `pgvector` extension with HNSW indexes.
  - Generates semantic matches for conceptual questions (*"reasoning token overhead"*, *"chip export restrictions"*).
* **Sparse Retrieval**:
  - PostgreSQL full-text search (`tsvector` and `tsquery`) with English dictionary stemmer.
  - Matches exact entity names, model releases, and numbers (*"Claude 3.7 Sonnet"*, *"Llama 3.3 70B"*, *"SWE-bench 87.6%"*).
* **Hybrid Fusion**:
  - Reciprocal Rank Fusion (RRF) combines scores from dense and sparse retrieval to produce the final candidate set.

**Free-tier guardrails (Supabase Free plan):** the whole project shares **500 MB of database storage** and ~1 GB RAM (Micro compute) — the RAM is the real HNSW constraint, not disk. At AI Pulse volume (~30 articles/run, 1–3 chunks each ≈ ~100 chunks/day), a 768-dim index is comfortably within limits for years; if it ever isn't: switch to `halfvec` (2 bytes/dim), or upgrade compute. Chunk text + embeddings must be the only heavy columns in the table.

### Pillar B: Hierarchical Memory Architecture

Rather than treating every record at the same level of granularity, store and index intelligence across three distinct tiers:

| Tier | Granularity | Storage Table | Purpose |
|---|---|---|---|
| **Tier 1: Article Evidence** | Granular quotes, titles, original URLs | `articles` & `article_embeddings` | Answering specific questions, providing primary source hyperlinks |
| **Tier 2: Run Briefs** | Daily theme summaries (What happened, Why it matters, Watch) | `theme_summaries` | Tracking weekly progression and thematic movements |
| **Tier 3: Monthly Syntheses** | Macro monthly trends and strategic landscape rollups | `monthly_syntheses` | Answering multi-month evolution questions without token blowout |

When a query spans multiple quarters, Sage retrieves the Tier 3 rollups for macro context, and drills down to Tier 1 articles only for the specific milestones cited.

### Pillar C: Temporal-Aware RAG

News intelligence is inherently chronological. Standard RAG produces hallucinations if it confuses which event preceded another.
1. **Query Date Parsing**:
   - Extract relative and absolute date expressions (e.g. *"last week"*, *"August 2026"*, *"between June and September"*).
   - Constrain retrieval to the resolved temporal bounding box before vector distance calculation.
2. **First-Appearance Tracking**:
   - Maintain a dedicated `theme_first_seen` index to answer queries like *"When was this concept first mentioned?"* with single-millisecond index lookups. (Today this is computed at render time by scanning summaries on every request — `annotate_first_appearances` in `sage_agent.py` — an early incremental win is to persist it at ingest.)
3. **Time-Decayed Relevance Scoring**:
   - Score formula: $S(d, q) = \lambda \cdot \text{Similarity}(d, q) + (1 - \lambda) \cdot e^{-\gamma (T_{current} - T_{run})}$.
   - Balances relevance against freshness unless historical comparison is explicitly requested.

**⚠️ pgvector gotcha for the temporal bounding box:** combining an HNSW index with a `WHERE run_date BETWEEN …` filter can return *fewer* rows than requested `LIMIT` (the ANN graph doesn't guarantee matching rows). Mitigations, in order of preference: (a) keep the filtered date range wide and post-filter, (b) use pgvector's iterative search if available on the Supabase Postgres version, (c) partial indexes per month. Do not rely on naive filter+LIMIT.

### Pillar D: Deep Grounding & Interactive Citations

1. **Direct Article Links**:
   - Sage responses should cite original articles using Markdown hyperlinks retrieved from the `articles.link` column:
     > *"On September 12, OpenAI launched o1 [OpenAI Announcement](https://openai.com/...)."*
2. **Evidence Popovers**:
   - In the Streamlit UI, cited runs and articles should be inspectable in an accordion or side-drawer showing the exact article snippet that grounded each claim.

### Pillar E: Multi-Agent Synthesis Workflow

For complex research questions, expand Sage from a single LLM call into a 3-agent pipeline:
1. **Sage Scout (Retriever Agent)**: Analyzes the question, generates 2–3 sub-queries, executes vector + date queries, and compiles the relevant context documents.
2. **Sage Fact-Checker (Faithfulness Evaluator)**: Validates that each statement in the proposed response is explicitly supported by retrieved context.
3. **Sage Analyst (Synthesis Agent)**: Drafts the final prose using the strict AI Pulse voice (Factual chronological account + "My read on this").

**Gate:** this phase lands *last* and only after Pillar F — a 3-agent pipeline without per-agent traces is undebuggable by construction.

### Pillar F: Observability & Evaluation (NEW)

Agent traces are the troubleshooting backbone for everything above.

**Tooling decision (free-first):**

> **Decision (2026-10): Langfuse Cloud (Hobby).** Alternatives recorded below for future reference; since instrumentation is OpenTelemetry-based, switching backends later means changing the exporter, not the code.

| Option | License | Cost to run | Ops burden | Fit |
|---|---|---|---|---|
| **Langfuse Cloud (Hobby)** ✅ chosen | MIT (cloud is SaaS) | Free tier (~50k units/mo, 30-day retention — verify current terms) | None | **Chosen default.** Single-user volume fits easily; prompt management + evals included |
| **AgentOps** | OSS | Free, self-host dashboard | Low | Runner-up: Python SDK with `agentops.init()` auto-instrumentation, session replay, per-session cost tracking. Best if we ever want fully local tracing with near-zero setup |
| **Arize Phoenix** | Apache-2.0 | Free, single container | Low | Self-host fallback if data must stay local; strongest RAG/retrieval-trace debugging (UMAP over embeddings) |
| **Langfuse self-hosted** | MIT | Free, but 6 services (web, worker, Postgres, ClickHouse, Redis, MinIO) | High | Only worth it on a paid VPS with real ops intent; overkill for this project today |
| LangSmith | Closed | Paid | — | Ruled out |
| Helicone | Free tier + OSS | Free | Low | Ruled out: proxy/gateway-first — would duplicate the routing/fallback layer `core/ai_gateway` already provides |

*Context: Langfuse was acquired by ClickHouse in Jan 2026; it remains open-source and self-hostable, but production self-hosting grew heavy (the 6-service stack above). Phoenix remains lightweight.*

**Instrumentation plan:**
1. **Phase 0 requirement — route Sage through `core/ai_gateway`** so every Sage call already produces `Provenance` (provider, model, latency, attempts, fallback, correlation_id).
2. Extend the gateway contracts (`Provenance`) to carry **token usage** — providers already parse it today but discard it.
3. Emit **OpenTelemetry spans** for each gateway call and each Sage pipeline stage (context assembly, retrieval, fusion, generation). Export OTLP to Langfuse (or Phoenix). Env-gated (`LANGFUSE_PUBLIC_KEY`/`SECRET_KEY` absent → no-op) so tests and offline runs stay clean; follow the existing conftest pattern of pinning external services off.
4. **Durable record**: traces are short-lived on free tiers (30-day retention), so mirror every LLM call into a new `llm_calls` Supabase table (timestamp, provider, model, tokens in/out, latency, cost_class, correlation_id, trace_id). This doubles as the cost accounting the project currently lacks entirely.
5. **Sage evaluation**: the existing eval infra (evaluator agents, `quality_evaluations` table, page 7) covers summaries and classification only. Add a faithfulness-vs-retrieved-context judge for Sage answers, reusing the same plumbing.

---

## 4. Supabase Database Schema Additions for Future RAG

> **Corrected 2026-10:** `articles`, `theme_summaries`, and `sage_conversations` **already exist** (see `supabase_schema.sql` + `supabase_migration_sage_conversations.sql` — note the real column is `period_label`, not `period_info` as previously written here). The genuinely new tables are `article_embeddings`, `monthly_syntheses`, `theme_first_seen`, and `llm_calls`.

```sql
-- Enable pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- Article chunks & embeddings for granular retrieval
CREATE TABLE IF NOT EXISTS article_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    article_id UUID REFERENCES articles(id) ON DELETE CASCADE,
    run_date DATE NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector(768),          -- switch to halfvec(768) if storage pressure
    embedding_model VARCHAR(100) NOT NULL,  -- model + version, for re-embed decisions
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_article_embeddings_hnsw
ON article_embeddings USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_article_embeddings_run_date
ON article_embeddings (run_date);  -- supports the temporal bounding box

-- Sparse retrieval: FTS index on articles (Pillar A, needed from Phase 2)
-- (column + GIN index to be added via migration at Phase 2.3)

-- Monthly macro rollups
CREATE TABLE IF NOT EXISTS monthly_syntheses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    year_month VARCHAR(7) NOT NULL UNIQUE, -- e.g. '2026-09'
    theme_name VARCHAR(255) NOT NULL,
    macro_summary TEXT NOT NULL,
    key_milestones JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- First-appearance index (Pillar C #2) — maintained at ingest, replaces render-time scan
CREATE TABLE IF NOT EXISTS theme_first_seen (
    theme_name VARCHAR(255) PRIMARY KEY,
    first_seen_date DATE NOT NULL,
    first_seen_run_id UUID,
    evidence_summary TEXT
);

-- Durable LLM call log (Pillar F #4) — cost accounting + trace linkage
CREATE TABLE IF NOT EXISTS llm_calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    task_type VARCHAR(50) NOT NULL,      -- 'sage_chat' | 'summarise' | 'classify' | ...
    provider VARCHAR(50) NOT NULL,
    model VARCHAR(100) NOT NULL,
    tokens_in INT,
    tokens_out INT,
    latency_ms INT,
    cost_class VARCHAR(20),              -- 'low' | 'medium' (matches gateway registry)
    correlation_id VARCHAR(40),          -- AITaskRequest 8-char correlation id
    trace_id VARCHAR(100),               -- Langfuse/Phoenix trace id
    success BOOLEAN NOT NULL DEFAULT TRUE,
    error_text TEXT
);

-- sage_conversations already exists (period_label column) — nothing to add.
```

---

## 5. Phased Implementation Roadmap

Phases are deliberately smaller than the original five — each sub-step is independently shippable and verifiable, and most have an explicit evaluation gate before moving on.

- [x] **Phase 1 (Completed)**: Fix query order, period selector (Last 7d, 30d, Sep 2026, All Time, Custom), theme filtering in chat, conversation persistence (JSON/Supabase sync), Markdown export.

### Phase 0 — Foundations: routing + observability (do this before any RAG work)
- [x] **0.1** Route Sage through `core/ai_gateway` (new `sage_chat` task policy): gains fallback chain, health tracking, provenance chips for free. *(Done 2026-10: `SAGE_CHAT` TaskType, Ollama-primary policy with Gemini fallbacks, `chat_with_sage` rewritten to gateway contract returning `{"response", "provenance"}`, provenance caption in the UI.)*
- [x] **0.2** Extend `Provenance` to carry token usage; add `llm_calls` table + gateway hook so every call (Sage, summariser, classifier, evaluator) is persisted with tokens/latency/cost_class. *(Done 2026-10: `Provenance.tokens_in/out` populated from provider usage; `core/ai_gateway/call_log.py` fire-and-forget writer; `insert_llm_call` on the Supabase manager; `supabase_migration_llm_calls.sql` — note `correlation_id` is VARCHAR(40), not UUID, matching the 8-char request ids.)*
- [x] **0.3** Langfuse Cloud Hobby account + OTel span export from the gateway (env-gated no-op when keys absent). Link `trace_id` into `llm_calls`. *(Done 2026-10: `core/ai_gateway/tracing.py` — one span per gateway execution, OTLP HTTP export to `{LANGFUSE_HOST}/api/public/otel/v1/traces` with Basic-auth keys, `gen_ai.*`/`langfuse.observation.*` attributes so LLM calls render as generations, input/output capped at ~2000 chars. `Provenance.trace_id` (32-char hex) flows into the `llm_calls` row. Keys resolve via the `config.settings` chain (`LANGFUSE_PUBLIC_KEY`/`SECRET_KEY`/`HOST`); conftest autouse fixture pins tracing off in tests. Deps: `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`.)*
- [ ] **0.4** (optional but cheap) `Dockerfile` + `docker-compose.yml` (app + nothing else — Supabase stays cloud) for reproducible deploys.
- **Gate**: a Sage chat produces a visible trace in Langfuse and a row in `llm_calls`. *(Verified 2026-10 with a real US-region Langfuse Hobby project: one live `sage_chat` gateway call exported a span with no OTLP errors and the newest `llm_calls` row carried the matching 32-hex `trace_id`. Project is US-region — `LANGFUSE_HOST = https://us.cloud.langfuse.com` in secrets.toml.)*

### Phase 2 — Article-level grounding (sparse retrieval only, no vectors yet)
- [x] **2.1** Add `articles` retrieval to Sage context assembly (SQL: recent + keyword match within budget), alongside theme summaries. *(Done 2026-10: `SupabaseManager.get_articles_for_context()` fetches a bounded newest-first candidate window (theme/source/date filters, no SQL keyword matching — that's Phase 2.3 FTS); `core/sage_agent.py` scores candidates against the question client-side (`score_article_relevance`, title ×2) and renders the top `ARTICLE_TOP_K`=15 as an `=== ARTICLE EVIDENCE ===` section within a 30% slice of the char budget, unused budget flowing back to summaries. Local fallback flattens `themed_articles` from history.json. No keyword matches → newest-first, so Sage always has some Tier-1 grounding. Links carried but not rendered (Phase 2.2). Sequencing decision: the faithfulness judge below is a separate follow-up task — no pre-2.1 baseline will be recorded; judge scores start from post-2.1.)*
- [ ] **2.2** Citations: Sage prompt gains article links from `articles.link`; UI renders them (accordion/evidence popover — Pillar D).
- [ ] **2.3** Add `tsvector` column + GIN index on `articles`; switch keyword matching to proper FTS queries. *(This is the sparse half of Pillar A, landing early.)*
- **Gate**: faithfulness judge for Sage answers added to eval infra (`quality_evaluations`) — baseline scores recorded before any retrieval changes.

### Phase 3 — Embedding pipeline (dense half of Pillar A)
- [ ] **3.1** Migration: `CREATE EXTENSION vector`, `article_embeddings` table + HNSW + run_date indexes (schema above).
- [ ] **3.2** Embedding provider added to the gateway (Gemini embeddings primary; Ollama `nomic-embed-text` fallback), with dedup by article content hash and per-article `embedding_model` recorded.
- [ ] **3.3** Ingest-time embedding for **new articles only** — a step in the pipeline (bg_refresher / future scheduled job).
- [ ] **3.4** Resumable, rate-limit-aware backfill script for historical articles (run manually; writes through the same provider path so it's traced too).
- **Gate**: retrieval hit-rate eval on a small hand-labelled question set (reuse eval infra; store as scores in Langfuse).

### Phase 4 — Hybrid retrieval + temporal awareness
- [ ] **4.1** Dense top-k search replaces the keyword-overlap relevance scorer in Sage context assembly (keep keyword path as fallback while `article_embeddings` coverage is partial).
- [ ] **4.2** Sparse + dense candidates fused via Reciprocal Rank Fusion.
- [ ] **4.3** Query date parsing → temporal bounding box on retrieval (respect the HNSW+filter gotcha in Pillar C); time-decay re-ranking.
- [ ] **4.4** `theme_first_seen` maintained at ingest; render-time scanning removed.
- **Gate**: side-by-side eval old-vs-new retrieval on the labelled question set + latency budget check; decision recorded here.

### Phase 5 — Hierarchical memory & monthly rollups
- [ ] **5.1** `monthly_syntheses` table + monthly rollup job generating Tier 3 syntheses per theme (runs in the scheduled pipeline, not pg_cron — external LLM calls).
- [ ] **5.2** Tier-aware assembly: macro queries pull Tier 3 rollups; drill down to Tier 1 only for cited milestones.

### Phase 6 — Multi-agent synthesis (Pillar E; gated on Phase 0 tracing being live)
- [ ] **6.1** Sage Scout: sub-query generation + retrieval execution (traced as nested spans).
- [ ] **6.2** Sage Fact-Checker: statement-level faithfulness against retrieved context (reuses the Phase 2 judge).
- [ ] **6.3** Sage Analyst: final synthesis in AI Pulse voice; full trace visible end-to-end in Langfuse.

---

## 6. Deployment & Operations (NEW)

### Verdict on Streamlit
**Keep Streamlit as the UI.** It is the right tool for a single-user analytics dashboard — a FastAPI+React rewrite would cost weeks and buy nothing at this scale. What *should* change is the operational shape around it:

1. **Decouple ingestion from the request thread.** Today `BackgroundRefresher` runs as a thread on app load (12h cache). Target state: a **scheduled pipeline** runs ingestion/classification/summaries/embeddings daily and writes to Supabase; the app becomes read-mostly and starts instantly.
   - **Recommended (free): GitHub Actions cron** (~daily, off-peak minute) running the pipeline as a job. Secrets via GitHub Actions secrets. Free for public repos, generous free minutes for private ones.
   - pg_cron inside Supabase is *not* suitable — it can't call external LLM APIs cleanly; keep it for DB-only maintenance (rollup triggers, retention pruning) if ever needed.
   - Keep the on-load background refresh as a *fallback* for local/first-run, not the primary path.
2. **Hosting options (free-first):**

| Host | Cost | Notes |
|---|---|---|
| Local (status quo) | Free | Fine while this is a personal tool; nothing to migrate until you want always-on access |
| **Hugging Face Spaces (CPU Basic)** | Free | 2 vCPU / 16 GB RAM / 50 GB disk; sleeps after 48h idle (keep-alive via UptimeRobot ping); Streamlit SDK supported — **best free always-reachable option** once ingestion is decoupled |
| Streamlit Community Cloud | Free | Deploys from GitHub on push; viewer allow-lists for privacy; sleeps on inactivity; modest resources |
| VPS (e.g. ~$5/mo) | Paid | Only needed if self-hosting Phoenix/Langfuse or wanting zero sleep + custom domains |

3. **Supabase stays the single source of truth** (already the design intent — `app_settings` exists precisely because cloud deploys have ephemeral filesystems). Everything stateful (history, conversations, embeddings, llm_calls, rollups) lives there; the host is stateless and replaceable.

### Cost posture
- **Free stack end-to-end is viable**: Supabase free (500MB DB — embedding math in Pillar A), HF Spaces free, GitHub Actions free, Langfuse Cloud Hobby free, Gemini/Ollama-Cloud existing keys.
- **Paid trigger points** (only when hit): Supabase storage/RAM pressure (compute add-on), always-on hosting (VPS), Langfuse retention/features (Core), heavier embedding volume.
- With `llm_calls` recording every call, upgrade decisions become data-driven rather than speculative.

---

## 7. References (researched 2026-10)

- Langfuse self-hosting stack & ClickHouse acquisition: [Langfuse GitHub](https://github.com/langfuse/langfuse/), [production self-host writeups](https://cloud.tencent.com/developer/article/2698255)
- Observability comparison (Langfuse vs LangSmith vs Phoenix): [2026 platform comparison](https://zhuanlan.zhihu.com/p/2070013256492643730), [Langfuse vs LangSmith](https://zhuanlan.zhihu.com/p/1979937430023980217)
- Supabase pgvector guidance: [pgvector docs](https://supabase.com/docs/guides/database/extensions/pgvector)
- HF Spaces free tier & keep-alive: [deployment walkthrough](https://post.smzdm.com/p/avg57884/)
- AgentOps (session replay, self-host monitoring): [AgentOps monitoring comparison](https://blog.csdn.net/2501_91590464/article/details/162110801)
- Langfuse Cloud metered billing shape: [Stripe case study](https://stripe.com/en-br/customers/langfuse)
- Langfuse Cloud Hobby pricing (~50k units/mo, 30-day retention): from training knowledge — **verify at [langfuse.com/pricing](https://langfuse.com/pricing)** before committing.
