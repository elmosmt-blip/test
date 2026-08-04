"""Regression tests for the targeted multi-source corroboration search added
to agents/agent-01-trend-hunter.py: find_corroborating_sources() and its
integration into build_briefs().

These tests monkeypatch search_google_news_rss / search_duckduckgo so no
network access is required, per 00_MASTER_PLAN.md section 26 ("do not make
the entire test suite dependent on live websites").
"""

from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_FILE = REPO_ROOT / "agents" / "agent-01-trend-hunter.py"


def _load_agent_module():
    spec = importlib.util.spec_from_file_location(f"agent01_{id(object())}", AGENT_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_ask_json_echo_first_signal(system: str, user: str, **kw) -> dict:
    """Deterministic stand-in for llm_client.ask_json() used only by the
    integration tests below. Avoids depending on LLM_MOCK, which is read
    once as a module-level constant in llm_client.py at first import — since
    pytest runs all test files in one process and Python caches imported
    modules by name, whichever test file imports llm_client first "locks in"
    its LLM_MOCK value for the whole session. Patching ask_json directly
    sidesteps that import-order fragility entirely instead of fighting it.
    """
    # Pull the first signal's title and URL back out of the prompt so the
    # "topic" returned looks like a real trend-hunter pick, including a
    # populated `sources` list (as the real LLM is instructed to produce),
    # so source_expander's fresh_primary role gets attached correctly.
    import re
    m = re.search(r"\|\s*([^|]+?)\s*\|([^|]*)\|\s*(https?://\S+)", user)
    if m:
        topic_title = m.group(1).strip()
        source_url = m.group(3).strip()
    else:
        topic_title, source_url = "Test Topic", "https://example.com/fallback"
    return {
        "topics": [{
            "topic": topic_title,
            "angle": "Test angle for engineering readers.",
            "format": "review",
            "editorial_type": "review",
            "keywords": ["AXI", "TRI"],
            "category": "Inspection",
            "urgency": "HIGH",
            "source_count": 1,
            "source_notes": "test",
            "key_facts": ["test fact"],
            "sources": [{"title": topic_title, "url": source_url, "date": "2026-07-08"}],
        }]
    }


@pytest.fixture
def agent01(monkeypatch):
    monkeypatch.setenv("NEWS_FULLTEXT_ENABLED", "0")
    module = _load_agent_module()
    module.llm_client.ask_json = _fake_ask_json_echo_first_signal
    return module


class TestDuckDuckGoSearch:
    def test_uses_get_html_endpoint_and_parses_results(self, agent01, monkeypatch):
        class FakeResponse:
            text = '''
                <div class="result"><a class="result__a" href="https://example.com/news">SMT launch</a>
                <span class="result__snippet">New AOI system</span>
                <span class="result__url">example.com/news</span></div>
            '''
            def raise_for_status(self):
                pass

        calls = []
        def fake_get(url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

        monkeypatch.setattr(agent01.requests, "get", fake_get)
        results = agent01.search_duckduckgo("SMT AOI")

        assert calls[0][0] == "https://html.duckduckgo.com/html/"
        assert calls[0][1]["params"]["q"] == "SMT AOI"
        assert results[0]["title"] == "SMT launch"
        assert results[0]["source"] == "https://example.com/news"


def test_expired_pre_event_announcement_is_detected(agent01):
    now = datetime(2026, 8, 2, tzinfo=timezone.utc)
    text = "Kurtz Ersa will exhibit at the SMTA Querétaro Expo & Tech Forum on July 16, 2026."
    assert agent01._expired_future_event(text, now) == "2026-07-16"


class TestFindCorroboratingSources:
    def test_finds_relevant_corroborating_source(self, agent01):
        def fake_gnews(q, max_results=5, lookback_days=30):
            return [{
                "title": "TRI TR7600 SV Independent Review Finds Lower Throughput",
                "snippet": "Independent test found 2900 CPH.",
                "source": "https://smt007.com/review/tr7600sv",
                "feed": "GoogleNews:SMT007",
                "published_at": "2026-07-10",
                "date_source": "google_news_rss",
            }]
        agent01.search_google_news_rss = fake_gnews
        agent01.search_duckduckgo = lambda *a, **k: []

        topic = {"topic": "TRI TR7600 SV Ships With Higher AXI Throughput", "keywords": ["AXI", "TRI"]}
        result = agent01.find_corroborating_sources(topic, already_have_urls=set(), lookback_days=30, max_new=3)

        assert len(result) == 1
        assert "tr7600sv" in result[0]["source"]

    def test_filters_out_irrelevant_results(self, agent01):
        def fake_gnews(q, max_results=5, lookback_days=30):
            return [{
                "title": "Completely Unrelated Article About Cats",
                "snippet": "nothing to do with SMT",
                "source": "https://example.com/cats",
                "feed": "GoogleNews:Random",
                "published_at": "2026-07-10",
                "date_source": "google_news_rss",
            }]
        agent01.search_google_news_rss = fake_gnews
        agent01.search_duckduckgo = lambda *a, **k: []

        topic = {"topic": "TRI TR7600 SV Ships With Higher AXI Throughput", "keywords": ["AXI", "TRI"]}
        result = agent01.find_corroborating_sources(topic, already_have_urls=set(), lookback_days=30, max_new=3)

        assert result == []

    def test_skips_urls_already_have(self, agent01):
        existing_url = "https://smt007.com/review/tr7600sv"

        def fake_gnews(q, max_results=5, lookback_days=30):
            return [{
                "title": "TRI TR7600 SV Independent Review Finds Lower Throughput",
                "snippet": "Independent test found 2900 CPH.",
                "source": existing_url,
                "feed": "GoogleNews:SMT007",
                "published_at": "2026-07-10",
                "date_source": "google_news_rss",
            }]
        agent01.search_google_news_rss = fake_gnews
        agent01.search_duckduckgo = lambda *a, **k: []

        topic = {"topic": "TRI TR7600 SV Ships With Higher AXI Throughput", "keywords": ["AXI", "TRI"]}
        result = agent01.find_corroborating_sources(
            topic, already_have_urls={existing_url}, lookback_days=30, max_new=3
        )
        assert result == []

    def test_respects_max_new_cap(self, agent01):
        def fake_gnews(q, max_results=5, lookback_days=30):
            return [
                {"title": f"TRI TR7600 SV Coverage {i}", "snippet": "TRI TR7600 SV coverage",
                 "source": f"https://outlet{i}.example.com/tri", "feed": f"GoogleNews:Outlet{i}",
                 "published_at": "2026-07-10", "date_source": "google_news_rss"}
                for i in range(5)
            ]
        agent01.search_google_news_rss = fake_gnews
        agent01.search_duckduckgo = lambda *a, **k: []

        topic = {"topic": "TRI TR7600 SV Ships With Higher AXI Throughput", "keywords": ["AXI", "TRI"]}
        result = agent01.find_corroborating_sources(topic, already_have_urls=set(), lookback_days=30, max_new=2)
        assert len(result) == 2

    def test_no_network_functions_available_returns_empty_not_crash(self, agent01):
        def raising(*a, **k):
            raise RuntimeError("network unavailable")
        agent01.search_google_news_rss = raising
        agent01.search_duckduckgo = raising

        topic = {"topic": "Some Topic", "keywords": ["x"]}
        result = agent01.find_corroborating_sources(topic, already_have_urls=set(), lookback_days=30, max_new=3)
        assert result == []

    def test_empty_topic_title_returns_empty(self, agent01):
        result = agent01.find_corroborating_sources({"topic": ""}, already_have_urls=set(), lookback_days=30)
        assert result == []


class TestBuildBriefsMultiSourceIntegration:
    def test_single_source_topic_gets_supplemented(self, agent01, monkeypatch):
        monkeypatch.setenv("NEWS_TOPIC_SUPPLEMENTARY_SEARCH", "1")
        def fake_gnews(q, max_results=5, lookback_days=30):
            return [{
                "title": "TRI TR7600 SV Independent Review Finds Lower Throughput",
                "snippet": "Independent test found 2900 CPH under mixed-board conditions.",
                "source": "https://smt007.com/review/tr7600sv",
                "feed": "GoogleNews:SMT007",
                "published_at": "2026-07-10",
                "date_source": "google_news_rss",
            }]
        agent01.search_google_news_rss = fake_gnews
        agent01.search_duckduckgo = lambda *a, **k: []

        signals = [{
            "title": "TRI launches new AXI X-ray inspection system, 20% faster throughput",
            "snippet": "New system offers 20% higher throughput.",
            "source": "https://tri.com.tw/news/1",
            "feed": "TRI Vendor",
            "published_at": "2026-07-08",
            "date_verified": True,
        }]
        data = agent01.build_briefs(signals, max_topics=1, lookback_days=30)
        topic = data["topics"][0]

        assert topic["source_count"] == 2
        roles = {s["role"] for s in topic["sources"]}
        assert "fresh_primary" in roles
        assert "related_fresh_signal" in roles
        # Each source should carry real excerpt content for synthesis.
        assert all(s.get("excerpt") for s in topic["sources"])

    def test_no_corroboration_found_degrades_gracefully(self, agent01):
        agent01.search_google_news_rss = lambda *a, **k: []
        agent01.search_duckduckgo = lambda *a, **k: []

        signals = [{
            "title": "TRI launches new AXI X-ray inspection system, 20% faster throughput",
            "snippet": "New system offers 20% higher throughput.",
            "source": "https://tri.com.tw/news/1",
            "feed": "TRI Vendor",
            "published_at": "2026-07-08",
            "date_verified": True,
        }]
        data = agent01.build_briefs(signals, max_topics=1, lookback_days=30)
        topic = data["topics"][0]

        assert topic["source_count"] == 1
        assert topic["sources"][0]["role"] == "fresh_primary"

    def test_supplementary_search_can_be_disabled(self, agent01, monkeypatch):
        monkeypatch.setenv("NEWS_TOPIC_SUPPLEMENTARY_SEARCH", "0")

        calls = {"count": 0}
        def fake_gnews(q, max_results=5, lookback_days=30):
            calls["count"] += 1
            return [{"title": "Should not be called", "snippet": "", "source": "https://x.example.com/1",
                      "feed": "x", "published_at": "2026-07-10", "date_source": "google_news_rss"}]
        agent01.search_google_news_rss = fake_gnews
        agent01.search_duckduckgo = lambda *a, **k: []

        signals = [{
            "title": "TRI launches new AXI X-ray inspection system",
            "snippet": "New system.",
            "source": "https://tri.com.tw/news/1",
            "feed": "TRI Vendor",
            "published_at": "2026-07-08",
            "date_verified": True,
        }]
        data = agent01.build_briefs(signals, max_topics=1, lookback_days=30)
        topic = data["topics"][0]

        assert topic["source_count"] == 1
        assert calls["count"] == 0
