# Weekly Quality Evaluation Module for AI Pulse

## Context

AI Pulse runs a pipeline (fetcher → classifier → summariser) and persists runs to Supabase. Today there is no automated way to know whether the **classifier** is routing articles to the right theme, or whether the **summariser** is producing accurate, non-hallucinated, non-duplicate briefs. As the project grows we need a recurring, **measurable** quality gate that:

1. Scores the **classifier** (categorisation quality)
2. Scores the **summariser** (faithfulness + uniqueness)
3. Plots the scores over time
4. **Recommends concrete actions** when either score drops below an 80% threshold

This will be a new module triggered **on-demand** from a Streamlit page, and a **weekly scheduled trigger** that runs in the background after the first run of the week has been recorded. It uses the same LLM (Ollama) as the rest of the app for "judge" calls.

---

## Design Decisions (confirmed with user)

- **Scoring approach**: reference-free — three LLM judge agents compare classifier output to a fresh classification, and summaries to source articles. No ground-truth labels needed.
- **Sample size**: 20 articles per run for the categoriser judge (140 calls/week at 7 runs).
- **Persistence**: new Supabase table `quality_evaluations`.
- **Trigger**: on-demand button on the new page **plus** a `WeeklyEvaluator` background thread that fires once per ISO week if the latest trend run is at least 1h old.
- **Threshold**: configurable in the page UI per evaluation (default 80%); the chosen threshold is stored with each evaluation. The default lives in `config/settings.py` as `QUALITY_THRESHOLD = 0.80`.
- **Missing Supabase**: block with a clear error message on the page; weekly auto-run logs and skips.
- **Multi-agent**: three judge agents run concurrently via `concurrent.futures.ThreadPoolExecutor`, matching the existing `BackgroundRefresher` style.

---

## File Plan

### New files
- `core/evaluator.py` — the engine: data loading, three judge agents, scoring, aggregation, recommendations
- `core/quality_schema.py` — Supabase table creation SQL + DDL constant
- `pages/8_Quality_Evaluation.py` — Streamlit page with controls + plots
- `core/weekly_evaluator.py` — background weekly scheduler
- `tests/test_evaluator.py` — basic smoke tests (deterministic metric aggregation, no LLM calls)

### Modified files
- `core/bg_refresher.py` — start the `WeeklyEvaluator` alongside the trend refresher
- `config/settings.py` — add `QUALITY_THRESHOLD = 0.80` (page default), `EVALUATION_SAMPLE_SIZE = 20`, `EVALUATION_MAX_RUNS = 7`
- `supabase_schema.sql` — append `quality_evaluations` table DDL (includes `threshold` column so per-evaluation overrides are stored)
- `requirements.txt` — already has `plotly`, `pandas`, `supabase` — no new deps needed

---

## Module: `core/evaluator.py`

### Public API
```python
def run_weekly_evaluation(lookback_days: int = 7, threshold: float = QUALITY_THRESHOLD) -> EvaluationReport
def run_evaluation_for_runs(run_ids: List[str], threshold: float = QUALITY_THRESHOLD) -> EvaluationReport
def load_evaluation_history(limit: int = 12) -> pd.DataFrame
```

### `EvaluationReport` dataclass
```python
@dataclass
class EvaluationReport:
    run_ids: List[str]
    run_timestamps: List[str]
    threshold: float                  # 0..1, used for this evaluation
    classifier_score: float          # 0..1
    faithfulness_score: float        # 0..1
    uniqueness_score: float          # 0..1
    per_theme_classifier: Dict[str, float]
    per_run_scores: List[Dict]       # for time-series plot
    recommendations: List[str]       # human-readable action items
    raw_metrics: Dict                # full JSON for the DB
    generated_at: datetime
```

### Data loading
- Query Supabase `trend_runs` where `run_timestamp >= now() - lookback_days`, order desc, cap at `EVALUATION_MAX_RUNS = 7`.
- For each run, fetch its `articles` (id, theme_name, title, summary).
- If Supabase unavailable, fall back to `history.json` (only the latest run will be available — surface a clear "Supabase required for weekly eval" message).

### Three judge agents (run in parallel via ThreadPoolExecutor)

**1. CategoriserJudge**
- For each evaluated run, take a stratified sample of `EVALUATION_SAMPLE_SIZE = 20` articles (round-robin across themes).
- For each article, call the LLM with the same 7-theme prompt used by `classifier.classify_with_ollama`, asking it to return the canonical theme name.
- Compare predicted vs assigned `theme_name`. Score per article = 1.0 if match, 0.0 if not.
- Aggregate: `classifier_score = mean(scores)`. Also break down `per_theme_classifier = {theme: mean(scores_for_theme)}`.

**2. FaithfulnessJudge**
- For each run, sample 3 summaries (one section each: "what_is_happening", "engineering_tradeoffs", "product_impact").
- For each summary, build a prompt: "Here is a theme summary:\n{summary}\nHere are the source articles:\n{articles_text}\nScore faithfulness from 0..1: does the summary make any claim NOT supported by the source articles? Return a JSON object: {score: float, hallucinated_claims: [str]}"
- Aggregate: `faithfulness_score = mean(scores)` across all samples.

**3. UniquenessJudge**
- For each run, take all 7 theme summaries.
- For each pair of summaries in the same run, ask the LLM to score overlap from 0..1 (1 = duplicate).
- Also compare across runs: for each run, compare its summaries to the *previous run's* summaries for the same theme, score 0..1.
- `uniqueness_score = 1 - mean(pairwise_overlap)`. High score = summaries are distinct.

### Recommendation engine
```python
def generate_recommendations(report) -> List[str]:
    threshold = report.threshold
    recs = []
    if report.classifier_score < threshold:
        worst_themes = sorted(report.per_theme_classifier.items(), key=lambda x: x[1])[:2]
        recs.append(
            f"⚠️ Classifier score {report.classifier_score:.0%} < {threshold:.0%}. "
            f"Weakest themes: {', '.join(t for t,_ in worst_themes)}. "
            f"Action: review keywords in config/themes.py for these themes and add new high-signal terms."
        )
    if report.faithfulness_score < threshold:
        recs.append(
            f"⚠️ Faithfulness score {report.faithfulness_score:.0%} < {threshold:.0%}. "
            f"Action: tighten the summariser prompt in core/summariser.py:78-112 to require source-article IDs in claims, "
            f"or reduce max_tokens to discourage fabrication."
        )
    if report.uniqueness_score < threshold:
        recs.append(
            f"⚠️ Uniqueness score {report.uniqueness_score:.0%} < {threshold:.0%}. "
            f"Action: summaries overlap too much across themes. "
            f"Review the prompt to emphasize differentiation, and check for cross-theme article leakage in core/classifier.py."
        )
    if not recs:
        recs.append(f"✅ All quality scores above {threshold:.0%}. No action required.")
    return recs
```

### Persist to Supabase
Write to new table `quality_evaluations`:
```sql
CREATE TABLE quality_evaluations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lookback_days INT NOT NULL,
    runs_evaluated JSONB NOT NULL,           -- list of run_ids
    threshold FLOAT NOT NULL,                -- 0..1, the threshold used for this eval
    classifier_score FLOAT NOT NULL,
    faithfulness_score FLOAT NOT NULL,
    uniqueness_score FLOAT NOT NULL,
    per_theme_classifier JSONB,
    recommendations JSONB,
    raw_metrics JSONB
);
```

Graceful degrade: if Supabase unavailable, return the report anyway (in-memory only), the page will warn.

---

## Module: `core/weekly_evaluator.py`

A small singleton thread, modeled after `BackgroundRefresher`:
- On `start()`, check every hour: has an evaluation been generated in the current ISO calendar week? If not, and the latest `trend_run` is at least 1h old, run `run_weekly_evaluation()` and persist.
- Idempotent — safe to call `start()` multiple times.
- Started from `core/bg_refresher.py` alongside the existing refresher, only if `is_available()` for the LLM and Supabase.

---

## Page: `pages/8_Quality_Evaluation.py`

- **Header**: page icon 🔬, title "Quality Evaluation"
- **Controls** (sidebar):
  - "Threshold" slider 0.50–0.99, default = `QUALITY_THRESHOLD` from settings (the value used for the next run)
  - "Lookback days" radio: 1 / 7 (default) / 14 / 30
- **Run button**: "▶ Run evaluation now" — calls `run_weekly_evaluation(threshold=current_threshold, lookback_days=current_lookback)` and shows a spinner with multi-agent progress
- **Score tiles**: three large metric tiles for Classifier / Faithfulness / Uniqueness with the chosen threshold shown as a colored bar (green if above, red if below)
- **Time-series plot**: Plotly line chart of all three scores over evaluation history (last 12 evaluations), threshold line drawn at the most-recently-used threshold
- **Per-theme classifier heatmap**: plotly heatmap (theme × run) for misclassification rates
- **Recommendations panel**: rendered as a Streamlit `st.warning` / `st.success` block, one entry per failed criterion
- **Empty state**: if no evaluations exist yet, prompt the user to run one
- **Supabase-missing guard**: if Supabase is unavailable, show a `st.error` with a clear message and disable the run button

---

## Verification

1. **Unit test** (`tests/test_evaluator.py`): mock the LLM and Supabase, verify that
   - `generate_recommendations()` returns the correct warnings when each score is below the chosen threshold (and only the warnings for the failed criteria)
   - the score aggregator returns a correct mean
   - the `WeeklyEvaluator` does not double-run within the same ISO week
2. **Smoke test**: run `pytest tests/test_evaluator.py`
3. **Schema check**: run the new DDL in Supabase SQL editor; confirm `quality_evaluations` table is created with the `threshold` column
4. **Manual end-to-end**: open the new page, slide threshold to 0.70, click "Run evaluation now" with Supabase populated; confirm scores appear, threshold line moves to 0.70, recommendations reflect the new threshold
5. **Linting**: `python -c "import ast; ast.parse(open('core/evaluator.py').read())"` for all new files
