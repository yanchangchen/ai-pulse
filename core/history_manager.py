"""
History and memory manager for AI Pulse.
Handles persisting summaries to JSON (for parsing) and memory.md (for context/wiki).
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Paths
ROOT_DIR = Path(__file__).resolve().parent.parent
HISTORY_JSON = ROOT_DIR / "history.json"
MEMORY_MD = ROOT_DIR / "memory.md"

def save_run_to_history(summaries: Dict[str, Dict[str, str]], article_counts: Dict[str, int]) -> None:
    """Save the current summaries to both JSON and Markdown history."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    date_key = datetime.now().strftime("%Y-%m-%d")

    # 1. Update JSON History
    history_data = {}
    if HISTORY_JSON.exists():
        try:
            with open(HISTORY_JSON, "r", encoding="utf-8") as f:
                history_data = json.load(f)
        except Exception:
            history_data = {}

    history_data[timestamp] = {
        "date": date_key,
        "summaries": summaries,
        "counts": article_counts
    }

    with open(HISTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(history_data, f, indent=2, ensure_ascii=False)

    # 2. Update memory.md (Append-only Wiki)
    new_entry = f"\n## ⚡ AI Pulse Run: {timestamp}\n"
    for theme, summary in summaries.items():
        count = article_counts.get(theme, 0)
        new_entry += f"### {theme} ({count} articles)\n"
        new_entry += f"**What is happening:** {summary.get('what_is_happening', '')}\n\n"
        new_entry += f"**Why it matters:** {summary.get('why_it_matters', '')}\n\n"
        new_entry += f"**Watch:** {summary.get('what_to_watch', '')}\n\n"
        new_entry += "---\n"

    if not MEMORY_MD.exists():
        with open(MEMORY_MD, "w", encoding="utf-8") as f:
            f.write("# AI Pulse - Memory Wiki\nTracking the evolution of AI developments.\n")

    with open(MEMORY_MD, "a", encoding="utf-8") as f:
        f.write(new_entry)

def get_recent_context(theme_name: str, limit: int = 2) -> str:
    """Retrieve the most recent summaries for a theme to provide context to the LLM."""
    if not HISTORY_JSON.exists():
        return ""

    try:
        with open(HISTORY_JSON, "r", encoding="utf-8") as f:
            history_data = json.load(f)
        
        # Sort by timestamp descending
        sorted_keys = sorted(history_data.keys(), reverse=True)
        
        context_parts = []
        for key in sorted_keys[:limit]:
            theme_summary = history_data[key]["summaries"].get(theme_name)
            if theme_summary:
                date = history_data[key]["date"]
                context_parts.append(f"On {date}: {theme_summary.get('what_is_happening')}")
        
        if context_parts:
            return "\nPrevious Context:\n" + "\n".join(context_parts)
    except Exception:
        pass
    
    return ""

def load_full_history() -> Dict:
    """Load the full history for the Wiki page."""
    if not HISTORY_JSON.exists():
        return {}
    try:
        with open(HISTORY_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
