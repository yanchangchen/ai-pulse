"""
Background Refresher for AI Pulse.
Runs the data ingestion and intelligence pipeline in a background thread
to avoid blocking the main Streamlit UI.
"""

import threading
import logging
from datetime import datetime
from typing import Optional

from core.logger import setup_logger

logger = setup_logger(__name__)

class BackgroundRefresher:
    _thread: Optional[threading.Thread] = None
    _status: str = "idle"  # idle, running, completed, failed
    _error: Optional[str] = None
    _lock = threading.Lock()
    _completed_timestamp: Optional[str] = None

    @classmethod
    def is_running(cls) -> bool:
        with cls._lock:
            return cls._thread is not None and cls._thread.is_alive()

    @classmethod
    def get_status(cls) -> dict:
        with cls._lock:
            return {
                "status": cls._status,
                "error": cls._error,
                "completed_timestamp": cls._completed_timestamp,
                "is_running": cls.is_running()
            }

    @classmethod
    def start(cls) -> bool:
        with cls._lock:
            if cls.is_running():
                logger.info("Background refresh is already running.")
                return False
            
            cls._status = "running"
            cls._error = None
            # Start the background thread
            cls._thread = threading.Thread(
                target=cls._run_pipeline, 
                name="AI-Pulse-BG-Refresher", 
                daemon=True
            )
            cls._thread.start()
            logger.info("Started background refresh thread.")
            return True

    @classmethod
    def _run_pipeline(cls) -> None:
        try:
            logger.info("Background pipeline execution started.")
            
            # Direct imports of the core logic to bypass Streamlit's @st.cache_data
            from core.fetcher import fetch_all_news
            from core.classifier import classify_articles
            from core.summariser import generate_all_summaries
            from core.history_manager import save_run_to_history
            
            # 1. Fetch news
            logger.info("BG Pipeline: Fetching news...")
            articles = fetch_all_news()
            if not articles:
                with cls._lock:
                    cls._status = "failed"
                    cls._error = "No articles found in the last 14 days."
                logger.warning("BG Pipeline failed: No articles found.")
                return

            # 2. Classify
            logger.info("BG Pipeline: Classifying articles...")
            themed_articles = classify_articles(articles)

            # 3. Summarize
            logger.info("BG Pipeline: Generating summaries...")
            theme_counts = {theme: len(themed_articles.get(theme, [])) for theme in themed_articles}
            summaries = generate_all_summaries(themed_articles, articles)

            # 4. Save to history
            logger.info("BG Pipeline: Saving to history...")
            save_run_to_history(summaries, theme_counts, articles, themed_articles)

            # Clear Streamlit cache so that subsequent normal loads get the fresh data
            try:
                import streamlit as st
                st.cache_data.clear()
                logger.info("BG Pipeline: Streamlit cache cleared successfully.")
            except Exception as cache_err:
                logger.debug("BG Pipeline: Failed to clear streamlit cache (expected if run outside main thread): %s", cache_err)

            with cls._lock:
                cls._status = "completed"
                cls._completed_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logger.info("Background pipeline execution successfully completed.")
            
        except Exception as e:
            with cls._lock:
                cls._status = "failed"
                cls._error = str(e)
            logger.error("Background pipeline failed: %s", e, exc_info=True)


def check_and_show_bg_status() -> None:
    """Helper to check background refresh status, display banners, and render control elements."""
    import streamlit as st
    from core.bg_refresher import BackgroundRefresher
    from core.history_manager import get_last_run
    
    # 1. Top-of-page alert banner if new data is available in the persistence layer
    if st.session_state.get('data_loaded') and st.session_state.get('loaded_timestamp'):
        last_run = get_last_run()
        if last_run:
            last_ts = last_run["timestamp"]
            loaded_ts = st.session_state.get('loaded_timestamp')
            
            if last_ts > loaded_ts:
                st.info(f"📢 **New intelligence insights are ready!** (Generated at {last_ts})")
                if st.button("💡 Load New Insights Now", key="bg_refresher_load_btn", use_container_width=True):
                    # Load the new data directly into session state
                    st.session_state.articles = last_run['data']['full_articles']
                    st.session_state.themed_articles = last_run['data']['themed_articles']
                    st.session_state.summaries = last_run['data']['summaries']
                    st.session_state.loaded_timestamp = last_ts
                    st.session_state.data_loaded = True
                    st.session_state.force_refresh = False
                    st.toast("✅ Loaded new insights successfully!")
                    st.rerun()


def render_sidebar_info() -> None:
    """Render background refresher status inside the Streamlit sidebar."""
    import streamlit as st
    from core.bg_refresher import BackgroundRefresher
    from core.history_manager import get_last_run_time
    
    status_info = BackgroundRefresher.get_status()
    
    st.sidebar.divider()
    st.sidebar.subheader("🔄 Background Updates")
    
    if status_info["is_running"]:
        st.sidebar.info("⏳ Updating dashboard in the background...")
        st.sidebar.caption("Ingesting feeds, classifying themes with LLM, and rewriting wiki memory...")
    else:
        # Show status
        last_run_time = get_last_run_time()
        if last_run_time:
            hours_since = (datetime.now() - last_run_time).total_seconds() / 3600
            if hours_since < 6:
                st.sidebar.success(f"✅ Cache up-to-date ({int(hours_since)}h ago)")
            else:
                st.sidebar.warning(f"⚠️ Cache expired ({int(hours_since)}h old)")
                # If cache is expired and not running, allow manual trigger
                if st.sidebar.button("⚡ Force Refresh Now", key="bg_refresher_trigger_btn", use_container_width=True):
                    st.session_state.force_refresh = True
                    BackgroundRefresher.start()
                    st.rerun()
        else:
            st.sidebar.info("No cached data found.")
            if st.sidebar.button("⚡ Trigger Initial Load", key="bg_refresher_trigger_btn", use_container_width=True):
                BackgroundRefresher.start()
                st.rerun()
                
        if status_info["status"] == "failed":
            st.sidebar.error(f"Last update failed: {status_info['error']}")
        elif status_info["status"] == "completed" and status_info["completed_timestamp"]:
            st.sidebar.caption(f"Last update finished: {status_info['completed_timestamp']}")

