#!/usr/bin/env python3
"""
Test script to verify backfill functionality.
"""

import sys
import os
from datetime import datetime

sys.path.insert(0, '/home/ubuntu/ai-pulse')

from core.supabase_client import get_supabase_manager
from core.history_manager import load_full_history

def test_backfill():
    """Test that backfill works correctly."""
    print("\n" + "="*60)
    print("🧪 Testing Historical Data Backfill")
    print("="*60 + "\n")
    
    supabase = get_supabase_manager()
    
    if not supabase.is_available():
        print("❌ Supabase not available")
        return False
    
    print("✅ Supabase connected")
    
    # Load historical data
    print("\n1️⃣  Loading historical data from history.json")
    history = load_full_history()
    
    if not history:
        print("⚠️  No historical data found in history.json (this is OK for first run)")
        return True
    
    print(f"✅ Found {len(history)} historical runs")
    
    # Run backfill
    print(f"\n2️⃣  Running backfill on {len(history)} runs")
    stats = supabase.backfill_from_history(history)
    
    print(f"\n📊 Backfill Results:")
    print(f"   Total runs: {stats['total_runs']}")
    print(f"   Inserted: {stats['inserted_runs']}")
    print(f"   Skipped: {stats['skipped_runs']}")
    print(f"   Articles inserted: {stats['inserted_articles']}")
    print(f"   Errors: {len(stats['errors'])}")
    
    if stats['errors']:
        print(f"\n⚠️  Errors encountered:")
        for error in stats['errors'][:5]:  # Show first 5 errors
            print(f"   - {error}")
    
    # Verify data in Supabase
    print(f"\n3️⃣  Verifying data in Supabase")
    try:
        runs_response = supabase.client.table("trend_runs").select("id").execute()
        total_runs = len(runs_response.data) if runs_response.data else 0
        
        articles_response = supabase.client.table("articles").select("id").execute()
        total_articles = len(articles_response.data) if articles_response.data else 0
        
        print(f"✅ Supabase now contains:")
        print(f"   - {total_runs} trend runs")
        print(f"   - {total_articles} articles")
        
        return True
    except Exception as e:
        print(f"❌ Verification failed: {e}")
        return False

if __name__ == "__main__":
    success = test_backfill()
    
    print("\n" + "="*60)
    if success:
        print("✅ BACKFILL TEST PASSED")
    else:
        print("❌ BACKFILL TEST FAILED")
    print("="*60 + "\n")
    
    sys.exit(0 if success else 1)
