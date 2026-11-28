"""Pure trend computations for the Trend Analytics page (no Streamlit).

Separates *baseline* topics (stable, high-frequency background) from
*emerging* ones (accelerating vs their own trailing baseline), and
normalises theme momentum so source-volume noise doesn't read as trend.
All functions are unit-testable; the page only renders their outputs.
"""

from typing import Dict, List, Optional, Tuple

import pandas as pd


def theme_share_matrix(df_themes: pd.DataFrame, themes: List[str]) -> pd.DataFrame:
    """Per-run theme SHARE of articles (%), normalising away source volume.

    Expects columns ``date`` + one column per theme + ``total``. Rows with
    total == 0 yield 0 shares (never divide by zero).
    """
    out = df_themes[["date"]].copy()
    total = df_themes["total"].astype(float).replace(0, float("nan"))
    for theme in themes:
        out[theme] = (df_themes[theme].astype(float) / total * 100.0).fillna(0.0)
    return out


def moving_average(df: pd.DataFrame, themes: List[str], window: int = 3) -> pd.DataFrame:
    """Trailing moving average per theme column (min_periods=1 so early runs
    still render)."""
    out = df[["date"]].copy()
    for theme in themes:
        out[theme] = df[theme].rolling(window=window, min_periods=1).mean()
    return out


def classify_keywords(
    velocity_rows: List[Dict],
    recent_n: int = 3,
    baseline_n: int = 10,
    min_recent: float = 2.0,
    ratio_threshold: float = 1.5,
    min_baseline: float = 2.0,
) -> Dict[str, List[Tuple]]:
    """Split tracked keywords into emerging vs baseline.

    ``velocity_rows``: [{run_timestamp, keyword, count}] (the
    ``get_keyword_velocity`` shape). Runs are ordered newest-first; the
    newest ``recent_n`` runs form the recent window, the next ``baseline_n``
    the trailing baseline. A keyword missing from a run counts 0 there.

    * **emerging**: recent average >= ``min_recent`` AND
      recent/baseline ratio >= ``ratio_threshold`` — sorted by ratio desc.
      Tuple: (keyword, recent_avg, baseline_avg, ratio).
    * **baseline**: baseline average >= ``min_baseline`` and NOT emerging —
      stable background topics, sorted by baseline average desc.
      Tuple: (keyword, recent_avg, baseline_avg).
    * **fading**: baseline average >= ``min_baseline`` and recent average
      < half the baseline — topics losing attention. Tuple shape like
      baseline.

    Returns {"emerging": [...], "baseline": [...], "fading": [...],
    "n_recent_runs": int, "n_baseline_runs": int}.
    """
    empty = {"emerging": [], "baseline": [], "fading": [],
             "n_recent_runs": 0, "n_baseline_runs": 0}
    if not velocity_rows:
        return empty

    runs = sorted({str(r.get("run_timestamp", "")) for r in velocity_rows}, reverse=True)
    recent_runs = runs[:recent_n]
    baseline_runs = runs[recent_n:recent_n + baseline_n]
    if not recent_runs:
        return empty

    recent_set, baseline_set = set(recent_runs), set(baseline_runs)
    recent_counts: Dict[str, float] = {}
    baseline_counts: Dict[str, float] = {}
    for r in velocity_rows:
        kw = str(r.get("keyword", "")).lower()
        if not kw:
            continue
        ts = str(r.get("run_timestamp", ""))
        count = float(r.get("count", 0) or 0)
        if ts in recent_set:
            recent_counts[kw] = recent_counts.get(kw, 0.0) + count
        elif ts in baseline_set:
            baseline_counts[kw] = baseline_counts.get(kw, 0.0) + count

    n_recent, n_baseline = len(recent_runs), len(baseline_runs)
    keywords = set(recent_counts) | set(baseline_counts)
    emerging: List[Tuple] = []
    baseline: List[Tuple] = []
    fading: List[Tuple] = []
    for kw in keywords:
        recent_avg = recent_counts.get(kw, 0.0) / n_recent
        baseline_avg = (baseline_counts.get(kw, 0.0) / n_baseline) if n_baseline else 0.0
        ratio = recent_avg / baseline_avg if baseline_avg > 0 else (
            float("inf") if recent_avg > 0 else 0.0
        )
        if recent_avg >= min_recent and ratio >= ratio_threshold:
            emerging.append((kw, round(recent_avg, 2), round(baseline_avg, 2),
                             round(ratio, 2) if ratio != float("inf") else 99.0))
        elif baseline_avg >= min_baseline:
            if recent_avg < baseline_avg * 0.5:
                fading.append((kw, round(recent_avg, 2), round(baseline_avg, 2)))
            else:
                baseline.append((kw, round(recent_avg, 2), round(baseline_avg, 2)))

    emerging.sort(key=lambda t: -t[3])
    baseline.sort(key=lambda t: -t[2])
    fading.sort(key=lambda t: t[1] / t[2] if t[2] else 0)
    return {
        "emerging": emerging,
        "baseline": baseline,
        "fading": fading,
        "n_recent_runs": n_recent,
        "n_baseline_runs": n_baseline,
    }


def specialist_keywords(themes: Dict, min_weight: int = 3, cap: int = 120) -> List[str]:
    """Weight>=``min_weight`` keywords across themes (the specialist signals),
    deduped lowercase, capped for query cost."""
    seen: Dict[str, None] = {}
    for theme_data in (themes or {}).values():
        for kw, weight in (theme_data.get("keywords") or {}).items():
            if int(weight) >= min_weight:
                k = kw.strip().lower()
                if k:
                    seen.setdefault(k, None)
    return list(seen)[:cap]