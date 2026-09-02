# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project: AI Pulse

AI News Intelligence Dashboard — a Streamlit application that aggregates, classifies, and summarises AI industry developments from engineering blogs and newsletters into 7 strategic themes. LLM work runs through a **multi-model gateway** (Ollama Cloud + Google Gemini) with routing, fallback, and provenance; a persistent memory system (local JSON + Markdown Wiki + Supabase cloud sync) feeds prior-run context back into every summary.

## Running the Application

```bash
cd ai-pulse
streamlit run app.py
```

First load triggers a `BackgroundRefresher` thread; the UI stays responsive (ingestion status screen) while news is fetched, classified, and summarised. Subsequent loads restore from `history.json` and only refresh in the background if the cache is older than 12 hours.

## Testing

```bash
python -m pytest tests/                      # main suite (~215 tests)
python -m pytest tests/test_classifier.py    # single file
python -m pytest tests/test_summariser.py::test_name   # single test
python -m pytest -m integration tests/       # opt-in: real LLM + Supabase wiring
```

- Always target `tests/` explicitly — the root-level `test_*.py` files (`test_supabase.py`, `test_backfill.py`, `test_deduplication.py`, `test_llm_optimization.py`) are manual smoke scripts meant to be run directly with `python`, not collected by pytest.
- `tests/conftest.py` has an autouse fixture that resets `LLMClient` quota flags around every test; keep it in mind when adding fixtures that touch quota state.

## Configuration

- `.streamlit/secrets.toml` — read by `config/settings.py` `_get_secret()` in order: `st.secrets` → direct TOML parse → env vars → default. Keys: `OLLAMA_BASE_URL`, `OLLAMA_MODEL` (default `nemotron-3-super:cloud`), `OLLAMA_API_KEY`, `GEMINI_API_KEY`, `GEMINI_MODEL` (default `gemini-3.7-flash`; `GEMINI_AVAILABLE_MODELS` lists the switchable set).
- `.env` — optional Supabase persistence: `SUPABASE_URL`, `SUPABASE_KEY`.
- **⚠️ Gateway gotcha:** `ModelGateway._init_providers()` reads `os.getenv("GEMINI_API_KEY" / "OLLAMA_API_KEY" / "OLLAMA_BASE_URL")` **directly** — it does NOT consult `st.secrets`/`secrets.toml`. If keys live only in `secrets.toml`, the gateway starts with zero providers and every gateway task silently lands on the deterministic fallback ("Non-LLM"/"gateway:error" provenance chips). Gateway-driven runs need real OS environment variables (or `_init_providers` must be extended to use the `config.settings` constants).
- Core constants are in `config/settings.py` — source of truth for `DAYS_LOOKBACK` (14), `CACHE_TTL_SECONDS` (12h), `FETCH_WORKERS` (8), evaluation budgets, and the **model-aware context-window table** (`OLLAMA_MODEL_CONTEXT_WINDOWS` / `get_ollama_num_ctx()` — wrong `num_ctx` truncates input and causes empty responses).
- The Quality Evaluation page's summariser tuner persists temperature / max_tokens / strict-faithfulness overrides to `config/custom_settings.json` via `load_custom_settings()` / `get_summariser_settings()` — code that reads summariser params must go through that merge, not hardcode defaults.

## Architecture

### Data Pipeline

`fetcher` → `classifier` → `summariser` → `history_manager` → (optional) `supabase_client`. Runs synchronously on first run or in a background daemon thread via `core/bg_refresher.py` (singleton `BackgroundRefresher`).

### Model Gateway (core/ai_gateway/) — the central LLM abstraction

- `contracts.py` — `TaskType` (categorise / extract / summarise / synthesise / project), `AITaskRequest`, `AITaskResult`, `ErrorType` (retryable / non_retryable / output_failure), and `Provenance` (provider, model, latency, attempts, fallback chain, correlation id).
- `gateway.py` — `ModelGateway` singleton via `get_gateway()`. Holds a **model registry + per-task routing policies** (primary + ordered fallback chain, currently Gemini flash models + Ollama cloud models). Per-model health tracking (3 consecutive failures → degraded, 5 → unavailable), context-fit check (input must fit ~60% of the model's window), exponential-backoff retries, JSON-schema validation, and a final **deterministic fallback** when all LLMs fail.
- `providers/` — `GeminiProvider` and `OllamaCloudProvider` async adapters behind `ProviderAdapter`.
- `deterministic.py` — zero-LLM fallbacks: `rule_categorise`, `extractive_summarise`, `keyword_extract`, `statistical_projection`.
- Callers: `core/classifier.py` (pass 3) and `core/summariser.py` call `get_gateway().execute(AITaskRequest(...))` inside `asyncio.run(...)`. New LLM work should go through the gateway, not raw clients.

### Theme Classification — 4-pass waterfall (core/classifier.py)

1. `gate_1_keyword` — weighted keyword matching (integer weights 1–3 in `config/themes.py`, highest score wins).
2. `gate_2_tfidf` — TF-IDF cosine similarity against synthetic theme documents (`core/tfidf_classifier.py`, zero-dependency, sub-millisecond).
3. `gate_3_llm` — LLM classification through the Model Gateway (fallback + provenance).
4. `gate_4_heuristic` — `find_closest_theme()` relaxed soft match.

Gate counts are exposed via `get_latest_gate_stats()` (keys `gate_1_keyword` … `gate_4_heuristic`) and surfaced in the UI.

### Summarisation — three engines, all provenance-tagged

1. **Gateway LLM synthesis** (`generate_theme_summary_gateway`) — a structured 5-section intelligence brief (What Is Happening / Engineering Tradeoffs / Product Impact / Actionable Watchlist / Strategic Further Reading) with length instructions scaled to article count. Articles are relevance-ranked (`_rank_articles_by_relevance`) and truncated to a char budget derived from `num_ctx`. Themes whose article hashes were all seen before are skipped (`gateway:skipped`). Prior-run memory is injected via `get_recent_context()` so briefs report evolutions, not static updates.
2. **Non-LLM extractive engine** (`core/non_llm_summariser.py`) — LexRank graph centrality + Luhn keyword-cluster scoring + n-gram keyphrase extraction; <50ms, zero-cost, 100% extractive. Used when the gateway fails completely, and forced when the user selects "⚡ Non-LLM Extractive Only" (`st.session_state.summariser_mode`).
3. **On-demand Gemini Deep Dive** (`generate_gemini_theme_summary` + `core/gemini_client.py`) — per-theme Deep Dive re-summarisation with up to `MAX_ARTICLES_PER_GEMINI_SUMMARY` (75) articles; on HTTP 429 the UI suggests switching to another model from `GEMINI_AVAILABLE_MODELS`.

Every summary dict carries provenance via `_with_provenance()`: `_source` token (e.g. `"google:gemini-3.6-flash"`, `"ollama:…"`, `"extractive_fallback"`, `"gateway:error"`), `_generation_log`, and optionally the full `_provenance` dict. `core/provenance.py` maps `_source` to the coloured UI chip; Supabase persists it in `theme_summaries.generation_source` / `generation_log`.

### Quota management (core/llm_client.py)

Legacy Ollama wrapper, still the shared client for Sage and quota state. Quota exhaustion is tracked process-wide via flags on the `sys` module (`_aipulse_llm_quota_exceeded` …); `LLMClient.is_quota_exceeded()` / `mark_quota_exceeded()` / `reset_quota_status()` manage it, and `probe_quota_status()` hits `/api/tags` to self-heal the flag instantly when quota recovers (called before refresh runs). A process-local empty-response streak counter lets the summariser degrade the rest of a run to the extractive engine instead of burning retries. Set `LLM_DEBUG=1` to log prompts/responses on failures.

### Memory System (Three Layers)

- `history.json` — machine-readable run history with full articles, themed articles, and summaries.
- `memory.md` — human-readable wiki that appends each run as a new section.
- Supabase (PostgreSQL) — cloud persistence for cross-device access; auto-backfilled from `history.json` on first load; degrades gracefully (`is_available()` returns `False` without env vars).

`core/history_manager.py` exposes `get_recent_context()` (injects the last 2 runs' summaries into LLM prompts) and `purge_run()`.

### Supabase Schema

Core 4 tables in `supabase_schema.sql`: `trend_runs`, `theme_summaries`, `articles` (deduplicated by `(content_hash, theme_name)`), `sync_metadata`. Migrations (run in the SQL Editor, roughly in order): `supabase_migration_dedup.sql`, `supabase_migration_keywords.sql` (keyword_suggestions), `supabase_migration_quality_metrics.sql`, `supabase_migration_provenance.sql` (generation_source/generation_log), `supabase_migration_user_feedback.sql` (page 8 feedback), `supabase_migration_backfill_articles.sql`. RLS is enabled with read-only public access.

### Pages

All pages share `core/shared_sidebar.py` (nav + background-refresh status) and `core/design_system.py` (adaptive CSS tokens, `sanitize_summary_html()`).

- `app.py` — entry point, ingestion state machine, Thematic Pulse dashboard
- `1_Overview.py` — theme summary cards with provenance chips and key takeaways
- `2_Deep_Dive.py` — per-theme article list, full summaries, on-demand Gemini re-summarisation, further reading
- `3_Keyword_Analysis.py` — keyword velocity analytics (theme filter, top-10 auto-plot, `canonicalize_word()` singular/plural merging, low-signal stopwords) + theme word clouds
- `4_Sources.py` — full source list with links
- `5_History.py` — Memory Wiki with 3 tabs: **🔮 Ask Sage** (conversational agent grounded in wiki data, `core/sage_agent.py`, automatic Gemini fallback when the primary LLM fails or is quota-blocked), 📖 Memory Timeline, ⚖️ Compare Runs
- `6_Trend_Analytics.py` — historical thematic momentum & theme drilldown timeline
- `7_Quality_Evaluation.py` — weekly automated evaluation engine scoring 7 metrics (3 LLM-as-judge + 4 deterministic judges, `ThreadPoolExecutor` in `core/evaluator.py`, ISO-week-guarded) with live progress panel and in-app remediation (1-click apply buttons, Theme Keyword Manager, Faithfulness & Summariser Tuner)
- `8_Feedback_&_Roadmap.py` — feature requests / bug reports / UX ideas persisted to the `user_feedback` table, plus SDD writing prompts

## Key Patterns

- **New LLM calls go through the Model Gateway** (`get_gateway()`), which owns routing, retries, health, and provenance. The legacy `_get_llm()` module-level `LLMClient` singletons (`threading.Semaphore(3)`) remain in classifier/summariser only for backward compatibility and quota state.
- **Theme keywords are weighted dicts** — higher weight = stronger signal for both gate 1 and TF-IDF synthetic documents. Edit `config/themes.py` to retrain the classifier.
- **`watch.md`** is the user's watchlist keywords and engineering blog sources — referenced by the fetcher for high-signal targeting.
- **Logs** go to `logs/app.log` via `core/logger.py` (`setup_logger(__name__)`).
- **LLM calls are batched** where possible (classification/evaluation), and summaries are skipped entirely when article content hashes are unchanged (`get_articles_hash()` / `_get_existing_article_hashes()`).
- **Two-layer cache**: `st.cache_data` (12h TTL) wraps every expensive step; `.cache/*.json` disk cache survives restarts; content-based SHA-256 hashing prevents redundant LLM calls.
- **Sage** (`core/sage_agent.py`) assembles a relevance-ranked, character-budgeted context string from cross-run summaries (`get_summaries_across_runs()`) with first-appearance annotations, then calls the primary LLM with automatic Gemini fallback. Responses are (1) a chronological account with `[Theme · Run YYYY-MM-DD]` citations, then (2) a "My read on this" assessment. Multi-turn state lives in `st.session_state.sage_messages`.
- **Keyword canonicalization** — `core/visualiser.py` exports `canonicalize_word()` (merges plurals: `agents` → `agent`); used by `extract_top_words()` and the Keyword Velocity Analytics page.
