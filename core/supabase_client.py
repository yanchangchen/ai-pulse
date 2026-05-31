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
                .upsert({"key": key, "value": value})\
                .execute()
            
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


# Singleton instance
_manager: Optional[SupabaseManager] = None


def get_supabase_manager() -> SupabaseManager:
    """Get or create the Supabase manager singleton."""
    global _manager
    if _manager is None:
        _manager = SupabaseManager()
    return _manager
    
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
