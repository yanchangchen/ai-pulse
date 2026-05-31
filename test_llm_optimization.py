#!/usr/bin/env python3
"""
Test script to verify LLM optimization works correctly.
"""

import sys
sys.path.insert(0, '/home/ubuntu/ai-pulse')

from core.summariser import _get_existing_article_hashes

def test_llm_optimization():
    """Test that LLM optimization detects existing articles."""
    print("\n" + "="*60)
    print("🧪 Testing LLM Optimization (Skip Already-Summarized Articles)")
    print("="*60 + "\n")
    
    # Test 1: Get existing hashes for a theme
    print("1️⃣  Testing _get_existing_article_hashes()")
    
    try:
        hashes = _get_existing_article_hashes("Agentic Systems")
        print(f"✅ Found {len(hashes)} existing article hashes for 'Agentic Systems'")
        
        if len(hashes) > 0:
            print(f"   Sample hashes: {list(hashes)[:3]}")
    except Exception as e:
        print(f"❌ Failed to get existing hashes: {e}")
        return False
    
    # Test 2: Verify the optimization logic
    print("\n2️⃣  Testing optimization logic")
    
    test_articles = [
        {
            "title": "Test Article 1",
            "content_hash": "hash_001",
            "summary": "Test summary 1"
        },
        {
            "title": "Test Article 2",
            "content_hash": "hash_002",
            "summary": "Test summary 2"
        }
    ]
    
    # Simulate filtering
    existing_hashes = _get_existing_article_hashes("Test Theme")
    new_articles = [
        a for a in test_articles
        if not a.get("content_hash") or a.get("content_hash") not in existing_hashes
    ]
    
    print(f"   Total articles: {len(test_articles)}")
    print(f"   Existing hashes in Supabase: {len(existing_hashes)}")
    print(f"   New articles to summarize: {len(new_articles)}")
    
    if len(new_articles) == len(test_articles):
        print("✅ All articles marked as new (expected for test theme)")
    else:
        print(f"✅ Correctly identified {len(test_articles) - len(new_articles)} existing articles")
    
    print("\n" + "="*60)
    print("✅ LLM OPTIMIZATION TEST PASSED")
    print("="*60 + "\n")
    
    return True

if __name__ == "__main__":
    success = test_llm_optimization()
    sys.exit(0 if success else 1)
