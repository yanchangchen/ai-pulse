"""Tests for core/trend_stats.py and the theme coverage helper."""

import pandas as pd

from core.history_manager import theme_coverage_counts
from core.trend_stats import (
    classify_keywords,
    moving_average,
    specialist_keywords,
    theme_share_matrix,
)


def _df():
    return pd.DataFrame({
        "date": ["2026-11-01", "2026-11-02", "2026-11-03"],
        "total": [100, 0, 50],
        "A": [50, 10, 25],
        "B": [50, 0, 25],
    })


def test_share_matrix_normalises_and_guards_zero_total():
    share = theme_share_matrix(_df(), ["A", "B"])
    assert share.loc[0, "A"] == 50.0
    assert share.loc[1, "A"] == 0.0  # zero total -> 0, no NaN/division error
    assert share.loc[2, "B"] == 50.0


def test_moving_average_trailing():
    share = theme_share_matrix(_df(), ["A", "B"])
    ma = moving_average(share, ["A"], window=3)
    assert ma.loc[0, "A"] == 50.0          # min_periods=1
    assert abs(ma.loc[2, "A"] - (50 + 0 + 50) / 3) < 1e-9


def _velocity(kw_counts, n_runs=13):
    """kw_counts: {keyword: [count per run, newest first]}."""
    rows = []
    for kw, counts in kw_counts.items():
        for i, c in enumerate(counts[:n_runs]):
            rows.append({"run_timestamp": f"2026-11-{30 - i:02d}T00:00:00",
                         "keyword": kw, "count": c})
    return rows


def test_emerging_detection():
    # "rocket": 0 for 10 baseline runs, then 6/run for the last 3 -> inf ratio
    # "steady": 5/run throughout -> baseline
    rows = _velocity({"rocket": [6, 6, 6] + [0] * 10,
                      "steady": [5] * 13})
    sig = classify_keywords(rows)
    assert sig["n_recent_runs"] == 3 and sig["n_baseline_runs"] == 10
    emerging_kws = [t[0] for t in sig["emerging"]]
    baseline_kws = [t[0] for t in sig["baseline"]]
    assert "rocket" in emerging_kws
    assert "steady" in baseline_kws
    assert "steady" not in emerging_kws


def test_fading_detection():
    rows = _velocity({"oldnews": [1, 1, 1] + [8] * 10})
    sig = classify_keywords(rows)
    assert [t[0] for t in sig["fading"]] == ["oldnews"]


def test_min_recent_filters_noise():
    # ratio huge but recent average below min_recent -> not emerging
    rows = _velocity({"blip": [1, 0, 0] + [0] * 10})
    sig = classify_keywords(rows)
    assert sig["emerging"] == []


def test_missing_runs_count_zero():
    # keyword appears only in the newest run; baseline runs exist but no rows
    rows = [{"run_timestamp": f"2026-11-{30 - i:02d}T00:00:00", "keyword": "anchor", "count": 4}
            for i in range(13)]
    rows.append({"run_timestamp": "2026-11-30T00:00:00", "keyword": "newterm", "count": 9})
    sig = classify_keywords(rows)
    new = next(t for t in sig["emerging"] if t[0] == "newterm")
    assert new[1] == 3.0  # 9 / 3 recent runs
    assert new[2] == 0.0  # absent from baseline = 0, not skipped


def test_empty_velocity():
    sig = classify_keywords([])
    assert sig["emerging"] == [] and sig["n_recent_runs"] == 0


def test_specialist_keywords_weight_filter_and_dedupe():
    themes = {
        "T1": {"keywords": {"Alpha": 3, "beta": 1, "gamma": 2}},
        "T2": {"keywords": {"alpha": 3, "delta": 3}},
    }
    kws = specialist_keywords(themes)
    assert kws == ["alpha", "delta"]  # weight>=3, lowercased, deduped


# ---------------------------------------------------------------------------
# theme_coverage_counts (Overview caption helper)
# ---------------------------------------------------------------------------

def test_coverage_counts_live_summary():
    s = {"included_count": 12, "total_articles": 37, "waiting_hashes": ["h"] * 25}
    assert theme_coverage_counts(s) == {"included": 12, "total": 37, "waiting": 25}


def test_coverage_counts_restored_generation_log_string():
    s = {"generation_log": '{"included_count": 5, "total_articles": 5, "waiting_hashes": []}',
         "article_count": 5}
    cov = theme_coverage_counts(s)
    assert cov == {"included": 5, "total": 5, "waiting": 0}


def test_coverage_counts_zero_state_is_stated():
    # Full coverage must render 0 waiting, not fall back to "tracked".
    s = {"included_count": 8, "total_articles": 8, "waiting_hashes": []}
    cov = theme_coverage_counts(s)
    assert cov["included"] == cov["total"] == 8 and cov["waiting"] == 0


def test_coverage_counts_fallbacks():
    assert theme_coverage_counts({}, fallback_total=4) == {"included": 0, "total": 4, "waiting": 0}
    assert theme_coverage_counts(None, fallback_total=2)["total"] == 2
    s = {"article_count": 6}
    assert theme_coverage_counts(s)["total"] == 6