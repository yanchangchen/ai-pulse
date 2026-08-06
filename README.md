# AI Pulse — Intelligence Dashboard

An advanced AI news intelligence dashboard that aggregates, analyses, and persists AI industry developments into a longitudinal memory system. Aggregates 30+ engineering blogs and newsletters, classifies them into 7 strategic themes, and produces context-aware LLM summaries that track the **evolution** of trends across runs. Includes **Sage**, an embedded AI research analyst you can chat with to explore trends in the archive.

## 🚀 Key Features

- **7 strategic themes** — weighted keyword classification, with batched LLM fallback for ambiguous articles (20 articles per JSON request, 10–20× token savings).
- **Background ingestion** — first load and 6-hour-expiry refreshes run in a daemon thread (`core/bg_refresher.py`); the UI stays responsive while the pipeline runs.
- **Persistent memory** — every run is appended to `history.json` (machine-readable), `memory.md` (human-readable wiki), and optionally Supabase (cloud). The last 2 runs' summaries are injected into the next LLM prompt so the model reports on **evolutions**, not static snapshots.
- **Token optimisation** — content-based SHA-256 hashing skips redundant LLM calls when fetched articles are unchanged; `articles` table uses `(content_hash, theme_name)` UPSERT to deduplicate across runs.
- **Longitudinal analytics** — Trend Analytics, Emerging Trends, and Memory Wiki pages query Supabase directly for cross-run metrics (momentum, novelty, keyword velocity).
- **Graceful degradation** — Supabase is fully optional. Missing env vars short-circuit cloud writes; the app keeps working from local files.

## 🧠 How the Memory System Works

1. **Fresh signal** — fetch latest articles from RSS + web sources (`core/fetcher.py`, `config/sources.py`).
2. **Historical context** — pull the last 2 runs' theme summaries from `history.json` / Supabase.
3. **Synthesis** — the LLM generates a summary that notes what has changed since the last update.
4. **Persistence** — write to `memory.md`, `history.json`, and Supabase, creating a permanent record of industry shifts.

## 🛠️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        AI Pulse App                              │
├─────────────────────────────────────────────────────────────────┤
│  Pages                                                           │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐│
│  │ Overview │ │ Deep Dive│ │ Keyword │ │ Sources  │ │ Memory ││
│  │  (Home)  │ │          │ │ Analysis│ │          │ │  Wiki  ││
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └────────┘│
│  ┌──────────────────┐ ┌──────────────────────────────────────┐ │
│  │  Trend Analytics │ │       Quality Evaluation             │ │
│  │  (cross-run)     │ │  (LLM-as-judge, weekly cadence)      │ │
│  └──────────────────┘ └──────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  Quality Evaluation (8) — LLM-as-judge, weekly cadence,    │ │
│  │  live progress panel, Supabase-backed                      │ │
│  └────────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────┤
│  Core Intelligence Layer                                         │
│  • Fetcher       (concurrent RSS + web scraping)                 │
│  • Classifier    (weighted keywords → batched LLM → fallback)    │
│  • Summariser    (context-aware, memory-injected)                │
│  • History Mgr   (JSON + Markdown + Supabase persistence)        │
│  • Cache         (st.cache_data 6h TTL + .cache/ disk JSON)      │
│  • BG Refresher  (daemon thread, non-blocking pipeline)          │
│  • Shared Sidebar (consistency across all pages)               │
│  • LLM Client    (Ollama Cloud, exponential-backoff retries)     │
│  • Sage Agent    (Memory Wiki chat — chronological citations)    │
│  • Evaluator     (3 concurrent LLM-as-judge pools; weekly run)   │
│  • Quality Schema (Supabase table for evaluator results)         │
├─────────────────────────────────────────────────────────────────┤
│  Configuration Layer                                             │
│  • config/settings.py   (Ollama endpoint, model, lookback, TTL)  │
│  • config/themes.py     (7 weighted-keyword theme dicts)         │
│  • config/sources.py    (RSS feeds + web-scrape registry)        │
│  • config/Appendix_*.md (experts, blogs, papers watchlists)      │
│  • watch.md             (user's keyword / engineering blog list) │
└─────────────────────────────────────────────────────────────────┘
```

## 📦 Setup & Deployment

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Secrets
Create `.streamlit/secrets.toml`:
```toml
OLLAMA_BASE_URL = "https://api.ollama.com"
OLLAMA_MODEL    = "qwen3.5:cloud"
OLLAMA_API_KEY  = "your-api-key"
```

### 3. Configure Supabase (Optional)
For cloud persistence, create `.env`:
```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-key
```

See [`SUPABASE_SETUP_GUIDE.md`](./SUPABASE_SETUP_GUIDE.md) for the full schema (4 tables, RLS, UPSERT migration) and [`SUPABASE_INTEGRATION_README.md`](./SUPABASE_INTEGRATION_README.md) for the integration overview.

### 4. Run the App
```bash
streamlit run app.py
```

The first load triggers a background ingestion. Subsequent loads restore from `history.json` and only refresh in the background if the cache is older than 6 hours.

## 🧭 Pages

| # | Page | Purpose |
|---|------|---------|
| — | Home (`app.py`) | Dashboard with theme metrics and live ingestion status |
| 1 | Overview | Theme summary cards with key takeaways |
| 2 | Deep Dive | Per-theme article list, full summaries, and further reading |
| 3 | Keyword Analysis | Keyword velocity analytics with theme filter & top-10 auto-plotting (with singular/plural canonicalization & low-signal stopword filtering), plus theme word clouds & frequency distributions |
| 4 | Sources | All RSS feeds and web sources with article counts |
| 5 | Memory Wiki | **🔮 Ask Sage** (default tab) — conversational chat agent grounded in wiki data with chronological citations; 📖 Memory Timeline — browse past runs; ⚖️ Compare Runs — side-by-side diff |
| 6 | Trend Analytics | Cross-run thematic momentum line chart & detailed theme historical drilldown timeline |
| 8 | Quality Evaluation | Weekly automated evaluation engine scoring 7 metrics: 3 LLM-as-judge (Categoriser, Faithfulness, Uniqueness) + 4 sub-millisecond deterministic judges (Grounding, Structural Compliance, Coverage, Temporal Coherence) with a live progress panel; results persist to Supabase. Also surfaces **in-app theme keyword manager, 1-click apply buttons, and summariser tuner**. |

All pages share a sidebar nav (`core/shared_sidebar.py`) and live background-status panel.

## 🎯 The 7 Strategic Themes

Defined in `config/themes.py` with **weighted keywords** (1–3 — higher weight = stronger signal):

1. **Agentic Systems & DevTools** — agentic workflows, RAG, MCP, LangChain/LangGraph, multi-agent orchestration, harness design, context engineering.
2. **Frontier Models & Benchmarks** — model releases, context windows, MoE, KV cache, speculative decoding, GPQA/SWE-bench/ARC-AGI, RLHF/GRPO.
3. **Hardware, Compute & LLMOps** — NVIDIA Blackwell, CoreWeave, TPU, GPU, Kubernetes, edge inference, unit economics, power demand.
4. **Enterprise Strategy & ROI** — funding, acquisitions, partnerships, IPO, enterprise revenue, time-to-market, pricing models, ROI.
5. **Governance, Safety & Policy** — EU AI Act, executive orders, export controls, sovereign AI, model signing, supply chain security, NIST.
6. **AI Security & Trust** — prompt injection, jailbreaks, red-teaming, guardrails, model poisoning, exfiltration, secure MCP, agent hijack, model theft, MLSec.
7. **AI-Assisted Software Engineering** — Cursor, Claude Code, Copilot, AI code review, agentic SDLC, spec-driven development, AI-generated tests, dev velocity.

The classifier processes articles through a **4-pass waterfall pipeline** (`core/classifier.py`, `core/tfidf_classifier.py`):
1. **Pass 1 (Weighted Keywords)**: Fast exact keyword matching using `config/themes.py` weights (~75% of items).
2. **Pass 2 (TF-IDF Cosine Similarity)**: Sub-millisecond vector cosine angle matching against theme vocabulary vectors (~20% of items).
3. **Pass 3 (Batched Ollama LLM)**: Batched LLM requests reserved strictly for remaining ambiguous items (< 5% of items).
4. **Pass 4 (Soft-Match Heuristic)**: Guaranteed fuzzy token/substring fallback to ensure 100% coverage.

Gate breakdown metrics (Pass 1/2/3/4 item counts and percentages) are tracked automatically and rendered on the **Quality Evaluation** page (`pages/8_Quality_Evaluation.py`) to monitor gate efficiency over time.

### 🔬 Quality Evaluation Metrics (7 Scores)

The evaluation suite (`core/evaluator.py`) combines LLM judges with zero-cost deterministic validation:

1. **Categoriser Accuracy** (LLM) — Re-classifies a stratified sample of articles to test theme assignment correctness.
2. **Faithfulness** (LLM) — Fact-checks summary claims against theme-filtered source articles (excludes empty/fallback sections).
3. **Uniqueness** (Hybrid Heuristic + LLM) — Measures pairwise overlap across themes and consecutive runs.
4. **Grounding Score** (Deterministic) — Verifies that `further_reading` citations match actual input source article titles/URLs.
5. **Structural Compliance** (Deterministic) — Validates section counts, prose sentence bounds (3–7 sentences), and watchlist formatting.
6. **Coverage Score** (Deterministic) — Measures source article recall across the theme's generated summary.
7. **Temporal Coherence** (Deterministic) — Verifies week-over-week summary evolution (flags stale text or ungrounded evolution claims).

### 🎯 Keyword & watchlist suggestions

The Quality Evaluation page (`pages/8_Quality_Evaluation.py`) automatically generates two extra lists at the end of every run:

- **Theme keyword suggestions** — for each theme whose classifier score falls below the threshold, the LLM is asked which 3–10 weighted keywords are missing. Suggestions are deduped against the existing entries in `config/themes.py` and rendered with a copy-pasteable `THEMES["…"]["keywords"].update(...)` snippet.
- **Watchlist term suggestions** — the LLM is asked which 5–15 high-signal terms are missing from `watch.md`. Suggestions are deduped against the parsed `## 1. SEARCH KEYWORDS` table and rendered as a CSV row ready to paste.

Suggestions persist to the `keyword_suggestions` Supabase table (run `supabase_migration_keywords.sql` once to create it). When the table is missing the suggestions still render in the page; they just won't be saved across runs. Run `supabase_migration_quality_metrics.sql` to add the 4 new metric columns to existing Supabase instances.

Set `LLM_DEBUG=1` in `.env` to dump the prompt and raw response body for every empty/failed LLM call — useful when Ollama Cloud is returning empty responses and the judges can't recover.

## 🧪 Tests

```bash
pytest tests/                                  # main test suite (fetcher, classifier, summariser, visualiser, evaluator, sage_agent)
                                               # picks up shared fixtures in tests/conftest.py (CannedLLM, clean_judge_events, integration marker)
pytest -m integration tests/                   # opt-in: exercises the real LLM and Supabase wiring
python test_supabase.py                        # Supabase connection + write/read smoke test
python test_backfill.py                        # history.json → Supabase backfill
python test_deduplication.py                   # UPSERT dedup of (content_hash, theme_name)
python test_llm_optimization.py                # skip-summarise-when-unchanged logic
```

## 📈 Performance & Monitoring

- **Logs** — `logs/app.log` (console + file handler via `core/logger.py`).
- **Cache** — `st.cache_data` with 6-hour TTL + `.cache/*.json` disk persistence; `get_articles_hash()` short-circuits LLM calls when content is unchanged.
- **Cloud sync** — Supabase sync status shown in the sidebar; all runs persisted automatically once env vars are set; first load backfills `history.json` → Supabase.

## 📁 Project Structure

```
ai-pulse/
├── app.py                       # Streamlit entry point + sidebar + ingestion state machine
├── pages/                       # 8 Streamlit pages (Home, Overview … Quality Evaluation)
├── core/
│   ├── fetcher.py               # Concurrent RSS + BeautifulSoup web scraping
│   ├── classifier.py            # Weighted keywords + batched LLM classification
│   ├── summariser.py            # LLM summarisation with memory-injection
│   ├── visualiser.py            # Word cloud generation
│   ├── evaluator.py             # LLM-as-judge quality evaluation pipeline
│   ├── weekly_evaluator.py      # Weekly cadence helper for the evaluator
│   ├── quality_schema.py        # Supabase schema for quality_evaluations table
│   ├── history_manager.py       # JSON / Markdown / Supabase persistence
│   ├── cache.py                 # st.cache_data + disk cache + content hashing
│   ├── bg_refresher.py          # BackgroundRefresher thread (singleton)
│   ├── llm_client.py            # Ollama Cloud wrapper (retries, auth, opt-in event_sink)
│   ├── sage_agent.py            # Sage chat agent (persona, context builder, relevance scorer)
│   ├── shared_sidebar.py        # Consistent nav + status across pages
│   ├── supabase_client.py       # All DB ops, graceful degradation
│   ├── supabase_ui.py           # Sidebar sync status widget
│   └── logger.py                # Centralised logging setup
├── config/
│   ├── settings.py              # Days lookback, cache TTL, fetch workers
│   ├── themes.py                # 7 themes with weighted keywords
│   ├── sources.py               # RSS feeds + web-scrape registry
│   └── Appendix_*.md            # Watchlists: experts, blogs, papers
├── tests/                       # pytest suite (fetcher/classifier/summariser/visualiser/evaluator/sage_agent)
│   ├── conftest.py              # Shared fixtures: CannedLLM, llm_table, clean_judge_events, integration marker
│   ├── test_fetcher.py
│   ├── test_classifier.py
│   ├── test_summariser.py
│   ├── test_visualiser.py
│   ├── test_evaluator.py
│   └── test_sage_agent.py
├── test_backfill.py             # Standalone: history.json → Supabase backfill smoke test
├── test_deduplication.py        # Standalone: UPSERT dedup of (content_hash, theme_name)
├── test_llm_optimization.py     # Standalone: skip-summarise-when-unchanged logic
├── test_supabase.py             # Standalone: Supabase connection + write/read smoke test
├── .streamlit/                  # secrets.toml.example
├── supabase_schema.sql          # 4 tables, RLS, indexes
├── supabase_migration_dedup.sql # Adds (content_hash, theme_name) unique constraint
├── supabase_migration_keywords.sql # Adds keyword_suggestions table (used by Quality Evaluation)
├── supabase_migration_quality_metrics.sql # Adds 4 new metric columns to quality_evaluations table
├── SUPABASE_SETUP_GUIDE.md
├── SUPABASE_INTEGRATION_README.md
├── watch.md                     # User's keyword / engineering blog list
├── memory.md                    # Human-readable run wiki
├── CLAUDE.md
└── requirements.txt
```

---

*Built for AI Engineering & Product Managers to track the high-signal frontier of the industry.*
