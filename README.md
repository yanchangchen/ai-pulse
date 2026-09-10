# AI Pulse — Intelligence Dashboard

An AI news intelligence dashboard that aggregates, classifies, and persists AI industry developments into a longitudinal memory system. Fetches from 30+ sources, classifies into 7 strategic themes, and produces context-aware summaries that track the **evolution** of trends across runs. Includes **Sage**, an embedded AI research analyst you can chat with to explore the archive.

## 🚀 Quick Start

```bash
pip install -r requirements.txt
# Configure secrets in .streamlit/secrets.toml (see below)
streamlit run app.py
```

The first load triggers a background ingestion. Subsequent loads restore from `history.json` and only refresh if the cache is older than 12 hours.

## ✨ Key Features

- **Multi-model AI gateway** — task-typed LLM calls routed across Gemini and Ollama Cloud with fallback chains, health tracking, context-fit checks, and per-request timeouts. [Architecture details →](docs/ARCHITECTURE.md#routing-policies)
- **Deterministic classification** — 4-pass waterfall (keywords → TF-IDF → optional LLM → heuristic). The LLM gate can be disabled entirely once keyword coverage matures. [Modes explained →](docs/ARCHITECTURE.md#classification-modes)
- **Self-improving keywords** — after each run, missing keywords are heuristically extracted from LLM/heuristic articles and auto-applied if they appear in 3+ articles. [How it works →](docs/ARCHITECTURE.md#self-improving-keywords)
- **Non-LLM fallback** — LexRank + Luhn extractive engine produces 5-section briefs in <50ms, zero tokens, 100% faithful.
- **Persistent memory** — every run persisted to `history.json`, `memory.md`, and optionally Supabase. The last 2 runs' summaries are injected into the next prompt so the model reports on **evolutions**, not static snapshots.
- **Quality evaluation** — 7 automated metrics (3 LLM-as-judge + 4 deterministic) with in-app remediation. [Details →](docs/QUALITY_EVALUATION.md)
- **Sage agent** — conversational AI research analyst grounded in the Memory Wiki archive with chronological citations.

## 📦 Setup

### 1. Configure Secrets

Create `.streamlit/secrets.toml`:
```toml
[general]
OLLAMA_BASE_URL = "https://api.ollama.com"
OLLAMA_MODEL    = "nemotron-3-super:cloud"
OLLAMA_API_KEY  = "your-ollama-api-key"
GEMINI_API_KEY  = "your-gemini-api-key"
```

### 2. Export Environment Variables (for gateway routing)

The Model Gateway reads from OS env vars, not Streamlit secrets:

```bash
export OLLAMA_BASE_URL="https://api.ollama.com"
export OLLAMA_API_KEY="your-ollama-api-key"
export GEMINI_API_KEY="your-gemini-api-key"
# Optional: tighten per-provider request timeouts
export OLLAMA_REQUEST_TIMEOUT="60"   # seconds; default 60
export GEMINI_REQUEST_TIMEOUT="30"   # seconds; default 30
```

### 3. Configure Supabase (Optional)

For cloud persistence, create `.env`:
```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-key
```

See [`SUPABASE_SETUP_GUIDE.md`](./SUPABASE_SETUP_GUIDE.md) for the full schema and migrations.

### 4. Run

```bash
streamlit run app.py
```

## 🧭 Pages

| # | Page | Purpose |
|---|------|---------|
| — | Home | Dashboard with theme metrics and live ingestion status |
| 1 | Overview | Theme summary cards with provenance chips |
| 2 | Deep Dive | Per-theme articles, summaries, on-demand Gemini re-summarisation |
| 3 | Keyword Analysis | Keyword velocity analytics, word clouds |
| 4 | Sources | RSS feeds and web sources with article counts |
| 5 | Memory Wiki | **🔮 Ask Sage** chat, 📖 Memory Timeline, ⚖️ Compare Runs |
| 6 | Trend Analytics | Cross-run thematic momentum and theme drilldown |
| 7 | Quality Evaluation | 7 automated metrics with in-app remediation |
| 8 | Feedback & Roadmap | Feature requests, bug reports, public roadmap |

## 🎯 The 7 Strategic Themes

1. **Agentic Systems & DevTools** — agentic workflows, RAG, MCP, LangChain/LangGraph
2. **Frontier Models & Benchmarks** — model releases, MoE, KV cache, GPQA/SWE-bench
3. **Hardware, Compute & LLMOps** — NVIDIA, TPU, GPU, Kubernetes, edge inference
4. **Enterprise Strategy & ROI** — funding, acquisitions, IPO, enterprise revenue
5. **Governance, Safety & Policy** — EU AI Act, export controls, sovereign AI, NIST
6. **AI Security & Trust** — prompt injection, jailbreaks, guardrails, agent hijack
7. **AI-Assisted Software Engineering** — Cursor, Claude Code, Copilot, AI code review

## 🧪 Tests

```bash
pytest tests/          # ~240 tests
pytest -m integration  # opt-in: real LLM + Supabase wiring
```

See [test files](tests/) for the full suite.

## 📁 Docs

| Document | What's Inside |
|---|---|
| [Architecture](docs/ARCHITECTURE.md) | Classification modes, system diagrams, routing policies, summarizer pipeline |
| [Quality Evaluation](docs/QUALITY_EVALUATION.md) | 7 judge metrics table, in-app remediation |
| [BLOG.md](BLOG.md) | Engineering lessons learned, dated entries grouped by 2-week blocks |
| [CLAUDE.md](CLAUDE.md) | Full architecture, configuration, and gotchas for developers |

## 📈 Performance & Monitoring

- **Logs** — `logs/app.log` (console + file handler)
- **Cache** — `st.cache_data` with 12h TTL + `.cache/*.json` disk persistence
- **Content hashing** — SHA-256 skips redundant LLM calls when articles are unchanged
- **Cloud sync** — Supabase sync status shown in sidebar; first load backfills `history.json` → Supabase

---

*Built for AI Engineering & Product Managers to track the high-signal frontier of the industry.*
