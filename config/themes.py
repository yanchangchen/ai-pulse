# Theme definitions for AI Pulse
# Keywords are weighted dicts: higher weight = stronger signal for that theme.

THEMES = {
    "Agentic Systems & DevTools": {
        "keywords": {
            "RAG": 3, "agents": 3, "agentic": 3, "LangChain": 3, "LangGraph": 3,
            "vector database": 3, "Neo4j": 2, "MCP": 3, "tool use": 3,
            "multi-agent": 3, "orchestration": 3, "swarm": 3, "A2A": 2,
            "agentic token control": 3, "doom-loop": 3, "dreaming": 2,
            "memory distillation": 3, "episodic memory": 3, "context engineering": 2,
            "prompt engineering": 2, "production": 1, "deployment": 1,
            "fine-tuning": 2, "LLM": 1, "chatbot": 2, "automation": 1,
            "workflow": 1, "API integration": 2, "pipeline": 2,
            "embedding": 2, "retrieval": 2, "knowledge base": 2,
            "Model Context Protocol": 3, "episodic": 3, "swarms": 3
        },
        "color": "blue"
    },
    "Frontier Models & Benchmarks": {
        "keywords": {
            "model release": 3, "benchmark": 3, "GPT": 2, "Claude": 2,
            "Gemini": 2, "Llama": 2, "Mistral": 2, "multimodal": 2,
            "reasoning": 2, "context window": 3, "training": 1, "weights": 2,
            "open source model": 3, "new model": 3, "frontier": 2,
            "MoE": 3, "speculative decoding": 3, "KV cache": 3,
            "MMLU": 2, "GPQA": 3, "SWE-bench": 3, "ARC-AGI": 3,
            "parameters": 2, "capabilities": 1, "LMSYS": 3,
            "RLHF": 2, "GRPO": 3, "alignment": 2, "contamination": 3
        },
        "color": "purple"
    },
    "Hardware, Compute & LLMOps": {
        "keywords": {
            "GPU": 3, "NVIDIA": 2, "compute": 2, "inference": 2,
            "latency": 2, "cost": 1, "TPU": 3, "hardware": 2,
            "Blackwell": 3, "CoreWeave": 3, "Graviton": 2,
            "data center": 3, "MLOps": 3, "LLMOps": 3, "Kubernetes": 2,
            "serving": 2, "cluster": 2, "chip": 3, "AMD": 2, "Intel": 2,
            "Semiconductor": 3, "memory": 1, "bandwidth": 2, "throughput": 2,
            "electricity": 2, "carbon footprint": 2, "power demand": 2,
            "edge inference": 3, "unit economics": 2
        },
        "color": "orange"
    },
    "Enterprise Strategy & ROI": {
        "keywords": {
            "funding": 3, "acquisition": 3, "partnership": 2, "valuation": 3,
            "startup": 2, "OpenAI": 1, "Anthropic": 1, "Google DeepMind": 1,
            "Meta AI": 1, "Microsoft": 1, "Amazon": 1, "revenue": 3,
            "enterprise": 2, "IPO": 3, "investor": 3, "deal": 2, " VC": 3,
            "business": 1, "commercial": 2, "market": 1, "time-to-market": 3,
            "pricing models": 2, "ROI": 3, "feasibility": 2
        },
        "color": "green"
    },
    "Governance, Safety & Policy": {
        "keywords": {
            "regulation": 3, "EU AI Act": 3, "executive order": 3, "safety": 2,
            "alignment": 2, "risk": 1, "copyright": 2, "policy": 2,
            "senate": 3, "congress": 3, "governance": 2, "ban": 2,
            "export control": 3, "sovereign AI": 3, "malicious model": 3,
            "supply chain security": 3, "model signing": 3, "HuggingFace malware": 3,
            "legislation": 3, "compliance": 2, "law": 1, "government": 2,
            "NIST": 3, "White House": 3, "Europe": 1, "China AI": 2,
            "export controls": 3, "copyright indemnity": 3
        },
        "color": "red"
    },
    "AI Security & Trust": {
        "keywords": {
            "prompt injection": 3, "indirect prompt injection": 3, "jailbreak": 3,
            "red-teaming": 3, "red team": 2, "red teaming": 3, "guardrails": 3,
            "adversarial": 2, "adversarial attack": 3, "poisoning": 3,
            "data poisoning": 3, "model poisoning": 3, "backdoor": 3,
            "evasion attack": 3, "exfiltration": 3, "data exfiltration": 3,
            "prompt leak": 3, "system prompt leak": 3, "prompt extraction": 3,
            "insecure output": 2, "insecure output handling": 3,
            "excessive agency": 3, "rogue agent": 3, "agent hijack": 3,
            "model theft": 3, "weight extraction": 3, "membership inference": 3,
            "model inversion": 3, "differential privacy": 2,
            "homomorphic encryption": 2, "federated learning": 2,
            "confidential computing": 2, "secure enclaves": 2,
            "vulnerability disclosure": 3, "CVE": 2, "supply chain": 2,
            "SBOM": 2, "model card": 2, "provenance": 2,
            "sandboxing": 2, "isolation": 1, "TrojAI": 3,
            "MLSec": 3, "AI security": 3, "LLM security": 3,
            "agent security": 3, "secure MCP": 3
        },
        "color": "crimson"
    },
    "AI-Assisted Software Engineering": {
        "keywords": {
            "AI-assisted coding": 3, "AI pair programming": 3, "Copilot": 2,
            "Cursor": 2, "Claude Code": 2, "Cody": 2, "Codeium": 2,
            "Tabnine": 2, "Continue": 2, "Aider": 2, "Devin": 3,
            "agentic coding": 3, "AI code review": 3, "AI refactoring": 3,
            "vibe coding": 3, "prompt-driven development": 3,
            "spec-driven development": 3, "AI-generated tests": 3,
            "AI test generation": 3, "AI-assisted debugging": 3,
            "code generation": 2, "code completion": 2,
            "developer productivity": 2, "developer experience": 2,
            "DX": 1, "engineering velocity": 2,
            "AI-native SDLC": 3, "agentic SDLC": 3,
            "autonomous software engineer": 3, "SWE-Agent": 3,
            "SWE-bench": 2, "humanEval": 2,
            "inner loop": 2, "outer loop": 2,
            "test-time compute": 2, "AI in IDE": 3, "AI for testing": 3,
            "AI for code migration": 3, "code modernization": 2,
            "legacy modernization": 2, "documentation generation": 2,
            "AI documentation": 2, "lint": 1, "static analysis": 1
        },
        "color": "teal"
    }
}

THEME_COLORS = {
    "Agentic Systems & DevTools": "#1f77b4",          # Blues
    "Frontier Models & Benchmarks": "#9467bd",         # Purples
    "Hardware, Compute & LLMOps": "#ff7f0e",           # Oranges
    "Enterprise Strategy & ROI": "#2ca02c",            # Greens
    "Governance, Safety & Policy": "#d62728",          # Reds
    "AI Security & Trust": "#8c564b",                  # Crimsons
    "AI-Assisted Software Engineering": "#17becf",     # Teals
}

# Theme order for display
THEME_ORDER = list(THEMES.keys())

