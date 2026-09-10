# Quality Evaluation

The evaluation suite (`core/evaluator.py`) runs **7 automated checks** to ensure news summaries and theme classifications remain accurate, faithful, and non-repetitive over time.

## Execution Architecture

- **3 Concurrent LLM Judges**: Run inside a `ThreadPoolExecutor` bounded by `Semaphore(3)` to prevent API rate limiting:
  - *Categoriser Judge* (fresh theme re-classification sample)
  - *Faithfulness Judge* (fact-checking summary claims against source articles)
  - *Uniqueness Judge* (pairwise summary overlap across themes and runs)

- **4 Sub-Millisecond Deterministic Judges**: Zero-cost rule-based checks running alongside:
  - *Grounding Judge* (citation matching)
  - *Structural Compliance Judge* (section, sentence, and list formatting bounds)
  - *Coverage Judge* (source article recall)
  - *Temporal Coherence Judge* (week-over-week summary evolution tracking)

## Judge Metrics

| Metric | Judge Type | What It Checks | Technical Method | Target |
|---|---|---|---|---|
| **Categoriser Accuracy** | LLM-as-Judge | Are articles being sorted into the right themes? | Re-classifies a stratified sample via LLM, compares against active assignments. | ≥ 80% |
| **Faithfulness Score** | LLM-as-Judge | Are summaries truthful without hallucination? | Extracts claims from bullet points, fact-checks against source articles. | ≥ 80% |
| **Uniqueness Score** | Hybrid Heuristic + LLM | Are summaries distinct across themes/runs? | Jaccard/cosine text overlap filtering; LLM pairwise judge for ambiguous bands. | ≥ 80% |
| **Grounding Score** | Deterministic | Do "Further Reading" links point to real articles? | Cross-references cited titles/links against the input source set. | 100% |
| **Structural Compliance** | Deterministic | Is the summary properly formatted? | Validates 5 mandatory sections and sentence count bounds (3–7) on prose. | 100% |
| **Coverage Score** | Deterministic | Did the summary capture key information from all articles? | Source article title/entity token recall across the generated summary. | ≥ 70% |
| **Temporal Coherence** | Deterministic | Is the summary updating week-over-week? | Compares active summary against past runs to flag stale text repetition. | ≥ 75% |

## In-App Remediation

When scores fall below threshold, users fix issues directly in the UI — no backend code edits needed:

1. **⚡ 1-Click "Apply All Suggested Keywords"** — When Categoriser Accuracy is low, the evaluation engine identifies missing high-signal keywords. Click to add them with disk persistence.

2. **🎛️ Theme Keyword Manager** — Add new keywords with customizable weights (1–3) or remove weak keywords for any theme. Persists to `config/custom_keywords.json` and hot-reloads the classifier.

3. **⚙️ Faithfulness & Summariser Tuner** — Adjust temperature, max tokens, and toggle strict anti-hallucination grounding mode. Persists to `config/custom_settings.json`.

4. **📌 Watchlist Term Suggestions** — Surfaces high-signal terms for `watch.md` in copy-pasteable blocks.

Suggestions persist to the `keyword_suggestions` Supabase table (run `supabase_migration_keywords.sql` once to create it). Set `LLM_DEBUG=1` in `.env` to dump prompts and raw responses for debugging.
