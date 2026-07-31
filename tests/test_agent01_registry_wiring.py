"""Regression tests for the registry-wiring in agents/agent-01-trend-hunter.py.

These tests import agent-01-trend-hunter.py directly (it has a hyphenated
filename, so it can't be a normal package import) and verify:
  1. The module loads the YAML registry successfully and returns the exact
     same URL/query sets as the hardcoded fallback lists (parity was
     already checked once at migration time by
     scripts/migrate_sources.py --verify-parity; these tests re-check it
     every time the suite runs, so any future drift between sources/ and
     the fallback lists is caught in CI, not just at migration time).
  2. NEWS_DISABLE_REGISTRY=1 correctly forces the fallback path.
  3. Env var overrides (NEWS_RSS_FEEDS / NEWS_VENDOR_SOURCES) still take
     precedence over both the registry and the fallback, unchanged from
     pre-registry behavior.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_FILE = REPO_ROOT / "agents" / "agent-01-trend-hunter.py"


def _load_agent_module():
    """Import agent-01-trend-hunter.py as a fresh module instance so each
    test gets its own _registry_cache (avoids cross-test cache pollution)."""
    spec = importlib.util.spec_from_file_location(
        f"agent01_{id(object())}", AGENT_FILE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def agent01(monkeypatch):
    # Ensure no leftover env override from the shell bleeds into these tests.
    monkeypatch.delenv("NEWS_RSS_FEEDS", raising=False)
    monkeypatch.delenv("NEWS_VENDOR_SOURCES", raising=False)
    monkeypatch.delenv("NEWS_DISABLE_REGISTRY", raising=False)
    return _load_agent_module()


class TestRegistryWiring:
    def test_registry_is_available(self, agent01):
        assert agent01._REGISTRY_AVAILABLE is True

    def test_configured_rss_feeds_matches_fallback_urls(self, agent01):
        registry_urls = {u for _, u in agent01.configured_rss_feeds()}
        fallback_urls = {u for _, u in agent01._FALLBACK_RSS_FEEDS}
        assert registry_urls == fallback_urls
        assert len(registry_urls) == 24

    def test_configured_vendor_sources_matches_fallback_urls(self, agent01):
        registry_urls = {u for _, u, _ in agent01.configured_vendor_sources()}
        fallback_urls = {u for _, u, _ in agent01._FALLBACK_VENDOR_SOURCES}
        assert registry_urls == fallback_urls
        assert len(registry_urls) == 38

    def test_configured_html_sources_matches_fallback_urls(self, agent01):
        registry_urls = {u for _, u, _ in agent01.configured_html_sources()}
        fallback_urls = {u for _, u, _ in agent01._FALLBACK_HTML_SOURCES}
        assert registry_urls == fallback_urls
        assert len(registry_urls) == 3

    def test_configured_google_news_queries_matches_fallback(self, agent01):
        assert set(agent01.configured_google_news_queries()) == set(agent01._FALLBACK_GOOGLE_NEWS_QUERIES)
        assert len(agent01.configured_google_news_queries()) == 8

    def test_configured_seed_queries_matches_fallback(self, agent01):
        assert set(agent01.configured_seed_queries()) == set(agent01._FALLBACK_SEED_QUERIES)
        assert len(agent01.configured_seed_queries()) == 15

    def test_registry_cached_across_calls(self, agent01):
        # _get_registry() should only actually load once per module instance.
        r1 = agent01._get_registry()
        r2 = agent01._get_registry()
        assert r1 is r2


class TestFallbackPath:
    def test_disable_registry_env_forces_fallback(self, monkeypatch):
        monkeypatch.setenv("NEWS_DISABLE_REGISTRY", "1")
        monkeypatch.delenv("NEWS_RSS_FEEDS", raising=False)
        monkeypatch.delenv("NEWS_VENDOR_SOURCES", raising=False)
        module = _load_agent_module()
        assert module._get_registry() is None
        # Falls back to the exact same hardcoded list, not an empty result.
        assert module.configured_rss_feeds() == module._FALLBACK_RSS_FEEDS
        assert module.configured_vendor_sources() == module._FALLBACK_VENDOR_SOURCES
        assert module.configured_html_sources() == module._FALLBACK_HTML_SOURCES

    def test_registry_missing_directory_falls_back_gracefully(self, agent01, monkeypatch, tmp_path):
        # Point the registry loader at an empty directory that has no source
        # files at all -- load_source_registry() should return an empty
        # (but validly-loaded) registry, and configured_*() must fall back
        # to the hardcoded lists rather than returning an empty result.
        empty_dir = tmp_path / "empty_sources"
        empty_dir.mkdir()
        monkeypatch.setattr(
            "src.config.loader.DEFAULT_SOURCES_DIR", empty_dir, raising=False
        )
        # Reset the module-level cache so _get_registry() re-evaluates
        # against the monkeypatched (empty) directory.
        agent01._registry_cache = None
        agent01._registry_load_attempted = False

        # load_source_registry() with no explicit dir uses DEFAULT_SOURCES_DIR
        # only if agent01 calls it with no args, which it does.
        rss = agent01.configured_rss_feeds()
        assert rss == agent01._FALLBACK_RSS_FEEDS


class TestEnvOverridePrecedence:
    def test_env_rss_override_beats_registry(self, monkeypatch):
        monkeypatch.setenv("NEWS_RSS_FEEDS", "Custom Feed|https://custom.example.com/feed")
        monkeypatch.delenv("NEWS_VENDOR_SOURCES", raising=False)
        monkeypatch.delenv("NEWS_DISABLE_REGISTRY", raising=False)
        module = _load_agent_module()
        feeds = module.configured_rss_feeds()
        assert feeds == [("Custom Feed", "https://custom.example.com/feed")]

    def test_env_vendor_override_beats_registry(self, monkeypatch):
        monkeypatch.setenv("NEWS_VENDOR_SOURCES", "Custom Vendor|https://custom.example.com/news|inspection")
        monkeypatch.delenv("NEWS_RSS_FEEDS", raising=False)
        monkeypatch.delenv("NEWS_DISABLE_REGISTRY", raising=False)
        module = _load_agent_module()
        sources = module.configured_vendor_sources()
        assert sources == [("Custom Vendor", "https://custom.example.com/news", "inspection")]
