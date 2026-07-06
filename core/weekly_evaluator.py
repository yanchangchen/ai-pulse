"""
Weekly quality evaluation background thread.

Modeled after `core.bg_refresher.BackgroundRefresher` — a singleton daemon
thread that wakes up hourly, asks "is there an evaluation row for the
current ISO calendar week?", and if not, runs `run_weekly_evaluation()`.
Skips silently if Supabase or the LLM are unavailable.

The thread is idempotent: `start()` is safe to call multiple times across
Streamlit module reloads.
"""

from __future__ import annotations

import sys
import threading
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from core.logger import setup_logger
from config.settings import (
    EVALUATION_CHECK_INTERVAL_SECONDS,
    EVALUATION_MAX_RUNS,
)

logger = setup_logger(__name__)

# Persist state across Streamlit module reloads via sys.
if not hasattr(sys, "_aipulse_weekly_evaluator_state"):
    sys._aipulse_weekly_evaluator_state = {
        "status": "idle",
        "error": None,
        "completed_timestamp": None,
        "last_checked_at": None,
        "thread": None,
    }


class WeeklyEvaluator:
    """Background thread that runs quality evaluations once per ISO week."""

    _lock = threading.RLock()

    # ------------------------------------------------------------------
    # State accessors
    # ------------------------------------------------------------------

    @classmethod
    def _get_state(cls) -> dict:
        return sys._aipulse_weekly_evaluator_state

    @classmethod
    def is_running(cls) -> bool:
        with cls._lock:
            thread = cls._get_state()["thread"]
            return thread is not None and thread.is_alive()

    @classmethod
    def get_status(cls) -> dict:
        with cls._lock:
            state = cls._get_state()
            thread = state["thread"]
            return {
                "status": state["status"],
                "error": state["error"],
                "completed_timestamp": state["completed_timestamp"],
                "last_checked_at": state["last_checked_at"],
                "is_running": thread is not None and thread.is_alive(),
            }

    @classmethod
    def update_status(cls, status: str, error: Optional[str] = None) -> None:
        with cls._lock:
            state = cls._get_state()
            state["status"] = status
            if error is not None:
                state["error"] = error
            if status == "completed":
                state["completed_timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            state["last_checked_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    @classmethod
    def start(cls) -> bool:
        """Spawn the daemon thread if not already running.  Returns True if
        a new thread was started.
        """
        with cls._lock:
            if cls.is_running():
                logger.info("Weekly evaluator is already running.")
                return False

            state = cls._get_state()
            state["status"] = "starting"
            state["error"] = None
            thread = threading.Thread(
                target=cls._run_loop,
                name="AI-Pulse-Weekly-Evaluator",
                daemon=True,
            )
            state["thread"] = thread
            thread.start()
            logger.info("Started weekly evaluator thread.")
            print(
                "⚡ [AI Pulse Weekly Evaluator] Started background weekly evaluation thread.",
                flush=True,
            )
            return True

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    @classmethod
    def _run_loop(cls) -> None:
        """Hourly check: should we run an evaluation this ISO week?"""
        try:
            cls.update_status("running")
            # Stagger the first run by a few seconds so we don't fight with
            # the main refresher for resources on cold start.
            import time
            time.sleep(5)

            while True:
                try:
                    cls._maybe_run_once()
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "Weekly evaluator iteration crashed: %s", exc, exc_info=True
                    )
                    cls.update_status("failed", error=str(exc))

                # Sleep until next check.
                time.sleep(EVALUATION_CHECK_INTERVAL_SECONDS)
        except Exception as exc:  # noqa: BLE001
            logger.error("Weekly evaluator loop died: %s", exc, exc_info=True)
            cls.update_status("failed", error=str(exc))

    @classmethod
    def _maybe_run_once(cls) -> None:
        """Run a single weekly check.  Skips silently if Supabase or LLM
        are unavailable, or if an evaluation already exists this week.
        """
        # Lazy imports to avoid pulling heavy modules at thread-spawn time.
        from core.supabase_client import get_supabase_manager
        from core.evaluator import run_weekly_evaluation
        from core.quality_schema import has_evaluation_this_iso_week

        supabase = get_supabase_manager()
        if not supabase.is_available():
            logger.info("Weekly evaluator: Supabase unavailable, skipping.")
            cls.update_status("idle")
            return

        from core.llm_client import LLMClient
        if not LLMClient().is_available():
            logger.info("Weekly evaluator: LLM unavailable, skipping.")
            cls.update_status("idle")
            return

        if has_evaluation_this_iso_week(supabase):
            logger.info(
                "Weekly evaluator: evaluation already exists for this ISO week, skipping."
            )
            cls.update_status("idle")
            return

        # Optional guard: don't run if the latest trend_run is too fresh.
        # We want a "settled" pipeline (≥1h old) so we evaluate a complete
        # picture rather than a mid-pipeline snapshot.
        latest = supabase.get_latest_run()
        if latest and latest.get("run_timestamp"):
            try:
                last_ts = datetime.fromisoformat(
                    latest["run_timestamp"].replace("Z", "+00:00")
                )
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=timezone.utc)
                age = datetime.now(timezone.utc) - last_ts
                if age < timedelta(hours=1):
                    logger.info(
                        "Weekly evaluator: latest run is only %s old (<1h), skipping.",
                        age,
                    )
                    cls.update_status("idle")
                    return
            except Exception as exc:  # noqa: BLE001
                logger.debug("Could not parse latest run timestamp: %s", exc)

        logger.info("Weekly evaluator: starting weekly evaluation run.")
        cls.update_status("running")
        try:
            report = run_weekly_evaluation(
                supabase=supabase, lookback_days=7,
            )
            logger.info(
                "Weekly evaluator: completed. classifier=%.2f faithfulness=%.2f uniqueness=%.2f",
                report.classifier_score,
                report.faithfulness_score,
                report.uniqueness_score,
            )
            cls.update_status("completed")
        except Exception as exc:  # noqa: BLE001
            logger.error("Weekly evaluator: run failed: %s", exc, exc_info=True)
            cls.update_status("failed", error=str(exc))


def maybe_start_weekly_evaluator() -> None:
    """Convenience entry point for `bg_refresher.py` to call after a pipeline
    run finishes.  Safe to call repeatedly.
    """
    WeeklyEvaluator.start()
