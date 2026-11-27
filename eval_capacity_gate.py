"""Phase 4.4 eval gate: controlled before/after coverage comparison.

Same article pool (freshest run), three selection arms via settings overrides:
  A_legacy12        ceiling 12, summary chars 300  (pre-4.4 behaviour)
  B_cap50           ceiling 50, summary chars 300  (4.4 default)
  C_cap50_chars500  ceiling 50, summary chars 500  (relaxed truncation option)

Each arm generates gateway summaries per theme (no ledger writes — the
orchestrator, not generate_theme_summary_gateway, marks processed), then the
deterministic coverage_judge scores source-article recall on the identical
pool. Only themes with >12 articles are run (below that the arms select
identically). Results: scratch/eval_4_4_results.json + printed table.

Run from the project root:  python eval_capacity_gate.py [ARM ...]
With arm names, only those arms run and their entries are merged into the
existing results file (used to re-run a cell contaminated by a fallback).
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import summariser
from core.evaluator import coverage_judge
from core.history_manager import load_latest_run_data

NARRATIVE_KEYS = (
    "what_is_happening", "engineering_tradeoffs", "product_impact",
    "why_it_matters", "what_to_watch", "further_reading",
)

ARMS = {
    "A_legacy12": {"max_articles_per_summary": 12, "article_summary_chars": 300},
    "B_cap50": {"max_articles_per_summary": 50, "article_summary_chars": 300},
    "C_cap50_chars500": {"max_articles_per_summary": 50, "article_summary_chars": 500},
    "D_mapreduce": {"max_articles_per_summary": 12, "mapreduce_batch_size": 12,
                    "mapreduce_max_batches": 8},
}
RUNNERS = {"D_mapreduce": "mapreduce"}


def main():
    run = load_latest_run_data()
    data = run["data"]
    themed = data.get("themed_articles") or {}
    full = data.get("full_articles") or []
    for a in full:
        a.setdefault("theme_name", a.get("theme"))
    themes = [t for t, arts in themed.items() if len(arts) > 12]
    print(f"Pool: {len(full)} articles; themes with >12: "
          f"{[(t, len(themed[t])) for t in themes]}")
    if not themes:
        print("No theme exceeds the legacy cap — gate not exercisable on this run.")
        return

    out = ROOT / "scratch" / "eval_4_4_results.json"
    results = {}
    if out.exists():
        try:
            results = json.loads(out.read_text(encoding="utf-8"))
        except Exception:
            results = {}
    selected = [a for a in sys.argv[1:] if a in ARMS] or list(ARMS)
    for arm in selected:
        overrides = ARMS[arm]
        summaries = {}
        with patch("config.settings.load_custom_settings", return_value=overrides):
            for theme in themes:
                if RUNNERS.get(arm) == "mapreduce":
                    s, inc = asyncio.run(
                        summariser.generate_theme_summary_mapreduce(theme, themed[theme])
                    )
                else:
                    s, inc = asyncio.run(
                        summariser.generate_theme_summary_gateway(theme, themed[theme])
                    )
                summaries[theme] = s
        # Judge narrative prose only — metadata keys (skipped_articles title
        # payload etc.) must not inflate recall (see evaluator hygiene fix).
        narrative = {
            t: {k: v for k, v in summaries[t].items() if k in NARRATIVE_KEYS}
            for t in themes
        }
        score, raw = coverage_judge({"run": narrative}, {"run": full})
        results[arm] = {
            "coverage": score,
            "covered": raw["covered"],
            "total": raw["total"],
            "missed_count": raw["missed_count"],
            "included_per_theme": {t: summaries[t].get("included_count") for t in themes},
            "sources": {t: summaries[t].get("_source") for t in themes},
            "missed_titles": [m["title"] for m in raw["missed_articles"]],
            "summary_texts": narrative,
        }
        print(f"\n=== {arm} === coverage={score:.4f} "
              f"({raw['covered']}/{raw['total']}, missed={raw['missed_count']})")
        for t in themes:
            print(f"  {t}: included={results[arm]['included_per_theme'][t]} "
                  f"source={results[arm]['sources'][t]}")

    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResults written to {out}")

    print("\nGATE SUMMARY")
    for arm in ARMS:
        r = results.get(arm)
        if not r:
            continue
        inc = sum(v or 0 for v in r["included_per_theme"].values())
        print(f"  {arm}: coverage={r['coverage']:.4f} missed={r['missed_count']} "
              f"processed={inc}/{len(full)}")


if __name__ == "__main__":
    main()