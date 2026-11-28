# Pages Reference

All pages share `core/shared_sidebar.py` (nav + background-refresh status) and
`core/design_system.py` (adaptive CSS tokens, `sanitize_summary_html()`).

The sidebar nav is **grouped** (Daily intelligence / Sage & memory / Quality &
evidence / Project) with caption separators; the "Sage & memory" group holds
both **Sage** (`pages/11_Sage.py`) and **Memory Wiki** (`pages/5_History.py`). Each page
calls `render_sidebar_nav(current=<nav key>)` so its own link renders bold
with a ▸ active marker (keys live in `_NAV_GROUPS`). The summarisation-engine
selectbox sits in a collapsed "⚙️ Engine Settings" expander; the mode still
persists in `st.session_state["summariser_mode"]`.

## `app.py` — entry point

Entry point, ingestion state machine, Thematic Pulse dashboard. The header
carries a plain-language "what AI Pulse does" intro; a "🧭 New to AI Pulse?
Start here" expander (visible during first ingestion too) explains the daily
pipeline and links every page in three groups; each Intelligence Preview
expander links to Deep Dive and Sage.

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

Two tabs (the Ask Sage tab moved to `pages/11_Sage.py` in Phase 7):

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

Sage internals (assembly, retrieval, citations, judging, conversations) are
documented in **[docs/SAGE.md](SAGE.md)** — the focused reference for all
Sage & Memory Wiki work.

## `pages/6_Trend_Analytics.py`

Historical thematic momentum & theme drilldown timeline.

## `pages/7_Quality_Evaluation.py`

Evaluation engine scoring 7 metrics (3 LLM-as-judge routed through the gateway `EVALUATE`
task + 4 deterministic judges, `ThreadPoolExecutor` in `core/evaluator.py`) with live
progress panel, in-app remediation, and opt-in auto-remediation. Phase 8 adds the
**🛡️ Sage grounding guard toggle** (auto-tighten/revert on the rolling faithfulness
baseline, status card) and the **🔬 deep-research pipeline activity** section; the
Sage Faithfulness section gained an answer-mode filter (fast/deep/untagged).

**🔮 Sage Faithfulness section (Phase 2 gate):** independent of the evaluation runs —
renders the chat-time judge's `sage_evaluations` rows (rolling mean over the last 20
replies, per-reply score line, and a recent unsupported-claims expander), with an
empty state pointing at the chat tab and the required migration.

→ Full execution model (EvaluationRunner, checkpointing, pause/resume, Judge Model
selector, keyword suggestions): [docs/QUALITY_EVALUATION.md](QUALITY_EVALUATION.md).

## `pages/8_Feedback_&_Roadmap.py`

Feature requests / bug reports / UX ideas persisted to the `user_feedback` table, plus SDD
writing prompts. Phase 8.3 adds a **🔮 Sage reply ratings** section: 👍/👎 counts and
recent ratings with corrections from `sage_reply_feedback` (the human signal feeding
Sage's exemplar corpus).

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

## `pages/11_Sage.py` — Sage (Phase 7)

The dedicated conversational research analyst, extracted from the Memory
Wiki page. **Canonical reference: [docs/SAGE.md](SAGE.md)** — read it before
any Sage change. Summary:

- Conversation bar (load/new/save/export/delete; `core/sage_conversations.py`),
  Research Scope expander (period presets / theme / source), Active Scope badge.
- **🔬 Deep research toggle** (default off, per-session): routes replies through
  `core/sage_pipeline.chat_with_sage_deep` — Scout → per-sub-query retrieval →
  Analyst draft → Fact-Checker → bounded revision. A "🔬 Pipeline detail"
  expander shows sub-queries, the stage table, flagged claims, and the
  correlation/trace ids.
- Captions per reply: engine provenance, grounding stats (incl. Tier-3 rollup
  note), pipeline summary (deep mode); 📰 Article Evidence popover (deep mode
  uses the pipeline's merged evidence list).
- Chat-time faithfulness judge fires in both modes — deep mode judges against
  the pipeline's rebuilt context (`result["context"]`); rows carry `answer_mode`.
- **👍/👎 + correction** on every reply (Phase 8.3): ratings persist to
  `sage_reply_feedback` and ride on the message dict; down-ratings with
  corrections become negative exemplars in Sage's prompt (Phase 8.1).
- Requires Supabase; local mode shows a connect notice. Session keys `sage_*`.
