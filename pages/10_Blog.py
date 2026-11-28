"""
AI Pulse - Dev Blog Page
Searchable rendering of BLOG.md — the development journal of everything built
in AI Pulse, newest first. Search filters entries by any terms appearing in
the entry title or body (case-insensitive AND match).
"""

from pathlib import Path

import streamlit as st

from core.design_system import apply_design_system
from core.shared_sidebar import render_sidebar_nav
from core.bg_refresher import check_and_show_bg_status
from core.blog_index import collect_labels, filter_entries, split_blog_entries

# Page configuration
st.set_page_config(
    page_title="Dev Blog - AI Pulse",
    page_icon="⚡",
    layout="wide"
)

apply_design_system()

_BLOG_PATH = Path(__file__).resolve().parent.parent / "BLOG.md"


@st.cache_data(ttl=300, show_spinner=False)
def _load_entries(mtime: float):
    """Parse BLOG.md; cache key includes mtime so edits appear within 5 min."""
    try:
        text = _BLOG_PATH.read_text(encoding="utf-8")
    except OSError:
        return []
    return split_blog_entries(text)


def main() -> None:
    """Main dev blog page."""
    check_and_show_bg_status()
    render_sidebar_nav(current="blog")

    st.title("📓 Dev Blog")
    st.markdown("### The AI Pulse development journal — search what we've built together")
    st.divider()

    mtime = _BLOG_PATH.stat().st_mtime if _BLOG_PATH.exists() else 0.0
    entries = _load_entries(mtime)
    if not entries:
        st.warning("BLOG.md not found or empty.")
        return

    query = st.text_input(
        "🔎 Search entries:",
        key="blog_search_query",
        placeholder="e.g. embeddings, gateway, Sage, quota — all terms must match",
    )
    selected_labels = st.multiselect(
        "🏷️ Filter by label (matches any selected):",
        collect_labels(entries),
        key="blog_label_filter",
    )
    matches = filter_entries(entries, query, selected_labels)

    if query.strip() or selected_labels:
        criteria = []
        if query.strip():
            criteria.append(f"“{query.strip()}”")
        if selected_labels:
            criteria.append("labels: " + ", ".join(selected_labels))
        st.caption(f"**{len(matches)}** of **{len(entries)}** entries match {' + '.join(criteria)}")
    else:
        st.caption(f"**{len(entries)}** entries · newest first")

    if not matches:
        st.info("No entries match your search. Try fewer or different terms.")
        return

    for entry in matches:
        heading = entry["title"]
        if entry["date_label"]:
            heading = f"{entry['date_label']} — {heading}"
        st.markdown(f"## {heading}")
        if entry.get("labels"):
            st.caption("🏷️ " + " · ".join(entry["labels"]))
        st.markdown(entry["body"])
        st.divider()


if __name__ == "__main__":
    main()