"""Phase 3 gate: retrieval hit-rate eval against a hand-labelled question set.

Embeds each question, runs the nearest-neighbour lookup
(``match_article_embeddings``), and scores Recall@k — for each question the
fraction of its expected title-keywords that appear among the retrieved
chunks' text.  This is the measurement that gates Phase 5: it proves the dense
index can surface the *right* articles for queries the FTS keyword path would
miss, before any hybrid-retrieval code is written on top of it.

Nothing calls this at runtime — run it manually after the backfill has covered
the archive:

    python -m core.retrieval_eval

The labelled set lives in ``eval_sets/retrieval_hit_rate.json`` (git-tracked;
discoverable by the Eval Sets UI page via ``core/eval_sets.py``).  It was
curated 2026-11-27 against the live archive, replacing the lost 8-question
seed that lived in gitignored ``data/``.  Results are printed, logged, and
(best-effort) snapshotted to the Supabase app_settings key
``retrieval_eval_latest`` — including per-question scores, which the Eval Sets
page renders — for a durable record; a dedicated Langfuse *score* surface does
not exist yet in tracing.py, so the metric is captured here rather than
fabricated into OTel score conventions.
"""
import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

RETRIEVAL_SET_ID = "retrieval-hit-rate"


def load_questions(path: Optional[Path] = None) -> List[Dict]:
    if path is not None:
        if not path.exists():
            logger.warning("Retrieval eval questions not found at %s", path)
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("questions", [])

    from core.eval_sets import load_eval_set

    eval_set = load_eval_set(RETRIEVAL_SET_ID)
    if not eval_set:
        logger.warning("Retrieval eval set '%s' not found in eval_sets/", RETRIEVAL_SET_ID)
        return []
    return eval_set.get("questions", [])


def _recall_for(question: Dict, retrieved: List[Dict], k: int) -> Dict:
    expected = [str(s).lower() for s in question.get("expected_keywords", [])]
    top_k = retrieved[:k] if retrieved else []
    haystacks = [str(r.get("chunk_text", "")).lower() for r in top_k]

    if not expected:
        return {"id": question.get("id"), "question": question.get("question", ""), "recall": None, "hits": 0, "expected": 0}

    hits = 0
    for kw in expected:
        if any(kw in h for h in haystacks):
            hits += 1
    return {
        "id": question.get("id"),
        "question": question.get("question", ""),
        "recall": hits / len(expected),
        "hits": hits,
        "expected": len(expected),
    }


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(pct / 100.0 * (len(ordered) - 1)))))
    return ordered[idx]


def run_retrieval_eval(path: Optional[Path] = None, quiet: bool = False, arm: str = "dense") -> Dict:
    """Run the hit-rate eval and return a summary dict.

    ``arm`` selects the retriever under test: "dense" (Phase 3 baseline —
    embed + ``match_article_embeddings``) or "hybrid" (Phase 5 — the full
    ``core.retrieval.hybrid_search`` pipeline: FTS + dense + RRF + temporal).
    Both arms score the same labelled set with the same Recall@k metric, so
    the numbers are directly comparable. Per-question latency is recorded.
    ``quiet`` suppresses the printed per-question table (used by tests).
    """
    from core.embedding import embed_texts
    from core.supabase_client import get_supabase_manager

    questions = load_questions(path)
    if not questions:
        return {"ran": False, "reason": "no_questions"}

    manager = get_supabase_manager()
    if not manager or not manager.is_available():
        return {"ran": False, "reason": "supabase_unavailable"}

    per_question = []
    for q in questions:
        k = int(q.get("k", 10))
        t0 = time.perf_counter()
        if arm == "hybrid":
            from core.retrieval import hybrid_search
            retrieved = hybrid_search(q.get("question", ""), supabase=manager, top_k=k)
        else:
            vectors, _ = embed_texts([q.get("question", "")])
            if not vectors:
                per_question.append({"id": q.get("id"), "question": q.get("question", ""), "recall": None, "hits": 0, "expected": 0, "error": "embed"})
                continue
            retrieved = manager.match_article_embeddings(vectors[0], match_count=k) or []
        latency_ms = (time.perf_counter() - t0) * 1000.0
        row = _recall_for(q, retrieved, k)
        row["latency_ms"] = round(latency_ms, 1)
        per_question.append(row)

    scored = [r for r in per_question if r.get("recall") is not None]
    mean_recall = sum(r["recall"] for r in scored) / len(scored) if scored else 0.0
    latencies = [r["latency_ms"] for r in per_question if r.get("latency_ms") is not None]

    summary = {
        "ran": True,
        "arm": arm,
        "n_questions": len(per_question),
        "n_scored": len(scored),
        "mean_recall": mean_recall,
        "latency_mean_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
        "latency_p95_ms": round(_percentile(latencies, 95), 1),
        "per_question": per_question,
    }

    if not quiet:
        print(f"Retrieval hit-rate [{arm}]: Recall@{k} mean = {mean_recall:.3f} "
              f"({len(scored)}/{len(per_question)} questions scored), "
              f"latency mean {summary['latency_mean_ms']}ms / p95 {summary['latency_p95_ms']}ms")
        for r in per_question:
            recall = f"{r['recall']:.2f}" if r.get("recall") is not None else "skip"
            print(f"  {recall}  {r['question']}")

    # Best-effort durable snapshot (no Langfuse score surface exists yet).
    # per_question powers the Eval Sets UI page; ran_at distinguishes reruns.
    try:
        manager.upsert_app_setting("retrieval_eval_latest", {
            "set_id": RETRIEVAL_SET_ID,
            "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "arm": arm,
            "mean_recall": mean_recall,
            "n_scored": len(scored),
            "n_questions": len(per_question),
            "latency_mean_ms": summary["latency_mean_ms"],
            "latency_p95_ms": summary["latency_p95_ms"],
            "per_question": per_question,
        })
    except Exception as e:
        logger.debug("Failed to snapshot retrieval eval to app_settings: %s", e)

    logger.info("Retrieval eval [%s] mean_recall=%.3f (%d/%d scored)", arm, mean_recall, len(scored), len(per_question))
    return summary


# Phase 5 gate: hybrid must not regress the dense baseline's Recall@k, and
# must stay inside the latency budget (one embedding call + two RPCs).
HYBRID_P95_BUDGET_MS = 3000.0


def run_phase5_gate(path: Optional[Path] = None) -> Dict:
    """Side-by-side old-vs-new retrieval gate on the labelled question set.

    Runs the dense baseline arm and the hybrid arm on the identical set,
    prints the per-question comparison, and snapshots the hybrid result
    (with the baseline recorded alongside) to ``retrieval_eval_latest``.
    Gate: hybrid mean Recall@k >= dense baseline, hybrid p95 latency within
    HYBRID_P95_BUDGET_MS. The decision is recorded in Future.md.
    """
    dense = run_retrieval_eval(path, quiet=True, arm="dense")
    hybrid = run_retrieval_eval(path, quiet=True, arm="hybrid")
    if not dense.get("ran") or not hybrid.get("ran"):
        return {"ran": False, "dense": dense, "hybrid": hybrid}

    recall_ok = hybrid["mean_recall"] >= dense["mean_recall"]
    latency_ok = hybrid["latency_p95_ms"] <= HYBRID_P95_BUDGET_MS
    gate_passed = recall_ok and latency_ok

    print(f"\nPhase 5 gate — {dense['n_questions']} questions, Recall@k side-by-side")
    print(f"{'id':<5} {'dense':>6} {'hybrid':>7} {'Δ':>6}  question")
    d_by_id = {r["id"]: r for r in dense["per_question"]}
    for r in hybrid["per_question"]:
        d = d_by_id.get(r["id"], {})
        dr, hr = d.get("recall"), r.get("recall")
        delta = f"{hr - dr:+.2f}" if dr is not None and hr is not None else "  -"
        ds = f"{dr:.2f}" if dr is not None else "skip"
        hs = f"{hr:.2f}" if hr is not None else "skip"
        print(f"{r['id']:<5} {ds:>6} {hs:>7} {delta:>6}  {r['question'][:60]}")
    print(f"\nMean Recall@k: dense {dense['mean_recall']:.3f} -> hybrid {hybrid['mean_recall']:.3f} "
          f"({'PASS' if recall_ok else 'REGRESSION'})")
    print(f"Latency: dense p95 {dense['latency_p95_ms']}ms | hybrid mean {hybrid['latency_mean_ms']}ms / "
          f"p95 {hybrid['latency_p95_ms']}ms (budget {HYBRID_P95_BUDGET_MS:.0f}ms: "
          f"{'PASS' if latency_ok else 'OVER'})")
    print(f"GATE: {'PASSED' if gate_passed else 'NOT PASSED'}")

    # Durable record: hybrid snapshot + baseline + gate verdict.
    try:
        from core.supabase_client import get_supabase_manager
        manager = get_supabase_manager()
        if manager and manager.is_available():
            manager.upsert_app_setting("retrieval_eval_latest", {
                "set_id": RETRIEVAL_SET_ID,
                "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "arm": "hybrid",
                "mean_recall": hybrid["mean_recall"],
                "n_scored": hybrid["n_scored"],
                "n_questions": hybrid["n_questions"],
                "latency_mean_ms": hybrid["latency_mean_ms"],
                "latency_p95_ms": hybrid["latency_p95_ms"],
                "baseline_dense_recall": dense["mean_recall"],
                "baseline_dense_p95_ms": dense["latency_p95_ms"],
                "gate_passed": gate_passed,
                "per_question": hybrid["per_question"],
            })
    except Exception as e:
        logger.debug("Failed to snapshot Phase 5 gate: %s", e)

    return {
        "ran": True,
        "gate_passed": gate_passed,
        "recall_ok": recall_ok,
        "latency_ok": latency_ok,
        "dense": dense,
        "hybrid": hybrid,
    }


if __name__ == "__main__":
    import argparse
    from core.logger import setup_logger
    setup_logger("retrieval_eval")
    parser = argparse.ArgumentParser(description="Retrieval hit-rate eval / Phase 5 gate")
    parser.add_argument("--arm", choices=["dense", "hybrid"], default="dense")
    parser.add_argument("--gate", action="store_true", help="run the Phase 5 side-by-side gate")
    args = parser.parse_args()
    if args.gate:
        run_phase5_gate()
    else:
        run_retrieval_eval(arm=args.arm)