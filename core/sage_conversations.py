"""
Sage Conversations Manager — Local & Cloud Persistent Conversation Threads.

Handles saving, listing, loading, deleting, and exporting Sage conversations
with Supabase as the primary persistent cloud store, plus a local cache fallback.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from core.logger import setup_logger

logger = setup_logger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
CONVERSATIONS_FILE = ROOT_DIR / "data" / "sage_conversations.json"


def _ensure_storage_file() -> Path:
    """Ensure the data directory and conversation file exist."""
    CONVERSATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not CONVERSATIONS_FILE.exists():
        try:
            with open(CONVERSATIONS_FILE, "w", encoding="utf-8") as f:
                json.dump({"conversations": {}}, f, indent=2)
        except Exception as e:
            logger.error("Failed to initialize conversations file: %s", e)
    return CONVERSATIONS_FILE


def _load_data() -> Dict[str, Any]:
    """Read the conversations store from disk."""
    _ensure_storage_file()
    try:
        with open(CONVERSATIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and "conversations" in data:
                return data
            return {"conversations": {}}
    except Exception as e:
        logger.error("Error reading sage conversations: %s", e)
        return {"conversations": {}}


def _save_data(data: Dict[str, Any]) -> bool:
    """Write the conversations store to disk safely."""
    _ensure_storage_file()
    try:
        temp_file = CONVERSATIONS_FILE.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        temp_file.replace(CONVERSATIONS_FILE)
        return True
    except Exception as e:
        logger.error("Error writing sage conversations: %s", e)
        return False


def list_saved_conversations() -> List[Dict[str, Any]]:
    """List all saved conversations on demand from Supabase (or local cache)."""
    try:
        from core.supabase_client import get_supabase_manager
        mgr = get_supabase_manager()
        if mgr.is_available():
            sb_convs = mgr.get_sage_conversations()
            if sb_convs is not None and len(sb_convs) > 0:
                return sb_convs
    except Exception as e:
        logger.warning("Failed to list conversations from Supabase: %s", e)

    data = _load_data()
    convs = data.get("conversations", {})
    results = []
    for cid, c in convs.items():
        results.append({
            "id": cid,
            "title": c.get("title", "Untitled Conversation"),
            "created_at": c.get("created_at", ""),
            "updated_at": c.get("updated_at", ""),
            "message_count": len(c.get("messages", [])),
            "theme_filter": c.get("theme_filter"),
            "period_label": c.get("period_label"),
        })
    results.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return results


def get_conversation(conv_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a single saved conversation by ID on demand from Supabase (or local cache)."""
    if not conv_id:
        return None

    try:
        from core.supabase_client import get_supabase_manager
        mgr = get_supabase_manager()
        if mgr.is_available():
            sb_conv = mgr.get_sage_conversation_by_id(conv_id)
            if sb_conv:
                return sb_conv
    except Exception as e:
        logger.warning("Failed to get conversation from Supabase: %s", e)

    data = _load_data()
    return data.get("conversations", {}).get(conv_id)


def save_conversation(
    messages: List[Dict[str, str]],
    conv_id: Optional[str] = None,
    title: Optional[str] = None,
    theme_filter: Optional[str] = None,
    period_label: Optional[str] = None,
) -> str:
    """Save or update a conversation thread in Supabase on demand (with local cache fallback).

    Returns the conversation ID.
    """
    if not messages:
        return conv_id or str(uuid.uuid4())

    conv_id = conv_id or str(uuid.uuid4())

    if not title:
        first_user_msg = next((m.get("content", "") for m in messages if m.get("role") == "user"), "Conversation")
        cleaned = " ".join(first_user_msg.split())
        title = cleaned[:50] + ("…" if len(cleaned) > 50 else "")

    # 1. Primary: Save to Supabase Cloud
    try:
        from core.supabase_client import get_supabase_manager
        mgr = get_supabase_manager()
        if mgr.is_available():
            sb_res = mgr.save_sage_conversation(
                messages=messages,
                conv_id=conv_id,
                title=title,
                theme_filter=theme_filter,
                period_label=period_label,
            )
            if sb_res and sb_res.get("id"):
                conv_id = str(sb_res["id"])
    except Exception as e:
        logger.warning("Failed to save conversation to Supabase: %s", e)

    # 2. Mirror to local cache for instant offline fallback
    data = _load_data()
    convs = data.setdefault("conversations", {})
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    convs[conv_id] = {
        "id": conv_id,
        "title": title,
        "created_at": convs.get(conv_id, {}).get("created_at", now_str),
        "updated_at": now_str,
        "theme_filter": theme_filter,
        "period_label": period_label,
        "messages": messages,
    }
    _save_data(data)
    return conv_id


def delete_conversation(conv_id: str) -> bool:
    """Delete a conversation thread by ID from Supabase and local cache."""
    deleted_sb = False
    try:
        from core.supabase_client import get_supabase_manager
        mgr = get_supabase_manager()
        if mgr.is_available():
            deleted_sb = mgr.delete_sage_conversation(conv_id)
    except Exception as e:
        logger.warning("Failed to delete conversation from Supabase: %s", e)

    data = _load_data()
    convs = data.get("conversations", {})
    if conv_id in convs:
        del convs[conv_id]
        _save_data(data)
        return True

    return deleted_sb


def export_conversation_markdown(
    title: str,
    messages: List[Dict[str, str]],
    theme_filter: Optional[str] = None,
    period_label: Optional[str] = None,
) -> str:
    """Format conversation as downloadable Markdown."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# 🔮 Sage Research Report: {title}",
        f"*Generated on {now_str} via AI Pulse Memory Wiki*",
        "",
        f"- **Theme Focus:** {theme_filter or 'All Themes'}",
        f"- **Time Period:** {period_label or 'Archive Default'}",
        "",
        "---",
        "",
    ]

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "").strip()
        if role == "user":
            lines.append(f"### 👤 User\n\n{content}\n")
        else:
            lines.append(f"### 🔮 Sage\n\n{content}\n")
        lines.append("---\n")

    return "\n".join(lines)
