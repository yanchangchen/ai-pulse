import sys
import os
import time
import io
import json
import threading

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Read secrets from .streamlit/secrets.toml and set as env vars for the gateway
import toml
from pathlib import Path

secrets_path = Path(__file__).resolve().parent.parent / ".streamlit" / "secrets.toml"
if secrets_path.exists():
    secrets = toml.load(secrets_path)
    for section in secrets.values():
        if isinstance(section, dict):
            for key, value in section.items():
                if isinstance(value, str) and value and key not in os.environ:
                    os.environ[key] = value

# Force a fresh gateway instance by clearing module-level singleton
sys.path.insert(0, 'c:/claude/ai-pulse')

if hasattr(sys, '_aipulse_bg_refresher_state'):
    delattr(sys, '_aipulse_bg_refresher_state')

# Reset the gateway singleton so it re-initialises with env vars
from core import ai_gateway
if hasattr(ai_gateway.gateway, '_gateway'):
    ai_gateway.gateway._gateway = None

from core.bg_refresher import BackgroundRefresher
from core.llm_client import LLMClient

print("Starting AI Pulse background refresh with real API keys...")
print("=" * 80)

LLMClient.reset_quota_status()

started = BackgroundRefresher.start()
if not started:
    print("Failed to start background refresh (already running?)")
    sys.exit(1)

while True:
    status = BackgroundRefresher.get_status()
    print(f"[{status['status'].upper()}] {status['progress']}")
    if not status['is_running']:
        break
    time.sleep(2)

print("=" * 80)
print(f"Final status: {status['status']}")
if status['error']:
    print(f"Error: {status['error']}")
