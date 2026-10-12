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
  routed through the Model Gateway (`sage_chat` policy: Ollama primary, Gemini
  fallbacks, per-model health tracking). Each reply is captioned with the engine
  provenance (provider/model, latency, fallback flag) and the grounding stats
  (summaries/dates plus article-evidence count).
- **📖 Memory Timeline**
- **⚖️ Compare Runs**

### Sage agent internals (`core/sage_agent.py`)

- Assembles a relevance-ranked, character-budgeted context string from cross-run
  summaries (`get_summaries_across_runs()`) with first-appearance annotations, then
  routes the fully-formed prompt (persona + wiki context + conversation) through
  the Model Gateway — no deterministic fallback, so total engine failure surfaces
  as a friendly notice string (quota exhaustion gets a dedicated 429 notice).
- **Tier-1 article evidence (Phases 2.1/2.3):** alongside the summary narrative,
  articles are fetched via `SupabaseManager.get_articles_for_context()` — with
  the question passed as `query`, keyword matching runs in Postgres FTS (the
  `match_articles` RPC over `articles.search_vector`, ranked by `ts_rank`);
  with no match or a pre-migration database it degrades to a bounded
  newest-first window. Local fallback (`themed_articles` from history.json)
  has no FTS and relies on client-side scoring. Either way the candidates are
  scored client-side (`score_article_relevance`, titles ×2) for the final
  top-K, which render as an `=== ARTICLE EVIDENCE ===` section capped at
  ~30% of the char budget, with unused budget flowing back to the summaries.
  With no keyword matches the selection degrades to newest-first, so Sage
  always has some article-level grounding.
- **Article citations (Phase 2.2):** each brief carries a `Link:` line
  (sanitised to http(s) via `_safe_link` — scraped URLs are untrusted), and
  the persona prompt instructs Sage to attach those links to article
  citations as Markdown hyperlinks (never inventing URLs). The rendered
  briefs also flow back as `build_wiki_context()["articles"]`, which the
  chat tab renders as a `📰 Article Evidence (N)` accordion under each
  assistant message (`_render_sage_article_evidence`). The list is persisted
  as an `articles` key on the message dict, so evidence popovers survive
  conversation reloads.
- Response structure (Phase 2.3 prompt rework): (1) a **Direct Answer** —
  1-3 sentences answering the question head-on, no preamble; (2) **Evidence**
  — for trend questions a chronological account with `[Theme · Run YYYY-MM-DD]`
  citations (articles cite `[Theme · Source · Date]`, hyperlinked when a
  source link exists), with partial evidence answered at its stated scope and
  conflicting runs surfaced with dates rather than smoothed; then (3) a
  "My read on this" assessment.
- Multi-turn state lives in `st.session_state.sage_messages`.
- **Chat-time faithfulness judging (Phase 2 gate):** every successful reply
  (LLM answers only — error/quota notices are skipped) is judged in the
  background by `record_sage_faithfulness()` against the exact context it
  was given; fire-and-forget on a daemon thread, so the chat never blocks.
  Scores land in `sage_evaluations` and surface on page 7.
- Persistent conversation threads are stored in Supabase and locally
  (`core/sage_conversations.py`).

## `pages/6_Trend_Analytics.py`

Historical thematic momentum & theme drilldown timeline.

## `pages/7_Quality_Evaluation.py`

Evaluation engine scoring 7 metrics (3 LLM-as-judge routed through the gateway `EVALUATE`
task + 4 deterministic judges, `ThreadPoolExecutor` in `core/evaluator.py`) with live
progress panel, in-app remediation, and opt-in auto-remediation.

**🔮 Sage Faithfulness section (Phase 2 gate):** independent of the evaluation runs —
renders the chat-time judge's `sage_evaluations` rows (rolling mean over the last 20
replies, per-reply score line, and a recent unsupported-claims expander), with an
empty state pointing at the chat tab and the required migration.

→ Full execution model (EvaluationRunner, checkpointing, pause/resume, Judge Model
selector, keyword suggestions): [docs/QUALITY_EVALUATION.md](QUALITY_EVALUATION.md).

## `pages/8_Feedback_&_Roadmap.py`

Feature requests / bug reports / UX ideas persisted to the `user_feedback` table, plus SDD
writing prompts.
