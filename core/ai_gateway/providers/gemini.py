"""
Google Gemini provider adapter.

Uses the ``google-genai`` SDK (>= 1.0) which communicates over REST/httpx,
replacing the deprecated ``google-generativeai`` package whose gRPC-aio
transport caused harmless-but-noisy ``InterceptedUnaryUnaryCall`` warnings
when event loops were torn down between ``asyncio.run()`` calls.
"""
import asyncio
import logging
from typing import Dict, Any, Optional
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
        model: str = "gemini-3.5-flash",
        thinking_level: str = "low",
        request_timeout: float = 30.0,
    ):
        if not _GENAI_AVAILABLE:
            raise ImportError(
                "google-genai is required for GeminiProvider. "
                "Install it with: pip install 'google-genai>=1.0.0'"
            )
        self.client = genai.Client(api_key=api_key)
        self.model_name = model
        self.thinking_level = thinking_level
        self.request_timeout = request_timeout

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
        try:
            response = await asyncio.wait_for(
                self.client.aio.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=config,
                ),
                timeout=self.request_timeout,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"GeminiProvider.generate() timed out after {self.request_timeout}s"
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
        try:
            response = await asyncio.wait_for(
                self.client.aio.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=config,
                ),
                timeout=self.request_timeout,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"GeminiProvider.generate_structured() timed out after {self.request_timeout}s"
            )
        return {
            "json": response.text,
            "usage": self._extract_usage(response),
            "model": self.model_name,
        }

    async def health_check(self) -> Dict[str, Any]:
        hc_timeout = min(self.request_timeout, 10.0)
        try:
            await asyncio.wait_for(
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
                timeout=hc_timeout,
            )
            return {"healthy": True, "model": self.model_name}
        except asyncio.TimeoutError:
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
