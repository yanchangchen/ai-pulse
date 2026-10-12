# Supabase Reference (Developer)

Cloud persistence for cross-device access; auto-backfilled from `history.json` on first
load; degrades gracefully (`is_available()` returns `False` without env vars).

Human-facing setup guides: [SUPABASE_SETUP_GUIDE.md](../SUPABASE_SETUP_GUIDE.md),
[SUPABASE_INTEGRATION_README.md](../SUPABASE_INTEGRATION_README.md).

## Core Schema

Core 4 tables in `supabase_schema.sql`:

- `trend_runs`
- `theme_summaries`
- `articles` — deduplicated by `(content_hash, theme_name)`
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

## RLS

RLS is enabled with read-only public access (`app_settings` / `custom_keywords` allow
public write — single-user app).

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
