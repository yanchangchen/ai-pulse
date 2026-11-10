"""Qwen Cloud provider adapter (OpenAI-compatible chat completions).

QwenCloud exposes an OpenAI-compatible endpoint:

    POST {base_url}/chat/completions
    Authorization: Bearer <API key>

See https://docs.qwencloud.com/developer-guides/getting-started/first-api-call
and https://docs.qwencloud.com/changelog/models.
"""
import asyncio
import json
from typing import Any, Dict, Optional

import httpx

from .base import ProviderAdapter

DEFAULT_BASE_URL = "https://token-plan.maas.qwencloudapi.com/compatible-mode/v1"

# Native 1M-token context window for qwen3.8-flash / qwen3.8-max.
DEFAULT_CONTEXT_WINDOW = 1_000_000


class QwenCloudProvider(ProviderAdapter):
    """Adapter for QwenCloud's OpenAI-compatible chat completions API."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str = "",
        model: str = "qwen3.8-flash",
        request_timeout: float = 180.0,
        context_window: int = DEFAULT_CONTEXT_WINDOW,
        enable_thinking: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.request_timeout = request_timeout
        self.context_window = context_window
        # qwen3.8-flash is a hybrid model with thinking enabled by default.
        # On a Sage-sized prompt (~32k chars) the hidden reasoning added
        # ~10k output tokens and 150s+ latency, tripping the gateway timeout.
        # QwenCloud documents ``enable_thinking: false`` as the way to skip it.
        self.enable_thinking = enable_thinking
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=request_timeout,
        )

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    async def generate(self, prompt: str, **kwargs) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": kwargs.get("temperature", 0.1),
            "max_tokens": kwargs.get("max_tokens", 1500),
            "enable_thinking": self.enable_thinking,
            "stream": False,
        }
        data = await self._post_chat(payload, "generate")
        return {
            "text": self._extract_text(data),
            "usage": self._parse_usage(data),
            "model": self.model,
        }

    async def generate_structured(
        self, prompt: str, schema: Dict, **kwargs
    ) -> Dict[str, Any]:
        """Return JSON matching ``schema``.

        The gateway validates the parsed object against the schema, so the
        provider only needs to produce clean JSON.  We ask for it in the
        prompt (rather than relying on a provider-specific JSON-mode flag)
        and strip any markdown fences the model wraps around it.
        """
        structured_prompt = (
            f"{prompt}\n\nOutput MUST be valid JSON matching this schema: "
            f"{json.dumps(schema)}"
        )
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": structured_prompt}],
            "temperature": kwargs.get("temperature", 0.1),
            "max_tokens": kwargs.get("max_tokens", 1500),
            "enable_thinking": self.enable_thinking,
            "stream": False,
        }
        data = await self._post_chat(payload, "generate_structured")
        return {
            "json": _strip_code_fences(self._extract_text(data)),
            "usage": self._parse_usage(data),
            "model": self.model,
        }

    async def _post_chat(self, payload: Dict, operation: str) -> Dict:
        try:
            response = await asyncio.wait_for(
                self.client.post("/chat/completions", json=payload),
                timeout=self.request_timeout,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"QwenCloudProvider.{operation}() timed out after "
                f"{self.request_timeout}s"
            )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _extract_text(data: Dict) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return (message.get("content") or "").strip()

    @staticmethod
    def _parse_usage(data: Dict) -> Optional[Dict]:
        usage = data.get("usage")
        if not usage:
            return None
        return {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }

    # ------------------------------------------------------------------
    # Health / capabilities
    # ------------------------------------------------------------------

    async def health_check(self) -> Dict[str, Any]:
        try:
            resp = await self.client.get("/models")
            if resp.status_code != 200:
                return {
                    "healthy": False,
                    "model": self.model,
                    "error": f"HTTP {resp.status_code}",
                }
            data = resp.json().get("data", [])
            ids = {
                item.get("id")
                for item in data
                if isinstance(item, dict) and item.get("id")
            }
            # A 200 with no listing still proves the endpoint is reachable;
            # only report unhealthy when the listing exists and lacks us.
            return {"healthy": (not ids) or self.model in ids, "model": self.model}
        except Exception as e:
            return {"healthy": False, "model": self.model, "error": str(e)}

    def get_capabilities(self) -> Dict[str, Any]:
        return {
            "provider": "qwen",
            "model": self.model,
            "tasks": [
                "categorise",
                "extract",
                "summarise",
                "synthesise",
                "project",
                "evaluate",
                "sage_chat",
            ],
            "context_window": self.context_window,
            "supports_structured": True,
        }

    async def close(self):
        await self.client.aclose()


def _strip_code_fences(text: str) -> str:
    """Remove a leading ``` / ```json fence and its closing fence."""
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()