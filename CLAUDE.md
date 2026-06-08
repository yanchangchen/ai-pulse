# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project: AI Pulse

AI News Intelligence Dashboard — a Streamlit application that aggregates, classifies, and summarises AI industry developments from engineering blogs and newsletters into 5 strategic themes, with a persistent memory system (local JSON + Markdown Wiki + Supabase cloud sync).

## Running the Application

```bash
cd ai-pulse
streamlit run app.py
```

First load triggers a `BackgroundRefresher` thread; the UI stays responsive while news is fetched, classified, and summarised. Subsequent loads restore from `history.json` and only refresh in the background if the cache is older than 6 hours.

## Testing

```bash
pytest tests/
```

Single test file: `pytest tests/test_classifier.py`

## Configuration

API keys and settings in `.streamlit/secrets.toml`:
```toml
OLLAMA_BASE_URL = "https://api.ollama.com"
OLLAMA_MODEL = "qwen3.5:cloud"
OLLAMA_API_KEY = "your-api-key"
```

Optional Supabase persistence via `.env`:
```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-key
```

Core constants are in `config/settings.py` — source of truth for `DAYS_LOOKBACK` (14), `CACHE_TTL_SECONDS` (6h), and `FETCH_WORKERS` (8). `_get_secret()` reads in order: `st.secrets` → `.streamlit/secrets.toml` → env vars → default.

## Architecture

### Data Pipeline
The app runs an intelligence pipeline: `fetcher` → `classifier` → `summariser` → `history_manager` → (optional) `supabase_client`. It can execute synchronously on first run or in a background thread via `core/bg_refresher.py` (singleton `BackgroundRefresher`) for non-blocking UI.

### Theme Classification
Articles are classified into 5 themes (defined in `config/themes.py`) using **weighted keyword matching**: each keyword has an integer weight (1–3), the highest-scoring theme wins. Unmatched articles fall back to **LLM batch classification** — articles are sent in batches of 20 with a system prompt requesting JSON output mapping `"ID N"` → theme name. A final `find_closest_theme()` relaxed match catches anything the LLM misses.

### Caching Strategy
Two-layer cache in `core/cache.py`:
1. **`st.cache_data`** with 6-hour TTL wraps every expensive call (`cache_fetch_news`, `cache_classify_articles`, `cache_generate_summaries`, `cache_wordclouds`).
2. **Disk-based JSON cache** in `.cache/` for restart survival.
3. **Content-based hashing** (`get_articles_hash()` — SHA-256 of sorted `title+link`) prevents redundant LLM calls if article content hasn't changed between runs.

### Memory System (Three Layers)
- `history.json` — machine-readable run history with full articles, themed articles, and summaries
- `memory.md` — human-readable wiki that appends each run as a new section
- Supabase (PostgreSQL) — cloud persistence for cross-device/mobile access; auto-backfilled on first load from `history.json`

`core/history_manager.py` exposes `get_recent_context()` which injects the last 2 runs' summaries into LLM prompts so the model reports on **evolutions** rather than static updates.

### Supabase Schema
4 tables in `supabase_schema.sql`:
- `trend_runs` — one row per pipeline run (`run_timestamp`, `run_date`, `total_articles`)
- `theme_summaries` — 5 rows per run (`run_id`, `theme_name`, `what_is_happening`, `why_it_matters`, `what_to_watch`, `article_count`)
- `articles` — every article ever seen, deduplicated by `(content_hash, theme_name)` via UPSERT
- `sync_metadata` — key/value sync state

RLS is enabled with read-only public access. Run `supabase_schema.sql` and `supabase_migration_dedup.sql` in the Supabase SQL Editor to set up.

### Pages
- `1_Overview.py` — theme summary cards with key takeaways
- `2_Deep_Dive.py` — per-theme article list and further reading
- `3_Word_Clouds.py` — trending topics visualised
- `4_Sources.py` — full source list with links
- `5_History.py` — Memory Wiki to browse past runs
- `6_Trend_Analytics.py` — historical trend analysis
- `7_Emerging_Trends.py` — emergence timeline, acceleration index, novelty scoring, and novel articles from the last 7 days (queries Supabase directly)

## Key Patterns

- **Shared LLM client is lazily instantiated** in each core module via `_get_llm()` (module-level singleton).
- **Theme keywords are weighted dicts** — higher weight = stronger classification signal. Edit `config/themes.py` to retrain the classifier.
- **`watch.md`** is the user's watchlist keywords and engineering blog sources — referenced by the fetcher for high-signal targeting.
- **Logs** are written to `logs/app.log` via `core/logger.py` (`setup_logger(__name__)`).
- **Supabase is optional and degrades gracefully** — `is_available()` returns `False` if env vars are missing, and the app continues to function with just `history.json` / `memory.md`.
- **LLM calls are batched** for efficiency (20 articles per JSON classification request; 10–20× token savings vs. per-article calls).
