# Supabase Reference (Developer)

Cloud persistence for cross-device access; auto-backfilled from `history.json` on first
load; degrades gracefully (`is_available()` returns `False` without env vars).

Human-facing setup guides: [SUPABASE_SETUP_GUIDE.md](../SUPABASE_SETUP_GUIDE.md),
[SUPABASE_INTEGRATION_README.md](../SUPABASE_INTEGRATION_README.md).

## Core Schema

Core 4 tables in `supabase_schema.sql`:

- `trend_runs`
- `theme_summaries`
- `articles` — deduplicated by `(content_hash, theme_name)`; carries `times_seen`
  (Phase 4.3 fair-drain counter: runs classified-but-not-summarised, 0 once included)
- `sync_metadata`

## Migrations

Run in the SQL Editor, roughly in order:

1. `supabase_migration_dedup.sql`
2. `supabase_migration_keywords.sql` — `keyword_suggestions`
3. `supabase_migration_quality_metrics.sql` — 4 deterministic-judge score columns on `quality_evaluations`
4. `supabase_migration_provenance.sql` — `generation_source` / `generation_log`
5. `supabase_migration_user_feedback.sql` — page 8 feedback
6. `supabase_migration_backfill_articles.sql`
7. `supabase_migration_app_settings.sql` — `app_settings` KV store for evaluation-driven config
8. `supabase_migration_llm_calls.sql` — `llm_calls` append-only observability log
9. `supabase_migration_fts_articles.sql` — `articles.search_vector` (generated tsvector + GIN) and the `match_articles` FTS RPC (Phase 2.3)
10. `supabase_migration_sage_evaluations.sql` — `sage_evaluations`, the chat-time Sage faithfulness judge's score store (Phase 2 gate)
11. `supabase_migration_article_embeddings.sql` — `article_embeddings` (pgvector) + `get_unembedded_articles` / `match_article_embeddings` RPCs (Phase 3). Enables `CREATE EXTENSION vector`.
12. `supabase_migration_times_seen.sql` — `articles.times_seen` + partial index + `record_article_seen(theme, waiting[], included[])` UPDATE-only RPC (Phase 4.3 fair drain). UPDATE-only on purpose: PostgREST upserts cannot express per-row increments, and a partial-column upsert would insert title-less article rows. Pre-migration databases degrade to the local JSON counter (`data/times_seen.json`).
13. `supabase_migration_hybrid_retrieval.sql` — `match_article_embeddings` v2 (article metadata join + date/theme bounds applied after a 4x over-fetched ANN scan) and `theme_first_seen` (Phase 5.4, backfilled from the archive).
14. `supabase_migration_monthly_syntheses.sql` — `monthly_syntheses` (Phase 6.1 Tier-3 memory): one LLM macro synthesis per theme per month, `UNIQUE(year_month, theme_name)`. No inline backfill — rollups need LLM calls, so backfill via `python backfill_monthly_syntheses.py`.
15. `supabase_migration_sage_evaluations_mode.sql` — `sage_evaluations.answer_mode` (`fast` | `deep`, NULL = legacy): separates the Phase 7 deep-research replies from fast-path replies in the rolling faithfulness baseline. The insert path retries without the column pre-migration, so judging never breaks while pending.

## `llm_calls`

Append-only Model Gateway call log (see `core/ai_gateway/call_log.py`): every completed
gateway execution — LLM success, deterministic fallback, and total failure — writes one
row with task type, provider, model, provider-reported tokens in/out, latency, cost
class, correlation id, trace id, and error text. Writes are fire-and-forget daemon
threads gated on `is_available()`, so the log never blocks or breaks an LLM task.
`trace_id` (32-char OTel hex) links each row back to its Langfuse trace when gateway
tracing is enabled (`core/ai_gateway/tracing.py`, Phase 0.3).

## Sage article retrieval

`SupabaseManager.get_articles_for_context()` (`core/supabase_client.py`) is Sage's
Tier-1 evidence query. Phase 2.1 was a bounded newest-first window on `articles`
with period/theme/source filters and client-side keyword scoring. Phase 2.3 moved
keyword matching into Postgres FTS: with a `query` (the raw Sage question), the
method calls the `match_articles` RPC, which matches `articles.search_vector`
(a GENERATED ALWAYS tsvector, title weight A / summary weight B) via
`websearch_to_tsquery` and returns rows ranked by `ts_rank`. On RPC failure or
no keyword match it degrades to the original newest-first window, so
pre-migration databases and no-match questions keep working. Final top-K
selection stays client-side in `core/sage_agent.py`.

## `sage_evaluations`

One row per successfully judged Sage reply (Phase 2 gate): `core.evaluator.record_sage_faithfulness()`
runs the judge fire-and-forget on a daemon thread right after each grounded reply in the chat tab
(the context and the answer coexist only then), and `SupabaseManager.insert_sage_evaluation()`
persists the score, unsupported claims, and the judge/answer models. The Quality Evaluation page
reads it back with `get_sage_evaluations()`. Judge failures skip the row — never a zero score —
so a judge outage can't masquerade as hallucination in the trend chart.

## `article_embeddings`

One 768-dim dense vector per article (Phase 3, the storage half of Pillar A). The
ingest step (`core/embedding.py::embed_unembedded_articles`, wired into
`core/bg_refresher.py` after persistence) and the manual `backfill_embeddings.py` both
drain the `get_unembedded_articles` RPC — a LEFT JOIN to `article_embeddings` that
returns only articles **without** an embedding, so a re-run resumes wherever the
previous run stopped (the table is the checkpoint, not a cursor file). `chunk_text`
records the exact `"title summary"` string that was embedded; `embedding_model` records
which model produced it. `embedding vector(768)` + HNSW (`vector_cosine_ops`) + a
`run_date` btree index (denormalized from `trend_runs`). `match_article_embeddings` (v2, Phase 5) is the
dense leg of the hybrid retriever (`core/retrieval.py`) and the Phase 3 gate:
it joins article metadata (title/summary/theme/source/link/published_at/run_date)
and applies optional date/theme bounds after a 4x over-fetched ANN scan
(HNSW+filter under-fill gotcha). RLS mirrors
`sage_evaluations`: public SELECT + insert. Embeddings go through the gateway
(`TaskType.EMBEDDING`, `gemini-embedding-001`), so each batch is traced and mirrored to
`llm_calls` with `task_type='embedding'`.

**Dimension and model-space contract.** `embedding vector(768)` and the
`match_article_embeddings(query_embedding vector(768), …)` signature are fixed together. A
replacement embedding model must emit 768 dims, or the column type, RPC signature, HNSW
index, and every stored row must change in one migration. Vectors from *different* models
are not comparable even at equal width, so a provider swap requires re-embedding the whole
table; `embedding_model` is the audit column that makes a partial migration visible.
`insert_article_embeddings()` is append-only (a duplicate `article_id` is logged, not
upserted), so re-embedding needs a delete-or-upsert path first. Provider options and the
re-embed runbook: [docs/EMBEDDING_PROVIDERS.md](EMBEDDING_PROVIDERS.md).

## Hybrid retrieval & `theme_first_seen` (Phase 5)

`core/retrieval.py::hybrid_search()` fuses the `match_articles` FTS leg with
the `match_article_embeddings` dense leg via Reciprocal Rank Fusion, parses
temporal bounds from the question, and applies bounded time-decay only to
unbounded queries. See [docs/ARCHITECTURE.md](ARCHITECTURE.md#hybrid-retrieval-phase-5--coreretrievalpy).

`theme_first_seen` (one row per theme: `first_seen_date`, `first_seen_run_id`,
`evidence_summary`) is maintained at ingest by `record_theme_first_seen()`
(upsert with ignore-duplicates => earliest run wins) and read whole by
`get_theme_first_seen()` for Sage's first-appearance annotation. RLS: public
read + insert (single-user app). Pre-migration databases degrade to the
legacy render-time window scan.

## `monthly_syntheses` (Phase 6.1 — Tier-3 memory)

One row per `(year_month, theme_name)` (composite UNIQUE — the Future.md draft's
single-column UNIQUE was a bug): `macro_summary` prose, `key_milestones` JSONB
(`[{date, milestone, source_link?}]`, links copied verbatim from the briefs'
further-reading URLs), `source_run_count` (briefs fed), `generation_source`
(provider:model audit), `created_at` / `updated_at`.

Write path: `core/monthly_synthesis.py::synthesize_due_monthly()` — shared by the
best-effort post-save hook in `core/bg_refresher.py` (runs even on no-new-article
runs so month rollover isn't skipped) and the `backfill_monthly_syntheses.py` CLI
(the future scheduled-pipeline entry point). Cadence: completed months synthesized
once; the current month re-synthesized every `monthly_refresh_days` (default 14);
a month whose row was written mid-month regenerates once after rollover.
`upsert_monthly_synthesis()` stamps `updated_at` explicitly on every upsert
(DEFAULT NOW() only fires on INSERT; the staleness rules read it). LLM calls route
through `TaskType.SYNTHESISE` with `allow_deterministic_fallback=False` — a failed
call writes no row and is retried next run.

Read path: `get_monthly_syntheses()` (due-check + History-page expander);
`get_theme_briefs_for_month()` bounds on `trend_runs.run_date` (not
`run_timestamp`) and retries without optional columns on schema drift.

RLS: public read + insert + update (FOR ALL, like `app_settings` — rows are
upserted on refresh). Degradation: offline / pre-migration the rollup job skips
silently and the expander renders nothing; Supabase is the single source of
truth for rollups (no local fallback — `history.json` keeps only 5 runs).
Knobs: `get_monthly_synthesis_settings()` in `config/settings.py`.

## RLS

RLS is enabled with read-only public access (`app_settings` / `custom_keywords` /
`monthly_syntheses` allow public write — single-user app).

## `app_settings` KV store

`core/supabase_client.py` `get_app_setting()` / `upsert_app_setting()` are the generic
accessors; the table comes from `supabase_migration_app_settings.sql` (already applied to
the live project; sync degrades to local-only when missing). Used for the
`custom_settings` overlay and auto-remediation state persistence — see
[docs/CONFIGURATION.md](CONFIGURATION.md#summariser-tuner-settings--configcustom_settingsjson).

## Test isolation

`tests/conftest.py` pins the Supabase manager singleton to an offline stub
(`is_available() == False`) so tests never touch the production project. This matters on
machines with `.streamlit/secrets.toml`: importing `config.settings` makes Streamlit
export all secrets (including `SUPABASE_URL` / `SUPABASE_KEY`) into `os.environ`, which
`core/supabase_client.py` reads directly. Tests that exercise Supabase-backed behaviour
must patch `core.supabase_client.get_supabase_manager` with their own mock (pattern:
`test_processed_articles.py::test_supabase_preferred`).
