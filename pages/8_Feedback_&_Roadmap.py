"""
AI Pulse - Feedback & Roadmap Page
Allows users to submit feature requests, bug reports, and UX ideas.
Stores feedback in Supabase (or local fallback) and provides SDD (Spec-Driven Development) writing prompts.
"""

import streamlit as st
from datetime import datetime

from core.supabase_client import get_supabase_manager
from core.design_system import apply_design_system
from core.shared_sidebar import render_sidebar_nav
from core.bg_refresher import check_and_show_bg_status

# Page configuration
st.set_page_config(
    page_title="Feedback & Roadmap - AI Pulse",
    page_icon="💡",
    layout="wide"
)

# Apply central design system
apply_design_system()


def format_date(iso_str: str) -> str:
    """Format ISO date string to human-readable format."""
    if not iso_str:
        return "Unknown Date"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y %H:%M")
    except Exception:
        return iso_str[:10]


def main() -> None:
    """Main Feedback & Roadmap page."""
    check_and_show_bg_status()
    render_sidebar_nav()

    st.title("💡 Feedback & Feature Roadmap")
    st.markdown("### Share feature ideas, report bugs, and track upcoming improvements")
    st.divider()

    supabase = get_supabase_manager()
    using_supabase = supabase.is_available()

    if not using_supabase:
        st.info("ℹ️ Running in Local Storage Mode: Feedback items are stored locally in `data/feedback.json`. Connect Supabase to sync across devices.")

    tab_submit, tab_roadmap = st.tabs(["📝 Submit Feedback / Spec", "🗺️ Feedback & Roadmap Tracker"])

    # ----------------------------------------------------
    # TAB 1: SUBMIT FEEDBACK & SDD PROMPTS
    # ----------------------------------------------------
    with tab_submit:
        st.subheader("Submit a Feature Spec, Bug Report, or UX Idea")
        
        # SDD Prompts Expander / Callout Box
        with st.expander("✨ Tips for writing great Specs & Bug Reports (SDD Workflow)", expanded=True):
            st.markdown("""
            > **Spec-Driven Development (SDD) Guidelines**  
            To help our team build exactly what you need, try structuring your description with these 3 elements:
            
            1. **Problem & Context:** What is the current limitation, pain point, or unexpected behavior?
            2. **Desired Behavior / Spec:** What is the ideal contract or outcome? (e.g. *"When X happens, the UI should show Y"*).
            3. **Acceptance Criteria:** How can we test or verify when this request is completed?
            """)

        with st.form("feedback_form", clear_on_submit=True):
            col_cat, col_title = st.columns([1, 2])
            
            with col_cat:
                category_label = st.selectbox(
                    "Category*",
                    options=["Feature Request", "Bug Report", "UX Improvement"],
                    help="Select the type of feedback"
                )
                category_map = {
                    "Feature Request": "feature",
                    "Bug Report": "bug",
                    "UX Improvement": "ux"
                }
                category = category_map[category_label]

            with col_title:
                title = st.text_input(
                    "Title / Short Summary*",
                    placeholder="e.g., Add Export to CSV button on Memory Wiki page",
                    max_chars=255
                )

            description_placeholder = (
                "## Problem & Context\n"
                "Describe the current issue or requirement...\n\n"
                "## Desired Behavior / Spec\n"
                "Explain how it should work...\n\n"
                "## Acceptance Criteria\n"
                "- [ ] Verification step 1\n"
                "- [ ] Verification step 2"
            )

            description = st.text_area(
                "Detailed Description & Specification*",
                placeholder=description_placeholder,
                height=220,
                help="Feel free to use Markdown formatting!"
            )

            submitted = st.form_submit_button("🚀 Submit to Roadmap", type="primary", width='stretch')

            if submitted:
                if not title.strip() or not description.strip():
                    st.error("Please fill in both the Title and Description before submitting.")
                else:
                    saved = supabase.save_feedback(
                        category=category,
                        title=title.strip(),
                        description=description.strip(),
                        status="open"
                    )
                    if saved:
                        st.success("✅ Your feedback has been saved successfully! Check the Roadmap Tracker tab.")
                        st.rerun()
                    else:
                        st.error("Failed to save feedback item. Please try again.")

    # ----------------------------------------------------
    # TAB 2: ROADMAP & TRACKER
    # ----------------------------------------------------
    with tab_roadmap:
        all_items = supabase.get_all_feedback(limit=200)

        # Metrics cards
        total_open = sum(1 for i in all_items if i.get("status") == "open")
        total_in_progress = sum(1 for i in all_items if i.get("status") == "in_progress")
        total_resolved = sum(1 for i in all_items if i.get("status") in ["resolved", "closed"])

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Items", len(all_items))
        m2.metric("Open Requests", total_open)
        m3.metric("In Progress", total_in_progress)
        m4.metric("Resolved / Completed", total_resolved)

        st.divider()

        # Filters
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            cat_filter = st.selectbox(
                "Filter by Category:",
                options=["All", "Feature", "Bug", "UX"],
                key="filter_cat"
            )
        with col_f2:
            status_filter = st.selectbox(
                "Filter by Status:",
                options=["All", "Open", "In_Progress", "Resolved", "Closed"],
                key="filter_status"
            )

        # Apply filters
        items = supabase.get_all_feedback(
            category_filter=cat_filter,
            status_filter=status_filter,
            limit=200
        )

        if not items:
            st.info("No feedback items found matching the selected filters.")
        else:
            cat_badges = {
                "feature": "✨ Feature Request",
                "bug": "🐛 Bug Report",
                "ux": "🎨 UX Improvement"
            }

            status_colors = {
                "open": "🔵 Open",
                "in_progress": "🟡 In Progress",
                "resolved": "🟢 Resolved",
                "closed": "⚪ Closed"
            }

            for item in items:
                item_id = str(item.get("id"))
                cat_name = item.get("category", "feature").lower()
                status_name = item.get("status", "open").lower()
                badge = cat_badges.get(cat_name, cat_name.title())
                status_str = status_colors.get(status_name, status_name.title())
                date_str = format_date(item.get("created_at", ""))

                with st.expander(f"{badge} | **{item.get('title', 'Untitled')}** ({status_str}) — {date_str}"):
                    st.markdown(f"**Submitted Date:** {date_str}")
                    st.markdown(f"**Category:** `{cat_name.upper()}`")
                    st.markdown("---")
                    st.markdown(item.get("description", ""))
                    st.markdown("---")

                    col_s1, col_s2 = st.columns([3, 1])
                    with col_s1:
                        new_status = st.selectbox(
                            "Update Status:",
                            options=["open", "in_progress", "resolved", "closed"],
                            index=["open", "in_progress", "resolved", "closed"].index(status_name) if status_name in ["open", "in_progress", "resolved", "closed"] else 0,
                            key=f"status_sel_{item_id}"
                        )
                        if new_status != status_name:
                            if st.button("Save Status", key=f"btn_save_{item_id}", type="primary"):
                                supabase.update_feedback_status(item_id, new_status)
                                st.success(f"Status updated to '{new_status}'!")
                                st.rerun()

                    with col_s2:
                        st.markdown("<br>", unsafe_allow_html=True)
                        if st.button("🗑️ Delete Item", key=f"btn_del_{item_id}"):
                            supabase.delete_feedback(item_id)
                            st.warning("Feedback item deleted.")
                            st.rerun()


if __name__ == "__main__":
    main()
