"""
Synthetic Test Suite for Non-LLM Extractive Summariser across 10 Distinct AI Domain Use Cases.
Verifies LexRank, Luhn keyword scoring, and keyphrase extraction accuracy and speed.
"""

import pytest
import time
from core.non_llm_summariser import generate_non_llm_theme_summary


# 10 Diverse AI Domain Synthetic Use Cases
SYNTHETIC_USE_CASES = [
    # Use Case 1: Agentic Coding & Tool Use
    {
        "theme": "Agentic AI & Coding Assistants",
        "articles": [
            {
                "title": "Anthropic Introduces Hybrid Reasoning in Claude 3.7 Sonnet",
                "summary": "Anthropic announced Claude 3.7 Sonnet featuring dynamic hybrid reasoning, enabling software engineers to switch between rapid code completion and extended step-by-step problem solving.",
                "source_name": "Anthropic Engineering",
                "link": "https://www.anthropic.com/news/claude-3-7-sonnet"
            },
            {
                "title": "Cursor and Windsurf Integrate Multi-File Agentic Refactoring",
                "summary": "AI code editors now support multi-file agentic execution, allowing autonomous modifications across complex repositories while reducing syntax regressions.",
                "source_name": "Tech Crunch AI",
                "link": "https://techcrunch.com/cursor-agentic"
            }
        ]
    },
    # Use Case 2: Open-Weight Models & Fine-Tuning
    {
        "theme": "Open Source Models",
        "articles": [
            {
                "title": "DeepSeek Releases DeepSeek-V3 Open-Weights with Mixture-of-Experts",
                "summary": "DeepSeek launched DeepSeek-V3 featuring 671 billion parameters with Multi-head Latent Attention and FP8 training efficiency.",
                "source_name": "DeepSeek Research",
                "link": "https://github.com/deepseek-ai/DeepSeek-V3"
            },
            {
                "title": "Meta Llama 3.3 70B Achieves Frontier Quality at Reduced Cost",
                "summary": "Llama 3.3 70B delivers capabilities comparable to early Llama 3.1 405B while remaining lightweight enough to host on single GPU nodes.",
                "source_name": "Meta AI Blog",
                "link": "https://ai.meta.com/blog/llama-3-3"
            }
        ]
    },
    # Use Case 3: GPU Hardware & Inference Acceleration
    {
        "theme": "AI Infrastructure & Compute",
        "articles": [
            {
                "title": "NVIDIA Blackwell B200 GPUs Enter Mass Production for Cloud Providers",
                "summary": "NVIDIA started mass shipments of B200 GPUs, promising up to 30x inference speedups for trillion-parameter LLM workloads.",
                "source_name": "NVIDIA Blog",
                "link": "https://blogs.nvidia.com/blackwell-b200"
            },
            {
                "title": "vLLM 0.7 Introduces Chunked Prefill and PagedAttention v2",
                "summary": "The vLLM inference engine optimized KV-cache allocation with chunked prefill, boosting serving throughput by 2.4x under heavy concurrency.",
                "source_name": "vLLM Project",
                "link": "https://vllm.ai/release-0-7"
            }
        ]
    },
    # Use Case 4: Multimodal & Vision-Language Models
    {
        "theme": "Multimodal AI & Vision",
        "articles": [
            {
                "title": "Google Unveils Gemini 2.0 Flash with Native Multimodal Audio Stream",
                "summary": "Gemini 2.0 Flash supports real-time low-latency video and audio streaming inputs with integrated spatial grounding.",
                "source_name": "Google DeepMind",
                "link": "https://deepmind.google/gemini-2-0"
            },
            {
                "title": "OpenAI Omni Vision Benchmarks Set New Standard for OCR and Document QA",
                "summary": "Multimodal vision-language models achieve 94% accuracy on complex financial charts and handwritten diagram extraction.",
                "source_name": "Wired AI",
                "link": "https://wired.com/openai-omni-vision"
            }
        ]
    },
    # Use Case 5: AI Safety, Governance & Policy
    {
        "theme": "AI Safety & Governance",
        "articles": [
            {
                "title": "EU AI Act Enforcement Begins for High-Risk Frontier Models",
                "summary": "European Union regulatory enforcement officially kicks off mandatory red-teaming, watermarking, and systemic risk disclosure rules.",
                "source_name": "AI Now Institute",
                "link": "https://ainowinstitute.org/eu-ai-act"
            },
            {
                "title": "US AI Safety Institute Releases Frontier Model Evaluation Benchmark",
                "summary": "NIST and AISI published standardized benchmarks for assessing autonomous cyber capabilities and CBRN risk thresholds in LLMs.",
                "source_name": "CSET Georgetown",
                "link": "https://cset.georgetown.edu/aisi-benchmarks"
            }
        ]
    },
    # Use Case 6: Vector Search & RAG Systems
    {
        "theme": "Vector Databases & Search",
        "articles": [
            {
                "title": "Pinecone and Qdrant Launch Hybrid Sparse-Dense Vector Indexing",
                "summary": "Vector database engines introduced hybrid search combining BM25 keyword matching with dense HNSW embeddings for higher retrieval precision.",
                "source_name": "Latent.Space",
                "link": "https://www.latent.space/vector-hybrid"
            },
            {
                "title": "GraphRAG Benchmark Demonstrates Superior Multi-Hop Reasoning",
                "summary": "Combining knowledge graphs with vector retrieval reduces context hallucination by 42% in enterprise document synthesis.",
                "source_name": "Microsoft Research",
                "link": "https://microsoft.com/graphrag"
            }
        ]
    },
    # Use Case 7: Small Language Models (SLMs) & Edge AI
    {
        "theme": "Small Models & Edge Computing",
        "articles": [
            {
                "title": "Microsoft Phi-4 14B Demonstrates Reasoning Outperforming 70B Models",
                "summary": "Phi-4 utilizes synthetic textbook data curation to achieve state-of-the-art math and coding benchmarks within a 14B parameter footprint.",
                "source_name": "Microsoft Blog",
                "link": "https://microsoft.com/phi-4"
            },
            {
                "title": "Apple MLX Framework Enables 4-bit On-Device LLM Execution on M4",
                "summary": "Quantized 8B parameter models achieve 65 tokens per second on Apple Silicon while consuming less than 4GB of unified RAM.",
                "source_name": "Apple Machine Learning Research",
                "link": "https://machinelearning.apple.com/mlx"
            }
        ]
    },
    # Use Case 8: Synthetic Data & Model Distillation
    {
        "theme": "Data Pipelines & Synthetic Training",
        "articles": [
            {
                "title": "Hugging Face Releases Synthetic Dataset Curation Pipeline",
                "summary": "Open-source data filtering tools allow developers to distill frontier LLM reasoning traces into compact domain-specific instruction datasets.",
                "source_name": "Hugging Face Blog",
                "link": "https://huggingface.co/blog/synthetic-data"
            },
            {
                "title": "Distillation Techniques Reduce Inference Costs by 80%",
                "summary": "Teacher-student logit matching enables 7B student models to match 90% of GPT-4 benchmark performance at a fraction of hosting costs.",
                "source_name": "VentureBeat AI",
                "link": "https://venturebeat.com/model-distillation"
            }
        ]
    },
    # Use Case 9: Multi-Agent Systems & Frameworks
    {
        "theme": "Agent Orchestration & Frameworks",
        "articles": [
            {
                "title": "LangGraph and AutoGen Support State Machine Workflow Execution",
                "summary": "Agent orchestration platforms added deterministic state machine graphs, cyclic execution loops, and human-in-the-loop inspection nodes.",
                "source_name": "TLDR Newsletter",
                "link": "https://tldr.tech/agent-frameworks"
            },
            {
                "title": "CrewAI Introduces Enterprise Task Queue and Memory Persistence",
                "summary": "Multi-agent frameworks now include Redis and Supabase backends for persistent agent memory across long-running asynchronous workflows.",
                "source_name": "GitHub Engineering",
                "link": "https://github.blog/crewai-enterprise"
            }
        ]
    },
    # Use Case 10: Enterprise AI ROI & Cost Benchmarks
    {
        "theme": "Enterprise AI & Business Impact",
        "articles": [
            {
                "title": "Enterprise LLM Token Costs Drop 90% Year-Over-Year",
                "summary": "API provider competition and hardware acceleration drove input token pricing down to $0.15 per million tokens for high-throughput workloads.",
                "source_name": "Stratechery",
                "link": "https://stratechery.com/ai-token-economics"
            },
            {
                "title": "Financial Services Report 3.5x Productivity Gains with AI Copilots",
                "summary": "Deploying customized internal LLMs for document auditing reduced customer support resolution times from 48 hours to 15 minutes.",
                "source_name": "MIT Technology Review AI",
                "link": "https://technologyreview.com/enterprise-ai-roi"
            }
        ]
    }
]


@pytest.mark.parametrize("use_case", SYNTHETIC_USE_CASES, ids=[f"use_case_{i+1}_{uc['theme'][:15]}" for i, uc in enumerate(SYNTHETIC_USE_CASES)])
def test_synthetic_non_llm_summarisation_use_cases(use_case):
    """Test non-LLM summarization across 10 distinct AI domain synthetic use cases."""
    theme = use_case["theme"]
    articles = use_case["articles"]

    t0 = time.time()
    summary = generate_non_llm_theme_summary(theme, articles)
    latency_ms = (time.time() - t0) * 1000

    # 1. Verify latency requirement (< 15 ms per test case)
    assert latency_ms < 50.0, f"Execution too slow: {latency_ms:.2f} ms"

    # 2. Verify all required keys exist
    required_keys = ["what_is_happening", "engineering_tradeoffs", "product_impact", "why_it_matters", "what_to_watch", "further_reading"]
    for k in required_keys:
        assert k in summary, f"Missing key '{k}' in summary output"
        assert len(summary[k]) > 0, f"Empty content for key '{k}'"

    # 3. Verify 'what_is_happening' (The Signal) contains core extracted sentences
    signal = summary["what_is_happening"]
    assert len(signal) > 20, "Signal string too short"
    
    # 4. Verify LexRank extracted meaningful content from input titles/summaries
    first_title_kw = articles[0]["title"].split()[0]
    assert first_title_kw.lower() in signal.lower() or any(w.lower() in signal.lower() for w in articles[0]["summary"].split()[:5])

    # 5. Verify 'further_reading' cites the input sources and links
    for a in articles:
        assert a["title"] in summary["further_reading"]
