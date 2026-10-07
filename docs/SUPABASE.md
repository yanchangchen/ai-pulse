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

## `llm_calls`

Append-only Model Gateway call log (see `core/ai_gateway/call_log.py`): every completed
gateway execution — LLM success, deterministic fallback, and total failure — writes one
row with task type, provider, model, provider-reported tokens in/out, latency, cost
class, correlation id, and error text. Writes are fire-and-forget daemon threads gated
on `is_available()`, so the log never blocks or breaks an LLM task. `trace_id` is
reserved for the OTel/Langfuse observability integration (Phase 0.3 in `Future.md`).

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
