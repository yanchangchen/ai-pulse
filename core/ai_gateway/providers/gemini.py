"""
Google Gemini provider adapter.
"""
import json
import google.generativeai as genai
from typing import Dict, Any, Optional
from .base import ProviderAdapter


class GeminiProvider(ProviderAdapter):
    """Google Gemini API provider."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.5-flash",
        thinking_level: str = "low",
    ):
        genai.configure(api_key=api_key)
        self.model_name = model
        self.thinking_level = thinking_level
        self.model = genai.GenerativeModel(model)

    async def generate(self, prompt: str, **kwargs) -> Dict[str, Any]:
        config = genai.types.GenerationConfig(
            thinking_config=genai.types.ThinkingConfig(thinking_level=self.thinking_level),
            temperature=kwargs.get("temperature", 0.3),
            max_output_tokens=kwargs.get("max_tokens", 2000),
        )
        response = await self.model.generate_content_async(prompt, generation_config=config)
        return {
            "text": response.text,
            "usage": self._extract_usage(response),
            "model": self.model_name,
        }

    async def generate_structured(
        self, prompt: str, schema: Dict, **kwargs
    ) -> Dict[str, Any]:
        config = genai.types.GenerationConfig(
            thinking_config=genai.types.ThinkingConfig(thinking_level=self.thinking_level),
            temperature=kwargs.get("temperature", 0.2),
            max_output_tokens=kwargs.get("max_tokens", 4000),
            response_mime_type="application/json",
            response_schema=schema,
        )
        response = await self.model.generate_content_async(prompt, generation_config=config)
        return {
            "json": response.text,
            "usage": self._extract_usage(response),
            "model": self.model_name,
        }

    def _extract_usage(self, response) -> Optional[Dict]:
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            return {
                "input_tokens": response.usage_metadata.prompt_token_count,
                "output_tokens": response.usage_metadata.candidates_token_count,
                "total_tokens": response.usage_metadata.total_token_count,
            }
        return None

    async def health_check(self) -> Dict[str, Any]:
        try:
            await self.model.generate_content_async("ping")
            return {"healthy": True, "model": self.model_name}
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