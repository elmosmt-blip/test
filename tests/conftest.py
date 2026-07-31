"""Shared pytest fixtures for the source-registry test suite.

Per 00_MASTER_PLAN.md section 26: "Do not make the entire test suite
dependent on live websites." These tests are pure unit tests against
in-memory YAML fixtures and the real (checked-in) sources/ directory --
no network access required.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest


@pytest.fixture
def tmp_sources_dir(tmp_path: Path) -> Path:
    """An empty, throwaway sources/ directory for tests that write their own
    fixture YAML instead of touching the real checked-in registry."""
    d = tmp_path / "sources"
    d.mkdir()
    (d / "rss").mkdir()
    (d / "vendors").mkdir()
    (d / "html").mkdir()
    (d / "search").mkdir()
    return d


@pytest.fixture
def real_sources_dir() -> Path:
    """The actual checked-in sources/ directory, migrated from the
    hardcoded Python lists in agents/agent-01-trend-hunter.py."""
    return REPO_ROOT / "sources"
