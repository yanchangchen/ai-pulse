import sys
import io

# Force UTF-8 for stdout/stderr in this terminal
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import time

sys.path.insert(0, 'c:/claude/ai-pulse')

if hasattr(sys, '_aipulse_bg_refresher_state'):
    delattr(sys, '_aipulse_bg_refresher_state')

from core.bg_refresher import BackgroundRefresher
from core.llm_client import LLMClient

print("Starting AI Pulse background refresh with new classification/routing logic...")
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
