"""
src/utils/console.py — Robust UTF-8 console and stream handling for Windows/Linux.

Solves Windows UnicodeEncodeError ('charmap' codec can't encode characters)
when printing emojis or Unicode characters to redirected/piped stdout or stderr.
"""

from __future__ import annotations

import sys


def ensure_utf8_console() -> None:
    """Ensure sys.stdout and sys.stderr never raise UnicodeEncodeError on
    Windows (e.g., under C:\\...\\python.exe with cp1251 or cp866 codepages).
    Sets UTF-8 encoding with 'replace' or 'backslashreplace' error handler
    so emojis (✍️, 🔍, ✅, ❌) and box-drawing characters print safely."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                try:
                    stream.reconfigure(errors="replace")
                except Exception:
                    pass


# Automatically execute when this module is imported
ensure_utf8_console()
