# AI Pulse Engineering Blog

Lessons learned building a multi-model AI news intelligence dashboard — one that fetches, classifies, summarises, and persists AI industry developments across 30+ sources with longitudinal memory.

---

## 2026-09-10 — The day classification went fully deterministic

**Problem**: We had a 4-pass waterfall classifier (Keywords → TF-IDF → LLM → Heuristic). The LLM pass felt like insurance — but insurance costs tokens, latency, and failure modes.

**What happened**: A live run classified **243 articles with zero LLM calls**. Keywords caught 73%, TF-IDF caught 22%, heuristic caught 5%. The LLM gate caught 0%.

**Learning**: After enough keyword refinement, non-LLM classification is not just viable — it's the best path. We added:
- A `deterministic` mode that skips the LLM gate entirely
- Gate stats persistence so the system knows *when* to suggest switching
- Heuristic keyword auto-improvement: after each run, terms from LLM/heuristic articles are extracted and auto-applied if they appear in 3+ articles

**Result**: The classifier can now run fully offline, zero-cost, and improve its own keywords run-over-run. The LLM gate is still available in `hybrid` mode for the rare edge cases that need it.

---

## 2026-09-07 to 2026-09-10 — Gemini reliability and why routing matters

**Problem**: Gemini was our primary model for summarisation. In production it kept returning "unable to summarise" errors. Users had to manually trigger regeneration.

**What happened**: We discovered the `google-genai` SDK had Automatic Function Calling enabled by default (useless noise), no request timeout (hanging calls blocked the fallback chain), and the gateway's deterministic fallback produced garbage output ("Unable to generate summary" for every theme).

**Learning**: Three fixes, three lessons:
1. **AFC must be disabled** if you're not using tools — it produces noisy SDK warnings about preferring `Chat.send_message` over `generate_content`
2. **Always set per-provider timeouts** — Gemini's default is effectively infinite. We added 30s for Gemini, 60s for Ollama, both configurable via env vars
3. **Routing order matters more than you think** — we swapped summarisation routing to Ollama-primary → Gemini-fallback because Ollama has been more reliable for our specific workload. Gemini stays primary for lightweight tasks (categorisation, extraction) where it excels

**Also fixed**: The gateway's deterministic fallback now delegates to the proper `non_llm_summariser.py` (LexRank + Luhn) instead of a crude text summariser that operated on the raw prompt string. The summariser layer has a belt-and-sunders check that re-routes any gateway deterministic result through the proper engine.

---

## 2026-09-02 — Modular AI gateway: one interface, five models

**What we built**: A task-typed routing layer that maps `categorise / extract / summarise / synthesise / project` to specific models with fallback chains, health tracking, context-fit checks, exponential-backoff retries, JSON-schema validation, and provenance tracking on every result.

**Key design decision**: The gateway doesn't just route to the first available model. It checks context window fit (input must fit 60% of the model's window), tracks consecutive failures (3 → degraded, 5 → unavailable), and falls back through an ordered chain. If all LLMs fail, a deterministic fallback kicks in.

**Provider migration**: We migrated Gemini from the deprecated `google-generativeai` SDK (gRPC-aio transport) to `google-genai` (httpx/REST). The gRPC transport caused `InterceptedUnaryUnaryCall` AttributeError warnings during GC when `asyncio.run()` destroyed event loops between calls. The REST transport eliminates this entirely.

---

## 2026-08-16 to 2026-08-26 — Sage, provenance, and the quota probe

**Sage agent**: Built a conversational agent grounded in the Memory Wiki archive. Key insight — the LLM needs a time-windowed, character-budgeted context string, not raw data. We implemented relevance scoring (token overlap with the user's question) and chronological first-appearance annotations so Sage can say "I first saw this trend on [date]."

**Automatic Gemini fallback**: When Ollama quota is exceeded, Sage seamlessly falls back to Gemini. No user-visible interruption.

**Provenance chips**: Every summary card now shows which model produced it (Google blue / Ollama purple / Non-LLM amber / Error red). This surfaced a key insight: ~15% of historical summaries were produced by the non-LLM fallback during quota outages. Without the chips, users would never know.

**Quota probe fix**: Ollama's quota recovery was previously detected on the next refresh cycle (up to 12 hours delay). We now probe `/api/tags` *before* the pipeline starts so live LLM synthesis resumes the moment quota recovers.

---

## 2026-08-06 to 2026-08-12 — Non-LLM summarisation and quality evaluation

**The non-LLM breakthrough**: We built a proper extractive summariser (LexRank graph centrality + Luhn keyword-cluster scoring + n-gram keyphrase extraction) that produces 5-section intelligence briefs. It runs in <50ms, costs zero tokens, and is 100% faithful to source articles.

**Key learning**: When LLMs fail (quota, timeout, empty response), the non-LLM engine produces summaries good enough for day-to-day use. The quality difference is in synthesis — the LLM can connect dots across articles; the non-LLM engine can only extract from within articles. But for factual reporting, non-LLM is often better because it can't hallucinate.

**Quality evaluation**: Built a 7-metric evaluation suite (3 LLM-as-judge + 4 deterministic). The deterministic metrics (Grounding, Structural Compliance, Coverage, Temporal Coherence) run in sub-milliseconds and catch real issues. LLM metrics are expensive but essential for Faithfulness and Uniqueness.

**In-app remediation**: Instead of editing Python files, users can now adjust keywords, temperature, and token budgets through the UI. 1-click apply buttons push keyword changes to `custom_keywords.json` that persist across restarts.

---

## 2026-07-06 to 2026-08-06 — The UX redesign and page rationalisation

**What changed**: Comprehensive UX redesign with central design tokens, adaptive light/dark CSS, multi-theme CSV export, and renumbered sequential pages.

**Page pruning**: Dropped the Emerging Trends page, consolidated analytics into Keyword Analysis. Lesson: fewer pages with deeper data is better than many pages with shallow data.

**Sage agent**: Added the conversational Memory Wiki interface. Initial design had Sage answering from the full archive — but the LLM context window is finite. We implemented character-budgeted context building with relevance scoring so Sage always sees the most relevant historical summaries.

---

## 2026-06-03 to 2026-07-06 — Supabase, deduplication, and the first quality evaluation

**Supabase integration**: Added cloud persistence with 4 tables (trend_runs, theme_summaries, articles, sync_metadata). Key decision: use `(content_hash, theme_name)` UPSERT constraints so the same article isn't duplicated across runs.

**Historical backfill**: The first Supabase load backfills all `history.json` runs into the cloud tables. This took 30+ minutes for 100+ runs but only happens once.

**Weekly quality evaluation**: Built an automated pipeline that re-classifies a sample of articles, fact-checks summary claims against source articles, and measures text overlap across themes. This caught real issues — some summaries were repeating the same claims week-over-week without updating.

---

## 2026-05-16 to 2026-06-03 — Memory wiki and the birth of longitudinal analysis

**The big idea**: What if the system remembers what happened last time? Every run's summaries are appended to `memory.md` (human-readable wiki) and `history.json` (machine-readable). The next run injects the last 2 runs' summaries into the LLM prompt so the model reports on *evolutions*, not static snapshots.

**Background ingestion**: First load runs the pipeline in a daemon thread so the UI stays responsive. Subsequent loads restore from cache and only refresh if older than 12 hours.

**Caching strategy**: Content-based SHA-256 hashing prevents redundant LLM calls when fetched articles are unchanged. This saves ~40% of LLM costs on days with no new content.

---

## 2026-03-03 — Day 1: AI Pulse begins

**What we built**: A Streamlit dashboard that fetches AI news from RSS feeds, classifies articles into themes, and produces LLM summaries.

**Initial challenges**: Feedparser 6.x broke with socket timeouts (fixed with `socket.setdefaulttimeout`). LLMs occasionally returned empty responses (fixed with retry + temperature nudge). The classifier over-fired on broad terms like "agent" (fixed with weighted keywords and TF-IDF fallback).

**Key early learning**: RSS feeds are unreliable. Some sources block scraping, some return 429s, some change their feed format without notice. We built a resilient fetcher with 3 retries, User-Agent rotation, and source pruning for feeds that consistently fail.

---

*This blog documents the engineering decisions, failures, and fixes that shaped AI Pulse. Entries are based on real production issues and the solutions that worked.*
