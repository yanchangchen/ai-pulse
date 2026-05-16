# AI Pulse - Intelligence Dashboard

An advanced AI news intelligence dashboard that aggregates, analyzes, and persists AI industry developments into a longitudinal memory system.

## 🚀 Key Features

- **Thematic Intelligence**: Aggregates from 30+ engineering blogs and newsletters, classifying them into 5 core strategic themes.
- **Persistent Memory**: Tracks every run in a machine-readable `history.json` and a human-readable `memory.md` (Wiki).
- **Temporal Context**: The LLM analyzes the current news in the context of the last two runs, allowing it to report on **evolutions** and **trends** rather than just static updates.
- **Memory Wiki**: A dedicated timeline interface to browse past runs, filter by theme, and track the history of AI developments.
- **Token Optimized**: 
  - **Content Hashing**: Skips LLM calls if the fetched articles are identical to the previous run.
  - **JSON Batching**: Classifies unknown articles in large batches (20+) using structured JSON for 10x-20x efficiency gains.
- **Premium UI**: Modern, card-based interface with custom CSS tokens, smooth transitions, and a dark-mode optimized design.

## 🧠 The Memory System

AI Pulse now functions as an "evolving brain" for your intelligence tracking:
1. **Fresh Signal**: Fetches the latest articles from sources.
2. **Historical Context**: Injects the summaries of previous runs into the LLM prompt.
3. **Synthesis**: The LLM generates a summary that notes what has changed since the last update.
4. **Persistence**: Saves the analysis to the **Memory Wiki**, creating a permanent record of industry shifts.

## 🛠️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        AI Pulse App                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │   Overview   │    │  Memory Wiki │    │ Word Clouds  │      │
│  │    (Latest)  │    │   (History)  │    │    (Trends)  │      │
│  └──────────────┘    └──────────────┘    └──────────────┘      │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                   Core Intelligence Layer                 │  │
│  ├──────────────────────────────────────────────────────────┤  │
│  │ • Fetcher (concurrent RSS + Web)                         │  │
│  │ • Classifier (Weighted Keywords + JSON Batching LLM)     │  │
│  │ • Summariser (Context-Aware Summaries + Memory)          │  │
│  │ • History Manager (JSON/Markdown Persistence)            │  │
│  │ • Cache (Content-Based Hashing + 6-hour TTL)             │  │
│  │ • Logger (Centralized app.log audit trail)               │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │               Configuration & Watchlist                  │  │
│  ├──────────────────────────────────────────────────────────┤  │
│  │ • watch.md (User Intelligence Watchlist)                 │  │
│  │ • themes.py (Deep Signal Keyword Mapping)                │  │
│  │ • sources.py (Engineering Blog & Newsletter Registry)    │  │
│  └──────────────────────────────────────────────────────────┘  │
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
OLLAMA_API_KEY = "your-api-key"
OLLAMA_MODEL = "qwen3.5:cloud"
```

### 3. Run the App
```bash
streamlit run app.py
```

## 🔍 The 5 Strategic Themes

1. **AI Applications & Architecture**: Agentic workflows (ReAct, Plan-and-Execute), RAG production, MCP, tool integration.
2. **AI Models**: LLM releases, context windows, MoE, KV cache optimization, PhD-level benchmarks (GPQA, ARC-AGI).
3. **AI Infrastructure**: NVIDIA Blackwell, CoreWeave, compute clusters, latency, and inference hardware.
4. **AI Companies & Business**: Funding rounds, enterprise partnerships (NVIDIA/SAP/ServiceNow), and M&A.
5. **AI in Government & Policy**: EU AI Act, export controls, safety alignment, and sovereign AI initiatives.

## 📈 Performance & Monitoring

- **Logs**: Detailed app activity and LLM retry logic are stored in `logs/app.log`.
- **Cache**: Data is cached for 6 hours. Manual refreshes use content hashing to prevent redundant LLM spending.
- **Tests**: Run `pytest tests/` to verify classifier, fetcher, and summarizer logic.

---
*Built for AI Engineering & Product Managers to track the high-signal frontier of the industry.*
