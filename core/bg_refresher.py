import sys
import threading
import logging
from datetime import datetime
from typing import Optional

from core.logger import setup_logger
import streamlit as st
from core.history_manager import get_last_run

logger = setup_logger(__name__)

# To survive module reloads in Streamlit, persist state in sys
if not hasattr(sys, "_aipulse_bg_refresher_state"):
    sys._aipulse_bg_refresher_state = {
        "status": "idle",
        "error": None,
        "completed_timestamp": None,
        "progress": "Ready",
        "thread": None
    }

class BackgroundRefresher:
    _lock = threading.RLock()

    @classmethod
    def _get_state(cls) -> dict:
        return sys._aipulse_bg_refresher_state

    @classmethod
    def is_running(cls) -> bool:
        with cls._lock:
            state = cls._get_state()
            thread = state["thread"]
            return thread is not None and thread.is_alive()

    @classmethod
    def get_status(cls) -> dict:
        with cls._lock:
            state = cls._get_state()
            return {
                "status": state["status"],
                "error": state["error"],
                "completed_timestamp": state["completed_timestamp"],
                "is_running": cls.is_running(),
                "progress": state["progress"]
            }

    @classmethod
    def update_progress(cls, msg: str) -> None:
        """Update progress string and output to console for instant visibility."""
        with cls._lock:
            state = cls._get_state()
            state["progress"] = msg
        logger.info("[BG Refresher Progress] %s", msg)
        print(f"🔄 [AI Pulse BG Refresher] Current Step: {msg}", flush=True)

    @classmethod
    def start(cls) -> bool:
        with cls._lock:
            if cls.is_running():
                logger.info("Background refresh is already running.")
                return False
            
            state = cls._get_state()
            state["status"] = "running"
            state["error"] = None
            state["progress"] = "Initializing pipeline thread..."
            
            # Start the background thread
            thread = threading.Thread(
                target=cls._run_pipeline, 
                name="AI-Pulse-BG-Refresher", 
                daemon=True
            )
            state["thread"] = thread
            thread.start()
            logger.info("Started background refresh thread.")
            print("⚡ [AI Pulse BG Refresher] Started background pipeline execution thread.", flush=True)
            return True

    @classmethod
    def _run_pipeline(cls) -> None:
        try:
            cls.update_progress("[ENGINE] Starting background news intelligence engine...")
            
            # Direct imports of the core logic to bypass Streamlit's @st.cache_data
            from core.fetcher import fetch_all_news
            from core.classifier import classify_articles
            from core.summariser import generate_all_summaries
            from core.history_manager import save_run_to_history
            
            # 1. Fetch news
            cls.update_progress("[FETCH] Ingesting and fetching AI news from RSS and web sources...")
            articles = fetch_all_news()
            if not articles:
                with cls._lock:
                    state = cls._get_state()
                    state["status"] = "failed"
                    state["error"] = "No articles found in the last 14 days."
                cls.update_progress("[FETCH] Failed: No articles found.")
                logger.warning("BG Pipeline failed: No articles found.")
                return

            # 2. Classify
            cls.update_progress(f"[CLASSIFY] Classifying {len(articles)} articles into new persona-aligned themes...")
            themed_articles = classify_articles(articles)

            # 3. Summarize
            cls.update_progress("[LLM] Generating targeted Engineering Blueprint & Product Feasibility briefs...")
            theme_counts = {theme: len(themed_articles.get(theme, [])) for theme in themed_articles}
            summaries = generate_all_summaries(themed_articles, articles)

            # 4. Save to history
            cls.update_progress("[CACHE] Saving intelligence run to persistent cache and Memory Wiki...")
            save_run_to_history(summaries, theme_counts, articles, themed_articles)

            # Clear Streamlit cache so that subsequent normal loads get the fresh data
            try:
                import streamlit as st
                st.cache_data.clear()
                logger.info("BG Pipeline: Streamlit cache cleared successfully.")
            except Exception as cache_err:
                logger.debug("BG Pipeline: Failed to clear streamlit cache (expected if run outside main thread): %s", cache_err)

            with cls._lock:
                state = cls._get_state()
                state["status"] = "completed"
                state["progress"] = "[CACHE] Pipeline completed successfully."
                state["completed_timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print("✅ [CACHE] BG Pipeline execution successfully finished.", flush=True)
            
        except Exception as e:
            with cls._lock:
                state = cls._get_state()
                state["status"] = "failed"
                state["progress"] = f"[ERROR] Failed with error: {e}"
                state["error"] = str(e)
            logger.error("Background pipeline failed: %s", e, exc_info=True)
            print(f"❌ [ERROR] BG Pipeline execution failed: {e}", flush=True)



def check_and_show_bg_status() -> None:
    """Helper to check background refresh status, display banners, and render control elements."""
        
    # 1. Top-of-page alert banner if new data is available in the persistence layer
    if st.session_state.get('data_loaded') and st.session_state.get('loaded_timestamp'):
        last_run = get_last_run()
        if last_run:
            last_ts = last_run["timestamp"]
            loaded_ts = st.session_state.get('loaded_timestamp')
            
            if last_ts > loaded_ts:
                st.info(f"📢 **New intelligence insights are ready!** (Generated at {last_ts})")
                if st.button("💡 Load New Insights Now", key="bg_refresher_load_btn", width="stretch"):
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
    import time
    from core.bg_refresher import BackgroundRefresher
    from core.history_manager import get_last_run_time
    
    status_info = BackgroundRefresher.get_status()
    
    st.sidebar.divider()
    st.sidebar.subheader("🔄 Background Updates")
    
    if status_info["is_running"]:
        st.sidebar.info("⏳ Updating dashboard in the background...")
        if status_info.get("progress"):
            st.sidebar.warning(f"⚡ **Current Step:**\n{status_info['progress']}")
        st.sidebar.caption("Ingesting feeds, classifying themes with LLM, and rewriting wiki memory...")
        
        # Self-throttled real-time visual refresh (auto rerun every 2 seconds when running)
        time.sleep(2)
        st.rerun()
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
                if st.sidebar.button("⚡ Force Refresh Now", key="bg_refresher_trigger_btn", width="stretch"):
                    st.session_state.force_refresh = True
                    BackgroundRefresher.start()
                    st.rerun()
        else:
            st.sidebar.info("No cached data found.")
            if st.sidebar.button("⚡ Trigger Initial Load", key="bg_refresher_trigger_btn", width="stretch"):
                BackgroundRefresher.start()
                st.rerun()
                
        if status_info["status"] == "failed":
            st.sidebar.error(f"Last update failed: {status_info['error']}")
        elif status_info["status"] == "completed" and status_info["completed_timestamp"]:
            st.sidebar.caption(f"Last update finished: {status_info['completed_timestamp']}")

