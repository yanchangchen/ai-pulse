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

# Fallback: load from streamlit secrets.toml
if not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_KEY"):
    secrets_path = Path(__file__).parent / ".streamlit" / "secrets.toml"
    if secrets_path.exists():
        try:
            import tomllib
            with open(secrets_path, "rb") as f:
                secrets = tomllib.load(f)
            for k, v in secrets.items():
                os.environ[k] = str(v)
        except Exception:
            # Fallback simple line parser
            try:
                with open(secrets_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if "=" in line:
                            k, v = line.split("=", 1)
                            os.environ[k.strip()] = v.strip().strip('"').strip("'")
            except Exception:
                pass

print("=" * 60)
print("Testing Supabase Integration")
print("=" * 60)

# Check environment variables
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")

if not supabase_url or not supabase_key:
    print("\n[ERROR] Missing SUPABASE_URL or SUPABASE_KEY in .env")
    sys.exit(1)

print(f"\n[OK] SUPABASE_URL: {supabase_url[:30]}...")
print(f"[OK] SUPABASE_KEY: {supabase_key[:20]}...")

# Test importing the client
try:
    from core.supabase_client import get_supabase_manager
    print("\n[OK] Successfully imported supabase_client module")
except ImportError as e:
    print(f"\n[ERROR] Failed to import supabase_client: {e}")
    sys.exit(1)

# Test initializing the manager
try:
    supabase = get_supabase_manager()
    print("[OK] Successfully initialized SupabaseManager")
except Exception as e:
    print("[ERROR] Failed to initialize SupabaseManager: {e}")
    sys.exit(1)

# Test connection
try:
    if supabase.is_available():
        print("[OK] Supabase client is available")
        
        # Try to get latest run
        latest = supabase.get_latest_run()
        if latest:
            print(f"[OK] Latest run found: {latest['run_timestamp']}")
        else:
            print("[OK] No runs yet (this is expected on first setup)")
    else:
        print("[ERROR] Supabase client is not available")
        sys.exit(1)
except Exception as e:
    print(f"[ERROR] Connection test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 60)
print("[SUCCESS] All tests passed! Supabase integration is working.")
print("=" * 60)
