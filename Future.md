# Sage RAG Architecture & Long-Term Roadmap

This document outlines the architectural roadmap for evolving **Sage** from its current character-budgeted prompt-injection mechanism into a production-grade, multi-stage **Retrieval-Augmented Generation (RAG)** intelligence system.

---

## 1. Executive Summary & Vision

Sage is designed to be the conversational analyst embedded in AI Pulse, answering questions about long-term AI trends, company moves, architectural shifts, and chronology.

As the memory archive expands across months and years of daily news runs, traditional context-window stuffing degrades due to:
1. **Context Window Saturation**: Even large-context LLMs suffer from "lost in the middle" degradation when processing 50+ runs.
2. **Coarse-Grained Retrieval**: Answering granular queries (e.g., *"What did Simon Willison say about GPT-6 Astra adapters?"*) requires article-level citations rather than high-level run summaries.
3. **Temporal Incoherence**: Standard semantic vector search ignores publication chronology, confusing the order in which breakthroughs occurred.

The long-term vision transforms Sage into a **Time-Aware, Multi-Stage Hierarchical RAG Agent**.

---

## 2. Core Architecture Blueprint

```
                      ┌──────────────────────────────┐
                      │          User Query          │
                      └──────────────┬───────────────┘
                                     │
                                     ▼
                ┌──────────────────────────────────────────┐
                │   Query Understanding & Routing Engine   │
                │  - Temporal entity extraction (dates)    │
                │  - Theme & keyword routing               │
                │  - Question intent (Timeline vs Deep)    │
                └────────────────────┬─────────────────────┘
                                     │
                    ┌────────────────┴────────────────┐
                    │                                 │
                    ▼                                 ▼
      ┌───────────────────────────┐     ┌───────────────────────────┐
      │   Dense Vector Search     │     │   Sparse Keyword (BM25)   │
      │  (pgvector / Gemini Embed)│     │   (Postgres tsvector/FTS) │
      └─────────────┬─────────────┘     └─────────────┬─────────────┘
                    │                                 │
                    └────────────────┬────────────────┘
                                     │
                                     ▼
                    ┌─────────────────────────────────┐
                    │      Reciprocal Rank Fusion     │
                    │   & Temporal Decay Re-ranking   │
                    └────────────────┬────────────────┘
                                     │
                                     ▼
                    ┌─────────────────────────────────┐
                    │     Hierarchical Memory Assembly│
                    │  - Tier 3: Monthly Rollup       │
                    │  - Tier 2: Theme Summaries      │
                    │  - Tier 1: Cited Articles       │
                    └────────────────┬────────────────┘
                                     │
                                     ▼
                    ┌─────────────────────────────────┐
                    │          Sage LLM Engine        │
                    │  - Chronological account        │
                    │  - Deep hyperlinks to sources   │
                    │  - Analyst synthesis            │
                    └─────────────────────────────────┘
```

---

## 3. Key Architectural Pillars

### Pillar A: Two-Stage Hybrid Retrieval (Dense + Sparse)
* **Dense Retrieval**:
  - Vector embeddings generated via Google Gemini `text-embedding-004` (768 dimensions) or local Ollama embeddings (`nomic-embed-text`).
  - Stored in Supabase using the `pgvector` extension with HNSW indexes.
  - Generates semantic matches for conceptual questions (*"reasoning token overhead"*, *"chip export restrictions"*).
* **Sparse Retrieval**:
  - PostgreSQL full-text search (`tsvector` and `tsquery`) with English dictionary stemmer.
  - Matches exact entity names, model releases, and numbers (*"Claude 3.7 Sonnet"*, *"Llama 3.3 70B"*, *"SWE-bench 87.6%"*).
* **Hybrid Fusion**:
  - Reciprocal Rank Fusion (RRF) combines scores from dense and sparse retrieval to produce the final candidate set.

---

### Pillar B: Hierarchical Memory Architecture

Rather than treating every record at the same level of granularity, store and index intelligence across three distinct tiers:

| Tier | Granularity | Storage Table | Purpose |
|---|---|---|---|
| **Tier 1: Article Evidence** | Granular quotes, titles, original URLs | `articles` & `article_embeddings` | Answering specific questions, providing primary source hyperlinks |
| **Tier 2: Run Briefs** | Daily theme summaries (What happened, Why it matters, Watch) | `theme_summaries` | Tracking weekly progression and thematic movements |
| **Tier 3: Monthly Syntheses** | Macro monthly trends and strategic landscape rollups | `monthly_syntheses` | Answering multi-month evolution questions without token blowout |

When a query spans multiple quarters, Sage retrieves the Tier 3 rollups for macro context, and drills down to Tier 1 articles only for the specific milestones cited.

---

### Pillar C: Temporal-Aware RAG

News intelligence is inherently chronological. Standard RAG produces hallucinations if it confuses which event preceded another.
1. **Query Date Parsing**:
   - Extract relative and absolute date expressions (e.g. *"last week"*, *"August 2026"*, *"between June and September"*).
   - Constrain retrieval to the resolved temporal bounding box before vector distance calculation.
2. **First-Appearance Tracking**:
   - Maintain a dedicated `theme_first_seen` index to answer queries like *"When was this concept first mentioned?"* with single-millisecond index lookups.
3. **Time-Decayed Relevance Scoring**:
   - Score formula: $S(d, q) = \lambda \cdot \text{Similarity}(d, q) + (1 - \lambda) \cdot e^{-\gamma (T_{current} - T_{run})}$.
   - Balances relevance against freshness unless historical comparison is explicitly requested.

---

### Pillar D: Deep Grounding & Interactive Citations

1. **Direct Article Links**:
   - Sage responses should cite original articles using Markdown hyperlinks retrieved from the `articles.link` column:
     > *"On September 12, OpenAI launched o1 [OpenAI Announcement](https://openai.com/...)."*
2. **Evidence Popovers**:
   - In the Streamlit UI, cited runs and articles should be inspectable in an accordion or side-drawer showing the exact article snippet that grounded each claim.

---

### Pillar E: Multi-Agent Synthesis Workflow

For complex research questions, expand Sage from a single LLM call into a 3-agent pipeline:
1. **Sage Scout (Retriever Agent)**: Analyzes the question, generates 2–3 sub-queries, executes vector + date queries, and compiles the relevant context documents.
2. **Sage Fact-Checker (Faithfulness Evaluator)**: Validates that each statement in the proposed response is explicitly supported by retrieved context.
3. **Sage Analyst (Synthesis Agent)**: Drafts the final prose using the strict AI Pulse voice (Factual chronological account + "My read on this").

---

## 4. Supabase Database Schema Additions for Future RAG

```sql
-- Enable pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- Article chunks & embeddings for granular retrieval
CREATE TABLE IF NOT EXISTS article_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    article_id UUID REFERENCES articles(id) ON DELETE CASCADE,
    run_date DATE NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector(768),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_article_embeddings_hnsw 
ON article_embeddings USING hnsw (embedding vector_cosine_ops);

-- Monthly macro rollups
CREATE TABLE IF NOT EXISTS monthly_syntheses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    year_month VARCHAR(7) NOT NULL UNIQUE, -- e.g. '2026-09'
    theme_name VARCHAR(255) NOT NULL,
    macro_summary TEXT NOT NULL,
    key_milestones JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Persistent Sage conversation threads
CREATE TABLE IF NOT EXISTS sage_conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(255) NOT NULL,
    theme_filter VARCHAR(255),
    period_info VARCHAR(100),
    messages JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

---

## 5. Phased Implementation Roadmap

- [ ] **Phase 1 (Completed)**: Fix query order, period selector (Last 7d, 30d, Sep 2026, All Time, Custom), theme filtering in chat, conversation persistence (JSON/Supabase sync), Markdown export.
- [ ] **Phase 2**: Add `articles` table retrieval to Sage context assembly (giving Sage access to primary sources & links).
- [ ] **Phase 3**: Vector embeddings generation pipeline in `bg_refresher.py` using Gemini / Ollama embeddings + pgvector table.
- [ ] **Phase 4**: Hybrid BM25 + Vector search with Reciprocal Rank Fusion.
- [ ] **Phase 5**: Automated monthly rollup jobs and multi-agent Scout/Analyst verification loop.
