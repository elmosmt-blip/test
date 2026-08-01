"""Unit tests for YouTube Scout's discovery-only path (no DB/network)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCOUT_FILE = REPO_ROOT / "agents" / "agent-07-youtube-scout.py"


def _load_scout():
    spec = importlib.util.spec_from_file_location("youtube_scout_test", SCOUT_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_relevance_prefers_official_smt_channel():
    scout = _load_scout()
    score, terms = scout._relevance({
        "title": "NXTR placement demonstration", "description": "SMT placement machine", "channel": "Fuji Corporation",
    })
    assert score >= 5
    assert "official-channel" in terms
    assert scout._channel_type("Fuji Corporation") == "official_vendor"


def test_video_date_accepts_timestamp():
    scout = _load_scout()
    date = scout._video_date({"timestamp": 1785504000})
    assert date is not None
    assert date.tzinfo is not None


def test_search_uses_flat_metadata_and_skips_irrelevant(monkeypatch):
    scout = _load_scout()

    class FakeYDL:
        def __init__(self, options):
            self.options = options
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def extract_info(self, query, download=False):
            assert query.startswith("ytsearchdate")
            return {"entries": [
                {"id": "good", "title": "AOI inspection for SMT assembly", "channel": "Saki Corporation", "upload_date": "20260801"},
                {"id": "bad", "title": "Cat video", "channel": "Random", "upload_date": "20260801"},
            ]}

    monkeypatch.setattr(scout.yt_dlp, "YoutubeDL", FakeYDL)
    videos = scout.search_videos("AOI inspection", max_results=5, max_days=365)
    assert [video["video_id"] for video in videos] == ["good"]
