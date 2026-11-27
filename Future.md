# Sage RAG Architecture & Long-Term Roadmap

This document outlines the architectural roadmap for evolving **Sage** from its current character-budgeted prompt-injection mechanism into a production-grade, multi-stage **Retrieval-Augmented Generation (RAG)** intelligence system.

> **Structure (2026-10):** completed phases, their implementation notes, and past decisions live in [docs/ROADMAP_HISTORY.md](docs/ROADMAP_HISTORY.md). This file tracks what's ahead and why — keep it lean; record completion details in the history file as work lands.

---

## 1. Executive Summary & Vision

Sage is designed to be the conversational analyst embedded in AI Pulse, answering questions about long-term AI trends, company moves, architectural shifts, and chronology.

As the memory archive expands across months and years of daily news runs, traditional context-window stuffing degrades due to:
1. **Context Window Saturation**: Even large-context LLMs suffer from "lost in the middle" degradation when processing 50+ runs.
2. **Coarse-Grained Retrieval**: Answering granular queries (e.g., *"What did Simon Willison say about GPT-6 Astra adapters?"*) requires article-level citations rather than high-level run summaries.
3. **Temporal Incoherence**: Standard semantic vector search ignores publication chronology, confusing the order in which breakthroughs occurred.

The long-term vision transforms Sage into a **Time-Aware, Multi-Stage Hierarchical RAG Agent** — with two enabling layers:
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
                └────────────────────┬────────────────────┘
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

---

## 3. Key Architectural Pillars

### Pillar A: Two-Stage Hybrid Retrieval (Dense + Sparse)
* **Dense Retrieval**:
  - Vector embeddings generated through the Model Gateway's `TaskType.EMBEDDING` route. Today that is Google Gemini `gemini-embedding-001` at 768 dims. Free hosted open-model replacements (Cloudflare Workers AI `bge-base-en-v1.5`, Jina `jina-embeddings-v5-omni-nano`) and a self-deployed Render embedding service are evaluated in [docs/EMBEDDING_PROVIDERS.md](docs/EMBEDDING_PROVIDERS.md). *Verify current model names/limits at implementation time — both Google's and Jina's lineups rotate.*
  - Stored in Supabase using the `pgvector` extension with HNSW indexes.
  - Generates semantic matches for conceptual questions (*"reasoning token overhead"*, *"chip export restrictions"*).
* **Sparse Retrieval**:
  - PostgreSQL full-text search (`tsvector` and `tsquery`) with English dictionary stemmer. *(The sparse half is live — `match_articles` FTS RPC from the closed Phase 2.)*
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
   - In the Streamlit UI, cited runs and articles should be inspectable in an accordion or side-drawer showing the exact article snippet that grounded each claim. *(Both landed with the closed Phase 2.)*

### Pillar E: Multi-Agent Synthesis Workflow

For complex research questions, expand Sage from a single LLM call into a 3-agent pipeline:
1. **Sage Scout (Retriever Agent)**: Analyzes the question, generates 2–3 sub-queries, executes vector + date queries, and compiles the relevant context documents.
2. **Sage Fact-Checker (Faithfulness Evaluator)**: Validates that each statement in the proposed response is explicitly supported by retrieved context.
3. **Sage Analyst (Synthesis Agent)**: Drafts the final prose using the strict AI Pulse voice (Factual chronological account + "My read on this").

**Gate:** this phase lands *last* and only after Pillar F — a 3-agent pipeline without per-agent traces is undebuggable by construction.

### Pillar F: Observability & Evaluation

Agent traces are the troubleshooting backbone for everything above.

> **Decision (2026-10): Langfuse Cloud (Hobby, US region)** — chosen free-first; instrumentation is OpenTelemetry-based, so switching backends later means changing the exporter, not the code. The alternatives comparison (AgentOps, Phoenix, self-hosted Langfuse, LangSmith, Helicone) is archived in [docs/ROADMAP_HISTORY.md](docs/ROADMAP_HISTORY.md).

**Instrumentation status:** the Phase 0 items are live — Sage routes through `core/ai_gateway` (provenance on every call), token usage is carried and mirrored into the durable `llm_calls` table, and OTel spans export to Langfuse with `trace_id` linkage. Sage-answer faithfulness judging landed with Phase 2. **Remaining:** input-side coverage tracking (Phase 4.1) — the coverage judge returns aggregate recall only; extend it to name missed articles and pair it with a per-run skipped-articles record so summarisation gaps are observable rather than silent.

---

## 4. Supabase Database Schema Additions for Future RAG

> `articles`, `theme_summaries`, and `sage_conversations` **already exist** (see `supabase_schema.sql` + `supabase_migration_sage_conversations.sql` — note the real column is `period_label`, not `period_info`). Also already live from completed phases: `llm_calls` (`supabase_migration_llm_calls.sql`), the `search_vector` FTS column + `match_articles` RPC (`supabase_migration_fts_articles.sql`), and `sage_evaluations` (`supabase_migration_sage_evaluations.sql`). The genuinely new tables are `article_embeddings`, `monthly_syntheses`, and `theme_first_seen`.

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

-- llm_calls is already deployed (supabase_migration_llm_calls.sql; correlation_id
-- is VARCHAR(40), not UUID, matching the 8-char gateway request ids).
```

---

## 5. Phased Implementation Roadmap

Phases are deliberately small — each sub-step is independently shippable and verifiable, and most have an explicit evaluation gate before moving on.

**Where we are (2026-11):** Phase 0 foundations are live — Sage is routed through the gateway (fallback + provenance), every LLM call is mirrored to `llm_calls`, and OTel traces land in Langfuse Cloud; the gate was verified end-to-end with a live trace + matching `llm_calls` row. Phase 2 (article-level grounding) is **closed**: Sage answers are grounded in retrieved `articles` via Postgres FTS, citations render as clickable evidence popovers, and a chat-time faithfulness judge writes `sage_evaluations` rows (no pre-2.1 baseline exists — Phase 3 changes are measured against the post-2.3 batch). **Embedding provider decision (2026-11-09):** replace Gemini with **Cloudflare Workers AI** (Option A); keep **Render self-hosted BGE** (Option C, same model/vector space) and **Jina** (Option B, different space) as documented fallbacks. **Status (2026-11-10):** migration complete — the live index is 6,304/6,304 articles, 100% `@cf/baai/bge-base-en-v1.5`, backlog 0; retrieval gate Recall@10 = 0.833. **Watch closed (2026-11-27):** the 3.6 14-day metrics watch passed every gate (no Option C trigger); the retrieval gate was re-run on a new curated 24-question set (`eval_sets/retrieval_hit_rate.json`, git-tracked, rendered on the Eval Sets page) — **Recall@10 = 0.917 is now the Phase 5 baseline**. **Phases 4.3–4.5 landed 2026-11-27** (fair drain; budget-aware capacity; map-reduce overflow — coverage threshold cleared, 6.1 gate satisfied); next: **4.6 (optional)** then **Phase 5**. Rationale and the contingent thresholds for building Option C live in Phase 3.6; implementation detail is in [docs/EMBEDDING_PROVIDERS.md](docs/EMBEDDING_PROVIDERS.md). Direction from here: **embeddings (Phase 3) → full-coverage processing (Phase 4) → hybrid retrieval + temporal awareness (Phase 5) → hierarchical memory (Phase 6) → multi-agent synthesis (Phase 7) → closed-loop agent improvement (Phase 8)**. Full implementation notes for everything landed so far: [docs/ROADMAP_HISTORY.md](docs/ROADMAP_HISTORY.md).

### Phase 0 — Foundations *(0.1–0.3 done, gate verified — one item remains)*
- [ ] **0.4** (optional but cheap) `Dockerfile` + `docker-compose.yml` (app + nothing else — Supabase stays cloud) for reproducible deploys.

### Phase 3 — Embedding pipeline (dense half of Pillar A) — COMPLETE

*3.1–3.5 closed 2026-11-10. Final state: **6,304 / 6,304 articles embedded** (backlog 0), **100% `@cf/baai/bge-base-en-v1.5`**, 768 dims; retrieval gate **Recall@10 = 0.833** (the recorded Gemini baseline was 0.510, but covered only ~100 rows — treat the comparison as directional, not controlled). 3.6 watch closed healthy 2026-11-27; re-gate on the new curated 24-question set: **Recall@10 = 0.917**. This deployment runs `EMBEDDING_PROVIDER=cloudflare`; the code default stays `gemini` for fresh installs. Provider decision, migration gotchas, and implementation notes: [docs/ROADMAP_HISTORY.md](docs/ROADMAP_HISTORY.md#phase-3--embedding-pipeline-completed-2026-11-10).*

- [x] **3.1** Storage: `CREATE EXTENSION vector`, `article_embeddings` + HNSW + run_date indexes.
- [x] **3.2** Gateway embedding provider with per-article `embedding_model` provenance.
- [x] **3.3** Ingest-time embedding for new articles.
- [x] **3.4** Resumable, DB-as-cursor backfill (`backfill_embeddings.py`).
- [x] **3.5** Cloudflare Workers AI migration: adapter, explicit `EMBEDDING_PROVIDER` switch, idempotent upsert, forced re-embed (`--reembed`), context-fit bypass; live index uniform.
- [x] **3.6 (contingent) — 14-day metrics watch: CLOSED 2026-11-27, all gates healthy — no Option C trigger.** Steady-state (Nov 11–26): ≤13.2k embedding tokens/day (~1% of estimated free capacity), 28/28 calls succeeded, p95 ≤1.8 s, backlog 0 (7,006/7,006 embedded), 100% `@cf/baai/bge-base-en-v1.5` at 768 dims. Retrieval re-gate on the new curated 24-question set: **Recall@10 = 0.917**. Threshold definitions, full results, and the Option C/B decision order: [docs/ROADMAP_HISTORY.md](docs/ROADMAP_HISTORY.md#phase-36--metrics-watch-closed-2026-11-27); tracking queries stay in [docs/EMBEDDING_PROVIDERS.md](docs/EMBEDDING_PROVIDERS.md) for ad-hoc re-checks.
- **Gate**: retrieval hit-rate eval on a hand-labelled question set (reuse eval infra; snapshot in `app_settings.retrieval_eval_latest`, rendered on the Eval Sets page). *(Passed 2026-11-10: Recall@10 = 0.833 on the 8-question seed — since lost with gitignored `data/`. Re-gated 2026-11-27: **Recall@10 = 0.917** on the curated 24-question `eval_sets/retrieval_hit_rate.json`, now the Phase 5 baseline; the two numbers are not directly comparable.)*

### Phase 4 — Full-coverage article processing (input-side)
*Landed between the embedding pipeline and retrieval work deliberately: the retrieval gates below (hit-rate evals, old-vs-new side-by-side) are confounded if theme-summary coverage is silently drifting, and Tier 2 gaps must be closed before monthly rollups bake them into Tier 3. Situational facts (re-verified 2026-11-27): per theme the summariser feeds only the unprocessed articles ranked by theme-keyword relevance plus the 4.3 aging boost, hard-capped at `MAX_ARTICLES_PER_SUMMARY = 12` (`config/settings.py`), then trimmed by `split_articles_for_prompt` to the char budget `QWEN_NUM_CTX × CHARS_PER_TOKEN × INPUT_BUDGET_FRACTION` = 1M × 3 × 0.6 = **1.8M chars** on the Qwen Cloud window (the ~59k figure recorded 2026-10 predates the migration). 12 blocks ≈ 6k chars ≈ 0.3% of that budget — the flat cap, not the context window, is the sole capacity constraint. Skipped articles are no longer silent: 4.1/4.3 record skipped, waiting, and aged-out sets; what remains is the drain *rate*, which 4.4 raises.*

- [x] **4.1** Skip visibility: persist the skipped complement (classified-but-not-summarised per theme — the summariser already computes `included_hashes`; persist counts + hashes for the rest) into run metadata / `history.json` / Supabase, and surface it — "12 of 37 articles reflected" caption, missed-article list in History run detail, and the Coverage judge recording *which* articles were missed instead of aggregate-only counts. No dependencies; shippable immediately.
- [x] **4.2** Fix the fallback ledger asymmetry: on gateway fallback the non-LLM extractive path marks the whole selected pool processed while surfacing ~3 articles by name — mark only actually-surfaced ones so 4.1's numbers are truthful.
- [x] **4.3** Fair drain / backlog carryover *(landed 2026-11-27)*: aging boost (`aging_boost_per_run`, default 2.0, via `get_summariser_settings()`) × `articles.times_seen` in `_rank_articles_by_relevance`; counter maintained by the `record_article_seen` UPDATE-only RPC (`supabase_migration_times_seen.sql`) with local JSON fallback; per-run `waiting_hashes` on summaries; `history_manager._record_aged_out()` records window-departed waiting articles as `aged_out_articles` (History 🍂 expander). Migration shipped separately from 5.4's `theme_first_seen` (4.3 needed the counter now; 5.4 adds its own migration). Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) fair-drain bullet + [docs/ROADMAP_HISTORY.md](docs/ROADMAP_HISTORY.md).
- [x] **4.4** Raise effective capacity *(landed 2026-11-27, eval-gated)*: flat 12 replaced by `max_articles_per_summary` ceiling (default 50, `get_summariser_settings()`) + existing char-budget fill; `article_summary_chars` knob (default 300; 500 tested, +1.6pp coverage — within noise, not adopted). **Gate (controlled same-pool experiment, `eval_capacity_gate.py`):** true narrative-only coverage **38.4% → 54.9%** (+16.5pp), processed fraction 22% → 81%, input tokens ~1.7×. The 70% absolute threshold stays open for 4.5; per-fed-article naming rate drops 93% → 61% (synthesis compression) — 4.5's per-batch briefs are the designed remedy. Gate experiment initially mis-measured (judge fed the `skipped_articles` payload); `coverage_judge` now joins narrative sections only (+ regression tests). Details: [docs/ROADMAP_HISTORY.md](docs/ROADMAP_HISTORY.md).
- [x] **4.5** Chunked overflow summarisation ("eventually ALL") *(landed 2026-11-27, eval-gated)*: `generate_theme_summary_mapreduce` — MAP batches of `mapreduce_batch_size` (12) through the normal gateway brief (semaphore 3), REDUCE merge under a preserve-every-name mandate, deterministic merge fallback (`extractive:mapreduce_merge`); included = union of batch-fed hashes; cost bounded by `mapreduce_max_batches` (8); reduce output budget scales with batch count. **Gate (arm D, same pool as 4.4):** coverage **74.1%** (≥70% threshold cleared), processed **251/274 (92%)**, missed 66 (vs 115 cap-50 / 157 legacy), naming ≈75% (vs 61%); cost 66.6k in / 34.1k out tokens, 26 calls, 0 failures. **6.1 gate satisfied.** Details: [docs/ROADMAP_HISTORY.md](docs/ROADMAP_HISTORY.md).
- [ ] **4.6** (optional) Prioritisation refinements: recency weighting and tie-breaks, source diversity (pattern exists in `core/non_llm_summariser.py`), near-duplicate clustering for syndicated volume (relevant with high-cadence sources like MarkTechPost), classification-gate confidence as a signal.
- **Gate**: rolling Coverage-judge score ≥ threshold with a shrinking named-missed list; aged-out articles exist only as explicit records; `llm_calls` cost delta within budget.

### Phase 5 — Hybrid retrieval + temporal awareness
- [ ] **5.1** Dense top-k search replaces the keyword-overlap relevance scorer in Sage context assembly (keep keyword path as fallback while `article_embeddings` coverage is partial).
- [ ] **5.2** Sparse + dense candidates fused via Reciprocal Rank Fusion.
- [ ] **5.3** Query date parsing → temporal bounding box on retrieval (respect the HNSW+filter gotcha in Pillar C); time-decay re-ranking.
- [ ] **5.4** `theme_first_seen` maintained at ingest; render-time scanning removed. *(Shares the ingest-metadata migration batch with 4.3's times-seen counter.)*
- **Gate**: side-by-side eval old-vs-new retrieval on the labelled question set + latency budget check; decision recorded here.

### Phase 6 — Hierarchical memory & monthly rollups
- [ ] **6.1** `monthly_syntheses` table + monthly rollup job generating Tier 3 syntheses per theme (runs in the scheduled pipeline, not pg_cron — external LLM calls). *(Gated on 4.5 chunked summarisation — rolling up incomplete Tier 2 summaries bakes the gaps into Tier 3 permanently.)*
- [ ] **6.2** Tier-aware assembly: macro queries pull Tier 3 rollups; drill down to Tier 1 only for cited milestones.

### Phase 7 — Multi-agent synthesis (Pillar E; gated on Phase 0 tracing being live)
- [ ] **7.1** Sage Scout: sub-query generation + retrieval execution (traced as nested spans).
- [ ] **7.2** Sage Fact-Checker: statement-level faithfulness against retrieved context (reuses the Phase 2 judge).
- [ ] **7.3** Sage Analyst: final synthesis in AI Pulse voice; full trace visible end-to-end in Langfuse.

### Phase 8 — Sage improvement loop (evaluate → *improve* the agent)

Phase 0 and Phase 2 measure Sage — traces, and a per-reply faithfulness score with named `unsupported_claims`. Nothing yet feeds that signal back into the agent that produces the answers, so evaluation is currently descriptive, not corrective. This phase closes the loop, giving Sage the same "evaluate → remediate → re-measure" cycle the summariser already has via `core/auto_remediation.py`.

- [ ] **8.1** Eval-driven exemplar memory: persist high-scoring Sage answers (and their context) as positive few-shot exemplars, and `sage_evaluations.unsupported_claims` as negative exemplars, injected into the Sage prompt's few-shot preamble — size- and recency-bounded (pattern: `core/summariser.py`'s prior-run context).
- [ ] **8.2** Grounding guard: when rolling Sage faithfulness drops below its threshold, tighten Sage's grounding mode (strict cite-or-drop, temperature floor) — the agent-side counterpart to the summariser's strict anti-hallucination remediation; reversible and bounded, state held in `app_settings`.
- [ ] **8.3** Human-feedback signal: add a rating/correction affordance on Sage replies and feed it into the same improvement corpus (the Feedback page 8 already surfaces general feedback), so the loop learns from people as well as the judge.
- [ ] **8.4** Regression gate: any change to Sage context assembly or prompts is gated on the accumulated `sage_evaluations` baseline before shipping — no silent eval regression.
- **Gate**: rolling Sage faithfulness ≥ Phase-2 baseline with the named unsupported-claims list shrinking; every loop action recorded in `llm_calls` / `app_settings` for audit.

---

## 6. Deployment & Operations

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
- **Free stack end-to-end is viable**: Supabase free (500MB DB — embedding math in Pillar A), HF Spaces free, GitHub Actions free, Langfuse Cloud Hobby free, Gemini/Qwen-Cloud existing keys.
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
