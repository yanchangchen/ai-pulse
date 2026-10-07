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
# FAST INNER LOOP (< 0.5s) — ALWAYS prefer targeted tests during iteration:
python -m pytest tests/test_sage_agent.py tests/test_sage_conversations.py   # Sage & conversations
python -m pytest tests/test_supabase_client.py                              # Supabase operations
python -m pytest tests/test_classifier.py                                   # Classifier
python -m pytest tests/test_summariser.py                                   # Summariser
python -m pytest tests/test_summariser.py::test_name                        # Single test function
python -m pytest tests/ -k "sage"                                           # Filter by keyword pattern

# FULL REGRESSION SUITE (444+ tests, ~90s) — ONLY run once as final pre-commit check:
python -m pytest tests/
python -m pytest -m integration tests/                                      # opt-in: real LLM + Supabase wiring
```

- **⚠️ HARNESS DIRECTIVE: NEVER run the full suite (`pytest tests/`) after routine code edits.** The suite has grown to 444+ tests and takes ~90 seconds. Future harnesses MUST run only the 1–2 test files relevant to the modified code during iteration (sub-second feedback), reserving the full regression run strictly for the final verification step before completing the task.
- Always target `tests/` explicitly — the root-level `test_*.py` files (`test_supabase.py`, `test_backfill.py`, `test_deduplication.py`, `test_llm_optimization.py`) are manual smoke scripts meant to be run directly with `python`, not collected by pytest.
- `tests/conftest.py` has an autouse fixture that resets `LLMClient` quota flags around every test; keep it in mind when adding fixtures that touch quota state.
- A second autouse fixture pins the Supabase manager singleton to an offline stub (`is_available() == False`) so tests never touch the production project. This matters on machines with `.streamlit/secrets.toml`: importing `config.settings` makes Streamlit export all secrets (including `SUPABASE_URL`/`SUPABASE_KEY`) into `os.environ`, which `core/supabase_client.py` reads directly. Tests that exercise Supabase-backed behaviour must patch `core.supabase_client.get_supabase_manager` with their own mock (pattern: `test_processed_articles.py::test_supabase_preferred`).

## Configuration (summary)

- API keys: `ai-pulse/.streamlit/secrets.toml` (`OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OLLAMA_API_KEY`, `GEMINI_API_KEY`); optional Supabase credentials in `ai-pulse/.env`.
- Gateway provider init resolves keys through `config.settings` (st.secrets → secrets.toml → env vars → default) — both `secrets.toml` and plain OS env vars work. Full details and gotchas: docs/CONFIGURATION.md.
- Summariser params must be read via `get_summariser_settings()` (merges `config/custom_settings.json`), never hardcoded.

## Documentation Routing

These docs are NOT auto-loaded — read the relevant one before working in that area:

| Working on | Read first |
|---|---|
| Model Gateway (`core/ai_gateway/`), providers, contracts, routing, health, fallbacks | docs/ARCHITECTURE.md |
| Classifier (`core/classifier.py`, `core/tfidf_classifier.py`, `core/classification_cache.py`, `config/themes.py`), gate stats | docs/ARCHITECTURE.md — "The 4-pass waterfall" |
| Summariser (`core/summariser.py`, `core/non_llm_summariser.py`, `core/gemini_client.py`, `core/provenance.py`) | docs/ARCHITECTURE.md — "Dual-Engine Summarization Detail" |
| Pipeline, `core/bg_refresher.py`, memory system, quota/LLMClient, caching | docs/ARCHITECTURE.md |
| Evaluation (`core/evaluator.py`, `core/eval_controller.py`, `core/auto_remediation.py`, page 7) | docs/QUALITY_EVALUATION.md |
| Any page/UI (`pages/*.py`, `app.py`, `core/shared_sidebar.py`, `core/design_system.py`, `core/sage_agent.py`, `core/visualiser.py`) | docs/PAGES.md |
| Config (`config/settings.py`, secrets, env vars, `custom_settings.json`, `custom_keywords.json`, `watch.md`) | docs/CONFIGURATION.md |
| Supabase (`core/supabase_client.py`, `supabase_*.sql`, schema, migrations, RLS, app_settings) | docs/SUPABASE.md |

Human-facing guides: README.md (quick start), SUPABASE_SETUP_GUIDE.md, SUPABASE_INTEGRATION_README.md. Test-targeting cheat sheet: AGENTS.md.
