# Embedding Providers & Migration Options

> **Status (2026-11-09): decision recorded, implementation pending.** The app still embeds
> exclusively through Google Gemini (`gemini-embedding-001`). **Option A (Cloudflare Workers
> AI) is selected**; Options B (Jina) and C (Render) are retained as fallbacks. Nothing below
> is wired into the gateway yet — this page is the decision record and implementation runbook.

Moving embeddings off Google is a *provider* change, not a model-quality change: the
pipeline, storage, and retrieval RPCs stay the same as long as the replacement emits
**768-dimensional** vectors. Two free hosted open-model providers (Cloudflare, Jina) and a
self-deployed Render service are evaluated here.

## TL;DR

| Option | Model | Dims | Free tier (verified 2026-11-09) | `vector(768)` change? | Effort |
|---|---|---|---|---|---|
| **Cloudflare Workers AI** ⭐ | `@cf/baai/bge-base-en-v1.5` | **768** | 10,000 Neurons/day; bge-base $0.0666/M input tokens | None | New adapter + registry/routing entry |
| **Jina AI** | `jina-embeddings-v5-omni-nano` (or v5-text at 768) | **768** | Free API key with free tokens; 100 RPM / 100k TPM | None | Same adapter as Cloudflare |
| **Render (self-deployed)** | `BAAI/bge-base-en-v1.5` via `fastembed` (ONNX) | **768** | 750 instance hours/month; sleeps after 15 min idle | None | Docker service + adapter |
| *Status quo* | `gemini-embedding-001` | 768 | Google API quota/paid | None | — |

**Decision:** implement **Option A (Cloudflare)** first. Keep **Option C (Render)** as the
first fallback because it serves the **same model**, then **Option B (Jina)** for
multilingual/long-context needs. See [Fallback rule](#fallback-rule-never-mix-model-spaces)
and [Decision record & trigger metrics](#8-decision-record--trigger-metrics).

### Baseline (live Supabase, 2026-11-09)

| Metric | Value |
|---|---|
| Articles | 6,235 |
| Stored embeddings | 650 (10.4%) |
| Embedding model | `gemini-embedding-001`, 768 dims — 100% of stored rows |
| Backlog | 5,585 articles |
| Embedding window | 2026-10-26 → 2026-11-08 |
| Gateway calls (`llm_calls`, `task_type='embedding'`) | 67 total: 22 success, 45 failed |
| Failure cause | Gemini 429 `embed_content_free_tier_requests`, 100 requests/min |

This baseline is why the migration is attractive: Cloudflare's free allowance is hundreds of
times the steady-state ingest volume, while Gemini's free embedding quota is already the
throughput bottleneck.

---

## 1. How embeddings work today

```
bg_refresher.py / backfill_embeddings.py
  └─ core.embedding.embed_unembedded_articles()
       ├─ SupabaseManager.get_unembedded_articles()   # DB is the checkpoint (LEFT JOIN)
       ├─ compose_embed_text(article)                 # "title summary"
       ├─ embed_texts(batch)                          # all-or-nothing per batch
       │    └─ ModelGateway.execute(TaskType.EMBEDDING)
       │         └─ ProviderAdapter.embed(texts, dimensions=768)
       │              └─ GeminiProvider.embed()       # the only implementation today
       └─ SupabaseManager.insert_article_embeddings(rows)
```

Key files:

| Concern | File |
|---|---|
| Batch/ingest/backfill loop | `core/embedding.py` |
| Model Gateway routing | `core/ai_gateway/gateway.py` |
| Provider contract | `core/ai_gateway/providers/base.py` (`ProviderAdapter.embed`, default `NotImplementedError`) |
| Gemini implementation | `core/ai_gateway/providers/gemini.py` (`models.embed_content`, `output_dimensionality=768`) |
| Settings | `config/settings.py` (`GEMINI_EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`, `get_embedding_settings()`) |
| Storage | `supabase_migration_article_embeddings.sql` (`article_embeddings`, `match_article_embeddings`) |
| Manual backfill | `backfill_embeddings.py` |
| Ingest hook | `core/bg_refresher.py` (after persistence, best-effort) |

### Hard constraints for any replacement

1. **768 dims, or a migration.** `article_embeddings.embedding` is `vector(768)` and
   `match_article_embeddings(query_embedding vector(768), …)` is typed the same. A model
   with another width requires altering the column, updating the RPC, dropping/recreating
   the HNSW index, and re-embedding every row.
2. **One vector per input.** `core/embedding.py` discards the whole batch if the provider
   returns a different count, so partial responses must be avoided by the adapter.
3. **Record the model.** Every row stores `embedding_model`; keep it accurate so mixed-model
   data is auditable and re-embeds are targetable.
4. **Best-effort.** Embedding must never fail an ingestion run. A cold start, timeout, or
   rate limit skips the batch; the DB checkpoint means the next run/backfill picks it up.
5. **No cross-model fallback.** (See below.)

### Fallback rule: never mix model spaces

Cosine similarity is only meaningful between vectors from the *same model*. Even two 768-dim
models produce incomparable spaces. The current routing policy intentionally has
`fallback: []` for `embedding`. When swapping providers:

- ✅ Primary Cloudflare `bge-base-en-v1.5`, fallback self-hosted `bge-base-en-v1.5`
  (identical weights → identical space, different host).
- ❌ Primary Cloudflare `bge-base-en-v1.5`, fallback Gemini `gemini-embedding-001`
  (different space → silently degraded retrieval).
- ❌ Primary Jina, fallback anything other than the same Jina model.

If no same-model fallback is available, leave the chain empty and rely on backfill for
resilience.

---

## 2. Option A — Cloudflare Workers AI (recommended)

| | |
|---|---|
| **Model** | `@cf/baai/bge-base-en-v1.5` (open weights, BGE) |
| **Dimensions** | **768** |
| **Max input** | 512 tokens per text — comfortable for `compose_embed_text()` (`"title summary"`, summaries ≤1500 chars) |
| **Free tier** | Workers AI: **10,000 Neurons/day**. bge-base is **$0.0666 per M input tokens**; at Cloudflare's token↔Neuron equivalence this is roughly **1–2M input tokens/day** free (estimated) |
| **API** | OpenAI-compatible embeddings endpoint, plus a native Workers AI endpoint |
| **Auth** | Account ID + API token with Workers AI permission |

**Endpoints**

```http
# OpenAI-compatible (preferred — same shape as Jina)
POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/embeddings
Authorization: Bearer {api_token}
Content-Type: application/json

{"model": "@cf/baai/bge-base-en-v1.5", "input": ["title summary", "..."]}
```

```http
# Native Workers AI
POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/baai/bge-base-en-v1.5
{"text": ["title summary", "..."]}
```

The OpenAI-compatible response carries `data[i].embedding` and `usage.prompt_tokens`;
the native response carries `result.data` (array of vectors). Pick one and normalise in the
adapter.

**Why it is the default choice**

- Exact 768-dim match — no schema, RPC, HNSW, or re-embed requirement (though a one-time
  re-embed is still recommended so the whole index comes from one model).
- Free quota is hundreds of times above AI Pulse's volume (~30 articles/run, title+summary
  ≈ low thousands of tokens/day).
- Open-weight model: if Cloudflare's terms change, the same `bge-base-en-v1.5` can move to
  Jina/self-hosted with no model-space change.
- Endpoint is OpenAI-compatible, so the same adapter serves Jina.

**Caveats**

- 512-token cap; fine today, but would bind if `compose_embed_text()` ever includes full text.
- Free-tier quotas are revocable — verify the dashboard before relying on it.
- Model slug and OpenAI-compatible path should be re-verified at implementation time.

---

## 3. Option B — Jina AI

| | |
|---|---|
| **Model** | `jina-embeddings-v5-omni-nano` (768); text-only v5 models use Matryoshka dimensions, request 768 |
| **Dimensions** | **768** (omni-nano); v5-text is selectable |
| **Free tier** | Free API key with free tokens; **100 RPM / 100,000 TPM** (token grant is not stated on the public page — check the dashboard at signup) |
| **API** | `POST https://api.jina.ai/v1/embeddings`, bearer key |
| **Auth** | `JINA_API_KEY` |

```http
POST https://api.jina.ai/v1/embeddings
Authorization: Bearer {JINA_API_KEY}
Content-Type: application/json

{"model": "jina-embeddings-v5-omni-nano", "input": ["title summary", "..."]}
```

Response is OpenAI-shaped (`data[i].embedding`) and includes a usage object with the exact
token count. The public example exposes `model`, `input`, `normalized`, and
`embedding_type` — not a `dimensions` field — so prefer the fixed-768 `omni-nano` model.
The v5-text models are Matryoshka-based; confirm on the model card that a 768-dim output
can be selected before using one.

**Pros**

- Exact 768 dims, long context, multilingual — better if sources include non-English items.
- Same adapter as Cloudflare (OpenAI-shaped embeddings API).
- Free rate limits (100 RPM / 100k TPM) are ample for batch backfills.

**Caveats**

- Jina's model lineup rotates faster than Google's; pin the exact model id and verify its
  current dimension/output options before coding. Do not copy a model name from this page
  without checking the model card.
- Check the model licence if you may later self-host the same weights.
- Free-token grant is promotional and unstated publicly; do not design around a fixed number.

---

## 4. Option C — Self-deployed embedding service on Render

This is the "own the whole path" option: a tiny Dockerized HTTP service on Render's free web
tier, running an open model with ONNX Runtime. It is still hosted (no local process), but you
control the model, the API, and the data path.

### Why `fastembed` and not PyTorch/TEI

Render's free web service has no published CPU/RAM guarantee (historically ~512 MB) and an
ephemeral filesystem. PyTorch + `sentence-transformers` or Hugging Face TEI will likely exceed
the free memory budget. `fastembed` runs quantised ONNX models with a small footprint:

| Model | Dims | fastembed size | Verdict on free Render |
|---|---|---|---|
| `BAAI/bge-base-en-v1.5` | **768** | **0.210 GB** | ✅ Use this |
| `nomic-ai/nomic-embed-text-v1.5` | 768 | 0.520 GB | ⚠️ Too tight alongside Python/ONNX — avoid on free |

Both are 768-dim, so either keeps the schema. Use `bge-base-en-v1.5` for memory headroom;
it also matches the Cloudflare option's model space, enabling a same-model fallback.

### Render free-tier facts that shape the design

- **Spins down after 15 minutes** without inbound traffic; next request needs **~1 minute**
  to cold-start.
- **750 free instance hours/month** per workspace. A single always-on service fits (~730 h);
  a sleeping service does not consume hours.
- **Ephemeral disk** — files are wiped on redeploy/restart/idle shutdown; no persistent disk.
  Bake the model into the image (or accept a download on every cold start).
- **No shell access, no private networking, no one-off jobs** on free.
- Outbound public traffic has monthly limits; SMTP ports are blocked.
- Render says free services are not for production. For a personal dashboard this is
  acceptable *because embedding is advisory and non-gating*; if it becomes critical, move to
  a paid instance.

### Reference implementation (not yet in this repo)

Suggested layout: `embedding_service/{main.py,requirements.txt,Dockerfile}` plus a
`render.yaml` blueprint.

**`main.py`**

```python
import os
from typing import List, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from fastembed import TextEmbedding

MODEL_NAME = os.getenv("EMBED_MODEL", "BAAI/bge-base-en-v1.5")
SERVICE_TOKEN = os.environ["EMBEDDING_SERVICE_TOKEN"]

app = FastAPI()
# cache_dir points at the image-baked model; see Dockerfile
model = TextEmbedding(model_name=MODEL_NAME, cache_dir=os.getenv("FASTEMBED_CACHE_PATH", "/models"))


class EmbedRequest(BaseModel):
    texts: List[str]
    dimensions: Optional[int] = None


@app.get("/healthz")
def healthz():
    return {"ok": True, "model": MODEL_NAME, "dimensions": 768}


@app.post("/embed")
def embed(req: EmbedRequest, authorization: str = Header(default="")):
    if authorization != f"Bearer {SERVICE_TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")
    if not req.texts:
        return {"vectors": [], "model": MODEL_NAME}
    vectors = [v.tolist() for v in model.embed(req.texts)]
    return {"vectors": vectors, "model": MODEL_NAME}
```

**`requirements.txt`**

```
fastapi
uvicorn[standard]
fastembed
```

**`Dockerfile`**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
ENV FASTEMBED_CACHE_PATH=/models PIP_NO_CACHE_DIR=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Bake the ONNX model into the image — Render free disk is ephemeral.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-base-en-v1.5', cache_dir='/models')"
COPY main.py .
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
```

**`render.yaml`**

```yaml
services:
  - type: web
    name: aipulse-embeddings
    runtime: docker
    plan: free
    dockerContext: ./embedding_service
    dockerfilePath: ./embedding_service/Dockerfile
    healthCheckPath: /healthz
    envVars:
      - key: EMBED_MODEL
        value: BAAI/bge-base-en-v1.5
      - key: EMBEDDING_SERVICE_TOKEN
        sync: false   # set in the Render dashboard; never commit it
```

### Operational notes

- **Cold starts:** set the gateway adapter timeout to ~90 s for this provider (not 30 s).
  A cold-start miss skips one batch; `backfill_embeddings.py` re-drains it.
- **Keep-alive:** an external ping every <15 min keeps the service warm, but consumes nearly
  all 750 free hours. Do this only if embedding latency matters; otherwise let it sleep.
- **Auth:** the service is public HTTPS; require the bearer token. Never deploy it
  unauthenticated.
- **Model updates:** changing `EMBED_MODEL` changes the vector space. Treat it exactly like a
  provider switch: re-embed everything.
- **Backfill** still runs from your machine/Streamlit process, pointed at the Render URL —
  Render free has no one-off jobs.
- **Same-model fallback:** because this serves `bge-base-en-v1.5`, it is the only fallback
  that is safe to pair with Cloudflare's identical model.

---

## 5. Comparison

| Criterion | Cloudflare | Jina | Render self-hosted |
|---|---|---|---|
| Free quota | ~1–2M tokens/day (est.) | Free tokens + 100 RPM / 100k TPM | 750 h/month, sleeps |
| Ops burden | None | None | Docker + Render + keep-alive decisions |
| Cold start | None | None | ~60 s after 15 min idle |
| Data path | Cloudflare | Jina | Your Render service |
| Model control | Cloudflare-hosted open model | Jina-hosted | Exact weights you pin |
| Failure mode | Rate limit / quota | Rate limit / quota | Spin-down, OOM, redeploy |
| Best for | Default, lowest effort | Multilingual/long context | Control, same-model fallback |

All three keep `vector(768)`. None requires a schema migration.

---

## 6. Integration runbook (shared by all three)

### 6.1 Add an adapter

Add one provider adapter (e.g. `core/ai_gateway/providers/openai_compat.py`) implementing
`ProviderAdapter.embed()`. Cloudflare (OpenAI-compatible) and Jina share it; the Render service
needs a trivial variant for its `/embed` shape. `generate()` / `generate_structured()` can
raise `NotImplementedError` — embedding-only providers never serve generation tasks.

```python
async def embed(self, texts, **kwargs):
    payload = {"model": self.model_slug, "input": texts}
    r = await self._client.post(self.embeddings_url, json=payload, headers=self._headers)
    r.raise_for_status()
    body = r.json()
    return {
        "vectors": [item["embedding"] for item in body["data"]],
        "usage": body.get("usage", {}),
        "model": body.get("model", self.model_slug),
    }
```

The gateway's `_execute_with_validation()` already special-cases `TaskType.EMBEDDING` and
calls `provider.embed(...)`; registration is the main gateway change.

**Context-fit gotcha (must fix when adding an embedding provider):** `_execute_inner()` calls
`_check_context_fit()` with the **batch's total** estimated tokens. Cloudflare's 512-token
limit is **per text**, so a normal 50-article batch (~5k tokens) would be rejected as
"exceeds context budget". Add a `request.task is TaskType.EMBEDDING` bypass for context-fit
(or return a deliberately large `context_window` from the embedding provider) and a
regression test that a multi-item batch is accepted. This does not affect the current Gemini
route because its registry entry carries a very large context window.

### 6.2 Register the model and route

In `core/ai_gateway/gateway.py`:

1. Add a registry entry with `"tasks": ["embedding"]`.
2. Point the `embedding` routing policy's `primary` at it.
3. Leave `fallback: []` unless the fallback serves the **same model**.

### 6.3 Settings / secrets

Read credentials through `config.settings` (never `os.getenv` directly inside
`_init_providers` — see the historical gotcha in [CONFIGURATION.md](CONFIGURATION.md)).

| Provider | Keys |
|---|---|
| Cloudflare | `CF_ACCOUNT_ID`, `CF_API_TOKEN` |
| Jina | `JINA_API_KEY` |
| Render | `EMBEDDING_SERVICE_URL`, `EMBEDDING_SERVICE_TOKEN` |

`embedding_model` in `get_embedding_settings()` is a **gateway provider key**, not a raw
model id: `core/embedding.py::_preferred_embedding_model()` ignores values that are not in
`get_gateway().providers` and silently falls back to the routing default. Keep the registry
key and this setting in sync.

Add an explicit switch (e.g. `EMBEDDING_PROVIDER`, default `gemini`). Flip it to the new
provider as the **first step of a controlled migration window**, then run the re-embed and
full backfill in §6.4; do not enable dense retrieval until every row carries the new model.
Do not auto-switch just because credentials are present, and do not leave the switch
half-done — normal ingest would keep writing old-model vectors. The switch also keeps the
app working for anyone without the new credentials.

### 6.4 Re-embed during the migration window

`article_embeddings` is one row per article (`UNIQUE(article_id)`), and
`insert_article_embeddings()` is a plain insert — a re-embed is not possible with the current
path. During one controlled migration window:

1. Add a re-embed path in `core/supabase_client.py`: either an `upsert(..., on_conflict="article_id")`
   or a `delete_article_embeddings(article_ids)` step.
2. Add a one-off script (or extend `backfill_embeddings.py` with `--reembed`) that reads all
   rows/articles, embeds with the new model, and upserts.
3. Flip `EMBEDDING_PROVIDER` to the new provider, then run the re-embed and the backlog
   backfill to completion.
4. Verify: row count unchanged, `embedding_model` all equal, no 768-dim errors, and
   `core/retrieval_eval.py`'s hit-rate gate does not regress. Only then resume normal dense
   retrieval use.

Do **not** leave a partially re-embedded index in service: mixed-model vectors degrade
retrieval silently (distances are meaningless across spaces).

### 6.5 Tests

Extend `tests/test_embedding.py` with an adapter test that mocks the HTTP response and
asserts: vector count matches input count, dimensions are 768, and a malformed/partial
response yields `([], None)` (the existing all-or-nothing guard). `tests/conftest.py` already
pins Supabase offline, so no network is touched.

### 6.6 Rollback

Keep the Gemini registry entry and key in place until the new model's backfill and retrieval
gate pass. Rollback = point the `embedding` routing policy back at Gemini and re-embed again
(the same upsert path). Because the vector spaces differ, a rollback is a full re-embed, not
a config flip.

---

## 7. Decision checklist

- [ ] Free-tier terms re-verified on the provider dashboard (quotas change).
- [ ] Model card confirms 768 output dims and the exact model id.
- [ ] Input length comfortable vs. the model's token cap (512 for Cloudflare bge-base).
- [ ] Fallback chain is empty or same-model-only.
- [ ] Adapter returns exactly one vector per input, or fails the whole batch.
- [ ] Re-embed path exists and has been run against the full table.
- [ ] `--dry-run` backfill reports 0 remaining after the re-embed.
- [ ] `core/retrieval_eval.py` hit-rate gate checked before/after.
- [ ] Docs updated: routing table in [ARCHITECTURE.md](ARCHITECTURE.md), settings in
      [CONFIGURATION.md](CONFIGURATION.md), storage contract in [SUPABASE.md](SUPABASE.md).

---

## 8. Decision record & trigger metrics

**Decision (2026-11-09):** implement **Option A (Cloudflare)** first; retain **Option C
(Render)** as the first fallback and **Option B (Jina)** as the second. Do not build Option C
until the metrics below show a real Cloudflare limit.

### Why Option A first

- One OpenAI-compatible adapter, exact 768-dim parity, no schema/RPC/HNSW migration.
- Free allowance is hundreds of times the steady-state ingest volume: ~30–60 articles/day ×
  ~50–100 tokens ≈ **3–6k tokens/day**, against an estimated **1–2M tokens/day** free.
- One-time backfill: ~5,585 articles × ~100 tokens ≈ **560k tokens**, still within one
  estimated day of free capacity, but spread it over several runs so quota headroom stays
  visible.
- Gemini's free embedding quota is already the bottleneck: 45 of 67 logged calls returned
  429 (`100 requests/min`).

### Metrics to watch (for at least 14 days after backfill)

Exclude the one-time backfill burst when judging steady-state headroom. Sources:
`llm_calls` (`task_type='embedding'`), DB counts, the Cloudflare dashboard Neurons graph, and
`core/retrieval_eval.py`. Full definitions live in
[Future.md Phase 3.6](../Future.md#phase-3--embedding-pipeline-dense-half-of-pillar-a).

| Metric | Healthy | Trigger to build Option C |
|---|---|---|
| Daily tokens / calls | ≤25% of estimated free capacity; no day >50% | >50% for 7 consecutive days |
| Cloudflare dashboard Neurons | <5,000/day | >8,000/day sustained (free cap 10,000/day) |
| Error rate (429/5xx/timeout) | <1% of embedding calls | >5% for 3 consecutive days, or outage >30 min |
| p95 latency | <2 s | >5 s for 7 consecutive days |
| Backlog (`articles - article_embeddings`) | 0 after backfill, then < one day's ingest | grows 7 days, or not drained within 14 days |
| Model uniformity | 100% one `embedding_model`, 768 dims | any mix is a re-embed stop condition, not a provider trigger |
| Retrieval hit-rate gate | no >5 pp drop vs baseline | confirmed model-quality regression |

**Fallback order when triggered:** **C first** (same `BAAI/bge-base-en-v1.5` weights → same
vector space → routing change, no re-embed), **B second** (different weights → full
re-embed; reserve for multilingual/long-context requirements or if A and C are both
constrained).

### Tracking queries (Supabase SQL editor)

```sql
-- Coverage and model uniformity
SELECT (SELECT count(*) FROM articles)                              AS articles,
       (SELECT count(*) FROM article_embeddings)                    AS embeddings,
       (SELECT count(*) FROM articles)
         - (SELECT count(*) FROM article_embeddings)                AS backlog;

SELECT embedding_model, count(*) AS rows
FROM article_embeddings
GROUP BY 1
ORDER BY 2 DESC;

-- Daily embedding usage, success rate, and p95 latency
SELECT date_trunc('day', created_at)                                AS day,
       count(*)                                                     AS calls,
       count(*) FILTER (WHERE success)                              AS ok,
       round(100.0 * count(*) FILTER (WHERE success) / count(*), 2) AS success_pct,
       sum(tokens_in) FILTER (WHERE success)                        AS tokens_in,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)     AS p95_ms
FROM llm_calls
WHERE task_type = 'embedding'
GROUP BY 1
ORDER BY 1 DESC;
```

Failed calls have `tokens_in = NULL` and `model`/`provider = NULL` in `llm_calls`, so token
totals must filter on `success`. Cloudflare's dashboard Neurons graph is the quota source of
truth; the query above is the application-side proxy.

## 9. Sources (verified 2026-11-09)

- Cloudflare Workers AI — bge-base-en-v1.5 (768 dims, 512-token cap, $0.0666/M tokens):
  <https://developers.cloudflare.com/workers-ai/models/bge-base-en-v1.5/>
- Cloudflare Workers AI pricing (10,000 Neurons/day free):
  <https://developers.cloudflare.com/workers-ai/platform/pricing/>
- Jina Embeddings API (v5 models, 768-dim omni-nano, 100 RPM / 100k TPM):
  <https://jina.ai/embeddings/>
- Render free tier (15-min spin-down, ~1 min cold start, 750 h/month, ephemeral disk):
  <https://render.com/docs/free>
- fastembed supported models (bge-base-en-v1.5: 768 dims / 0.210 GB; nomic-embed-text-v1.5:
  768 dims / 0.520 GB): <https://qdrant.github.io/fastembed/examples/Supported_Models/>
- Ollama Cloud docs (no embedding endpoint documented — not a viable hosted option):
  <https://docs.ollama.com/cloud>