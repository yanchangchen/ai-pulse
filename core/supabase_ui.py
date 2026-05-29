"""
Supabase UI components for AI Pulse Streamlit app.
Displays cloud sync status in the sidebar.
"""

import streamlit as st


def render_supabase_status():
    """Render Supabase cloud sync status in the sidebar."""
    st.divider()
    
    st.subheader("☁️ Cloud Sync Status")
    try:
        from core.supabase_client import get_supabase_manager
        supabase = get_supabase_manager()
        
        if supabase.is_available():
            st.success("✅ Connected to Supabase")
            
            # Get last sync time
            last_sync = supabase.get_sync_metadata("last_sync_time")
            if last_sync:
                st.caption(f"Last sync: {last_sync}")
            else:
                st.caption("No syncs yet")
            
            # Get last run info
            last_run = supabase.get_latest_run()
            if last_run:
                st.caption(f"Latest run: {last_run['run_timestamp']}")
                st.caption(f"Articles: {last_run['total_articles']}")
        else:
            st.warning("⚠️ Supabase not configured")
            st.caption("Add SUPABASE_URL and SUPABASE_KEY to .env to enable cloud sync")
    except Exception as e:
        st.warning(f"⚠️ Supabase error: {str(e)[:50]}...")
