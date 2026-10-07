"""Durable LLM call logging for the Model Gateway.

Every completed gateway execution (LLM success, deterministic fallback, and
total failure) is mirrored into the Supabase ``llm_calls`` table (created by
``supabase_migration_llm_calls.sql``).  This is the project's only durable
token/cost accounting: provider-reported usage flows from the provider
adapters through ``Provenance.tokens_in`` / ``tokens_out`` into a row here.

Design constraints:

- **Fire-and-forget** — the insert runs on a daemon thread so the DB round
  trip never blocks or fails the LLM call it is observing.
- **Availability-gated** — when Supabase is unavailable the row is skipped
  synchronously (no thread is spawned), which also keeps offline tests and
  local runs quiet.
- **trace_id stays NULL** until Phase 0.3 links the OpenTelemetry trace ids
  into this table (see Future.md, Pillar F).
"""
import logging
import threading
from typing import Optional

from .contracts import Provenance

logger = logging.getLogger(__name__)

# Cap the persisted error text so a long traceback can never bloat the row.
_MAX_ERROR_CHARS = 500


def record_call(
    provenance: Provenance,
    success: bool,
    cost_class: Optional[str] = None,
) -> Optional[threading.Thread]:
    """Queue one ``llm_calls`` row for insertion.

    Returns the writer ``Thread`` (already started) so callers/tests can
    join it, or ``None`` when Supabase is unavailable and the row was
    skipped.  Never raises.
    """
    try:
        from core.supabase_client import get_supabase_manager

        manager = get_supabase_manager()
        if not manager or not manager.is_available():
            return None
    except Exception as e:
        logger.debug(f"llm_calls logging skipped (no Supabase manager): {e}")
        return None

    row = {
        "task_type": provenance.task,
        "provider": provenance.provider,
        "model": provenance.model,
        "tokens_in": provenance.tokens_in,
        "tokens_out": provenance.tokens_out,
        "latency_ms": provenance.latency_ms,
        "cost_class": cost_class,
        "correlation_id": provenance.correlation_id or None,
        "success": success,
    }
    if provenance.error:
        row["error_text"] = str(provenance.error)[:_MAX_ERROR_CHARS]

    try:
        thread = threading.Thread(
            target=_insert_row,
            args=(row,),
            daemon=True,
            name="llm-call-log",
        )
        thread.start()
        return thread
    except Exception as e:
        logger.debug(f"llm_calls logging skipped (thread spawn failed): {e}")
        return None


def _insert_row(row: dict) -> None:
    """Insert one row via the Supabase manager; swallow every failure."""
    try:
        from core.supabase_client import get_supabase_manager

        manager = get_supabase_manager()
        if not manager or not manager.is_available():
            return
        manager.insert_llm_call(row)
    except Exception as e:
        logger.debug(f"llm_calls insert failed (row dropped): {e}")
