# Theme definitions for AI Pulse

THEMES = {
    "AI Applications & Architecture": {
        "keywords": [
            "RAG", "agents", "agentic", "LangChain", "LangGraph", "vector database",
            "Neo4j", "MCP", "tool use", "prompt engineering", "production",
            "deployment", "fine-tuning", "LLM", "chatbot", "automation", "workflow",
            "API integration", "pipeline", "embedding", "retrieval", "knowledge base"
        ],
        "color": "blue"
    },
    "AI Models": {
        "keywords": [
            "model release", "benchmark", "GPT", "Claude", "Gemini", "Llama", "Mistral",
            "multimodal", "reasoning", "context window", "training", "weights",
            "open source model", "model", "version", "update", "new model", "premium",
            "frontier", "architecture", "parameters", "capabilities", "performance"
        ],
        "color": "purple"
    },
    "AI Infrastructure": {
        "keywords": [
            "GPU", "NVIDIA", "compute", "cloud", "inference", "latency", "cost",
            "TPU", "hardware", "data center", "MLOps", "LLMOps", "Kubernetes",
            "serving", "cluster", "server", "performance", "optimization", "chip",
            "AMD", "Intel", "Semiconductor", "memory", "bandwidth", "throughput"
        ],
        "color": "orange"
    },
    "AI Companies & Business": {
        "keywords": [
            "funding", "acquisition", "partnership", "valuation", "startup",
            "OpenAI", "Anthropic", "Google DeepMind", "Meta AI", "Microsoft",
            "Amazon", "revenue", "enterprise", "IPO", "investor", "deal", " VC",
            "估值", "收购", "合作", "融资", "business", "commercial", "market"
        ],
        "color": "green"
    },
    "AI in Government & Policy": {
        "keywords": [
            "regulation", "EU AI Act", "executive order", "safety", "alignment",
            "risk", "copyright", "policy", "senate", "congress", "governance",
            "ban", "legislation", "compliance", "law", "government", "official",
            "agency", "NIST", "White House", "Europe", "China AI"
        ],
        "color": "red"
    }
}

THEME_COLORS = {
    "AI Applications & Architecture": "#1f77b4",  # Blues
    "AI Models": "#9467bd",  # Purples
    "AI Infrastructure": "#ff7f0e",  # Oranges
    "AI Companies & Business": "#2ca02c",  # Greens
    "AI in Government & Policy": "#d62728"  # Reds
}

# Theme order for display
THEME_ORDER = list(THEMES.keys())
