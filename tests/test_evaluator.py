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
