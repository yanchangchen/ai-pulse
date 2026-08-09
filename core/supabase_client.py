"""
Supabase client wrapper for AI Pulse.
Handles all database operations for trend persistence.
Gracefully degrades if Supabase is unavailable.
"""

import os
import json
import uuid
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
                      total_articles: int) -> Optional[Dict]:
        """
        Save a new trend run to the database.
        
        Args:
            run_timestamp: ISO format timestamp (e.g., "2026-05-29 16:10:37")
            run_date: Date string (e.g., "2026-05-29")
            total_articles: Total number of articles in this run
        
        Returns:
            Dict with run record including 'id', or None if failed
        """
        if not self.available:
            return None
        
        try:
            response = self.client.table("trend_runs").insert({
                "run_timestamp": run_timestamp,
                "run_date": run_date,
                "total_articles": total_articles
            }).execute()
            
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
            summary: Dict with keys: what_is_happening, why_it_matters, what_to_watch
            article_count: Number of articles for this theme
        
        Returns:
            Dict with summary record, or None if failed
        """
        if not self.available:
            return None
        
        try:
            response = self.client.table("theme_summaries").insert({
                "run_id": run_id,
                "theme_name": theme_name,
                "what_is_happening": summary.get("what_is_happening", ""),
                "engineering_tradeoffs": summary.get("engineering_tradeoffs", ""),
                "product_impact": summary.get("product_impact", ""),
                "why_it_matters": summary.get("why_it_matters", ""),
                "what_to_watch": summary.get("what_to_watch", ""),
                "article_count": article_count
            }).execute()

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
            logger.error(f"Failed to get run {run_id}: {e}")
            return None
    
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
    ) -> Optional[List[Dict]]:
        """Retrieve theme summaries across historical runs within an optional date range
        and theme/source filter.

        Returns:
            List of summary dicts (with run_timestamp/run_date), or None on failure.
        """
        if not self.available:
            return None

        try:
            # Step 1 – fetch runs in the requested date window
            runs_q = self.client.table("trend_runs") \
                .select("id, run_timestamp, run_date") \
                .order("run_timestamp", desc=False)
            if date_from:
                runs_q = runs_q.gte("run_timestamp", date_from)
            if date_to:
                runs_q = runs_q.lte("run_timestamp", date_to)

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

            # Step 3 – augment with run metadata and sort chronologically
            results: List[Dict] = []
            for s in all_summaries:
                run_meta = run_lookup.get(s["run_id"])
                if run_meta:
                    results.append({
                        **s,
                        "run_timestamp": run_meta["run_timestamp"],
                        "run_date": run_meta["run_date"],
                    })

            results.sort(key=lambda r: r["run_timestamp"])
            return results[:limit]
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
