"""
Central Design System for AI Pulse.
Provides unified CSS tokens, theme-aware card components, responsive layouts,
and dynamic Plotly chart styling that seamlessly adapt to Streamlit light and dark modes.
"""

from __future__ import annotations

from typing import Dict, Any
import streamlit as st

DESIGN_SYSTEM_CSS = """
<style>
    /* Google Fonts Import */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Outfit:wght@500;600;700&display=swap');

    /* Global Typography & Tabular Numbers */
    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }
    h1, h2, h3, .font-heading {
        font-family: 'Outfit', 'Inter', sans-serif !important;
        font-weight: 600;
        letter-spacing: -0.02em;
    }
    .metric-value, .tabular-nums {
        font-variant-numeric: tabular-nums;
    }

    /* Adaptive Theme Card System */
    .theme-card, .metric-card {
        background-color: var(--secondary-background-color, rgba(128, 128, 128, 0.05)) !important;
        border: 1px solid rgba(128, 128, 128, 0.18) !important;
        border-radius: 10px !important;
        padding: 18px 20px !important;
        margin-bottom: 14px !important;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04) !important;
        transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
    }
    .theme-card:hover, .metric-card:hover {
        border-color: rgba(128, 128, 128, 0.35) !important;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08) !important;
    }

    /* Accent Pill & Badge Styles */
    .theme-pill {
        display: inline-flex;
        align-items: center;
        padding: 3px 10px;
        border-radius: 12px;
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 0.02em;
        margin-right: 6px;
        color: #ffffff !important;
        text-shadow: 0 1px 2px rgba(0,0,0,0.3);
    }

    /* Adaptive Card Typography */
    .card-title {
        font-family: 'Outfit', sans-serif;
        font-size: 16px;
        font-weight: 600;
        margin-bottom: 6px;
    }
    .card-meta {
        font-size: 12px;
        opacity: 0.75;
    }
    .card-section-label {
        font-size: 11px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        opacity: 0.65;
        margin-top: 10px;
        margin-bottom: 2px;
    }

    /* Sage Banner Styling */
    .sage-intro {
        background: linear-gradient(135deg, rgba(26, 26, 46, 0.9) 0%, rgba(22, 33, 62, 0.9) 50%, rgba(15, 52, 96, 0.9) 100%);
        border: 1px solid rgba(196, 181, 253, 0.3);
        border-radius: 12px;
        padding: 20px 24px;
        margin-bottom: 20px;
        color: #f3f4f6 !important;
    }
    .sage-intro h3 {
        margin: 0 0 8px 0;
        color: #c4b5fd !important;
    }
    .sage-intro p {
        margin: 0;
        color: #e5e7eb !important;
        font-style: italic;
    }

    /* Touch / Clickable Button Polish */
    div.stButton > button {
        border-radius: 8px !important;
        font-weight: 500 !important;
        transition: all 0.15s ease !important;
    }
</style>
"""


def apply_design_system() -> None:
    """Inject the central design system CSS tokens and styles into the Streamlit app."""
    st.markdown(DESIGN_SYSTEM_CSS, unsafe_allow_html=True)


def get_plotly_theme_layout() -> Dict[str, Any]:
    """Returns Plotly kwargs for transparent theme-aware background layout."""
    return {
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {
            "family": "Inter, sans-serif",
        },
        "margin": dict(l=20, r=20, t=30, b=20),
    }


def sanitize_summary_html(text: str) -> str:
    """Strips or converts raw HTML <small> tags into clean Markdown text."""
    import re
    if not text:
        return ""
    # Clean legacy <small style="..."> tags from earlier runs
    cleaned = re.sub(r"<small[^>]*>(.*?)</small>", r"\1", text, flags=re.DOTALL)
    cleaned = re.sub(r"</?small[^>]*>", "", cleaned)
    return cleaned.strip()
