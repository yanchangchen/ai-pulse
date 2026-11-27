"""coverage_judge must measure narrative prose, not metadata payloads.

Regression for the 4.4 gate experiment contamination: summary dicts carry the
4.1 skipped_articles title payload at top level; joining all dict values let
the backlog list inflate recall (98% "coverage" for a 12-article brief).
"""
from core.evaluator import coverage_judge

_ARTICLES = [
    {"title": "DiffusionGemma faster generation", "theme_name": "T", "source_name": "S"},
    {"title": "FalconASR Arabic speech release", "theme_name": "T", "source_name": "S"},
]


def test_skipped_payload_does_not_count_as_prose():
    summaries = {"run": {"T": {
        "what_is_happening": "DiffusionGemma speeds generation.",
        "skipped_articles": [{"title": "FalconASR Arabic speech release"}],
        "skipped_count": 1,
    }}}
    score, raw = coverage_judge(summaries, {"run": _ARTICLES})
    # Only the first article is mentioned in prose; the second appears solely
    # in the skipped payload and must NOT count.
    assert raw["covered"] == 1
    assert score == 0.5


def test_narrative_sections_all_count():
    summaries = {"run": {"T": {
        "what_is_happening": "DiffusionGemma speeds generation.",
        "further_reading": "FalconASR Arabic speech release",
    }}}
    score, raw = coverage_judge(summaries, {"run": _ARTICLES})
    assert raw["covered"] == 2
    assert score == 1.0


def test_generation_log_metadata_ignored():
    summaries = {"run": {"T": {
        "what_is_happening": "Nothing relevant here.",
        "_generation_log": {"skipped_articles": [
            {"title": "DiffusionGemma faster generation"},
            {"title": "FalconASR Arabic speech release"},
        ]},
    }}}
    score, raw = coverage_judge(summaries, {"run": _ARTICLES})
    assert raw["covered"] == 0
    assert score == 0.0