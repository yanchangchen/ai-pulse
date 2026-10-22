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

The labelled set (data/eval_retrieval_questions.json) is a SEED: its keyword
lists are thematic placeholders and must be curated against the actual archive
before the number is trusted.  Results are printed, logged, and (best-effort)
snapshotted to the Supabase app_settings key ``retrieval_eval_latest`` for a
durable record; a dedicated Langfuse *score* surface does not exist yet in
tracing.py, so the metric is captured here rather than fabricated into OTel
score conventions.
"""
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_EVAL_FILE = Path(__file__).resolve().parent.parent / "data" / "eval_retrieval_questions.json"


def load_questions(path: Optional[Path] = None) -> List[Dict]:
    p = path or _EVAL_FILE
    if not p.exists():
        logger.warning("Retrieval eval questions not found at %s", p)
        return []
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("questions", [])


def _recall_for(question: Dict, retrieved: List[Dict], k: int) -> Dict:
    expected = [str(s).lower() for s in question.get("expected_keywords", [])]
    top_k = retrieved[:k] if retrieved else []
    haystacks = [str(r.get("chunk_text", "")).lower() for r in top_k]

    if not expected:
        return {"question": question.get("question", ""), "recall": None, "hits": 0, "expected": 0}

    hits = 0
    for kw in expected:
        if any(kw in h for h in haystacks):
            hits += 1
    return {
        "question": question.get("question", ""),
        "recall": hits / len(expected),
        "hits": hits,
        "expected": len(expected),
    }


def run_retrieval_eval(path: Optional[Path] = None, quiet: bool = False) -> Dict:
    """Run the hit-rate eval and return a summary dict.

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
        vectors, _ = embed_texts([q.get("question", "")])
        if not vectors:
            per_question.append({"question": q.get("question", ""), "recall": None, "hits": 0, "expected": 0, "error": "embed"})
            continue
        retrieved = manager.match_article_embeddings(vectors[0], match_count=k) or []
        per_question.append(_recall_for(q, retrieved, k))

    scored = [r for r in per_question if r.get("recall") is not None]
    mean_recall = sum(r["recall"] for r in scored) / len(scored) if scored else 0.0

    summary = {
        "ran": True,
        "n_questions": len(per_question),
        "n_scored": len(scored),
        "mean_recall": mean_recall,
        "per_question": per_question,
    }

    if not quiet:
        print(f"Retrieval hit-rate: Recall@{k} mean = {mean_recall:.3f} "
              f"({len(scored)}/{len(per_question)} questions scored)")
        for r in per_question:
            recall = f"{r['recall']:.2f}" if r.get("recall") is not None else "skip"
            print(f"  {recall}  {r['question']}")

    # Best-effort durable snapshot (no Langfuse score surface exists yet).
    try:
        manager.upsert_app_setting("retrieval_eval_latest", {
            "mean_recall": mean_recall,
            "n_scored": len(scored),
            "n_questions": len(per_question),
        })
    except Exception as e:
        logger.debug("Failed to snapshot retrieval eval to app_settings: %s", e)

    logger.info("Retrieval eval mean_recall=%.3f (%d/%d scored)", mean_recall, len(scored), len(per_question))
    return summary


if __name__ == "__main__":
    from core.logger import setup_logger
    setup_logger("retrieval_eval")
    run_retrieval_eval()