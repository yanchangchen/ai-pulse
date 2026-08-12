import sys
import logging
from core.fetcher import fetch_all_news
from core.classifier import classify_articles, get_latest_gate_stats

logging.basicConfig(level=logging.INFO)

def main():
    print("Fetching articles from RSS feeds...")
    articles = fetch_all_news()
    print(f"\nTotal articles fetched: {len(articles)}")
    
    if not articles:
        print("No articles fetched.")
        return

    print("\nRunning 4-Pass Waterfall Classification...")
    themed = classify_articles(articles)
    
    stats = get_latest_gate_stats()
    total = stats["total"]
    g1 = stats["gate_1_keyword"]
    g2 = stats["gate_2_tfidf"]
    g3 = stats["gate_3_ollama"]
    g4 = stats["gate_4_heuristic"]
    
    g1_pct = (g1 / total * 100) if total else 0
    g2_pct = (g2 / total * 100) if total else 0
    g12_pct = ((g1 + g2) / total * 100) if total else 0
    g3_pct = (g3 / total * 100) if total else 0
    g4_pct = (g4 / total * 100) if total else 0

    print("\n" + "="*60)
    print("EMPIRICAL CLASSIFICATION GATE BREAKDOWN (REAL LIVE FEEDS)")
    print("="*60)
    print(f"Total Articles Processed:               {total}")
    print(f"Gate 1 (Weighted Keywords):             {g1:3d}  ({g1_pct:5.1f}%)")
    print(f"Gate 2 (TF-IDF Cosine Vector):          {g2:3d}  ({g2_pct:5.1f}%)")
    print(f"------------------------------------------------------------")
    print(f"TOTAL NON-LLM AUTOMATED MATCH (Gate 1+2): {g1+g2:3d}  ({g12_pct:5.1f}%)")
    print(f"------------------------------------------------------------")
    print(f"Gate 3 (Ollama LLM Semantic Batch):     {g3:3d}  ({g3_pct:5.1f}%)")
    print(f"Gate 4 (Soft Heuristic Fallback):       {g4:3d}  ({g4_pct:5.1f}%)")
    print("="*60)

if __name__ == "__main__":
    main()
