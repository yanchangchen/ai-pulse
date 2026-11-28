"""Sage grounding guard (Phase 8.2 — the agent-side improvement loop).

The summariser has evaluate -> remediate -> re-measure via
``core/auto_remediation.py``; Sage had measurement only (the chat-time
faithfulness judge).  This module closes Sage's loop:

* **Trigger** — the rolling mean of the last ``WINDOW`` judged replies
  drops below ``TRIGGER`` (with at least ``MIN_N`` judgments): set
  ``sage_strict_grounding`` in custom_settings.  Both Sage paths then
  append a STRICT GROUNDING MANDATE to the persona and cap temperature
  at ``TARGET_TEMP`` (consumed in sage_agent / sage_pipeline).
* **Revert** — unlike the summariser tuner (sticky until a human changes
  it), the guard is explicitly reversible: when the rolling mean recovers
  to ``RECOVER`` (hysteresis above the trigger, so judge noise can't
  oscillate the mode), the flag is cleared automatically.
* **Audit** — every action is appended to the state's history and the
  whole state is write-through persisted to ``app_settings``
  (``sage_grounding_state``) + a local JSON mirror, the auto_remediation
  pattern.  Disabled by default; armed from the Quality Evaluation page.

The check runs fire-and-forget after each successful judge insert
(``record_sage_faithfulness``), so the loop closes without any scheduler.
Never raises; a guard failure must never break the chat it observes.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "sage_grounding_state.json"
STATE_KEY = "sage_grounding_state"
SETTINGS_FLAG = "sage_guard_enabled"
STRICT_FLAG = "sage_strict_grounding"

WINDOW = 20          # rolling window (matches the page-7 baseline)
MIN_N = 10           # don't act on a handful of judgments
TRIGGER = 0.8        # tighten below this rolling mean
RECOVER = 0.85       # revert at/above this (hysteresis)
TARGET_TEMP = 0.2    # temperature cap while strict
HISTORY_CAP = 20


def is_enabled() -> bool:
    from config.settings import load_custom_settings
    return bool(load_custom_settings().get(SETTINGS_FLAG, False))


def set_enabled(enabled: bool) -> None:
    from config.settings import load_custom_settings, save_custom_settings
    data = load_custom_settings()
    data[SETTINGS_FLAG] = bool(enabled)
    save_custom_settings(data)


def rolling_sage_faithfulness(manager, window: int = WINDOW, mode: Optional[str] = None):
    """(mean, n) over the last ``window`` judged replies, oldest-to-newest.

    The single shared definition of the rolling baseline (guard, gate8,
    and any future consumer).  ``mode`` optionally restricts to 'fast' /
    'deep'.  Returns (0.0, 0) when unavailable/empty — never raises.
    """
    try:
        rows = manager.get_sage_evaluations(limit=max(window * 5, 100)) or []
        if mode:
            rows = [r for r in rows if (r.get("answer_mode") or "untagged") == mode]
        rows = sorted(rows, key=lambda r: str(r.get("created_at", "")))
        recent = rows[-window:]
        scores = [float(r["score"]) for r in recent if r.get("score") is not None]
        if not scores:
            return 0.0, 0
        return sum(scores) / len(scores), len(scores)
    except Exception as e:
        logger.debug("rolling faithfulness computation failed: %s", e)
        return 0.0, 0


def _load_state() -> Dict:
    """app_settings is authoritative; local JSON mirrors it (auto_remediation
    pattern).  Corrupt state resets to empty — never raises."""
    state: Dict = {}
    try:
        from core.supabase_client import get_supabase_manager
        manager = get_supabase_manager()
        if manager and manager.is_available():
            remote = manager.get_app_setting(STATE_KEY)
            if isinstance(remote, dict):
                state = remote
                try:
                    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
                except Exception:
                    pass
                return state
    except Exception as e:
        logger.debug("guard state remote load failed: %s", e)
    try:
        if STATE_FILE.exists():
            loaded = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return loaded
    except Exception as e:
        logger.warning("Corrupt sage guard state, resetting: %s", e)
    return {}


def _save_state(state: Dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to write local sage guard state: %s", e)
    try:
        from core.supabase_client import get_supabase_manager
        manager = get_supabase_manager()
        if manager and manager.is_available():
            manager.upsert_app_setting(STATE_KEY, state)
    except Exception as e:
        logger.warning("Failed to persist sage guard state: %s", e)


def _set_strict(value: bool) -> None:
    from config.settings import load_custom_settings, save_custom_settings
    data = load_custom_settings()
    data[STRICT_FLAG] = bool(value)
    save_custom_settings(data)


def check_and_act(manager=None) -> Dict:
    """One pass of the guard: tighten / revert / no-op.  Returns an action
    record ({} when nothing happened).  Never raises."""
    try:
        if not is_enabled():
            return {}
        if manager is None:
            from core.supabase_client import get_supabase_manager
            manager = get_supabase_manager()
        if not manager or not manager.is_available():
            return {}

        mean, n = rolling_sage_faithfulness(manager)
        if n < MIN_N:
            return {}

        from config.settings import load_custom_settings
        strict = bool(load_custom_settings().get(STRICT_FLAG, False))
        state = _load_state()
        now = datetime.now(timezone.utc).isoformat()
        action: Dict = {}

        if not strict and mean < TRIGGER:
            _set_strict(True)
            action = {
                "action": "tighten_grounding",
                "rolling_mean": round(mean, 4),
                "window_n": n,
                "at": now,
            }
            state["strict"] = True
            state["trigger_mean"] = round(mean, 4)
            state["applied_at"] = now
        elif strict and mean >= RECOVER:
            _set_strict(False)
            action = {
                "action": "revert_grounding",
                "rolling_mean": round(mean, 4),
                "window_n": n,
                "at": now,
            }
            state["strict"] = False
            state["reverted_at"] = now
        else:
            return {}

        history = state.get("history") or []
        history.append(action)
        state["history"] = history[-HISTORY_CAP:]
        state["last_mean"] = round(mean, 4)
        state["last_n"] = n
        _save_state(state)
        logger.info("Sage grounding guard: %s (rolling %.3f, n=%d)",
                    action["action"], mean, n)
        return action
    except Exception as e:
        logger.warning("Sage grounding guard pass failed: %s", e)
        return {}