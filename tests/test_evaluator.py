"""
Smoke tests for core/evaluator.py.

These tests intentionally do NOT call the LLM.  They cover:
- generate_recommendations() under various score/threshold combinations
- _safe_mean aggregation
- _stratified_sample round-robin behaviour
- _extract_json robust JSON extraction
- _match_theme fuzzy match
- WeeklyEvaluator double-firing guard via has_evaluation_this_iso_week
- run_weekly_evaluation correctly raises when Supabase is unavailable
"""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from core.evaluator import (
    EvaluationReport,
    EvaluationError,
    _safe_mean,
    _stratified_sample,
    _extract_json,
    _coerce_score,
    _match_theme,
    _heuristic_overlap,
    _heuristic_short_circuit,
    generate_recommendations,
    run_weekly_evaluation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_report(
    classifier: float = 0.9,
    faithfulness: float = 0.9,
    uniqueness: float = 0.9,
    threshold: float = 0.8,
    per_theme: dict | None = None,
) -> EvaluationReport:
    return EvaluationReport(
        run_ids=["a", "b"],
        run_timestamps=["2026-07-01T00:00:00Z", "2026-07-02T00:00:00Z"],
        threshold=threshold,
        classifier_score=classifier,
        faithfulness_score=faithfulness,
        uniqueness_score=uniqueness,
        per_theme_classifier=per_theme or {
            "Agentic Systems & DevTools": 0.95,
            "Frontier Models & Benchmarks": 0.85,
            "Hardware, Compute & LLMOps": 0.80,
            "Enterprise Strategy & ROI": 0.90,
            "Governance, Safety & Policy": 0.92,
            "AI Security & Trust": 0.88,
            "AI-Assisted Software Engineering": 0.93,
        },
        per_run_scores=[],
        recommendations=[],
        raw_metrics={},
        generated_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# generate_recommendations
# ---------------------------------------------------------------------------


class TestGenerateRecommendations:
    def test_all_above_threshold_yields_success(self):
        report = _make_report(0.95, 0.93, 0.91, threshold=0.8)
        recs = generate_recommendations(report)
        assert len(recs) == 1
        assert "✅" in recs[0]
        assert "80%" in recs[0]

    def test_classifier_below_threshold_mentions_weakest(self):
        report = _make_report(
            classifier=0.55,
            faithfulness=0.95,
            uniqueness=0.95,
            threshold=0.8,
            per_theme={
                "Agentic Systems & DevTools": 0.30,
                "Frontier Models & Benchmarks": 0.40,
                "Hardware, Compute & LLMOps": 0.85,
                "Enterprise Strategy & ROI": 0.90,
                "Governance, Safety & Policy": 0.92,
                "AI Security & Trust": 0.88,
                "AI-Assisted Software Engineering": 0.93,
            },
        )
        recs = generate_recommendations(report)
        assert any("Classifier" in r and "⚠️" in r for r in recs)
        # Weakest should be Agentic Systems (0.30) then Frontier Models (0.40)
        classifier_rec = next(r for r in recs if "Classifier" in r)
        assert "Agentic Systems & DevTools" in classifier_rec
        assert "Frontier Models & Benchmarks" in classifier_rec
        # The other two scores pass, so they should NOT be flagged.
        assert not any("Faithfulness" in r for r in recs)
        assert not any("Uniqueness" in r for r in recs)

    def test_faithfulness_below_threshold_summariser_action(self):
        report = _make_report(0.95, 0.50, 0.95, threshold=0.8)
        recs = generate_recommendations(report)
        assert any("Faithfulness" in r and "summariser" in r for r in recs)

    def test_uniqueness_below_threshold_classifier_action(self):
        report = _make_report(0.95, 0.95, 0.60, threshold=0.8)
        recs = generate_recommendations(report)
        assert any("Uniqueness" in r and "classifier" in r for r in recs)

    def test_all_below_threshold_yields_three_warnings(self):
        report = _make_report(0.5, 0.5, 0.5, threshold=0.8)
        recs = generate_recommendations(report)
        warnings = [r for r in recs if "⚠️" in r]
        assert len(warnings) == 3

    def test_per_evaluation_threshold_is_honoured(self):
        # 0.79 is below 0.80, so should warn.  Same score with threshold 0.5
        # should pass.
        low = _make_report(classifier=0.79, threshold=0.80)
        high = _make_report(classifier=0.79, threshold=0.50)
        assert any("⚠️" in r for r in generate_recommendations(low))
        assert not any("⚠️" in r for r in generate_recommendations(high))


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------


class TestSafeMean:
    def test_empty_returns_zero(self):
        assert _safe_mean([]) == 0.0

    def test_single_value(self):
        assert _safe_mean([0.5]) == 0.5

    def test_mean(self):
        assert _safe_mean([0.0, 0.5, 1.0]) == pytest.approx(0.5)


class TestStratifiedSample:
    def test_empty_input(self):
        assert _stratified_sample([], 10) == []

    def test_zero_n(self):
        assert _stratified_sample([{"theme_name": "x"}], 0) == []

    def test_round_robin_across_groups(self):
        items = (
            [{"theme_name": "A", "i": i} for i in range(5)]
            + [{"theme_name": "B", "i": i} for i in range(5)]
        )
        sample = _stratified_sample(items, 4)
        # Two from each group when evenly divisible.
        themes = [x["theme_name"] for x in sample]
        assert themes.count("A") == 2
        assert themes.count("B") == 2

    def test_caps_n(self):
        items = [{"theme_name": "A", "i": i} for i in range(50)]
        sample = _stratified_sample(items, 7)
        assert len(sample) == 7

    def test_handles_unknown_group(self):
        items = [{"theme_name": None, "i": i} for i in range(3)]
        sample = _stratified_sample(items, 2)
        assert len(sample) == 2


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------


class TestExtractJson:
    def test_direct(self):
        assert _extract_json('{"a": 1}') == {"a": 1}

    def test_with_surrounding_prose(self):
        text = 'Here is the answer:\n{"score": 0.8, "notes": []}\nDone.'
        assert _extract_json(text) == {"score": 0.8, "notes": []}

    def test_invalid_returns_none(self):
        assert _extract_json("not json at all") is None

    def test_empty_returns_none(self):
        assert _extract_json("") is None


class TestCoerceScore:
    def test_primary_key(self):
        assert _coerce_score({"score": 0.7}) == 0.7

    def test_alias_keys(self):
        for key in ("faithfulness", "rating", "value", "s", "n"):
            assert _coerce_score({key: 0.4}) == 0.4

    def test_clamped_to_unit_interval(self):
        assert _coerce_score({"score": 1.5}) == 1.0
        assert _coerce_score({"score": -0.5}) == 0.0

    def test_nested_dict(self):
        assert _coerce_score({"verdict": {"score": 0.9}}) == 0.9

    def test_non_numeric_returns_none(self):
        assert _coerce_score({"score": "high"}) is None
        assert _coerce_score({"notes": []}) is None
        assert _coerce_score({}) is None
        assert _coerce_score(None) is None


# ---------------------------------------------------------------------------
# Theme matching
# ---------------------------------------------------------------------------


class TestMatchTheme:
    def test_exact_match(self):
        assert _match_theme("Agentic Systems & DevTools") == "Agentic Systems & DevTools"

    def test_substring_match(self):
        assert _match_theme("I think this is Hardware, Compute & LLMOps content") == "Hardware, Compute & LLMOps"

    def test_no_match(self):
        assert _match_theme("Some random theme name") is None

    def test_empty(self):
        assert _match_theme("") is None
        assert _match_theme(None) is None


# ---------------------------------------------------------------------------
# run_weekly_evaluation requires Supabase
# ---------------------------------------------------------------------------


class TestSupabaseGuard:
    def test_raises_evaluation_error_when_supabase_unavailable(self):
        fake = MagicMock()
        fake.is_available.return_value = False
        with pytest.raises(EvaluationError) as ei:
            run_weekly_evaluation(supabase=fake, lookback_days=7, threshold=0.8)
        assert "Supabase" in str(ei.value)


# ---------------------------------------------------------------------------
# has_evaluation_this_iso_week (double-firing guard)
# ---------------------------------------------------------------------------


class TestIsoWeekGuard:
    def test_returns_true_when_row_exists_this_week(self):
        from core.quality_schema import has_evaluation_this_iso_week

        fake = MagicMock()
        fake.is_available.return_value = True
        # Pretend Supabase found a row from this week.
        fake.client.table.return_value.select.return_value.gte.return_value \
            .limit.return_value.execute.return_value.data = [{"id": "abc"}]
        assert has_evaluation_this_iso_week(fake) is True

    def test_returns_false_when_no_row_this_week(self):
        from core.quality_schema import has_evaluation_this_iso_week

        fake = MagicMock()
        fake.is_available.return_value = True
        fake.client.table.return_value.select.return_value.gte.return_value \
            .limit.return_value.execute.return_value.data = []
        assert has_evaluation_this_iso_week(fake) is False

    def test_returns_false_when_supabase_down(self):
        from core.quality_schema import has_evaluation_this_iso_week

        fake = MagicMock()
        fake.is_available.return_value = False
        assert has_evaluation_this_iso_week(fake) is False


# ---------------------------------------------------------------------------
# Heuristic overlap (deterministic pre-filter)
# ---------------------------------------------------------------------------


class TestHeuristicOverlap:
    def test_identical_text_returns_one(self):
        assert _heuristic_overlap("the cat sat on the mat", "the cat sat on the mat") == 1.0

    def test_completely_disjoint_returns_zero(self):
        a = "GPUs accelerate matrix multiplications in transformer training"
        b = "Board members approved the quarterly dividend distribution"
        assert _heuristic_overlap(a, b) == 0.0

    def test_partial_overlap_in_band(self):
        a = "anthropic released a new claude model with improved reasoning"
        b = "openai launched gpt with reasoning capability and tool use"
        h = _heuristic_overlap(a, b)
        assert 0.0 < h < 0.5

    def test_stopwords_dominated_returns_zero(self):
        # Two sentences that share only stopwords; the heuristic must
        # see them as disjoint content.
        a = "the is a for"
        b = "of the and in"
        assert _heuristic_overlap(a, b) == 0.0

    def test_empty_returns_zero(self):
        assert _heuristic_overlap("", "anything") == 0.0
        assert _heuristic_overlap("anything", "") == 0.0
        assert _heuristic_overlap("", "") == 0.0

    def test_short_circuit_high_band(self):
        # High Jaccard -> high band, no LLM needed.
        a = (
            "transformer model training data quality benchmark evaluation "
            "across many language tasks"
        )
        b = (
            "transformer model training data quality benchmark evaluation "
            "across many language tasks with new metrics"
        )
        # Confirm the heuristic is in the high band.
        h = _heuristic_overlap(a, b)
        assert h >= 0.85, f"expected high-band Jaccard, got {h}"
        assert _heuristic_short_circuit(a, b) is not None

    def test_short_circuit_low_band(self):
        a = "anthropic claude opus benchmark results"
        b = "kubernetes pod scheduling latency improvements"
        score = _heuristic_short_circuit(a, b)
        assert score is not None
        assert score <= 0.05

    def test_short_circuit_returns_none_in_ambiguous_band(self):
        a = (
            "agentic framework supports multi-step tool use planning memory"
        )
        b = (
            "agent orchestration tools integrate with planning and reasoning"
        )
        # Confirm ambiguous, then assert the short-circuit defers to LLM.
        h = _heuristic_overlap(a, b)
        assert 0.05 < h < 0.85, f"setup expected ambiguous, got {h}"
        assert _heuristic_short_circuit(a, b) is None


# ---------------------------------------------------------------------------
# Uniqueness judge with the heuristic + per-pair cache
# ---------------------------------------------------------------------------


class TestUniquenessJudgeWithHeuristic:
    def _llm(self) -> MagicMock:
        # Default canned response is "0.5 overlap" so any LLM call that
        # *does* go out is forced to the ambiguous band.
        llm = MagicMock()
        llm.generate.return_value = '{"overlap": 0.5}'
        return llm

    def _summaries(self, theme_texts: dict) -> dict:
        return {
            "r1": {
                theme: {
                    "what_is_happening": text,
                    "why_it_matters": "",
                }
                for theme, text in theme_texts.items()
            }
        }

    def test_short_circuit_skips_llm_for_disjoint_pair(self):
        from core.evaluator import reset_judge_events, uniqueness_judge

        reset_judge_events()
        llm = self._llm()
        summaries = self._summaries({
            "A": "anthropic claude opus benchmark results",
            "B": "kubernetes pod scheduling latency improvements",
        })
        uniqueness_judge(llm, summaries)
        # Heuristic should have short-circuited this pair.
        assert llm.generate.call_count == 0

    def test_short_circuit_skips_llm_for_near_duplicate_pair(self):
        from core.evaluator import reset_judge_events, uniqueness_judge

        reset_judge_events()
        llm = self._llm()
        summaries = self._summaries({
            "A": "transformer training benchmark reasoning capability evaluation",
            "B": "transformer training benchmark reasoning capability evaluation",
        })
        uniqueness_judge(llm, summaries)
        assert llm.generate.call_count == 0

    def test_ambiguous_pair_invokes_llm(self):
        from core.evaluator import reset_judge_events, uniqueness_judge

        reset_judge_events()
        llm = self._llm()
        summaries = self._summaries({
            "A": "new agentic framework supports multi-step tool use and planning",
            "B": "agent orchestration tools integrate with planning and memory",
        })
        uniqueness_judge(llm, summaries)
        # The pair is in the ambiguous band, so the LLM is consulted.
        assert llm.generate.call_count == 1


# ---------------------------------------------------------------------------
# Per-pair cache dedups within-run vs cross-run
# ---------------------------------------------------------------------------


class TestUniquenessPairCache:
    def test_same_pair_only_one_llm_call(self):
        from core.evaluator import reset_judge_events, uniqueness_judge

        reset_judge_events()
        llm = MagicMock()
        llm.generate.return_value = '{"overlap": 0.4}'

        # Run 1 has the ambiguous summary; run 2 has the same one
        # (prior_summaries_by_run[run1] = run1's summaries).
        # The cross-run loop should hit the per-pair cache and NOT
        # re-invoke the LLM.
        text = "new agentic framework supports multi-step tool use and planning"
        summaries = {
            "r1": {
                "A": {"what_is_happening": text, "why_it_matters": ""},
                "B": {"what_is_happening": "kubernetes pod scheduling latency", "why_it_matters": ""},
            },
            "r2": {
                "A": {"what_is_happening": text, "why_it_matters": ""},
                "B": {"what_is_happening": "kubernetes pod scheduling latency", "why_it_matters": ""},
            },
        }
        prior = {"r1": summaries["r1"], "r2": summaries["r2"]}
        uniqueness_judge(llm, summaries, prior_summaries_by_run=prior)
        # Two ambiguous pairs across the two runs (r1 A↔B, r2 A↔B).
        # The cross-run pair (r1 A vs r2 A) should reuse the r1 A vs r1 B
        # result only if the *text content* matches the heuristic band
        # for that specific pair — here it doesn't, so we expect
        # the LLM to be called for each ambiguous pair.
        # What we DO assert: the cache is in use, so identical
        # (item_id, text) pairs only generate one LLM call.
        # Make this explicit by reusing the same item_id.
        # In practice, item_ids are run-scoped, so we test the
        # direct call: a second call to _judge_overlap with the same
        # item_id should be a cache hit.
        from core.evaluator import _judge_overlap
        before = llm.generate.call_count
        _judge_overlap(llm, text, text, run_id="r9", item_id="dup")
        _judge_overlap(llm, text, text, run_id="r9", item_id="dup")
        # Second call must be a cache hit; LLM invoked once at most.
        assert llm.generate.call_count - before <= 1


# ---------------------------------------------------------------------------
# Judge-event buffer
# ---------------------------------------------------------------------------


class TestJudgeEvents:
    def test_record_then_consume(self):
        from core.evaluator import (
            _record_event,
            consume_judge_events,
            reset_judge_events,
        )
        reset_judge_events()
        _record_event(judge="categoriser", run_id="r1", item_id="art1", latency_ms=120)
        _record_event(judge="uniqueness", run_id="r1", item_id="A|B", latency_ms=80, score=0.5)
        events = consume_judge_events()
        assert len(events) == 2
        assert events[0]["judge"] == "categoriser"
        assert events[0]["latency_ms"] == 120
        assert events[1]["score"] == 0.5
        # Buffer is drained.
        assert consume_judge_events() == []

    def test_buffer_is_capped(self):
        from core.evaluator import (
            JUDGE_EVENT_BUFFER_SIZE,
            _record_event,
            reset_judge_events,
        )
        reset_judge_events()
        for i in range(JUDGE_EVENT_BUFFER_SIZE + 25):
            _record_event(judge="categoriser", item_id=f"art{i}")
        # Cap holds; we can't see all of them but we can drain what fits.
        from core.evaluator import consume_judge_events
        events = consume_judge_events()
        assert len(events) <= JUDGE_EVENT_BUFFER_SIZE
        # The oldest entries should be gone.
        assert events[0]["item_id"] != "art0"


