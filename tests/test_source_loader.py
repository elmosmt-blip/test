"""Unit tests for src/config/loader.py — YAML source registry loading and
validation, including a parity check against the real migrated registry
under sources/.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.config.loader import SourceConfigError, load_source_registry, validate_all
from src.models.source import SourceType


def _write(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(entries, sort_keys=False), encoding="utf-8")


class TestLoadSourceRegistryFixtures:
    def test_loads_valid_rss_and_vendor_files(self, tmp_sources_dir: Path):
        _write(tmp_sources_dir / "rss" / "media.yaml", [
            {"id": "feed-a", "name": "Feed A", "source_type": "rss", "url": "https://a.example.com/feed"},
            {"id": "feed-b", "name": "Feed B", "source_type": "rss", "url": "https://b.example.com/feed"},
        ])
        _write(tmp_sources_dir / "vendors" / "aoi.yaml", [
            {"id": "vendor-a", "name": "Vendor A", "source_type": "vendor",
             "url": "https://vendor-a.example.com/news", "category": "inspection"},
        ])
        registry = load_source_registry(tmp_sources_dir)
        assert len(registry.sources) == 3
        assert len(registry.enabled_sources(SourceType.RSS)) == 2
        assert len(registry.enabled_sources(SourceType.VENDOR)) == 1

    def test_loads_search_queries_from_search_subdir(self, tmp_sources_dir: Path):
        _write(tmp_sources_dir / "search" / "seed.yaml", [
            {"id": "q-one", "query": "SMT AOI inspection"},
            {"id": "q-two", "query": "pick and place", "engine": "google_news"},
        ])
        registry = load_source_registry(tmp_sources_dir)
        assert len(registry.search_queries) == 2
        assert registry.search_queries[0].engine == "duckduckgo"  # default
        assert registry.search_queries[1].engine == "google_news"

    def test_single_mapping_file_is_wrapped_as_list(self, tmp_sources_dir: Path):
        # A file with one bare mapping instead of a list should still work.
        path = tmp_sources_dir / "rss" / "single.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "id: solo-rss\nname: Solo Feed\nsource_type: rss\nurl: https://solo.example.com/feed\n",
            encoding="utf-8",
        )
        registry = load_source_registry(tmp_sources_dir)
        assert len(registry.sources) == 1
        assert registry.sources[0].id == "solo-rss"

    def test_missing_sources_dir_raises(self, tmp_path: Path):
        with pytest.raises(SourceConfigError, match="does not exist"):
            load_source_registry(tmp_path / "nonexistent")

    def test_invalid_yaml_syntax_raises_with_file_path(self, tmp_sources_dir: Path):
        path = tmp_sources_dir / "rss" / "broken.yaml"
        path.write_text("id: [unterminated", encoding="utf-8")
        with pytest.raises(SourceConfigError) as exc_info:
            load_source_registry(tmp_sources_dir)
        assert "broken.yaml" in str(exc_info.value.file_path)

    def test_invalid_entry_raises_with_useful_diagnostic(self, tmp_sources_dir: Path):
        _write(tmp_sources_dir / "vendors" / "bad.yaml", [
            # vendor source missing required 'category'
            {"id": "bad-vendor", "name": "Bad Vendor", "source_type": "vendor",
             "url": "https://bad.example.com/news"},
        ])
        with pytest.raises(SourceConfigError, match="category"):
            load_source_registry(tmp_sources_dir)

    def test_duplicate_id_across_two_files_raises(self, tmp_sources_dir: Path):
        _write(tmp_sources_dir / "rss" / "a.yaml", [
            {"id": "dup-id", "name": "A", "source_type": "rss", "url": "https://a.example.com/feed"},
        ])
        _write(tmp_sources_dir / "rss" / "b.yaml", [
            {"id": "dup-id", "name": "B", "source_type": "rss", "url": "https://b.example.com/feed"},
        ])
        with pytest.raises(Exception, match="duplicate"):
            load_source_registry(tmp_sources_dir)


class TestValidateAllNonStrict:
    def test_collects_all_errors_without_stopping_at_first(self, tmp_sources_dir: Path):
        _write(tmp_sources_dir / "vendors" / "bad1.yaml", [
            {"id": "bad-vendor-1", "name": "Bad 1", "source_type": "vendor", "url": "https://bad1.example.com/news"},
        ])
        _write(tmp_sources_dir / "vendors" / "bad2.yaml", [
            {"id": "bad-vendor-2", "name": "Bad 2", "source_type": "vendor", "url": "https://bad2.example.com/news"},
        ])
        _write(tmp_sources_dir / "rss" / "good.yaml", [
            {"id": "good-rss", "name": "Good", "source_type": "rss", "url": "https://good.example.com/feed"},
        ])
        registry, errors = validate_all(tmp_sources_dir)
        assert len(errors) == 2
        assert len(registry.sources) == 1
        assert registry.sources[0].id == "good-rss"


class TestRealMigratedRegistry:
    """Parity tests against the actual checked-in sources/ directory,
    produced by scripts/migrate_sources.py from
    agents/agent-01-trend-hunter.py's hardcoded lists. If these fail, the
    registry has drifted from what migrate_sources.py last wrote, or the
    checked-in YAML was hand-edited into an invalid state.
    """

    def test_real_registry_loads_without_error(self, real_sources_dir: Path):
        registry = load_source_registry(real_sources_dir)
        assert len(registry.sources) > 0
        assert len(registry.search_queries) > 0

    def test_real_registry_has_expected_counts(self, real_sources_dir: Path):
        registry = load_source_registry(real_sources_dir)
        rss = registry.enabled_sources(SourceType.RSS)
        html = registry.enabled_sources(SourceType.HTML)
        vendor = registry.enabled_sources(SourceType.VENDOR)

        # These counts mirror agents/agent-01-trend-hunter.py's
        # _FALLBACK_RSS_FEEDS (24, after adding Assembly Magazine) /
        # _FALLBACK_HTML_SOURCES (3) / _FALLBACK_VENDOR_SOURCES (38, after
        # two source-expansion passes) at the time of migration. If the
        # Python source list grows, re-run scripts/migrate_sources.py and
        # update this test to match the new counts.
        assert len(rss) == 24, "RSS source count drifted from migrated baseline"
        assert len(html) == 3, "HTML source count drifted from migrated baseline"
        assert len(vendor) == 38, "Vendor source count drifted from migrated baseline"

    def test_real_registry_all_vendor_sources_have_category(self, real_sources_dir: Path):
        registry = load_source_registry(real_sources_dir)
        for s in registry.enabled_sources(SourceType.VENDOR):
            assert s.category, f"vendor source {s.id!r} is missing a category"

    def test_real_registry_all_html_sources_have_parser(self, real_sources_dir: Path):
        registry = load_source_registry(real_sources_dir)
        for s in registry.enabled_sources(SourceType.HTML):
            assert s.html_parser is not None, f"html source {s.id!r} is missing html_parser"

    def test_real_registry_no_duplicate_urls(self, real_sources_dir: Path):
        registry = load_source_registry(real_sources_dir)
        urls = [s.url for s in registry.sources]
        assert len(urls) == len(set(urls)), "duplicate URLs found across the migrated registry"

    def test_real_registry_search_queries_present(self, real_sources_dir: Path):
        registry = load_source_registry(real_sources_dir)
        ddg = registry.enabled_queries(engine="duckduckgo")
        gnews = registry.enabled_queries(engine="google_news")
        assert len(ddg) == 15
        assert len(gnews) == 8
