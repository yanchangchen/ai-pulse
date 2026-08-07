# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project: AI Pulse

AI News Intelligence Dashboard — a Streamlit application that aggregates, classifies, and summarises AI industry developments from engineering blogs and newsletters into 7 strategic themes, with a persistent memory system (local JSON + Markdown Wiki + Supabase cloud sync).

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
OLLAMA_MODEL = "minimax-m3:cloud"
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
Articles are classified into 7 themes (defined in `config/themes.py`) using **weighted keyword matching**: each keyword has an integer weight (1–3), the highest-scoring theme wins. Unmatched articles fall back to **LLM batch classification** — articles are sent in batches of 20 with a system prompt requesting JSON output mapping `"ID N"` → theme name. A final `find_closest_theme()` relaxed match catches anything the LLM misses. The 7 themes: Agentic Systems & DevTools, Frontier Models & Benchmarks, Hardware/Compute/LLMOps, Enterprise Strategy & ROI, Governance/Safety/Policy, AI Security & Trust, AI-Assisted Software Engineering.

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
- `3_Keyword_Analysis.py` — keyword velocity analytics with theme filter & top-10 auto-plotting (with `canonicalize_word()` singular/plural merging and expanded low-signal stopwords), plus theme word clouds
- `4_Sources.py` — full source list with links
- `5_History.py` — Memory Wiki with 3 tabs: **🔮 Ask Sage** (conversational chat agent grounded in wiki data with chronological citations, powered by `core/sage_agent.py`), 📖 Memory Timeline, ⚖️ Compare Runs
- `6_Trend_Analytics.py` — historical thematic momentum & theme drilldown timeline
- `7_Quality_Evaluation.py` — weekly automated quality evaluation engine scoring 7 metrics with live progress panel, in-app theme keyword manager, 1-click apply buttons, and summariser tuner

## Project Structure

```
ai-pulse/
├── app.py                       # Streamlit entry point + sidebar + ingestion state machine
├── pages/                       # 8 Streamlit pages (1_Overview … 8_Quality_Evaluation)
├── core/
│   ├── fetcher.py               # Concurrent RSS + BeautifulSoup web scraping
│   ├── classifier.py            # Weighted keywords → batched LLM classification
│   ├── summariser.py            # LLM summarisation with memory injection
│   ├── visualiser.py            # Word cloud generation
│   ├── evaluator.py             # LLM-as-judge + deterministic quality evaluation pipeline
│   ├── weekly_evaluator.py      # Weekly cadence helper for the evaluator
│   ├── quality_schema.py        # Supabase schema for quality_evaluations table
│   ├── history_manager.py       # JSON / Markdown / Supabase persistence
│   ├── cache.py                 # st.cache_data 6h TTL + disk cache + content hashing
│   ├── bg_refresher.py          # BackgroundRefresher thread (singleton)
│   ├── llm_client.py            # Ollama wrapper (Semaphore(3) concurrency, retries, auth, opt-in event_sink)
│   ├── sage_agent.py            # Sage chat agent (persona, relevance-ranked context builder, first-appearance annotation)
│   ├── shared_sidebar.py        # Consistent nav + status across pages
│   ├── supabase_client.py       # All DB ops with graceful degradation (incl. get_summaries_across_runs)
│   ├── supabase_ui.py           # Sidebar sync status widget
│   └── logger.py                # Centralised logging setup
├── config/
│   ├── settings.py              # Days lookback, cache TTL, fetch workers, evaluation judge budgets
│   ├── themes.py                # 7 themes with weighted keywords
│   ├── sources.py               # RSS feeds + web-scrape registry
│   └── Appendix_*.md            # Watchlists: experts, blogs, papers
├── tests/                       # pytest suite (fetcher, classifier, summariser, visualiser, evaluator, sage_agent)
│   ├── conftest.py              # Shared fixtures: CannedLLM, llm_table, clean_judge_events, integration marker
│   ├── test_fetcher.py
│   ├── test_classifier.py
│   ├── test_summariser.py
│   ├── test_visualiser.py
│   ├── test_evaluator.py
│   └── test_sage_agent.py
├── supabase_schema.sql          # 4 tables, RLS, indexes
├── supabase_migration_dedup.sql # Adds (content_hash, theme_name) unique constraint
├── supabase_migration_keywords.sql # Adds keyword_suggestions table
├── supabase_migration_quality_metrics.sql # Adds 4 new metric columns to quality_evaluations
├── SUPABASE_SETUP_GUIDE.md
├── SUPABASE_INTEGRATION_README.md
├── watch.md                     # User's keyword / engineering blog list
├── memory.md                    # Human-readable run wiki
├── CLAUDE.md
├── README.md
└── requirements.txt
```

## Key Patterns

- **Shared LLM client is lazily instantiated** in each core module via `_get_llm()` (module-level singleton). Uses `threading.Semaphore(3)` for bounded concurrency.
- **Theme keywords are weighted dicts** — higher weight = stronger classification signal. Edit `config/themes.py` to retrain the classifier.
- **`watch.md`** is the user's watchlist keywords and engineering blog sources — referenced by the fetcher for high-signal targeting.
- **Logs** are written to `logs/app.log` via `core/logger.py` (`setup_logger(__name__)`).
- **Supabase is optional and degrades gracefully** — `is_available()` returns `False` if env vars are missing, and the app continues to function with just `history.json` / `memory.md`.
- **LLM calls are batched** for efficiency (20 articles per JSON classification request; 10–20× token savings vs. per-article calls).
- **Quality Evaluation runs 3 LLM judges in parallel + 4 deterministic sub-millisecond judges** — Categoriser (re-classification), Faithfulness (fact-checking), Uniqueness (pairwise overlap), Grounding (citation matching), Structural Compliance (section/sentence bounds), Coverage (article recall), and Temporal Coherence (week-over-week evolution) — inside a `ThreadPoolExecutor` in `core/evaluator.py`. Results persist to Supabase via `core/quality_schema.insert_quality_evaluation()`. The page is ISO-week-guarded (`has_evaluation_this_iso_week`) so a fresh evaluation only runs once per week. While running, the page drives the pipeline from a daemon thread and renders a live progress panel. At the end of a run, in-app remediation controls (**⚡ 1-click apply buttons**, **🎛️ Theme Keyword Manager**, and **⚙️ Faithfulness & Summariser Tuner**) allow users to resolve weak scores directly in the UI without editing backend code.
- **Sage (`core/sage_agent.py`)** is the Memory Wiki's conversational chat agent. It assembles a relevance-ranked, character-budgeted context string from cross-run summaries (`get_summaries_across_runs()`) with first-appearance annotations, then calls the same Ollama LLM via `LLMClient.generate()`. Sage's response format: (1) factual chronological account with `[Theme · Run YYYY-MM-DD]` citations, then (2) "My read on this" assessment. Multi-turn conversation state is stored in `st.session_state.sage_messages`.
- **Keyword canonicalization** — `core/visualiser.py` exports `canonicalize_word()` which normalises plurals (`agents` → `agent`, `models` → `model`, `capabilities` → `capability`). Both `extract_top_words()` and the Keyword Velocity Analytics in `pages/3_Keyword_Analysis.py` use it to merge singular/plural variants. A comprehensive low-signal stopword list filters verbs, time indicators, and generic prose.
