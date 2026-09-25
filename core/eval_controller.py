"""
Pause/resume + crash-safe progress for the quality evaluation engine.

Three pieces:

1. **Checkpoint store** — ``data/evaluation_checkpoint.json`` holds an
   evaluation's config, per-item judge results, and (once built) the final
   payload destined for Supabase.  Writes are atomic (temp file +
   ``os.replace``) so a crash can never leave a half-written file, and
   loads are fail-soft: a corrupt file is quarantined to ``.corrupt``
   and treated as "no checkpoint".

2. **``EvalControl``** — the object threaded into the judge loops in
   ``core/evaluator.py``.  Judges call ``record()`` after every
   *successful* item (throttled disk save), check ``pause_requested``
   before each item, and replay cached results via ``get()`` on resume.
   Only successful judgments are checkpointed — failed items are retried
   on resume instead of having a transient outage baked into the metrics.

3. **``EvaluationRunner``** — a ``BackgroundRefresher``-style singleton
   (state hung on ``sys`` so it survives Streamlit reruns and module
   reloads) that owns the worker thread.  Moving the worker out of the
   page closure means navigating away no longer orphans a run: the
   thread completes and persists regardless, and the page can reattach
   to live status on any rerun.

Evaluations stay manually triggered — nothing here fires automatically.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from config.settings import (
    EVAL_CHECKPOINT_MIN_SAVE_INTERVAL,
    EVAL_DB_RETRY_SECONDS,
)

logger = logging.getLogger(__name__)

CHECKPOINT_VERSION = 1
DEFAULT_CHECKPOINT_PATH = Path(__file__).resolve().parent.parent / "data" / "evaluation_checkpoint.json"

# Re-exported for the evaluator's persist retry loop (tests monkeypatch this
# module-level value to keep retry tests fast).
DB_RETRY_SECONDS = float(EVAL_DB_RETRY_SECONDS)
CHECKPOINT_MIN_SAVE_INTERVAL = float(EVAL_CHECKPOINT_MIN_SAVE_INTERVAL)


class EvaluationPaused(Exception):
    """Raised inside judge loops when a pause was requested.  Partial
    per-item results are already persisted in the checkpoint."""


# ---------------------------------------------------------------------------
# Checkpoint file I/O
# ---------------------------------------------------------------------------


def atomic_write_json(path: Path, data: Dict) -> bool:
    """Write ``data`` as JSON to ``path`` atomically.

    Writes to a ``.tmp`` sibling first and ``os.replace``s it into place,
    so a crash mid-write can never leave a truncated checkpoint.  Returns
    False (fail-soft, logged) if the disk write fails — matching the
    ``core.auto_remediation`` state-file semantics.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        logger.warning("Could not persist evaluation checkpoint at %s: %s", path, exc)
        return False


def load_checkpoint(path: Optional[Path] = None) -> Optional[Dict]:
    """Load the checkpoint dict, or None if missing/corrupt/unknown version.

    A corrupt file is renamed to ``<name>.corrupt`` (quarantined) so the
    app doesn't repeatedly trip over the same broken file.
    """
    path = path or DEFAULT_CHECKPOINT_PATH
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or data.get("version") != CHECKPOINT_VERSION:
            raise ValueError("unknown checkpoint version")
        return data
    except Exception as exc:
        logger.warning("Corrupt evaluation checkpoint at %s (%s); quarantining.", path, exc)
        try:
            os.replace(path, path.with_suffix(path.suffix + ".corrupt"))
        except OSError:
            pass
        return None


def save_checkpoint(state: Dict, path: Optional[Path] = None) -> bool:
    return atomic_write_json(path or DEFAULT_CHECKPOINT_PATH, state)


def clear_checkpoint(path: Optional[Path] = None) -> None:
    (path or DEFAULT_CHECKPOINT_PATH).unlink(missing_ok=True)


def new_checkpoint(config: Dict) -> Dict:
    """Fresh checkpoint state dict for a new evaluation run."""
    return {
        "version": CHECKPOINT_VERSION,
        "config": {
            "run_ids": list(config.get("run_ids", [])),
            "lookback_days": int(config.get("lookback_days", 7)),
            "threshold": float(config.get("threshold", 0.8)),
            "judge_selection": config.get("judge_selection", "all"),
            "judge_model": config.get("judge_model"),
            "started_at": time.time(),
        },
        "status": "running",
        "phase": "judges",
        "error": None,
        "item_results": {
            "categoriser": {},
            "faithfulness": {},
            "uniqueness": {},
        },
        "final_payload": None,
        "final_report": None,
        "keyword_rows": None,
        "db_row_id": None,
        "completed_at": None,
    }


# ---------------------------------------------------------------------------
# EvalControl — passed into the judge loops
# ---------------------------------------------------------------------------


class EvalControl:
    """Thread-safe bridge between the judge loops and the checkpoint.

    The three LLM judges run concurrently and all call ``record()``; unlike
    the lockless judge-event deque, this structure is read-modify-write, so
    every access is under a lock.  Disk saves are throttled to
    ``CHECKPOINT_MIN_SAVE_INTERVAL``; status/phase transitions force a save.
    """

    def __init__(
        self,
        state: Dict,
        path: Optional[Path] = None,
        pause_event: Optional[threading.Event] = None,
    ):
        self.state = state
        self.path = path or DEFAULT_CHECKPOINT_PATH
        self._pause_event = pause_event or threading.Event()
        self._lock = threading.Lock()
        self._last_save = 0.0

    # -- read side ----------------------------------------------------------

    @property
    def pause_requested(self) -> bool:
        return self._pause_event.is_set()

    def get(self, judge: str, run_id: str, item_id: str) -> Optional[Dict]:
        with self._lock:
            return self.state["item_results"].get(judge, {}).get(run_id, {}).get(item_id)

    def seed_pair_cache(self) -> Dict[str, float]:
        """Flatten ``item_results["uniqueness"]`` into the ``_judge_overlap``
        pair-cache shape: every uniqueness item_id IS the cache key, so the
        checkpoint doubles as the pair cache — no separate structure."""
        flat: Dict[str, float] = {}
        with self._lock:
            for run_items in self.state["item_results"].get("uniqueness", {}).values():
                for item_id, result in run_items.items():
                    if result is not None and result.get("overlap") is not None:
                        flat[item_id] = float(result["overlap"])
        return flat

    def counts(self) -> Dict[str, int]:
        with self._lock:
            return {
                judge: sum(len(items) for items in runs.values())
                for judge, runs in self.state["item_results"].items()
            }

    # -- write side ---------------------------------------------------------

    def record(self, judge: str, run_id: str, item_id: str, result: Dict) -> None:
        with self._lock:
            self.state["item_results"].setdefault(judge, {}) \
                .setdefault(run_id, {})[item_id] = result
        self.save()

    def request_pause(self) -> None:
        self._pause_event.set()

    def set_run_ids(self, run_ids: List[str]) -> None:
        with self._lock:
            self.state["config"]["run_ids"] = list(run_ids)
        self.save(force=True)

    def set_phase(self, phase: str) -> None:
        with self._lock:
            self.state["phase"] = phase
        self.save(force=True)

    def set_status(self, status: str, error: Optional[str] = None) -> None:
        with self._lock:
            self.state["status"] = status
            self.state["error"] = error
        self.save(force=True)

    def set_final(self, payload: Dict, report_dict: Dict, keyword_rows: List[Dict]) -> None:
        """Persist the finished report BEFORE the Supabase insert is
        attempted — this is what makes "DB down at persist" recoverable."""
        with self._lock:
            self.state["phase"] = "persist"
            self.state["final_payload"] = payload
            self.state["final_report"] = report_dict
            self.state["keyword_rows"] = keyword_rows
        self.save(force=True)

    def mark_completed(self, db_row_id: Optional[str]) -> None:
        """Record completion, then remove the active checkpoint file.

        Order matters: if the process dies between the insert and the file
        removal, the leftover file says ``completed`` and a later resume is
        a no-op instead of a double insert."""
        with self._lock:
            self.state["status"] = "completed"
            self.state["db_row_id"] = db_row_id
            self.state["completed_at"] = time.time()
        self.save(force=True)
        clear_checkpoint(self.path)

    def save(self, force: bool = False) -> bool:
        with self._lock:
            now = time.monotonic()
            if not force and (now - self._last_save) < CHECKPOINT_MIN_SAVE_INTERVAL:
                return True
            self._last_save = now
            return save_checkpoint(self.state, self.path)


# ---------------------------------------------------------------------------
# EvaluationRunner — cross-rerun worker (BackgroundRefresher pattern)
# ---------------------------------------------------------------------------

if not hasattr(sys, "_aipulse_eval_state"):
    sys._aipulse_eval_state = {
        "status": "idle",          # idle | running | pausing | paused | awaiting_db | completed | failed
        "error": None,
        "thread": None,
        "control": None,           # live EvalControl (in-memory only)
        "report": None,            # EvaluationReport after completion
        "db_row_id": None,
    }

_EVAL_STATE = sys._aipulse_eval_state


class EvaluationRunner:
    """Singleton worker manager for on-demand quality evaluations.

    Modeled on ``core.bg_refresher.BackgroundRefresher``: state lives on
    ``sys._aipulse_eval_state`` so it survives Streamlit reruns and module
    reloads, and all classmethods are guarded by an RLock.
    """

    _lock = threading.RLock()

    @classmethod
    def get_status(cls) -> Dict:
        with cls._lock:
            control = _EVAL_STATE.get("control")
            status = _EVAL_STATE.get("status", "idle")
            if (
                control is not None
                and status == "running"
                and control.state.get("status") == "awaiting_db"
            ):
                # The worker is alive but parked in the persist retry loop.
                status = "awaiting_db"
            return {
                "status": status,
                "error": _EVAL_STATE.get("error"),
                "is_running": cls.is_running(),
                "counts": control.counts() if control is not None else {},
                "phase": control.state.get("phase") if control is not None else None,
                "db_row_id": _EVAL_STATE.get("db_row_id"),
                "report": _EVAL_STATE.get("report"),
            }

    @classmethod
    def is_running(cls) -> bool:
        thread = _EVAL_STATE.get("thread")
        return thread is not None and thread.is_alive()

    @classmethod
    def start(cls, config: Optional[Dict] = None, supabase=None, resume: bool = False) -> bool:
        """Start a worker.  Returns False if one is already running, or
        (resume mode) no resumable checkpoint exists.

        On resume the config comes FROM the checkpoint — the page's current
        widget values are deliberately ignored so a paused run always
        continues with the settings it was started with.
        """
        with cls._lock:
            if cls.is_running():
                return False
            if resume:
                state = load_checkpoint()
                if state is None or state.get("status") == "completed":
                    return False
            else:
                clear_checkpoint()
                state = new_checkpoint(config or {})
            control = EvalControl(state)
            _EVAL_STATE.update({
                "status": "running",
                "error": None,
                "control": control,
                "report": None,
                "db_row_id": None,
            })
            from core.evaluator import reset_judge_events
            reset_judge_events()
            thread = threading.Thread(
                target=cls._run, args=(control, supabase),
                name="quality-eval-runner", daemon=True,
            )
            _EVAL_STATE["thread"] = thread
            thread.start()
            return True

    @classmethod
    def request_pause(cls) -> None:
        with cls._lock:
            control = _EVAL_STATE.get("control")
            if control is None or not cls.is_running():
                return
            control.request_pause()
            _EVAL_STATE["status"] = "pausing"

    @classmethod
    def resume(cls, supabase=None) -> bool:
        return cls.start(resume=True, supabase=supabase)

    @classmethod
    def discard(cls) -> bool:
        """Delete the checkpoint and reset to idle.  Refuses while running."""
        with cls._lock:
            if cls.is_running():
                return False
            clear_checkpoint()
            _EVAL_STATE.update({
                "status": "idle", "error": None,
                "control": None, "report": None, "db_row_id": None,
            })
            return True

    # -- worker -------------------------------------------------------------

    @classmethod
    def _run(cls, control: EvalControl, supabase) -> None:
        try:
            control.set_status("running")
            cfg = control.state["config"]
            # Lazy imports keep this module importable without a cycle.
            from core.evaluator import (
                EvaluationReport,
                persist_final_payload,
                run_evaluation_for_runs,
                run_weekly_evaluation,
            )
            from core.supabase_client import get_supabase_manager
            sb = supabase if supabase is not None else get_supabase_manager()

            if control.state.get("phase") == "persist" and control.state.get("final_payload"):
                # Judges and keywords already finished — resume straight to
                # the persisted-payload insert (retry loop inside).
                report_dict, db_row_id = persist_final_payload(sb, control)
                _EVAL_STATE["report"] = EvaluationReport.from_dict(report_dict) \
                    if report_dict else None
                _EVAL_STATE["db_row_id"] = db_row_id
            elif cfg.get("run_ids"):
                report = run_evaluation_for_runs(
                    cfg["run_ids"], supabase=sb,
                    threshold=cfg["threshold"], lookback_days=cfg["lookback_days"],
                    judge_selection=cfg["judge_selection"], judge_model=cfg["judge_model"],
                    control=control,
                )
                _EVAL_STATE["report"] = report
                _EVAL_STATE["db_row_id"] = control.state.get("db_row_id")
            else:
                report = run_weekly_evaluation(
                    supabase=sb, lookback_days=cfg["lookback_days"],
                    threshold=cfg["threshold"], judge_selection=cfg["judge_selection"],
                    judge_model=cfg["judge_model"], control=control,
                )
                _EVAL_STATE["report"] = report
                _EVAL_STATE["db_row_id"] = control.state.get("db_row_id")
            _EVAL_STATE["status"] = "completed"
        except EvaluationPaused:
            control.set_status("paused")
            _EVAL_STATE["status"] = "paused"
            logger.info("Evaluation paused; checkpoint retained for resume.")
        except Exception as exc:  # noqa: BLE001
            # Load-phase Supabase outage or unexpected error.  Keep the
            # checkpoint so the user can Resume manually — but only if the
            # run got far enough to have anything worth resuming.
            if control.state.get("item_results") and any(
                control.counts().values()
            ):
                control.set_status("paused", error=str(exc))
            else:
                clear_checkpoint(control.path)
            _EVAL_STATE["status"] = "failed"
            _EVAL_STATE["error"] = str(exc)
            logger.warning("Evaluation worker failed: %s", exc)
        finally:
            _EVAL_STATE["thread"] = None
            logger.info("Evaluation worker exited (status=%s).", _EVAL_STATE.get("status"))
