"""
Weekly quality evaluation engine for AI Pulse.

Three LLM judge agents run in parallel to score recent runs:

1. **CategoriserJudge** — re-classifies a stratified sample of articles per run
   and compares against the theme that was originally assigned.  Output:
   `classifier_score` (mean accuracy) and `per_theme_classifier` (per-theme
   accuracy).

2. **FaithfulnessJudge** — for each run, samples three summary sections
   ("what_is_happening", "engineering_tradeoffs", "product_impact") and asks
   the LLM to score 0..1 how well the claims are supported by the source
   articles.  Output: `faithfulness_score` (mean).

3. **UniquenessJudge** — for each run, asks the LLM to score pairwise
   overlap (0..1) between all theme summaries *in the same run* and against
   the same theme in the previous run.  Output: `uniqueness_score` (1 minus
   mean overlap).

The three judges run concurrently via `concurrent.futures.ThreadPoolExecutor`,
matching the project's existing threading style (`BackgroundRefresher`).
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd

from config.settings import (
    EVALUATION_MAX_RUNS,
    EVALUATION_SAMPLE_SIZE,
    QUALITY_THRESHOLD,
)
from config.themes import THEMES, THEME_ORDER
from core.llm_client import LLMClient, LLMClientError
from core.quality_schema import insert_quality_evaluation

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------


@dataclass
class EvaluationReport:
    run_ids: List[str]
    run_timestamps: List[str]
    threshold: float
    classifier_score: float
    faithfulness_score: float
    uniqueness_score: float
    per_theme_classifier: Dict[str, float]
    per_run_scores: List[Dict]
    recommendations: List[str]
    raw_metrics: Dict
    generated_at: datetime
    # Optional Supabase row id, populated after a successful insert.
    db_row_id: Optional[str] = None

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["generated_at"] = self.generated_at.isoformat()
        return d


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class EvaluationError(Exception):
    """Raised when an evaluation cannot be completed (e.g. no Supabase data)."""


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _load_recent_runs(supabase, lookback_days: int) -> List[Dict]:
    """Return trend_runs rows from the last `lookback_days`, newest first,
    capped at EVALUATION_MAX_RUNS.  Raises EvaluationError if Supabase is
    unavailable or no runs exist in the window.
    """
    if supabase is None or not supabase.is_available():
        raise EvaluationError(
            "Supabase is not configured.  Weekly evaluation requires Supabase "
            "to access historical runs.  See the setup guide in supabase_schema.sql."
        )

    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

    try:
        runs_resp = supabase.client.table("trend_runs") \
            .select("id, run_timestamp, run_date, total_articles") \
            .gte("run_timestamp", cutoff) \
            .order("run_timestamp", desc=True) \
            .limit(EVALUATION_MAX_RUNS) \
            .execute()
    except Exception as exc:
        raise EvaluationError(f"Failed to query trend_runs: {exc}") from exc

    runs = runs_resp.data or []
    if not runs:
        raise EvaluationError(
            f"No trend_runs in the last {lookback_days} day(s).  "
            f"Run the regular pipeline at least once before evaluating."
        )
    return runs


def _load_articles_for_run(supabase, run_id: str) -> List[Dict]:
    """Return articles for a single run, with theme_name / title / summary."""
    try:
        resp = supabase.client.table("articles") \
            .select("id, theme_name, title, summary") \
            .eq("run_id", run_id) \
            .execute()
        return resp.data or []
    except Exception as exc:
        logger.warning("Failed to load articles for run %s: %s", run_id, exc)
        return []


def _load_summaries_for_run(supabase, run_id: str) -> Dict[str, Dict]:
    """Return {theme_name: summary_dict} for a run."""
    try:
        resp = supabase.client.table("theme_summaries") \
            .select("theme_name, what_is_happening, engineering_tradeoffs, "
                    "product_impact, why_it_matters, what_to_watch") \
            .eq("run_id", run_id) \
            .execute()
        return {row["theme_name"]: row for row in (resp.data or [])}
    except Exception as exc:
        logger.warning("Failed to load summaries for run %s: %s", run_id, exc)
        return {}


# ---------------------------------------------------------------------------
# Helpers shared by the judges
# ---------------------------------------------------------------------------


def _get_llm() -> LLMClient:
    return LLMClient()


def _safe_mean(values: List[float]) -> float:
    """Return mean of a list, or 0.0 if empty."""
    return float(sum(values) / len(values)) if values else 0.0


def _stratified_sample(
    items: List[Dict],
    n: int,
    group_key: str = "theme_name",
) -> List[Dict]:
    """Take a roughly even sample across groups.  Round-robin when there are
    more groups than slots, cap when there are fewer.
    """
    if not items or n <= 0:
        return []

    # Group items
    groups: Dict[str, List[Dict]] = {}
    for it in items:
        groups.setdefault(it.get(group_key, "_unknown"), []).append(it)

    sample: List[Dict] = []
    group_names = list(groups.keys())
    idx = 0
    while len(sample) < n and idx < max((len(v) for v in groups.values()), default=0):
        for g in group_names:
            bucket = groups[g]
            if idx < len(bucket):
                sample.append(bucket[idx])
                if len(sample) >= n:
                    break
        idx += 1
    return sample


def _extract_json(text: str) -> Optional[Dict]:
    """Best-effort JSON extraction from LLM output that may include prose."""
    if not text:
        return None
    # Try direct parse first
    try:
        return json.loads(text)
    except Exception:
        pass
    # Find first {...} block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return None
    return None


def _match_theme(predicted: str) -> Optional[str]:
    """Fuzzy-match a free-form LLM theme name to one of the canonical THEMES."""
    if not predicted:
        return None
    lower = predicted.lower()
    for theme in THEMES.keys():
        if theme.lower() in lower:
            return theme
    return None


# ---------------------------------------------------------------------------
# Judge 1: Categoriser
# ---------------------------------------------------------------------------


CATEGORISER_PROMPT = """You are an AI news classifier.  Given a title and summary,
classify the article into EXACTLY one of these seven themes:

- Agentic Systems & DevTools
- Frontier Models & Benchmarks
- Hardware, Compute & LLMOps
- Enterprise Strategy & ROI
- Governance, Safety & Policy
- AI Security & Trust
- AI-Assisted Software Engineering

Return ONLY the exact theme name on a single line, nothing else.

Title: {title}
Summary: {summary}
"""


def _judge_single_classification(llm: LLMClient, article: Dict) -> Tuple[bool, str]:
    """Classify a single article via the LLM.  Returns (correct, predicted_name)."""
    try:
        response = llm.generate(
            CATEGORISER_PROMPT.format(
                title=article.get("title", ""),
                summary=(article.get("summary") or "")[:500],
            ),
            temperature=0.1,
            max_tokens=60,
        ).strip()
    except LLMClientError as exc:
        logger.warning("Categoriser judge LLM error: %s", exc)
        return (False, "")

    predicted = _match_theme(response)
    correct = bool(predicted and predicted == article.get("theme_name"))
    return (correct, predicted or "")


def categoriser_judge(
    llm: LLMClient,
    articles_by_run: Dict[str, List[Dict]],
) -> Tuple[float, Dict[str, float], Dict]:
    """Score classifier accuracy across all sampled articles.  Returns
    (overall_score, per_theme_scores, raw_metrics).
    """
    all_correct: List[bool] = []
    per_theme_total: Dict[str, int] = {t: 0 for t in THEMES}
    per_theme_correct: Dict[str, int] = {t: 0 for t in THEMES}
    per_run_breakdown: Dict[str, Dict] = {}

    for run_id, articles in articles_by_run.items():
        sample = _stratified_sample(articles, EVALUATION_SAMPLE_SIZE)
        run_correct = 0
        for art in sample:
            correct, predicted = _judge_single_classification(llm, art)
            all_correct.append(correct)
            theme = art.get("theme_name", "_unknown")
            if theme in per_theme_total:
                per_theme_total[theme] += 1
                if correct:
                    per_theme_correct[theme] += 1
            if correct:
                run_correct += 1
        per_run_breakdown[run_id] = {
            "sampled": len(sample),
            "correct": run_correct,
        }

    overall = _safe_mean([1.0 if c else 0.0 for c in all_correct])
    per_theme = {
        t: (per_theme_correct[t] / per_theme_total[t]) if per_theme_total[t] > 0 else 0.0
        for t in THEMES
    }
    raw = {
        "samples_judged": len(all_correct),
        "per_run": per_run_breakdown,
    }
    return overall, per_theme, raw


# ---------------------------------------------------------------------------
# Judge 2: Faithfulness
# ---------------------------------------------------------------------------


FAITHFULNESS_PROMPT = """You are a strict fact-checker for AI-generated summaries.

You will be given a SUMMARY (a section of a theme brief) and the SOURCE ARTICLES
that were provided to the model when it wrote the summary.  Score how faithfully
the summary reflects ONLY what the source articles support.

Rules:
- A claim is "supported" if the source articles either state it directly or
  can be reasonably inferred from them.
- A claim is "unsupported" if it introduces facts, model names, numbers, or
  events that are NOT in the source articles.
- Do not penalise rewording or condensation.  Only penalise fabricated claims.

Return a JSON object on a single line, exactly:
{{"score": <float 0.0-1.0>, "unsupported_claims": [<string>, ...]}}

Scoring guide:
- 1.0 = every claim is supported
- 0.8 = minor unsupported detail
- 0.5 = several unsupported claims
- 0.0 = heavily fabricated

SUMMARY:
{summary}

SOURCE ARTICLES:
{articles}
"""


def _format_articles_for_judge(articles: List[Dict], max_chars: int = 3000) -> str:
    """Format a list of articles into a compact text block, truncated."""
    parts: List[str] = []
    used = 0
    for i, art in enumerate(articles, 1):
        title = art.get("title", "").strip()
        summary = (art.get("summary") or "").strip()[:300]
        block = f"[{i}] {title}\n    {summary}"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n".join(parts) if parts else "(no source articles available)"


def _judge_faithfulness_one(
    llm: LLMClient,
    summary_text: str,
    articles: List[Dict],
) -> float:
    if not summary_text or not summary_text.strip():
        # Empty summaries are perfectly faithful (no claims made).
        return 1.0
    prompt = FAITHFULNESS_PROMPT.format(
        summary=summary_text[:2000],
        articles=_format_articles_for_judge(articles),
    )
    try:
        resp = llm.generate(prompt, temperature=0.1, max_tokens=300)
    except LLMClientError as exc:
        logger.warning("Faithfulness judge LLM error: %s", exc)
        return 0.0
    parsed = _extract_json(resp)
    if not parsed:
        return 0.0
    try:
        score = float(parsed.get("score", 0.0))
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, score))


def faithfulness_judge(
    llm: LLMClient,
    summaries_by_run: Dict[str, Dict[str, Dict]],
    articles_by_run: Dict[str, List[Dict]],
) -> Tuple[float, Dict]:
    """Score summary faithfulness across all runs.  Returns (overall, raw)."""
    sections_to_judge = [
        "what_is_happening",
        "engineering_tradeoffs",
        "product_impact",
    ]
    scores: List[float] = []
    raw_per_run: Dict[str, Dict] = {}

    for run_id, summaries in summaries_by_run.items():
        articles = articles_by_run.get(run_id, [])
        run_scores: List[float] = []
        # Sample at most one summary per section across themes
        themes = list(summaries.keys())[:3]
        for theme in themes:
            s = summaries[theme]
            for section in sections_to_judge:
                text = s.get(section, "")
                score = _judge_faithfulness_one(llm, text, articles)
                scores.append(score)
                run_scores.append(score)
        raw_per_run[run_id] = {"scores": run_scores}

    return _safe_mean(scores), {"per_run": raw_per_run, "samples": len(scores)}


# ---------------------------------------------------------------------------
# Judge 3: Uniqueness
# ---------------------------------------------------------------------------


OVERLAP_PROMPT = """You are measuring how much two AI-generated summaries overlap.

Given SUMMARY A and SUMMARY B, score the overlap from 0.0 to 1.0:
- 1.0 = nearly identical content (a reader couldn't tell them apart)
- 0.5 = same theme and share some facts, but distinct angles/details
- 0.0 = completely different topics or perspectives

Return a JSON object on a single line: {{"overlap": <float 0.0-1.0>}}

SUMMARY A:
{a}

SUMMARY B:
{b}
"""


def _summaries_to_text(summaries: Dict[str, Dict], theme: str) -> str:
    s = summaries.get(theme, {})
    return (
        f"[{theme}] {s.get('what_is_happening', '')}\n"
        f"{s.get('why_it_matters', '')}"
    ).strip() or "(empty)"


def _judge_overlap(llm: LLMClient, a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a.strip() or not b.strip():
        return 0.0
    try:
        resp = llm.generate(
            OVERLAP_PROMPT.format(a=a[:1500], b=b[:1500]),
            temperature=0.1,
            max_tokens=80,
        )
    except LLMClientError as exc:
        logger.warning("Uniqueness judge LLM error: %s", exc)
        return 0.0
    parsed = _extract_json(resp)
    if not parsed:
        return 0.0
    try:
        score = float(parsed.get("overlap", 0.0))
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, score))


def uniqueness_judge(
    llm: LLMClient,
    summaries_by_run: Dict[str, Dict[str, Dict]],
    prior_summaries_by_run: Optional[Dict[str, Dict[str, Dict]]] = None,
) -> Tuple[float, Dict]:
    """Score uniqueness across (a) within-run theme pairs and
    (b) cross-run same-theme pairs if `prior_summaries_by_run` is provided.
    """
    overlaps: List[float] = []

    # (a) Within-run pairwise overlap
    for run_id, summaries in summaries_by_run.items():
        themes = list(summaries.keys())
        for i in range(len(themes)):
            for j in range(i + 1, len(themes)):
                a = _summaries_to_text(summaries, themes[i])
                b = _summaries_to_text(summaries, themes[j])
                overlaps.append(_judge_overlap(llm, a, b))

    # (b) Cross-run same-theme overlap
    if prior_summaries_by_run:
        for run_id, summaries in summaries_by_run.items():
            prior = prior_summaries_by_run.get(run_id)
            if not prior:
                continue
            for theme in summaries.keys():
                if theme not in prior:
                    continue
                a = _summaries_to_text(summaries, theme)
                b = _summaries_to_text(prior, theme)
                overlaps.append(_judge_overlap(llm, a, b))

    mean_overlap = _safe_mean(overlaps)
    uniqueness = 1.0 - mean_overlap
    return uniqueness, {"samples": len(overlaps), "mean_overlap": mean_overlap}


# ---------------------------------------------------------------------------
# Recommendation engine
# ---------------------------------------------------------------------------


def generate_recommendations(report: EvaluationReport) -> List[str]:
    """Build human-readable recommendations based on the report."""
    threshold = report.threshold
    recs: List[str] = []

    if report.classifier_score < threshold:
        weakest = sorted(
            report.per_theme_classifier.items(), key=lambda kv: kv[1]
        )[:2]
        weakest_str = ", ".join(
            f"{name} ({score:.0%})" for name, score in weakest
        )
        recs.append(
            f"⚠️ Classifier score {report.classifier_score:.0%} < "
            f"{threshold:.0%}. Weakest themes: {weakest_str}. "
            f"Action: review keywords in config/themes.py for these themes "
            f"and add new high-signal terms."
        )

    if report.faithfulness_score < threshold:
        recs.append(
            f"⚠️ Faithfulness score {report.faithfulness_score:.0%} < "
            f"{threshold:.0%}. Action: tighten the summariser prompt in "
            f"core/summariser.py to require source-article IDs in claims, "
            f"or reduce max_tokens to discourage fabrication."
        )

    if report.uniqueness_score < threshold:
        recs.append(
            f"⚠️ Uniqueness score {report.uniqueness_score:.0%} < "
            f"{threshold:.0%}. Action: summaries overlap too much across "
            f"themes. Review the prompt to emphasize differentiation, and "
            f"check for cross-theme article leakage in core/classifier.py."
        )

    if not recs:
        recs.append(
            f"✅ All quality scores above {threshold:.0%}. No action required."
        )
    return recs


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def run_weekly_evaluation(
    supabase=None,
    lookback_days: int = 7,
    threshold: float = QUALITY_THRESHOLD,
) -> EvaluationReport:
    """Run the full evaluation.  Returns an EvaluationReport and persists it
    to Supabase (if available).
    """
    if supabase is None:
        from core.supabase_client import get_supabase_manager
        supabase = get_supabase_manager()

    runs = _load_recent_runs(supabase, lookback_days)

    # Pre-load articles + summaries for each run
    articles_by_run: Dict[str, List[Dict]] = {}
    summaries_by_run: Dict[str, Dict[str, Dict]] = {}
    for run in runs:
        run_id = run["id"]
        articles_by_run[run_id] = _load_articles_for_run(supabase, run_id)
        summaries_by_run[run_id] = _load_summaries_for_run(supabase, run_id)

    # Build a "prior run" lookup for uniqueness: pair each run with the
    # chronologically previous one (by run_timestamp).
    runs_sorted = sorted(runs, key=lambda r: r.get("run_timestamp", ""))
    prior_summaries_by_run: Dict[str, Dict[str, Dict]] = {}
    for i in range(1, len(runs_sorted)):
        cur_id = runs_sorted[i]["id"]
        prev_id = runs_sorted[i - 1]["id"]
        if prev_id in summaries_by_run:
            prior_summaries_by_run[cur_id] = summaries_by_run[prev_id]

    # Run the three judges concurrently
    llm = _get_llm()
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(
                categoriser_judge, llm, articles_by_run
            ): "categoriser",
            executor.submit(
                faithfulness_judge, llm, summaries_by_run, articles_by_run
            ): "faithfulness",
            executor.submit(
                uniqueness_judge, llm, summaries_by_run, prior_summaries_by_run
            ): "uniqueness",
        }
        results: Dict[str, Tuple] = {}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                results[name] = fut.result()
            except Exception as exc:
                logger.error("Judge %s failed: %s", name, exc)
                results[name] = (0.0, {})

    classifier_score, per_theme_classifier, cat_raw = results["categoriser"]
    faithfulness_score, faith_raw = results["faithfulness"]
    uniqueness_score, uniq_raw = results["uniqueness"]

    per_run_scores: List[Dict] = []
    for run in runs:
        run_id = run["id"]
        per_run_scores.append({
            "run_id": run_id,
            "run_timestamp": run.get("run_timestamp", ""),
            "run_date": run.get("run_date", ""),
            "classifier_correct": cat_raw.get("per_run", {})
                                          .get(run_id, {})
                                          .get("correct", 0),
            "classifier_sampled": cat_raw.get("per_run", {})
                                            .get(run_id, {})
                                            .get("sampled", 0),
        })

    raw_metrics = {
        "categoriser": cat_raw,
        "faithfulness": faith_raw,
        "uniqueness": uniq_raw,
        "lookback_days": lookback_days,
    }

    report = EvaluationReport(
        run_ids=[r["id"] for r in runs],
        run_timestamps=[r.get("run_timestamp", "") for r in runs],
        threshold=threshold,
        classifier_score=round(classifier_score, 4),
        faithfulness_score=round(faithfulness_score, 4),
        uniqueness_score=round(uniqueness_score, 4),
        per_theme_classifier={k: round(v, 4) for k, v in per_theme_classifier.items()},
        per_run_scores=per_run_scores,
        recommendations=[],  # filled in below
        raw_metrics=raw_metrics,
        generated_at=datetime.now(timezone.utc),
    )
    report.recommendations = generate_recommendations(report)

    # Persist
    payload = {
        "lookback_days": lookback_days,
        "runs_evaluated": report.run_ids,
        "threshold": report.threshold,
        "classifier_score": report.classifier_score,
        "faithfulness_score": report.faithfulness_score,
        "uniqueness_score": report.uniqueness_score,
        "per_theme_classifier": report.per_theme_classifier,
        "recommendations": report.recommendations,
        "raw_metrics": report.raw_metrics,
    }
    inserted = insert_quality_evaluation(supabase, payload)
    if inserted:
        report.db_row_id = inserted.get("id")
        logger.info("Persisted quality_evaluations row %s", report.db_row_id)
    else:
        logger.info("Supabase unavailable; evaluation report not persisted.")

    return report


def run_evaluation_for_runs(
    run_ids: List[str],
    supabase=None,
    threshold: float = QUALITY_THRESHOLD,
) -> EvaluationReport:
    """Run evaluation for a specific set of run_ids (used by tests / page
    pre-selection).  Falls back to weekly behaviour when run_ids is empty.
    """
    if not run_ids:
        return run_weekly_evaluation(supabase=supabase, threshold=threshold)

    if supabase is None:
        from core.supabase_client import get_supabase_manager
        supabase = get_supabase_manager()

    runs_resp = supabase.client.table("trend_runs") \
        .select("id, run_timestamp, run_date, total_articles") \
        .in_("id", run_ids) \
        .execute()
    runs = runs_resp.data or []
    if not runs:
        raise EvaluationError("None of the requested run_ids were found.")

    # Reuse the same logic as run_weekly_evaluation by faking the "recent
    # runs" load: build the same shape and inline the rest.
    articles_by_run = {r["id"]: _load_articles_for_run(supabase, r["id"]) for r in runs}
    summaries_by_run = {r["id"]: _load_summaries_for_run(supabase, r["id"]) for r in runs}

    runs_sorted = sorted(runs, key=lambda r: r.get("run_timestamp", ""))
    prior_summaries_by_run: Dict[str, Dict[str, Dict]] = {}
    for i in range(1, len(runs_sorted)):
        cur_id = runs_sorted[i]["id"]
        prev_id = runs_sorted[i - 1]["id"]
        if prev_id in summaries_by_run:
            prior_summaries_by_run[cur_id] = summaries_by_run[prev_id]

    llm = _get_llm()
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(categoriser_judge, llm, articles_by_run): "categoriser",
            executor.submit(
                faithfulness_judge, llm, summaries_by_run, articles_by_run
            ): "faithfulness",
            executor.submit(
                uniqueness_judge, llm, summaries_by_run, prior_summaries_by_run
            ): "uniqueness",
        }
        results: Dict[str, Tuple] = {}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                results[name] = fut.result()
            except Exception as exc:
                logger.error("Judge %s failed: %s", name, exc)
                results[name] = (0.0, {})

    classifier_score, per_theme_classifier, cat_raw = results["categoriser"]
    faithfulness_score, faith_raw = results["faithfulness"]
    uniqueness_score, uniq_raw = results["uniqueness"]

    per_run_scores = [
        {
            "run_id": r["id"],
            "run_timestamp": r.get("run_timestamp", ""),
            "run_date": r.get("run_date", ""),
            "classifier_correct": cat_raw.get("per_run", {}).get(r["id"], {}).get("correct", 0),
            "classifier_sampled": cat_raw.get("per_run", {}).get(r["id"], {}).get("sampled", 0),
        }
        for r in runs
    ]
    raw_metrics = {
        "categoriser": cat_raw,
        "faithfulness": faith_raw,
        "uniqueness": uniq_raw,
        "lookback_days": 0,
        "explicit_run_ids": run_ids,
    }
    report = EvaluationReport(
        run_ids=[r["id"] for r in runs],
        run_timestamps=[r.get("run_timestamp", "") for r in runs],
        threshold=threshold,
        classifier_score=round(classifier_score, 4),
        faithfulness_score=round(faithfulness_score, 4),
        uniqueness_score=round(uniqueness_score, 4),
        per_theme_classifier={k: round(v, 4) for k, v in per_theme_classifier.items()},
        per_run_scores=per_run_scores,
        recommendations=[],
        raw_metrics=raw_metrics,
        generated_at=datetime.now(timezone.utc),
    )
    report.recommendations = generate_recommendations(report)
    return report


def load_evaluation_history(supabase=None, limit: int = 12) -> pd.DataFrame:
    """Return a DataFrame of recent quality_evaluations rows, newest first.
    Empty DataFrame if Supabase is unavailable.
    """
    if supabase is None:
        from core.supabase_client import get_supabase_manager
        supabase = get_supabase_manager()
    from core.quality_schema import fetch_quality_evaluations
    rows = fetch_quality_evaluations(supabase, limit=limit)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "generated_at" in df.columns:
        df["generated_at"] = pd.to_datetime(df["generated_at"])
        df = df.sort_values("generated_at")
    return df
