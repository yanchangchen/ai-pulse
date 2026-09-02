"""
Ollama Cloud provider adapter.
"""
import httpx
import json
from typing import Dict, Any, Optional
from .base import ProviderAdapter


class OllamaCloudProvider(ProviderAdapter):
    """Ollama Cloud API provider."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "nemotron-3-super:cloud",
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=180.0,
        )

    async def generate(self, prompt: str, **kwargs) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": kwargs.get("temperature", 0.1),
                "num_ctx": kwargs.get("num_ctx", 131072),
                **kwargs.get("options", {}),
            },
        }
        response = await self.client.post("/api/generate", json=payload)
        response.raise_for_status()
        data = response.json()
        return {
            "text": data.get("response", ""),
            "usage": self._parse_usage(data),
            "model": self.model,
        }

    async def generate_structured(
        self, prompt: str, schema: Dict, **kwargs
    ) -> Dict[str, Any]:
        structured_prompt = (
            f"{prompt}\n\nOutput MUST be valid JSON matching this schema: {json.dumps(schema)}"
        )
        payload = {
            "model": self.model,
            "prompt": structured_prompt,
            "stream": False,
            "format": "json",
            "options": kwargs.get("options", {}),
        }
        response = await self.client.post("/api/generate", json=payload)
        response.raise_for_status()
        data = response.json()
        return {
            "json": data.get("response", ""),
            "usage": self._parse_usage(data),
            "model": self.model,
        }

    def _parse_usage(self, data: Dict) -> Optional[Dict]:
        if "eval_count" in data or "prompt_eval_count" in data:
            return {
                "input_tokens": data.get("prompt_eval_count", 0),
                "output_tokens": data.get("eval_count", 0),
                "total_tokens": data.get("prompt_eval_count", 0)
                + data.get("eval_count", 0),
            }
        return None

    async def health_check(self) -> Dict[str, Any]:
        try:
            resp = await self.client.get("/api/tags")
            models = resp.json().get("models", [])
            available = any(
                self.model.replace(":cloud", "") in m.get("name", "") for m in models
            )
            return {"healthy": available, "model": self.model}
        except Exception as e:
            return {"healthy": False, "model": self.model, "error": str(e)}

    def get_capabilities(self) -> Dict[str, Any]:
        ctx = 262144 if "nemotron" in self.model else 131072
        return {
            "provider": "ollama",
            "model": self.model,
            "tasks": ["categorise", "extract", "summarise", "synthesise", "project"],
            "context_window": ctx,
            "supports_structured": True,
        }

    async def close(self):
        await self.client.aclose()