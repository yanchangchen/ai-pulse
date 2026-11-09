# Configuration Reference

Where every setting lives, how it is resolved, and the gotchas. Read this before touching
`config/`, secrets, environment variables, or anything that reads summariser parameters.

## `.streamlit/secrets.toml`

Read by `config/settings.py` `_get_secret()` in order: `st.secrets` → direct TOML parse →
env vars → default. Keys: `OLLAMA_BASE_URL`, `OLLAMA_MODEL` (default
`nemotron-3-ultra:cloud`), `OLLAMA_API_KEY`, `GEMINI_API_KEY`, `GEMINI_MODEL` (default
`gemini-3.8-flash`; `GEMINI_AVAILABLE_MODELS` lists the switchable set — stable/GA
models only), `GEMINI_EMBEDDING_MODEL` (default `gemini-embedding-001`),
and Langfuse tracing: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`,
`LANGFUSE_HOST` (default `https://cloud.langfuse.com`; US region
`https://us.cloud.langfuse.com`).

## `.env`

Optional Supabase persistence: `SUPABASE_URL`, `SUPABASE_KEY`. See [docs/SUPABASE.md](SUPABASE.md).

## Embedding pipeline (Phase 3)

Settings are read through `get_embedding_settings()` (`config/settings.py`), which merges
`config/custom_settings.json` over these defaults. Never hardcode them.

| Setting | Default | Meaning |
|---|---|---|
| `embedding_enabled` | `true` | Gates the ingest-time embed step |
| `embedding_batch_size` | `50` | Texts per embedding call |
| `embedding_model` | `GEMINI_EMBEDDING_MODEL` | **Gateway provider key** (not a raw model id). `core/embedding.py::_preferred_embedding_model()` ignores values not registered in `get_gateway().providers` and silently falls back to the routing default |
| `embedding_dimensions` | `768` | Must match `article_embeddings.embedding vector(768)`; `EMBEDDING_DIMENSIONS` env/secret overrides the default |
| `embedding_backfill_sleep` | `0.5` | Seconds between backfill batches (rate-limit headroom) |

Provider credentials today: `GEMINI_API_KEY`. The only registered embedding route is
`gemini-embedding-001` (no fallback).

**Not implemented yet.** Moving to a free hosted provider — Cloudflare Workers AI, Jina AI,
or a self-deployed Render service — adds these credentials through the same
`config.settings` chain:

| Provider | New keys |
|---|---|
| Cloudflare Workers AI | `CF_ACCOUNT_ID`, `CF_API_TOKEN` |
| Jina AI | `JINA_API_KEY` |
| Render service | `EMBEDDING_SERVICE_URL`, `EMBEDDING_SERVICE_TOKEN` |

> **Safe switch:** the implementation plan adds an explicit `EMBEDDING_PROVIDER` setting
> (default `gemini`) so credentials alone do not switch the live route. Flip it as the first
> step of a controlled migration window, then re-embed every existing row and drain the
> backlog before enabling retrieval. Do not leave it half-switched: old Gemini and new BGE
> vectors in one index make distances meaningless.

See [docs/EMBEDDING_PROVIDERS.md](EMBEDDING_PROVIDERS.md) for models, free tiers, the adapter
contract, and the re-embed runbook. `embedding_dimensions` stays `768` for all three options;
only a non-768 model would require a migration.

## Gateway provider init

`ModelGateway._init_providers()` (`core/ai_gateway/gateway.py`) resolves
`GEMINI_API_KEY` / `OLLAMA_API_KEY` / `OLLAMA_BASE_URL` **through the `config.settings`
constants**, i.e. the same `st.secrets` → secrets.toml → env vars → default chain. Keys
stored only in `secrets.toml` are visible to the gateway; plain OS environment variables
also work.

> Historical gotcha (now fixed — see BLOG.md 2026-09-11): an earlier version read
> `os.getenv(...)` directly inside `_init_providers`, which started the gateway with zero
> providers when keys lived only in `secrets.toml` — every gateway task then silently
> landed on the deterministic fallback ("Non-LLM" / "gateway:error" provenance chips).

Gateway behaviour tunables (OS env vars): `OLLAMA_REQUEST_TIMEOUT` (default 180s — cloud
models can exceed 60s under load), `GEMINI_REQUEST_TIMEOUT` (default 30s),
`GATEWAY_HEALTH_RESET_SECONDS` (default 300s).

## Langfuse tracing (Phase 0.3, Pillar F)

`core/ai_gateway/tracing.py` emits one OpenTelemetry span per gateway
execution and exports it OTLP to Langfuse. **Env-gated**: with either key
absent (or the `opentelemetry-*` packages uninstalled) tracing is a silent
no-op — no network, no threads, `trace_id` stays `None` in `llm_calls`.
Keys resolve through the same `config.settings` chain, so
`.streamlit/secrets.toml` or plain env vars both work. Dependencies:
`opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`
(requirements.txt). Switching to another OTLP backend (e.g. self-hosted
Phoenix) means changing `LANGFUSE_HOST` / the exporter in `_configure()`,
nothing else.

## Core constants — `config/settings.py`

Source of truth for `DAYS_LOOKBACK` (14), `CACHE_TTL_SECONDS` (12h), `FETCH_WORKERS` (8),
evaluation budgets, and the **model-aware context-window table**
(`OLLAMA_MODEL_CONTEXT_WINDOWS` / `get_ollama_num_ctx()` — wrong `num_ctx` truncates input
and causes empty responses).

## Summariser tuner settings — `config/custom_settings.json`

The Quality Evaluation page's summariser tuner persists temperature / max_tokens /
strict-faithfulness overrides to `config/custom_settings.json` via
`load_custom_settings()` / `get_summariser_settings()` — code that reads summariser params
must go through that merge, not hardcode defaults.

The overlay is **Supabase-backed** (`app_settings` table, key `custom_settings`): loads are
remote-first (remote row wins and refreshes the local file as an offline cache; the remote
fetch is TTL-cached ~30s), saves write through to both. Same for the auto-remediation state
(`data/auto_remediation_state.json`, key `auto_remediation_state`) — this is what makes
evaluation-driven config survive app restarts and Streamlit Cloud redeploys, where the
local filesystem is wiped on every git push. Accessors:
`core/supabase_client.py` `get_app_setting()` / `upsert_app_setting()` — see
[docs/SUPABASE.md](SUPABASE.md).

## `config/custom_keywords.json`

Persisted custom theme keywords (added via the Theme Keyword Manager, the classifier's
self-improvement loop, or approved watchlist suggestions) — hot-reloaded by the classifier. See
[docs/ARCHITECTURE.md](ARCHITECTURE.md#self-improving-keywords).
