# Sage Reference (Developer)

**The focused home for everything Sage and Memory Wiki.** Future changes to
the conversational analyst, its data assembly, the deep-research pipeline,
or the Memory Wiki pages should start here. Routing: this doc covers
`core/sage_agent.py`, `core/sage_pipeline.py`, `core/sage_conversations.py`,
`pages/11_Sage.py`, and the Sage-facing parts of `pages/5_History.py`.

Related depth: [ARCHITECTURE.md](ARCHITECTURE.md) (pipeline, retrieval,
tier-aware assembly), [SUPABASE.md](SUPABASE.md) (schema),
[QUALITY_EVALUATION.md](QUALITY_EVALUATION.md) (judges),
[EMBEDDING_PROVIDERS.md](EMBEDDING_PROVIDERS.md) (vectors).
Newcomer-friendly trilogy in `BLOG.md` (filter by the `explainer` label,
read in this order): 1) "How Sage remembers: from news feeds to cited
answers" (data), 2) "Agentic Sage: how the deep-research pipeline thinks"
(agents), 3) "Sage end to end: data, agents, and the grades that keep them
honest" (capstone).

---

## 1. What Sage is

Sage is the conversational AI research analyst embedded in AI Pulse. It
answers questions about the archived AI-news intelligence (months of daily
runs across 8 strategic themes) with a strict voice: **Direct Answer →
Evidence (chronological, cited) → "My read on this:"**. It never answers
from parametric memory — every reply is grounded in a context assembled
from the archive, and every claim is citable.

### Page map (Phase 7 reorganisation, 2026-11-28)

| Page | Role |
|---|---|
| `pages/11_Sage.py` — **Sage** 🔮 | The chat: conversations bar (load/new/save/export/delete), Research Scope expander (period presets, theme, source), **🔬 Deep research toggle**, Active Scope badge, chat with provenance/grounding/pipeline captions and evidence popovers |
| `pages/5_History.py` — **Memory Wiki** 🧠 | The archive browser: Memory Timeline (run cards, coverage captions, backlog/aged-out expanders, 🗓️ Monthly Syntheses Tier-3 expander) + Compare Runs |

Both live in the sidebar's "Sage & memory" group (`core/shared_sidebar.py`
`_NAV_GROUPS`, keys `sage` / `memory`). Sage requires Supabase (it grounds
in the cloud archive); Memory Wiki degrades to the local cached run.

Session state is namespaced `sage_*` (`sage_messages`, `sage_current_conv_id`,
`sage_conv_title`, `sage_period_preset`, `sage_selected_theme`,
`sage_deep_mode`) — all owned by the Sage page.

---

## 2. The data Sage stands on (summary)

Three memory tiers (details: ARCHITECTURE.md "Monthly rollups" + "Tier-aware
Sage assembly", SUPABASE.md):

```
 Tier 3  monthly_syntheses     1 macro rollup per theme per completed month
 Tier 2  theme_summaries       1 daily brief per theme per run
 Tier 1  articles + embeddings every article + its 768-dim meaning vector
```

`build_wiki_context()` (core/sage_agent.py) packs a ~40k-char budget:
Tier-1 article evidence (~30%, hybrid retrieval, milestone-biased query when
Tier 3 is active) + recent-2-dates full Tier-2 blocks + Tier-3 rollup blocks
for older completed months + condensed Tier-2 for the rest. Returns
`{context, run_count, date_count, date_range, article_count, articles,
tier3_block_count, tier3_months}`.

---

## 3. Fast path (default)

```
question → build_wiki_context() → chat_with_sage() → one SAGE_CHAT call
```

`chat_with_sage(messages, wiki_context)` folds `SAGE_SYSTEM_PROMPT` +
context + conversation into one prompt (providers take a single prompt — no
separate system channel), routes `TaskType.SAGE_CHAT` (Qwen primary, Gemini
fallbacks, **no deterministic fallback** — a rule-based stand-in for Sage's
persona would fabricate analysis), and returns
`{"response": str, "provenance": dict|None}`. Total failure surfaces as a
friendly notice string (dedicated 429 notice when every model quota-failed).

The UI then: renders the reply, captions engine provenance + grounding
stats, shows the evidence popover (`articles` payload, also persisted on the
message), and fires the chat-time faithfulness judge
(`record_sage_faithfulness`, daemon thread, judges the reply against the
exact context; rows → `sage_evaluations`, charted on page 7).

---

## 4. Deep-research pipeline (Phase 7 — the agentic approach)

Opt-in via the **🔬 Deep research toggle** on the Sage page (default OFF;
per-session). Kill switch: `sage_deep_research_enabled` in
`get_sage_settings()` — when off, deep requests delegate to the fast path.

Three agents, one question, **one Langfuse trace**:

```
        ┌────────────────────────────────────────────────────────┐
        │  span: sage.pipeline  (one trace, one correlation_id)  │
        │                                                        │
        │  1. SCOUT (sage_scout)                                 │
        │     question + scope → 2-3 focused sub-queries (JSON)  │
        │            │                                           │
        │  2. RETRIEVAL per sub-query (span: sage.retrieval)     │
        │     hybrid_search each → AUGMENT the fast-path         │
        │     evidence (base articles first, scout finds append, │
        │     dedupe by link; wiki narrative kept as-is)         │
        │            │                                           │
        │  3. ANALYST draft (sage_analyst)                       │
        │     persona + rebuilt context + conversation → draft   │
        │            │                                           │
        │  4. FACT-CHECKER (sage_factcheck)                      │
        │     every draft statement vs context → JSON            │
        │     {unsupported_claims: [{claim, reason}]}            │
        │            │                                           │
        │  5. ANALYST revision (bounded: 1)                      │
        │     only if claims flagged: DROP unless explicitly     │
        │     supported — never rephrase into new claims         │
        └────────────────────────────────────────────────────────┘
```

Implementation: `core/sage_pipeline.py::chat_with_sage_deep(messages,
wiki_context, supabase=..., theme_filter=..., date_from=..., date_to=...,
source_filter=...)`.

| Stage | Task type | Temp / max tokens | On failure |
|---|---|---|---|
| Scout | `SAGE_SCOUT` | 0.3 / 400 | sub-queries = [question] (pipeline continues) |
| Retrieval | — (hybrid_search per sub-query) | — | keep the fast-path evidence |
| Analyst draft | `SAGE_ANALYST` | 0.4 / 3000 | notice string (same semantics as fast path) |
| Fact-Checker | `SAGE_FACTCHECK` | 0.1 / 900 | publish draft; `factcheck_ok=False` recorded (an outage never blocks answers and never masquerades as a clean check) |
| Revision | `SAGE_ANALYST` | 0.3 / 3000 | keep draft; `revised=False` |

Design rules:

- **Return contract superset**: `{"response", "provenance", "articles",
  "context", "pipeline": {mode, sub_queries, unsupported_claims,
  factcheck_ok, revised, stages[], correlation_id, trace_id}}`. The UI reads
  `response`/`provenance` exactly as the fast path; `articles` is the merged
  evidence list; **`context` is the rebuilt context** — the faithfulness
  judge must see what the answer actually saw.
- **Bounded loop**: at most `sage_deep_max_revisions` (1) revision. The
  Fact-Checker is a gate, not a debate. Revision rule: flagged statements are
  **dropped entirely** unless explicitly supported — never rephrased into
  new assertions (gate run 1 lesson: embellished narratives survived a
  softer cite-or-drop revision).
- **Evidence is augmented, never replaced**: the fast path's milestone-biased
  articles are eval-validated grounding and stay first; scout finds append
  within the budget. (Gate run 1 replaced them and faithfulness regressed
  0.950 → 0.863; the Fact-Checker also treats exaggeration/generalization
  beyond the source text as unsupported.)
- **Defensive parsing**: Scout/Fact-Checker JSON is extracted tolerantly
  (fenced → bare → brace slice); parse failure degrades, never raises.
- **Verbatim links only**: evidence links come from stored articles; the
  persona forbids inventing URLs.
- **Single-threaded orchestration**: gateway calls run via `asyncio.run`
  inside the ambient `pipeline_span` context, so OTel contextvars nest every
  child span correctly. Do NOT move stages into ThreadPoolExecutor workers
  without copying context (spans would orphan).

### Observability (Pillar F, made real)

- `tracing.pipeline_span(name, attributes)` (core/ai_gateway/tracing.py) is
  the public parent-span helper; nesting is automatic via
  `start_as_current_span`. No-op when tracing is off.
- One reply = one trace: `sage.pipeline` → `gateway.sage_scout`,
  `sage.retrieval` ×N (each containing its `gateway.embedding` child),
  `gateway.sage_analyst`, `gateway.sage_factcheck`, optional second
  `gateway.sage_analyst`. Parent attributes: `sage.sub_queries`,
  `sage.unsupported_claims`, `sage.revised`, `sage.factcheck_ok`.
- Every agent request shares one `correlation_id` → the `llm_calls` rows
  for a reply group by it; `trace_id` links them to the Langfuse trace.
- The UI's "🔬 Pipeline detail" expander shows sub-queries, the stage table
  (ok/latency/model), flagged claims, and both ids.

---

## 5. The improvement loop (Phase 8)

Phases 0–7 *measured* Sage; Phase 8 feeds the signal back into the agent.
Four mechanisms, all auditable:

### 5.1 Exemplar memory (`core/sage_exemplars.py`)

`build_exemplar_block()` builds a bounded few-shot preamble from the
improvement corpus and injects it between persona and context in **both**
answer modes:

- **Positive** — newest replies scored ≥0.95 by the judge: question +
  condensed answer (the *shape* of a well-grounded answer, not its facts).
  Hard cap: 2 positives.
- **Negative** — judge-flagged unsupported claims from recent replies
  (≤2 rows × ≤2 claims), plus human corrections (5.3, ≤2): concrete failure
  patterns to avoid.

Budget `sage_exemplar_max_chars` (2500); kill switch `sage_exemplars_enabled`.
Empty corpus → empty block → prompt unchanged. No per-reply context copy is
stored (the live context is already in the prompt; exemplars teach shape).

### 5.2 Grounding guard (`core/sage_guard.py`)

The agent-side counterpart of the summariser's auto-remediation, **with the
reversibility the summariser tuner lacks**:

- Checked fire-and-forget after every successful judge insert.
- **Trigger**: rolling mean (last 20 judgments, shared definition
  `rolling_sage_faithfulness`) < 0.8 with ≥10 judgments → sets
  `sage_strict_grounding`: both paths append a STRICT GROUNDING MANDATE
  (cite-or-drop, no extrapolation/framing) and cap temperature at 0.2 on
  the answer/draft calls (Scout and revision keep their own 0.3).
- **Revert**: rolling mean ≥ 0.85 (hysteresis — judge noise can't oscillate
  the mode) → flag cleared automatically.
- **Audit**: every action appended to `app_settings.sage_grounding_state`
  (+ local mirror), history capped at 20; status card on page 7.
- Disabled by default; armed via the 🛡️ toggle on Quality Evaluation.

### 5.3 Human feedback (`sage_reply_feedback` table)

👍/👎 on every Sage reply; 👎 opens an optional correction box. Ratings
persist (Supabase + local JSON fallback) and ride on the message dict.
Down-ratings **with corrections** become "READER CORRECTION" negative
exemplars — the loop learns from people as well as the judge. Recent
ratings surface on the Feedback & Roadmap page.

### 5.4 Regression gate (`python -m core.sage_eval --gate8`)

**The shipping rule: any change to Sage context assembly or prompts must
pass gate8 before shipping.** It runs the macro question set with the
current code and requires no regression (eps 0.02) against BOTH:

1. the **live rolling baseline** — the mean of the last 20 judgments in
   `sage_evaluations` (≥10 required — the gate refuses to run without a
   baseline to defend), and
2. the previous gate8 snapshot.

Verdict snapshots to `sage_eval_latest` (arm `gate8`). Eval rows never enter
the rolling baseline.

**Signal-quality fix (Phase 8):** the live judge's context truncation
(`EVAL_SAGE_CONTEXT_CHARS`) was raised 8000 → 60000. Contexts are ~35k chars
chronological-ascending with evidence last; the 8k prefix showed the judge
only the oldest narrative and systematically scored recent citations as
unsupported (live baseline ~0.31 vs controlled-gate 0.9+). The guard and
gate8 are only meaningful with a trustworthy judge.

---

## 6. Conversations

`core/sage_conversations.py`: Supabase-primary (`sage_conversations` table,
`sync_metadata` fallback pre-migration), local JSON mirror
(`data/sage_conversations.json`). API: `list_saved_conversations`,
`get_conversation`, `save_conversation(messages, conv_id, title,
theme_filter, period_label)`, `delete_conversation`,
`export_conversation_markdown`.

- Assistant messages may carry an `articles` key (evidence popover payload)
  — it rides through persistence untouched.
- `period_label` is the opaque preset label string (emoji included); stored
  and restored verbatim, never parsed.
- Auto-title: first user message, 50 chars. Auto-save after each reply when
  a saved thread is active.

---

## 7. Knobs (`get_sage_settings()`, config/settings.py)

| Key | Default | Meaning |
|---|---|---|
| `sage_tier3_enabled` | True | Tier-3 assembly kill switch (False = byte-identical pre-6.2) |
| `sage_tier3_block_char_limit` | 1200 | macro prose ceiling per rollup block |
| `sage_tier3_milestones_per_block` | 3 | rendered milestones per block |
| `sage_tier3_max_blocks_per_month` | 2 | relevance-fill cap per month |
| `sage_tier3_budget_share` | 0.6 | Tier-3 share of the older-tier budget |
| `sage_tier3_query_rollups` | 3 | rollups feeding the milestone-biased query |
| `sage_tier3_query_char_limit` | 600 | appended milestone-tail cap |
| `sage_deep_research_enabled` | True | deep-mode kill switch (UI toggle chooses per session) |
| `sage_deep_max_subqueries` | 3 | Scout output cap / retrieval fan-out |
| `sage_deep_max_revisions` | 1 | bounded fact-check revision loop |
| `sage_guard_enabled` | False | grounding guard armed (page-7 toggle) |
| `sage_strict_grounding` | False | guard-managed: strict mandate + temp cap 0.2 |
| `sage_exemplars_enabled` | True | few-shot block from graded replies |
| `sage_exemplar_max_chars` | 2500 | exemplar block budget |

All merge key-filtered from `config/custom_settings.json` (Supabase-backed).

---

## 8. Evaluation & gates

- **Chat-time judge**: every grounded reply (both modes) is judged
  fire-and-forget → `sage_evaluations` (score + named unsupported claims),
  charted on Quality Evaluation. Judge failures are excluded, never zeros.
- **Macro eval harness**: `core/sage_eval.py` +
  `eval_sets/sage_macro_faithfulness.json` (8 multi-month questions).
  Arms: `legacy` (pre-6.2), `tier3` (fast, live defaults), `deep` (Phase 7
  pipeline). Each answer is judged against its own arm's context (judge
  truncation raised symmetrically to 60k).
  - `python -m core.sage_eval --gate` — Phase 6.2 gate (faithfulness
    no-regression + chars/covered-month −30% + coverage ≥0.75 + ≥6 scored
    per arm).
  - `python -m core.sage_eval --gate7` — Phase 7 gate (deep faithfulness
    ≥ fast − 0.02, coverage ≥0.75, deep mean latency ≤60s, ≥6 scored/arm).
  - `python -m core.sage_eval --gate8` — **standing regression gate**: run
    before shipping ANY assembly/prompt change (see §5.4).
  - Verdicts snapshot to `app_settings.sage_eval_latest`, rendered on the
    Eval Sets page. Eval rows never enter the rolling `sage_evaluations`
    baseline.

---

## 9. Extension guide (where to change what)

| Change | Touch | Tests to run |
|---|---|---|
| Persona / citation format | `SAGE_SYSTEM_PROMPT` (sage_agent.py) | `tests/test_sage_agent.py` |
| Context assembly / tiers / budget | `build_wiki_context` + helpers (sage_agent.py) | `test_sage_agent.py` |
| Pipeline stages / prompts / degradation | `core/sage_pipeline.py` | `tests/test_sage_pipeline.py` |
| Agent routing / models | gateway `_default_routing_config` + contracts enum | `tests/test_gateway_sage_tasks.py` |
| Spans / trace attributes | `tracing.pipeline_span` call sites | `tests/test_gateway_tracing.py` |
| Chat UI / toggle / captions | `pages/11_Sage.py` | compile + manual smoke |
| Archive browser / rollups UI | `pages/5_History.py` | compile + manual smoke |
| Conversation persistence | `core/sage_conversations.py` | `tests/test_sage_conversations.py` |
| Eval arms / gates | `core/sage_eval.py` | `tests/test_sage_eval.py` |
| Improvement loop (exemplars / guard / ratings) | `core/sage_exemplars.py`, `core/sage_guard.py`, `sage_reply_feedback` methods | `tests/test_sage_exemplars.py`, `tests/test_sage_guard.py` |

Rules of the road: no deterministic fallback for any Sage task; links only
verbatim from stored data; per-stage degradation never fatal (except the
Analyst, which surfaces a notice); keep the `response`/`provenance` return
contract; new session keys stay `sage_*`; new pages must be registered in
`_NAV_GROUPS` (a test enforces coverage). **After any assembly/prompt change,
run `python -m core.sage_eval --gate8` before shipping** — the standing
regression gate against the live baseline (§5.4).