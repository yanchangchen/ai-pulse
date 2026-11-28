"""
Supabase client wrapper for AI Pulse.
Handles all database operations for trend persistence.
Gracefully degrades if Supabase is unavailable.
"""

import os
import json
import uuid
import time
import logging
from typing import Dict, List, Optional
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Lazy import to avoid hard dependency
_supabase_client = None


def _get_supabase():
    """Lazy-load Supabase client."""
    global _supabase_client
    if _supabase_client is None:
        try:
            from supabase import create_client
            url = os.getenv("SUPABASE_URL")
            key = os.getenv("SUPABASE_KEY")
            
            if not url or not key:
                try:
                    import streamlit as st
                    if hasattr(st, "secrets"):
                        url = url or st.secrets.get("SUPABASE_URL")
                        key = key or st.secrets.get("SUPABASE_KEY")
                except Exception:
                    pass

            if not url or not key:
                # Try loading from .env if python-dotenv available
                try:
                    from dotenv import load_dotenv
                    load_dotenv()
                    url = url or os.getenv("SUPABASE_URL")
                    key = key or os.getenv("SUPABASE_KEY")
                except Exception:
                    pass

            if not url or not key:
                logger.warning("SUPABASE_URL or SUPABASE_KEY not set. Supabase persistence disabled.")
                return None
            
            _supabase_client = create_client(url, key)
            logger.info("Supabase client initialized successfully")
        except ImportError:
            logger.warning("supabase package not installed. Supabase persistence disabled.")
            return None
        except Exception as e:
            logger.error(f"Failed to initialize Supabase client: {e}")
            return None
    
    return _supabase_client


class SupabaseManager:
    """Manager for Supabase operations with graceful error handling."""
    
    def __init__(self):
        self.client = _get_supabase()
        self.available = self.client is not None
    
    def is_available(self) -> bool:
        """Check if Supabase is available."""
        return self.available
    
    def save_trend_run(self, run_timestamp: str, run_date: str,
                       total_articles: int,
                       article_fingerprint: Optional[str] = None) -> Optional[Dict]:
        """
        Save a new trend run to the database.

        Args:
            run_timestamp: ISO format timestamp (e.g., "2026-05-29 16:10:37")
            run_date: Date string (e.g., "2026-05-29")
            total_articles: Total number of articles in this run
            article_fingerprint: Optional stable fingerprint of the article set

        Returns:
            Dict with run record including 'id', or None if failed
        """
        if not self.available:
            return None

        try:
            payload = {
                "run_timestamp": run_timestamp,
                "run_date": run_date,
                "total_articles": total_articles,
            }
            if article_fingerprint:
                payload["article_fingerprint"] = article_fingerprint

            response = self.client.table("trend_runs").insert(payload).execute()

            if response.data:
                logger.info(f"Saved trend run to Supabase: {response.data[0]['id']}")
                return response.data[0]
            return None
        except Exception as e:
            logger.error(f"Failed to save trend run: {e}")
            return None
    
    def save_theme_summary(self, run_id: str, theme_name: str,
                          summary: Dict, article_count: int) -> Optional[Dict]:
        """
        Save a theme summary for a run.

        Args:
            run_id: UUID of the trend run
            theme_name: Name of the theme
            summary: Dict with keys: what_is_happening, engineering_tradeoffs,
                product_impact, why_it_matters, what_to_watch, further_reading
            article_count: Number of articles for this theme

        The summary dict may also carry `_source` and `_generation_log` keys
        set by the summariser.  If the `theme_summaries` table has the
        optional `generation_source` and `generation_log` columns (see
        ``supabase_migration_provenance.sql``), they are populated; otherwise
        the writes silently fall back to the legacy row shape.  The same
        graceful degradation applies to `further_reading` (see
        ``supabase_migration_further_reading.sql``) — required by the
        Quality Evaluation grounding judge.

        Returns:
            Dict with summary record, or None if failed
        """
        if not self.available:
            return None

        try:
            import json as _json
            generation_source = summary.get("_source")
            generation_log_raw = summary.get("_generation_log")
            generation_log = (
                _json.dumps(generation_log_raw)
                if isinstance(generation_log_raw, (dict, list))
                else (generation_log_raw or None)
            )

            payload: Dict[str, object] = {
                "run_id": run_id,
                "theme_name": theme_name,
                "what_is_happening": summary.get("what_is_happening", ""),
                "engineering_tradeoffs": summary.get("engineering_tradeoffs", ""),
                "product_impact": summary.get("product_impact", ""),
                "why_it_matters": summary.get("why_it_matters", ""),
                "what_to_watch": summary.get("what_to_watch", ""),
                "further_reading": summary.get("further_reading", ""),
                "article_count": article_count,
            }
            if generation_source is not None:
                payload["generation_source"] = generation_source
            if generation_log is not None:
                payload["generation_log"] = generation_log

            # Columns added by migrations; older deployments may lack any
            # subset of them.  On a schema error we drop the offending
            # column(s) and retry, so persistence never hard-fails.
            optional_cols = (
                "generation_source", "generation_log", "further_reading",
                "engineering_tradeoffs", "product_impact",
            )

            def _upsert(data: Dict[str, object]):
                # UPSERT on (run_id, theme_name) — re-synthesising a theme
                # for the same run (e.g. on-demand Gemini on Deep Dive) must
                # overwrite the previous row, not violate the constraint.
                return self.client.table("theme_summaries").upsert(
                    data, on_conflict="run_id,theme_name"
                ).execute()

            def _is_schema_drift(exc: Exception) -> bool:
                m = str(exc).lower()
                return "column" in m or any(c in m for c in optional_cols)

            try:
                response = _upsert(payload)
            except Exception as schema_exc:
                if not _is_schema_drift(schema_exc):
                    raise
                msg = str(schema_exc).lower()
                named = [c for c in optional_cols if c in msg]
                drop = set(named) if named else set(optional_cols)
                logger.warning(
                    "theme_summaries schema drift; retrying upsert without %s. "
                    "Run supabase_migration_further_reading.sql / "
                    "supabase_migration_provenance.sql to enable the full row shape.",
                    sorted(drop),
                )
                try:
                    response = _upsert(
                        {k: v for k, v in payload.items() if k not in drop}
                    )
                except Exception as retry_exc:
                    if not _is_schema_drift(retry_exc):
                        raise
                    # Final attempt: core columns only.
                    logger.warning(
                        "theme_summaries still rejecting optional columns; "
                        "final retry with core columns only."
                    )
                    response = _upsert({
                        k: v for k, v in payload.items()
                        if k not in set(optional_cols)
                    })

            if response.data:
                logger.info(f"Saved theme summary for {theme_name}")
                return response.data[0]
            return None
        except Exception as e:
            logger.error(f"Failed to save theme summary for {theme_name}: {e}")
            return None
    
    def save_articles(self, run_id: str, theme_name: str, 
                     articles: List[Dict]) -> Optional[List[Dict]]:
        """
        Save articles for a theme in a run with deduplication.
        Uses UPSERT to avoid duplicate articles by content_hash.
        
        Args:
            run_id: UUID of the trend run
            theme_name: Name of the theme
            articles: List of article dicts with keys: title, summary, source_name, link, published_at, content_hash
        
        Returns:
            List of saved article records, or None if failed
        """
        if not self.available or not articles:
            return None
        
        try:
            # Deduplicate within the batch by content_hash first
            seen_hashes = set()
            rows = []
            
            for article in articles:
                content_hash = article.get("content_hash")
                
                # Skip if we've already seen this hash in this batch
                if content_hash and content_hash in seen_hashes:
                    logger.debug(f"Skipping duplicate article with hash {content_hash} in batch")
                    continue
                
                if content_hash:
                    seen_hashes.add(content_hash)
                
                pub_at = article.get("published_at") or article.get("published_date") or datetime.now(timezone.utc).isoformat()
                src_name = article.get("source_name") or "Unknown Source"
                
                rows.append({
                    "run_id": run_id,
                    "theme_name": theme_name,
                    "title": article.get("title", "")[:500],  # Limit title length
                    "summary": article.get("summary", ""),
                    "source_name": src_name,
                    "link": article.get("link", ""),
                    "published_at": pub_at,
                    "content_hash": content_hash
                })
            
            if not rows:
                logger.info(f"No unique articles to save for {theme_name}")
                return []
            
            # Use UPSERT to handle duplicates across runs: on conflict with (content_hash, theme_name), do nothing
            response = self.client.table("articles").upsert(
                rows,
                on_conflict="content_hash,theme_name"
            ).execute()
            
            if response.data:
                logger.info(f"Saved/deduplicated {len(response.data)} articles for {theme_name}")
                return response.data
            return []
        except Exception as e:
            logger.error(f"Failed to save articles for {theme_name}: {e}")
            return None

    def get_processed_hashes(self, theme_name: str) -> set:
        """Return the set of content hashes already summarised for a theme."""
        if not self.available:
            return set()
        try:
            response = self.client.table("processed_articles") \
                .select("content_hash") \
                .eq("theme_name", theme_name) \
                .execute()
            if response.data:
                return {row["content_hash"] for row in response.data if row.get("content_hash")}
        except Exception as e:
            logger.error(f"Failed to get processed hashes for {theme_name}: {e}")
        return set()

    def mark_processed(self, theme_name: str, content_hashes: List[str]) -> None:
        """Mark content hashes as summarised for a theme."""
        if not self.available or not content_hashes:
            return
        try:
            rows = [
                {"theme_name": theme_name, "content_hash": h}
                for h in set(content_hashes) if h
            ]
            if not rows:
                return
            self.client.table("processed_articles").upsert(
                rows, on_conflict="theme_name,content_hash"
            ).execute()
            logger.info(f"Marked {len(rows)} articles as processed for {theme_name}")
        except Exception as e:
            logger.error(f"Failed to mark processed for {theme_name}: {e}")

    def get_times_seen(self, theme_name: str) -> Dict[str, int]:
        """Return {content_hash: times_seen} for waiting articles (Phase 4.3).

        Only positive counts: zero means the article is not in the backlog.
        Degrades to {} when the column/migration is absent so pre-migration
        databases keep working (the caller falls back to its local counter).
        """
        if not self.available:
            return {}
        try:
            response = self.client.table("articles") \
                .select("content_hash,times_seen") \
                .eq("theme_name", theme_name) \
                .gt("times_seen", 0) \
                .execute()
            return {
                row["content_hash"]: int(row["times_seen"])
                for row in (response.data or [])
                if row.get("content_hash")
            }
        except Exception as e:
            logger.error(f"Failed to get times-seen for {theme_name}: {e}")
        return {}

    def record_run_seen(self, theme_name: str, waiting_hashes: List[str],
                        included_hashes: List[str]) -> None:
        """Advance the fair-drain counter via the record_article_seen RPC.

        UPDATE-only (see supabase_migration_times_seen.sql): waiting hashes
        +1, included hashes reset to 0. Never inserts article rows.
        """
        if not self.available or (not waiting_hashes and not included_hashes):
            return
        try:
            self.client.rpc("record_article_seen", {
                "p_theme": theme_name,
                "p_waiting": waiting_hashes,
                "p_included": included_hashes,
            }).execute()
            logger.info(
                f"Times-seen advanced for {theme_name}: "
                f"+1 on {len(waiting_hashes)}, reset on {len(included_hashes)}"
            )
        except Exception as e:
            logger.error(f"Failed to record times-seen for {theme_name}: {e}")

    def get_classifications(self, content_hashes: List[str]) -> Dict[str, str]:
        """
        Return {content_hash: theme_name} for previously saved articles.

        Serves as the cloud side of the cross-run classification cache:
        every classified article is upserted into the ``articles`` table
        with (content_hash, theme_name), so no extra table is needed.
        If a hash was saved under several themes across runs, the first
        row encountered wins.

        Args:
            content_hashes: content hashes to look up

        Returns:
            Dict mapping content_hash to theme_name
        """
        if not self.available or not content_hashes:
            return {}
        found: Dict[str, str] = {}
        try:
            # Chunk the .in_() filter to keep request URLs within limits
            for i in range(0, len(content_hashes), 200):
                chunk = [h for h in content_hashes[i:i + 200] if h]
                if not chunk:
                    continue
                response = self.client.table("articles") \
                    .select("content_hash,theme_name") \
                    .in_("content_hash", chunk) \
                    .execute()
                for row in response.data or []:
                    h = row.get("content_hash")
                    t = row.get("theme_name")
                    if h and t and h not in found:
                        found[h] = t
        except Exception as e:
            logger.error(f"Failed to look up cached classifications: {e}")
        return found

    def get_latest_run(self) -> Optional[Dict]:
        """
        Retrieve the most recent trend run.
        
        Returns:
            Dict with latest run record, or None if failed
        """
        if not self.available:
            return None
        
        try:
            response = self.client.table("trend_runs")\
                .select("*")\
                .order("run_timestamp", desc=True)\
                .limit(1)\
                .execute()
            
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            logger.error(f"Failed to get latest run: {e}")
            return None
    
    def get_theme_history(self, theme_name: str, limit: int = 10) -> Optional[List[Dict]]:
        """
        Retrieve historical summaries for a theme.
        
        Args:
            theme_name: Name of the theme
            limit: Maximum number of records to retrieve
        
        Returns:
            List of theme summary records, or None if failed
        """
        if not self.available:
            return None
        
        try:
            response = self.client.table("theme_summaries")\
                .select("*")\
                .eq("theme_name", theme_name)\
                .order("created_at", desc=True)\
                .limit(limit)\
                .execute()
            
            if response.data:
                return response.data
            return None
        except Exception as e:
            logger.error(f"Failed to get theme history for {theme_name}: {e}")
            return None
    
    def get_run_by_id(self, run_id: str) -> Optional[Dict]:
        """
        Retrieve a specific trend run by ID.
        
        Args:
            run_id: UUID of the trend run
        
        Returns:
            Dict with run record, or None if failed
        """
        if not self.available:
            return None
        
        try:
            response = self.client.table("trend_runs")\
                .select("*")\
                .eq("id", run_id)\
                .execute()
            
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            logger.error(f"Failed to get run by ID {run_id}: {e}")
            return None

    def delete_run(self, run_id: str) -> bool:
        """
        Purge a trend run and all associated theme summaries, articles, and metrics.
        """
        if not self.available or not run_id:
            return False
        try:
            self.client.table("theme_summaries").delete().eq("run_id", run_id).execute()
            self.client.table("articles").delete().eq("run_id", run_id).execute()
            try:
                self.client.table("trend_metrics").delete().eq("run_id", run_id).execute()
            except Exception:
                pass
            try:
                self.client.table("quality_evaluations").delete().eq("run_id", run_id).execute()
            except Exception:
                pass
            self.client.table("trend_runs").delete().eq("id", run_id).execute()
            logger.info("Successfully deleted trend run ID '%s' and associated records.", run_id)
            return True
        except Exception as e:
            logger.error(f"Failed to delete run {run_id}: {e}")
            return False
    
    def get_summaries_for_run(self, run_id: str) -> Optional[List[Dict]]:
        """
        Retrieve all theme summaries for a specific run.
        
        Args:
            run_id: UUID of the trend run
        
        Returns:
            List of theme summary records, or None if failed
        """
        if not self.available:
            return None
        
        try:
            response = self.client.table("theme_summaries")\
                .select("*")\
                .eq("run_id", run_id)\
                .execute()
            
            if response.data:
                return response.data
            return None
        except Exception as e:
            logger.error(f"Failed to get summaries for run {run_id}: {e}")
            return None

    def get_summaries_by_run(self, run_id: str) -> Dict[str, Dict]:
        """
        Retrieve theme summaries for a run, keyed by theme_name.
        
        Args:
            run_id: UUID of the trend run
            
        Returns:
            Dict mapping theme_name -> summary dict, or empty dict if none/failed
        """
        summaries = self.get_summaries_for_run(run_id)
        if not summaries:
            return {}
        return {s["theme_name"]: s for s in summaries if "theme_name" in s}

    def get_runs_summary(self, limit: int = 10) -> Optional[List[Dict]]:
        """
        Retrieve a summary of recent trend runs.
        
        Args:
            limit: Maximum number of runs to retrieve.
            
        Returns:
            List of run records, or None if failed.
        """
        return self.get_all_runs(limit=limit)

    def get_summaries_across_runs(
        self,
        theme_filter: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        source_filter: Optional[str] = None,
        limit: int = 200,
        prefer_recent: bool = True,
    ) -> Optional[List[Dict]]:
        """Retrieve theme summaries across historical runs within an optional date range
        and theme/source filter.

        When `prefer_recent` is True (default), it prioritizes the newest runs in the
        window up to `limit`, and returns them sorted chronologically ascending for
        the LLM's forward narrative.

        Returns:
            List of summary dicts (with run_timestamp/run_date), or None on failure.
        """
        if not self.available:
            return None

        try:
            # Step 1 – fetch runs in the requested date window
            # Order newest first if prefer_recent to ensure recent dates are never cut off
            runs_q = self.client.table("trend_runs") \
                .select("id, run_timestamp, run_date")
            if date_from:
                runs_q = runs_q.gte("run_timestamp", date_from)
            if date_to:
                runs_q = runs_q.lte("run_timestamp", date_to)

            runs_q = runs_q.order("run_timestamp", desc=prefer_recent)

            runs_resp = runs_q.execute()
            if not runs_resp.data:
                return []

            run_lookup = {r["id"]: r for r in runs_resp.data}
            run_ids = list(run_lookup.keys())

            if source_filter:
                art_resp = self.client.table("themed_articles") \
                    .select("run_id") \
                    .eq("source_name", source_filter) \
                    .in_("run_id", run_ids) \
                    .execute()
                if art_resp.data:
                    matching_run_ids = set(a["run_id"] for a in art_resp.data)
                    run_ids = [rid for rid in run_ids if rid in matching_run_ids]
                else:
                    return []

            # Step 2 – fetch summaries for those runs
            # Supabase .in_() has a practical limit; chunk if necessary
            all_summaries: List[Dict] = []
            chunk_size = 50
            for i in range(0, len(run_ids), chunk_size):
                chunk = run_ids[i:i + chunk_size]
                sum_q = self.client.table("theme_summaries") \
                    .select("run_id, theme_name, what_is_happening, why_it_matters, what_to_watch, article_count") \
                    .in_("run_id", chunk)
                if theme_filter:
                    sum_q = sum_q.eq("theme_name", theme_filter)
                sum_resp = sum_q.execute()
                if sum_resp.data:
                    all_summaries.extend(sum_resp.data)

            # Step 3 – augment with run metadata and sort chronologically (ascending)
            results: List[Dict] = []
            for s in all_summaries:
                run_meta = run_lookup.get(s["run_id"])
                if run_meta:
                    results.append({
                        **s,
                        "run_timestamp": run_meta["run_timestamp"],
                        "run_date": run_meta["run_date"],
                    })

            # Always return chronologically ascending for Sage narrative
            results.sort(key=lambda r: r["run_timestamp"])
            if limit and len(results) > limit:
                # If prefer_recent, keep the newest 'limit' items (which are at the tail of the sorted list)
                results = results[-limit:] if prefer_recent else results[:limit]
            return results
        except Exception as e:
            logger.error(f"Failed to get summaries across runs: {e}")
            return None

    def get_articles_for_run(self, run_id: str, theme_name: Optional[str] = None) -> Optional[List[Dict]]:
        """
        Retrieve articles for a specific run, optionally filtered by theme.
        
        Args:
            run_id: UUID of the trend run
            theme_name: Optional theme name to filter by
        
        Returns:
            List of article records, or None if failed
        """
        if not self.available:
            return None
        
        try:
            query = self.client.table("articles")\
                .select("*")\
                .eq("run_id", run_id)
            
            if theme_name:
                query = query.eq("theme_name", theme_name)
            
            response = query.execute()
            
            if response.data:
                return response.data
            return None
        except Exception as e:
            logger.error(f"Failed to get articles for run {run_id}: {e}")
            return None
    
    def update_sync_metadata(self, key: str, value: str) -> Optional[Dict]:
        """
        Update sync metadata (last sync time, status, etc).
        
        Args:
            key: Metadata key (e.g., "last_sync_time")
            value: Metadata value
        
        Returns:
            Dict with updated record, or None if failed
        """
        if not self.available:
            return None
        
        try:
            response = self.client.table("sync_metadata")\
                .upsert({"key": key, "value": value}, on_conflict="key").execute()
            
            if response.data:
                logger.info(f"Updated sync metadata: {key}")
                return response.data[0]
            return None
        except Exception as e:
            logger.error(f"Failed to update sync metadata {key}: {e}")
            return None
    
    def get_sync_metadata(self, key: str) -> Optional[str]:
        """
        Retrieve sync metadata value.

        Args:
            key: Metadata key to retrieve

        Returns:
            Metadata value, or None if not found or failed
        """
        if not self.available:
            return None

        try:
            response = self.client.table("sync_metadata")\
                .select("value")\
                .eq("key", key)\
                .execute()

            if response.data:
                return response.data[0]["value"]
            return None
        except Exception as e:
            logger.error(f"Failed to get sync metadata {key}: {e}")
            return None

    # ------------------------------------------------------------------
    # App settings (evaluation-driven config persistence)
    # ------------------------------------------------------------------

    def get_app_setting(self, key: str) -> Optional[Dict]:
        """
        Fetch one row from the app_settings key-value table.

        Args:
            key: Setting key (e.g. "custom_settings", "auto_remediation_state")

        Returns:
            The stored JSONB value as a dict, or None if the row does not
            exist / Supabase is unavailable / the table is missing (the
            app_settings migration has not been run). Never raises.
        """
        if not self.available:
            return None

        try:
            response = self.client.table("app_settings")\
                .select("value")\
                .eq("key", key)\
                .limit(1)\
                .execute()

            if response.data:
                return response.data[0].get("value")
            return None
        except Exception as e:
            logger.warning(f"Failed to read app_settings '{key}' (run supabase_migration_app_settings.sql if not yet): {e}")
            return None

    def upsert_app_setting(self, key: str, value: Dict) -> bool:
        """
        Write one row to the app_settings key-value table (last-write-wins).

        Returns True on success; False (never raises) if Supabase is
        unavailable, the table is missing, or the write fails — callers
        treat the local file as the fallback.
        """
        if not self.available:
            return False

        try:
            from datetime import datetime, timezone
            self.client.table("app_settings")\
                .upsert(
                    {"key": key, "value": value,
                     "updated_at": datetime.now(timezone.utc).isoformat()},
                    on_conflict="key",
                )\
                .execute()
            return True
        except Exception as e:
            logger.warning(f"Failed to upsert app_settings '{key}' (run supabase_migration_app_settings.sql if not yet): {e}")
            return False

    def insert_llm_call(self, row: Dict) -> bool:
        """
        Append one row to the llm_calls observability log (Model Gateway).

        Rows are built by ``core/ai_gateway/call_log.py`` from a completed
        call's ``Provenance``: task_type, provider, model, tokens_in/out,
        latency_ms, cost_class, correlation_id, success, error_text.

        Returns True on success; False (never raises) if Supabase is
        unavailable, the table is missing, or the write fails — the call
        log is best-effort observability and must never break an LLM task.
        """
        if not self.available:
            return False

        try:
            self.client.table("llm_calls").insert(row).execute()
            return True
        except Exception as e:
            logger.warning(
                f"Failed to insert llm_calls row (run supabase_migration_llm_calls.sql if not yet): {e}"
            )
            return False

    # ------------------------------------------------------------------
    # New query helpers for analytics, trends, and wiki pages
    # ------------------------------------------------------------------

    def get_all_runs(self, limit: int = 5, offset: int = 0) -> Optional[List[Dict]]:
        """
        Retrieve paginated trend runs (newest first).

        Args:
            limit: Number of runs to return per page.
            offset: Offset for pagination.

        Returns:
            List of run records, or None if failed.
        """
        if not self.available:
            return None

        try:
            response = self.client.table("trend_runs")\
                .select("*")\
                .order("run_timestamp", desc=True)\
                .range(offset, offset + limit - 1)\
                .execute()

            return response.data if response.data else []
        except Exception as e:
            logger.error(f"Failed to get paginated runs: {e}")
            return None

    def get_total_run_count(self) -> int:
        """Return the total number of trend runs in the database."""
        if not self.available:
            return 0

        try:
            response = self.client.table("trend_runs")\
                .select("id", count="exact")\
                .execute()
            return response.count if response.count is not None else 0
        except Exception as e:
            logger.error(f"Failed to get total run count: {e}")
            return 0

    def get_theme_article_counts_by_run(self) -> Optional[List[Dict]]:
        """
        Return article counts grouped by run and theme.
        Used by Trend Analytics for the heatmap and momentum charts.

        Returns:
            List of dicts: [{run_id, run_timestamp, run_date, theme_name, count}, ...]
        """
        if not self.available:
            return None

        try:
            # Get all runs
            runs_resp = self.client.table("trend_runs")\
                .select("id, run_timestamp, run_date")\
                .order("run_timestamp", desc=False)\
                .execute()

            if not runs_resp.data:
                return []

            # Get all theme summaries (which contain article_count)
            summaries_resp = self.client.table("theme_summaries")\
                .select("run_id, theme_name, article_count")\
                .execute()

            if not summaries_resp.data:
                return []

            # Build a lookup: run_id -> run metadata
            run_lookup = {r["id"]: r for r in runs_resp.data}

            results = []
            for s in summaries_resp.data:
                run = run_lookup.get(s["run_id"])
                if run:
                    results.append({
                        "run_id": s["run_id"],
                        "run_timestamp": run["run_timestamp"],
                        "run_date": run["run_date"],
                        "theme_name": s["theme_name"],
                        "count": s["article_count"]
                    })

            return results
        except Exception as e:
            logger.error(f"Failed to get theme article counts by run: {e}")
            return None

    def search_articles(self, query: str = "", theme_filter: Optional[str] = None,
                       date_from: Optional[str] = None, date_to: Optional[str] = None,
                       source_filter: Optional[str] = None,
                       limit: int = 50) -> Optional[List[Dict]]:
        """
        Search articles with optional filters.

        Args:
            query: Free-text search string (matched against title).
            theme_filter: Optional theme name to filter by.
            date_from: ISO date string lower bound on published_at.
            date_to: ISO date string upper bound on published_at.
            source_filter: Optional source name to filter by.
            limit: Max articles to return.

        Returns:
            List of article dicts, or None on failure.
        """
        if not self.available:
            return None

        try:
            q = self.client.table("articles")\
                .select("id, run_id, theme_name, title, summary, source_name, link, published_at")\
                .order("published_at", desc=True)\
                .limit(limit)

            if query:
                q = q.ilike("title", f"%{query}%")

            if theme_filter:
                q = q.eq("theme_name", theme_filter)

            if date_from:
                q = q.gte("published_at", date_from)

            if date_to:
                q = q.lte("published_at", date_to)

            if source_filter:
                q = q.eq("source_name", source_filter)

            response = q.execute()
            articles = response.data if response.data else []

            # Augment each article with the run_timestamp from trend_runs
            if articles:
                unique_run_ids = list({a["run_id"] for a in articles if a.get("run_id")})
                run_lookup: Dict[str, str] = {}
                chunk_size = 50
                for i in range(0, len(unique_run_ids), chunk_size):
                    chunk = unique_run_ids[i:i + chunk_size]
                    try:
                        runs_resp = self.client.table("trend_runs") \
                            .select("id, run_timestamp") \
                            .in_("id", chunk) \
                            .execute()
                        if runs_resp.data:
                            for r in runs_resp.data:
                                run_lookup[r["id"]] = r["run_timestamp"]
                    except Exception:
                        pass  # gracefully degrade — articles still usable without run_timestamp
                for a in articles:
                    a["run_timestamp"] = run_lookup.get(a.get("run_id"), None)

            return articles
        except Exception as e:
            logger.error(f"Failed to search articles: {e}")
            return None

    def get_articles_for_context(
        self,
        theme_filter: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        source_filter: Optional[str] = None,
        limit: int = 300,
        query: Optional[str] = None,
    ) -> Optional[List[Dict]]:
        """
        Retrieve articles for Sage's Tier-1 evidence context (Phases 2.1/2.3).

        With ``query`` set (the raw user question), keyword matching runs in
        Postgres FTS via the ``match_articles`` RPC — the candidate pool is
        every keyword-matching article ranked by ``ts_rank``, not just the
        newest window. With no ``query``, or when the RPC returns nothing /
        fails (function not migrated yet, no keyword matches), this falls
        back to a bounded newest-first window within the filters. Either way
        the caller (core/sage_agent.py) makes the final selection
        client-side; SQL bounds and matches, judgement stays in Python.

        Args:
            theme_filter: Optional theme name to filter by.
            date_from: ISO date string lower bound on published_at.
            date_to: ISO date string upper bound on published_at.
            source_filter: Optional source name to filter by.
            limit: Max candidate articles to return.
            query: Optional question text for SQL-side FTS matching.

        Returns:
            List of article dicts (best matches / newest first), or None on
            failure.
        """
        if not self.available:
            return None

        # Phase 2.3: SQL-side keyword matching via FTS. The RPC degrades to
        # the newest-first window below on any failure or empty match, so
        # Sage always has some Tier-1 evidence.
        if query and query.strip():
            try:
                params = {"query": query, "result_limit": limit}
                if theme_filter:
                    params["theme_filter"] = theme_filter
                if date_from:
                    params["date_from"] = date_from
                if date_to:
                    params["date_to"] = date_to
                if source_filter:
                    params["source_filter"] = source_filter
                response = self.client.rpc("match_articles", params).execute()
                if isinstance(response.data, list) and response.data:
                    return response.data
                logger.info(
                    "match_articles returned no rows; falling back to the "
                    "newest-first article window"
                )
            except Exception as e:
                # Pre-migration databases (no match_articles function) keep
                # working through the fallback path.
                logger.warning(f"match_articles RPC failed, using window fetch: {e}")

        try:
            q = self.client.table("articles") \
                .select("id, run_id, theme_name, title, summary, source_name, link, published_at") \
                .order("published_at", desc=True) \
                .limit(limit)

            if theme_filter:
                q = q.eq("theme_name", theme_filter)
            if date_from:
                q = q.gte("published_at", date_from)
            if date_to:
                q = q.lte("published_at", date_to)
            if source_filter:
                # NB: filter source_name on the real articles table — the
                # themed_articles reference in get_summaries_across_runs is a
                # latent bug against a table that does not exist.
                q = q.eq("source_name", source_filter)

            response = q.execute()
            return response.data if response.data else []
        except Exception as e:
            logger.error(f"Failed to fetch articles for context: {e}")
            return None

    # ------------------------------------------------------------------
    # Article Embeddings (Phase 3, dense half of Pillar A)
    # ------------------------------------------------------------------

    def get_unembedded_articles(self, limit: int = 200) -> List[Dict]:
        """Return articles that still lack an embedding, oldest first.

        Backed by the ``get_unembedded_articles`` RPC (a LEFT JOIN against
        ``article_embeddings``), so an article drops out the moment its
        embedding lands — both the ingest step and the backfill drain the
        same set and are resumable by re-running.  Returns ``[]`` (never
        raises) when unavailable, the RPC is missing, or the set is empty.
        """
        if not self.available:
            return []
        try:
            response = self.client.rpc(
                "get_unembedded_articles", {"limit_count": limit}
            ).execute()
            rows = []
            for r in response.data or []:
                if not r.get("article_id"):
                    continue
                r["run_date"] = str(r.get("run_date") or "")
                rows.append(r)
            return rows
        except Exception as e:
            # Pre-migration databases (no RPC) degrade to "nothing to embed".
            logger.warning(f"get_unembedded_articles RPC failed: {e}")
            return []

    def get_articles_for_reembedding(
        self, limit: int = 200, offset: int = 0, exclude_model: Optional[str] = None
    ) -> List[Dict]:
        """Return existing embedding rows for a forced re-embed (model migration).

        ``get_unembedded_articles()`` deliberately excludes rows that already
        have a vector, which makes a provider switch impossible to complete —
        the old-model rows would never be revisited.  This selects those rows
        instead, carrying the stored ``chunk_text`` (the exact string that was
        embedded, so vectors stay re-derivable) and ``run_date``.  Paginated by
        ``article_id`` so a backfill-plus-upsert loop sees a stable window.

        ``exclude_model`` filters out rows already written by the target model,
        making re-embedding idempotent and resumable: every successful upsert
        removes its rows from the next query, so a retry after an interruption
        continues instead of re-processing the same page.

        Returns ``[]`` (never raises) when unavailable or empty.
        """
        if not self.available:
            return []
        try:
            query = self.client.table("article_embeddings").select(
                "article_id, run_date, chunk_text"
            )
            if exclude_model:
                query = query.neq("embedding_model", exclude_model)
            response = (
                query.order("article_id")
                .range(offset, offset + max(limit, 1) - 1)
                .execute()
            )
            rows = []
            for r in response.data or []:
                if not r.get("article_id"):
                    continue
                r["run_date"] = str(r.get("run_date") or "")
                r["chunk_text"] = r.get("chunk_text") or ""
                rows.append(r)
            return rows
        except Exception as e:
            logger.warning(f"get_articles_for_reembedding failed: {e}")
            return []

    def insert_article_embeddings(self, rows: List[Dict]) -> bool:
        """Upsert embedding rows (best-effort, never raises).

        ``article_embeddings`` has ``UNIQUE(article_id)``, so a re-embed
        (provider migration or model refresh) must replace the existing row
        rather than append a duplicate.  An upsert on ``article_id`` makes
        the write idempotent and enables the Phase 3.5 controlled switch:
        re-running the backfill overwrites old-model vectors in place.

        Args:
            rows: List of dicts with article_id / run_date / chunk_text /
                embedding (list of 768 floats) / embedding_model.

        Returns:
            True when the upsert succeeded, False otherwise (including a
            concurrent ingest/backfill race — logged, not raised).
        """
        if not self.available or not rows:
            return False
        last_error = None
        # One retry: the sync Supabase client occasionally surfaces a
        # transient Windows socket error (WinError 10035) under rapid
        # backfill traffic, which is safe to retry on an idempotent upsert.
        for attempt in range(2):
            try:
                self.client.table("article_embeddings").upsert(
                    rows, on_conflict="article_id"
                ).execute()
                return True
            except Exception as e:
                last_error = e
                if attempt == 0:
                    time.sleep(1.0)
        logger.warning(f"Failed to upsert article embeddings: {last_error}")
        return False

    def match_article_embeddings(
        self,
        query_embedding: List[float],
        match_count: int = 10,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        theme_filter: Optional[str] = None,
    ) -> Optional[List[Dict]]:
        """Dense nearest-neighbour lookup (Phase 3 gate + Phase 5 dense leg).

        Returns the top-k cosine-similar rows joined with article metadata
        (title/summary/theme/source/link/published_at/run_date), or ``None``
        when unavailable or the RPC is missing. Optional date/theme bounds
        are applied by the RPC *after* an over-fetched ANN scan (HNSW+filter
        under-fill gotcha — see supabase_migration_hybrid_retrieval.sql).
        Bounds are sent only when set, so the unbounded Phase 3 gate call
        keeps working on pre-Phase-5 databases; a bounded call there fails
        to resolve and returns None, degrading the hybrid to its sparse leg.
        """
        if not self.available or not query_embedding:
            return None
        try:
            params = {"query_embedding": query_embedding, "match_count": match_count}
            if date_from:
                params["date_from"] = date_from
            if date_to:
                params["date_to"] = date_to
            if theme_filter:
                params["theme_filter"] = theme_filter
            response = self.client.rpc("match_article_embeddings", params).execute()
            return response.data if isinstance(response.data, list) else None
        except Exception as e:
            logger.warning(f"match_article_embeddings RPC failed: {e}")
            return None

    def match_articles_fts(
        self,
        query: str,
        theme_filter: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        source_filter: Optional[str] = None,
        result_limit: int = 50,
    ) -> Optional[List[Dict]]:
        """Pure FTS leg for hybrid retrieval (Phase 5.2).

        Unlike ``get_articles_for_context``, this does NOT fall back to a
        newest-first window on empty matches — a fusion leg must contain only
        genuine keyword matches, or RRF would rank recency noise as evidence.
        Returns ``None`` on failure / missing RPC so the dense leg can carry
        the query alone.
        """
        if not self.available or not (query or "").strip():
            return None
        try:
            params = {"query": query, "result_limit": result_limit}
            if theme_filter:
                params["theme_filter"] = theme_filter
            if date_from:
                params["date_from"] = date_from
            if date_to:
                params["date_to"] = date_to
            if source_filter:
                params["source_filter"] = source_filter
            response = self.client.rpc("match_articles", params).execute()
            return response.data if isinstance(response.data, list) else None
        except Exception as e:
            logger.warning(f"match_articles RPC failed (hybrid sparse leg): {e}")
            return None

    # ------------------------------------------------------------------
    # theme_first_seen (Phase 5.4)
    # ------------------------------------------------------------------

    def get_theme_first_seen(self) -> Optional[Dict[str, Dict]]:
        """Return {theme_name: row} from theme_first_seen, or None on failure.

        Small table (one row per theme); fetched whole for Sage's
        first-appearance annotation, replacing the render-time window scan.
        """
        if not self.available:
            return None
        try:
            response = self.client.table("theme_first_seen").select("*").execute()
            if isinstance(response.data, list):
                return {r["theme_name"]: r for r in response.data if r.get("theme_name")}
            return None
        except Exception as e:
            logger.warning(f"theme_first_seen fetch failed: {e}")
            return None

    def record_theme_first_seen(
        self, theme_name: str, first_seen_date: str,
        run_id: str = "", evidence_summary: str = "",
    ) -> bool:
        """Insert-if-absent the theme's first appearance (ingest-time, 5.4).

        Upsert with ignore-duplicates => ON CONFLICT DO NOTHING: the earliest
        archived run wins and later runs never overwrite it.
        """
        if not self.available or not theme_name or not first_seen_date:
            return False
        try:
            self.client.table("theme_first_seen").upsert(
                {
                    "theme_name": theme_name,
                    "first_seen_date": first_seen_date,
                    "first_seen_run_id": run_id or None,
                    "evidence_summary": (evidence_summary or "")[:500],
                },
                on_conflict="theme_name",
                ignore_duplicates=True,
            ).execute()
            return True
        except Exception as e:
            logger.warning(f"theme_first_seen record failed for {theme_name}: {e}")
            return False

    # ------------------------------------------------------------------
    # monthly_syntheses (Phase 6.1 — Tier-3 hierarchical memory)
    # ------------------------------------------------------------------

    def get_run_months(self) -> Optional[List[str]]:
        """Sorted unique 'YYYY-MM' months that have archived runs.

        Cheap date-column scan of trend_runs; the due-check input for the
        monthly rollup job. None on failure/unavailable (never raises).
        """
        if not self.available:
            return None
        try:
            response = self.client.table("trend_runs")\
                .select("run_date")\
                .order("run_date", desc=False)\
                .execute()
            if not isinstance(response.data, list):
                return None
            months = sorted({
                str(r["run_date"])[:7] for r in response.data
                if r.get("run_date")
            })
            return months
        except Exception as e:
            logger.warning(f"Failed to list run months: {e}")
            return None

    def get_theme_briefs_for_month(self, year_month: str) -> Optional[List[Dict]]:
        """All themes' daily briefs for one calendar month, chronological.

        Bounds on trend_runs.run_date (the local DATE key — not
        run_timestamp), then chunks run_ids into theme_summaries. Selects
        the narrative sections plus further_reading/generation_source for
        the rollup; on schema drift (older DB without those columns) it
        retries with the core select. Returns [] when the month has no
        runs, None on failure/unavailable/invalid input.
        """
        if not self.available:
            return None
        import re
        if not year_month or not re.fullmatch(r"\d{4}-\d{2}", year_month):
            return None
        try:
            import calendar
            year, month = int(year_month[:4]), int(year_month[5:7])
            if not 1 <= month <= 12:
                return None
            last_day = calendar.monthrange(year, month)[1]
            date_first = f"{year_month}-01"
            date_last = f"{year_month}-{last_day:02d}"

            runs_resp = self.client.table("trend_runs")\
                .select("id, run_timestamp, run_date")\
                .gte("run_date", date_first)\
                .lte("run_date", date_last)\
                .execute()
            if not runs_resp.data:
                return []
            run_lookup = {r["id"]: r for r in runs_resp.data}
            run_ids = list(run_lookup.keys())

            full_select = (
                "run_id, theme_name, what_is_happening, why_it_matters, "
                "what_to_watch, further_reading, generation_source, article_count"
            )
            core_select = (
                "run_id, theme_name, what_is_happening, why_it_matters, "
                "what_to_watch, article_count"
            )
            all_briefs: List[Dict] = []
            select_cols = full_select
            chunk_size = 50
            i = 0
            while i < len(run_ids):
                chunk = run_ids[i:i + chunk_size]
                try:
                    resp = self.client.table("theme_summaries")\
                        .select(select_cols)\
                        .in_("run_id", chunk)\
                        .execute()
                except Exception:
                    if select_cols == full_select:
                        # Schema drift: retry without the migration-added
                        # columns (read-side analogue of save_theme_summary's
                        # retry ladder).
                        logger.warning(
                            "theme_summaries optional columns missing; retrying "
                            "core select (run supabase_migration_provenance.sql "
                            "/ supabase_migration_further_reading.sql if not yet)"
                        )
                        select_cols = core_select
                        continue  # retry the same chunk
                    raise
                if resp.data:
                    all_briefs.extend(resp.data)
                i += chunk_size

            results: List[Dict] = []
            for s in all_briefs:
                run_meta = run_lookup.get(s["run_id"])
                if run_meta:
                    results.append({
                        **s,
                        "run_timestamp": run_meta["run_timestamp"],
                        "run_date": run_meta["run_date"],
                    })
            results.sort(key=lambda r: r["run_timestamp"])
            return results
        except Exception as e:
            logger.warning(f"Failed to fetch theme briefs for {year_month}: {e}")
            return None

    def get_monthly_syntheses(self, year_months: Optional[List[str]] = None) -> Optional[List[Dict]]:
        """Full monthly_syntheses rows (tiny table), newest month first.

        Serves both the rollup due-check and the History-page expander.
        None on failure/unavailable/pre-migration (never raises).
        """
        if not self.available:
            return None
        try:
            query = self.client.table("monthly_syntheses").select("*")
            if year_months:
                query = query.in_("year_month", year_months)
            response = query.order("year_month", desc=True).execute()
            if isinstance(response.data, list):
                return response.data
            return None
        except Exception as e:
            logger.warning(
                f"Failed to read monthly_syntheses "
                f"(run supabase_migration_monthly_syntheses.sql if not yet): {e}"
            )
            return None

    def upsert_monthly_synthesis(
        self, year_month: str, theme_name: str, macro_summary: str,
        key_milestones: List[Dict], source_run_count: int,
        generation_source: str = "",
    ) -> bool:
        """Idempotent overwrite of one (month, theme) Tier-3 synthesis.

        updated_at is stamped explicitly on EVERY upsert — DEFAULT NOW()
        only fires on INSERT, and the refresh-staleness rules read it.
        created_at is omitted so the INSERT default applies and UPDATE
        preserves the original. Returns False (never raises) on failure.
        """
        if not self.available or not year_month or not theme_name or not macro_summary:
            return False
        payload = {
            "year_month": year_month,
            "theme_name": theme_name,
            "macro_summary": macro_summary,
            "key_milestones": key_milestones if isinstance(key_milestones, list) else [],
            "source_run_count": int(source_run_count),
            "generation_source": (generation_source or "")[:100],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        last_error = None
        # One retry: the sync Supabase client occasionally surfaces a
        # transient Windows socket error (WinError 10035) under rapid
        # backfill traffic, which is safe to retry on an idempotent upsert
        # (same pattern as insert_article_embeddings).
        for attempt in range(2):
            try:
                self.client.table("monthly_syntheses").upsert(
                    payload, on_conflict="year_month,theme_name",
                ).execute()
                return True
            except Exception as e:
                last_error = e
                if attempt == 0:
                    time.sleep(1.0)
        logger.warning(
            f"Failed to upsert monthly synthesis {year_month}/{theme_name} "
            f"(run supabase_migration_monthly_syntheses.sql if not yet): {last_error}"
        )
        return False

    def get_keyword_velocity(self, keywords: List[str], limit: int = 30) -> Optional[List[Dict]]:
        """
        Count keyword mentions per run across article titles and summaries.

        Args:
            keywords: List of keywords to track.
            limit: Max number of runs to scan (most recent first).

        Returns:
            List of dicts: [{run_timestamp, run_date, keyword, count}, ...]
        """
        if not self.available:
            return None

        try:
            # Get recent runs
            runs_resp = self.client.table("trend_runs")\
                .select("id, run_timestamp, run_date")\
                .order("run_timestamp", desc=True)\
                .limit(limit)\
                .execute()

            if not runs_resp.data:
                return []

            run_ids = [r["id"] for r in runs_resp.data]
            run_lookup = {r["id"]: r for r in runs_resp.data}

            results = []

            # Process each run
            for run_id in run_ids:
                articles_resp = self.client.table("articles")\
                    .select("title, summary")\
                    .eq("run_id", run_id)\
                    .execute()

                if not articles_resp.data:
                    continue

                # Combine all text for this run
                full_text = ""
                for a in articles_resp.data:
                    full_text += f" {a.get('title', '')} {a.get('summary', '')}"
                full_text = full_text.lower()

                run_meta = run_lookup[run_id]
                for kw in keywords:
                    count = full_text.count(kw.lower())
                    results.append({
                        "run_timestamp": run_meta["run_timestamp"],
                        "run_date": run_meta["run_date"],
                        "keyword": kw,
                        "count": count
                    })

            return results
        except Exception as e:
            logger.error(f"Failed to get keyword velocity: {e}")
            return None

    def get_unique_sources(self) -> List[str]:
        """Return a sorted list of unique source names across all articles."""
        if not self.available:
            return []

        try:
            response = self.client.table("articles")\
                .select("source_name")\
                .execute()

            if response.data:
                return sorted({r["source_name"] for r in response.data if r.get("source_name")})
            return []
        except Exception as e:
            logger.error(f"Failed to get unique sources: {e}")
            return []

    def backfill_from_history(self, history_data: Dict) -> Dict:
        """
        Backfill Supabase with historical data from history.json.
        Skips runs that already exist in Supabase.
        
        Args:
            history_data: Dict from load_full_history() with format:
                         {timestamp: {date, summaries, counts, full_articles, themed_articles}}
        
        Returns:
            Dict with stats: {total_runs, inserted_runs, skipped_runs, inserted_articles, errors}
        """
        if not self.available:
            logger.warning("Supabase not available, skipping backfill")
            return {"total_runs": 0, "inserted_runs": 0, "skipped_runs": 0, "inserted_articles": 0, "errors": []}
        
        stats = {
            "total_runs": len(history_data),
            "inserted_runs": 0,
            "skipped_runs": 0,
            "inserted_articles": 0,
            "errors": []
        }
        
        try:
            for timestamp, entry in history_data.items():
                # 1. Check if this run already exists
                try:
                    existing = self.client.table("trend_runs").select("id").eq(
                        "run_timestamp", timestamp
                    ).execute()
                    
                    if existing.data:
                        logger.debug(f"Run {timestamp} already in Supabase, skipping")
                        stats["skipped_runs"] += 1
                        continue
                except Exception as e:
                    logger.debug(f"Could not check existing run {timestamp}: {e}")
                
                # 2. Create trend run record
                date = entry.get("date", timestamp[:10])
                full_articles = entry.get("full_articles", [])
                
                try:
                    run_response = self.client.table("trend_runs").insert({
                        "run_timestamp": timestamp,
                        "run_date": date,
                        "total_articles": len(full_articles)
                    }).execute()
                    
                    if not run_response.data:
                        stats["errors"].append(f"Failed to insert run {timestamp}")
                        continue
                    
                    run_id = run_response.data[0]["id"]
                except Exception as e:
                    stats["errors"].append(f"Failed to insert run {timestamp}: {e}")
                    continue
                
                # 3. Insert theme summaries
                summaries = entry.get("summaries", {})
                counts = entry.get("counts", {})
                
                for theme_name, summary in summaries.items():
                    try:
                        self.client.table("theme_summaries").insert({
                            "run_id": run_id,
                            "theme_name": theme_name,
                            "what_is_happening": summary.get("what_is_happening", ""),
                            "engineering_tradeoffs": summary.get("engineering_tradeoffs", ""),
                            "product_impact": summary.get("product_impact", ""),
                            "why_it_matters": summary.get("why_it_matters", ""),
                            "what_to_watch": summary.get("what_to_watch", ""),
                            "article_count": counts.get(theme_name, 0)
                        }).execute()
                    except Exception as e:
                        stats["errors"].append(f"Failed to insert summary for {theme_name} in run {timestamp}: {e}")
                
                # 4. Insert articles by theme
                themed_articles = entry.get("themed_articles", {})
                for theme_name, articles in themed_articles.items():
                    if articles:
                        try:
                            self.save_articles(run_id, theme_name, articles)
                            stats["inserted_articles"] += len(articles)
                        except Exception as e:
                            stats["errors"].append(f"Failed to insert articles for {theme_name} in run {timestamp}: {e}")
                
                stats["inserted_runs"] += 1
                logger.info(f"Backfilled run {timestamp} with {len(full_articles)} articles")
        
        except Exception as e:
            logger.error(f"Backfill failed: {e}")
            stats["errors"].append(str(e))
        
        return stats

    # ------------------------------------------------------------------
    # User Feedback & Feature Requests (with local JSON fallback)
    # ------------------------------------------------------------------

    def save_feedback(self, category: str, title: str, description: str, status: str = "open") -> Optional[Dict]:
        """Save a new user feedback/feature/bug item to Supabase or local fallback."""
        now_iso = datetime.now(timezone.utc).isoformat()
        feedback_data = {
            "category": category.lower(),
            "title": title[:255],
            "description": description,
            "status": status.lower(),
            "created_at": now_iso,
            "updated_at": now_iso,
        }

        if self.available:
            try:
                response = self.client.table("user_feedback").insert(feedback_data).execute()
                if response.data:
                    logger.info(f"Saved user feedback to Supabase: {response.data[0]['id']}")
                    return response.data[0]
            except Exception as e:
                logger.error(f"Failed to save feedback to Supabase: {e}")

        # Fallback to local storage
        return _save_local_feedback_item(feedback_data)

    def get_all_feedback(self, category_filter: Optional[str] = None,
                         status_filter: Optional[str] = None,
                         limit: int = 100) -> List[Dict]:
        """Retrieve user feedback items, filtered by category or status."""
        if self.available:
            try:
                q = self.client.table("user_feedback").select("*").order("created_at", desc=True).limit(limit)
                if category_filter and category_filter.lower() != "all":
                    q = q.eq("category", category_filter.lower())
                if status_filter and status_filter.lower() != "all":
                    q = q.eq("status", status_filter.lower())
                resp = q.execute()
                if resp.data is not None:
                    return resp.data
            except Exception as e:
                logger.error(f"Failed to get user feedback from Supabase: {e}")

        # Fallback to local storage
        return _get_local_feedback(category_filter=category_filter, status_filter=status_filter, limit=limit)

    def update_feedback_status(self, feedback_id: str, new_status: str) -> Optional[Dict]:
        """Update status of a feedback item (e.g., 'open', 'in_progress', 'resolved', 'closed')."""
        now_iso = datetime.now(timezone.utc).isoformat()
        if self.available:
            try:
                resp = self.client.table("user_feedback").update({
                    "status": new_status.lower(),
                    "updated_at": now_iso
                }).eq("id", feedback_id).execute()
                if resp.data:
                    return resp.data[0]
            except Exception as e:
                logger.error(f"Failed to update feedback status in Supabase: {e}")

        return _update_local_feedback_status(feedback_id, new_status)

    def delete_feedback(self, feedback_id: str) -> bool:
        """Delete a feedback item."""
        if self.available:
            try:
                self.client.table("user_feedback").delete().eq("id", feedback_id).execute()
                return True
            except Exception as e:
                logger.error(f"Failed to delete feedback in Supabase: {e}")

        return _delete_local_feedback(feedback_id)

    # ------------------------------------------------------------------
    # Sage Conversations (Persistent Cloud Threads)
    # ------------------------------------------------------------------

    def insert_sage_evaluation(self, row: Dict) -> bool:
        """Insert one Sage faithfulness judgment (Phase 2 gate).

        Called from the fire-and-forget judge thread in
        ``core.evaluator.record_sage_faithfulness`` — one row per successful
        Sage reply, scored against the exact context the reply was grounded
        in.  Failures return ``False`` (logged, never raised) so a judge
        thread can never surface in the chat.

        Args:
            row: Dict with conversation_id / question / answer_text / score /
                unsupported_claims / judge_model / answer_model keys.

        Returns:
            True when the row landed, False otherwise.
        """
        if not self.available:
            return False
        try:
            try:
                self.client.table("sage_evaluations").insert(row).execute()
                return True
            except Exception:
                # Schema drift: pre-migration databases lack answer_mode
                # (supabase_migration_sage_evaluations_mode.sql). Retry
                # without it so judging never breaks while pending.
                if "answer_mode" in row:
                    retry = {k: v for k, v in row.items() if k != "answer_mode"}
                    self.client.table("sage_evaluations").insert(retry).execute()
                    return True
                raise
        except Exception as e:
            logger.warning(f"Failed to insert sage evaluation: {e}")
            return False

    def get_sage_evaluations(self, limit: int = 100) -> Optional[List[Dict]]:
        """Read recent Sage faithfulness judgments, newest first.

        Feeds the Sage Faithfulness section on the Quality Evaluation page.
        Returns ``None`` when unavailable or on failure so the caller can
        render the empty state rather than an error.
        """
        if not self.available:
            return None
        try:
            res = self.client.table("sage_evaluations") \
                .select("*") \
                .order("created_at", desc=True) \
                .limit(limit) \
                .execute()
            return res.data if res.data else []
        except Exception as e:
            logger.warning(f"Failed to fetch sage evaluations: {e}")
            return None

    def get_recent_llm_calls(
        self, task_types: Optional[List[str]] = None, limit: int = 500,
    ) -> Optional[List[Dict]]:
        """Recent llm_calls rows (newest first) for pipeline activity stats.

        Optional task_type filter (e.g. the Phase 7 sage agent tasks).
        Returns None on failure/unavailable/pre-migration (never raises).
        """
        if not self.available:
            return None
        try:
            query = self.client.table("llm_calls")\
                .select("task_type, success, latency_ms, tokens_in, tokens_out, correlation_id, created_at")
            if task_types:
                query = query.in_("task_type", task_types)
            res = query.order("created_at", desc=True).limit(limit).execute()
            return res.data if isinstance(res.data, list) else None
        except Exception as e:
            logger.warning(f"Failed to fetch recent llm_calls: {e}")
            return None

    def save_sage_conversation(
        self,
        messages: List[Dict],
        conv_id: Optional[str] = None,
        title: Optional[str] = None,
        theme_filter: Optional[str] = None,
        period_label: Optional[str] = None,
    ) -> Optional[Dict]:
        """Save or update a Sage conversation in Supabase.

        Attempts table `sage_conversations`. If the table does not exist yet,
        it automatically persists to `sync_metadata` under key 'sage_conversations_store'.
        """
        import uuid
        now_iso = datetime.now(timezone.utc).isoformat()
        conv_id = conv_id or str(uuid.uuid4())

        if not title:
            first_user_msg = next((m.get("content", "") for m in messages if m.get("role") == "user"), "Conversation")
            cleaned = " ".join(first_user_msg.split())
            title = cleaned[:50] + ("…" if len(cleaned) > 50 else "")

        payload = {
            "id": conv_id,
            "title": title,
            "theme_filter": theme_filter,
            "period_label": period_label,
            "messages": messages,
            "updated_at": now_iso,
        }

        if not self.available:
            return None

        # 1. Try native sage_conversations table
        try:
            existing = self.client.table("sage_conversations").select("id").eq("id", conv_id).execute()
            if existing.data:
                res = self.client.table("sage_conversations").update(payload).eq("id", conv_id).execute()
            else:
                payload["created_at"] = now_iso
                res = self.client.table("sage_conversations").insert(payload).execute()
            if res.data:
                logger.info(f"Saved Sage conversation to Supabase table: {conv_id}")
                return res.data[0]
        except Exception as e:
            logger.debug(f"Direct sage_conversations table save failed (falling back to sync_metadata): {e}")

        # 2. Resilient fallback to sync_metadata store
        try:
            store = self._get_conversations_from_sync_metadata()
            store[conv_id] = {
                **payload,
                "created_at": store.get(conv_id, {}).get("created_at", now_iso),
            }
            if self._save_conversations_to_sync_metadata(store):
                logger.info(f"Saved Sage conversation to Supabase sync_metadata: {conv_id}")
                return store[conv_id]
        except Exception as e:
            logger.error(f"Failed to persist conversation to Supabase: {e}")

        return None

    def get_sage_conversations(self, limit: int = 50) -> List[Dict]:
        """Retrieve all saved Sage conversations from Supabase."""
        if not self.available:
            return []

        # 1. Try native sage_conversations table
        try:
            res = self.client.table("sage_conversations") \
                .select("id, title, theme_filter, period_label, created_at, updated_at, messages") \
                .order("updated_at", desc=True) \
                .limit(limit) \
                .execute()
            if res.data is not None and len(res.data) > 0:
                results = []
                for row in res.data:
                    results.append({
                        "id": str(row["id"]),
                        "title": row.get("title", "Untitled"),
                        "theme_filter": row.get("theme_filter"),
                        "period_label": row.get("period_label"),
                        "created_at": str(row.get("created_at", "")),
                        "updated_at": str(row.get("updated_at", "")),
                        "message_count": len(row.get("messages", [])),
                    })
                return results
        except Exception as e:
            logger.debug(f"Direct sage_conversations table select failed: {e}")

        # 2. Fallback to sync_metadata store
        try:
            store = self._get_conversations_from_sync_metadata()
            results = []
            for cid, c in store.items():
                results.append({
                    "id": cid,
                    "title": c.get("title", "Untitled"),
                    "theme_filter": c.get("theme_filter"),
                    "period_label": c.get("period_label"),
                    "created_at": str(c.get("created_at", "")),
                    "updated_at": str(c.get("updated_at", "")),
                    "message_count": len(c.get("messages", [])),
                })
            results.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
            return results[:limit]
        except Exception as e:
            logger.error(f"Failed to fetch conversations from Supabase: {e}")
            return []

    def get_sage_conversation_by_id(self, conv_id: str) -> Optional[Dict]:
        """Retrieve a specific Sage conversation by ID from Supabase."""
        if not self.available or not conv_id:
            return None

        # 1. Try native sage_conversations table
        try:
            res = self.client.table("sage_conversations").select("*").eq("id", conv_id).execute()
            if res.data and len(res.data) > 0:
                return res.data[0]
        except Exception as e:
            logger.debug(f"Direct sage_conversations table get failed: {e}")

        # 2. Fallback to sync_metadata store
        try:
            store = self._get_conversations_from_sync_metadata()
            return store.get(conv_id)
        except Exception as e:
            logger.error(f"Failed to fetch conversation {conv_id} from Supabase: {e}")
            return None

    def delete_sage_conversation(self, conv_id: str) -> bool:
        """Delete a saved Sage conversation by ID from Supabase."""
        if not self.available or not conv_id:
            return False

        deleted = False
        # 1. Try native sage_conversations table
        try:
            res = self.client.table("sage_conversations").delete().eq("id", conv_id).execute()
            if res.data:
                deleted = True
        except Exception as e:
            logger.debug(f"Direct sage_conversations table delete failed: {e}")

        # 2. Also clean from sync_metadata fallback store if present
        try:
            store = self._get_conversations_from_sync_metadata()
            if conv_id in store:
                del store[conv_id]
                self._save_conversations_to_sync_metadata(store)
                deleted = True
        except Exception as e:
            logger.error(f"Failed to delete conversation from sync_metadata: {e}")

        return deleted

    def _get_conversations_from_sync_metadata(self) -> Dict[str, Dict]:
        """Read fallback conversations store from sync_metadata."""
        try:
            res = self.client.table("sync_metadata").select("value").eq("key", "sage_conversations_store").execute()
            if res.data and res.data[0].get("value"):
                val = json.loads(res.data[0]["value"])
                return val if isinstance(val, dict) else {}
        except Exception:
            pass
        return {}

    def _save_conversations_to_sync_metadata(self, store: Dict[str, Dict]) -> bool:
        """Write fallback conversations store to sync_metadata."""
        try:
            payload = {
                "key": "sage_conversations_store",
                "value": json.dumps(store, ensure_ascii=False),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self.client.table("sync_metadata").upsert(payload, on_conflict="key").execute()
            return True
        except Exception as e:
            logger.error(f"Failed to save conversations to sync_metadata: {e}")
            return False


# ------------------------------------------------------------------
# Local JSON Fallback Helpers for User Feedback
# ------------------------------------------------------------------

LOCAL_FEEDBACK_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "feedback.json")


def _ensure_local_feedback_file() -> List[Dict]:
    """Ensure data/feedback.json exists and return items."""
    os.makedirs(os.path.dirname(LOCAL_FEEDBACK_FILE), exist_ok=True)
    if not os.path.exists(LOCAL_FEEDBACK_FILE):
        with open(LOCAL_FEEDBACK_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)
        return []
    try:
        with open(LOCAL_FEEDBACK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error reading local feedback file: {e}")
        return []


def _save_local_feedback_item(item: Dict) -> Dict:
    """Save item to local feedback.json."""
    items = _ensure_local_feedback_file()
    item_copy = dict(item)
    if "id" not in item_copy:
        item_copy["id"] = str(uuid.uuid4())
    items.insert(0, item_copy)
    try:
        with open(LOCAL_FEEDBACK_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving to local feedback file: {e}")
    return item_copy


def _get_local_feedback(category_filter: Optional[str] = None,
                         status_filter: Optional[str] = None,
                         limit: int = 100) -> List[Dict]:
    """Get items from local feedback.json with optional filters."""
    items = _ensure_local_feedback_file()
    filtered = []
    for item in items:
        if category_filter and category_filter.lower() != "all":
            if item.get("category", "").lower() != category_filter.lower():
                continue
        if status_filter and status_filter.lower() != "all":
            if item.get("status", "").lower() != status_filter.lower():
                continue
        filtered.append(item)
    return filtered[:limit]


def _update_local_feedback_status(feedback_id: str, new_status: str) -> Optional[Dict]:
    """Update status of item in local feedback.json."""
    items = _ensure_local_feedback_file()
    updated_item = None
    for item in items:
        if str(item.get("id")) == str(feedback_id):
            item["status"] = new_status.lower()
            item["updated_at"] = datetime.now(timezone.utc).isoformat()
            updated_item = item
            break
    if updated_item:
        try:
            with open(LOCAL_FEEDBACK_FILE, "w", encoding="utf-8") as f:
                json.dump(items, f, indent=2)
        except Exception as e:
            logger.error(f"Error updating local feedback file: {e}")
    return updated_item


def _delete_local_feedback(feedback_id: str) -> bool:
    """Delete item from local feedback.json."""
    items = _ensure_local_feedback_file()
    new_items = [i for i in items if str(i.get("id")) != str(feedback_id)]
    try:
        with open(LOCAL_FEEDBACK_FILE, "w", encoding="utf-8") as f:
            json.dump(new_items, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Error deleting from local feedback file: {e}")
        return False


# Singleton instance
_manager: Optional[SupabaseManager] = None


def get_supabase_manager() -> SupabaseManager:
    """Get or create the Supabase manager singleton."""
    global _manager
    if _manager is None:
        _manager = SupabaseManager()
    return _manager
