"""
Bounded auto-remediation driven by quality evaluation reports.

Closes the evaluate → remediate loop without a human click, with
guardrails so the loop can't run away:

**Summariser tuner rules** (deterministic, bounded):
- Faithfulness < threshold → strict anti-hallucination mode ON and
  temperature dropped to ``AUTO_TARGET_TEMPERATURE``.
- Coverage < threshold → ``max_tokens`` raised by ``AUTO_MAX_TOKENS_STEP``
  (hard cap ``AUTO_MAX_TOKENS_CAP``).

**Keyword rule** (experimental, reversible):
- Per-theme classifier score < threshold → apply up to
  ``AUTO_MAX_TERMS_PER_THEME`` suggestions from the evaluation's keyword
  suggestion engine, weights capped at ``AUTO_MAX_WEIGHT`` (weight-3
  "specialist" terms stay human-approved).  Each apply is recorded in a
  pending-state file with the theme's baseline score.

**Rollback**: on the next evaluation, a theme whose per-theme score DROPPED
below its pre-apply baseline has its auto-applied terms removed.  A theme
that was just rolled back is not re-applied in the same round (prevents
oscillation on the same failed terms).

Disabled by default.  Toggled from the Quality Evaluation page and
persisted in ``custom_settings.json`` (``auto_remediation_enabled``).
Every action is returned in the evaluation's ``raw_metrics``
(``auto_remediation``) so it persists to Supabase for audit.

All settings/keyword writes go through the existing runtime-effective
paths (``config.settings.update_summariser_settings`` merges at call
time; ``config.themes.add_keywords_to_theme`` mutates THEMES in place and
persists Supabase-first), so applied remediations take effect on the next
pipeline run without a restart.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

from config.settings import (
    get_summariser_settings,
    load_custom_settings,
    save_custom_settings,
    update_summariser_settings,
)

logger = logging.getLogger(__name__)

# Pending keyword experiments (baseline scores + applied terms) live here
# so the next evaluation can roll back failures.  Best-effort: on a
# read-only filesystem the loop still applies remediations, it just loses
# cross-restart rollback memory (logged as a warning).
STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "auto_remediation_state.json"

# --- Guardrail bounds -------------------------------------------------------
AUTO_MAX_TERMS_PER_THEME = 5     # max keyword terms applied per theme per round
AUTO_MAX_WEIGHT = 2              # weight-3 specialist terms stay human-approved
AUTO_TARGET_TEMPERATURE = 0.1    # faithfulness remedy target
AUTO_MAX_TOKENS_STEP = 500       # coverage remedy increment
AUTO_MAX_TOKENS_CAP = 2500       # coverage remedy hard cap

SETTINGS_FLAG = "auto_remediation_enabled"


# ---------------------------------------------------------------------------
# Enablement
# ---------------------------------------------------------------------------


def is_enabled() -> bool:
    return bool(load_custom_settings().get(SETTINGS_FLAG, False))


def set_enabled(enabled: bool) -> None:
    data = load_custom_settings()
    data[SETTINGS_FLAG] = bool(enabled)
    try:
        save_custom_settings(data)
    except OSError as exc:
        logger.warning("Could not persist auto-remediation flag: %s", exc)


# ---------------------------------------------------------------------------
# Pending-experiment state
# ---------------------------------------------------------------------------


def _get_supabase_manager():
    """Lazy import to avoid circular dependencies."""
    from core.supabase_client import get_supabase_manager
    return get_supabase_manager()


AUTO_REMEDIATION_STATE_KEY = "auto_remediation_state"


def _load_state() -> Dict:
    """Load the pending-experiment state.

    Supabase app_settings is authoritative when it has a row (that's the
    copy that survives app restarts and redeploys — the local file alone
    does not); a remote hit also refreshes the local file so offline runs
    stay current.  Falls back to the local file when Supabase is
    unavailable or the app_settings migration hasn't been run.
    """
    try:
        manager = _get_supabase_manager()
        if manager.is_available():
            remote = manager.get_app_setting(AUTO_REMEDIATION_STATE_KEY)
            if isinstance(remote, dict) and remote.get("pending_keyword_applies"):
                remote.setdefault("pending_keyword_applies", {})
                try:
                    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                    STATE_FILE.write_text(
                        json.dumps(remote, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except OSError:
                    pass  # local cache refresh is best-effort
                return remote
    except Exception as exc:  # noqa: BLE001 — state loading must never break evaluation
        logger.warning("Failed to load auto-remediation state from Supabase: %s", exc)
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("pending_keyword_applies", {})
                return data
    except Exception:  # noqa: BLE001 — corrupt state must not break evaluation
        logger.warning("Corrupt auto-remediation state at %s; starting fresh.", STATE_FILE)
    return {"pending_keyword_applies": {}}


def _save_state(state: Dict) -> None:
    """Write the state to the local file AND Supabase app_settings
    (write-through — the remote copy is what survives redeploys)."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("Could not persist auto-remediation state: %s", exc)
    try:
        manager = _get_supabase_manager()
        if manager.is_available():
            manager.upsert_app_setting(AUTO_REMEDIATION_STATE_KEY, state)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not sync auto-remediation state to Supabase: %s", exc)


# ---------------------------------------------------------------------------
# Rollback pass
# ---------------------------------------------------------------------------


def check_rollbacks(report) -> List[Dict]:
    """Remove auto-applied keywords for themes whose classifier score DROPPED
    below the pre-apply baseline.  Themes that improved (or held) keep their
    terms and leave the pending set.

    Returns a list of rollback action records.  When the categoriser judge
    was skipped this round there is no signal, so pending experiments are
    left untouched.
    """
    state = _load_state()
    pending: Dict[str, Dict] = state.get("pending_keyword_applies", {})
    actions: List[Dict] = []
    if not pending:
        return actions

    cat_raw = (report.raw_metrics or {}).get("categoriser", {}) or {}
    if cat_raw.get("skipped"):
        return actions  # no per-theme signal — keep pending for next round

    from config.themes import remove_keyword_from_theme

    changed = False
    for theme, entry in list(pending.items()):
        current = (report.per_theme_classifier or {}).get(theme)
        if current is None:
            continue  # theme not evaluated this round; keep pending
        baseline = float(entry.get("baseline_score", 0.0))
        if current < baseline:
            terms = entry.get("terms", [])
            for term in terms:
                try:
                    remove_keyword_from_theme(theme, term)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "auto-remediation rollback: could not remove %r from %s: %s",
                        term, theme, exc,
                    )
            actions.append({
                "action": "rollback_keywords",
                "theme": theme,
                "terms": terms,
                "baseline_score": baseline,
                "current_score": current,
                "original_evaluation": entry.get("evaluation_id"),
            })
            logger.warning(
                "auto-remediation: rolled back %d keyword(s) for %s "
                "(score %.2f < baseline %.2f)", len(terms), theme, current, baseline,
            )
        del pending[theme]
        changed = True

    if changed:
        state["pending_keyword_applies"] = pending
        _save_state(state)
    return actions


# ---------------------------------------------------------------------------
# Apply pass
# ---------------------------------------------------------------------------


def _tune_faithfulness(report, raw: Dict) -> List[Dict]:
    """Strict grounding ON + temperature floor when faithfulness is low."""
    faith_raw = raw.get("faithfulness", {}) or {}
    if faith_raw.get("skipped") or report.faithfulness_score >= report.threshold:
        return []
    s = get_summariser_settings()
    cur_temp = float(s.get("temperature", 0.3))
    cur_strict = bool(s.get("strict_faithfulness_mode", False))
    new_temp = min(cur_temp, AUTO_TARGET_TEMPERATURE)
    new_strict = True
    if new_temp == cur_temp and new_strict == cur_strict:
        return []  # already at the remedy target — nothing to do
    previous = {"temperature": cur_temp, "strict_faithfulness_mode": cur_strict}
    update_summariser_settings(new_temp, int(s.get("max_tokens", 1500)), new_strict)
    return [{
        "action": "tune_summariser_faithfulness",
        "previous": previous,
        "new": {"temperature": new_temp, "strict_faithfulness_mode": new_strict},
        "trigger_score": report.faithfulness_score,
    }]


def _raise_token_budget(report, raw: Dict) -> List[Dict]:
    """Bump max_tokens one bounded step when coverage is low."""
    cov_raw = raw.get("coverage", {}) or {}
    if cov_raw.get("skipped") or report.coverage_score >= report.threshold:
        return []
    s = get_summariser_settings()
    tokens = int(s.get("max_tokens", 1500))
    if tokens >= AUTO_MAX_TOKENS_CAP:
        return []  # at cap — further increases won't help coverage
    new_tokens = min(AUTO_MAX_TOKENS_CAP, tokens + AUTO_MAX_TOKENS_STEP)
    update_summariser_settings(
        float(s.get("temperature", 0.3)), new_tokens,
        bool(s.get("strict_faithfulness_mode", False)),
    )
    return [{
        "action": "raise_max_tokens",
        "previous": {"max_tokens": tokens},
        "new": {"max_tokens": new_tokens},
        "trigger_score": report.coverage_score,
    }]


def _apply_weak_theme_keywords(
    report, raw: Dict, skip_themes: Optional[Set[str]] = None
) -> List[Dict]:
    """Apply bounded keyword suggestions to sub-threshold themes."""
    cat_raw = raw.get("categoriser", {}) or {}
    if cat_raw.get("skipped"):
        return []
    suggestions = {}
    if getattr(report, "keyword_suggestions", None):
        suggestions = report.keyword_suggestions.get("theme_suggestions", {}) or {}
    if not suggestions:
        return []

    skip_themes = skip_themes or set()
    from config.themes import THEMES, add_keywords_to_theme

    actions: List[Dict] = []
    state = _load_state()
    pending: Dict[str, Dict] = state.setdefault("pending_keyword_applies", {})
    changed = False

    for theme, per_theme_score in (report.per_theme_classifier or {}).items():
        if per_theme_score >= report.threshold:
            continue
        if theme in skip_themes:
            # Just rolled back this round — don't re-apply (possibly the
            # same) terms immediately; wait for the next evaluation.
            continue
        if theme in pending:
            continue  # one experiment per theme at a time
        items = suggestions.get(theme) or []
        if not items:
            continue
        existing = {k.lower() for k in THEMES.get(theme, {}).get("keywords", {})}
        payload: Dict[str, int] = {}
        for it in items:
            term = (it.get("term") or "").strip()
            if not term or term.lower() in existing or term.lower() in payload:
                continue
            try:
                weight = int(it.get("weight") or AUTO_MAX_WEIGHT)
            except (TypeError, ValueError):
                weight = AUTO_MAX_WEIGHT
            payload[term] = max(1, min(AUTO_MAX_WEIGHT, weight))
            if len(payload) >= AUTO_MAX_TERMS_PER_THEME:
                break
        if not payload:
            continue
        add_keywords_to_theme(theme, payload)
        pending[theme] = {
            "terms": list(payload.keys()),
            "weights": payload,
            "baseline_score": float(per_theme_score),
            "evaluation_id": getattr(report, "db_row_id", None),
            "applied_at": datetime.now(timezone.utc).isoformat(),
        }
        changed = True
        actions.append({
            "action": "apply_keywords",
            "theme": theme,
            "terms": payload,
            "baseline_score": float(per_theme_score),
        })
        logger.info(
            "auto-remediation: applied %d keyword(s) to %s (baseline %.2f)",
            len(payload), theme, per_theme_score,
        )

    if changed:
        state["pending_keyword_applies"] = pending
        _save_state(state)
    return actions


def apply_remediations(report, skip_themes: Optional[Set[str]] = None) -> List[Dict]:
    """Run all apply rules against a fresh EvaluationReport."""
    raw = report.raw_metrics or {}
    actions: List[Dict] = []
    actions.extend(_tune_faithfulness(report, raw))
    actions.extend(_raise_token_budget(report, raw))
    actions.extend(_apply_weak_theme_keywords(report, raw, skip_themes=skip_themes))
    return actions


# ---------------------------------------------------------------------------
# Entry point (called from core/evaluator.py after each report is built)
# ---------------------------------------------------------------------------


def run_auto_remediation(report) -> Dict:
    """Roll back failed keyword experiments, then apply bounded fixes.

    Returns ``{}`` when disabled or when there was nothing to do, else
    ``{"applied": [...], "rolled_back": [...]}`` for raw_metrics/audit.
    """
    if not is_enabled():
        return {}
    rolled_back: List[Dict] = []
    applied: List[Dict] = []
    try:
        rolled_back = check_rollbacks(report)
    except Exception as exc:  # noqa: BLE001 — never break the evaluation
        logger.warning("auto-remediation rollback check failed: %s", exc)
    try:
        applied = apply_remediations(
            report,
            skip_themes={a.get("theme") for a in rolled_back if a.get("theme")},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("auto-remediation apply failed: %s", exc)
    if not (rolled_back or applied):
        return {}
    return {"applied": applied, "rolled_back": rolled_back}
