"""Phase 4.6 eval gate: deterministic before/after selection comparison.

Same article pool (freshest run), two ranking arms via settings overrides:
  A_legacy   all 4.6 signals off (pre-4.6 behaviour; 4.3 aging stays on)
  B_4_6      4.6 defaults (recency 1.0, source penalty 1.5, dup penalty 4.0
             @ Jaccard 0.8, gate confidence 0.5)

No LLM calls: ranking changes *which* articles are fed, not capacity, so the
gate measures selection quality directly — distinct sources, syndicated
near-duplicate pairs fed, mean article age, gate mix, and overlap. Themes
with <= cap articles select identically in both arms (everything is fed) and
are reported but excluded from the delta table.

Run from the project root:  python eval_ranking_gate.py
Results: scratch/eval_4_6_results.json + printed table.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import summariser
from core.history_manager import load_latest_run_data
from core.non_llm_summariser import _tokenize

ARMS = {
    "A_legacy": {
        "recency_weight": 0.0,
        "source_diversity_penalty": 0.0,
        "near_duplicate_penalty": 0.0,
        "gate_confidence_weight": 0.0,
    },
    "B_4_6": {},
}


def _age_days(a):
    raw = a.get("published_date")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)


def _dup_pairs(arts, threshold=0.8):
    """Count near-duplicate title pairs inside a selection."""
    token_sets = [set(_tokenize(a.get("title", ""))) for a in arts]
    pairs = 0
    for i in range(len(arts)):
        for j in range(i + 1, len(arts)):
            union = len(token_sets[i] | token_sets[j])
            if union and len(token_sets[i] & token_sets[j]) / union >= threshold:
                pairs += 1
    return pairs


def _metrics(selected, pool):
    ages = [d for d in (_age_days(a) for a in selected) if d is not None]
    gates = {}
    for a in selected:
        g = str(a.get("gate", "none"))
        gates[g] = gates.get(g, 0) + 1
    return {
        "selected": len(selected),
        "pool": len(pool),
        "distinct_sources": len({a.get("source_name") or a.get("source") for a in selected}),
        "dup_pairs_fed": _dup_pairs(selected),
        "mean_age_days": round(sum(ages) / len(ages), 2) if ages else None,
        "gate_mix": dict(sorted(gates.items())),
        "hashes": [a.get("content_hash") for a in selected],
    }


def main():
    run = load_latest_run_data()
    data = run["data"]
    themed = data.get("themed_articles") or {}
    cap = 50
    print(f"Pool: {sum(len(v) for v in themed.values())} articles across "
          f"{len(themed)} themes; cap {cap}")

    results = {"ran_at": datetime.now(timezone.utc).isoformat(), "cap": cap, "themes": {}}
    for theme, arts in themed.items():
        entry = {}
        for arm, overrides in ARMS.items():
            with patch("config.settings.load_custom_settings", return_value=overrides):
                selected = summariser._rank_articles_by_relevance(arts, theme, cap)
            entry[arm] = _metrics(selected, arts)
        a, b = entry["A_legacy"], entry["B_4_6"]
        overlap = len(set(a["hashes"]) & set(b["hashes"]))
        entry["overlap"] = overlap
        entry["exercisable"] = len(arts) > cap
        results["themes"][theme] = entry

    out = ROOT / "scratch" / "eval_4_6_results.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print(f"\n{'Theme':<38} {'Pool':>4} | {'sources A->B':>11} | {'dupPairs A->B':>12} | "
          f"{'meanAge A->B':>13} | {'overlap':>7}")
    for theme, e in results["themes"].items():
        a, b = e["A_legacy"], e["B_4_6"]
        age_a = a["mean_age_days"] if a["mean_age_days"] is not None else "-"
        age_b = b["mean_age_days"] if b["mean_age_days"] is not None else "-"
        flag = "" if e["exercisable"] else "  (pool <= cap, order-only)"
        print(f"{theme[:38]:<38} {a['pool']:>4} | "
              f"{a['distinct_sources']:>4} -> {b['distinct_sources']:<4} | "
              f"{a['dup_pairs_fed']:>5} -> {b['dup_pairs_fed']:<5} | "
              f"{str(age_a):>6} -> {str(age_b):<6} | {e['overlap']:>7}{flag}")
    print(f"\nResults written to {out}")


if __name__ == "__main__":
    main()
