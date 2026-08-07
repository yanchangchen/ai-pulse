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

Gate breakdown metrics (Pass 1/2/3/4 item counts and percentages) are tracked automatically and rendered on the **Quality Evaluation** page (`pages/7_Quality_Evaluation.py`) to monitor gate efficiency over time.

### 🔬 Quality Evaluation Suite & Judge Metrics

The evaluation suite (`core/evaluator.py`) runs **7 automated checks** to ensure news summaries and theme classifications remain accurate, faithful, and non-repetitive over time.

#### ⚙️ Execution Architecture
- **3 Concurrent LLM Judges**: Run inside a `ThreadPoolExecutor` bounded by `Semaphore(3)` to prevent API rate limiting:
  - *Categoriser Judge* (fresh theme re-classification sample)
  - *Faithfulness Judge* (fact-checking summary claims against source articles)
  - *Uniqueness Judge* (pairwise summary overlap across themes and runs)
- **4 Sub-Millisecond Deterministic Judges**: Zero-cost rule-based checks running alongside:
  - *Grounding Judge* (citation matching)
  - *Structural Compliance Judge* (section, sentence, and list formatting bounds)
  - *Coverage Judge* (source article recall)
  - *Temporal Coherence Judge* (week-over-week summary evolution tracking)

#### 📊 Comprehensive Judge Metrics (Layman & Technical Summary)

| Metric | Judge Type | What It Checks (Layman Explanation) | How It Checks (Technical Method) | Target Threshold |
|---|---|---|---|---|
| **Categoriser Accuracy** | LLM-as-Judge | Are articles being sorted into the right themes? | Re-classifies a stratified sample of articles via LLM and compares predicted themes against active assignments. | ≥ 80% |
| **Faithfulness Score** | LLM-as-Judge | Are the generated summaries truthful to the original articles without making things up? | Extracts claims from summary bullet points and uses LLM fact-checking against theme-filtered source article text. | ≥ 80% |
| **Uniqueness Score** | Hybrid Heuristic + LLM | Are different theme summaries distinct, or are they repeating the same stories across themes/runs? | Performs Jaccard/cosine text overlap filtering; invokes LLM pairwise uniqueness judge when overlap is in ambiguous bands. | ≥ 80% |
| **Grounding Score** | Deterministic | Do the "Further Reading" links point to real articles fetched in the run? | Cross-references cited titles/links in `further_reading` against the exact set of input source articles. | 100% |
| **Structural Compliance** | Deterministic | Is the summary formatted properly with the right sections, sentence lengths, and bullet counts? | Regex validates mandatory headers (`WHAT HAPPENED`, `SIGNIFICANCE`, `WATCHLIST`) and checks sentence count bounds (3–7 sentences). | 100% |
| **Coverage Score** | Deterministic | Did the summary capture key information from all fetched articles, or did it ignore most of them? | Measures source article title/entity token recall across the generated theme summary. | ≥ 70% |
| **Temporal Coherence** | Deterministic | Is the summary actually updating week-over-week, or is it echoing static past summaries? | Compares active summary against past run summaries to verify evolution claims and flag stale text repetition. | ≥ 75% |

---

### 🛠️ In-App Remediation & Guidance (No Backend Code Edits Needed)

When evaluation scores fall below threshold targets, users do **not** need to edit Python files on the backend. The **Quality Evaluation** page (`pages/7_Quality_Evaluation.py`) includes built-in interactive controls to fix issues directly inside the Streamlit UI:

1. **⚡ 1-Click "Apply All Suggested Keywords"**:
   - When **Categoriser Accuracy** is low for a specific theme, the evaluation engine identifies missing high-signal keywords.
   - Click the **"⚡ Apply all suggested keywords to [Theme]"** button to instantly add missing terms to active configuration with disk persistence.

2. **🎛️ Theme Keyword Manager**:
   - Add new keywords with customizable weights (1–3) or remove weak keywords for any of the 7 strategic themes.
   - Automatically persists changes to `config/custom_keywords.json` and hot-reloads the classifier pipeline.

3. **⚙️ Faithfulness & Summariser Tuner**:
   - If **Faithfulness** drops, adjust LLM generation parameters directly via UI sliders:
     - Lower **Temperature** (e.g. `0.1 – 0.2`) to reduce hallucination risk.
     - Adjust **Max Output Tokens** to discourage wordy fabrication.
     - Toggle **Strict Anti-Hallucination Grounding Mode** to enforce strict source-article citation rules in prompt templates.
   - Automatically persists settings to `config/custom_settings.json`.

4. **📌 Watchlist Term Suggestions**:
   - Surfaces high-signal term suggestions for `watch.md` in copy-pasteable blocks to improve future fetching precision.

Suggestions persist to the `keyword_suggestions` Supabase table (run `supabase_migration_keywords.sql` once to create it). Set `LLM_DEBUG=1` in `.env` to dump the prompt and raw response body for debugging.

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
