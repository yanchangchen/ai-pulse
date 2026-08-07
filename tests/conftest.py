"""
Pytest configuration and global fixtures for AI Pulse tests.
"""

import pytest
from core.llm_client import LLMClient


@pytest.fixture(autouse=True)
def reset_llm_quota_status():
    """Ensure LLM quota status flag is reset before and after every test."""
    LLMClient.reset_quota_status()
    yield
    LLMClient.reset_quota_status()
