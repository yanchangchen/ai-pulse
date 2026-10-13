# Roadmap History — Completed Phases & Decisions (Sage RAG)

Implementation record for work that has already landed, moved out of `Future.md` to keep the
roadmap lean. Entries are preserved verbatim from the roadmap at completion time — they record
*what shipped, how, and the gotchas discovered along the way*, so future phases can build on
them without re-deriving. Pending work lives in [Future.md](../Future.md).

---

## Phase 1 — Sage conversational foundations (Completed)

- [x] **Phase 1 (Completed)**: Fix query order, period selector (Last 7d, 30d, Sep 2026, All Time, Custom), theme filtering in chat, conversation persistence (JSON/Supabase sync), Markdown export.

---

## Phase 0 — Foundations: routing + observability

*(0.1–0.3 done and gate verified 2026-10; only 0.4 — Docker packaging — remains, tracked in
Future.md.)*

- [x] **0.1** Route Sage through `core/ai_gateway` (new `sage_chat` task policy): gains fallback chain, health tracking, provenance chips for free. *(Done 2026-10: `SAGE_CHAT` TaskType, Ollama-primary policy with Gemini fallbacks, `chat_with_sage` rewritten to gateway contract returning `{"response", "provenance"}`, provenance caption in the UI.)*
- [x] **0.2** Extend `Provenance` to carry token usage; add `llm_calls` table + gateway hook so every call (Sage, summariser, classifier, evaluator) is persisted with tokens/latency/cost_class. *(Done 2026-10: `Provenance.tokens_in/out` populated from provider usage; `core/ai_gateway/call_log.py` fire-and-forget writer; `insert_llm_call` on the Supabase manager; `supabase_migration_llm_calls.sql` — note `correlation_id` is VARCHAR(40), not UUID, matching the 8-char request ids.)*
- [x] **0.3** Langfuse Cloud Hobby account + OTel span export from the gateway (env-gated no-op when keys absent). Link `trace_id` into `llm_calls`. *(Done 2026-10: `core/ai_gateway/tracing.py` — one span per gateway execution, OTLP HTTP export to `{LANGFUSE_HOST}/api/public/otel/v1/traces` with Basic-auth keys, `gen_ai.*`/`langfuse.observation.*` attributes so LLM calls render as generations, input/output capped at ~2000 chars. `Provenance.trace_id` (32-char hex) flows into the `llm_calls` row. Keys resolve via the `config.settings` chain (`LANGFUSE_PUBLIC_KEY`/`SECRET_KEY`/`HOST`); conftest autouse fixture pins tracing off in tests. Deps: `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`.)*
- **Gate**: a Sage chat produces a visible trace in Langfuse and a row in `llm_calls`. *(Verified 2026-10 with a real US-region Langfuse Hobby project: one live `sage_chat` gateway call exported a span with no OTLP errors and the newest `llm_calls` row carried the matching 32-hex `trace_id`. Project is US-region — `LANGFUSE_HOST = https://us.cloud.langfuse.com` in secrets.toml.)*

---

## Phase 2 — Article-level grounding (sparse retrieval only, no vectors yet) — CLOSED

- [x] **2.1** Add `articles` retrieval to Sage context assembly (SQL: recent + keyword match within budget), alongside theme summaries. *(Done 2026-10: `SupabaseManager.get_articles_for_context()` fetches a bounded newest-first candidate window (theme/source/date filters, no SQL keyword matching — that's Phase 2.3 FTS); `core/sage_agent.py` scores candidates against the question client-side (`score_article_relevance`, title ×2) and renders the top `ARTICLE_TOP_K`=15 as an `=== ARTICLE EVIDENCE ===` section within a 30% slice of the char budget, unused budget flowing back to summaries. Local fallback flattens `themed_articles` from history.json. No keyword matches → newest-first, so Sage always has some Tier-1 grounding. Links carried but not rendered (Phase 2.2). Sequencing decision: the faithfulness judge below is a separate follow-up task — no pre-2.1 baseline will be recorded; judge scores start from post-2.1.)*
- [x] **2.2** Citations: Sage prompt gains article links from `articles.link`; UI renders them (accordion/evidence popover — Pillar D). *(Done 2026-10: `_render_article_section` appends a `Link:` line to each brief and returns the rendered-article list (title/source/date/link/snippet) as `build_wiki_context()["articles"]`; system prompt instructs Sage to attach context links as Markdown hyperlinks and never invent URLs. Links sanitised to http(s) only (`_safe_link`) since scraped URLs are untrusted. `pages/5_History.py` renders a `📰 Article Evidence (N)` accordion under each assistant message — the list is persisted as an `articles` key on the message dict, so popovers survive conversation reload (JSONB-friendly extra key; Supabase + local JSON paths both pass it through).*
- [x] **2.3** Add `tsvector` column + GIN index on `articles`; switch keyword matching to proper FTS queries. *(Done 2026-10: `supabase_migration_fts_articles.sql` adds a `search_vector` GENERATED ALWAYS tsvector (title weight A, summary weight B — title×2 semantics in SQL) with a GIN index and an invoker-rights `match_articles(query, filters, result_limit)` RPC ranked by `ts_rank` (PostgREST's built-in fts filter can't ORDER BY `ts_rank`, so a function is the standard pattern; no backfill needed — STORED generation populates existing rows). `get_articles_for_context(query=...)` routes to the RPC when a question is supplied, degrading to the existing newest-first window on RPC failure or empty match — pre-migration databases keep working. `build_wiki_context` passes the raw question (websearch_to_tsquery does its own parsing); `_select_articles` client-side scoring stays the final provider-agnostic ranking step, and the local history.json fallback (no Postgres → no FTS) is unchanged. Riding along: `SAGE_SYSTEM_PROMPT` reworked to answer-first format — Direct Answer → Evidence (chronological for trend questions) → Assessment — with new grounding rules for partial-evidence scope, conflicting-evidence surfacing, and concrete numbers over vague characterisations; all 2.1/2.2 groundedness invariants kept. Sparse half of Pillar A.)*
- **Gate**: faithfulness judge for Sage answers added to eval infra — baseline scores recorded. *(Done 2026-10: `record_sage_faithfulness()` in `core/evaluator.py` judges each successful Sage reply chat-time, fire-and-forget on a daemon thread (the `llm_calls` pattern — the context string and the answer coexist only at the moment of reply, so judging later would rebuild a drifted context). One `TaskType.EVALUATE` call via `GatewayJudgeLLM` scores the answer against the exact wiki/article context it was given; only successful judgments insert a `sage_evaluations` row (`supabase_migration_sage_evaluations.sql` — per-reply shape, not `quality_evaluations`, which is run-scoped with NOT NULL summary columns), so judge outages skip rows rather than masquerade as hallucination. Page 7 renders the rolling mean, per-reply history, and recent unsupported claims. Baseline caveat, per the recorded sequencing decision: no pre-2.1 baseline exists — scores start from post-2.3, and Phase 3 changes are measured against that batch. **Phase 2 closed.**)*

---

## Decision: tracing backend — Langfuse Cloud (Hobby) *(2026-10)*

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
