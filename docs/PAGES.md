# Pages Reference

All pages share `core/shared_sidebar.py` (nav + background-refresh status) and
`core/design_system.py` (adaptive CSS tokens, `sanitize_summary_html()`).

## `app.py` — entry point

Entry point, ingestion state machine, Thematic Pulse dashboard.

## `pages/1_Overview.py`

Theme summary cards with provenance chips and key takeaways.

## `pages/2_Deep_Dive.py`

Per-theme article list, full summaries, on-demand Gemini re-summarisation, further reading.
Coverage caption always shows "N of M reflected · K left unprocessed" (even at K = 0).

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
  routed through the Model Gateway (`sage_chat` policy: Qwen Cloud primary, Gemini
  fallbacks, per-model health tracking). Each reply is captioned with the engine
  provenance (provider/model, latency, fallback flag) and the grounding stats
  (summaries/dates plus article-evidence count, and — Phase 6.2 — "🗓️ N monthly
  rollups (range)" when Tier-3 month blocks grounded the answer).
- **📖 Memory Timeline** — run-level caption ("🆕 N new unprocessed articles this
  run · ✅ M summarised · ⏳ K waiting", from `compute_run_processing_stats()`);
  per-theme cards/expanders always show "N of M reflected · K left unprocessed"
  (even when K = 0 — full coverage is stated, not implied), the ⏳ Missed/Backlog
  list, and the Phase 4.3 🍂 Aged-out Unprocessed list (waiting articles that left
  the fetch window before ever being summarised, with last-seen run). A collapsed
  **🗓️ Monthly Syntheses (Tier 3)** expander (Phase 6.1) lists the monthly macro
  rollups per theme (months descending, theme-filter aware): macro prose, a
  🔑 Key milestones sub-expander (date — milestone, verbatim source links), and an
  "Updated … · covers N runs · engine" caption. Read-only; silent when the
  `monthly_syntheses` table is empty or missing.
- **⚖️ Compare Runs**

### Sage agent internals (`core/sage_agent.py`)

- Assembles a relevance-ranked, character-budgeted context string from cross-run
  summaries (`get_summaries_across_runs()`) with first-appearance annotations, then
  routes the fully-formed prompt (persona + wiki context + conversation) through
  the Model Gateway — no deterministic fallback, so total engine failure surfaces
  as a friendly notice string (quota exhaustion gets a dedicated 429 notice).
- **Tier-aware assembly (Phase 6.2):** older completed months with
  `monthly_syntheses` rows render as Tier-3 macro rollup blocks (prose + dated
  milestones with real links) instead of condensed per-date blocks; the current
  month and the recent-2-dates full tier stay Tier-2. The hybrid article leg gets
  a milestone-biased query (the drill-down); the grounding caption appends
  "🗓️ N monthly rollups (range)". Kill switch `sage_tier3_enabled`
  (`get_sage_settings()`); details in docs/ARCHITECTURE.md.
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
- **Hybrid retrieval (Phase 5):** with the real Supabase client and a
  question, `core/retrieval.hybrid_search()` replaces the keyword-overlap
  scorer: FTS + pgvector dense legs fused by RRF, temporal bounds parsed from
  the question, bounded time-decay for unbounded queries. Results arrive
  pre-ranked (`_rrf_score`) and `_select_articles` preserves that order.
  Any failure/empty result falls through to the Phase 2.3 FTS path above.
  First-appearance tags come from the `theme_first_seen` table (true archive
  dates), not the window scan. Details: [ARCHITECTURE.md — Hybrid Retrieval](ARCHITECTURE.md#hybrid-retrieval-phase-5--coreretrievalpy).
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

## `pages/9_Eval_Sets.py`

Browsable registry of every git-tracked evaluation question set in `eval_sets/*.json`
(`core/eval_sets.py` discovers them — adding a JSON file with the schema documented in
that module makes it appear here automatically; this is the standing home for all future
eval sets).

- Set selector + metadata (description, metric, created date, runner command).
- **Pagination: 20 questions per page** with Previous/Next and a page indicator;
  page state is per-set in `st.session_state`.
- Each question card shows the question, its ground truth (`expected_keywords` for
  retrieval sets, `answer` for QA-style sets), the `target_title` provenance article,
  notes, and the latest per-question score when a run snapshot exists.
- Latest run summary comes from `app_settings` via the set's `results_key`
  (retrieval set → `retrieval_eval_latest`, written by `python -m core.retrieval_eval`;
  Sage macro-faithfulness set → `sage_eval_latest`, written by
  `python -m core.sage_eval --gate`). Snapshots carry `set_id`, `ran_at`, and
  `per_question` scores. Score rendering is kind-agnostic (`result_score` /
  `summary_mean` in `core/eval_sets.py`): recall for retrieval sets, faithfulness
  score + context chars + latency (+ legacy-arm comparison) for the Phase 6.2 set;
  a ✅/❌ gate-verdict caption appears when the snapshot carries `gate_passed`.
  Offline or never-run sets degrade gracefully to questions without scores.

## `pages/10_Blog.py`

Searchable rendering of `BLOG.md` — the development journal, newest first.
`core/blog_index.py` splits the file on `## <date range> — <title>` headings and
provides case-insensitive AND search across entry titles + bodies (substring terms,
so partial words match). Results render inline (no expanders) so matches are
immediately readable; the parse is cached with a 5-minute TTL keyed on file mtime.

**Labels**: each entry carries a `Labels: a, b, c` line immediately after its
heading (parsed into `entry["labels"]`, stripped from the rendered body, shown
as a 🏷️ caption). A multiselect filters by label with **OR** semantics (any
selected label matches), intersected with the search's AND terms
(`filter_entries`). Taxonomy: `explainer, sage, retrieval, pipeline, gateway,
evals, supabase, reliability, ui`. Entries without the line degrade to no labels.
