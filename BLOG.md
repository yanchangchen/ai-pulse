# AI Pulse Engineering Blog

Lessons learned building a multi-model AI news intelligence dashboard — one that fetches, classifies, summarises, and persists AI industry developments across 30+ sources with longitudinal memory.

---

## 2026-10-26 to 2026-10-27 — Phase 4.1–4.2: Making article coverage observable, and fixing the extractive ledger lie

**Problem**: The summariser processes only a ranked subset of classified articles per theme (capped at `MAX_ARTICLES_PER_SUMMARY = 12`), but until now, the skipped articles — the ones classified but never fed to the LLM — were invisible. No count, no list, no signal. They quietly re-competed each run inside their 14-day fetch window and then aged out. Worse, on extractive fallback (gateway failure or user-selected Non-LLM mode), the non-LLM path stamped *all* input articles as "processed" despite only surfacing ~3–5 by name. The backlog silently drained, and the LLM would never revisit those articles even after recovery — a ledger asymmetry that made any future coverage metric deceptive by construction.

**What happened**:

1. **Skip visibility (Phase 4.1).** Every summary — LLM, extractive fallback, or all-articles-already-seen skip — now records the **skipped complement**: `total_articles`, `included_count`, `skipped_count`, `skipped_hashes`, and a `skipped_articles` payload (title, source, link, hash) per article that was classified-for-theme but not included in the prompt. This data is computed at the point of truth — inside `generate_theme_summary_gateway` and `extractive_theme_summary` — and propagated into `_generation_log`, run metadata, and Supabase persistence.

   The UI surfaces this in three places: **Overview** and **Deep Dive** pages show an `"X of Y articles reflected (Z in backlog)"` caption beneath each theme brief, so coverage gaps are visible at a glance. The **History** page renders an expandable missed-article list per theme per run, so you can inspect exactly which articles were skipped and why they ranked lower. The **Quality Evaluation** Coverage judge was extended to record *which* articles were missed (not just aggregate counts), feeding directly into the 4.3 fair-drain priority queue.

   *Lesson: a metric you can't inspect is a metric you can't trust. The coverage judge was already computing aggregate recall — the gap was that nobody could see the named articles behind the number, so the number couldn't drive action.*

2. **The extractive ledger asymmetry fix (Phase 4.2).** Root cause in one line: `extractive_theme_summary()` did `included_hashes = [article_hash(a) for a in articles]` — every article passed in, regardless of whether it appeared in the summary text, was stamped as processed. With 35 articles and extractive fallback, all 35 were marked done while only ~3 lead articles and ~5 further-reading articles were actually cited. Result: 4.1's caption would read `"35 of 35 reflected (0 in backlog)"` — a lie.

   The fix has two parts. In `core/non_llm_summariser.py`, `generate_non_llm_theme_summary()` now tracks `_surfaced_articles` — the deduplicated union of the Section 1 lead articles (`top_articles`, ≤3) and the Section 6 further-reading pool (`reading_pool`, ≤5). In `core/summariser.py`, `extractive_theme_summary()` reads `_surfaced_articles` from the returned dict and builds `included_hashes` from only those. The remaining articles flow into `skipped_hashes` and `skipped_articles` — truthful numbers that match what the reader actually sees. The user-selected Non-LLM mode path now also calls `mark_processed()` with only the surfaced hashes.

   *Lesson: when a fallback path is "good enough to ship", its accounting must be as honest as the primary path. A silent `for a in articles` in a fallback is the kind of one-liner that makes every downstream metric fiction.*

**Also shipped**:

- 3 targeted Phase 4.2 tests in `tests/test_summariser.py`: `test_extractive_fallback_marks_only_surfaced_articles` (20 articles → ≤8 included, never 20), `test_extractive_fallback_surfaced_articles_are_in_summary_text` (every included hash has its title in `what_is_happening` or `further_reading`), and `test_non_llm_mode_backlog_carryover` (unsurfaced articles remain eligible via `filter_unprocessed`).
- Implementation plan for 4.2 documented in `docs/PLAN_PHASE_4_2.md` (Option B design with file-by-file steps) before coding, then marked complete post-implementation.
- 4.1's skip complement tests in `test_summariser.py::test_skip_complement_recorded_in_summary` (15 articles via gateway → `skipped_count ≥ 3`, payload lengths match).

**Result**: Coverage is observable — the UI shows exactly how many articles each theme brief reflects and which ones were left out. The extractive fallback no longer silently drains the backlog, so articles that missed the cut during a gateway outage remain eligible for full LLM synthesis on the next healthy run. **Phases 4.1 and 4.2 closed.**

---

## 2026-10-22 to 2026-10-26 — Phase 3: Dense vector embeddings on articles, and the pgvector gate

**Problem**: FTS on Postgres (`match_articles` via `tsvector`) solved exact keyword search, but it had a semantic blind spot. A user asking Sage about "reasoning token overhead" or "test-time compute trade-offs" would get zero hits if the source articles used different phrasing (e.g., "thinking budget" or "inference-time scaling"). Sage needed dense semantic retrieval to bridge conceptual synonyms, but on Supabase's Free Tier (500 MB DB, micro compute RAM), we couldn't blindly chunk raw web pages or blow RAM on unconstrained index graphs.

**What happened**:

1. **pgvector on articles (Phase 3.1).** We enabled `vector` in Postgres and added `article_embeddings` (`supabase_migration_article_embeddings.sql`) with 768-dimensional vectors matching `gemini-embedding-001`. Key design decisions:
   - **One embedding per article (for now)**: Because article summaries are curated and bounded (≤1500 chars), full-text chunking was deferred. `UNIQUE(article_id)` enforces a clean 1:1 relationship while preserving storage.
   - **Auditable provenance**: Every row stores `chunk_text` (the exact title + summary string embedded) and `embedding_model`, making vectors auditable and re-derivable if models rotate.
   - **Denormalized `run_date`**: Coalesced from `trend_runs` at write time so Phase 5's temporal bounding box can filter dates without costly table joins.
   - **HNSW index & invoker RPC**: Indexed with `vector_cosine_ops`. Nearest-neighbour lookup is exposed via `match_article_embeddings` with invoker rights (RLS public read policy handles access).

2. **Embedding through the Model Gateway (Phases 3.2–3.3).** Vector generation isn't an ad-hoc script — it is a first-class citizen in the gateway. A new `TaskType.EMBEDDING` routes through `core/embedding.py` to `GeminiProvider.embed()` (with fallback to Ollama `nomic-embed-text`). Requests carry token estimates, respect batching limits (100 texts/call), log into `llm_calls`, and emit OTel spans to Langfuse. Ingestion hooks in `core/bg_refresher.py` automatically embed new articles at pipeline run, so the index stays fresh without manual steps.

3. **The database-as-cursor backfill and the Phase 3 gate (Phase 3.4).** We created `backfill_embeddings.py` backed by a `get_unembedded_articles` SQL RPC. Instead of fragile local cursor files or offset pagination, the database is the state machine: a `LEFT JOIN` excludes articles with existing embedding rows. If a backfill is interrupted, re-running simply resumes where it left off.
   - **The Phase 3 Gate (`core/retrieval_eval.py`)**: To gate Phase 5 before writing hybrid retrieval code, we benchmarked dense retrieval against a hand-labelled question set (`data/eval_retrieval_questions.json`). The eval embeds questions, executes `match_article_embeddings`, and measures Recall@10 against expected conceptual keywords. Running against the backfilled live database scored a **mean Recall@10 of 0.510 across all 8 test questions**, automatically persisting a durable baseline record into Supabase's `app_settings` under `retrieval_eval_latest`.

**Also shipped**:

- 12 unit tests in `tests/test_embedding.py` covering embedding token estimates, gateway dispatch bypass of prompt templates, batching, RPC normalization, and backfill loop guards.
- Verified live on Supabase: 100 historical articles embedded with zero rate-limit errors, RPCs healthy (HTTP 200), and all embedding calls fully traced in Langfuse with `llm_calls` audit records.

**Result**: Articles in Supabase now carry 768-dim dense vector embeddings alongside their sparse FTS vectors. With dense retrieval verified and gated at Recall@10 = 0.51, the storage half of Pillar A is complete. **Phase 3 closed.**

---

## 2026-10-08 to 2026-10-12 — Phase 2.3: keyword matching moves into Postgres, and Sage learns to answer first

**Problem**: Two gaps. Retrieval: Sage's article evidence drew its candidate pool from the newest 300 rows — anything relevant-but-older was invisible, and the Python scorer wasn't word-aware (no stemming, no ranking, `LIKE`-style substring thinking). Answering: the persona forced every reply to open as a chronological walk, so a direct factual question ("what did Anthropic ship in September?") buried its answer under a timeline.

**What happened**:

1. **Full-text search on `articles` (Phase 2.3).** For anyone new to Postgres FTS, the mechanics in one breath: each row's text folds into a `tsvector` — a normalised list of word *stems* (`"Models are running"` → `'model':1 'run':3`, plurals stripped, verb forms merged); the question folds the same way into a `tsquery` (`websearch_to_tsquery('english', ...)`, which parses raw user text safely); the `@@` operator matches query against vector; a GIN index turns that match into an index lookup instead of a table scan; and `ts_rank()` scores how well each row matched, so ordering happens in the database. Concretely: `supabase_migration_fts_articles.sql` adds a `search_vector` GENERATED ALWAYS column (title at weight A, summary at B — the existing "title counts double" client-side semantics, expressed in SQL) with a GIN index, plus a `match_articles` RPC returning matches ranked by `ts_rank`. PostgREST's built-in `fts` filter can't `ORDER BY ts_rank`, so a function is the standard Supabase pattern for ranked retrieval. `GENERATED ... STORED` backfills every existing row on ALTER — no backfill script. Design kept from 2.1: SQL now both bounds *and* matches the candidate set, but final top-K judgement stays client-side (`score_article_relevance` unchanged) where it's testable and provider-agnostic. The RPC degrades to the old newest-first window on failure or empty match, so pre-migration databases and no-match questions keep working, and the local history.json fallback (no Postgres → no FTS) is untouched. *Lesson: move matching to where the index is, keep judgement where the tests are.*

2. **The prompt rework: answer first, evidence second.** The response format became three steps — **Direct Answer** (1-3 sentences, no preamble; a narrow factual question can be just this plus citations) → **Evidence** (the chronological account with citations, kept for trend-shaped questions) → **My read on this**. New grounding rules: partial evidence yields a *partial answer with its stated scope* (which dates/themes the context covers) instead of a bare refusal; runs that disagree get surfaced with dates, never smoothed away; concrete numbers/names/dates beat vague characterisations; and the pre-filtered ARTICLE EVIDENCE section is for specific claims while WIKI CONTEXT carries the multi-run arc. Every 2.1/2.2 invariant survived — context-only claims, citation formats, hyperlinked links, "never invent URLs" — which is the interesting part: *groundedness lives in the grounding rules, not in the format order. Reordering the format for readability cost nothing in faithfulness.*

3. **The Phase 2 gate: a chat-time faithfulness judge (closing the phase).** Each successful Sage reply is now judged in the background — one `EVALUATE` call scoring the answer against the exact wiki/article context it was given, fire-and-forget on a daemon thread, one `sage_evaluations` row per reply, rendered on the Quality Evaluation page (rolling mean, per-reply trend, and the actionable payload: recent unsupported claims). The design constraint dictated the timing: the context string and the answer coexist *only at the moment of reply* — conversations persist messages but not context, and rebuilding it later would judge against a drifted context. *Lesson: judge what you actually fed the model, at the moment you fed it.* Failure semantics follow the summary judges: judge outages skip the row entirely rather than persisting a zero, so a judge outage can never masquerade as mass hallucination in the trend chart. Honest caveat, carried over from the recorded sequencing decision: there is no pre-2.1 baseline — judge scores start from post-2.3, and that batch is what Phase 3 (embeddings) gets measured against.

**Also shipped**:

- 514 tests, all offline — the FTS path is tested by pinning the `match_articles` RPC call and its graceful degradation to the window fetch; the prompt invariants are string-asserted against `SAGE_SYSTEM_PROMPT`; the judge tests cover parse/infra failures, thread gating, and row skipping.
- **A dark-process fix in gateway tracing.** Symptom: today's `sage_chat` rows landed in `llm_calls` with NULL `trace_id` — no traces in Langfuse — while keys, region, OTLP ingestion, and the span→generation mapping all verified healthy from fresh processes. Root cause: the first `_configure()` failure in the app process was *memoized for the process lifetime* and logged at DEBUG, so one transient startup hiccup (an import or settings blip) silenced tracing for the whole run with no visible symptom. Fix: a failed configuration returns False for that call but leaves the gate un-memoized — the next gateway call retries — and failures log at WARNING. *Lesson: "best-effort observability" still needs a broken-effort signal; a silent cache of a transient failure turns best-effort into no-effort.*

**Result**: Sage's candidate pool is now every keyword-matching article in the table, ranked in the database — not the newest 300 — replies open with the answer instead of a preamble, and every reply quietly grades its own groundedness into a durable baseline. **Phase 2 closed.**

---

## 2026-10-01 to 2026-10-07 — RAG foundations: Sage joins the gateway, gets traced, and reads articles

**Problem**: `Future.md` lays out the path from prompt-stuffed Sage to a time-aware hierarchical RAG system, but three foundations were missing. Sage still called the LLM client directly, bypassing the gateway's fallback chain, health tracking, and provenance. Nothing anywhere recorded token usage — provider responses carried it and we threw it away. And Sage's only grounding was theme summaries, so a question like "what did that Mistral release actually say" had no article-level evidence to draw on.

**What happened**:

1. **Sage joined the Model Gateway (Phases 0.1–0.2).** A new `SAGE_CHAT` task type with an Ollama-primary policy (matching Sage's old engine) and Gemini fallbacks — but *no* deterministic fallback, because a rule-based stand-in for Sage's analyst voice would fabricate an "analysis". `chat_with_sage` now returns `{"response", "provenance"}` and the chat UI shows the engine chip. `Provenance` gained `tokens_in/out` (parsed from provider usage that was previously discarded), and every completed gateway execution — success, deterministic fallback, or total failure — lands as a row in a new fire-and-forget `llm_calls` table. The project went from zero cost accounting to durable per-call records in one step.

2. **OTel tracing to Langfuse (Phase 0.3).** One span per gateway execution, exported OTLP/HTTP to Langfuse Cloud, env-gated to a silent no-op when keys are absent — the same "observability must never reduce availability" contract as the call log. Three war stories worth recording:
   - **The live gate check caught what the unit test couldn't.** `trace_id` was stamped onto provenance *after* the fire-and-forget row insert had already snapshotted it — rows went out NULL. The test passed because it compared the same mutated object after the fact. Fix: stamp the trace id on the request the moment the span opens, before routing starts; the regression test now snapshots the value *at call time*. *Lesson: a test that inspects an object after the fact can pass while the value was wrong at the moment that mattered.*
   - **401 from Langfuse meant "wrong region", not "bad keys".** The project lives in the US region; the SDK default host is EU. Probing both endpoints with the same keys (EU → 401, US → 200) settled it in seconds. *Lesson: rotate keys last, probe endpoints first.*
   - **Free-tier discipline.** Traced input/output capped at ~2000 chars (Hobby meters units), `BatchSpanProcessor` so export never blocks a task, and an autouse conftest pin so real keys in `secrets.toml` can never make a test export spans — mirroring the existing Supabase offline pin.

3. **Article-level grounding (Phase 2.1).** `build_wiki_context` now packs a second layer into its char budget: a bounded newest-first window of articles (`get_articles_for_context`, filters in SQL but *no keyword matching there* — proper FTS is Phase 2.3), scored client-side against the question's keywords (title tokens ×2), with the top 15 rendered as an `=== ARTICLE EVIDENCE ===` section within a 30% budget slice. Unused budget flows back to the summaries. When nothing matches any keyword, selection degrades to newest-first — Sage always has *some* Tier-1 evidence. Local fallback flattens `themed_articles` from `history.json` (data that existed all along and Sage never read). *Lesson: SQL should bound the candidate set; judgement about relevance belongs client-side where it's testable and provider-agnostic.*

4. **Citations and evidence popovers (Phase 2.2).** Each article brief now carries a `Link:` line into the prompt, and the persona tells Sage to attach those links to article citations as Markdown hyperlinks — "never invent URLs" is part of the instruction, because a model that hallucinates a URL is worse than one that cites nothing. The rendered briefs also flow back out as `build_wiki_context()["articles"]`, and the chat tab shows a `📰 Article Evidence (N)` accordion under every reply: clickable title, source, date, and the exact snippet Sage saw. Two details worth recording:
   - **Scraped URLs are untrusted input.** Article links come from RSS feeds and can be anything a feed contained, so `_safe_link` whitelists http(s) before a URL can reach the prompt or render as a hyperlink — a `javascript:` string in `articles.link` becomes `None`, not a click. *Lesson: any value you're about to turn into a clickable link or put in an LLM prompt deserves a scheme check, regardless of where it "came from".*
   - **Evidence rides on the message.** The accordion persists because the `articles` list is stored as an extra key on the assistant message dict — Supabase JSONB and the local JSON cache both pass unknown keys through untouched, so popovers survive conversation reloads without any schema change. Titles get bracket-escaped so a `[` in a headline can't break the Markdown link syntax.

**Also shipped**:

- Roadmap re-phased (`Future.md`, 2026-10 revision) into small independently-verifiable increments with explicit gates per phase.
- A sequencing decision recorded honestly: the Sage faithfulness judge (the Phase 2 gate) is deferred as its own task, so there is *no pre-2.1 baseline* — judge scores will start from post-2.1. Trading a clean before/after comparison for shipping speed, knowing the old commit is still there if we ever want the comparison.
- 493 tests, all offline — including one that exists specifically to pin the Mock-vs-real-data boundary (a non-list Supabase return must be ignored, not crash the builder).

**Result**: One Sage chat now produces a Langfuse trace rendered as a generation (model, tokens, latency), a durable `llm_calls` row linked by `trace_id`, and an answer grounded in 15 keyword-relevant article briefs alongside 300 cross-run summaries — with article citations now clickable in the reply itself and inspectable in a per-message evidence accordion.

---

## 2026-09-17 to 2026-09-30 — Sage grows a real memory

**Problem**: Sage, the AI analyst persona, was confidently answering questions about "the latest runs" — but was silently reading the *oldest* data in the window. A user asking about September activity got July answers, with no indication anything was wrong. On top of that, long conversations had no persistence: closing the tab meant losing the thread entirely.

**What happened**:

1. **The oldest-first pagination bug.** `get_summaries_across_runs` in `core/supabase_client.py` was returning pages in ascending timestamp order — oldest first — and then truncating to the context-window limit. The result: Sage always read the *earliest* slice of whatever date window was selected, not the most recent. Fixed by adding `prefer_recent=True` and enforcing `ORDER BY run_date DESC` before the limit is applied. *Lesson: "give me the last N rows" and "give me the first N rows, sorted ascending" are the same query unless you're deliberate about it.*

2. **No scope controls in the chat UI.** Users had to know SQL or restart the app to change what period Sage was looking at. Added a "Research Scope & Historical Filters" expander directly inside the chat interface (`pages/5_History.py`) with one-click presets (Last 7 days, Last 30 days, Current Month) and a custom date-range picker. Theme isolation is also exposed here, letting users pin Sage to a single topic (e.g. "LLM Infrastructure") for focused Q&A. *Lesson: scope controls belong next to the thing being scoped, not buried in settings.*

3. **Conversation persistence via Supabase.** Chat threads were living entirely in `st.session_state` — ephemeral, tab-scoped, gone on refresh. Added four methods to `SupabaseManager` (`save_sage_conversation`, `get_sage_conversations`, `get_sage_conversation_by_id`, `delete_sage_conversation`) backed by a dedicated `sage_conversations` table (see `supabase_migration_sage_conversations.sql`). The integration follows the same resilient-fallback pattern used elsewhere: if the table migration is pending, the system falls back to `sync_metadata` with key `sage_conversations_store` and logs a warning — zero downtime, zero data loss. *Lesson: make the happy path use Supabase and the fallback path obvious; never silently drop data.*

**Also shipped**:

- `AGENTS.md` — a standalone directive for AI coding harnesses codifying the "Fast Inner Loop / Full Regression" testing policy: targeted tests (`< 0.5s`) during active development; full `pytest tests/` only as a final sanity check before PRs.
- `CLAUDE.md` updated with the same targeted-testing table so both human and AI contributors know which test file maps to which component.

**Result**: Sage now reads the *right* data, users can slice by period and theme without leaving the chat, conversations survive page refreshes, and AI harnesses working on this repo no longer burn 90 seconds on a 444-test suite for every small edit.

---

## 2026-09-11 to 2026-09-16 — Silent fallbacks and the cost of failing quietly

**Problem**: Three production issues in one week, all sharing the same root pattern — the system was failing *quietly*. Fallbacks did their job so well that nobody knew the primary path was gone.

**What happened**:

1. **The zero-provider gateway.** `ModelGateway._init_providers()` read API keys straight from `os.getenv`, but our deployments keep them in `secrets.toml`. The gateway started with zero providers, every task silently landed on the deterministic fallback, and the only clue was a wall of "Non-LLM" provenance chips. Fixed by routing provider init through `config.settings` (`st.secrets` → TOML parse → env vars). *Lesson: a fallback is for outages, not misconfiguration — if degradation is silent, a setup bug can run for weeks unnoticed.*

2. **HTTP 200, body "Unable to generate summary."** Some LLM responses returned success status codes with failure text inside. The summariser stored them as valid briefs. Now every parsed section is checked against known failure/refusal strings, and any theme that trips the check is re-routed through the extractive engine. *Lesson: validate content, not status codes.*

3. **The circuit breaker that never reset.** A transient Ollama Cloud slowdown (60s default timeout) latched Nemotron `unavailable` after 5 consecutive failures — for the entire process lifetime, with no logs. It kept losing its primary slot to `gpt-oss:120b-cloud` long after recovering. Fixed with: timeout raised to 180s, WARNING logs on *every* fallback transition, half-open retry after 300s (1 success fully restores, 2 failures re-latch), and `health_check_all()` clearing latched state without a restart. *Lesson: a circuit breaker without auto-recovery is just a permanent outage with extra steps.*

**Also shipped**:

- **Run deduplication + processed-article tracking.** The fetcher now assigns `content_hash` at ingest; a `processed_articles` tracker (Supabase with local JSON fallback) means the summariser only spends LLM tokens on articles it hasn't summarised before, and runs whose fingerprint duplicates one from the last 30 minutes are skipped entirely.
- **Learned keywords survive deployments.** Custom keyword overlays — the output of the classifier's self-improvement loop — now persist to Supabase first with local JSON as cache, so a read-only deployment filesystem no longer wipes them.
- **Singapore time everywhere.** UI timestamps are interpreted as UTC and displayed in Asia/Singapore (UTC+8) instead of server-local time.

**Result**: Fallbacks still exist — we rely on them — but every degradation now leaves a WARNING trail, a provenance chip, and (for health latches) an automatic path back to the primary model.

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
