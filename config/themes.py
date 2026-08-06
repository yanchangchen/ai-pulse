# Theme definitions for AI Pulse
# Keywords are weighted dicts: higher weight = stronger signal for that theme.

THEMES = {
    "Agentic Systems & DevTools": {
        "keywords": {
            # Generic "agent" terms are weighted LOWER than specialist
            # themes' coding-/security-/hardware-specific terms so that an
            # article about, say, "agentic coding in the IDE" still routes
            # to AI-Assisted Software Engineering rather than landing here
            # by default.
            "RAG": 3, "agents": 2, "agentic": 2, "LangChain": 3, "LangGraph": 3,
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
            # Bare model names (Claude, GPT, Gemini, Llama, Mistral) are
            # weighted LOW (1) because they fire on virtually every AI
            # article, not just frontier-model news.  The truly specific
            # signals — benchmark names, model-release phrasings,
            # architecture terms — are weighted 3 and dominate the score.
            "model release": 3, "benchmark": 3,
            "GPT": 1, "Claude": 1, "Gemini": 1, "Llama": 1, "Mistral": 1,
            "multimodal": 2, "reasoning": 2,
            "context window": 3, "training": 1, "weights": 2,
            "open source model": 3, "new model": 3, "frontier": 3,
            "MoE": 3, "speculative decoding": 3, "KV cache": 3,
            "MMLU": 3, "GPQA": 3, "SWE-bench": 3, "ARC-AGI": 3,
            "HumanEval": 3, "LiveBench": 3, "Chatbot Arena": 3,
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
            # Specialist coding-agent signals are weighted 3 so that an
            # article mentioning, say, "agentic coding" or "Cursor" strongly
            # prefers this theme over the more generic Agentic bucket.
            "AI-assisted coding": 3, "AI pair programming": 3,
            "Copilot": 3, "Cursor": 3, "Claude Code": 3,
            "Cody": 2, "Codeium": 2, "Tabnine": 2, "Continue": 2,
            "Aider": 3, "Devin": 3,
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

# ---------------------------------------------------------------------------
# Runtime & Persistent Keyword Management (In-App Editing Support)
# ---------------------------------------------------------------------------
import json
from pathlib import Path

CUSTOM_KEYWORDS_FILE = Path(__file__).parent / "custom_keywords.json"


def load_custom_keywords() -> Dict[str, Dict[str, int]]:
    """Load custom keywords overlay from JSON if it exists."""
    if CUSTOM_KEYWORDS_FILE.exists():
        try:
            with open(CUSTOM_KEYWORDS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_custom_keywords(custom_data: Dict[str, Dict[str, int]]) -> None:
    """Save custom keywords overlay to JSON."""
    with open(CUSTOM_KEYWORDS_FILE, "w", encoding="utf-8") as f:
        json.dump(custom_data, f, indent=2, ensure_ascii=False)


def add_keywords_to_theme(theme_name: str, new_keywords: Dict[str, int]) -> bool:
    """Add or update keywords for a theme at runtime and persist to custom_keywords.json."""
    if theme_name not in THEMES:
        return False
    THEMES[theme_name]["keywords"].update(new_keywords)
    custom = load_custom_keywords()
    if theme_name not in custom:
        custom[theme_name] = {}
    custom[theme_name].update(new_keywords)
    save_custom_keywords(custom)
    return True


def remove_keyword_from_theme(theme_name: str, keyword: str) -> bool:
    """Remove a keyword from a theme at runtime and update custom_keywords.json."""
    if theme_name not in THEMES or keyword not in THEMES[theme_name]["keywords"]:
        return False
    del THEMES[theme_name]["keywords"][keyword]
    custom = load_custom_keywords()
    if theme_name in custom and keyword in custom[theme_name]:
        del custom[theme_name][keyword]
        save_custom_keywords(custom)
    return True


# Auto-apply any stored custom keywords overlay on import
_custom = load_custom_keywords()
for _t_name, _kw_map in _custom.items():
    if _t_name in THEMES:
        THEMES[_t_name]["keywords"].update(_kw_map)


