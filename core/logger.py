"""
Centralized logging configuration for AI Pulse.
"""

import logging
import os
from pathlib import Path

# Create logs directory if it doesn't exist
LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

LOG_FILE = LOGS_DIR / "app.log"

def setup_logger(name: str) -> logging.Logger:
    """Configure and return a logger instance.

    Honours two optional environment variables:

    - ``LOG_LEVEL`` — override the default ``INFO`` (e.g. ``DEBUG``).
    - ``LLM_DEBUG`` — when truthy, the LLM client dumps the prompt and
      raw response body on every empty or failed call (see
      ``core/llm_client.py``).
    """
    logger = logging.getLogger(name)

    # Only configure if the logger doesn't have handlers yet
    if not logger.handlers:
        level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_name, logging.INFO)
        logger.setLevel(level)
        
        # Formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        # Console Handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        # File Handler
        file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
    return logger
