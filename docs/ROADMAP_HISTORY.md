# Roadmap History — Completed Phases & Decisions (Sage RAG)

Implementation record for work that has already landed, moved out of `Future.md` to keep the
roadmap lean. Entries are preserved verbatim from the roadmap at completion time — they record
*what shipped, how, and the gotchas discovered along the way*, so future phases can build on
them without re-deriving. Pending work lives in [Future.md](../Future.md).

---

## Phase 1 — Sage conversational foundations (Completed)

- [x] **Phase 1 (Completed)**: Fix query order, period selector (Last 7d, 30d, Sep 2026, All Time, Custom), theme filtering in chat, conversation persistence (JSON/Supabase sync), Markdown export.

---

## Phase 0 — Foundations: routing + observability

*(0.1–0.3 done and gate verified 2026-10; only 0.4 — Docker packaging — remains, tracked in
Future.md.)*

- [x] **0.1** Route Sage through `core/ai_gateway` (new `sage_chat` task policy): gains fallback chain, health tracking, provenance chips for free. *(Done 2026-10: `SAGE_CHAT` TaskType, Ollama-primary policy with Gemini fallbacks, `chat_with_sage` rewritten to gateway contract returning `{"response", "provenance"}`, provenance caption in the UI.)*
- [x] **0.2** Extend `Provenance` to carry token usage; add `llm_calls` table + gateway hook so every call (Sage, summariser, classifier, evaluator) is persisted with tokens/latency/cost_class. *(Done 2026-10: `Provenance.tokens_in/out` populated from provider usage; `core/ai_gateway/call_log.py` fire-and-forget writer; `insert_llm_call` on the Supabase manager; `supabase_migration_llm_calls.sql` — note `correlation_id` is VARCHAR(40), not UUID, matching the 8-char request ids.)*
- [x] **0.3** Langfuse Cloud Hobby account + OTel span export from the gateway (env-gated no-op when keys absent). Link `trace_id` into `llm_calls`. *(Done 2026-10: `core/ai_gateway/tracing.py` — one span per gateway execution, OTLP HTTP export to `{LANGFUSE_HOST}/api/public/otel/v1/traces` with Basic-auth keys, `gen_ai.*`/`langfuse.observation.*` attributes so LLM calls render as generations, input/output capped at ~2000 chars. `Provenance.trace_id` (32-char hex) flows into the `llm_calls` row. Keys resolve via the `config.settings` chain (`LANGFUSE_PUBLIC_KEY`/`SECRET_KEY`/`HOST`); conftest autouse fixture pins tracing off in tests. Deps: `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`.)*
- **Gate**: a Sage chat produces a visible trace in Langfuse and a row in `llm_calls`. *(Verified 2026-10 with a real US-region Langfuse Hobby project: one live `sage_chat` gateway call exported a span with no OTLP errors and the newest `llm_calls` row carried the matching 32-hex `trace_id`. Project is US-region — `LANGFUSE_HOST = https://us.cloud.langfuse.com` in secrets.toml.)*

---

## Phase 2 — Article-level grounding (sparse retrieval only, no vectors yet) — CLOSED

- [x] **2.1** Add `articles` retrieval to Sage context assembly (SQL: recent + keyword match within budget), alongside theme summaries. *(Done 2026-10: `SupabaseManager.get_articles_for_context()` fetches a bounded newest-first candidate window (theme/source/date filters, no SQL keyword matching — that's Phase 2.3 FTS); `core/sage_agent.py` scores candidates against the question client-side (`score_article_relevance`, title ×2) and renders the top `ARTICLE_TOP_K`=15 as an `=== ARTICLE EVIDENCE ===` section within a 30% slice of the char budget, unused budget flowing back to summaries. Local fallback flattens `themed_articles` from history.json. No keyword matches → newest-first, so Sage always has some Tier-1 grounding. Links carried but not rendered (Phase 2.2). Sequencing decision: the faithfulness judge below is a separate follow-up task — no pre-2.1 baseline will be recorded; judge scores start from post-2.1.)*
- [x] **2.2** Citations: Sage prompt gains article links from `articles.link`; UI renders them (accordion/evidence popover — Pillar D). *(Done 2026-10: `_render_article_section` appends a `Link:` line to each brief and returns the rendered-article list (title/source/date/link/snippet) as `build_wiki_context()["articles"]`; system prompt instructs Sage to attach context links as Markdown hyperlinks and never invent URLs. Links sanitised to http(s) only (`_safe_link`) since scraped URLs are untrusted. `pages/5_History.py` renders a `📰 Article Evidence (N)` accordion under each assistant message — the list is persisted as an `articles` key on the message dict, so popovers survive conversation reload (JSONB-friendly extra key; Supabase + local JSON paths both pass it through).*
- [x] **2.3** Add `tsvector` column + GIN index on `articles`; switch keyword matching to proper FTS queries. *(Done 2026-10: `supabase_migration_fts_articles.sql` adds a `search_vector` GENERATED ALWAYS tsvector (title weight A, summary weight B — title×2 semantics in SQL) with a GIN index and an invoker-rights `match_articles(query, filters, result_limit)` RPC ranked by `ts_rank` (PostgREST's built-in fts filter can't ORDER BY `ts_rank`, so a function is the standard pattern; no backfill needed — STORED generation populates existing rows). `get_articles_for_context(query=...)` routes to the RPC when a question is supplied, degrading to the existing newest-first window on RPC failure or empty match — pre-migration databases keep working. `build_wiki_context` passes the raw question (websearch_to_tsquery does its own parsing); `_select_articles` client-side scoring stays the final provider-agnostic ranking step, and the local history.json fallback (no Postgres → no FTS) is unchanged. Riding along: `SAGE_SYSTEM_PROMPT` reworked to answer-first format — Direct Answer → Evidence (chronological for trend questions) → Assessment — with new grounding rules for partial-evidence scope, conflicting-evidence surfacing, and concrete numbers over vague characterisations; all 2.1/2.2 groundedness invariants kept. Sparse half of Pillar A.)*
- **Gate**: faithfulness judge for Sage answers added to eval infra — baseline scores recorded. *(Done 2026-10: `record_sage_faithfulness()` in `core/evaluator.py` judges each successful Sage reply chat-time, fire-and-forget on a daemon thread (the `llm_calls` pattern — the context string and the answer coexist only at the moment of reply, so judging later would rebuild a drifted context). One `TaskType.EVALUATE` call via `GatewayJudgeLLM` scores the answer against the exact wiki/article context it was given; only successful judgments insert a `sage_evaluations` row (`supabase_migration_sage_evaluations.sql` — per-reply shape, not `quality_evaluations`, which is run-scoped with NOT NULL summary columns), so judge outages skip rows rather than masquerade as hallucination. Page 7 renders the rolling mean, per-reply history, and recent unsupported claims. Baseline caveat, per the recorded sequencing decision: no pre-2.1 baseline exists — scores start from post-2.3, and Phase 3 changes are measured against that batch. **Phase 2 closed.**)*

---

## Phase 3 — Embedding pipeline (Completed 2026-11-10)

*(Dense half of Pillar A. Provider decision and comparisons: [EMBEDDING_PROVIDERS.md](EMBEDDING_PROVIDERS.md).)*

- [x] **3.1 Storage (2026-10-26).** Enabled `vector`; added `article_embeddings` with `UNIQUE(article_id)` (one curated title+summary per article, no chunking yet), `embedding vector(768)`, denormalized `run_date`, `chunk_text` + `embedding_model` for audit/re-derivation, an HNSW cosine index, and the invoker-rights `match_article_embeddings` RPC.
- [x] **3.2 Gateway provider (2026-10-26).** `TaskType.EMBEDDING` carries a text batch in `embed_inputs` (empty prompt), skips templates/schemas, and writes vectors through the same health/retry/provenance/`llm_calls`/OTel wiring as every other task. `GeminiProvider.embed()` pins `output_dimensionality=768`. The policy intentionally had `fallback: []` — a rule-based embedding is meaningless and a cross-model fallback would mix vector spaces.
- [x] **3.3 Ingest-time embedding (2026-10-26).** `core/bg_refresher.py` calls `embed_unembedded_articles()` after persistence; best-effort, never fails a run.
- [x] **3.4 Resumable backfill (2026-10-26).** `backfill_embeddings.py` + the `get_unembedded_articles` RPC: the LEFT JOIN is the cursor, so an interrupted run simply resumes. Batching (50/call) and an inter-batch sleep provide rate-limit headroom.
- [x] **3.5 Cloudflare Workers AI migration (2026-11-10).** Gemini's free embedding quota was the throughput bottleneck (45 of 67 logged calls failed with 429 at 100 req/min; only 750 of 6,304 articles embedded). Migrated to `@cf/baai/bge-base-en-v1.5` via the OpenAI-compatible `/ai/v1/embeddings` endpoint.
  - **Explicit switch:** `EMBEDDING_PROVIDER` (default `gemini`) selects the route from `EMBEDDING_ROUTE_PRIMARY`; registering `CF_ACCOUNT_ID`/`CF_API_TOKEN` does not move traffic. The `embedding` fallback chain stays empty for both providers because cosine distance is only meaningful within one model's space.
  - **Context-fit bypass:** `_execute_inner()` used to check the batch's *summed* token estimate against a model window. Cloudflare's 512-token cap is **per text**, so a normal 50-article batch would have been skipped. `TaskType.EMBEDDING` now bypasses context-fit; a regression test covers a 50-item batch.
  - **Idempotent upsert:** `insert_article_embeddings()` changed from append-only to `upsert(rows, on_conflict="article_id")`, enabling in-place re-embedding under `UNIQUE(article_id)`. One retry was added for a transient Windows `WinError 10035` socket failure under rapid backfill traffic.
  - **Usage telemetry:** Cloudflare's OpenAI-compatible embeddings response omits `usage` (verified 2026-11-10), so the adapter records an estimated `tokens_in` (~3 chars/token) to keep `llm_calls` quota tracking usable; the Cloudflare dashboard remains exact. Migration rows written before this fix carry zero token counts — use call count / the dashboard for that one-off burst.
  - **Forced re-embed + resumability:** `backfill_embeddings.py --reembed` reads each row's stored `chunk_text` and re-embeds it. `get_articles_for_reembedding(exclude_model=...)` filters out rows already carrying the target model, so every successful upsert removes its rows from the next query — a rerun resumes instead of redoing the first page (observed: the first attempt stopped after 100 rows on a socket error; the retry continued from row 101 without loss).
  - **Migration result (2026-11-10):** 750 existing Gemini rows re-embedded, 5,554 backlog articles embedded → **6,304/6,304 (backlog 0)**, `embedding_model` 100% `@cf/baai/bge-base-en-v1.5`, all rows accepted by the `vector(768)` column. Cloudflare embedding calls all succeeded; the only failures in `llm_calls` were historical Gemini 429s.
- **Gate (passed 2026-11-10):** `core/retrieval_eval.py` scored **Recall@10 = 0.833** across all 8 questions (per-question 0.67–1.00), snapshot in `app_settings.retrieval_eval_latest`. Caveat: the recorded 0.510 baseline was Gemini on a partial ~100-row index, so the improvement reflects both the model and full coverage — treat it as directional, not a controlled model comparison. The 3.6 14-day quota/error/latency watch ran 2026-11-10 → 2026-11-27 and closed healthy (below).

---

## Phase 3.6 — Metrics watch (closed 2026-11-27)

The contingent 14-day watch after the Cloudflare migration (thresholds defined in
[EMBEDDING_PROVIDERS.md §8](EMBEDDING_PROVIDERS.md)). **Verdict: every gate healthy —
Option C (Render) is NOT triggered; Cloudflare stays the embedding provider.**

Results over the watch window (steady-state Nov 11–26; the Nov 10 backfill burst —
268 calls, 58k tokens, 3×429 — excluded per the watch rules):

| Metric | Observed | Threshold | Verdict |
|---|---|---|---|
| Quota headroom | 1–3 calls/day, 0.9k–13.2k tokens/day (peak Nov 18) vs ~1–2M tokens/day estimated free → ~1% | ≤25%, no day >50% | ✅ |
| Errors | 28/28 steady-state calls succeeded. All 9 failures in the window were 429s: 6 pre-migration Gemini (Nov 8–9) + 3 backfill-day | <1%; trigger >5% × 3 days | ✅ |
| p95 latency | 387–1,794 ms every steady-state day | <2 s | ✅ |
| Backlog | 0 — 7,006/7,006 embedded (up from 6,304; ~41 new articles/day all kept up) | 0, then < one day's ingest | ✅ |
| Model uniformity | 100% `@cf/baai/bge-base-en-v1.5`, all rows exactly 768 dims | single model, 768 dims | ✅ |
| Retrieval quality | Re-gated on a new curated set: **Recall@10 = 0.917** (24/24 scored) | no >5 pp drop vs baseline | ✅ (new baseline) |

Caveats: `tokens_in` is the adapter's ~3-chars/token estimate (Cloudflare omits `usage`);
the dashboard Neurons graph remains the exact quota source. Zero-call days (Nov 16–17,
21, 27) had no ingest; backlog 0 confirms nothing pending.

**Eval-set loss & recreation.** The original 8-question seed
(`data/eval_retrieval_questions.json`, baseline 0.833) lived in gitignored `data/` and
was lost — the retrieval gate could not be re-run, which also blocked Phase 5's
old-vs-new comparison. Recreated 2026-11-27 as **`eval_sets/retrieval_hit_rate.json`**
(git-tracked): 24 questions curated against the live archive, covering all 7 themes,
each with 2–3 expected keywords verified present in the target article's `chunk_text`
(title + summary) and a `target_title` provenance field. New baseline **Recall@10 =
0.917** — 21 questions at 1.00; honest weak spots kept as hard cases: SOCI cold-start
(q06, 0.00), OpenAI Lockdown Mode (q19, 0.33), Anthropic IPO (q24, 0.67). The old 0.833
is NOT directly comparable (different questions); 0.917 is the Phase 5 gate baseline.
Snapshot now carries `set_id`, `ran_at`, and `per_question` scores in
`app_settings.retrieval_eval_latest`.

**Infrastructure shipped with the closure:**
- `core/eval_sets.py` — generic registry: any `eval_sets/*.json` matching the schema
  is discoverable; pagination + best-effort results helpers, no Streamlit imports.
- `pages/9_Eval_Sets.py` — browses every eval set, 20 questions/page with prev/next,
  ground-truth keywords + target article + latest per-question score per card.
- `core/retrieval_eval.py` now loads from the registry (`RETRIEVAL_SET_ID`), and the
  snapshot includes per-question results for the UI.
- `pages/10_Blog.py` + `core/blog_index.py` — searchable rendering of BLOG.md
  (case-insensitive AND search over entry titles + bodies).

**Decision order if a future re-check trips a threshold** (unchanged): **Option C
(Render, `BAAI/bge-base-en-v1.5`) first** — same weights as Cloudflare → same vector
space → routing change, no re-embed. **Option B (Jina)** second — different weights →
full re-embed; reserve for multilingual/long-context needs or if A and C are both
constrained. Tracking queries for ad-hoc re-checks: [EMBEDDING_PROVIDERS.md §8](EMBEDDING_PROVIDERS.md).

---

## Phase 4 — Full-coverage article processing (4.1–4.6 landed)

*(4.1 skip visibility and 4.2 fallback-ledger truthfulness landed 2026-10-26/27 —
see the BLOG.md entry for that week. 4.3–4.6 below landed 2026-11-27.)*

- [x] **4.3 Fair drain / backlog carryover (2026-11-27).** Three pieces:
  - **Counter:** `articles.times_seen` (`supabase_migration_times_seen.sql`) counts
    runs classified-but-not-summarised; reset to 0 on inclusion. Maintained by the
    `record_article_seen(theme, waiting[], included[])` **UPDATE-only** RPC —
    PostgREST upserts cannot express per-row increments, and a partial-column
    upsert would insert title-less rows for articles whose first `save_articles`
    happens after summarisation. Partial index on `(theme_name, times_seen) WHERE
    times_seen > 0`. Local JSON fallback `data/times_seen.json` keeps pre-migration
    and offline deployments working (`core/processed_articles.py`:
    `get_times_seen` / `record_run_seen`, Supabase-preferred like the processed ledger).
  - **Aging boost:** `_rank_articles_by_relevance` adds `aging_boost_per_run ×
    times_seen` (default 2.0 via `get_summariser_settings()`, 0 disables) to the
    shared keyword-relevance score, so waiting articles monotonically rise and
    drain inside the 14-day window. The orchestrator computes `waiting_hashes` =
    unprocessed − surfaced (NOT the skipped complement, which also contains
    already-processed articles) on all three paths (gateway, extractive fallback,
    extractive-only mode) and advances the counter before `mark_processed`.
  - **Aged-out record:** `history_manager._record_aged_out()` at persistence diffs
    the previous run's `waiting_hashes` against the current classified set;
    departed hashes become `aged_out_articles` (title/source/link/hash/last_seen)
    on the summary + `generation_log`, rendered by the History page's 🍂 expander
    in both local and Supabase modes. A feed dropping an article early is recorded
    too; if it returns later it re-competes as unprocessed (ledger unchanged).
  - **Migration batching decision:** Future.md originally batched this migration
    with 5.4's `theme_first_seen`; shipped separately because 4.3 needs the counter
    now and 5.4 is gated behind Phase 5 retrieval work. 5.4 adds its own migration.
  - Tests: `tests/test_fair_drain.py` (17 tests: boost on/off, counter local+cloud,
    RPC shape, aged-out diff incl. generation_log-only prev runs, orchestrator
    waiting set).
- [x] **4.4 Budget-aware capacity (2026-11-27, eval-gated).** Flat
  `MAX_ARTICLES_PER_SUMMARY = 12` retired from the run pipeline (constant kept for
  legacy importers); selection ranks to `max_articles_per_summary` (default 50,
  `get_summariser_settings()`) then `split_articles_for_prompt` fills to the char
  budget (1.8M chars on Qwen's 1M window — the ceiling binds, not the window).
  `article_summary_chars` (default 300) controls per-article truncation.
  - **Gate experiment** (`eval_capacity_gate.py`, controlled same-pool arms
    on the freshest run, ~272 articles / 5 themes >12): A = legacy 12/300,
    B = 50/300, C = 50/500. **True coverage (narrative-only): A 38.4% → B 54.9%
    (+16.5pp); C 56.5%** (+1.6pp over B, within single-run sampling noise at
    higher token cost → 300 kept). Processed fraction 22% → 81%; input tokens
    ~1.7× (17k → ~22-26k per arm). Per-fed-article naming rate 93% → 61%:
    synthesis compression is real; 4.5's per-batch briefs are the remedy. The
    70% absolute Coverage threshold remains open until 4.5.
  - **Measurement bug found & fixed:** the first experiment passed full summary
    dicts into `coverage_judge`, whose join of *all* dict values read the 4.1
    `skipped_articles` title payload as prose — inflating A to 98% and reversing
    the gate's apparent direction. Production was unaffected (the evaluator's
    `_load_summaries_for_run` selects only narrative columns), but
    `coverage_judge` now joins `_NARRATIVE_SECTION_KEYS` only, with regression
    tests (`tests/test_coverage_hygiene.py`). Lesson: a recall metric must
    whitelist prose sections; any metadata payload carrying titles is a leak.
  - **Environment note:** two experiment runs hit Qwen 180s timeouts followed by
    Gemini fallbacks hanging 702s / 8329s (DNS `getaddrinfo failed`); the Gemini
    provider lacks an effective timeout bound — candidate reliability fix,
    tracked outside this phase.
  - Tests: `tests/test_capacity.py` (6: defaults, ceiling, override, budget bind,
    truncation knob ×2) + adapted skip-complement test.
- [x] **4.5 Chunked overflow / map-reduce (2026-11-27, eval-gated).**
  `generate_theme_summary_mapreduce`: MAP = ranked pool sliced into
  `mapreduce_batch_size` (12) batches, each through the unchanged
  `generate_theme_summary_gateway` (semaphore 3); REDUCE = one gateway call
  merging all batch briefs + prior context under a preserve-every-name mandate
  (section-1 target 120 words/batch capped 600; further reading merged, deduped,
  kept whole). Reduce failure → `_merge_intermediates_deterministic`
  (concatenated sections, deduped reading; source `extractive:mapreduce_merge`).
  Included = union of batch-fed hashes (4.1/4.3 ledgers stay truthful); trigger
  only when unprocessed pool > ceiling; cost bounded by `mapreduce_max_batches`
  (8 → 96 articles/theme/run), remainder waits via 4.3.
  - **Reduce output budget scales** (`min(8000, max(settings, 1600+220×batches))`):
    the single-pass 2200 max_tokens would truncate the merged reading list
    mid-entry and silently drop names — caught pre-gate, regression-tested.
  - **Gate (arm D of `eval_capacity_gate.py`, same pool as 4.4):** coverage
    **74.12%** (189/255) — clears the ≥70% absolute threshold 4.4 left open;
    processed **251/274 (92%)**; missed 66 (B 115, A 157); fed-naming ≈75%
    (B 61%, A 93% at 12 fed). Cost: 26 logged calls, 66.6k tokens in / 34.1k out,
    340 s, 0 failures (~3× single-pass input; only overflow themes pay it, so
    steady-state daily runs mostly stay single-pass). **6.1 gate satisfied.**
  - Residuals: 66 pool articles still unnamed (reduce compression is real but
    bounded); 23 articles beyond the batch bound wait via aging. 4.6
    prioritisation and Phase 5 retrieval are the next levers, not more capacity.
  - Tests: `tests/test_mapreduce.py` (6: defaults, batching+union, cost bound,
    deterministic merge+dedupe, reduce budget scaling, orchestrator trigger).
- [x] **4.6 Prioritisation refinements (2026-11-27, deterministic gate).**
  Four ranking signals added to `_rank_articles_by_relevance`, each a knob via
  `get_summariser_settings()` (each 0 disables that signal; all 0 + aging 0 =
  legacy relevance order):
  - **Recency** (`recency_weight`, default 1.0): bonus decaying linearly to 0 at
    `DAYS_LOOKBACK` (14) days old; also the first tie-break (score desc →
    published_date desc → insertion order). Missing/unparseable dates are
    neutral, never penalised.
  - **Source diversity** (`source_diversity_penalty`, default 1.5): greedy
    selection subtracts the penalty per already-picked article from the same
    source — the extractive engine's diversity-filter pattern applied to prompt
    selection. Demotes, never drops: small pools still feed everything.
  - **Near-duplicate demotion** (`near_duplicate_penalty` 4.0,
    `near_duplicate_threshold` 0.8): greedy title-token Jaccard clustering in
    base-score order; syndicated copies (e.g. MarkTechPost reprints) score below
    the representative but stay selectable, so the processed ledger stays
    truthful.
  - **Gate confidence** (`gate_confidence_weight`, default 0.5): bonus scaled by
    classification gate (1 keyword → 1.0, 2 TF-IDF / 3 LLM → 0.7, 4 heuristic →
    0.0, missing → neutral 0.4). Applies to freshly classified articles;
    Supabase-restored articles carry no `gate` column and stay neutral.
  - **Gate** (`eval_ranking_gate.py`, deterministic same-pool before/after —
    ranking changes selection, not capacity, so no LLM arms; 4.3 precedent):
    freshest run, 273 articles / 7 themes, cap 50. Only Agentic Systems &
    DevTools exceeds the cap (84): distinct sources **19 → 24**, mean article
    age **7.27 → 6.34 days**, 40/50 selection overlap, duplicate pairs fed
    0 → 0 (no syndication in this pool at Jaccard 0.8 — the signal is
    unit-tested for when it appears). Other themes select identically
    (pool ≤ cap; ordering only). Results: `scratch/eval_4_6_results.json`.
  - Composition with 4.3 verified: the aging boost still flips order for
    long-waiting articles (regression test); map-reduce batch composition
    inherits the refined order unchanged.
  - Tests: `tests/test_ranking_refinements.py` (20: per-signal on/off, decay,
    tie-breaks, threshold respect, never-drop guarantees, neutral missing
    fields, legacy path, aging composition, defaults).

## Phase 5 — Hybrid retrieval + temporal awareness (CLOSED 2026-11-28)

- [x] **5.1 Dense leg in Sage.** `core/retrieval.py::hybrid_search()` replaces
  the keyword-overlap scorer in `_fetch_article_candidates` (real-client
  guard; stub/mock clients keep the legacy path). `match_article_embeddings`
  v2 (`supabase_migration_hybrid_retrieval.sql`) joins article metadata
  (title/summary/theme/source/link/published_at/run_date) so dense hits are
  renderable evidence, not just chunk text. Hybrid results carry `_rrf_score`
  and `_select_articles` preserves their order instead of re-scoring.
  Fallback chain intact: hybrid empty/failed -> Phase 2.3 FTS -> newest-first
  window -> local client-side scoring. Kill switch:
  `hybrid_retrieval_enabled` (`get_retrieval_settings()`).
- [x] **5.2 RRF fusion.** `match_articles_fts` (pure FTS matches — no window
  fallback inside a fusion leg) + dense top-50, fused by
  `score = sum 1/(60+rank)`, deduped by article id (sparse `id` vs dense
  `article_id`). `chunk_text` eval haystack preserved from the dense leg,
  synthesised title+summary for sparse-only rows.
- [x] **5.3 Temporal awareness.** `parse_temporal_bounds()`: last/past N
  days/weeks/months, last week/month, this week/month, today/yesterday,
  "<month> <year>", "in/during <year>", "since <month>", "between <month>
  and <month>" (cross-year aware). Bounds intersect caller (UI) filters and
  constrain both legs. **HNSW gotcha handled in SQL**: the RPC over-fetches
  4x match_count unfiltered in an ANN CTE, then filters + limits (Pillar C
  mitigation (a)). Time-decay `rrf * (floor + (1-floor)*exp(-age/halflife))`
  applies ONLY to unbounded queries.
  - **Gate-driven tuning:** first attempt (floor 0.5 / halflife 30d)
    REGRESSED Recall 0.875 -> 0.833 — both lost targets (q05 Voltus/Google
    VPP, q21 Google Flash Cyber) were June/July articles whose scores the
    steep decay halved below fresher dense neighbours; on a ~6-month archive
    decay acted as a filter, not a tie-break. Retuned to **floor 0.9 /
    halflife 180d** (max ~1.11x freshness preference): both recovered, gate
    passed. Lesson: decay strength must be calibrated to archive age span,
    not ingest cadence.
- [x] **5.4 theme_first_seen.** Table (theme PK, first_seen_date, run_id,
  evidence) + archive backfill in the migration; insert-if-absent at ingest
  (`record_theme_first_seen`, upsert ignore-duplicates, skipped themes never
  claim first appearance); Sage's FIRST APPEARANCE tag compares run_date to
  the true archive date — the old window scan mislabelled every window's
  oldest run. Offline/pre-migration keeps the legacy scan.
- **Gate (PASSED 2026-11-28):** `python -m core.retrieval_eval --gate`,
  24-question curated set, same-day arms: dense **0.875** -> hybrid
  **0.875** (no regression), hybrid latency mean 1587ms / p95 **1918ms**
  (budget 3000ms). The 0.917 snapshot from 2026-11-27 predates archive
  growth — the same-day control is the valid comparison. Verdict +
  per-question scores in `app_settings.retrieval_eval_latest` (Eval Sets
  page). Residuals: q06/q16 missed by both arms (dense-index limits, not
  fusion); q19/q24 partial keyword recall on both arms.
- Tests: `tests/test_retrieval.py` (41: temporal parsing x14, RRF math,
  leg degradation, decay on/off, bounds intersection, source post-filter,
  Sage wiring incl. legacy-path guard, first-seen accessors, eval hybrid
  arm). Migration applied to the live project 2026-11-28 (user-approved;
  the MCP SQL tool is read-only, so it was run via the SQL Editor).

---

## Phase 6 — Hierarchical memory & monthly rollups (CLOSED 2026-11-28)

- [x] **6.1 monthly_syntheses + monthly rollup job (2026-11-28).**
  - **Schema:** `supabase_migration_monthly_syntheses.sql` —
    `UNIQUE(year_month, theme_name)` (fixes the Future.md §4 draft's
    single-column UNIQUE bug, which would have allowed only one row per
    month), `macro_summary`, `key_milestones` JSONB, `source_run_count`,
    `generation_source`, `created_at`/`updated_at`. RLS FOR ALL + anon
    SELECT/INSERT/UPDATE (rows are upserted on refresh, like
    `app_settings`). Applied via SQL Editor (MCP SQL is read-only).
  - **Cadence** (`compute_due_syntheses`, pure/unit-tested): missing
    (month, theme) rows are due; the current month re-synthesizes every
    `monthly_refresh_days` (14, user-chosen biweekly); a completed month
    whose row was written mid-month (`updated_at` < next-month-start UTC)
    regenerates once after rollover, then never again. `updated_at` is
    stamped explicitly on every upsert — DEFAULT NOW() only fires on
    INSERT, and the staleness rules read it.
  - **Runner:** `core/monthly_synthesis.py::synthesize_due_monthly()`
    shared by the best-effort `bg_refresher` hook (placed AFTER the
    persistence if/else so month rollover is checked even on
    no-new-article runs; capped at `monthly_synthesis_max_per_run`=7) and
    `backfill_monthly_syntheses.py` (`--dry-run/--month/--force/--limit`)
    — the future GitHub Actions cron entry point. Offline/pre-migration
    skips silently; Supabase is the single source of truth (no local
    fallback — history.json keeps only 5 runs).
  - **Input:** `get_theme_briefs_for_month()` bounds on
    `trend_runs.run_date` (not run_timestamp), chunks run_ids by 50,
    retries without optional columns on schema drift. Feeds only the
    narrative sections (what_is_happening/why_it_matters/what_to_watch)
    plus ≤3 verbatim further-reading URLs per brief (LINKS lines are the
    only citable URL source); >`max_runs` (40) briefs → evenly-spaced
    chronological sample keeping first+last.
  - **LLM:** `TaskType.SYNTHESISE` (the previously-unused gateway route),
    `allow_deterministic_fallback=False` — an extractive "macro synthesis"
    would be misleading, so failures write no row and retry next run.
    Milestone JSON parsed defensively (last ```json fence → bare fence →
    bracket slice; any failure → [] while keeping the prose).
  - **Gotchas:** WinError 10035 transient socket errors under backfill
    traffic → one retry on the idempotent upsert (same pattern as
    `insert_article_embeddings`); two smoke-run "write failures" had
    actually landed server-side. The May archive predates 2 of the 7
    current themes — brief-less theme-months skip without a row and
    re-check cheaply each run (self-heals if briefs appear).
  - **Backfill (2026-11-28):** 47 rollups across 2026-05→2026-11 (May:
    5 themes × 22 briefs; Jun–Sep hit the 40-brief sampling cap). 47
    `synthesise` calls in `llm_calls`: 726.9k in / 60.4k out tokens, avg
    latency 12.2s, ~10 min wall clock, all qwen3.8-flash, 0 synthesis
    failures. Re-run → **0 writes in 1.7s** (idempotency proven).
    Spot-check: milestone dates/content faithful to source briefs; no
    fabricated URLs (May briefs have empty further_reading → milestones
    correctly carry no links).
  - **UI:** History → Memory Timeline 🗓️ Monthly Syntheses (Tier 3)
    expander — read-only, months desc, theme-filter aware, milestone
    sub-expander, "Updated … · covers N runs · engine" caption; silent
    pre-migration. Sage tier-aware consumption is 6.2 (pending).
  - Tests: `tests/test_monthly_synthesis.py` (41: due-check rules incl.
    14-day boundary/clock-skew/force, input bounding/sampling/LINKS,
    parser validation, gateway request contract, runner isolation/caps),
    `tests/test_supabase_client.py` (+11: month bounds/chunking/drift
    retry/upsert contract), `tests/test_bg_refresher.py` (+2: hook on
    no-persistence path, hook failure doesn't fail the run). Full suite:
    791 passed, 1 skipped.
- [x] **6.2 Tier-aware Sage assembly (2026-11-28, eval-gated).**
  - **Assembly** (`core/sage_agent.py::build_wiki_context`): older
    COMPLETED months with rollup rows render as Tier-3 macro blocks
    (`_format_month_block`: prose condensed to `sage_tier3_block_char_limit`
    1200 + ≤3 dated milestones with verbatim `_safe_link` URLs) instead of
    per-date condensed blocks. Current month always stays Tier-2 (rollup
    staleness); recent-2 dates keep full detail; a month containing a
    recent date is never double-represented; months without rollups keep
    condensed blocks. Selection: one coverage block per month (ascending),
    then global relevance fill capped at `sage_tier3_max_blocks_per_month`,
    within `sage_tier3_budget_share` (0.6) of the older budget — surplus
    flows back to condensed; a budget-starved selection never silently
    drops Tier-2 content. Chronological merge keys month blocks
    'YYYY-MM-00'. FIRST APPEARANCE on a month block when the theme's true
    first-seen month matches. Stats: `tier3_block_count` / `tier3_months`
    (return dict 6 → 8 keys); grounding caption gains "🗓️ N monthly
    rollups". Knobs: `get_sage_settings()` (`_SAGE_DEFAULTS`, key-filtered);
    `sage_tier3_enabled=False` restores byte-identical pre-6.2 assembly
    (regression-tested). Source filters disable Tier 3 (rollups aggregate
    across sources). Effective bounds = UI ∩ `parse_temporal_bounds(question)`,
    applied to the Tier-2 fetch too.
  - **Drill-down (user-chosen scope):** milestone-biased query — the hybrid
    article leg receives question + top milestones of the ≤3 most relevant
    coverage rollups (`sage_tier3_query_char_limit` 600), with ISO dates
    and 4-digit years stripped so milestone text cannot poison
    `parse_temporal_bounds`. FTS fallback keeps the raw question
    (websearch_to_tsquery dilution). Zero extra retrieval calls;
    per-milestone sub-queries stay with Phase 7's Sage Scout.
  - **Gate finding that changed the design:** activation must NOT gate on
    the fetched Tier-2 window's month span — a Full Archive fetch truncated
    by `limit=300` to one recent month (November) is precisely the case
    Tier 3 must extend. First gate run FAILED coverage (0.38) because of
    this; eligibility comes from the rollup TABLE, and the full-coverage +
    recent-exclusion rules already keep single-month windows unchanged.
    Regression test: `test_full_archive_truncated_window_still_activates`.
  - **Gate metric finding:** absolute context-char reduction is
    unachievable — both arms fill the same 40k budget greedily and Tier-3
    ADDS reach. Operationalized as chars PER COVERED MONTH (≥30% reduction)
    plus a ≥0.75 expected-month coverage guard (stops the judge rewarding
    high-scoring "no data" refusals). Also: the faithfulness judge truncates
    context to `EVAL_SAGE_CONTEXT_CHARS` (8000) — the harness patches it to
    60k for BOTH arms symmetrically, else the legacy arm is penalized
    asymmetrically (contexts are chronological ascending, articles last).
  - **Eval harness:** `core/sage_eval.py` (pattern: `core/retrieval_eval.py`)
    + git-tracked `eval_sets/sage_macro_faithfulness.json` (8 multi-month
    questions curated against the live rollups). Two arms (legacy via
    single-knob settings patch / tier3 live defaults), real gateway chats,
    `judge_sage_answer` against each arm's own context; chat/judge failures
    are excluded rows, never zeros; eval rows NOT written to
    `sage_evaluations` (rolling baseline stays clean). Verdict snapshot
    `app_settings.sage_eval_latest`, rendered on the Eval Sets page via new
    kind-agnostic helpers `result_score`/`summary_mean` (`core/eval_sets.py`).
    Console-safe ASCII prints + snapshot-before-print (a cp1252 `Δ` crash
    after a completed first run lost its verdict — both fixed).
  - **Gate (PASSED 2026-11-28):** `python -m core.sage_eval --gate`,
    8 questions, 32 LLM calls: faithfulness legacy **0.925** → tier3
    **0.975** (eps 0.02); chars/covered-month 33,640 → **6,848**
    (**−80%**, need ≥30%); month coverage **0.90** (need ≥0.75); 8/8
    scored both arms. Residual: m01 tier3 0.80 (one judge-flagged claim,
    within noise); October sometimes budget-dropped from coverage (6 of 7
    months at the live budget).
  - Tests: `tests/test_sage_agent.py` (+22: TestTier3Context incl. offline
    autouse hybrid patch — spec-mocks pass the isinstance guard and would
    otherwise embed over the network), `tests/test_sage_eval.py` (16),
    `tests/test_eval_sets.py` (+4). Full suite: 833 passed, 1 skipped.

---

## Phase 7 — Multi-agent synthesis (CLOSED 2026-11-28)

- [x] **7.1–7.3 Sage deep-research pipeline (2026-11-28, eval-gated).**
  - **Pipeline** (`core/sage_pipeline.py::chat_with_sage_deep`): Scout
    (`SAGE_SCOUT`, 2–3 JSON sub-queries) → hybrid retrieval per sub-query
    (`sage.retrieval` spans) → Analyst draft (`SAGE_ANALYST`) →
    Fact-Checker (`SAGE_FACTCHECK`, statement-level JSON
    `{unsupported_claims:[{claim,reason}]}`) → bounded revision (1,
    drop-on-flag). Opt-in via the 🔬 toggle on the new Sage page (default
    off); kill switch `sage_deep_research_enabled`. Return contract is the
    fast path's superset: `response`/`provenance` + `articles` (merged
    evidence), `context` (the rebuilt context the judge must see), and
    `pipeline` metadata (sub_queries, claims, stages, ids).
  - **Tracing made real (Pillar F):** new public
    `tracing.pipeline_span(name, attributes)` parent-span helper; nesting
    is automatic via OTel context (`start_as_current_span`), so one reply
    = one Langfuse trace: `sage.pipeline` → `gateway.sage_scout`,
    `sage.retrieval`×N (each with its `gateway.embedding` child),
    `gateway.sage_analyst`, `gateway.sage_factcheck`, optional revision.
    All agent requests share one `correlation_id` → `llm_calls` rows
    group per reply; `trace_id` links to Langfuse. Orchestration stays
    single-threaded (`asyncio.run` inside the ambient span) — thread
    pools would orphan spans without explicit context copies.
  - **New task types:** `sage_scout`/`sage_factcheck`/`sage_analyst` —
    routing mirrors `sage_chat` (Qwen primary, Gemini fallbacks, NO
    deterministic fallback), verbatim prompts, no schemas. The `system`
    field remains dead — prompts fold into `input`.
  - **Page reorganisation (user-requested):** Ask Sage extracted from
    `pages/5_History.py` into **`pages/11_Sage.py`** (all `sage_*` session
    keys moved verbatim; the tab's date/source params were dead code).
    Memory Wiki keeps Timeline + Compare + Tier-3 expander. Nav "Sage &
    memory" group now holds both. Pipeline-detail expander shows
    sub-queries, stage table, flagged claims, correlation/trace ids.
  - **Documentation (user-requested):** new **docs/SAGE.md** — the focused
    reference for ALL Sage & Memory Wiki work (page map, fast path,
    pipeline stages + degradation table, tracing, conversations, knobs,
    gates, extension guide). CLAUDE.md routing row added; README gained a
    "🔮 Sage" section; PAGES.md internals subsection moved to SAGE.md.
  - **Gate run 1 FAILED — and caught two real design bugs:** deep 0.863
    vs fast 0.950. (a) The pipeline REPLACED the fast path's
    eval-validated milestone-biased evidence with sub-query hits; the
    Analyst embellished narratives around the new articles (m03 0.50:
    export-control framing beyond the sources). (b) The soft "cite-or-drop"
    revision let flagged claims survive rephrased (m03 revised=true, still
    0.50). Fixes: evidence **augments** (base articles first, scout finds
    append within budget, dedupe by link); Fact-Checker treats
    exaggeration/generalization as unsupported; revision DROPS flagged
    statements entirely unless explicitly supported. Regression tests
    pin both (`test_base_evidence_survives_scout_finds`).
  - **Gate (PASSED 2026-11-28, run 2):** `python -m core.sage_eval --gate7`,
    8 macro questions: deep **0.950** vs fast 0.900 (improved; eps 0.02),
    coverage **0.90** (≥0.75), deep mean latency **44.1s** (≤60s budget),
    8/8 scored both arms. Note: the fast arm scored 0.950 in run 1 and
    0.900 in run 2 — judge noise across runs; the same-run controlled
    comparison is the valid one (Phase 5 lesson applies to judges too).
    Verdict snapshotted to `app_settings.sage_eval_latest` (arm "deep").
  - Tests: `tests/test_sage_pipeline.py` (17: happy path + shared
    correlation, per-stage degradation, bounded revision, kill switch,
    evidence augmentation/dedupe), `tests/test_gateway_sage_tasks.py` (4),
    `tests/test_gateway_tracing.py` (+4: pipeline_span nesting/no-op/
    broken-tracer), `tests/test_sage_eval.py` (+4: deep arm judges the
    rebuilt context, gate7 verdict matrix).

---

## Phase 8 — Sage improvement loop (CLOSED 2026-11-28)

- [x] **8.1–8.4 The loop closes (2026-11-28, gate8 passed).**
  - **8.1 Exemplar memory** (`core/sage_exemplars.py`): bounded few-shot
    block between persona and context in BOTH modes — 2 newest ≥0.95
    replies (Q + condensed A), judge-flagged claims (≤2/row, 2 rows),
    ≤2 reader corrections. Budget `sage_exemplar_max_chars` 2500, kill
    switch `sage_exemplars_enabled`. Design note: exemplars teach answer
    *shape*, not grounding — no per-reply context copy stored
    (`sage_evaluations` has no context column; the live context is
    already in the prompt).
  - **8.2 Grounding guard** (`core/sage_guard.py`): the auto_remediation
    structure (enable flag, app_settings write-through state, action
    audit) PLUS the two things the summariser tuner lacks — a rolling
    window (shared `rolling_sage_faithfulness`, last 20) and automatic
    reversibility (trigger <0.8 with ≥10 judgments → strict mandate +
    temp cap 0.2; recover ≥0.85 → auto-revert; hysteresis prevents
    oscillation). Host: fire-and-forget after every judge insert in
    `record_sage_faithfulness`. Page-7 🛡️ toggle + status card; off by
    default (house pattern).
  - **8.3 Human feedback**: `sage_reply_feedback` table
    (`supabase_migration_sage_reply_feedback.sql`, public read/insert/
    update RLS), 👍/👎 + correction box on every reply (ratings ride the
    message dict through conversation persistence), Supabase-primary with
    local JSON fallback. Down+correction → "READER CORRECTION" negative
    exemplars. Page 8 shows counts + recent ratings.
  - **8.4 Regression gate**: `python -m core.sage_eval --gate8` — current
    macro-set mean must not regress (eps 0.02) the live rolling baseline
    (≥10 judgments or the gate refuses) NOR the previous gate8 snapshot;
    verdict → `sage_eval_latest` arm "gate8". Shipping rule documented in
    docs/SAGE.md §5.4/§9.
  - **The loop's first catch was its own instrument:** the live baseline
    sat at 0.307 while controlled gates scored 0.9+. Cause: the chat-time
    judge truncated context to `EVAL_SAGE_CONTEXT_CHARS`=8000 — contexts
    are ~35k chars chronological-ascending with evidence LAST, so the
    judge never saw what recent citations referenced (the same artifact
    the 6.2 harness patches around). Raised to 60000; live judging now
    matches the harness. Old rows wash out of the rolling window as fair
    judgments accumulate. Lesson: before closing a loop, audit the
    signal it feeds on.
  - **Gate (PASSED 2026-11-28):** gate8 current mean **0.925** (8/8) vs
    live baseline 0.307 (n=14, recovering) — PASS; no previous gate8.
  - Tests: `tests/test_sage_guard.py` (11: rolling window/mode filter,
    enablement, trigger/min-n/hysteresis/revert, audit write-through,
    history cap, never-raises), `tests/test_sage_exemplars.py` (9),
    `tests/test_sage_eval.py` (+4 gate8 matrix), `test_sage_agent.py` (+3
    mandate/temp-cap/exemplar injection/kill switch), `test_evaluator.py`
    (+2 guard hook), `test_supabase_client.py` (+4 feedback CRUD).

---

## Decision: tracing backend — Langfuse Cloud (Hobby) *(2026-10)*

> **Decision (2026-10): Langfuse Cloud (Hobby).** Alternatives recorded below for future reference; since instrumentation is OpenTelemetry-based, switching backends later means changing the exporter, not the code.

| Option | License | Cost to run | Ops burden | Fit |
|---|---|---|---|---|
| **Langfuse Cloud (Hobby)** ✅ chosen | MIT (cloud is SaaS) | Free tier (~50k units/mo, 30-day retention — verify current terms) | None | **Chosen default.** Single-user volume fits easily; prompt management + evals included |
| **AgentOps** | OSS | Free, self-host dashboard | Low | Runner-up: Python SDK with `agentops.init()` auto-instrumentation, session replay, per-session cost tracking. Best if we ever want fully local tracing with near-zero setup |
| **Arize Phoenix** | Apache-2.0 | Free, single container | Low | Self-host fallback if data must stay local; strongest RAG/retrieval-trace debugging (UMAP over embeddings) |
| **Langfuse self-hosted** | MIT | Free, but 6 services (web, worker, Postgres, ClickHouse, Redis, MinIO) | High | Only worth it on a paid VPS with real ops intent; overkill for this project today |
| LangSmith | Closed | Paid | — | Ruled out |
| Helicone | Free tier + OSS | Free | Low | Ruled out: proxy/gateway-first — would duplicate the routing/fallback layer `core/ai_gateway` already provides |

*Context: Langfuse was acquired by ClickHouse in Jan 2026; it remains open-source and self-hostable, but production self-hosting grew heavy (the 6-service stack above). Phoenix remains lightweight.*

---

## Decision: generation provider — Qwen Cloud replaces Ollama *(2026-11-11)*

> **Decision (2026-11-11): remove Ollama Cloud; make Qwen Cloud (`qwen3.8-flash`) the generation primary.** Ollama is no longer free, so it was dropped from the gateway entirely rather than left as a dead fallback.

**What changed**

- `OllamaCloudProvider` and every `OLLAMA_*` setting were deleted; `QwenCloudProvider` (`core/ai_gateway/providers/qwen.py`) calls QwenCloud's OpenAI-compatible `POST /chat/completions` with `Authorization: Bearer <key>`.
- The legacy synchronous `core/llm_client.py` was ported to the same API (chat `messages` in, `choices[0].message.content` out). Its quota probe tries `GET /models` first and falls back to a 1-token chat request when the deployment answers 404/405.
- Config: `QWEN_BASE_URL`, `QWEN_MODEL` (default `qwen3.8-flash`), `QWEN_API_KEY` (also accepts `DASHSCOPE_API_KEY`). The Token Plan endpoint is `https://token-plan.maas.qwencloudapi.com/compatible-mode/v1`; keys are plan/region-specific.
- Context window: `qwen3.8-flash` / `qwen3.8-max` are documented with a native 1M-token window (`QWEN_MODEL_CONTEXT_WINDOWS`).

**Routing**

| Task | Primary | Fallbacks |
|---|---|---|
| summarise / synthesise / project / sage_chat | qwen3.8-flash | gemini-3.6-flash, gemini-3.8-flash |
| categorise / extract / evaluate | gemini-3.5-flash-lite | gemini-3.8-flash, qwen3.8-flash |

The Gemini-first tasks stay that way by design: classification is cheaper on flash-lite, and keeping the judges Gemini-first means a Qwen outage cannot disable evaluation. Qwen took Ollama's former slots (primary where Ollama was primary, fallback where it was a fallback).

**Provenance compatibility**

- New summaries carry `qwen:<model>` and render a Qwen chip. The old `ollama:` chip mapping is retained only so summaries cached before this migration still render correctly; no runtime path emits it.

**Verification (2026-11-11)**

- Live direct provider call: `qwen3.8-flash` → `"OK"`, usage reported.
- Gateway `SAGE_CHAT` and a Qwen-pinned `CATEGORISE` (schema-validated JSON) both succeeded through the token-plan endpoint.
- Legacy `LLMClient.probe_quota_status()` / `is_available()` / `generate()` all succeeded.
- Full suite: 589 passed, 1 skipped (tracing tests excluded because `opentelemetry` was absent from the local venv).
