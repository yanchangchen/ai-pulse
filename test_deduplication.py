#!/usr/bin/env python3
"""
Test script to verify UPSERT deduplication works correctly.
"""

import sys
import os
import json
from datetime import datetime

# Add project root to path
sys.path.insert(0, '/home/ubuntu/ai-pulse')

from core.supabase_client import get_supabase_manager
from core.history_manager import load_full_history

def test_deduplication():
    """Test that UPSERT deduplication works."""
    print("\n" + "="*60)
    print("🧪 Testing UPSERT Deduplication")
    print("="*60 + "\n")
    
    supabase = get_supabase_manager()
    
    if not supabase.is_available():
        print("❌ Supabase not available")
        return False
    
    print("✅ Supabase connected")
    
    # Create a test trend run
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    date = datetime.now().strftime("%Y-%m-%d")
    
    print(f"\n1️⃣  Creating test trend run: {timestamp}")
    run = supabase.save_trend_run(timestamp, date, 2)
    
    if not run:
        print("❌ Failed to create trend run")
        return False
    
    run_id = run["id"]
    print(f"✅ Created run: {run_id}")
    
    # Create test articles with same content_hash (simulating duplicates)
    test_articles = [
        {
            "title": "Test Article 1",
            "summary": "This is a test article",
            "source_name": "Test Source",
            "link": "https://example.com/1",
            "published_at": "2026-05-29T10:00:00Z",
            "content_hash": "abc123def456"  # Same hash
        },
        {
            "title": "Test Article 1 (Duplicate)",
            "summary": "This is a test article (different summary)",
            "source_name": "Test Source",
            "link": "https://example.com/1",
            "published_at": "2026-05-29T10:00:00Z",
            "content_hash": "abc123def456"  # Same hash - should be deduplicated
        }
    ]
    
    print(f"\n2️⃣  Saving 2 articles with SAME content_hash (testing deduplication)")
    result1 = supabase.save_articles(run_id, "Test Theme", test_articles)
    
    if not result1:
        print("❌ Failed to save articles")
        return False
    
    print(f"✅ First save: {len(result1)} article(s) saved")
    
    # Try saving the same articles again - should not create duplicates
    print(f"\n3️⃣  Saving same articles again (should deduplicate)")
    result2 = supabase.save_articles(run_id, "Test Theme", test_articles)
    
    if not result2:
        print("❌ Failed to save articles on second attempt")
        return False
    
    print(f"✅ Second save: {len(result2)} article(s) saved")
    
    # Query to verify only 1 unique article exists
    print(f"\n4️⃣  Querying database to verify deduplication")
    try:
        response = supabase.client.table("articles").select("*").eq(
            "run_id", run_id
        ).execute()
        
        unique_articles = response.data if response.data else []
        print(f"✅ Found {len(unique_articles)} unique article(s) in database")
        
        if len(unique_articles) == 1:
            print("✅ DEDUPLICATION WORKS: Only 1 article stored despite 2 attempts")
            return True
        else:
            print(f"❌ DEDUPLICATION FAILED: Expected 1 article, got {len(unique_articles)}")
            return False
    
    except Exception as e:
        print(f"❌ Query failed: {e}")
        return False

if __name__ == "__main__":
    success = test_deduplication()
    
    print("\n" + "="*60)
    if success:
        print("✅ DEDUPLICATION TEST PASSED")
    else:
        print("❌ DEDUPLICATION TEST FAILED")
    print("="*60 + "\n")
    
    sys.exit(0 if success else 1)
