# AI Pulse Engineering Blog

Lessons learned building a multi-model AI news intelligence dashboard — one that fetches, classifies, summarises, and persists AI industry developments across 30+ sources with longitudinal memory.

---

## 2026-11-27 — The 139-minute "success": why wait_for is not a timeout

**Problem**: During the 4.4/4.5 gate experiments, two Gemini fallbacks logged `success=true` with latencies of 702s and 8329s — a 2.3-hour "successful" call. The provider already wrapped every SDK call in `asyncio.wait_for(..., 30s)`. The timeout existed; it just didn't bind.

**What happened**:

1. **The mechanism**: `asyncio.wait_for` cancels the inner task on timeout and then *awaits its completion*. The google-genai SDK retries connection/DNS failures through tenacity + aiohttp, and cancellation during DNS-executor resolution gets deferred — so the cancelled task keeps running, and `wait_for` patiently waits for it. The deadline becomes a suggestion. (The latency column measures from gateway-execution start, which is why the row also absorbed the preceding Qwen timeouts.)

2. **The fix, two layers**: the provider now uses `asyncio.wait({task}, timeout=...)` and on expiry cancels best-effort and **abandons** the task — the caller returns on time no matter what the SDK does with cancellation. And the SDK itself is bounded at construction: `HttpRetryOptions(attempts=2, max_delay=2s)` plus `HttpOptions.timeout` so abandoned tasks die on their own.

3. **The unit trap, caught by a test**: `HttpOptions.timeout` is **milliseconds**. Passing the seconds value (30) would have meant a 30-millisecond timeout — every real Gemini call failing instantly. A construction test asserting `timeout == 30_000` caught it before any live traffic; a live sanity call then returned in 1.3s.

*Lesson: a timeout is only as strong as its cancellation path. If the callee can defer cancellation, wrap-with-wait_for is not a deadline — use wait-and-abandon, bound the callee's own retries, and test the deadline with a coroutine that deliberately defers cancellation. And read the unit of every timeout field you set: seconds-vs-milliseconds is a silent 1000× bug.*

**Result**: Gemini fallbacks are now bounded at ~30s wall-clock regardless of DNS/network pathology; 5 regression tests (`tests/test_gemini_timeout.py`), gateway suite green, live call verified.

---

## 2026-11-27 — Phase 4.5: map-reduce, or how to compress without forgetting

**Problem**: 4.4's audited gate left two honest residuals: pools over the ceiling wait (capacity gap), and a 50-article brief names only ~61% of what it's fed (naming gap). One call cannot compress 85 stories into 250 words without forgetting most of them. The roadmap's answer: never ask it to.

**What happened**:

1. **MAP at the size where memory works.** The ranked unprocessed pool is sliced into batches of 12 — the size where the 4.4 gate measured ~93% naming — and each batch gets the *unchanged* gateway brief, three at a time. Small batches name their articles; that is the entire trick.

2. **REDUCE under a preservation mandate.** One call merges the batch briefs into the theme's normal five sections, instructed to compress wording never coverage: every development any batch named must survive, further-reading lists merge whole. Two guards make the mandate enforceable: the output token budget scales with batch count (the default 2200 would have truncated the merged reading list mid-entry — caught before the gate, regression-tested), and a failed reduce degrades to a deterministic concatenation of the batch briefs, so names survive even total reduce failure.

3. **The ledger stays truthful.** `included` is the union of batch-fed hashes — every marked-processed article genuinely appeared in a prompt. Skipped, waiting, and times-seen semantics from 4.1/4.3 carry over untouched. Cost is bounded at 8 batches/theme/run; beyond that, articles wait and age up rather than exploding spend.

4. **The gate cleared the threshold 4.4 left open.** Same pool, arm D: coverage **38.4% (legacy) → 54.9% (cap 50) → 74.1% (map-reduce)**, past the ≥70% bar; processed **92%** of the pool; missed articles 157 → 115 → 66; naming rate back to ~75%. Cost: 66.6k input tokens across 26 calls, zero failures — ~3× a single pass, paid only by overflow themes, so steady-state days mostly stay single-pass. Phase 6.1's gate (no incomplete Tier 2 under monthly rollups) is satisfied.

*Lesson: compression is safe at the size where the model can still name everything; the engineering is in making the merge lossless — by mandate, by budget, and by a fallback that cannot forget. Measure the merge, not just the parts: a map-reduce pipeline is only as faithful as its reduce step.*

**Residuals, honestly**: 66 pool articles still unnamed (reduce compresses somewhat), 23 beyond the batch bound waiting via aging. The next levers are prioritisation (4.6) and retrieval (Phase 5), not more capacity.

**Result**: Phase 4.5 closed; Phase 4's input-side work is complete except optional 4.6. Next: **Phase 5 — hybrid retrieval + temporal awareness**.

---

## 2026-11-27 — Special entry: the day we audited our own instruments

*A retrospective, not a phase log. One session closed the Cloudflare watch, resurrected a lost eval set, shipped two UI pages and Phases 4.3–4.4 — and caught three of our own instruments lying before any of it shipped.*

**Lie 1 — the receipt without the asset.** The Phase 3 retrieval gate's baseline (Recall@10 = 0.833) survived in `app_settings`, but the eight questions that produced it lived in gitignored `data/` and were gone. A score in a database is a receipt; the questions are the asset. We recreated the set (24 questions, curated against the live archive, new baseline 0.917), gave it a git-tracked home with a `.gitignore` exception, and built the Eval Sets page so every future eval set is visible by construction. *Rule adopted: anything a gate depends on lives where git can see it.*

**Lie 2 — the metric that ate the backlog.** The 4.4 gate experiment first reported that raising the article cap *destroyed* coverage (98% → 66%). Impossible numbers deserve an audit, not a decision: `coverage_judge` joined every value of the summary dict, and since 4.1 that dict carries `skipped_articles` — the backlog payload with every skipped title. The judge was scoring the backlog list as brief prose; arm A "covered" 192 articles it was never fed. With narrative-only sections the gate reversed to a honest pass (38.4% → 54.9%). Production had been safe by accident (the evaluator loads only narrative columns); the judge now whitelists prose sections so safety is by construction. *Rule adopted: a recall metric must whitelist what counts as text; metadata carrying titles is a leak waiting for a caller.*

**Lie 3 — the stale fact in the roadmap.** Future.md still described a ~59k-char prompt budget from the Ollama era; on Qwen's 1M window the budget is 1.8M chars and the flat 12-cap used ~0.3% of it. The phase premise ("the context window is not the constraint") was right for the wrong reason. Re-verified numbers replaced the folklore before 4.4 was designed. *Rule adopted: situational facts in a roadmap carry a verification date, and get re-measured when the stack under them changes.*

**Process footnotes.** The permission classifier timed out repeatedly mid-session; a "manual commit" reported by a human never landed in the repo (reflog showed no attempt) and was caught by checking rather than trusting; and two Gemini fallbacks hung 12 and 139 minutes because that provider has no effective timeout bound — queued as a reliability fix. None of these changed the engineering, but all of them changed what we verified: *reports are claims; reflogs, call logs and re-measurements are evidence.*

**What stands at the end of the day**: watch closed (Cloudflare healthy, no Option C), eval set loss-proofed and displayed, fair drain live (backlog drains or gets a death certificate), capacity raised with an audited gate, and a coverage judge that cannot eat metadata. Next: 4.5's map-reduce briefs, to close the naming gap the gate honestly left open.

*Lesson: the most dangerous failures in a measured system are not crashes — they are instruments that keep reporting confidently while measuring the wrong thing. Audit the instrument before you obey the gate.*

---

## 2026-11-27 — Phase 4.4: raising the cap, and the metric that lied about it

**Problem**: The summariser hard-capped each theme at 12 articles while the Qwen prompt budget sat at 1.8M chars — 12 blocks use ~0.3% of it. The cap, not the context window, was the sole capacity constraint, and bursty themes accumulated backlog faster than 12/run could drain.

**What happened**:

1. **The cap became a ceiling, not a guillotine.** `max_articles_per_summary` (default 50, via `get_summariser_settings()`) replaces the flat 12 at all four pipeline sites; the existing char-budget fill stays as the secondary guard. Per-article truncation became a knob (`article_summary_chars`, default 300). The on-demand Gemini Deep Dive keeps its own 75-cap.

2. **The gate experiment said "no" — and was wrong.** Controlled same-pool arms (legacy 12 vs cap 50 vs cap 50 + 500-char summaries), judged by the deterministic Coverage judge: arm A scored 98%, arm B 66%. Raising capacity appeared to *destroy* coverage. Suspicious: a 12-article brief cannot mention 248 of 255 pool articles. The leak: `coverage_judge` joined **all** summary-dict values, and since 4.1 the dict carries `skipped_articles` — the backlog payload *with every skipped title*. The judge was reading the backlog list as brief prose. Arm A "covered" 192 articles it was never fed. Production was unaffected (the evaluator loads only narrative columns), but the experiment wasn't.

3. **With narrative-only sections, the gate reversed to a pass.** True coverage: **38.4% → 54.9% (+16.5pp)**; processed fraction 22% → 81%; input tokens ~1.7×. The 500-char truncation arm added +1.6pp — within single-run noise at higher cost, so 300 stays. `coverage_judge` now whitelists narrative sections (`_NARRATIVE_SECTION_KEYS`) with regression tests, so no future caller can repeat the leak.

4. **Honest residuals.** Per-fed-article naming rate drops 93% → 61%: a 250-word brief compresses 50 articles harder than 12. Total named stories still rises (98 → 140), which is what the pool-recall gate measures — but the compression is real, and 4.5's per-batch map-reduce briefs are the designed remedy. The 70% absolute Coverage threshold stays open until then. Also logged: two runs hit Qwen timeouts followed by Gemini fallbacks hanging 12–139 minutes (DNS failures, no effective timeout bound on the Gemini provider) — a reliability fix of its own, later.

*Lesson: before trusting a gate, audit what the metric actually reads. A recall metric that concatenates "all values" of a rich dict will eventually eat metadata — and a backlog payload full of titles is the most flattering metadata imaginable. Whitelist prose; make the leak impossible, not just unlikely.*

**Also shipped**: `tests/test_capacity.py` (6 tests) + `tests/test_coverage_hygiene.py` (3); Future.md's stale Phase 4 budget facts corrected (1.8M chars, not ~59k); ARCHITECTURE/QUALITY_EVALUATION/ROADMAP_HISTORY updated; gate runner tracked as `eval_capacity_gate.py` (re-runnable for 4.5), results in `scratch/eval_4_4_results.json`.

**Result**: Phase 4.4 closed with a passing, audited gate. Next: **4.5 — chunked overflow summarisation**, which closes the remaining coverage gap and gates Phase 6.

---

## 2026-11-27 — Phase 4.3: the backlog now drains or gets a death certificate

**Problem**: The summariser feeds each theme only unprocessed articles, ranked by keyword relevance and hard-capped at 12. An article that loses the ranking re-competes next run while inside the 14-day fetch window — and otherwise ages out *silently*. Nothing counted how long an article had been waiting, and nothing recorded the ones that never made it. Coverage was observable (4.1) but not fair.

**What happened**:

1. **A times-seen counter on `articles`.** `times_seen` counts runs classified-but-not-summarised, reset to 0 on inclusion. The interesting constraint: PostgREST upserts can't express per-row increments, and a partial-column upsert would insert title-less rows for articles whose first save happens *after* summarisation. So the counter is maintained by an UPDATE-only RPC, `record_article_seen(theme, waiting[], included[])` — atomic, insert-proof, one call per theme per run. Offline and pre-migration deployments fall back to `data/times_seen.json`, same Supabase-preferred pattern as the processed ledger.

2. **An aging boost in the shared ranking.** `_rank_articles_by_relevance` now adds `aging_boost_per_run × times_seen` (default 2.0, configurable through `get_summariser_settings()`, 0 disables) to the keyword-relevance score all three engines share. Waiting articles rise monotonically: a zero-relevance article overtakes a score-14 fresh one after seven runs, comfortably inside the 14-day window at daily cadence.

3. **Waiting ≠ skipped.** The 4.1 skipped complement includes already-processed articles (they're "not reflected" but not waiting). The fair-drain set is `unprocessed − surfaced`, computed on all three orchestrator paths and stored as `waiting_hashes` on each summary + generation log. Getting this distinction wrong would have inflated the counter with articles that can never drain.

4. **Aged-out articles get a death certificate.** At persistence, `_record_aged_out()` diffs the previous run's waiting hashes against the current classified set. Anything that departed — window expiry or a feed dropping it — is recorded as `aged_out_articles` with title, source, link, hash, and last-seen run, in history.json and the Supabase generation log, and rendered in History's 🍂 expander. If the article returns later it re-competes as unprocessed; the record stays as audit trail. Silent loss is now structurally impossible: drain, or document.

*Lesson: "observable" and "fair" are different properties. 4.1 made the gap visible; making it close required a state variable the pipeline never had (how long has this been waiting?) and an explicit terminal state for the ones that never close. A backlog without both is a queue that forgets.*

**Also shipped**: `supabase_migration_times_seen.sql` (column + partial index + RPC); History page 🍂 expanders in both local and Supabase modes; 17 tests in `tests/test_fair_drain.py`; ARCHITECTURE/SUPABASE/PAGES docs and the roadmap updated. The migration ships separately from 5.4's `theme_first_seen` — 4.3 needs the counter now.

**Result**: Phase 4.3 closed. Next: **4.4 — budget-aware article cap** (replace the flat 12 with prompt-budget filling, eval-gated on coverage).

---

## 2026-11-27 — Closing the Cloudflare watch, resurrecting the lost eval set, and giving evals a UI

**Problem**: The Phase 3.6 14-day metrics watch on the Cloudflare embedding migration had matured, but re-running its retrieval-quality gate was impossible: the hand-labelled question set (`data/eval_retrieval_questions.json`, the 8 questions behind Recall@10 = 0.833) lived in gitignored `data/` and was gone — from disk, git history, and scratch. The same set gates Phase 5's old-vs-new retrieval comparison, so losing it blocked two phases, not one.

**What happened**:

1. **The watch closed healthy on every operational gate.** Steady-state (Nov 11–26, backfill burst excluded): 1–3 embedding calls/day and 0.9k–13.2k tokens/day against an estimated 1–2M tokens/day free (~1% headroom); 28/28 calls succeeded; p95 latency 387–1,794 ms; backlog 0 with the index grown from 6,304 to 7,006 articles (~41/day, all embedded); 100% `@cf/baai/bge-base-en-v1.5` at exactly 768 dims. Every failure in the window was a 429 from the pre-migration Gemini era or the one-off backfill day. **Option C (Render) is not triggered.**

2. **The eval set was recreated, not recovered.** `llm_calls` stores no input text, so the original questions were unrecoverable from the audit trail (and the hand-curated keyword labels wouldn't have been there anyway). Instead: 24 new questions curated against the live archive, covering all 7 themes, each with expected keywords verified present in the target article's actual `chunk_text` and a `target_title` provenance field. New home: **`eval_sets/retrieval_hit_rate.json`, tracked in git** — the loss mode is structurally impossible now.

3. **New baseline: Recall@10 = 0.917** (24/24 scored). 21 questions at 1.00; three honest weak spots kept as hard cases rather than curated away: SOCI container cold-start (0.00 — the one total retrieval miss), OpenAI Lockdown Mode (0.33), and the Anthropic IPO title-only chunk (0.67). The old 0.833 is not directly comparable — different questions — so 0.917 becomes the Phase 5 gate baseline. The `app_settings` snapshot now carries `set_id`, `ran_at`, and per-question scores.

4. **Eval sets got a UI, and a registry.** `core/eval_sets.py` discovers any `eval_sets/*.json` matching a documented schema; `pages/9_Eval_Sets.py` renders them with **20 questions per page** (Previous/Next + page indicator), showing each question's ground-truth keywords, target article, notes, and latest per-question score. Future eval sets — faithfulness, QA, anything — appear on the page automatically by dropping in a JSON file.

5. **The blog got a page too.** `pages/10_Blog.py` + `core/blog_index.py` render BLOG.md newest-first with case-insensitive AND search over entry titles and bodies, so "what did we do about X" is a text box, not a scroll.

*Lesson: an eval set is code, not runtime data. Anything a gate depends on must live where git can see it — a baseline number in a database is a receipt, but the questions that produced it are the asset. And a metric you can't re-measure is a metric you've already lost.*

**Also shipped**: `core/retrieval_eval.py` now loads through the registry; 19 new tests (`tests/test_eval_sets.py`, `tests/test_blog_index.py`); sidebar nav gains 🧪 Eval Sets and 📓 Dev Blog; Future.md 3.6 checked off with details moved to ROADMAP_HISTORY.md.

**Result**: Phase 3 is fully closed including its contingent watch. Next unchecked roadmap item: **Phase 4.3 — fair drain / backlog carryover**.

---

## 2026-11-10 to 2026-11-11 — Ollama goes paid, and Qwen Cloud takes the generation stage

**Problem**: Ollama Cloud stopped being free, and it was still the primary engine for `summarise`, `synthesise`, `project`, and Sage chat. Keeping it as a dead fallback would leave the routing table lying about capacity; removing it without a replacement would collapse the generation path onto Gemini alone, losing provider diversity and putting cost and availability in one vendor's hands.

**What happened**:

1. **A new adapter, not a re-pointed URL.** `QwenCloudProvider` (`core/ai_gateway/providers/qwen.py`) speaks QwenCloud's OpenAI-compatible `POST /chat/completions` API with `qwen3.8-flash`, a native 1M-token context window, and Bearer auth. Nothing about Ollama's `/api/generate` shape survived.

2. **Ollama removed completely.** The provider, `OLLAMA_*` settings, registry entries, fallback slots, and `test_ollama_timeout.py` are gone. A provider that still exists in config but can never be reached is worse than no provider — it hides missing-credential failures and makes every routing table suspect.

3. **Routing kept its design intent.** Qwen became primary exactly where Ollama was primary: `summarise` / `synthesise` / `project` / `sage_chat` → `qwen3.8-flash`, with Gemini as fallback. Gemini remains primary for `categorise` / `extract` (cheap flash-lite classification) and `evaluate` (a Qwen outage must not disable the quality judges), with Qwen now occupying Ollama's old fallback slot there.

4. **The legacy synchronous path had to move too.** `core/llm_client.py` was still the shared client for the classifier's legacy path, the summariser's compatibility path, and process-wide quota state. Leaving it on Ollama's JSON shape would have been a second, silently stale integration — it now sends chat `messages`, reads `choices[0].message.content`, and probes `GET /models`, falling back to a 1-token chat request when the deployment answers 404/405.

5. **Provenance stayed honest across the migration.** New summaries emit `qwen:<model>` and render a Qwen chip. The old `ollama:` chip mapping is retained only so summaries cached before the switch still render correctly — no runtime path emits it anymore.

6. **The endpoint is plan-specific.** This deployment uses a Qwen Cloud Token Plan, whose base URL is `https://token-plan.maas.qwencloudapi.com/compatible-mode/v1`, not the pay-as-you-go regional endpoint. A stale `QWEN_BASE_URL` in `secrets.toml` produced a `401` even though the key itself was valid; moving the URL to token-plan fixed it.

*Lesson: a provider migration is not finished when the new adapter returns 200. It is finished when the old provider is gone from the registry, settings, tests, and docs; when the legacy client has moved too; and when historical provenance still renders. Otherwise the "removed" provider survives in a code path nobody tests until the day it matters.*

**Also shipped**: Installed the `opentelemetry-sdk` / `opentelemetry-exporter-otlp-proto-http` packages that `requirements.txt` had always listed but the local venv lacked, so the existing Langfuse keys finally activated tracing. A live gateway call produced trace `7dec106681b1a57faad3089aa1b24298` (`gateway.sage_chat`) in Langfuse (US region), and the 13 previously uncollectable tracing tests now pass.

**Follow-up (2026-11-11)**: The first live session surfaced two failures the migration tests could not see. First, `qwen3.8-flash` is a *hybrid thinking* model: on a 32k-char Sage prompt it emitted ~10,600 hidden reasoning tokens and took 156s, so the 180s provider timeout fired, retried once, fell through to Gemini, and Sage reported "all engines quota-limited" even though Qwen had timed out. QwenCloud documents `enable_thinking: false`; adding it cut the same call to 19s. Second, Deep Dive showed "Unknown Source" because `load_data()` read only `history.json`, which was months stale while Supabase held today's run — the background worker that persisted to Supabase never wrote *this* machine's cache. `load_latest_run_data()` now compares local and cloud timestamps, loads the newer Supabase run, maps `generation_source`/`generation_log` back to the UI's `_source`/`_generation_log`, and caches it locally. The gateway also records per-model `failure_reasons` so Sage can say "qwen: timed out; gemini: 429" instead of blaming every engine for quota.

**Result**: The gateway runs entirely on Qwen Cloud + Gemini. Live checks passed for the direct provider, the gateway's `SAGE_CHAT` and a Qwen-pinned `CATEGORISE` (schema-validated JSON), and the legacy `LLMClient` probe/generate paths. Full suite: **589 passed, 1 skipped**, plus **13/13** tracing tests after the OTel install.

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
