# Appendix D — Papers Reading List
*Part of AI & Engineering Intelligence Watchlist v3.0*
*Last updated: 2026-05-30 | Review: Add 2–3 papers per weekly run*

---

## How to Use This List

- **Foundations** = papers you should have read once and understand conceptually
- **Production Reference** = papers directly applicable to engineering decisions
- **Recent Signals** = papers from the last 6 months worth tracking
- **Difficulty** = 🟢 Accessible to intermediate ML practitioner / 🟡 Requires maths / 🔴 Requires deep ML background
- Use arXiv Sanity (arxiv-sanity-lite.com), papers.huggingface.co, and paperswithcode.com/trending as weekly discovery sources

---

## D1. Foundational — Architecture

*These are the canonical papers. Read once; revisit when a new paper builds on them.*

| Paper | Authors | Year | Link | Why It Matters | Difficulty |
|-------|---------|------|------|----------------|------------|
| **Attention Is All You Need** | Vaswani et al. | 2017 | arxiv.org/abs/1706.03762 | Original Transformer architecture — everything builds on this | 🟡 |
| **BERT: Pre-training of Deep Bidirectional Transformers** | Devlin et al. | 2018 | arxiv.org/abs/1810.04805 | Foundational encoder model; transfer learning paradigm | 🟡 |
| **Language Models are Few-Shot Learners (GPT-3)** | Brown et al. | 2020 | arxiv.org/abs/2005.14165 | Established few-shot prompting and scale hypothesis | 🟢 |
| **Training Language Models to Follow Instructions (InstructGPT)** | Ouyang et al. | 2022 | arxiv.org/abs/2203.02155 | RLHF origin paper; why modern LLMs are instruction-following | 🟡 |
| **Constitutional AI: Harmlessness from AI Feedback** | Bai et al. (Anthropic) | 2022 | arxiv.org/abs/2212.08073 | Anthropic's alignment approach; foundation for Claude | 🟢 |
| **Scaling Laws for Neural Language Models** | Kaplan et al. | 2020 | arxiv.org/abs/2001.08361 | Why bigger = better (with compute); compute-optimal training | 🟡 |
| **Chinchilla: Training Compute-Optimal LLMs** | Hoffmann et al. | 2022 | arxiv.org/abs/2203.15556 | Corrects GPT-3 scaling; optimal data/compute ratio | 🟡 |
| **Mixtral of Experts** | Mistral AI | 2024 | arxiv.org/abs/2401.04088 | MoE architecture that powers most frontier open models | 🟡 |
| **LLaMA: Open and Efficient Foundation Language Models** | Touvron et al. | 2023 | arxiv.org/abs/2302.13971 | Foundation of the open-weight model ecosystem | 🟢 |
| **Flash Attention** | Dao et al. | 2022 | arxiv.org/abs/2205.14135 | IO-aware attention — standard in all modern training | 🔴 |

---

## D2. Foundational — Reasoning & Alignment

| Paper | Authors | Year | Link | Why It Matters | Difficulty |
|-------|---------|------|------|----------------|------------|
| **Chain-of-Thought Prompting (CoT)** | Wei et al. | 2022 | arxiv.org/abs/2201.11903 | Why step-by-step prompting works; basis for o1/o3 | 🟢 |
| **Self-Consistency Improves CoT Reasoning** | Wang et al. | 2022 | arxiv.org/abs/2203.11171 | Sample multiple paths, majority vote — simple but powerful | 🟢 |
| **Tree of Thoughts** | Yao et al. | 2023 | arxiv.org/abs/2305.10601 | Search-based reasoning; LLM as deliberate problem solver | 🟢 |
| **Reinforcement Learning from Human Feedback (RLHF)** | Christiano et al. | 2017 | arxiv.org/abs/1706.03741 | Original RLHF paper — how models learn from human preference | 🟡 |
| **Direct Preference Optimization (DPO)** | Rafailov et al. | 2023 | arxiv.org/abs/2305.18290 | Simpler alternative to RLHF; widely adopted | 🟡 |
| **GRPO: Group Relative Policy Optimization** | DeepSeek | 2024 | arxiv.org/abs/2402.03300 | Memory-efficient RLHF; powers DeepSeek-R1 | 🔴 |
| **Reward Model Ensembles Help Mitigate Overoptimization** | Coste et al. | 2023 | arxiv.org/abs/2310.02743 | Why RLHF fails at scale; reward hacking | 🟡 |

---

## D3. Foundational — Agents & Tool Use

| Paper | Authors | Year | Link | Why It Matters | Difficulty |
|-------|---------|------|------|----------------|------------|
| **ReAct: Synergizing Reasoning and Acting in LLMs** | Yao et al. | 2022 | arxiv.org/abs/2210.03629 | Foundation of all tool-using agent patterns | 🟢 |
| **Toolformer: LMs Can Teach Themselves to Use Tools** | Schick et al. | 2023 | arxiv.org/abs/2302.04761 | Self-supervised tool use training | 🟡 |
| **HuggingGPT: Solving AI Tasks with ChatGPT** | Shen et al. | 2023 | arxiv.org/abs/2303.17580 | Multi-model orchestration pattern | 🟢 |
| **Gorilla: Large Language Model Connected with APIs** | Patil et al. | 2023 | arxiv.org/abs/2305.15334 | LLMs calling real-world APIs accurately | 🟢 |
| **AgentBench: Evaluating LLMs as Agents** | Liu et al. | 2023 | arxiv.org/abs/2308.03688 | Benchmark for agent capabilities across 8 environments | 🟢 |
| **MetaGPT: Meta Programming for Multi-Agent Collaboration** | Hong et al. | 2023 | arxiv.org/abs/2308.00352 | Role-based multi-agent framework | 🟢 |
| **Voyager: Open-Ended Embodied Agent with LLMs** | Wang et al. | 2023 | arxiv.org/abs/2305.16291 | Lifelong learning agent in Minecraft; memory patterns | 🟢 |
| **Reflexion: Language Agents with Verbal Reinforcement** | Shinn et al. | 2023 | arxiv.org/abs/2303.11366 | Self-critique loop for agents | 🟢 |

---

## D4. Foundational — RAG & Memory

| Paper | Authors | Year | Link | Why It Matters | Difficulty |
|-------|---------|------|------|----------------|------------|
| **Retrieval-Augmented Generation (RAG)** | Lewis et al. | 2020 | arxiv.org/abs/2005.11401 | Original RAG paper | 🟡 |
| **Self-RAG: Learning to Retrieve, Generate, Critique** | Asai et al. | 2023 | arxiv.org/abs/2310.11511 | Adaptive RAG with self-critique | 🟡 |
| **RAPTOR: Recursive Abstractive Processing for Tree RAG** | Sarthi et al. | 2024 | arxiv.org/abs/2401.18059 | Hierarchical RAG for long documents | 🟡 |
| **HippoRAG: Neurobiologically Inspired Long-Term Memory** | Guo et al. | 2024 | arxiv.org/abs/2405.14831 | Graph-based episodic memory for RAG | 🟡 |
| **MemGPT: Towards LLMs as Operating Systems** | Packer et al. | 2023 | arxiv.org/abs/2310.08560 | Hierarchical memory management for long-context agents | 🟢 |
| **Lost in the Middle: How LLMs Use Long Contexts** | Liu et al. | 2023 | arxiv.org/abs/2307.03172 | Why middle-of-context info is forgotten; prompt engineering implications | 🟢 |

---

## D5. Production & Engineering Reference

| Paper | Authors | Year | Link | Why It Matters | Difficulty |
|-------|---------|------|------|----------------|------------|
| **Efficient Memory Management for LLM Serving (PagedAttention)** | Kwon et al. | 2023 | arxiv.org/abs/2309.06180 | Foundation of vLLM; how inference servers manage memory | 🔴 |
| **Speculative Decoding** | Leviathan et al. | 2022 | arxiv.org/abs/2211.17192 | 2–3× inference speedup with a draft model | 🔴 |
| **LoRA: Low-Rank Adaptation of LLMs** | Hu et al. | 2021 | arxiv.org/abs/2106.09685 | Parameter-efficient fine-tuning — standard in production | 🟡 |
| **QLoRA: Efficient Finetuning of Quantized LLMs** | Dettmers et al. | 2023 | arxiv.org/abs/2305.14314 | Fine-tune 65B models on consumer GPU | 🟡 |
| **GPTQ: Accurate Post-Training Quantization** | Frantar et al. | 2022 | arxiv.org/abs/2210.17323 | 4-bit quantization for inference | 🔴 |
| **Judging LLM-as-a-Judge with MT-Bench** | Zheng et al. | 2023 | arxiv.org/abs/2306.05685 | LLM evaluation with LLM judges; bias analysis | 🟢 |
| **Large Language Models as Optimizers (OPRO)** | Yang et al. | 2023 | arxiv.org/abs/2309.03409 | Automatic prompt optimization | 🟢 |

---

## D6. Safety, Evaluation & Governance

| Paper | Authors | Year | Link | Why It Matters | Difficulty |
|-------|---------|------|------|----------------|------------|
| **Measuring Massive Multitask Language Understanding (MMLU)** | Hendrycks et al. | 2020 | arxiv.org/abs/2009.03300 | Know this to understand why MMLU is saturated and unreliable | 🟢 |
| **GPQA: A Graduate-Level Google-Proof Q&A Benchmark** | Rein et al. | 2023 | arxiv.org/abs/2311.12022 | Why GPQA is the trusted hard benchmark | 🟢 |
| **Holistic Evaluation of Language Models (HELM)** | Liang et al. | 2022 | arxiv.org/abs/2211.09110 | Multi-dimensional evaluation framework | 🟢 |
| **Sleeper Agents: Training Deceptive LLMs** | Hubinger et al. | 2024 | arxiv.org/abs/2401.05566 | Models can hide unsafe behavior during training; alignment risk | 🟢 |
| **Prompt Injection Attacks Against LLM-Integrated Applications** | Greshake et al. | 2023 | arxiv.org/abs/2302.12173 | Foundational security paper for agentic systems | 🟢 |
| **Not What You've Signed Up For: Compromising Real-World LLM-Integrated Applications** | Greshake et al. | 2023 | arxiv.org/abs/2302.12173 | Indirect prompt injection in deployed systems | 🟢 |

---

## D7. Recent Signals — Track These (2025–2026)
*Add 2–3 new entries per weekly run from arXiv, papers.huggingface.co, paperswithcode.com/trending*

| Paper | Authors | Date | Link | Why It Matters | Status |
|-------|---------|------|------|----------------|--------|
| **Autogenesis: Cross-Entity Agent Version Drift** | [arXiv] | May 2026 | arxiv.org/... | Protocol version drift tracking gap in multi-agent systems | 👀 Watch |
| [Add each run] | | | | | |

---

## D8. Reading Strategy by Role

**For AI Platform Engineering interviews:**
Priority order: D3 (Agents) → D5 (Production) → D1 (Architecture) → D4 (RAG)

**For Product Management of AI products:**
Priority order: D6 (Safety/Evaluation) → D3 (Agents conceptual) → D4 (RAG conceptual) → D7 (Recent signals)

**For understanding model training deeply:**
Priority order: D1 (Architecture) → D2 (Reasoning/Alignment) → D5 (Production) → D2 (GRPO/DPO)

**For agentic system security:**
Priority order: D6 (Prompt injection) → D3 (Agents) → D5 (Evaluation)

---

## D9. Discovery Sources (Check Weekly)

| Source | URL | What to Look For |
|--------|-----|-----------------|
| **arXiv cs.AI / cs.LG** | arxiv.org/list/cs.AI/recent | New agent, alignment, architecture papers |
| **Papers with Code (Trending)** | paperswithcode.com/trending | Papers with public code — more likely production-relevant |
| **Hugging Face Papers** | huggingface.co/papers | Community-curated; daily top papers |
| **Semantic Scholar** | semanticscholar.org | Citation tracking; see what papers cite your key refs |
| **Connected Papers** | connectedpapers.com | Visual paper graph — find related work fast |
| **arXiv Sanity Lite** | arxiv-sanity-lite.com | Personal recommendation based on past reads |

---

## D10. Paper Reading Protocol

For each paper added to this list:

1. **Abstract only (2 min)** — Is this worth more time?
2. **Intro + Conclusion (10 min)** — What problem, what result?
3. **Figures + Tables (10 min)** — What does the benchmark show?
4. **Method section (20 min)** — How did they do it?
5. **Full read (60 min)** — Only for papers directly shaping your work

**Capture format for NotebookLM:**
```
Paper: [Title]
Link: [arXiv URL]
Key claim: [one sentence]
Why it matters for my work: [one sentence]
Status: Read abstract / Read paper / Applied
```
