# AI Pulse — Intelligence Dashboard

An AI news intelligence dashboard that aggregates, classifies, and persists AI industry developments into a longitudinal memory system. Fetches from 30+ sources, classifies into 7 strategic themes, and produces context-aware summaries that track the **evolution** of trends across runs. Includes **Sage**, an embedded AI research analyst you can chat with to explore the archive.

## 🚀 Quick Start

```bash
pip install -r requirements.txt
# Configure secrets in .streamlit/secrets.toml (see below)
streamlit run app.py
```

The first load triggers a background ingestion. Subsequent loads restore from `history.json` and only refresh if the cache is older than 12 hours.

## ✨ Key Features

- **Multi-model AI gateway** — task-typed LLM calls routed across Qwen Cloud and Gemini with fallback chains, health tracking, context-fit checks, per-request timeouts, and a hard wall-clock deadline bounding each fallback chain. [Architecture details →](docs/ARCHITECTURE.md#routing-policies)
- **Dense article embeddings** — 768-dim vectors in Supabase pgvector with per-article model provenance. This deployment runs Cloudflare Workers AI (`@cf/baai/bge-base-en-v1.5`, full index, retrieval Recall@10 = 0.917); fresh installs default to Gemini until switched via `EMBEDDING_PROVIDER`. [Embedding providers →](docs/EMBEDDING_PROVIDERS.md)
- **Deterministic classification** — 4-pass waterfall (keywords → TF-IDF → optional LLM → heuristic). The LLM gate can be disabled entirely once keyword coverage matures. [Modes explained →](docs/ARCHITECTURE.md#classification-modes)
- **Self-improving keywords** — after each run, missing keywords are heuristically extracted from LLM/heuristic articles and auto-applied if they appear in 3+ articles. [How it works →](docs/ARCHITECTURE.md#self-improving-keywords)
- **Non-LLM fallback** — LexRank + Luhn extractive engine produces 5-section briefs in <50ms, zero tokens, 100% faithful.
- **Full-coverage processing (Phases 4.1–4.6)** — skip visibility ("N of M articles reflected" + missed/backlog lists), fair-drain aging boost for waiting articles, budget-aware capacity, map-reduce overflow summarisation (eval-gated at 74% narrative coverage), and prioritisation refinements: recency weighting, source diversity, syndicated near-duplicate demotion, classification-gate confidence.
- **Persistent memory** — every run persisted to `history.json`, `memory.md`, and optionally Supabase. The last 2 runs' summaries are injected into the next prompt so the model reports on **evolutions**, not static snapshots.
- **Duplicate-run suppression** — a stable article-set fingerprint stops the same fetched set from being persisted twice within a configurable window.
- **Processed-article ledger** — tracks which articles have actually been included in a summary prompt, so only new/unprocessed articles are summarised and the rest are carried forward to the next run.
- **Quality evaluation** — 7 automated run metrics (3 LLM-as-judge + 4 deterministic) plus a chat-time Sage faithfulness judge, with in-app and automatic remediation. [Details →](docs/QUALITY_EVALUATION.md)
- **Sage agent** — conversational AI research analyst on its own page, grounded in the Memory Wiki archive plus **hybrid article retrieval** (pgvector dense + Postgres FTS, RRF fusion, temporal bounds and freshness-aware re-ranking) and **tier-aware assembly** (monthly macro rollups for older periods). Opt-in **🔬 deep-research mode** runs a 3-agent pipeline (Scout → Fact-Checker → Analyst, one bounded revision), every stage traced as nested spans in one Langfuse trace. Clickable citations, per-reply evidence popovers, and a chat-time faithfulness judge in both modes. [Sage reference →](docs/SAGE.md)
- **Observability** — every gateway call mirrored to the durable `llm_calls` table; OpenTelemetry traces export to Langfuse Cloud with trace-id linkage.

## 🧠 How Sage's Data Works (brief)

Sage answers questions about months of AI news without reading it all at question time. A daily pipeline **fetches → classifies (7 themes) → summarises** articles into a three-tier memory, and each question triggers **hybrid retrieval → budgeted context assembly → a cited answer** that a second LLM judges for faithfulness.

```
 Tier 3 🗺️  monthly_syntheses     macro rollups: 1 per theme per month
 Tier 2 🏙️  theme_summaries       daily briefs: 1 per theme per run
 Tier 1 🚶  articles + embeddings every article + its 768-dim meaning vector
                │
   question → word search (FTS) + meaning search (pgvector)
                → rank fusion (RRF) + date bounds
                → pack the context: recent days in full, older months
                  as rollups, top articles as evidence (~40k char budget)
                → Sage answers with clickable citations → judge scores it
```

The tier-aware assembly was eval-gated: faithfulness **0.925 → 0.975** while context cost per covered month dropped **80%** and archive reach extended from ~2 to 6–7 months.

**New to AI/data engineering?** Read the diagram-heavy tour: [BLOG.md → "How Sage remembers: from news feeds to cited answers"](BLOG.md). Deeper references: [Architecture](docs/ARCHITECTURE.md) · [Supabase schema](docs/SUPABASE.md) · [Roadmap](Future.md).

## 🔮 Sage — the conversational research analyst

Sage has its own page (**Sage** 🔮 in the sidebar; Memory Wiki 🧠 keeps the archive timeline, run comparison, and monthly rollups). Two answer modes:

- **Fast (default)** — one gateway call grounded in the tier-aware context: recent days in full detail, older months as macro rollups, top articles as clickable evidence. ~10–20s.
- **🔬 Deep research (opt-in toggle)** — the Phase 7 multi-agent pipeline: a **Scout** plans 2–3 sub-queries, retrieval runs per sub-query, an **Analyst** drafts, and a **Fact-Checker** validates every statement against the context (one bounded cite-or-drop revision). Every stage is traced as nested spans in one Langfuse trace and grouped in `llm_calls` by a shared correlation id. ~30–45s.

Every reply — either mode — is graded by a chat-time faithfulness judge, and conversations persist with save/export. **The loop closes (Phase 8):** high-scoring replies become few-shot exemplars, judge-flagged claims and your 👎 corrections become negative exemplars, an opt-in grounding guard auto-tightens Sage when rolling faithfulness dips (and auto-reverts on recovery), and a standing regression gate (`--gate8`) guards every prompt/assembly change. **The focused developer reference for all Sage & Memory Wiki work is [docs/SAGE.md](docs/SAGE.md)** (architecture, pipeline stages, improvement loop, knobs, eval gates, extension guide); start there before changing anything Sage-related.

## 📦 Setup

### 1. Configure Secrets

Create `.streamlit/secrets.toml`:
```toml
[general]
QWEN_BASE_URL   = "https://token-plan.maas.qwencloudapi.com/compatible-mode/v1"
QWEN_MODEL      = "qwen3.8-flash"
QWEN_API_KEY    = "your-qwen-cloud-api-key"
GEMINI_API_KEY  = "your-gemini-api-key"
```

Embeddings route through the gateway's `EMBEDDING_PROVIDER` switch (code default `gemini` →
`gemini-embedding-001` via `GEMINI_API_KEY`). This deployment runs **Cloudflare Workers AI**;
to enable it, add to the same `[general]` block:

```toml
EMBEDDING_PROVIDER = "cloudflare"
CF_ACCOUNT_ID      = "your-cloudflare-account-id"
CF_API_TOKEN       = "your-workers-ai-api-token"
```

Provider options, free tiers, the migration/re-embed runbook, and the closed 14-day metrics
watch: [docs/EMBEDDING_PROVIDERS.md](docs/EMBEDDING_PROVIDERS.md).

### 2. Export Environment Variables (optional tuning)

The Model Gateway resolves provider keys through `config.settings` (st.secrets → secrets.toml → env vars → default), so `secrets.toml` alone is sufficient; OS env vars also work and additionally tune gateway behaviour:

```bash
export QWEN_BASE_URL="https://token-plan.maas.qwencloudapi.com/compatible-mode/v1"
export QWEN_API_KEY="your-qwen-cloud-api-key"
export GEMINI_API_KEY="your-gemini-api-key"
# Optional: tune per-provider request timeouts and health recovery
export QWEN_REQUEST_TIMEOUT="180"           # seconds; default 180 (large summary prompts can be slow)
export GEMINI_REQUEST_TIMEOUT="30"          # seconds; default 30
export GATEWAY_HEALTH_RESET_SECONDS="300"   # cooldown before an "unavailable" model is retried; default 300
```

### 3. Configure Supabase (Optional)

For cloud persistence, create `.env`:
```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-key
```

See [`SUPABASE_SETUP_GUIDE.md`](./SUPABASE_SETUP_GUIDE.md) for the full schema and migrations.

#### Supabase migration for processed-article tracking

If you are upgrading an existing Supabase project, also run:

```sql
-- Run in Supabase SQL Editor
\i supabase_migration_processed_articles.sql
```

This adds:

- `trend_runs.article_fingerprint` for duplicate-run detection
- `processed_articles` table for tracking which articles have been summarised

If your existing `articles` table contains duplicate `(content_hash, theme_name)` rows, clean them first before backfilling `content_hash`. See the migration file for the deduplication pattern.

### 4. Run

```bash
streamlit run app.py
```

## 🧭 Pages

| # | Page | Purpose |
|---|------|---------|
| — | Home | Dashboard with theme metrics and live ingestion status |
| 1 | Overview | Theme summary cards with provenance chips |
| 2 | Deep Dive | Per-theme articles, summaries, on-demand Gemini re-summarisation |
| 3 | Keyword Analysis | Keyword velocity analytics, word clouds |
| 4 | Sources | RSS feeds and web sources with article counts |
| 5 | Memory Wiki | 📖 Memory Timeline (run cards, coverage, 🗓️ monthly rollups), ⚖️ Compare Runs |
| 6 | Trend Analytics | Cross-run thematic momentum and theme drilldown |
| 7 | Quality Evaluation | 7 automated run metrics + chat-time Sage faithfulness, with in-app remediation |
| 8 | Feedback & Roadmap | Feature requests, bug reports, public roadmap |
| 9 | Eval Sets | Browsable registry of git-tracked eval question sets (`eval_sets/*.json`) with latest run scores + gate verdicts |
| 10 | Dev Blog | Searchable, label-filterable rendering of the `BLOG.md` engineering journal |
| 11 | **Sage** 🔮 | The conversational research analyst: persistent conversations, research scope, **🔬 deep-research toggle** (Scout → Fact-Checker → Analyst), pipeline-detail expander |

## 🎯 The 8 Strategic Themes

1. **Agentic Systems & DevTools** — agentic workflows, RAG, MCP, LangChain/LangGraph
2. **Frontier Models & Benchmarks** — model releases, MoE, KV cache, GPQA/SWE-bench
3. **Hardware, Compute & LLMOps** — NVIDIA, TPU, GPU, Kubernetes, edge inference
4. **Enterprise Strategy & ROI** — funding, acquisitions, IPO, enterprise revenue
5. **Governance, Safety & Policy** — EU AI Act, export controls, sovereign AI, NIST
6. **AI Security & Trust** — prompt injection, jailbreaks, guardrails, agent hijack
7. **AI-Assisted Software Engineering** — Cursor, Claude Code, Copilot, AI code review
8. **Robotics & Embodied AI** — humanoids, VLA/world models, robotaxi, sim-to-real, manipulation *(added 2026-11-28; ~105 archived articles reclassified into it)*

## 🧪 Tests

```bash
python -m pytest tests/                    # full suite: 738 tests (final check only)
python -m pytest tests/test_summariser.py  # targeted: prefer during iteration (<0.5s)
python -m pytest -m integration tests/     # opt-in: real LLM + Supabase wiring
```

During iteration, run only the 1–2 test files touching the changed code — see the
[AGENTS.md](AGENTS.md) cheat sheet. Root-level `test_*.py` scripts are manual smoke tests,
not pytest tests.

## 📁 Docs

| Document | What's Inside |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Always-loaded agent rules: testing directive, configuration gotchas, documentation routing table |
| [Architecture](docs/ARCHITECTURE.md) | Classification modes, 4-pass waterfall, system diagrams, gateway internals, routing policies, summarizer pipeline, quota, memory, caching |
| [Quality Evaluation](docs/QUALITY_EVALUATION.md) | Judge metrics table (7 run metrics + chat-time Sage faithfulness), page-7 execution model (checkpointing, judge model selector), in-app + auto remediation |
| [Sage](docs/SAGE.md) | **The focused Sage & Memory Wiki reference**: page map, fast path, deep-research pipeline stages + degradation, tracing, conversations, knobs, eval gates, extension guide |
| [Pages](docs/PAGES.md) | Per-page reference for all 11 pages and app.py |
| [Configuration](docs/CONFIGURATION.md) | Secrets/env resolution, settings constants, custom_settings/custom_keywords, gateway tunables |
| [Supabase](docs/SUPABASE.md) | Schema, migrations, RLS, app_settings KV store, test isolation |
| [Embedding Providers](docs/EMBEDDING_PROVIDERS.md) | Live Cloudflare Workers AI pipeline, provider options (Gemini code default, Jina, self-deployed Render), adapter/re-embed runbook, closed metrics-watch results |
| [Future.md](Future.md) | Standing Sage RAG roadmap: hybrid retrieval, temporal awareness, hierarchical memory, multi-agent synthesis, improvement loop |
| [Roadmap History](docs/ROADMAP_HISTORY.md) | Implementation notes for every completed phase (0–7) |
| [BLOG.md](BLOG.md) | Engineering lessons learned, dated entries grouped by 2-week blocks |

## 📈 Performance & Monitoring

- **Logs** — `logs/app.log` (console + file handler)
- **Cache** — `st.cache_data` with 12h TTL + `.cache/*.json` disk persistence
- **Content hashing** — SHA-256 skips redundant LLM calls when articles are unchanged
- **Cloud sync** — Supabase sync status shown in sidebar; first load backfills `history.json` → Supabase

---

*Built for AI Engineering & Product Managers to track the high-signal frontier of the industry.*
