# Quality Evaluation

The evaluation suite (`core/evaluator.py`) runs **7 automated checks** to ensure news summaries and theme classifications remain accurate, faithful, and non-repetitive over time.

## Execution Architecture

- **3 Concurrent LLM Judges**: Run inside a `ThreadPoolExecutor` (3 workers), each routed
  through the **Model Gateway** (`TaskType.EVALUATE` — Gemini-first, Ollama fallback,
  no deterministic fallback), so an Ollama quota outage no longer disables them:
  - *Categoriser Judge* (fresh theme re-classification sample)
  - *Faithfulness Judge* (fact-checking summary claims against source articles)
  - *Uniqueness Judge* (pairwise summary overlap across themes and runs)

- **4 Sub-Millisecond Deterministic Judges**: Zero-cost rule-based checks running alongside:
  - *Grounding Judge* (citation matching)
  - *Structural Compliance Judge* (section, sentence, and list formatting bounds)
  - *Coverage Judge* (source article recall)
  - *Temporal Coherence Judge* (week-over-week summary evolution tracking)

## Judge Failure Semantics

LLM judge calls that error out or return unparseable responses are **excluded** from the
metric and counted in `raw_metrics` (`infra_errors` / `parse_failures` / `unmatched` /
`failed_pairs`) — never scored as zeros. An Ollama outage must not masquerade as mass
hallucination (faithfulness) or as perfectly distinct summaries (uniqueness). A judge that
could score nothing at all is marked `skipped`; the Score History chart renders skipped
points as gaps instead of the persisted 1.0 placeholder.

## Judge Metrics

| Metric | Judge Type | What It Checks | Technical Method | Target |
|---|---|---|---|---|
| **Categoriser Accuracy** | LLM-as-Judge | Are articles being sorted into the right themes? | Re-classifies a stratified sample via LLM, compares against active assignments. | ≥ 80% |
| **Faithfulness Score** | LLM-as-Judge | Are summaries truthful without hallucination? | Extracts claims from bullet points, fact-checks against source articles. | ≥ 80% |
| **Uniqueness Score** | Hybrid Heuristic + LLM | Are summaries distinct across themes/runs? | Jaccard/cosine text overlap filtering; LLM pairwise judge for ambiguous bands. | ≥ 80% |
| **Grounding Score** | Deterministic | Do "Further Reading" links point to real articles? | Cross-references cited titles/links against the input source set. Reports *Skipped* (not a vacuous 100%) when a run has no persisted citations — requires `supabase_migration_further_reading.sql`. | 100% |
| **Structural Compliance** | Deterministic | Is the summary properly formatted? | Validates 5 mandatory sections and sentence count bounds (3–7) on prose. Sections absent from a legacy schema are skipped, not failed. | 100% |
| **Coverage Score** | Deterministic | Did the summary capture key information from all articles? | Source article title/entity token recall across the generated summary. | ≥ 70% |
| **Temporal Coherence** | Deterministic | Is the summary updating week-over-week? | Compares active summary against past runs to flag stale text repetition. | ≥ 75% |

## In-App Remediation

When scores fall below threshold, users fix issues directly in the UI — no backend code edits needed:

1. **⚡ 1-Click "Apply All Suggested Keywords"** — When Categoriser Accuracy is low, the evaluation engine identifies missing high-signal keywords. Click to add them with disk persistence.

2. **🎛️ Theme Keyword Manager** — Add new keywords with customizable weights (1–3) or remove weak keywords for any theme. Persists to `config/custom_keywords.json` and hot-reloads the classifier.

3. **⚙️ Faithfulness & Summariser Tuner** — Adjust temperature, max tokens, and toggle strict anti-hallucination grounding mode. Persists to `config/custom_settings.json`.

4. **📌 Watchlist Term Suggestions** — Surfaces high-signal terms for `watch.md` in copy-pasteable blocks.

Suggestions persist to the `keyword_suggestions` Supabase table (run `supabase_migration_keywords.sql` once to create it). Set `LLM_DEBUG=1` in `.env` to dump prompts and raw responses for debugging.

Grounding and Structural Compliance read the persisted `further_reading` column — run
`supabase_migration_further_reading.sql` once on existing deployments.

## Auto-Remediation (opt-in, bounded, reversible)

The **"🤖 Auto-apply remediations"** toggle on the Quality Evaluation page closes the
evaluate → remediate loop (`core/auto_remediation.py`, persisted in
`custom_settings.json` as `auto_remediation_enabled`):

| Trigger | Action | Bound |
|---|---|---|
| Faithfulness < threshold | Strict anti-hallucination mode ON, temperature → 0.1 | Never raises temperature back |
| Coverage < threshold | `max_tokens` += 500 | Hard cap 2500 |
| Per-theme classifier < threshold | Apply evaluation keyword suggestions | ≤ 5 terms/theme/round, weight ≤ 2 (weight-3 stays human-approved), one experiment per theme at a time |

**Rollback**: each keyword apply is recorded in `data/auto_remediation_state.json` with the
theme's baseline score. On the next evaluation, a theme whose score *dropped* below its
baseline has the auto-applied terms removed, and that theme is skipped in the same round's
apply pass (no oscillation). Skipped judges produce no signal, so pending experiments wait.

Every action (applied or rolled back) is recorded in the evaluation's `raw_metrics`
(`auto_remediation` key) and persists to Supabase for audit. Remediations take effect at
runtime — keyword and settings writes go through the same hot-reload paths as the manual
UI controls.

## Page 7 Execution Model

Runs are **user-triggered on-demand**. The ISO-week-guarded background thread
(`core/weekly_evaluator.py`) exists, but `maybe_start_weekly_evaluator()` is currently a
no-op.

### Runner, checkpointing & crash-safe resume

- Evaluations run on the **`EvaluationRunner` worker** (`core/eval_controller.py`,
  `sys._aipulse_eval_state` — survives page navigation/reruns) with every successful judge
  item checkpointed to `data/evaluation_checkpoint.json` (atomic writes; only *successful*
  judgments are cached — failures retry on resume; faithfulness keys by
  `(run_id, item_id)`).
- The page offers **Pause/Resume/Discard**; resume replays cached items and pins the
  original run set/config (`set_run_ids` at start).
- If the final `quality_evaluations` insert fails, the finished report sits in the
  checkpoint and the worker retries every `EVAL_DB_RETRY_SECONDS` (status `awaiting_db`)
  until the DB recovers or the user pauses; `mark_completed` (status write, then file
  delete) is the double-insert guard.
- The `awaiting_db` banner shows the **actual insert error** (`EvalControl.set_db_error`,
  fed by `insert_quality_evaluation(on_error=...)`) so a schema mismatch is diagnosable
  from the UI instead of hiding behind a generic retry loop.

### 🧠 Judge Model selector

The page's **🧠 Judge Model** selector (`EVALUATION_JUDGE_MODELS` in `config/settings.py`)
pins every LLM judge call to ONE gateway model via `AITaskRequest.preferred_model` (no
cross-model fallback; unknown/unconfigured model falls back to default routing with a
WARNING) so scores stay comparable across evaluations; the choice is recorded in
`raw_metrics.judge_model`.

### Failure surfacing

Judge infra failures are **excluded from scores** (counted in `raw_metrics`), skipped
judges render as chart gaps, and the grounding/structural judges need
`supabase_migration_further_reading.sql`.

### Pending keyword suggestions

A standing "Pending keyword suggestions" section re-surfaces every pending
`keyword_suggestions` row (from evaluations AND the classifier's heuristic
auto-improvement) with Apply/Dismiss buttons (`update_keyword_suggestion_status()` in
`core/quality_schema.py`) — the one-time result panel is session-only, this section is the
durable surface.

### Tuner persistence

The Faithfulness & Summariser Tuner and the auto-remediation state persist through
`config/custom_settings.json` / `data/auto_remediation_state.json`, both **Supabase-backed**
via the `app_settings` table so they survive app restarts and Streamlit Cloud redeploys —
see [docs/CONFIGURATION.md](CONFIGURATION.md#summariser-tuner-settings--configcustom_settingsjson).
