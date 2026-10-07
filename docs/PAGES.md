# Pages Reference

All pages share `core/shared_sidebar.py` (nav + background-refresh status) and
`core/design_system.py` (adaptive CSS tokens, `sanitize_summary_html()`).

## `app.py` — entry point

Entry point, ingestion state machine, Thematic Pulse dashboard.

## `pages/1_Overview.py`

Theme summary cards with provenance chips and key takeaways.

## `pages/2_Deep_Dive.py`

Per-theme article list, full summaries, on-demand Gemini re-summarisation, further reading.

## `pages/3_Keyword_Analysis.py`

- Keyword velocity analytics: theme filter, top-10 auto-plot, `canonicalize_word()`
  singular/plural merging, low-signal stopwords.
- Theme word clouds.
- `canonicalize_word()` is exported by `core/visualiser.py` (merges plurals: `agents` →
  `agent`); used by `extract_top_words()` and this page.

## `pages/4_Sources.py`

Full source list with links.

## `pages/5_History.py` — Memory Wiki

Three tabs:

- **🔮 Ask Sage** — conversational agent grounded in wiki data (`core/sage_agent.py`),
  automatic Gemini fallback when the primary LLM fails or is quota-blocked.
- **📖 Memory Timeline**
- **⚖️ Compare Runs**

### Sage agent internals (`core/sage_agent.py`)

- Assembles a relevance-ranked, character-budgeted context string from cross-run
  summaries (`get_summaries_across_runs()`) with first-appearance annotations, then calls
  the primary LLM with automatic Gemini fallback.
- Response structure: (1) a chronological account with `[Theme · Run YYYY-MM-DD]`
  citations, then (2) a "My read on this" assessment.
- Multi-turn state lives in `st.session_state.sage_messages`.
- Persistent conversation threads are stored in Supabase and locally
  (`core/sage_conversations.py`).

## `pages/6_Trend_Analytics.py`

Historical thematic momentum & theme drilldown timeline.

## `pages/7_Quality_Evaluation.py`

Evaluation engine scoring 7 metrics (3 LLM-as-judge routed through the gateway `EVALUATE`
task + 4 deterministic judges, `ThreadPoolExecutor` in `core/evaluator.py`) with live
progress panel, in-app remediation, and opt-in auto-remediation.

→ Full execution model (EvaluationRunner, checkpointing, pause/resume, Judge Model
selector, keyword suggestions): [docs/QUALITY_EVALUATION.md](QUALITY_EVALUATION.md).

## `pages/8_Feedback_&_Roadmap.py`

Feature requests / bug reports / UX ideas persisted to the `user_feedback` table, plus SDD
writing prompts.
