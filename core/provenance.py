"""
Render a small provenance chip for a theme summary.

The summary dict carries two fields set by ``core.summariser._with_provenance``:
    _source         — short token (e.g. "ollama:qwen3", "gemini:gemini-2.0-flash",
                      "extractive_fallback", "ollama:error")
    _generation_log — dict with model, article_count, generated_at, note, …

This module turns those into a coloured pill + optional tooltip so the user
sees, at a glance, whether the brief is a real LLM synthesis or a fallback.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple


# (label, background, foreground, border) — the "Provenance" palette.
# Keep these in one place so chips stay consistent across pages.
_CHIP_STYLES: dict[str, Tuple[str, str, str, str]] = {
    "extractive_fallback":     ("Non-LLM fallback",   "#fff4e5", "#8a4b00", "#f0a850"),
    "ollama:error":            ("LLM error",          "#fde7e7", "#a0162b", "#e06a6a"),
    "ollama:no_articles":      ("No articles",        "#eef0f4", "#525a6b", "#aab1bf"),
    "ollama:limited_coverage": ("Limited coverage",   "#eef0f4", "#525a6b", "#aab1bf"),
    "ollama:no_new_articles_skip": ("Skipped — cached", "#eef0f4", "#525a6b", "#aab1bf"),
    "ollama":                  ("Ollama synthesis",   "#e6f3ff", "#0b4a8a", "#5aa6dd"),
    "gemini":                  ("Gemini synthesis",   "#ece9ff", "#3a1d8a", "#7e6dd8"),
}

_BADGE_RE = re.compile(r"<em[^>]*>\s*⚠️?\s*Non-LLM Extractive Summary.*?</em>", re.IGNORECASE | re.DOTALL)


def _classify(source: Optional[str]) -> str:
    """Map a raw ``_source`` token to a chip style key."""
    if not source:
        return "extractive_fallback"  # Treat unknowns as fallback for safety.
    if source in _CHIP_STYLES:
        return source
    if source.startswith("ollama:"):
        return "ollama"
    if source.startswith("gemini:"):
        return "gemini"
    return "extractive_fallback"


def _format_label(source: Optional[str]) -> str:
    """Turn ``"ollama:qwen3-coder:30b"`` into ``"Ollama · qwen3-coder:30b"``."""
    if not source:
        return "Unknown source"
    if source in _CHIP_STYLES:
        return _CHIP_STYLES[source][0]
    if source.startswith("ollama:"):
        return f"Ollama · {source.split(':', 1)[1]}"
    if source.startswith("gemini:"):
        return f"Gemini · {source.split(':', 1)[1]}"
    return source


def strip_fallback_banner(text: str) -> str:
    """Drop the inline ``<em>⚠️ Non-LLM Extractive Summary…</em>`` banner so the
    chip is the single source of truth for the fallback signal."""
    if not text:
        return text
    return _BADGE_RE.sub("", text).strip()


def render_provenance_chip(
    summary: dict,
    *,
    tooltip: bool = True,
) -> str:
    """Return an HTML string for a coloured provenance pill.

    The chip is intentionally self-contained (inline style + emoji-free) so it
    renders identically inside the existing ``theme-card`` markup and the
    Deep Dive header.
    """
    source = summary.get("_source")
    style_key = _classify(source)
    label, bg, fg, border = _CHIP_STYLES[style_key]
    title_attr = ""
    if tooltip:
        log = summary.get("_generation_log") or {}
        parts = []
        if log.get("model"):
            parts.append(f"model: {log['model']}")
        if log.get("article_count") is not None:
            parts.append(f"articles: {log['article_count']}")
        if log.get("note"):
            parts.append(f"note: {log['note']}")
        if log.get("generated_at"):
            parts.append(f"at: {log['generated_at']}")
        if log.get("error"):
            parts.append(f"error: {log['error']}")
        if parts:
            title_attr = f' title="{" · ".join(parts)}"'

    icon = "🧠" if style_key == "ollama" else "✨" if style_key == "gemini" else "🛟"
    return (
        f'<span class="provenance-chip"'
        f' style="display:inline-block; padding:2px 10px; margin:0 0 8px 0;'
        f' font-size:11px; font-weight:600; letter-spacing:0.02em;'
        f' border-radius:999px; background:{bg}; color:{fg};'
        f' border:1px solid {border};"{title_attr}>{icon} {_format_label(source)}</span>'
    )


def render_provenance_chip_markdown(
    summary: dict,
    *,
    tooltip: bool = True,
) -> str:
    """Same as :func:`render_provenance_chip` but emitted as a ``:streamlit:``
    badge that the design system can style.  Currently identical; reserved
    for future theming."""
    return render_provenance_chip(summary, tooltip=tooltip)
