"""Run-level processing stats (compute_run_processing_stats).

Reconstructs how many new unprocessed articles entered a run and how many
were summarised, from the per-theme included_count + waiting_hashes already
persisted by Phases 4.1/4.3 — on live dicts and Supabase-restored rows.
"""
import json

from core.history_manager import compute_run_processing_stats


def test_live_summary_dicts():
    summaries = {
        "T1": {"included_count": 84, "waiting_hashes": []},
        "T2": {"included_count": 12, "waiting_hashes": ["a", "b", "c"]},
    }
    stats = compute_run_processing_stats(summaries)
    assert stats == {"new_unprocessed": 99, "processed": 96, "waiting": 3}


def test_restored_rows_generation_log_dict():
    summaries = {
        "T1": {"_generation_log": {"included_count": 50, "waiting_hashes": ["x"]}},
        "T2": {"generation_log": {"included_count": 8, "waiting_hashes": []}},
    }
    stats = compute_run_processing_stats(summaries)
    assert stats == {"new_unprocessed": 59, "processed": 58, "waiting": 1}


def test_generation_log_json_string():
    summaries = {
        "T1": {"generation_log": json.dumps({"included_count": 7, "waiting_hashes": ["h1", "h2"]})},
    }
    stats = compute_run_processing_stats(summaries)
    assert stats == {"new_unprocessed": 9, "processed": 7, "waiting": 2}


def test_top_level_keys_win_over_log():
    summaries = {
        "T1": {"included_count": 5, "waiting_hashes": ["a"],
               "_generation_log": {"included_count": 99, "waiting_hashes": ["z"] * 10}},
    }
    stats = compute_run_processing_stats(summaries)
    assert stats == {"new_unprocessed": 6, "processed": 5, "waiting": 1}


def test_skipped_themes_contribute_zero():
    summaries = {
        "T1": {"included_count": 0, "waiting_hashes": [],
               "_generation_log": {"note": "no_new_articles_skip"}},
        "T2": {"included_count": 3, "waiting_hashes": []},
    }
    stats = compute_run_processing_stats(summaries)
    assert stats == {"new_unprocessed": 3, "processed": 3, "waiting": 0}


def test_legacy_summaries_without_counts():
    # Pre-4.1 runs carry neither included_count nor waiting_hashes.
    summaries = {"T1": {"what_is_happening": "old brief"}}
    stats = compute_run_processing_stats(summaries)
    assert stats == {"new_unprocessed": 0, "processed": 0, "waiting": 0}


def test_empty_and_malformed_inputs():
    assert compute_run_processing_stats({}) == {"new_unprocessed": 0, "processed": 0, "waiting": 0}
    assert compute_run_processing_stats(None) == {"new_unprocessed": 0, "processed": 0, "waiting": 0}
    summaries = {
        "T1": None,
        "T2": {"generation_log": "not-json{{"},
        "T3": {"_generation_log": ["unexpected", "list"]},
        "T4": {"included_count": 2, "waiting_hashes": None},
    }
    stats = compute_run_processing_stats(summaries)
    assert stats == {"new_unprocessed": 2, "processed": 2, "waiting": 0}
