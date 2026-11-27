"""
Google Gemini provider adapter.

Uses the ``google-genai`` SDK (>= 1.0) which communicates over REST/httpx,
replacing the deprecated ``google-generativeai`` package whose gRPC-aio
transport caused harmless-but-noisy ``InterceptedUnaryUnaryCall`` warnings
when event loops were torn down between ``asyncio.run()`` calls.
"""
import asyncio
import logging
from typing import Dict, Any, Optional, List
from .base import ProviderAdapter

logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import types
    _GENAI_AVAILABLE = True
except ImportError:
    genai = None  # type: ignore
    types = None  # type: ignore
    _GENAI_AVAILABLE = False


class GeminiProvider(ProviderAdapter):
    """Google Gemini API provider (google-genai >= 1.0, httpx transport)."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.8-flash",
        thinking_level: str = "low",
        request_timeout: float = 30.0,
    ):
        if not _GENAI_AVAILABLE:
            raise ImportError(
                "google-genai is required for GeminiProvider. "
                "Install it with: pip install 'google-genai>=1.0.0'"
            )
        # Bound the SDK itself: its tenacity/aiohttp retry loop otherwise
        # retries DNS/connection failures with unbounded-feeling backoff.
        self.client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                # HttpOptions.timeout is MILLISECONDS (SDK converts /1000).
                timeout=int(request_timeout * 1000),
                retry_options=types.HttpRetryOptions(
                    attempts=2, initial_delay=1.0, max_delay=2.0,
                    exp_base=1.0, jitter=0.0,
                ),
            ),
        )
        self.model_name = model
        self.thinking_level = thinking_level
        self.request_timeout = request_timeout

    async def _await_with_deadline(self, coro, timeout: float, label: str):
        """Hard wall-clock deadline that holds even if cancellation is delayed.

        ``asyncio.wait_for()`` awaits the cancelled task's completion after a
        timeout; a retry loop that defers ``CancelledError`` (tenacity /
        aiohttp DNS-executor resolution) can therefore hang it far past the
        timeout — observed in production as 12-139 minute "successful" Gemini
        fallbacks. ``asyncio.wait`` + abandon returns on time regardless; the
        SDK-level ``HttpOptions.timeout`` reaps the abandoned task.
        """
        task = asyncio.ensure_future(coro)
        done, pending = await asyncio.wait({task}, timeout=timeout)
        if pending:
            task.cancel()  # best-effort; deliberately NOT awaited
            raise TimeoutError(f"{label} timed out after {timeout}s")
        return task.result()

    # ------------------------------------------------------------------
    # Config builder
    # ------------------------------------------------------------------

    def _build_generation_config(
        self,
        temperature: float,
        max_output_tokens: int,
        schema: Optional[Dict] = None,
    ) -> "types.GenerateContentConfig":
        """Build a GenerateContentConfig for the new google-genai SDK.

        The new SDK natively supports ``ThinkingConfig``, so the old
        feature-detection shim for the deprecated SDK has been removed.

        AFC (Automatic Function Calling) is disabled because the gateway
        never passes tools — leaving it on produces noisy SDK warnings
        about using ``generate_content`` instead of ``Chat.send_message``.
        """
        kwargs: Dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        }
        if schema is not None:
            kwargs["response_mime_type"] = "application/json"
            kwargs["response_schema"] = schema
        if self.thinking_level:
            kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_level=self.thinking_level,
            )
        return types.GenerateContentConfig(**kwargs)

    # ------------------------------------------------------------------
    # ProviderAdapter interface
    # ------------------------------------------------------------------

    async def generate(self, prompt: str, **kwargs) -> Dict[str, Any]:
        config = self._build_generation_config(
            temperature=kwargs.get("temperature", 0.3),
            max_output_tokens=kwargs.get("max_tokens", 2000),
        )
        response = await self._await_with_deadline(
            self.client.aio.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=config,
            ),
            self.request_timeout,
            "GeminiProvider.generate()",
        )
        return {
            "text": response.text,
            "usage": self._extract_usage(response),
            "model": self.model_name,
        }

    async def generate_structured(
        self, prompt: str, schema: Dict, **kwargs
    ) -> Dict[str, Any]:
        config = self._build_generation_config(
            temperature=kwargs.get("temperature", 0.2),
            max_output_tokens=kwargs.get("max_tokens", 4000),
            schema=schema,
        )
        response = await self._await_with_deadline(
            self.client.aio.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=config,
            ),
            self.request_timeout,
            "GeminiProvider.generate_structured()",
        )
        return {
            "json": response.text,
            "usage": self._extract_usage(response),
            "model": self.model_name,
        }

    async def embed(self, texts: List[str], **kwargs) -> Dict[str, Any]:
        """Embed a batch of texts via ``models.embed_content``.

        ``dimensions`` pins the output vector width (768 for AI Pulse's
        ``article_embeddings.embedding vector(768)`` column; the model's
        native default is 3072, so the pin is mandatory for schema parity).
        """
        config = types.EmbedContentConfig(
            output_dimensionality=kwargs.get("dimensions", 768)
        )
        response = await self._await_with_deadline(
            self.client.aio.models.embed_content(
                model=self.model_name,
                contents=list(texts),
                config=config,
            ),
            self.request_timeout,
            "GeminiProvider.embed()",
        )
        vectors = [
            [float(v) for v in emb.values]
            for emb in getattr(response, "embeddings", [])
        ]
        return {
            "vectors": vectors,
            "usage": self._extract_usage(response),
            "model": self.model_name,
        }

    async def health_check(self) -> Dict[str, Any]:
        hc_timeout = min(self.request_timeout, 10.0)
        try:
            await self._await_with_deadline(
                self.client.aio.models.generate_content(
                    model=self.model_name,
                    contents="ping",
                    config=types.GenerateContentConfig(
                        max_output_tokens=8,
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(
                            disable=True
                        ),
                    ),
                ),
                hc_timeout,
                "health check",
            )
            return {"healthy": True, "model": self.model_name}
        except TimeoutError:
            return {"healthy": False, "model": self.model_name, "error": f"health check timed out after {hc_timeout}s"}
        except Exception as e:
            return {"healthy": False, "model": self.model_name, "error": str(e)}

    def get_capabilities(self) -> Dict[str, Any]:
        return {
            "provider": "google",
            "model": self.model_name,
            "tasks": ["categorise", "extract", "summarise", "synthesise", "project"],
            "context_window": 1_048_576,
            "max_output": 65_536,
            "supports_structured": True,
            "thinking_level": self.thinking_level,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_usage(response) -> Optional[Dict]:
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return None
        prompt = getattr(usage, "prompt_token_count", None)
        candidates = getattr(usage, "candidates_token_count", None)
        total = getattr(usage, "total_token_count", None)
        if prompt is None and candidates is None and total is None:
            return None
        return {
            "input_tokens": prompt,
            "output_tokens": candidates,
            "total_tokens": total,
        }
