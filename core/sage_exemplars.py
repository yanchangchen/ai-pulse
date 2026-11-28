"""Eval-driven exemplar memory (Phase 8.1 — Sage learns from graded replies).

Builds a bounded few-shot preamble from the improvement corpus:

* **Positive exemplars** — the newest high-scoring judged replies
  (score >= 0.95): question + condensed answer, showing the *shape* of a
  well-grounded Sage answer.
* **Negative exemplars** — judge-flagged unsupported claims from recent
  low-scoring replies, plus human corrections (Phase 8.3 down-ratings
  with free text): concrete failure patterns to avoid.

Injected between the persona and the wiki context in BOTH Sage paths
(fast ``chat_with_sage`` and the deep pipeline's Analyst), mirroring the
summariser's conditional ``--- PRIOR CONTEXT ---`` block pattern. The
block has its own char budget (``sage_exemplar_max_chars``) so it can
never crowd out grounding context; kill switch ``sage_exemplars_enabled``.

Design note: exemplars demonstrate answer *shape*, not grounding — the
live context is already in the prompt, so the corpus stores Q/A/claims
(no per-reply context copy; ``sage_evaluations`` has no context column).
Never raises; an empty corpus yields "" and the prompt is unchanged.
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

POSITIVE_MIN_SCORE = 0.95
MAX_POSITIVES = 2
MAX_NEGATIVE_ROWS = 2
MAX_CLAIMS_PER_ROW = 2
MAX_CORRECTIONS = 2
ANSWER_CHARS = 600
QUESTION_CHARS = 150
CLAIM_CHARS = 200
CORRECTION_CHARS = 250


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_exemplar_block(manager=None, max_chars: Optional[int] = None) -> str:
    """The bounded ``--- EXEMPLARS ---`` block, or "" when nothing to teach."""
    try:
        from config.settings import get_sage_settings
        settings = get_sage_settings()
        if not settings.get("sage_exemplars_enabled", True):
            return ""
        budget = int(max_chars if max_chars is not None
                     else settings.get("sage_exemplar_max_chars", 2500))
        if manager is None:
            from core.supabase_client import get_supabase_manager
            manager = get_supabase_manager()
        if not manager or not manager.is_available():
            return ""

        rows = manager.get_sage_evaluations(limit=100) or []
        rows = sorted(rows, key=lambda r: str(r.get("created_at", "")), reverse=True)

        positives: List[str] = []
        negatives: List[str] = []
        for r in rows:
            score = r.get("score")
            if score is not None and float(score) >= POSITIVE_MIN_SCORE and len(positives) < MAX_POSITIVES:
                positives.append(
                    f"GOOD EXAMPLE (judge score {float(score):.0%}):\n"
                    f"Q: {_clip(r.get('question', ''), QUESTION_CHARS)}\n"
                    f"A: {_clip(r.get('answer_text', ''), ANSWER_CHARS)}"
                )
            claims = r.get("unsupported_claims")
            if isinstance(claims, list) and claims and len(negatives) < MAX_NEGATIVE_ROWS * MAX_CLAIMS_PER_ROW:
                for c in claims[:MAX_CLAIMS_PER_ROW]:
                    negatives.append(
                        f"AVOID (judge-flagged as unsupported): \"{_clip(c, CLAIM_CHARS)}\""
                    )

        # Human corrections (8.3): down-ratings with free text.
        try:
            fb = manager.get_sage_reply_feedback(limit=20) or []
        except Exception:
            fb = []
        corrections = 0
        for f in fb:
            if corrections >= MAX_CORRECTIONS:
                break
            if f.get("rating") == "down" and (f.get("correction") or "").strip():
                negatives.append(
                    f"READER CORRECTION (re: {_clip(f.get('question', ''), 100)}): "
                    f"{_clip(f.get('correction', ''), CORRECTION_CHARS)}"
                )
                corrections += 1

        if not positives and not negatives:
            return ""

        parts: List[str] = []
        if positives:
            parts.append("Good answers to imitate (structure and grounding, NOT facts):\n"
                         + "\n\n".join(positives))
        if negatives:
            parts.append("Failure patterns to avoid:\n" + "\n".join(negatives))
        block = "--- EXEMPLARS (from graded replies — imitate the good, avoid the flagged) ---\n"
        block += "\n\n".join(parts)
        block += "\n--- END EXEMPLARS ---"
        end_marker = "\n--- END EXEMPLARS ---"
        if len(block) > budget:
            block = block[: budget - len(end_marker)] + end_marker
        return block
    except Exception as e:
        logger.debug("exemplar block build failed: %s", e)
        return ""