"""
Google Gemini provider adapter.
"""
import json
import logging
from typing import Dict, Any, Optional
from .base import ProviderAdapter

logger = logging.getLogger(__name__)

try:
    import google.generativeai as genai
    _GENAI_AVAILABLE = True
except ImportError:
    genai = None  # type: ignore
    _GENAI_AVAILABLE = False

# The deprecated `google.generativeai` SDK is frozen and predates the Gemini 3
# thinking API — its `types` module has no `ThinkingConfig`. Detect it once;
# when absent, generation configs are built without a thinking_config so calls
# still work (thinking then defaults per model).
_THINKING_CONFIG_CLS = (
    getattr(getattr(genai, "types", None), "ThinkingConfig", None)
    if _GENAI_AVAILABLE else None
)


class GeminiProvider(ProviderAdapter):
    """Google Gemini API provider."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.5-flash",
        thinking_level: str = "low",
    ):
        if not _GENAI_AVAILABLE:
            raise ImportError(
                "google-generativeai is required for GeminiProvider. "
                "Install it with: pip install google-generativeai"
            )
        genai.configure(api_key=api_key)
        self.model_name = model
        self.thinking_level = thinking_level
        self.model = genai.GenerativeModel(model)

    def _build_generation_config(
        self,
        temperature: float,
        max_output_tokens: int,
        schema: Optional[Dict] = None,
    ):
        """Build a GenerationConfig, attaching thinking_config only when the
        installed SDK actually supports it (see _THINKING_CONFIG_CLS)."""
        kwargs: Dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
        if schema is not None:
            kwargs["response_mime_type"] = "application/json"
            kwargs["response_schema"] = schema
        if _THINKING_CONFIG_CLS is not None:
            try:
                kwargs["thinking_config"] = _THINKING_CONFIG_CLS(
                    thinking_level=self.thinking_level
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("thinking_config unavailable (%s); omitting it", exc)
                kwargs.pop("thinking_config", None)
        return genai.types.GenerationConfig(**kwargs)

    async def generate(self, prompt: str, **kwargs) -> Dict[str, Any]:
        config = self._build_generation_config(
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
        config = self._build_generation_config(
            temperature=kwargs.get("temperature", 0.2),
            max_output_tokens=kwargs.get("max_tokens", 4000),
            schema=schema,
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