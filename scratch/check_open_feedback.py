"""
Script to query Supabase and local storage for open user feedback items.
"""

import os
import sys
import logging
from pathlib import Path

secrets_path = Path(".streamlit/secrets.toml")
if secrets_path.exists():
    import toml
    try:
        sec = toml.load(secrets_path)
        if "SUPABASE_URL" in sec:
            os.environ["SUPABASE_URL"] = sec["SUPABASE_URL"]
        if "SUPABASE_KEY" in sec:
            os.environ["SUPABASE_KEY"] = sec["SUPABASE_KEY"]
    except Exception as e:
        print(f"Error loading secrets: {e}")

from core.supabase_client import get_supabase_manager

logging.basicConfig(level=logging.INFO)

def main():
    supabase = get_supabase_manager()
    print(f"Supabase available: {supabase.is_available()}")

    # Retrieve all open feedback items
    open_items = supabase.get_all_feedback(status_filter="open", limit=100)
    all_items = supabase.get_all_feedback(limit=100)

    print(f"\n--- FEEDBACK STATUS REPORT ---")
    print(f"Total Feedback Items Stored: {len(all_items)}")
    print(f"Total OPEN Feedback Items:  {len(open_items)}")

    if open_items:
        print("\nOPEN FEEDBACK ITEMS:")
        for idx, item in enumerate(open_items, 1):
            print(f"{idx}. [{item.get('category', 'General')}] {item.get('title', 'Untitled')}")
            print(f"   Status: {item.get('status')} | Created: {item.get('created_at')}")
            print(f"   Description: {item.get('description', '')}")
            print("-" * 50)
    elif all_items:
        print("\nALL FEEDBACK ITEMS (None are open):")
        for idx, item in enumerate(all_items, 1):
            print(f"{idx}. [{item.get('category', 'General')}] {item.get('title', 'Untitled')}")
            print(f"   Status: {item.get('status')} | Created: {item.get('created_at')}")
            print(f"   Description: {item.get('description', '')[:200]}")
            print("-" * 50)

if __name__ == "__main__":
    main()
