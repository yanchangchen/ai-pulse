"""
Deterministic Intelligence Layer - Non-LLM fallback processing.
"""
import re
from collections import Counter
from typing import List, Dict, Any

# AI Pulse theme keywords (matches config/themes.py)
THEME_KEYWORDS = {
    "Agentic Systems & DevTools": [
        "agent", "langchain", "rag", "framework", "tool", "orchestration",
        "mcp", "autogpt", "babyagi", "langgraph", "crewai", "llamaindex"
    ],
    "Frontier Models & Benchmarks": [
        "gpt", "claude", "llama", "gemini", "benchmark", "model", "release",
        "mistral", "mixtral", "qwen", "deepseek", "yi", "phi", "nemotron"
    ],
    "Hardware/Compute/LLMOps": [
        "gpu", "compute", "inference", "mlops", "training", "latency", "throughput",
        "h100", "a100", "tpu", "vllm", "tensorrt", "onnx", "quantization"
    ],
    "Enterprise Strategy & ROI": [
        "funding", "acquisition", "enterprise", "roi", "business", "valuation",
        "revenue", "partnership", "ipo", "series", "round", "investment"
    ],
    "Governance/Safety/Policy": [
        "regulation", "safety", "governance", "policy", "eu ai act", "alignment",
        "executive order", "nist", "iso", "compliance", "audit", "red team"
    ],
    "AI Security & Trust": [
        "security", "privacy", "attack", "vulnerability", "red team", "trust",
        "injection", "jailbreak", "poisoning", "watermark", "provenance"
    ],
    "AI-Assisted Software Engineering": [
        "copilot", "code", "developer", "programming", "ide", "generation",
        "cursor", "windsurf", "vscode", "github copilot", "codeium", "tabnine"
    ],
}


def rule_categorise(text: str) -> Dict[str, Any]:
    """Weighted keyword categorisation (matches existing classifier logic)."""
    text_lower = text.lower()
    scores = {}

    for theme, keywords in THEME_KEYWORDS.items():
        score = sum(text_lower.count(kw) * (i + 1) for i, kw in enumerate(keywords))
        if score > 0:
            scores[theme] = score

    if not scores:
        return {"category": "Uncategorised", "confidence": 0.0, "method": "deterministic"}

    best = max(scores, key=scores.get)
    total = sum(scores.values())
    confidence = min(scores[best] / max(total, 1), 1.0)

    return {
        "category": best,
        "confidence": confidence,
        "scores": scores,
        "method": "deterministic",
    }


def extractive_summarise(text: str, max_sentences: int = 5) -> Dict[str, Any]:
    """Simple extractive summarisation using position + keyword scoring."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    if len(sentences) <= max_sentences:
        return {"summary": text, "sentences": sentences, "method": "extractive-summary-v1"}

    # Score by position and keyword density
    scored = []
    ai_keywords = [
        "ai", "model", "release", "benchmark", "funding", "agent", "rag",
        "gpu", "inference", "training", "enterprise", "regulation", "security"
    ]
    for i, sent in enumerate(sentences):
        pos_score = 1.0 - (i / len(sentences)) * 0.5  # Earlier = higher
        kw_score = sum(1 for kw in ai_keywords if kw in sent.lower())
        scored.append((sent, pos_score + kw_score * 0.3))

    top = sorted(scored, key=lambda x: x[1], reverse=True)[:max_sentences]
    # Restore original order
    top_sentences = [s for s, _ in sorted(top, key=lambda x: sentences.index(x[0]))]

    return {
        "summary": " ".join(top_sentences),
        "sentences": top_sentences,
        "method": "extractive-summary-v1",
    }


def keyword_extract(text: str, top_k: int = 10) -> Dict[str, Any]:
    """Simple keyword extraction with stopword filtering."""
    words = re.findall(r"\b[a-z]{3,}\b", text.lower())
    stopwords = {
        "the", "and", "for", "are", "but", "not", "you", "all", "can", "has",
        "was", "one", "our", "out", "day", "get", "use", "her", "his", "how",
        "its", "may", "new", "now", "old", "see", "two", "who", "did", "man",
        "put", "say", "she", "way", "will", "with", "from", "they", "this",
        "that", "have", "been", "were", "said", "each", "which", "their",
        "time", "would", "there", "could", "other", "than", "then", "them",
        "these", "some", "what", "when", "where", "while", "after", "before",
        "about", "above", "below", "between", "under", "over", "again",
        "further", "once", "here", "more", "most", "such", "only", "own",
        "same", "very", "just", "into", "than", "been", "also", "into",
    }
    filtered = [w for w in words if w not in stopwords]
    freq = Counter(filtered)

    return {
        "keywords": [{"word": w, "count": c} for w, c in freq.most_common(top_k)],
        "method": "deterministic",
    }


def statistical_projection(series: List[float]) -> Dict[str, Any]:
    """Simple trend projection using linear regression."""
    if len(series) < 3:
        return {"trend": "insufficient_data", "method": "deterministic"}

    n = len(series)
    x = list(range(n))
    x_mean = sum(x) / n
    y_mean = sum(series) / n

    num = sum((x[i] - x_mean) * (series[i] - y_mean) for i in range(n))
    den = sum((x[i] - x_mean) ** 2 for i in range(n))
    slope = num / den if den != 0 else 0

    trend = "increasing" if slope > 0.01 else "decreasing" if slope < -0.01 else "stable"
    momentum = min(abs(slope) * 10, 1.0)

    return {
        "trend": trend,
        "momentum": momentum,
        "slope": slope,
        "method": "deterministic",
    }


def extract_key_signals(text: str) -> Dict[str, Any]:
    """Extract structured signals from text (dates, numbers, entities)."""
    signals = {}

    # Extract percentages
    pcts = re.findall(r"(\d+(?:\.\d+)?)\s*%", text)
    if pcts:
        signals["percentages"] = [float(p) for p in pcts]

    # Extract dollar amounts
    dollars = re.findall(r"\$(\d+(?:\.\d+)?)\s*([MBK]?)", text)
    if dollars:
        signals["currency"] = [{"amount": float(d[0]), "unit": d[1]} for d in dollars]

    # Extract version numbers
    versions = re.findall(r"\b(v?\d+\.\d+(?:\.\d+)?)\b", text)
    if versions:
        signals["versions"] = list(set(versions))

    # Extract dates
    dates = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if dates:
        signals["dates"] = dates

    return {"signals": signals, "method": "deterministic"}