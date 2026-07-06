"""
Supabase client wrapper for AI Pulse.
Handles all database operations for trend persistence.
Gracefully degrades if Supabase is unavailable.
"""

import os
import logging
from typing import Dict, List, Optional
from datetime import datetime

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
                
                rows.append({
                    "run_id": run_id,
                    "theme_name": theme_name,
                    "title": article.get("title", "")[:500],  # Limit title length
                    "summary": article.get("summary", ""),
                    "source_name": article.get("source_name", ""),
                    "link": article.get("link", ""),
                    "published_at": article.get("published_at"),
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
            return response.data if response.data else []
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


# Singleton instance
_manager: Optional[SupabaseManager] = None


def get_supabase_manager() -> SupabaseManager:
    """Get or create the Supabase manager singleton."""
    global _manager
    if _manager is None:
        _manager = SupabaseManager()
    return _manager
