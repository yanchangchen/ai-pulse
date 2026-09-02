"""
Base provider adapter interface.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class ProviderAdapter(ABC):
    """Abstract base class for LLM provider adapters."""

    @abstractmethod
    async def generate(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """
        Generate free-form text.

        Returns: {"text": str, "usage": dict, "model": str}
        """
        pass

    @abstractmethod
    async def generate_structured(
        self, prompt: str, schema: Dict, **kwargs
    ) -> Dict[str, Any]:
        """
        Generate structured JSON output.

        Returns: {"json": str, "usage": dict, "model": str}
        """
        pass

    @abstractmethod
    async def health_check(self) -> Dict[str, Any]:
        """
        Check provider health.

        Returns: {"healthy": bool, "model": str, "error": str}
        """
        pass

    @abstractmethod
    def get_capabilities(self) -> Dict[str, Any]:
        """
        Get model capabilities.

        Returns: {
            "provider": str,
            "model": str,
            "tasks": List[str],
            "context_window": int,
            "supports_structured": bool,
            ...
        }
        """
        pass