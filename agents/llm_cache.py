#!/usr/bin/env python3
"""llm_cache.py — simple JSON file cache for LLM responses.

Cache key: SHA-256 of (model + messages).
TTL: 24 hours.
File: cache/llm_responses.json
"""

import sys
# Ensure UTF-8 console output on Windows (prevent UnicodeEncodeError for emojis/box chars)
for _s in ("stdout", "stderr"):
    _stream = getattr(sys, _s, None)
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            try:
                _stream.reconfigure(errors="replace")
            except Exception:
                pass


import hashlib
import json
import os
import time
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "llm_responses.json"
CACHE_TTL = 24 * 3600  # 24 hours in seconds


def _ensure_cache_file() -> None:
    """Create cache directory and empty cache file if they don't exist."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not CACHE_FILE.exists():
        CACHE_FILE.write_text("{}", encoding="utf-8")


def compute_request_hash(model: str, messages: list) -> str:
    """Hash based on model + messages content only."""
    raw = json.dumps({"model": model, "messages": messages}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_cache() -> dict:
    """Load the entire cache from disk."""
    _ensure_cache_file()
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_cache(cache_data: dict) -> None:
    """Write the entire cache dict to disk."""
    _ensure_cache_file()
    CACHE_FILE.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_cached_response(model: str, messages: list) -> str | None:
    """Return cached response if present and not expired, otherwise None."""
    if os.environ.get("LLM_CACHE_ENABLED", "1").lower() not in {"1", "true", "yes", "on"}:
        return None
    cache = load_cache()
    key = compute_request_hash(model, messages)
    entry = cache.get(key)
    if not entry:
        return None
    age = time.time() - entry.get("timestamp", 0)
    if age > CACHE_TTL:
        del cache[key]
        save_cache(cache)
        return None
    return entry.get("response")


def set_cached_response(model: str, messages: list, response: str) -> None:
    """Store a response in the cache."""
    if os.environ.get("LLM_CACHE_ENABLED", "1").lower() not in {"1", "true", "yes", "on"}:
        return
    cache = load_cache()
    key = compute_request_hash(model, messages)
    cache[key] = {
        "timestamp": time.time(),
        "response": response,
        "model": model,
    }
    save_cache(cache)
