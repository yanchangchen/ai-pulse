"""
Cloudflare Workers AI provider adapter (Phase 3.5 — Option A).

Serves dense embeddings via Cloudflare's OpenAI-compatible endpoint::

    POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/embeddings
    {"model": "@cf/baai/bge-base-en-v1.5", "input": ["title summary", ...]}

``bge-base-en-v1.5`` emits **768-dim** vectors, the same width as the existing
``article_embeddings.embedding vector(768)`` column and the retired-from-service
Gemini route — but vectors from different models occupy different spaces, so
the gateway's ``embedding`` policy must never mix them.

This adapter is embedding-only: generation methods raise ``NotImplementedError``
so a routing mistake surfaces instead of producing empty text.  The request
loop, retries, health latching, provenance, ``llm_calls`` mirroring and OTel
tracing all live in ``ModelGateway``.
"""
import asyncio
from typing import Any, Dict, List

import httpx

from .base import ProviderAdapter

_DEFAULT_MODEL = "@cf/baai/bge-base-en-v1.5"
_DEFAULT_TIMEOUT = 30.0


class CloudflareProvider(ProviderAdapter):
    """Cloudflare Workers AI embeddings provider (OpenAI-compatible API)."""

    def __init__(
        self,
        account_id: str,
        api_token: str,
        model: str = _DEFAULT_MODEL,
        request_timeout: float = _DEFAULT_TIMEOUT,
    ):
        if not account_id or not api_token:
            raise ValueError(
                "CloudflareProvider requires both CF_ACCOUNT_ID and CF_API_TOKEN"
            )
        self.account_id = account_id
        self.model = model
        self.request_timeout = request_timeout
        self.client = httpx.AsyncClient(
            base_url=(
                "https://api.cloudflare.com/client/v4/accounts/"
                f"{account_id}/ai/v1"
            ),
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=request_timeout,
        )

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    async def embed(self, texts: List[str], **kwargs) -> Dict[str, Any]:
        """Embed a batch of texts.

        Returns ``{"vectors": [[float, ...], ...], "usage": dict|None,
        "model": str}``.  Vectors are returned in request order and validated
        strictly — a count/dimension mismatch raises so the gateway discards
        the whole batch instead of writing partial or misaligned rows
        (``core/embedding.py`` is all-or-nothing per batch).
        """
        texts = list(texts or [])
        if not texts:
            return {"vectors": [], "usage": None, "model": self.model}

        payload = {"model": self.model, "input": texts}
        try:
            response = await asyncio.wait_for(
                self.client.post("/embeddings", json=payload),
                timeout=self.request_timeout,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"CloudflareProvider.embed() timed out after {self.request_timeout}s"
            )
        response.raise_for_status()

        data = response.json()
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ValueError(
                "Cloudflare embedding response is missing the 'data' list"
            )

        # OpenAI-compatible responses carry an explicit index; honour it when
        # present so batch order survives any provider-side reordering.
        if all(isinstance(r, dict) and isinstance(r.get("index"), int) for r in rows):
            rows = sorted(rows, key=lambda r: r["index"])

        try:
            vectors = [
                [float(v) for v in (r.get("embedding") or [])]
                for r in rows
                if isinstance(r, dict)
            ]
        except (TypeError, ValueError) as e:
            raise ValueError(f"Cloudflare embedding response is malformed: {e}")

        if len(vectors) != len(texts):
            raise ValueError(
                f"Cloudflare embedding count mismatch: {len(texts)} texts in, "
                f"{len(vectors)} vectors out"
            )
        if any(not vector for vector in vectors):
            raise ValueError("Cloudflare embedding response contains an empty vector")

        expected_dims = kwargs.get("dimensions")
        if expected_dims is not None:
            expected_dims = int(expected_dims)
            bad = [len(v) for v in vectors if len(v) != expected_dims]
            if bad:
                raise ValueError(
                    "Cloudflare embedding dimension mismatch: expected "
                    f"{expected_dims}, got {sorted(set(bad))}"
                )

        usage = data.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        if isinstance(prompt_tokens, int):
            parsed_usage = {
                "input_tokens": prompt_tokens,
                "output_tokens": 0,
                "total_tokens": prompt_tokens,
            }
        else:
            # Cloudflare's OpenAI-compatible embeddings response currently
            # omits ``usage``.  Record a ~3-chars/token estimate (the same
            # heuristic as AITaskRequest.estimated_input_tokens) so
            # llm_calls still carries usable quota data for the Phase 3.6
            # watch; the Cloudflare dashboard remains the exact source.
            estimated = sum(max(1, len(t) // 3) for t in texts)
            parsed_usage = {
                "input_tokens": estimated,
                "output_tokens": 0,
                "total_tokens": estimated,
                "estimated": True,
            }

        return {"vectors": vectors, "usage": parsed_usage, "model": self.model}

    # ------------------------------------------------------------------
    # Generation (unsupported — this adapter claims only the embedding task)
    # ------------------------------------------------------------------

    async def generate(self, prompt: str, **kwargs) -> Dict[str, Any]:
        raise NotImplementedError(
            "CloudflareProvider is embedding-only; route generation tasks to "
            "a Gemini/Qwen model"
        )

    async def generate_structured(
        self, prompt: str, schema: Dict, **kwargs
    ) -> Dict[str, Any]:
        raise NotImplementedError(
            "CloudflareProvider is embedding-only; route structured tasks to "
            "a Gemini/Qwen model"
        )

    # ------------------------------------------------------------------
    # ProviderAdapter interface
    # ------------------------------------------------------------------

    async def health_check(self) -> Dict[str, Any]:
        """Probe with a one-word embedding (verifies auth, model and width)."""
        try:
            await self.embed(["ping"], dimensions=768)
            return {"healthy": True, "model": self.model}
        except Exception as e:
            return {"healthy": False, "model": self.model, "error": str(e)}

    def get_capabilities(self) -> Dict[str, Any]:
        return {
            "provider": "cloudflare",
            "model": self.model,
            # bge-base-en-v1.5 caps at 512 tokens PER TEXT.  The gateway
            # bypasses whole-batch context-fit for EMBEDDING tasks (the batch
            # estimate is not a single request's token count); this value is
            # the honest per-text limit for display/diagnostics.
            "context_window": 512,
            "max_input_tokens_per_text": 512,
            "tasks": ["embedding"],
            "dimensions": 768,
            "supports_structured": False,
        }

    async def close(self):
        await self.client.aclose()