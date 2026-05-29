#!/usr/bin/env python3
"""Test script to verify Supabase integration."""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

# Load environment variables
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

print("=" * 60)
print("Testing Supabase Integration")
print("=" * 60)

# Check environment variables
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")

if not supabase_url or not supabase_key:
    print("\n✗ Missing SUPABASE_URL or SUPABASE_KEY in .env")
    sys.exit(1)

print(f"\n✓ SUPABASE_URL: {supabase_url[:30]}...")
print(f"✓ SUPABASE_KEY: {supabase_key[:20]}...")

# Test importing the client
try:
    from core.supabase_client import get_supabase_manager
    print("\n✓ Successfully imported supabase_client module")
except ImportError as e:
    print(f"\n✗ Failed to import supabase_client: {e}")
    sys.exit(1)

# Test initializing the manager
try:
    supabase = get_supabase_manager()
    print("✓ Successfully initialized SupabaseManager")
except Exception as e:
    print(f"✗ Failed to initialize SupabaseManager: {e}")
    sys.exit(1)

# Test connection
try:
    if supabase.is_available():
        print("✓ Supabase client is available")
        
        # Try to get latest run
        latest = supabase.get_latest_run()
        if latest:
            print(f"✓ Latest run found: {latest['run_timestamp']}")
        else:
            print("✓ No runs yet (this is expected on first setup)")
    else:
        print("✗ Supabase client is not available")
        sys.exit(1)
except Exception as e:
    print(f"✗ Connection test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 60)
print("✅ All tests passed! Supabase integration is working.")
print("=" * 60)
