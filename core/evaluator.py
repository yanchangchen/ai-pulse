"""
Weekly quality evaluation engine for AI Pulse.

Combines three LLM judge agents running concurrently via ThreadPoolExecutor
with four sub-millisecond deterministic quality checkers to score recent runs:

LLM Judges (concurrent via ThreadPoolExecutor + Semaphore(3)):
1. **CategoriserJudge** — re-classifies a stratified sample of articles per run
   and compares against the theme originally assigned. Output: `classifier_score`
   and `per_theme_classifier`.

2. **FaithfulnessJudge** — fact-checks summary claims across all 7 themes against
   theme-filtered source articles (excludes empty/fallback sections from mean).
   Output: `faithfulness_score`.

3. **UniquenessJudge** — scores pairwise overlap (0..1) between all theme summaries
   in the same run and across consecutive runs. Output: `uniqueness_score`.

Deterministic Judges (sub-millisecond, zero LLM cost):
4. **GroundingScore** — verifies further reading citations match input source articles.
5. **StructuralComplianceScore** — checks section presence, 3–7 sentence bounds for prose, and list formatting.
6. **CoverageScore** — measures source article recall across generated summaries.
7. **TemporalCoherenceScore** — checks for meaningful week-over-week summary evolution.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Callable, Deque, Dict, List, Optional, Tuple

import pandas as pd

from config.settings import (
    EVALUATION_MAX_RUNS,
    EVALUATION_SAMPLE_SIZE,
    QUALITY_THRESHOLD,
    EVAL_CATEGORISER_SUMMARY_CHARS,
    EVAL_FAITHFULNESS_SUMMARY_CHARS,
    EVAL_FAITHFULNESS_ARTICLE_BUDGET,
    EVAL_OVERLAP_TEXT_CHARS,
    EVAL_HEURISTIC_LOW,
    EVAL_HEURISTIC_HIGH,
    EVAL_HEURISTIC_MAX_CHARS,
    EVAL_KEYWORD_MAX_ARTICLES,
    EVAL_KEYWORD_REASON_CHARS,
    EVAL_FAITHFULNESS_SKIP_STRINGS,
)
from config.themes import THEMES, THEME_ORDER
from core.llm_client import LLMClient, LLMClientError
from core.quality_schema import insert_quality_evaluation

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Judge event buffer (consumed by the Streamlit progress panel)
# ---------------------------------------------------------------------------
# A small in-process ring buffer of recent judge activity.  Each entry is
# a dict with the keys documented on ``_record_event``.  The Quality
# Evaluation page polls ``consume_judge_events()`` to render a live
# progress table; the buffer is thread-safe for a single producer (each
# judge thread appends) and a single consumer (the Streamlit page).

JUDGE_EVENT_BUFFER_SIZE = 200
_judge_events: Deque[Dict] = deque(maxlen=JUDGE_EVENT_BUFFER_SIZE)
# Optional override: tests can swap this for a different deque.
_judge_event_buffer: Deque[Dict] = _judge_events

# Per-(a, b) overlap cache is now scoped per-evaluation (passed as a
# local dict to uniqueness_judge / _judge_overlap) rather than living
# as a module-level global.  This prevents concurrent evaluations from
# corrupting each other's results.


def _record_event(
    judge: str,
    run_id: str = "",
    item_id: str = "",
    latency_ms: int = 0,
    parse_ok: bool = True,
    score: Optional[float] = None,
) -> None:
    """Append one row to the judge-event ring buffer.  The Streamlit
    page reads this via ``consume_judge_events()`` to render a live
    progress panel.
    """
    try:
        _judge_event_buffer.append({
            "ts": time.time(),
            "judge": judge,
            "run_id": run_id or "",
            "item_id": item_id or "",
            "latency_ms": int(latency_ms),
            "parse_ok": bool(parse_ok),
            "score": score,
        })
    except Exception:  # noqa: BLE001
        # Never let observability break a judge.
        pass


def consume_judge_events() -> List[Dict]:
    """Return and clear the judge-event buffer.  The Streamlit progress
    panel calls this on each tick to drain new events.
    Uses popleft() for atomic draining to avoid TOCTOU race.
    """
    out = []
    while _judge_event_buffer:
        try:
            out.append(_judge_event_buffer.popleft())
        except IndexError:
            break
    return out


def reset_judge_events() -> None:
    """Hard-reset the buffer.  Useful in tests."""
    _judge_event_buffer.clear()


def _event_sink_for_run(run_id: str) -> Callable[[int, bool, str], None]:
    """Return an LLMClient event-sink that records latency and parse
    outcome against the most recent ``_record_event`` for ``run_id``.
    """
    def _sink(latency_ms: int, ok: bool, error_msg: str) -> None:
        _record_event(
            judge="llm_attempt",
            run_id=run_id,
            item_id=error_msg[:80] if not ok else "ok",
            latency_ms=latency_ms,
            parse_ok=ok,
            score=None,
        )
    return _sink

# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------


@dataclass
class KeywordSuggestion:
    """A single suggested term (theme keyword or watchlist entry)."""
    term: str
    weight: Optional[int] = None
    reason: str = ""
    frequency: int = 0


@dataclass
class KeywordSuggestionReport:
    """Bundle of suggestions produced by generate_keyword_suggestions().

    ``theme_suggestions`` is keyed by theme name; each value is the list of
    terms that the LLM thought were missing from that theme's keyword set.
    ``watchlist_suggestions`` is a flat list of high-signal terms that the
    LLM thought were under-represented in watch.md.
    """
    theme_suggestions: Dict[str, List[KeywordSuggestion]]
    watchlist_suggestions: List[KeywordSuggestion]

    def is_empty(self) -> bool:
        return not any(self.theme_suggestions.values()) and not self.watchlist_suggestions

    def to_dict(self) -> Dict:
        return {
            "theme_suggestions": {
                theme: [
                    {
                        "term": s.term,
                        "weight": s.weight,
                        "reason": s.reason,
                        "frequency": s.frequency,
                    }
                    for s in sugs
                ]
                for theme, sugs in self.theme_suggestions.items()
            },
            "watchlist_suggestions": [
                {
                    "term": s.term,
                    "reason": s.reason,
                    "frequency": s.frequency,
                }
                for s in self.watchlist_suggestions
            ],
        }


@dataclass
class EvaluationReport:
    run_ids: List[str]
    run_timestamps: List[str]
    threshold: float
    classifier_score: float
    faithfulness_score: float
    uniqueness_score: float
    grounding_score: float
    structural_compliance_score: float
    coverage_score: float
    temporal_coherence_score: float
    per_theme_classifier: Dict[str, float]
    per_run_scores: List[Dict]
    recommendations: List[str]
    raw_metrics: Dict
    generated_at: datetime
    # Optional Supabase row id, populated after a successful insert.
    db_row_id: Optional[str] = None
    # Optional keyword/watchlist suggestions (populated when
    # generate_keyword_suggestions() is run alongside the judges).
    keyword_suggestions: Optional[Dict] = None

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


_llm: Optional[LLMClient] = None


def _get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm


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


# Common alternate keys the model may use instead of "score".  Picked from
# observed drift across the Ollama Cloud model in use.
_SCORE_KEY_ALIASES = ("score", "faithfulness", "rating", "value", "s", "n")


def _extract_float_from_raw(text: str) -> Optional[float]:
    """Fallback regex extraction of 0.0-1.0 float score from raw text when JSON parsing fails."""
    if not text:
        return None
    match = re.search(r'(?:score|overlap|rating|faithfulness|value)[:\s=]+([0-1]\.\d+|0|1)', text, re.IGNORECASE)
    if not match:
        match = re.search(r'\b(0\.\d+|1\.0)\b', text)
    if match:
        try:
            val = float(match.group(1))
            return max(0.0, min(1.0, val))
        except (TypeError, ValueError):
            pass
    return None


def _coerce_score(parsed: Optional[Dict]) -> Optional[float]:
    """Pull a 0..1 score out of a parsed JSON dict, trying common key
    aliases.  Returns None if no plausible numeric value is present.
    """
    if not parsed or not isinstance(parsed, dict):
        return None
    for key in _SCORE_KEY_ALIASES:
        if key in parsed:
            try:
                v = float(parsed[key])
                return max(0.0, min(1.0, v))
            except (TypeError, ValueError):
                continue
    # Nested: {"verdict": {"score": ...}} or similar
    for v in parsed.values():
        if isinstance(v, dict):
            nested = _coerce_score(v)
            if nested is not None:
                return nested
    return None


def _match_theme(predicted: str) -> Optional[str]:
    """Fuzzy-match a free-form LLM theme name to one of the canonical THEMES.
    Checks both directions: canonical-in-predicted and predicted-in-canonical,
    plus word-overlap fallback for short LLM responses.
    """
    if not predicted:
        return None
    lower = predicted.lower().strip()
    # Forward: full canonical name appears in the LLM response
    for theme in THEMES.keys():
        if theme.lower() in lower:
            return theme
    # Reverse: LLM response appears in a canonical name
    for theme in THEMES.keys():
        if lower in theme.lower():
            return theme
    # Word-overlap fallback: match if >= 2 significant words overlap
    pred_words = {w for w in re.findall(r'[a-z]+', lower) if len(w) > 3}
    if len(pred_words) >= 2:
        for theme in THEMES.keys():
            theme_words = {w for w in re.findall(r'[a-z]+', theme.lower()) if len(w) > 3}
            if len(pred_words & theme_words) >= 2:
                return theme
    return None


# ---------------------------------------------------------------------------
# Heuristic overlap (deterministic pre-filter for uniqueness)
# ---------------------------------------------------------------------------

# A small stopword set keeps the Jaccard score focused on content words
# rather than the boilerplate every AI-Pulse summary shares ("the", "is",
# "for", "and", etc.).  The set is intentionally compact — too many
# stopwords collapses the metric and makes the heuristic useless.
_HEURISTIC_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "have", "in", "is", "it", "its", "of", "on", "or", "that",
    "the", "this", "to", "was", "were", "will", "with", "we", "our",
    "you", "your", "they", "their", "ai", "ml", "model", "models",
    "using", "use", "new", "key", "into", "over", "more", "most",
    "such", "also", "than", "while", "these", "those", "some", "any",
})

# Heuristic band: below this we trust Jaccard, above we trust Jaccard,
# in between we delegate to the LLM.  Configurable via settings.
_HEURISTIC_LOW = EVAL_HEURISTIC_LOW
_HEURISTIC_HIGH = EVAL_HEURISTIC_HIGH

# Cap on text length fed into the heuristic.  Configurable via settings.
_HEURISTIC_MAX_CHARS = EVAL_HEURISTIC_MAX_CHARS

_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")


def _heuristic_tokens(text: str) -> set:
    """Lowercase alphanumeric tokens of length >= 2, minus stopwords."""
    if not text:
        return set()
    text = text[:_HEURISTIC_MAX_CHARS].lower()
    return {t for t in _TOKEN_RE.findall(text) if t not in _HEURISTIC_STOPWORDS}


def _heuristic_overlap(a: str, b: str) -> float:
    """Jaccard similarity over content tokens.  Returns a float in
    ``[0.0, 1.0]``.  ``0.0`` is returned for either side being empty
    (after stopword filtering) so that empty summaries don't get
    conflated with truly-disjoint content.
    """
    ta = _heuristic_tokens(a)
    tb = _heuristic_tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def _heuristic_short_circuit(a: str, b: str) -> Optional[float]:
    """Return a non-None overlap score if the heuristic can stand in
    for the LLM (i.e. the pair is in the very-low or very-high
    Jaccard band).  Return ``None`` if the pair needs an LLM call.
    """
    if a == b:
        return 1.0
    if not (a and a.strip()) or not (b and b.strip()):
        return 0.0
    h = _heuristic_overlap(a, b)
    if h <= _HEURISTIC_LOW or h >= _HEURISTIC_HIGH:
        return h
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
    run_id = article.get("run_id", "")
    article_id = str(article.get("id", ""))
    t0 = time.monotonic()
    try:
        response = llm.generate(
            CATEGORISER_PROMPT.format(
                title=article.get("title", ""),
                summary=(article.get("summary") or "")[:EVAL_CATEGORISER_SUMMARY_CHARS],
            ),
            temperature=0.1,
            max_tokens=150,
            event_sink=_event_sink_for_run(run_id),
        ).strip()
        latency_ms = int((time.monotonic() - t0) * 1000)
    except LLMClientError as exc:
        logger.warning("Categoriser judge LLM error: %s", exc)
        _record_event(
            judge="categoriser",
            run_id=run_id,
            item_id=article_id,
            latency_ms=0,
            parse_ok=False,
            score=0.0,
        )
        return (False, "")

    predicted = _match_theme(response)
    if not predicted:
        # Log the raw response so silent mismatches become diagnosable.
        # Rate-limited by sampling: only log 1 in 5 to avoid log spam on
        # bulk evaluations.
        if not hasattr(_judge_single_classification, "_miss_counter"):
            _judge_single_classification._miss_counter = 0
        _judge_single_classification._miss_counter += 1
        if _judge_single_classification._miss_counter % 5 == 1:
            logger.debug(
                "Categoriser judge: could not match theme in response: %r",
                (response or "")[:200],
            )
    correct = bool(predicted and predicted == article.get("theme_name"))
    _record_event(
        judge="categoriser",
        run_id=run_id,
        item_id=article_id,
        latency_ms=latency_ms,
        parse_ok=bool(predicted),
        score=1.0 if correct else 0.0,
    )
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


def _format_articles_for_judge(articles: List[Dict], max_chars: int = EVAL_FAITHFULNESS_ARTICLE_BUDGET) -> str:
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
    run_id: str = "",
    item_id: str = "",
) -> Optional[float]:
    if not summary_text or not summary_text.strip():
        # Empty summaries are excluded from scoring (return None) rather
        # than scored 1.0, which would inflate the metric.
        return None
    prompt = FAITHFULNESS_PROMPT.format(
        summary=summary_text[:EVAL_FAITHFULNESS_SUMMARY_CHARS],
        articles=_format_articles_for_judge(articles),
    )
    t0 = time.monotonic()
    try:
        resp = llm.generate(
            prompt,
            temperature=0.1,
            max_tokens=400,
            event_sink=_event_sink_for_run(run_id),
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
    except LLMClientError as exc:
        logger.warning("Faithfulness judge LLM error: %s", exc)
        _record_event(
            judge="faithfulness",
            run_id=run_id,
            item_id=item_id,
            latency_ms=0,
            parse_ok=False,
            score=0.0,
        )
        return 0.0
    parsed = _extract_json(resp)
    score = _coerce_score(parsed)
    if score is None:
        score = _extract_float_from_raw(resp)
    if score is None:
        # Log the raw response so silent zeros become diagnosable.
        logger.warning(
            "Faithfulness judge: no parseable score in response: %r",
            (resp or "")[:300],
        )
        _record_event(
            judge="faithfulness",
            run_id=run_id,
            item_id=item_id,
            latency_ms=latency_ms,
            parse_ok=False,
            score=0.0,
        )
        return 0.0
    _record_event(
        judge="faithfulness",
        run_id=run_id,
        item_id=item_id,
        latency_ms=latency_ms,
        parse_ok=True,
        score=score,
    )
    return score


def faithfulness_judge(
    llm: LLMClient,
    summaries_by_run: Dict[str, Dict[str, Dict]],
    articles_by_run: Dict[str, List[Dict]],
) -> Tuple[float, Dict]:
    """Score summary faithfulness across all runs.  Returns (overall, raw).

    Evaluates all themes (not a subset) and filters articles to only
    those matching the theme being judged.  Empty or known-fallback
    sections are excluded from the mean to avoid inflating the score.
    """
    sections_to_judge = [
        "what_is_happening",
        "engineering_tradeoffs",
        "product_impact",
    ]
    scores: List[float] = []
    raw_per_run: Dict[str, Dict] = {}

    for run_id, summaries in summaries_by_run.items():
        all_articles = articles_by_run.get(run_id, [])
        run_scores: List[float] = []
        for theme in summaries.keys():
            s = summaries[theme]
            # Filter articles to only those matching this theme
            theme_articles = [a for a in all_articles if a.get("theme_name") == theme]
            for section in sections_to_judge:
                text = s.get(section, "")
                # Skip empty or known-fallback sections
                if not text or not text.strip() or text.strip() in EVAL_FAITHFULNESS_SKIP_STRINGS:
                    continue
                score = _judge_faithfulness_one(
                    llm, text, theme_articles,
                    run_id=run_id, item_id=f"{theme}|{section}",
                )
                if score is not None:
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


def _judge_overlap(
    llm: LLMClient,
    a: str,
    b: str,
    run_id: str = "",
    item_id: str = "",
    pair_cache: Optional[Dict[str, float]] = None,
) -> float:
    """Return overlap for a single (a, b) pair, using a deterministic
    short-circuit when the heuristic is confident, and a per-evaluation
    cache so the same pair is only sent to the LLM once even if it
    appears in both the within-run and cross-run loops.
    """
    cache_key = item_id or f"{hash((a, b)) & 0xFFFFFFFF:08x}"
    if pair_cache is not None and cache_key in pair_cache:
        cached = pair_cache[cache_key]
        _record_event(
            judge="uniqueness",
            run_id=run_id,
            item_id=item_id or cache_key,
            latency_ms=0,
            parse_ok=True,
            score=cached,
        )
        return cached

    heuristic = _heuristic_short_circuit(a, b)
    if heuristic is not None:
        if pair_cache is not None:
            pair_cache[cache_key] = heuristic
        _record_event(
            judge="uniqueness",
            run_id=run_id,
            item_id=item_id or cache_key,
            latency_ms=0,
            parse_ok=True,
            score=heuristic,
        )
        return heuristic

    try:
        t0 = time.monotonic()
        resp = llm.generate(
            OVERLAP_PROMPT.format(a=a[:EVAL_OVERLAP_TEXT_CHARS], b=b[:EVAL_OVERLAP_TEXT_CHARS]),
            temperature=0.1,
            max_tokens=250,
            event_sink=_event_sink_for_run(run_id),
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
    except LLMClientError as exc:
        logger.warning("Uniqueness judge LLM error: %s", exc)
        _record_event(
            judge="uniqueness",
            run_id=run_id,
            item_id=item_id or cache_key,
            latency_ms=0,
            parse_ok=False,
            score=0.0,
        )
        return 0.0
    parsed = _extract_json(resp)
    overlap = _coerce_score({"score": parsed.get("overlap") if parsed else None}) \
        if parsed else None
    if overlap is None:
        # Try common alias keys directly.
        overlap = _coerce_score(parsed)
    if overlap is None:
        overlap = _extract_float_from_raw(resp)
    if overlap is None:
        logger.warning(
            "Uniqueness judge: no parseable overlap in response: %r",
            (resp or "")[:200],
        )
        _record_event(
            judge="uniqueness",
            run_id=run_id,
            item_id=item_id or cache_key,
            latency_ms=latency_ms,
            parse_ok=False,
            score=0.0,
        )
        return 0.0
    if pair_cache is not None:
        pair_cache[cache_key] = overlap
    _record_event(
        judge="uniqueness",
        run_id=run_id,
        item_id=item_id or cache_key,
        latency_ms=latency_ms,
        parse_ok=True,
        score=overlap,
    )
    return overlap


def uniqueness_judge(
    llm: LLMClient,
    summaries_by_run: Dict[str, Dict[str, Dict]],
    prior_summaries_by_run: Optional[Dict[str, Dict[str, Dict]]] = None,
) -> Tuple[float, Dict]:
    """Score uniqueness across (a) within-run theme pairs and
    (b) cross-run same-theme pairs if `prior_summaries_by_run` is provided.

    Uses a local per-evaluation cache so concurrent evaluations don't interfere.
    """
    overlaps: List[float] = []
    pair_cache: Dict[str, float] = {}

    # (a) Within-run pairwise overlap
    for run_id, summaries in summaries_by_run.items():
        themes = list(summaries.keys())
        for i in range(len(themes)):
            for j in range(i + 1, len(themes)):
                a = _summaries_to_text(summaries, themes[i])
                b = _summaries_to_text(summaries, themes[j])
                item_id = f"within|{run_id}|{themes[i]}|{themes[j]}"
                overlaps.append(_judge_overlap(llm, a, b, run_id=run_id, item_id=item_id, pair_cache=pair_cache))

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
                item_id = f"cross|{run_id}|{theme}"
                overlaps.append(_judge_overlap(llm, a, b, run_id=run_id, item_id=item_id, pair_cache=pair_cache))

    mean_overlap = _safe_mean(overlaps)
    uniqueness = 1.0 - mean_overlap
    return uniqueness, {"samples": len(overlaps), "mean_overlap": mean_overlap}


def parse_further_reading_titles(further_reading_text: str) -> List[str]:
    """Parse titles out of further reading section."""
    titles = []
    if not further_reading_text:
        return titles
    for line in further_reading_text.splitlines():
        line = line.strip().lstrip("-*").strip()
        if not line:
            continue
        parts = line.split("|")
        if parts:
            title = parts[0].strip().strip("[]* ")
            if title:
                titles.append(title)
    return titles


def grounding_judge(
    summaries_by_run: Dict[str, Dict[str, Dict]],
    articles_by_run: Dict[str, List[Dict]],
) -> Tuple[float, Dict]:
    """Deterministic score: what fraction of further_reading citations
    actually exist in the source articles for that run?
    """
    matched, total = 0, 0
    for run_id, summaries in summaries_by_run.items():
        all_articles = articles_by_run.get(run_id, [])
        article_titles = {a.get("title", "").lower().strip() for a in all_articles if a.get("title")}
        for theme, sections in summaries.items():
            fr_text = sections.get("further_reading", "")
            cited_titles = parse_further_reading_titles(fr_text)
            for title in cited_titles:
                total += 1
                t_lower = title.lower().strip()
                if any(t_lower in at or at in t_lower for at in article_titles):
                    matched += 1
    score = matched / total if total > 0 else 1.0
    return round(score, 4), {"matched": matched, "total": total}


def structural_compliance_judge(
    summaries_by_run: Dict[str, Dict[str, Dict]],
) -> Tuple[float, Dict]:
    """Deterministic score: does the generated output adhere to requested structure?"""
    checks_passed, checks_total = 0, 0
    required_sections = [
        "what_is_happening",
        "engineering_tradeoffs",
        "product_impact",
        "what_to_watch",
        "further_reading",
    ]
    prose_sections = ["what_is_happening", "engineering_tradeoffs", "product_impact"]

    for run_id, summaries in summaries_by_run.items():
        for theme, sections in summaries.items():
            for sec in required_sections:
                checks_total += 1
                val = sections.get(sec, "")
                if val and val.strip() and val.strip() not in EVAL_FAITHFULNESS_SKIP_STRINGS:
                    checks_passed += 1

            for sec in prose_sections:
                checks_total += 1
                text = sections.get(sec, "")
                sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
                if 3 <= len(sentences) <= 7:
                    checks_passed += 1

    score = checks_passed / checks_total if checks_total > 0 else 1.0
    return round(score, 4), {"passed": checks_passed, "total": checks_total}


def coverage_judge(
    summaries_by_run: Dict[str, Dict[str, Dict]],
    articles_by_run: Dict[str, List[Dict]],
) -> Tuple[float, Dict]:
    """Deterministic recall metric: fraction of source articles mentioned in summary."""
    covered, total = 0, 0
    for run_id, summaries in summaries_by_run.items():
        all_articles = articles_by_run.get(run_id, [])
        for theme, sections in summaries.items():
            theme_articles = [a for a in all_articles if a.get("theme_name") == theme]
            full_summary_text = " ".join(str(v) for v in sections.values()).lower()
            for art in theme_articles:
                total += 1
                title = art.get("title", "")
                words = {w.lower() for w in re.findall(r"[a-zA-Z0-9]+", title) if len(w) > 4}
                if words and any(w in full_summary_text for w in words):
                    covered += 1

    score = covered / total if total > 0 else 1.0
    return round(score, 4), {"covered": covered, "total": total}


def temporal_coherence_judge(
    summaries_by_run: Dict[str, Dict[str, Dict]],
    prior_summaries_by_run: Optional[Dict[str, Dict[str, Dict]]] = None,
) -> Tuple[float, Dict]:
    """Deterministic score: checks that summary changes between runs are meaningful."""
    if not prior_summaries_by_run:
        return 1.0, {"samples": 0, "notes": "No prior runs to compare"}

    coherent, total = 0, 0
    evolution_words = {"new", "evolved", "since", "previously", "building on", "update", "latest"}

    for run_id, summaries in summaries_by_run.items():
        prior = prior_summaries_by_run.get(run_id)
        if not prior:
            continue
        for theme, sections in summaries.items():
            if theme not in prior:
                continue
            total += 1
            cur_text = sections.get("what_is_happening", "")
            prev_text = prior[theme].get("what_is_happening", "")

            overlap = _heuristic_overlap(cur_text, prev_text)
            if overlap >= 0.95:
                continue

            cur_lower = cur_text.lower()
            has_evo_claim = any(w in cur_lower for w in evolution_words)
            if has_evo_claim and not prev_text.strip():
                continue

            coherent += 1

    score = coherent / total if total > 0 else 1.0
    return round(score, 4), {"coherent": coherent, "total": total}


# ---------------------------------------------------------------------------
# Recommendation engine
# ---------------------------------------------------------------------------


def generate_recommendations(report: EvaluationReport) -> List[str]:
    """Build human-readable recommendations based on the report."""
    threshold = report.threshold
    recs: List[str] = []
    raw = report.raw_metrics or {}

    cat_skipped = raw.get("categoriser", {}).get("skipped", False)
    faith_skipped = raw.get("faithfulness", {}).get("skipped", False)
    uniq_skipped = raw.get("uniqueness", {}).get("skipped", False)
    ground_skipped = raw.get("grounding", {}).get("skipped", False)
    struct_skipped = raw.get("structural_compliance", {}).get("skipped", False)
    cov_skipped = raw.get("coverage", {}).get("skipped", False)
    temp_skipped = raw.get("temporal_coherence", {}).get("skipped", False)

    if not cat_skipped and report.classifier_score < threshold:
        weakest = sorted(
            report.per_theme_classifier.items(), key=lambda kv: kv[1]
        )[:2]
        weakest_str = ", ".join(
            f"{name} ({score:.0%})" for name, score in weakest
        )
        recs.append(
            f"⚠️ Classifier score {report.classifier_score:.0%} < "
            f"{threshold:.0%}. Weakest themes: {weakest_str}. "
            f"Action: Click '⚡ Apply All Suggestions' below or use the '🎛️ In-App Theme Keyword Editor' expander to add terms directly in the app."
        )

    if not faith_skipped and report.faithfulness_score < threshold:
        recs.append(
            f"⚠️ Faithfulness score {report.faithfulness_score:.0%} < "
            f"{threshold:.0%}. "
            f"Action: Use the '⚙️ Faithfulness & Prompt Tuner' below to enable Strict Anti-Hallucination Mode or adjust summariser temperature directly in the app."
        )

    if not uniq_skipped and report.uniqueness_score < threshold:
        recs.append(
            f"⚠️ Uniqueness score {report.uniqueness_score:.0%} < "
            f"{threshold:.0%}. Action: Summaries overlap across themes. Use the Theme Keyword Editor to sharpen classifier boundaries or tune summariser settings."
        )

    if not ground_skipped and report.grounding_score < threshold:
        recs.append(
            f"⚠️ Grounding score {report.grounding_score:.0%} < "
            f"{threshold:.0%}. Action: further reading citations include "
            f"titles/URLs not present in input source articles."
        )

    if not struct_skipped and report.structural_compliance_score < threshold:
        recs.append(
            f"⚠️ Structural Compliance score {report.structural_compliance_score:.0%} < "
            f"{threshold:.0%}. Action: check summary formatting and section counts."
        )

    if not cov_skipped and report.coverage_score < threshold:
        recs.append(
            f"⚠️ Coverage score {report.coverage_score:.0%} < "
            f"{threshold:.0%}. Action: many source articles are missing from output summaries."
        )

    if not temp_skipped and report.temporal_coherence_score < threshold:
        recs.append(
            f"⚠️ Temporal Coherence score {report.temporal_coherence_score:.0%} < "
            f"{threshold:.0%}. Action: review week-over-week summary evolution."
        )

    if not recs:
        recs.append(
            f"✅ All quality scores above {threshold:.0%}. No action required."
        )
    return recs


# ---------------------------------------------------------------------------
# Keyword & watchlist suggestion engine
# ---------------------------------------------------------------------------


KEYWORD_SUGGESTION_PROMPT = """You are an AI taxonomy editor.  You are given:

1. The current keyword list for one theme.  Each keyword has a weight in
   1..3 (3 = strong specialist signal, 1 = generic).
2. A sample of recently-fetched articles that the existing keyword
   classifier placed in this theme.

Your task: suggest 3-10 NEW weighted keywords that are MISSING from the
existing list and would help future articles classify correctly into this
theme.  Multi-word phrases are allowed and encouraged (e.g. "prompt
injection", "secure MCP").

Rules:
- Each new keyword must be different (case-insensitive) from the existing
  ones — don't repeat terms the theme already has.
- Prefer specific, technical terms over generic ones.  Bare model names
  ("Claude", "GPT", "Llama") are weight 1 in this taxonomy; reserve
  weight 1 for broadly-firing terms and use weight 3 for strong signals.
- A weight-3 keyword must be near-unique to this theme; a weight-2
  keyword signals strong-but-not-exclusive; weight-1 is generic.
- For each keyword, give a one-sentence reason explaining what kind of
  article it would help catch.

Return ONLY a JSON object on a single line:
{{"missing": [{{"term": "...", "weight": <1|2|3>, "reason": "..."}}, ...]}}

Existing theme keywords:
{existing}

Sample articles already classified as this theme:
{articles}
"""


WATCHLIST_SUGGESTION_PROMPT = """You are an AI industry-watchlist editor.  You are given:

1. The user's current watchlist of high-signal terms grouped by category.
2. The set of AI-news theme summaries produced by the summariser this week.

Your task: suggest 5-15 NEW high-signal terms that are MISSING from the
watchlist (case-insensitive).  Each new term should be a noun phrase that
the user would want to see in a weekly AI news digest (e.g. "open-weight
release", "data center electricity", "agentic SDLC").  Don't repeat
terms the watchlist already has.

For each new term, propose a one-word category that fits the existing
watchlist vocabulary (one of: "Agent frameworks", "Agent memory", "Models",
"RAG & context", "Hardware", "Open source", "Benchmarks", "Papers",
"Regulation", "Supply chain security", "AI energy").  If none fits,
propose a new short category name.

Return ONLY a JSON object on a single line:
{{"missing": [{{"term": "...", "category": "...", "reason": "..."}}, ...]}}

Existing watchlist:
{existing}

Recent theme summaries:
{summaries}
"""


def _format_articles_for_suggestion(articles: List[Dict], max_chars: int = 4000) -> str:
    """Compact title+summary block, truncated, for the keyword prompt."""
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
    return "\n".join(parts) if parts else "(no articles available)"


def _load_watchlist_existing() -> set:
    """Best-effort read of the existing watchlist terms from watch.md.

    Returns a lowercase set of comma-separated terms under the
    `## 1. SEARCH KEYWORDS` heading.  Empty set if the file is missing
    or unparseable.
    """
    try:
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / "watch.md"
        text = path.read_text(encoding="utf-8")
    except Exception:
        return set()
    # Just pull every word/phrase after a `|` to next `|` inside the
    # SEARCH KEYWORDS table.  Lightweight parser — markdown isn't worth a
    # full AST.
    if "## 1. SEARCH KEYWORDS" not in text:
        return set()
    head = text.split("## 1. SEARCH KEYWORDS", 1)[1]
    head = head.split("## 4.", 1)[0]
    terms = set()
    for line in head.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        # Skip header rows like "| Category | Keywords |"
        if cells[0].lower() == "category" or cells[1].lower() == "keywords":
            continue
        for term in cells[1].split(","):
            t = term.strip().lower()
            if t and len(t) >= 2:
                terms.add(t)
    return terms


def _suggest_for_theme(
    llm: LLMClient,
    theme: str,
    articles: List[Dict],
) -> List[KeywordSuggestion]:
    """Ask the LLM for missing keywords for one theme."""
    from config.themes import THEMES
    existing = THEMES.get(theme, {}).get("keywords", {})
    existing_str = "\n".join(f"  - {k}: {w}" for k, w in sorted(existing.items()))
    article_block = _format_articles_for_suggestion(articles)

    prompt = KEYWORD_SUGGESTION_PROMPT.format(
        existing=existing_str,
        articles=article_block,
    )
    try:
        resp = llm.generate(
            prompt,
            temperature=0.3,
            max_tokens=600,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, never fail the report
        logger.warning("Keyword suggestion LLM error for theme %s: %s", theme, exc)
        return []

    parsed = _extract_json(resp)
    if not parsed or not isinstance(parsed, dict):
        return []
    raw_missing = parsed.get("missing") or []
    existing_lower = {k.lower() for k in existing.keys()}
    out: List[KeywordSuggestion] = []
    for item in raw_missing:
        if not isinstance(item, dict):
            continue
        term = (item.get("term") or "").strip()
        if not term:
            continue
        if term.lower() in existing_lower:
            continue
        weight = item.get("weight")
        try:
            weight = int(weight) if weight is not None else 2
            weight = max(1, min(3, weight))
        except (TypeError, ValueError):
            weight = 2
        reason = (item.get("reason") or "").strip()[:EVAL_KEYWORD_REASON_CHARS]
        out.append(KeywordSuggestion(term=term, weight=weight, reason=reason))
    return out


def _suggest_for_watchlist(
    llm: LLMClient,
    summaries: List[str],
) -> List[KeywordSuggestion]:
    """Ask the LLM for missing watchlist terms, deduped against watch.md."""
    existing_terms = _load_watchlist_existing()
    if not existing_terms:
        existing_block = "(watchlist could not be read)"
    else:
        existing_block = ", ".join(sorted(existing_terms))
    summary_block = "\n\n".join(summaries[:30])[:6000] or "(no summaries)"

    prompt = WATCHLIST_SUGGESTION_PROMPT.format(
        existing=existing_block,
        summaries=summary_block,
    )
    try:
        resp = llm.generate(
            prompt,
            temperature=0.3,
            max_tokens=800,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, never fail the report
        logger.warning("Watchlist suggestion LLM error: %s", exc)
        return []

    parsed = _extract_json(resp)
    if not parsed or not isinstance(parsed, dict):
        return []
    raw_missing = parsed.get("missing") or []
    out: List[KeywordSuggestion] = []
    for item in raw_missing:
        if not isinstance(item, dict):
            continue
        term = (item.get("term") or "").strip()
        if not term or len(term) < 2:
            continue
        if term.lower() in existing_terms:
            continue
        category = (item.get("category") or "").strip()[:64]
        reason = (item.get("reason") or "").strip()[:300]
        # Embed the category into reason so the page has it without a
        # second field on the dataclass.
        full_reason = f"[{category}] {reason}".strip() if category else reason
        out.append(KeywordSuggestion(term=term, reason=full_reason))
    return out


def generate_keyword_suggestions(
    llm: LLMClient,
    report: "EvaluationReport",
    articles_by_run: Dict[str, List[Dict]],
    summaries_by_run: Dict[str, Dict[str, Dict]],
    *,
    include_all_themes: bool = False,
    theme_max_articles: int = EVAL_KEYWORD_MAX_ARTICLES,
) -> KeywordSuggestionReport:
    """Ask the LLM which theme keywords and watchlist terms are missing.

    For each theme whose ``per_theme_classifier`` score is below the report
    threshold (or for all themes if ``include_all_themes``), pull a sample
    of articles and ask the LLM what new keywords would help classify
    similar future articles correctly.  Dedupes against the existing
    ``THEMES`` dict.

    For watchlist: collect all theme summaries from ``summaries_by_run``
    and ask the LLM what high-signal terms are missing from watch.md.
    Dedupes against the parsed watchlist file.

    The LLM is invoked once per weak theme plus once for the watchlist —
    so a 7-theme run with 3 weak themes produces 4 LLM calls.  Failure
    of any individual call degrades gracefully (that theme gets no
    suggestions, the rest still do).
    """
    from config.themes import THEMES, THEME_ORDER

    threshold = report.threshold
    weak_themes: List[str] = []
    if include_all_themes:
        weak_themes = list(THEME_ORDER)
    else:
        weak_themes = [
            theme for theme, score in report.per_theme_classifier.items()
            if score < threshold
        ]

    theme_suggestions: Dict[str, List[KeywordSuggestion]] = {}
    for theme in weak_themes:
        # Pool articles across all evaluated runs for this theme.
        pool: List[Dict] = []
        for articles in articles_by_run.values():
            for art in articles:
                if art.get("theme_name") == theme:
                    pool.append(art)
        if not pool:
            continue
        sample = pool[:theme_max_articles]
        sugs = _suggest_for_theme(llm, theme, sample)
        if sugs:
            theme_suggestions[theme] = sugs

    # Watchlist — collect summaries from all runs/themes, deduped lightly.
    summary_strings: List[str] = []
    seen_hashes: set = set()
    for summaries in summaries_by_run.values():
        for theme_name, sections in summaries.items():
            text = " ".join(sections.get(k, "") for k in (
                "what_is_happening", "engineering_tradeoffs", "product_impact",
                "what_to_watch",
            )).strip()
            if not text:
                continue
            h = hash(text[:500])
            if h in seen_hashes:
                continue
            seen_hashes.add(h)
            summary_strings.append(f"[{theme_name}] {text[:600]}")
    watch_suggestions = _suggest_for_watchlist(llm, summary_strings) if summary_strings else []

    return KeywordSuggestionReport(
        theme_suggestions=theme_suggestions,
        watchlist_suggestions=watch_suggestions,
    )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def _execute_judges_and_build_report(
    runs: List[Dict],
    articles_by_run: Dict[str, List[Dict]],
    summaries_by_run: Dict[str, Dict[str, Dict]],
    prior_summaries_by_run: Dict[str, Dict[str, Dict]],
    threshold: float,
    lookback_days: int = 7,
    supabase=None,
    judge_selection: str = "all",
) -> EvaluationReport:
    """Run selected judges (all 7, 3 LLM only, or 4 deterministic only), assemble
    EvaluationReport, and persist results.
    """
    llm_enabled = judge_selection in ("all", "llm")
    deterministic_enabled = judge_selection in ("all", "deterministic")

    results: Dict[str, Tuple] = {}

    if llm_enabled:
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
            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    results[name] = fut.result()
                except Exception as exc:
                    logger.error("Judge %s failed: %s", name, exc)
                    if name == "categoriser":
                        results[name] = (0.0, {}, {})
                    else:
                        results[name] = (0.0, {})
    else:
        results["categoriser"] = (1.0, {}, {"skipped": True})
        results["faithfulness"] = (1.0, {"skipped": True})
        results["uniqueness"] = (1.0, {"skipped": True})

    classifier_score, per_theme_classifier, cat_raw = results.get("categoriser", (0.0, {}, {}))
    faithfulness_score, faith_raw = results.get("faithfulness", (0.0, {}))
    uniqueness_score, uniq_raw = results.get("uniqueness", (0.0, {}))

    if deterministic_enabled:
        grounding_score, ground_raw = grounding_judge(summaries_by_run, articles_by_run)
        structural_compliance_score, struct_raw = structural_compliance_judge(summaries_by_run)
        coverage_score, cov_raw = coverage_judge(summaries_by_run, articles_by_run)
        temporal_coherence_score, temp_raw = temporal_coherence_judge(summaries_by_run, prior_summaries_by_run)
    else:
        grounding_score, ground_raw = 1.0, {"skipped": True}
        structural_compliance_score, struct_raw = 1.0, {"skipped": True}
        coverage_score, cov_raw = 1.0, {"skipped": True}
        temporal_coherence_score, temp_raw = 1.0, {"skipped": True}

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

    from core.classifier import get_latest_gate_stats

    raw_metrics = {
        "categoriser": cat_raw,
        "faithfulness": faith_raw,
        "uniqueness": uniq_raw,
        "grounding": ground_raw,
        "structural_compliance": struct_raw,
        "coverage": cov_raw,
        "temporal_coherence": temp_raw,
        "classifier_gates": get_latest_gate_stats(),
        "lookback_days": lookback_days,
        "judge_selection": judge_selection,
    }

    report = EvaluationReport(
        run_ids=[r["id"] for r in runs],
        run_timestamps=[r.get("run_timestamp", "") for r in runs],
        threshold=threshold,
        classifier_score=round(classifier_score, 4),
        faithfulness_score=round(faithfulness_score, 4),
        uniqueness_score=round(uniqueness_score, 4),
        grounding_score=round(grounding_score, 4),
        structural_compliance_score=round(structural_compliance_score, 4),
        coverage_score=round(coverage_score, 4),
        temporal_coherence_score=round(temporal_coherence_score, 4),
        per_theme_classifier={k: round(v, 4) for k, v in per_theme_classifier.items()},
        per_run_scores=per_run_scores,
        recommendations=[],
        raw_metrics=raw_metrics,
        generated_at=datetime.now(timezone.utc),
    )
    report.recommendations = generate_recommendations(report)

    try:
        kw_report = generate_keyword_suggestions(
            llm,
            report,
            articles_by_run,
            summaries_by_run,
        )
        report.keyword_suggestions = kw_report.to_dict()
        logger.info(
            "Keyword suggestions: %d themes, %d watchlist terms",
            len(kw_report.theme_suggestions),
            len(kw_report.watchlist_suggestions),
        )
    except Exception as exc:
        logger.warning("generate_keyword_suggestions failed: %s", exc)
        report.keyword_suggestions = {"theme_suggestions": {}, "watchlist_suggestions": []}

    # Persist
    payload = {
        "lookback_days": lookback_days,
        "runs_evaluated": report.run_ids,
        "threshold": report.threshold,
        "classifier_score": report.classifier_score,
        "faithfulness_score": report.faithfulness_score,
        "uniqueness_score": report.uniqueness_score,
        "grounding_score": report.grounding_score,
        "structural_compliance_score": report.structural_compliance_score,
        "coverage_score": report.coverage_score,
        "temporal_coherence_score": report.temporal_coherence_score,
        "per_theme_classifier": report.per_theme_classifier,
        "recommendations": report.recommendations,
        "raw_metrics": report.raw_metrics,
    }
    inserted = insert_quality_evaluation(supabase, payload)
    if inserted:
        report.db_row_id = inserted.get("id")
        logger.info("Persisted quality_evaluations row %s", report.db_row_id)
        try:
            from core.quality_schema import insert_keyword_suggestions
            if report.keyword_suggestions:
                rows: List[Dict] = []
                for theme, items in report.keyword_suggestions.get("theme_suggestions", {}).items():
                    for it in items:
                        rows.append({
                            "kind": "theme_keyword",
                            "theme_name": theme,
                            "term": it.get("term", ""),
                            "suggested_weight": it.get("weight"),
                            "reason": it.get("reason"),
                        })
                for it in report.keyword_suggestions.get("watchlist_suggestions", []):
                    rows.append({
                        "kind": "watchlist_term",
                        "theme_name": None,
                        "term": it.get("term", ""),
                        "suggested_weight": None,
                        "reason": it.get("reason"),
                    })
                if rows:
                    insert_keyword_suggestions(
                        supabase, rows, evaluation_id=report.db_row_id,
                    )
        except Exception as exc:
            logger.warning("Failed to persist keyword_suggestions: %s", exc)
    else:
        logger.info("Supabase unavailable; evaluation report not persisted.")

    return report


def run_weekly_evaluation(
    supabase=None,
    lookback_days: int = 7,
    threshold: float = QUALITY_THRESHOLD,
    judge_selection: str = "all",
) -> EvaluationReport:
    """Run the evaluation for recent runs. Returns an EvaluationReport and persists it to Supabase (if available)."""
    if supabase is None:
        from core.supabase_client import get_supabase_manager
        supabase = get_supabase_manager()

    runs = _load_recent_runs(supabase, lookback_days)

    articles_by_run: Dict[str, List[Dict]] = {}
    summaries_by_run: Dict[str, Dict[str, Dict]] = {}
    for run in runs:
        run_id = run["id"]
        articles_by_run[run_id] = _load_articles_for_run(supabase, run_id)
        summaries_by_run[run_id] = _load_summaries_for_run(supabase, run_id)

    runs_sorted = sorted(runs, key=lambda r: r.get("run_timestamp", ""))
    prior_summaries_by_run: Dict[str, Dict[str, Dict]] = {}
    for i in range(1, len(runs_sorted)):
        cur_id = runs_sorted[i]["id"]
        prev_id = runs_sorted[i - 1]["id"]
        if prev_id in summaries_by_run:
            prior_summaries_by_run[cur_id] = summaries_by_run[prev_id]

    return _execute_judges_and_build_report(
        runs=runs,
        articles_by_run=articles_by_run,
        summaries_by_run=summaries_by_run,
        prior_summaries_by_run=prior_summaries_by_run,
        threshold=threshold,
        lookback_days=lookback_days,
        supabase=supabase,
        judge_selection=judge_selection,
    )


def run_evaluation_for_runs(
    run_ids: List[str],
    supabase=None,
    threshold: float = QUALITY_THRESHOLD,
    judge_selection: str = "all",
) -> EvaluationReport:
    """Run evaluation for a specific set of run_ids (used by tests / page pre-selection)."""
    if not run_ids:
        return run_weekly_evaluation(supabase=supabase, threshold=threshold, judge_selection=judge_selection)

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

    articles_by_run = {r["id"]: _load_articles_for_run(supabase, r["id"]) for r in runs}
    summaries_by_run = {r["id"]: _load_summaries_for_run(supabase, r["id"]) for r in runs}

    runs_sorted = sorted(runs, key=lambda r: r.get("run_timestamp", ""))
    prior_summaries_by_run: Dict[str, Dict[str, Dict]] = {}
    for i in range(1, len(runs_sorted)):
        cur_id = runs_sorted[i]["id"]
        prev_id = runs_sorted[i - 1]["id"]
        if prev_id in summaries_by_run:
            prior_summaries_by_run[cur_id] = summaries_by_run[prev_id]

    return _execute_judges_and_build_report(
        runs=runs,
        articles_by_run=articles_by_run,
        summaries_by_run=summaries_by_run,
        prior_summaries_by_run=prior_summaries_by_run,
        threshold=threshold,
        lookback_days=0,
        supabase=supabase,
        judge_selection=judge_selection,
    )


def load_evaluation_history(supabase=None, limit: int = 12) -> pd.DataFrame:
    """Return a DataFrame of recent quality_evaluations rows, newest first."""
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
