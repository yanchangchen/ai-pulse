# AI Pulse Engineering Blog

Lessons learned building a multi-model AI news intelligence dashboard — one that fetches, classifies, summarises, and persists AI industry developments across 30+ sources with longitudinal memory.

---

## 2026-11-28 — Agentic Sage: how the deep-research pipeline thinks
Labels: explainer, sage, gateway

**Who this is for**: you've heard "agentic AI" and want to see what it actually means in a working system — not a chatbot that calls tools, but a small team of specialised LLM calls that plan, check each other's work, and leave an audit trail. This post walks through Sage's deep-research mode step by step. (Companion pieces: "How Sage remembers: from news feeds to cited answers" for the archive behind it, and the Phase 7 journal entry for the engineering war story — both below, filter by the `explainer` / `sage` labels.)

### What "agentic" means here

A normal LLM feature is one call: prompt in, text out. Plenty of "agentic" demos are just that one call with a search tool bolted on. The defining difference here is **checks and balances**: the work is decomposed into stages, each stage has a narrow job, and one stage *reviews another's output before it reaches you* — with a record of every step left behind. Sage's deep mode is exactly that, with three roles (plus search as a tool):

```
   ┌─────────────────────────────────────────────────────────────┐
   │                    ONE QUESTION                             │
   │   "How did AI governance shift between May and October?"    │
   └──────────────────────────┬──────────────────────────────────┘
                              ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  🔭 SCOUT — the planner                                      │
   │  Reads the question + archive scope, writes 2-3 focused      │
   │  search queries as JSON (a plain-text data format models     │
   │  can emit reliably). Doesn't answer anything.                │
   │     "EU AI Act enforcement May-Oct 2026"                     │
   │     "US state AI legislation summer 2026"                    │
   │     "export controls chips 2026 timeline"                    │
   └──────────────────────────┬───────────────────────────────────┘
                              ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  🔍 RETRIEVAL — the tool (not an LLM)                        │
   │  Per sub-query: search by exact words AND by meaning         │
   │  (each article has a numeric "meaning fingerprint" — an      │
   │  embedding — so similar ideas match even with different      │
   │  words). Hits AUGMENT the existing evidence, never replace.  │
   └──────────────────────────┬───────────────────────────────────┘
                              ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  ✍️ ANALYST — the writer                                     │
   │  Sage's persona + the assembled context → a full draft:      │
   │  Direct Answer → cited Evidence → "My read on this".         │
   └──────────────────────────┬───────────────────────────────────┘
                              ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  ⚖️ FACT-CHECKER — the reviewer                              │
   │  Reads the draft AGAINST the exact context. Every statement  │
   │  must be explicitly supported — exaggeration and added       │
   │  framing count as unsupported. Returns a JSON list of        │
   │  flagged claims (empty = clean).                             │
   └──────────────┬───────────────────────────┬───────────────────┘
                  │ clean                     │ claims flagged
                  ▼                           ▼
            publish draft        ┌────────────────────────────────┐
                                 │  ✍️ ANALYST revision (ONE try) │
                                 │  Drop flagged statements unless│
                                 │  explicitly supported. Never   │
                                 │  rephrase them into new claims.│
                                 └───────────────┬────────────────┘
                                                 ▼
                                           publish final
```

Notice what each role is *not* allowed to do: the Scout never answers, the Fact-Checker never rewrites, the Analyst never searches. Narrow jobs are what make the pipeline debuggable — when something goes wrong, you know which stage to blame.

**Why these separations?** Three reasons that aren't obvious:

- **A writer makes a poor reviewer of its own work.** Asking the same call to "write an answer and check it" lets the model rationalise its own phrasing. The Fact-Checker is a *fresh* call that receives only two things — the context and the draft — with no stake in the draft and no memory of having written it. Separation is what makes the check meaningful.
- **One search query averages your question's facets.** "Governance shifts since May" blends legislation, export controls, and international policy into one compromise query, and retrieval returns a compromise set. The Scout splits the facets so *each* gets its own best hits — three narrow searches beat one broad one.
- **The revision is bounded to one pass on purpose.** Every extra pass costs latency and tokens, and an unbounded "check → rewrite → check" loop can oscillate forever. One pass suffices because the rule is *deletion*, not negotiation: a flagged statement is dropped unless the context explicitly supports it, so there is nothing left to argue about.

### A worked example

Question: *"What major AI governance shifts occurred since May 2026?"*

1. **Scout** emits three sub-queries covering legislation, export controls, and international policy — facets one keyword search would blur together.
2. **Retrieval** runs each sub-query through the hybrid engine (exact-word search + meaning search, combined by rank position so the two scoring scales never need to agree). The hits *append* to the articles a plain single-search answer would already have found — those stay first; the scout's finds are added alongside, never substituted.
3. **Analyst** drafts a chronological account citing dated articles and *monthly rollups* — pre-computed ~400-word summaries of a theme's whole month, distilled from its daily briefs (the archive's "chapter summaries"; the data tour below explains how they're made). A citation looks like `[Governance, Safety & Policy · Month 2026-05]`.
4. **Fact-Checker** flags one sentence: *"the U.S. government's volatile use of export controls as diplomatic leverage"* — the sources mention export controls, but "volatile diplomatic leverage" is the writer's framing, not the archive's words.
5. **Revision** drops that sentence entirely (it cannot be cited), keeps everything else, and publishes.

You see the whole story in the UI: the 🔬 caption ("3 sub-queries · fact-check 1 claim flagged · revised · 41s"), and a Pipeline-detail expander with the sub-queries, per-stage latencies, and the flagged claim itself.

### What if a stage fails?

A pipeline with five stages has five ways to break, so each stage has a designed fallback — the rule is **degrade honestly, never block silently**:

| If this fails… | …this happens instead |
|---|---|
| Scout (planner) | the original question becomes the single search query — you get a fast-mode-shaped answer, still checked |
| One retrieval | the evidence already gathered stands; the answer proceeds without that sub-query's finds |
| Fact-Checker | the draft publishes, and the reply is *marked* "checker unavailable" — an outage must never masquerade as a clean check |
| Revision | the draft publishes unchanged (flagged claims are shown in the pipeline detail, so you can see what was contested) |
| Analyst (writer) | an honest notice ("engines quota-limited / failed") — never a fabricated answer |

The principle: a committee that can't convene should still give you a truthful solo answer, and tell you which members were absent.

### Every reply leaves one audit trail

Agentic systems are hard to debug *unless you instrument them first* — that was the roadmap's explicit gate before this phase was allowed to exist. The vocabulary, briefly: a **span** is a timed record of one step (what ran, how long, which model, how many tokens); a **trace** is the tree of spans for one whole operation; **Langfuse** is the trace viewer they're shipped to. Each deep reply produces one trace:

```
 sage.pipeline                        ← the whole reply, one trace id
 ├─ gateway.sage_scout                ← planner call (model, tokens, latency)
 ├─ sage.retrieval  "EU AI Act…"      ← one span per sub-query
 │   └─ gateway.embedding             ← the meaning-vector lookup
 ├─ sage.retrieval  "US state AI…"
 │   └─ gateway.embedding
 ├─ gateway.sage_analyst              ← the draft
 ├─ gateway.sage_factcheck            ← the review
 └─ gateway.sage_analyst              ← the revision (only if flagged)
```

Every call also lands in the durable `llm_calls` database table sharing one **correlation id** — a shared tag stamped on all calls belonging to the same reply, so cost, latency, and model choice per stage are queryable in SQL, not just visible in a UI. If a reply looks wrong, you can open its trace and see exactly what the Scout asked, what retrieval returned, what the draft said, and what the checker flagged.

### Why the checker matters: the gate caught the pipeline lying

First, the measuring stick. A **faithfulness score** is assigned by a judge LLM that reads the question, the answer, and the *exact context the answer was given*, and rates 0–1 how fully every claim is supported (naming the unsupported ones). An **eval gate** is the shipping rule: a fixed set of questions runs through both the old and new version *on the same day*, each answer is judged against its own context, and the new version must not regress. Same-day matters — the archive grows and judge moods vary, so only a side-by-side comparison is honest.

The first evaluation run scored deep mode **0.863** against the fast path's 0.950 — *worse*. Two causes, both instructive:

- The pipeline originally **replaced** the fast path's evidence with sub-query hits. New articles tempted the Analyst into embellished narratives (one answer scored 0.50). Fix: validated evidence stays first; scout finds only append. **Augment grounding, never replace it.**
- The revision instruction said "cite or drop". The model happily *rephrased* flagged claims into fresh unsupported assertions. Fix: the instruction now says **drop entirely unless explicitly supported**, and the checker treats exaggeration/generalisation as unsupported. **A revision rule must say "drop", not "fix".**

After the fixes, the controlled gate passed: deep **0.950** vs fast 0.900 on the same 8 macro questions, same day — better truthfulness, ~44s mean latency, and **90% month coverage** (the fraction of months the questions ask about that the assembled context actually reaches — the guard against a pipeline that scores well by answering "I have no data"). The lesson generalises: *every stage that transforms grounding is a new hallucination surface, and an eval gate is where those surfaces get audited.*

### When to use which mode

| | Fast (default) | 🔬 Deep research |
|---|---|---|
| Shape | one retrieval pass + one call | Scout → retrieval ×N → Analyst → Checker → ≤1 revision |
| Latency | ~10–20s | ~30–50s |
| LLM calls | 1 | 3–5 |
| Best for | "what happened last week?", quick facts | multi-month arcs, comparisons, anything you'll act on |
| Grounding | recent days in full, older months as rollups, top articles as evidence | all of that, plus sub-query evidence and a pre-publication check |

Both modes are graded by the same post-publication faithfulness judge, and both cite only real links from the archive — the checker never invents a URL, it can only flag what the writer said.

**To try it**: open the **Sage** page (🔮 in the sidebar), flip the **🔬 Deep research** toggle, and ask a multi-month question — "How did agentic coding tools evolve between June and September?" is a good first committee case. Watch the spinner narrate the stages, then open the "🔬 Pipeline detail" expander under the reply to see the sub-queries, the stage timings, and anything the fact-checker flagged. It's off by default, because most questions don't need a committee.

*The agentic bet in one sentence: decompose the work, narrow each role, check before publishing, and trace everything — then let an eval gate tell you whether the committee actually beats the solo act.*

---

## 2026-11-28 — Phase 7: Sage grows a pipeline — Scout, Fact-Checker, Analyst
Labels: sage, gateway, evals

**Problem**: Sage answered every question with one retrieval pass and one LLM call. Nothing planned the search, and nothing checked the answer *before* it was published — the faithfulness judge only graded it afterwards. Complex multi-month questions deserved better than a single shot.

**What happened**:

1. **Three agents, one question.** Deep-research mode (an opt-in 🔬 toggle on Sage's new dedicated page) runs a pipeline: a **Scout** turns the question into 2–3 focused sub-queries; hybrid retrieval runs per sub-query; an **Analyst** drafts the answer in Sage's voice; a **Fact-Checker** validates every statement against the exact context and returns the unsupported ones as JSON; if anything is flagged, the Analyst gets **one** bounded revision pass. Each stage degrades independently — a Scout outage falls back to the original question, a Fact-Checker outage publishes the draft (recorded, never hidden), and only an Analyst failure surfaces a notice.

2. **One reply, one trace.** A new `pipeline_span` helper opens a `sage.pipeline` parent span; OTel context propagation nests every agent call, every retrieval, and every embedding under it automatically. All calls share one `correlation_id`, so the `llm_calls` rows for a reply group together and link to the Langfuse trace. The UI shows the whole breakdown — sub-queries, stage latencies, flagged claims — in a "🔬 Pipeline detail" expander. The 3-agent pipeline the roadmap promised in Phase 0 is now debuggable by construction.

3. **Sage got its own home.** The Ask Sage tab moved out of Memory Wiki into `pages/11_Sage.py`; Memory Wiki keeps the timeline, run comparison, and monthly rollups. And all Sage knowledge now lives in one focused reference, `docs/SAGE.md` — pipeline stages, knobs, gates, and an extension guide — so future changes start from one place.

4. **The gate caught the pipeline lying — twice.** First run: deep mode scored **0.863** vs the fast path's 0.950. Diagnosis: (a) the pipeline *replaced* the fast path's eval-validated evidence with sub-query hits, and the Analyst built embellished narratives around the new articles (one answer dropped to 0.50 with diplomatic framing the sources never stated); (b) the "cite-or-drop" revision let flagged claims survive *rephrased*. Fixes: evidence now **augments** — validated base articles first, scout finds append; the Fact-Checker treats exaggeration and generalization as unsupported; and revision **drops** flagged statements entirely unless explicitly supported. Second run: deep **0.950** vs fast 0.900, coverage 0.90, 44s mean latency — **gate passed**.

*Lesson: an agent pipeline is not automatically better than a single call — every stage that transforms grounding (evidence selection, revision) is a new hallucination surface, and the eval gate is where those surfaces get audited. Augment validated grounding; never replace it. And a revision instruction must say "drop", not "fix" — models will happily rephrase a flagged claim into a fresh unsupported one.*

**Result**: Phase 7 closed (7.1–7.3). Deep mode is opt-in (fast path unchanged, ~10–20s vs ~40s deep), every stage traced, verdict on the Eval Sets page. Next: Phase 8 — feeding the judge's signal back into the agent itself.

---

## 2026-11-28 — How Sage remembers: from news feeds to cited answers
Labels: explainer, sage, retrieval, pipeline

**Who this is for**: you're new to AI engineering or data engineering, and you want to understand *how an AI assistant can answer questions about months of news without having read it all*. No prior knowledge assumed — every term is explained where it first appears. This is the long, diagram-heavy companion to the brief in the README.

Sage is the conversational analyst inside AI Pulse. Ask it *"How did agentic coding tools evolve between June and September 2026?"* and it answers with a chronological account, citations you can click, and its own assessment.

The problem it solves: the engine behind Sage is an **LLM** (large language model — the kind of AI that writes text), and an LLM can only read a limited amount of text per question — its *context window*. The archive grows every day; stuffing all of it into every question is impossible, expensive, and counterproductive (models pay less attention to the middle of very long inputs). So Sage never "remembers" the news itself. Instead, a **data pipeline** collects, organises, and compresses the news ahead of time, and a **retrieval system** picks out just the relevant pieces for each question. This post follows the data through that whole journey.

Two loops, one picture: the **top half runs once a day** without you (fetch → classify → summarise → store); the **bottom half runs per question**, in seconds (retrieve → pack → answer → grade).

The one-picture overview:

```
  30+ engineering blogs & newsletters
              │  fetched daily
              ▼
   ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
   │     CLASSIFY     │ → │    SUMMARISE     │ → │  STORE & INDEX   │
   │ sort into 8      │   │ one daily brief  │   │ 3 tiers of       │
   │ strategic themes │   │ per theme        │   │ memory + vectors │
   └──────────────────┘   └──────────────────┘   └──────────────────┘
                                                          │
              your question                               ▼
   ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
   │      SAGE        │ ← │     ASSEMBLE     │ ← │     RETRIEVE     │
   │ the LLM writes   │   │ pack a budgeted  │   │ two search styles│
   │ a cited answer   │   │ context "suitcase"│  │ fused into one   │
   └──────────────────┘   └──────────────────┘   └──────────────────┘
              │
              ▼
   a second LLM ("judge") grades the answer's faithfulness
```

Everything below zooms into one box of that picture.

### 1. Every article's journey: fetch → classify → summarise

Once a day (or on demand), the pipeline visits 30+ sources — company engineering blogs, research newsletters, security write-ups — and collects new articles. Each article then goes through two machines:

**The classifier** decides which of the 8 strategic themes an article belongs to (things like *Frontier Models & Benchmarks*, *AI Security & Trust*, or *Robotics & Embodied AI*). It's a "waterfall" of four checks, cheapest first:

1. **Keywords** — weighted word lists per theme. Fast, free, surprisingly effective.
2. **TF-IDF** — a statistical measure of how characteristic each word is of a theme. Catches articles that use different vocabulary than the keyword lists.
3. **An LLM call** — only for the genuinely ambiguous leftovers. (This gate can be switched off entirely once the keyword lists mature.)
4. **A heuristic fallback** — a relaxed best-guess so nothing falls through the floor.

**The summariser** then writes one intelligence brief per theme per day, in five sections: what happened, engineering trade-offs, product impact, why it matters, what to watch. Two details matter for data quality:

- **Nothing is silently dropped.** If a theme has more articles than one prompt can hold, the summariser splits them into batches, summarises each, then merges the batch briefs into one ("map-reduce" — a pattern borrowed from big-data processing: *map* over the pieces, *reduce* them into one result). Articles that still don't fit get an "aging boost" next run, so the backlog drains fairly instead of starving.
- **Every run records its own coverage** — "22 of 37 articles reflected, 15 waiting" — so gaps are visible, not invisible.

The results land in **Supabase** — a hosted **Postgres** database (Postgres is the open-source workhorse that stores data as tables of rows; Supabase runs it in the cloud for you). Think of it as the project's shared filing cabinet: any device, any session reads the same memory. A local JSON cache mirrors the latest runs for offline use.

### 2. Three tiers of memory: the map-zoom idea

Here is the core design. Storing every article forever is easy; *finding the right altitude* for a question is the hard part. So the same intelligence is stored at **three zoom levels**, like a map app:

```
                        ┌─────────────────────────┐
   TIER 3  🗺️ country   │   monthly_syntheses     │  "What changed across
   macro rollups        │   1 rollup per theme    │   the whole quarter?"
                        │   per month             │
                        ├─────────────────────────┤
   TIER 2  🏙️ city      │   theme_summaries       │  "What happened in
   daily briefs         │   1 brief per theme     │   agentic tools last
                        │   per run (day)         │   Tuesday?"
                        ├─────────────────────────┤
   TIER 1  🚶 street    │   articles +            │  "What exactly did
   article evidence     │   article_embeddings    │   that Anthropic post
                        │   every article, with   │   say about X? Link me."
                        │   its meaning-vector    │
                        └─────────────────────────┘
```

- **Tier 1 — the raw evidence.** Every article: title, summary, source, date, and the original URL. This is what Sage cites when a claim needs a primary source.
- **Tier 2 — the diary.** The daily theme briefs from step 1. Good for "what happened recently" questions.
- **Tier 3 — the chapter summaries.** Once a month completes, a background job reads that month's daily briefs for each theme and asks an LLM to write one **macro synthesis**: the month's arc in ~400 words, plus a list of dated key milestones. Completed months are written once and never change; the current month refreshes every two weeks. This is what makes multi-month questions affordable — more on that in step 4.

**What's an embedding?** Each Tier-1 article also gets an *embedding*: a list of 768 numbers produced by a small model (here, Cloudflare's BGE) that represents the article's **meaning as coordinates**. Articles about similar ideas land near each other in that space, even if they share no words — "reasoning token overhead" and "chain-of-thought latency costs" end up as neighbours. Don't try to picture 768 dimensions: imagine a map with 768 axes instead of two. You can't draw it, but the maths of "nearby" works exactly the same, and that's all the search needs.

The vectors live in Postgres via **pgvector** — an add-on that teaches the database to store these number-lists and search them — with an **HNSW index**: a shortcut structure so a search hops through nearby candidates instead of comparing against every single row. (Without it, every question would scan the whole archive.)

**Why three tiers and not one?** Each tier trades detail for reach. A question about one Tuesday needs street view; a question about a quarter needs the country map — feeding it 90 days of street-level detail would overflow the model's reading budget and bury the arc in noise. The tiers let the assembler pick the right altitude per part of the question.

### 3. Finding the right data: two search styles, one ranked list

When you ask a question, Sage doesn't scan the archive linearly. It runs **hybrid retrieval** — two complementary searches, then a merge:

```
   "What did labs say about reasoning costs between June and September?"
              │
              ▼
   ┌─────────────────────┐
   │  parse the dates    │  → window: 2026-06-01 … 2026-09-30
   └─────────────────────┘
              │
      ┌───────┴────────┐
      ▼                ▼
 ┌──────────┐    ┌──────────────┐
 │  SPARSE  │    │    DENSE     │
 │ exact-ish│    │ meaning-based│
 │ word match│   │ vector search│
 │ (Postgres│    │ (pgvector    │
 │  FTS)    │    │  cosine)     │
 └──────────┘    └──────────────┘
      │                │
      └───────┬────────┘
              ▼
   ┌─────────────────────┐
   │  Reciprocal Rank    │  merge the two ranked lists
   │  Fusion (RRF)       │  by POSITION, not by score
   └─────────────────────┘
              │
              ▼
   gentle freshness nudge (only when the question has no dates)
              │
              ▼
   top articles → Sage's evidence section
```

**Why two styles?** They fail in opposite ways. Word matching (Postgres **full-text search**, which stems words — "running" matches "run") is perfect for exact entities: *"SWE-bench 87.6%"*, *"Llama 3.3 70B"*. Meaning search is perfect for concepts phrased differently than the source: it compares embeddings by **cosine similarity** — a 0-to-1 measure of how aligned two meaning-fingerprints point (1 = same direction, 0 = unrelated). Alone, each style misses what the other catches. And if either is unavailable — say the embedding service is down — the other still answers: degraded, never broken.

**Why fuse by rank?** The two searches produce scores on incomparable scales (a text-relevance number vs a cosine similarity). **Reciprocal Rank Fusion** sidesteps the problem: each list contributes `1/(60 + position)` per article, and the contributions add up. Like two judges ranking contestants — you don't need their scoring rubrics to agree, only their ordering. An article ranked highly by *both* judges wins.

**Time awareness.** The date parser understands "last week", "August 2026", "between June and September", "since July" — and constrains *both* searches to that window. When a question has **no** dates, a small freshness preference nudges newer articles up — deliberately a *tie-break, not a filter*. (An earlier, aggressive version buried five-month-old articles below fresher neighbours and measurably hurt retrieval; on a six-month archive, "decay" must be a gentle opinion, not a guillotine.)

### 4. Packing the suitcase: context assembly

An LLM can only read what you put in its **context window** — the text accompanying your question, measured in tokens (roughly word-pieces). Sage's budget is ~40,000 characters. Six months of daily briefs across 7 themes would overflow it many times over, and even when text *fits*, models pay less attention to the middle of long inputs. So assembly is a **packing problem**, and the three tiers are the compression strategy:

```
 ┌───────────────────────────────────────────────────────┐
 │                 THE CONTEXT SUITCASE                  │
 ├───────────────────────────────────────────────────────┤
 │  📰 ARTICLE EVIDENCE  (~30% of budget)                │
 │     top-15 retrieved articles, each with its real URL │
 │     (search biased toward the rollups' key milestones)│
 ├───────────────────────────────────────────────────────┤
 │  🗓️ MONTHLY ROLLUPS  (older completed months)         │
 │     one macro block per theme-month: the arc +        │
 │     dated milestones — ~30 daily briefs compressed    │
 │     into ~1,200 characters                            │
 ├───────────────────────────────────────────────────────┤
 │  📖 RECENT DETAIL  (the last 2 run dates)             │
 │     full 3-section daily briefs — newest news stays   │
 │     high-resolution                                   │
 ├───────────────────────────────────────────────────────┤
 │  📝 CONDENSED OLDER DAYS (whatever budget remains)    │
 │     2-sentence leads for days without a rollup        │
 └───────────────────────────────────────────────────────┘
   unused space in any layer flows back to the others
```

The rules that keep it honest:

- The **current month always stays Tier-2** (its rollup can be up to two weeks stale — recent news deserves full resolution).
- A month is represented **once**: either as a rollup or as daily blocks, never both.
- Every month in scope gets at least one block when budget allows — coverage first, then relevance fills the rest.
- Milestone links inside rollups are **copied verbatim** from the stored articles — the model is never allowed to invent a URL.

**Does the compression lose the truth?** That's an empirical question, so it was measured. A two-arm evaluation asked 8 multi-month questions with the old assembly and the new one, and a judge LLM graded each answer against the exact context it was given — a **faithfulness score** from 0 to 1 rating how fully every claim is supported (step 5 explains the judge):

```
                     old (daily briefs only)   new (tier-aware)
 faithfulness score          0.925          →      0.975
 characters per              33,640         →      6,848   (−80%)
 covered month
 months actually covered      ~2            →      6–7
```

Same-or-better truthfulness, one-fifth the cost per month of history, and the archive reach *extended* — the old assembly couldn't even see May–September within its row limit. That's the whole point of Tier 3: **zoom out to see further, not to see less.**

### 5. Trust but verify: citations and the judge

Sage's answer is only as good as its grounding, so two mechanisms keep it accountable:

1. **Citations with real links.** Claims cite `[Theme · Run date]` or `[Theme · Month YYYY-MM]`, and article claims carry the original URL as a clickable hyperlink. The UI shows an evidence popover with the exact articles behind each reply.
2. **A faithfulness judge.** After every grounded reply, a *second* LLM call reads the question, the answer, and the exact context Sage was given, and scores 0–1: is every claim supported? It names any unsupported claims, and the score is stored (`sage_evaluations`) and charted over time. Judge failures are recorded as *excluded*, never as zeros — an outage must not masquerade as hallucination.

```
   Sage's answer ──┐
                   ├──→  judge LLM  ──→  score + named claims  ──→  dashboard
   exact context ──┘         (0–1)
```

And before any change to retrieval or assembly ships, it must pass an **evaluation gate**: run the old and new versions side-by-side on a fixed question set, and prove no regression. The numbers in step 4 came from exactly such a gate.

### 6. Where everything lives

One table to orient you in the database:

| Table | Plain-English role |
|---|---|
| `trend_runs` | one row per daily pipeline run (the diary's dates) |
| `articles` | Tier 1 — every collected article, deduplicated |
| `article_embeddings` | Tier 1 — each article's 768-number meaning vector |
| `theme_summaries` | Tier 2 — the daily briefs per theme |
| `monthly_syntheses` | Tier 3 — the monthly macro rollups |
| `theme_first_seen` | when each theme truly debuted in the archive |
| `sage_evaluations` | the judge's scores for Sage's answers |
| `llm_calls` | every LLM call: model, tokens, latency, cost class |
| `app_settings` | small shared state (eval snapshots, tunable knobs) |

Every LLM call is also recorded as a timed step (**span**) in a per-reply tree (**trace**) using OpenTelemetry — the standard instrumentation format — and shipped to **Langfuse**, a trace viewer. So any answer can be followed back through the exact prompts, models, and retrieval steps that produced it. (The companion post "Agentic Sage: how the deep-research pipeline thinks" shows what that trace looks like when Sage runs a whole committee of agents per question.)

### The design principle in one sentence

**Store at three zoom levels, search two ways, pack a budgeted suitcase, and grade every answer.**

**To see it live**: open the **Sage** page (🔮 in the sidebar), ask a multi-month question like *"How did frontier model releases evolve between May and October?"*, and open the **📰 Article Evidence** accordion under the reply — those are the exact Tier-1 briefs, with their real links, that grounded the answer. The grounding caption tells you how many summaries, dates, and monthly rollups were packed. For the opt-in deep-research mode (a Scout plans sub-queries, a Fact-Checker reviews the draft before publication), read the companion post: **"Agentic Sage: how the deep-research pipeline thinks"** — filter the blog by the `explainer` label to find both.

If you want the implementation depth behind each box: [docs/SAGE.md](docs/SAGE.md) (the focused Sage reference), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (pipeline, retrieval, assembly), [docs/SUPABASE.md](docs/SUPABASE.md) (schema), and [Future.md](Future.md) (the roadmap this tour was built from).

---

## 2026-11-28 — Phase 5: fusion is mechanical, decay is an opinion
Labels: sage, retrieval, evals

**Problem**: Sage found articles by keyword overlap; the dense index existed but served only the eval gate. Phase 5 wires both legs together — and the interesting engineering turned out to be neither the vectors nor the fusion, but how strongly *freshness* should vote.

**What happened**:

1. **Two legs, one rank list.** FTS (`match_articles`) and pgvector top-k fuse via Reciprocal Rank Fusion — `sum 1/(60+rank)`, deduped by article id. Rank-based fusion needs no score normalisation between ts_rank and cosine, which is exactly why it's the right default. The dense RPC grew a metadata join so hits arrive as renderable evidence, and date/theme bounds are applied *after* a 4x over-fetched ANN scan: an HNSW index with a WHERE filter can under-fill LIMIT, so the filter never rides the index scan.

2. **The gate caught an opinion disguised as a formula.** First run: hybrid 0.833 vs dense 0.875 — a regression. Both lost targets were June/July articles. The time-decay (floor 0.5, halflife 30d) halved anything ~5 months old, so fresher neighbours pushed them out of the top-10. On a six-month archive, "decay" was acting as a filter. Retuned to floor 0.9 / halflife 180d — a bounded ~1.11x nudge — and both recovered: **0.875 = 0.875, gate passed**, p95 1.9s against a 3s budget. Decay strength must be calibrated to the archive's age span, not the ingest cadence.

3. **Same-day controls matter.** The recorded baseline was 0.917 (Nov 27); today's dense arm scored 0.875 on the identical set because the archive grew. Comparing hybrid against the stale snapshot would have manufactured a "regression" that was really index drift. The gate runs both arms back-to-back for exactly this reason.

4. **First appearances stopped lying.** The old render-time scan tagged the oldest run *in the fetched window* as a theme's first appearance — every window got one. `theme_first_seen` (insert-if-absent at ingest, backfilled from the archive) makes the tag mean what it says: the theme's true archive debut.

*Lesson: retrieval fusion is the easy half. The re-ranking policy — how much freshness outranks relevance — is a tunable opinion, and the eval gate is where opinions get audited. Run the control the same day as the experiment.*

**Result**: Phase 5 closed (5.1–5.4). Sage answers now ground in hybrid retrieval with temporal awareness; 41 new tests; migration applied; verdict snapshotted to the Eval Sets page. Next: Phase 6 hierarchical memory.

---

## 2026-11-27 — The run that landed 8 hours in the future
Labels: reliability, supabase, pipeline

**Problem**: after a headless demo run, the Memory Wiki showed the newest run as *tomorrow morning* (28/11 07:27 instead of 27/11 23:27), and the sidebar reported the cache as "-7h ago".

**What happened**:

1. **Naive local time, meet timestamptz.** `save_run_to_history` stamps runs with `datetime.now()` — local, naive — and handed that string straight to Supabase. Postgres reads a naive timestamp as **UTC**, so a UTC+8 process wrote its run 8 hours into the future. Every earlier run had come from a UTC process, which masked the bug for months. The shifted timestamp then sorted as "latest" everywhere, and `now − run_time` went negative — hence "-7h ago".

2. **The fix is one conversion, at the boundary.** Local history keys stay local (they're a local cache); the Supabase payload now converts to aware UTC ISO before insert. The duplicate-run check had the mirror bug — `strptime` with the local-key format silently failed on ISO cloud timestamps, so the Supabase fingerprint check had been dead code; it now parses ISO and normalises to local naive. The sidebar clamps negative ages at 0. The one shifted row was repaired from its `created_at` (the DB's own clock was right all along).

3. **While in there: say the zero.** Coverage labels only rendered "N of M reflected" when N < M — full coverage silently fell back to "(79 articles)", which reads as *nothing processed*. Now every surface (Timeline, Deep Dive, memory.md) always states "N of M reflected · K left unprocessed", even at K = 0, plus a run-level 🆕/✅/⏳ caption reconstructed from the persisted `included_count + waiting_hashes` — no schema change.

*Lesson: a naive datetime is a timezone bug waiting for a process in a different TZ. Convert at the storage boundary, keep `created_at` as the audit clock, and make "zero" an explicit, rendered state — an absent label is indistinguishable from a broken pipeline.*

**Result**: runs persist correctly from any timezone; the demo run (267/267 processed, Agentic 79 via 7-batch map-reduce) displays at its true time; regression tests in `tests/test_run_timestamps.py`.

---

## 2026-11-27 — Phase 4.6: ranking is a policy, not a sort
Labels: pipeline, evals

**Problem**: after 4.5 every overflow article eventually gets fed — but *which* articles fill the 50 slots was still a pure keyword-relevance sort with an aging boost. A syndicated reprint could occupy a slot twice, one prolific source could crowd out a quieter one, and a 13-day-old story could outrank this morning's at equal relevance.

**What happened**:

1. **Four signals, four knobs.** Recency (linear decay over the 14-day lookback, doubling as the first tie-break), source diversity (a greedy per-source diminishing penalty — the extractive engine's diversity filter, promoted to prompt selection), near-duplicate demotion (title-token Jaccard ≥ 0.8 against a higher-ranked representative), and classification-gate confidence (keyword > TF-IDF/LLM > heuristic). Each disables at 0; all off reproduces legacy order exactly — a regression test pins that.

2. **Demote, never drop.** Every penalty only reorders; when the pool fits the ceiling, all articles still feed. That keeps the 4.1/4.3 ledger contract intact — an article marked processed genuinely appeared in a prompt — and it's what makes the change safe to ship without an LLM gate.

3. **Deterministic gate, same-pool before/after.** Ranking changes selection, not capacity, so `eval_ranking_gate.py` measures selection quality directly: on the freshest run (273 articles), the one theme over the cap went from 19 to 24 distinct sources and 7.27 to 6.34 mean article age, with 40/50 overlap and zero duplicate pairs fed. The near-dup signal found nothing at Jaccard 0.8 today — it waits, unit-tested, for the next syndication wave.

4. **Two honest limits.** Supabase-restored articles carry no `gate` column, so gate confidence is neutral there (it acts on freshly classified articles); and identical titles from *different* sources cluster as duplicates before diversity ever sees them — a test that assumed otherwise failed, correctly.

*Lesson: a ranking function is a policy surface — give each preference its own knob, make every penalty a demotion rather than a deletion, and pin the all-off path to the legacy behaviour so "turn it off" is a tested state, not a hope.*

**Result**: Phase 4 complete (4.1–4.6). 20 new tests (`tests/test_ranking_refinements.py`), fair-drain and summariser suites green, gate results in `scratch/eval_4_6_results.json`. Next lever: Phase 5 hybrid retrieval.

---

## 2026-11-27 — The 139-minute "success": why wait_for is not a timeout
Labels: reliability, gateway

**Problem**: During the 4.4/4.5 gate experiments, two Gemini fallbacks logged `success=true` with latencies of 702s and 8329s — a 2.3-hour "successful" call. The provider already wrapped every SDK call in `asyncio.wait_for(..., 30s)`. The timeout existed; it just didn't bind.

**What happened**:

1. **The mechanism**: `asyncio.wait_for` cancels the inner task on timeout and then *awaits its completion*. The google-genai SDK retries connection/DNS failures through tenacity + aiohttp, and cancellation during DNS-executor resolution gets deferred — so the cancelled task keeps running, and `wait_for` patiently waits for it. The deadline becomes a suggestion. (The latency column measures from gateway-execution start, which is why the row also absorbed the preceding Qwen timeouts.)

2. **The fix, two layers**: the provider now uses `asyncio.wait({task}, timeout=...)` and on expiry cancels best-effort and **abandons** the task — the caller returns on time no matter what the SDK does with cancellation. And the SDK itself is bounded at construction: `HttpRetryOptions(attempts=2, max_delay=2s)` plus `HttpOptions.timeout` so abandoned tasks die on their own.

3. **The unit trap, caught by a test**: `HttpOptions.timeout` is **milliseconds**. Passing the seconds value (30) would have meant a 30-millisecond timeout — every real Gemini call failing instantly. A construction test asserting `timeout == 30_000` caught it before any live traffic; a live sanity call then returned in 1.3s.

*Lesson: a timeout is only as strong as its cancellation path. If the callee can defer cancellation, wrap-with-wait_for is not a deadline — use wait-and-abandon, bound the callee's own retries, and test the deadline with a coroutine that deliberately defers cancellation. And read the unit of every timeout field you set: seconds-vs-milliseconds is a silent 1000× bug.*

**Result**: Gemini fallbacks are now bounded at ~30s wall-clock regardless of DNS/network pathology; 5 regression tests (`tests/test_gemini_timeout.py`), gateway suite green, live call verified.

---

## 2026-11-27 — Phase 4.5: map-reduce, or how to compress without forgetting
Labels: pipeline, evals

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
Labels: evals, reliability

*A retrospective, not a phase log. One session closed the Cloudflare watch, resurrected a lost eval set, shipped two UI pages and Phases 4.3–4.4 — and caught three of our own instruments lying before any of it shipped.*

**Lie 1 — the receipt without the asset.** The Phase 3 retrieval gate's baseline (Recall@10 = 0.833) survived in `app_settings`, but the eight questions that produced it lived in gitignored `data/` and were gone. A score in a database is a receipt; the questions are the asset. We recreated the set (24 questions, curated against the live archive, new baseline 0.917), gave it a git-tracked home with a `.gitignore` exception, and built the Eval Sets page so every future eval set is visible by construction. *Rule adopted: anything a gate depends on lives where git can see it.*

**Lie 2 — the metric that ate the backlog.** The 4.4 gate experiment first reported that raising the article cap *destroyed* coverage (98% → 66%). Impossible numbers deserve an audit, not a decision: `coverage_judge` joined every value of the summary dict, and since 4.1 that dict carries `skipped_articles` — the backlog payload with every skipped title. The judge was scoring the backlog list as brief prose; arm A "covered" 192 articles it was never fed. With narrative-only sections the gate reversed to a honest pass (38.4% → 54.9%). Production had been safe by accident (the evaluator loads only narrative columns); the judge now whitelists prose sections so safety is by construction. *Rule adopted: a recall metric must whitelist what counts as text; metadata carrying titles is a leak waiting for a caller.*

**Lie 3 — the stale fact in the roadmap.** Future.md still described a ~59k-char prompt budget from the Ollama era; on Qwen's 1M window the budget is 1.8M chars and the flat 12-cap used ~0.3% of it. The phase premise ("the context window is not the constraint") was right for the wrong reason. Re-verified numbers replaced the folklore before 4.4 was designed. *Rule adopted: situational facts in a roadmap carry a verification date, and get re-measured when the stack under them changes.*

**Process footnotes.** The permission classifier timed out repeatedly mid-session; a "manual commit" reported by a human never landed in the repo (reflog showed no attempt) and was caught by checking rather than trusting; and two Gemini fallbacks hung 12 and 139 minutes because that provider has no effective timeout bound — queued as a reliability fix. None of these changed the engineering, but all of them changed what we verified: *reports are claims; reflogs, call logs and re-measurements are evidence.*

**What stands at the end of the day**: watch closed (Cloudflare healthy, no Option C), eval set loss-proofed and displayed, fair drain live (backlog drains or gets a death certificate), capacity raised with an audited gate, and a coverage judge that cannot eat metadata. Next: 4.5's map-reduce briefs, to close the naming gap the gate honestly left open.

*Lesson: the most dangerous failures in a measured system are not crashes — they are instruments that keep reporting confidently while measuring the wrong thing. Audit the instrument before you obey the gate.*

---

## 2026-11-27 — Phase 4.4: raising the cap, and the metric that lied about it
Labels: pipeline, evals

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
Labels: pipeline, supabase

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
Labels: retrieval, evals, ui

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
Labels: gateway

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
Labels: pipeline, evals

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
Labels: retrieval, supabase, evals

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
Labels: sage, retrieval, supabase

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
Labels: sage, gateway, retrieval

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
Labels: sage, supabase

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
Labels: gateway, reliability

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
Labels: pipeline

**Problem**: We had a 4-pass waterfall classifier (Keywords → TF-IDF → LLM → Heuristic). The LLM pass felt like insurance — but insurance costs tokens, latency, and failure modes.

**What happened**: A live run classified **243 articles with zero LLM calls**. Keywords caught 73%, TF-IDF caught 22%, heuristic caught 5%. The LLM gate caught 0%.

**Learning**: After enough keyword refinement, non-LLM classification is not just viable — it's the best path. We added:
- A `deterministic` mode that skips the LLM gate entirely
- Gate stats persistence so the system knows *when* to suggest switching
- Heuristic keyword auto-improvement: after each run, terms from LLM/heuristic articles are extracted and auto-applied if they appear in 3+ articles

**Result**: The classifier can now run fully offline, zero-cost, and improve its own keywords run-over-run. The LLM gate is still available in `hybrid` mode for the rare edge cases that need it.

---

## 2026-09-07 to 2026-09-10 — Gemini reliability and why routing matters
Labels: gateway, reliability

**Problem**: Gemini was our primary model for summarisation. In production it kept returning "unable to summarise" errors. Users had to manually trigger regeneration.

**What happened**: We discovered the `google-genai` SDK had Automatic Function Calling enabled by default (useless noise), no request timeout (hanging calls blocked the fallback chain), and the gateway's deterministic fallback produced garbage output ("Unable to generate summary" for every theme).

**Learning**: Three fixes, three lessons:
1. **AFC must be disabled** if you're not using tools — it produces noisy SDK warnings about preferring `Chat.send_message` over `generate_content`
2. **Always set per-provider timeouts** — Gemini's default is effectively infinite. We added 30s for Gemini, 60s for Ollama, both configurable via env vars
3. **Routing order matters more than you think** — we swapped summarisation routing to Ollama-primary → Gemini-fallback because Ollama has been more reliable for our specific workload. Gemini stays primary for lightweight tasks (categorisation, extraction) where it excels

**Also fixed**: The gateway's deterministic fallback now delegates to the proper `non_llm_summariser.py` (LexRank + Luhn) instead of a crude text summariser that operated on the raw prompt string. The summariser layer has a belt-and-sunders check that re-routes any gateway deterministic result through the proper engine.

---

## 2026-09-02 — Modular AI gateway: one interface, five models
Labels: gateway

**What we built**: A task-typed routing layer that maps `categorise / extract / summarise / synthesise / project` to specific models with fallback chains, health tracking, context-fit checks, exponential-backoff retries, JSON-schema validation, and provenance tracking on every result.

**Key design decision**: The gateway doesn't just route to the first available model. It checks context window fit (input must fit 60% of the model's window), tracks consecutive failures (3 → degraded, 5 → unavailable), and falls back through an ordered chain. If all LLMs fail, a deterministic fallback kicks in.

**Provider migration**: We migrated Gemini from the deprecated `google-generativeai` SDK (gRPC-aio transport) to `google-genai` (httpx/REST). The gRPC transport caused `InterceptedUnaryUnaryCall` AttributeError warnings during GC when `asyncio.run()` destroyed event loops between calls. The REST transport eliminates this entirely.

---

## 2026-08-16 to 2026-08-26 — Sage, provenance, and the quota probe
Labels: sage, gateway, reliability

**Sage agent**: Built a conversational agent grounded in the Memory Wiki archive. Key insight — the LLM needs a time-windowed, character-budgeted context string, not raw data. We implemented relevance scoring (token overlap with the user's question) and chronological first-appearance annotations so Sage can say "I first saw this trend on [date]."

**Automatic Gemini fallback**: When Ollama quota is exceeded, Sage seamlessly falls back to Gemini. No user-visible interruption.

**Provenance chips**: Every summary card now shows which model produced it (Google blue / Ollama purple / Non-LLM amber / Error red). This surfaced a key insight: ~15% of historical summaries were produced by the non-LLM fallback during quota outages. Without the chips, users would never know.

**Quota probe fix**: Ollama's quota recovery was previously detected on the next refresh cycle (up to 12 hours delay). We now probe `/api/tags` *before* the pipeline starts so live LLM synthesis resumes the moment quota recovers.

---

## 2026-08-06 to 2026-08-12 — Non-LLM summarisation and quality evaluation
Labels: pipeline, evals

**The non-LLM breakthrough**: We built a proper extractive summariser (LexRank graph centrality + Luhn keyword-cluster scoring + n-gram keyphrase extraction) that produces 5-section intelligence briefs. It runs in <50ms, costs zero tokens, and is 100% faithful to source articles.

**Key learning**: When LLMs fail (quota, timeout, empty response), the non-LLM engine produces summaries good enough for day-to-day use. The quality difference is in synthesis — the LLM can connect dots across articles; the non-LLM engine can only extract from within articles. But for factual reporting, non-LLM is often better because it can't hallucinate.

**Quality evaluation**: Built a 7-metric evaluation suite (3 LLM-as-judge + 4 deterministic). The deterministic metrics (Grounding, Structural Compliance, Coverage, Temporal Coherence) run in sub-milliseconds and catch real issues. LLM metrics are expensive but essential for Faithfulness and Uniqueness.

**In-app remediation**: Instead of editing Python files, users can now adjust keywords, temperature, and token budgets through the UI. 1-click apply buttons push keyword changes to `custom_keywords.json` that persist across restarts.

---

## 2026-07-06 to 2026-08-06 — The UX redesign and page rationalisation
Labels: ui, sage

**What changed**: Comprehensive UX redesign with central design tokens, adaptive light/dark CSS, multi-theme CSV export, and renumbered sequential pages.

**Page pruning**: Dropped the Emerging Trends page, consolidated analytics into Keyword Analysis. Lesson: fewer pages with deeper data is better than many pages with shallow data.

**Sage agent**: Added the conversational Memory Wiki interface. Initial design had Sage answering from the full archive — but the LLM context window is finite. We implemented character-budgeted context building with relevance scoring so Sage always sees the most relevant historical summaries.

---

## 2026-06-03 to 2026-07-06 — Supabase, deduplication, and the first quality evaluation
Labels: supabase, evals, pipeline

**Supabase integration**: Added cloud persistence with 4 tables (trend_runs, theme_summaries, articles, sync_metadata). Key decision: use `(content_hash, theme_name)` UPSERT constraints so the same article isn't duplicated across runs.

**Historical backfill**: The first Supabase load backfills all `history.json` runs into the cloud tables. This took 30+ minutes for 100+ runs but only happens once.

**Weekly quality evaluation**: Built an automated pipeline that re-classifies a sample of articles, fact-checks summary claims against source articles, and measures text overlap across themes. This caught real issues — some summaries were repeating the same claims week-over-week without updating.

---

## 2026-05-16 to 2026-06-03 — Memory wiki and the birth of longitudinal analysis
Labels: sage, supabase, pipeline

**The big idea**: What if the system remembers what happened last time? Every run's summaries are appended to `memory.md` (human-readable wiki) and `history.json` (machine-readable). The next run injects the last 2 runs' summaries into the LLM prompt so the model reports on *evolutions*, not static snapshots.

**Background ingestion**: First load runs the pipeline in a daemon thread so the UI stays responsive. Subsequent loads restore from cache and only refresh if older than 12 hours.

**Caching strategy**: Content-based SHA-256 hashing prevents redundant LLM calls when fetched articles are unchanged. This saves ~40% of LLM costs on days with no new content.

---

## 2026-03-03 — Day 1: AI Pulse begins
Labels: pipeline, reliability, gateway

**What we built**: A Streamlit dashboard that fetches AI news from RSS feeds, classifies articles into themes, and produces LLM summaries.

**Initial challenges**: Feedparser 6.x broke with socket timeouts (fixed with `socket.setdefaulttimeout`). LLMs occasionally returned empty responses (fixed with retry + temperature nudge). The classifier over-fired on broad terms like "agent" (fixed with weighted keywords and TF-IDF fallback).

**Key early learning**: RSS feeds are unreliable. Some sources block scraping, some return 429s, some change their feed format without notice. We built a resilient fetcher with 3 retries, User-Agent rotation, and source pruning for feeds that consistently fail.

---

*This blog documents the engineering decisions, failures, and fixes that shaped AI Pulse. Entries are based on real production issues and the solutions that worked.*
