"""Unit tests for src/models/source.py — SourceConfig, SearchQueryConfig,
SourceRegistry validation rules.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models.source import (
    HtmlParserKind,
    SearchQueryConfig,
    SourceConfig,
    SourceRegistry,
    SourceType,
    TrustLevel,
)


class TestSourceConfigValidation:
    def test_minimal_rss_source_is_valid(self):
        s = SourceConfig(
            id="smt-today-rss",
            name="SMT Today",
            source_type=SourceType.RSS,
            url="https://smttoday.com/feed/",
        )
        assert s.enabled is True
        assert s.priority == 5
        assert s.trust_level == TrustLevel.UNKNOWN

    def test_id_must_be_lowercase_slug(self):
        with pytest.raises(ValidationError, match="lowercase slug"):
            SourceConfig(
                id="SMT Today RSS",  # spaces + uppercase: invalid
                name="SMT Today",
                source_type=SourceType.RSS,
                url="https://smttoday.com/feed/",
            )

    def test_id_too_short_is_rejected(self):
        with pytest.raises(ValidationError):
            SourceConfig(id="a", name="X", source_type=SourceType.RSS, url="https://example.com/feed")

    def test_invalid_url_is_rejected(self):
        with pytest.raises(ValidationError):
            SourceConfig(
                id="bad-url-source",
                name="Bad URL",
                source_type=SourceType.RSS,
                url="not-a-url-at-all",
            )

    def test_html_source_requires_html_parser(self):
        with pytest.raises(ValidationError, match="html_parser"):
            SourceConfig(
                id="smtnet-html",
                name="SMTnet",
                source_type=SourceType.HTML,
                url="https://smtnet.com/news/",
                # html_parser intentionally omitted
            )

    def test_html_source_with_parser_is_valid(self):
        s = SourceConfig(
            id="smtnet-html",
            name="SMTnet",
            source_type=SourceType.HTML,
            url="https://smtnet.com/news/",
            html_parser=HtmlParserKind.SMTNET_NEWS,
        )
        assert s.html_parser == HtmlParserKind.SMTNET_NEWS

    def test_vendor_source_requires_category(self):
        with pytest.raises(ValidationError, match="category"):
            SourceConfig(
                id="koh-young-vendor",
                name="Koh Young",
                source_type=SourceType.VENDOR,
                url="https://kohyoungamerica.com/news/",
                # category intentionally omitted
            )

    def test_vendor_source_with_category_is_valid(self):
        s = SourceConfig(
            id="koh-young-vendor",
            name="Koh Young",
            source_type=SourceType.VENDOR,
            url="https://kohyoungamerica.com/news/",
            category="inspection",
        )
        assert s.category == "inspection"

    def test_priority_out_of_range_is_rejected(self):
        with pytest.raises(ValidationError):
            SourceConfig(
                id="x-rss", name="X", source_type=SourceType.RSS,
                url="https://example.com/feed", priority=99,
            )

    def test_unknown_extra_field_is_rejected(self):
        with pytest.raises(ValidationError):
            SourceConfig(
                id="x-rss", name="X", source_type=SourceType.RSS,
                url="https://example.com/feed", made_up_field="oops",
            )

    def test_legacy_adapters_round_trip(self):
        rss = SourceConfig(id="x-rss", name="X Feed", source_type=SourceType.RSS, url="https://example.com/feed")
        assert rss.as_legacy_rss_tuple() == ("X Feed", "https://example.com/feed")

        vendor = SourceConfig(
            id="x-vendor", name="X Corp", source_type=SourceType.VENDOR,
            url="https://example.com/news", category="placement",
        )
        assert vendor.as_legacy_vendor_tuple() == ("X Corp", "https://example.com/news", "placement")

        html = SourceConfig(
            id="x-html", name="X Listing", source_type=SourceType.HTML,
            url="https://example.com/list", html_parser=HtmlParserKind.GENERIC_DATED_LIST,
        )
        assert html.as_legacy_html_tuple() == ("X Listing", "https://example.com/list", "generic_dated_list")


class TestSearchQueryConfig:
    def test_minimal_query_is_valid(self):
        q = SearchQueryConfig(id="aoi-spi-query", query="AOI SPI inspection SMT")
        assert q.engine == "duckduckgo"
        assert q.enabled is True

    def test_empty_query_is_rejected(self):
        with pytest.raises(ValidationError, match="empty"):
            SearchQueryConfig(id="empty-query", query="   ")

    def test_invalid_id_is_rejected(self):
        with pytest.raises(ValidationError):
            SearchQueryConfig(id="Not A Slug!", query="something")


class TestSourceRegistry:
    def test_empty_registry_is_valid(self):
        r = SourceRegistry()
        assert r.sources == []
        assert r.search_queries == []

    def test_duplicate_source_ids_rejected(self):
        s1 = SourceConfig(id="dup-rss", name="A", source_type=SourceType.RSS, url="https://a.example.com/feed")
        s2 = SourceConfig(id="dup-rss", name="B", source_type=SourceType.RSS, url="https://b.example.com/feed")
        with pytest.raises(ValidationError, match="duplicate source id"):
            SourceRegistry(sources=[s1, s2])

    def test_duplicate_query_ids_rejected(self):
        q1 = SearchQueryConfig(id="dup-q", query="a")
        q2 = SearchQueryConfig(id="dup-q", query="b")
        with pytest.raises(ValidationError, match="duplicate search_query id"):
            SourceRegistry(search_queries=[q1, q2])

    def test_enabled_sources_filters_disabled(self):
        s1 = SourceConfig(id="on-rss", name="On", source_type=SourceType.RSS, url="https://a.example.com/feed")
        s2 = SourceConfig(id="off-rss", name="Off", source_type=SourceType.RSS, url="https://b.example.com/feed", enabled=False)
        r = SourceRegistry(sources=[s1, s2])
        assert [s.id for s in r.enabled_sources()] == ["on-rss"]

    def test_enabled_sources_filters_by_type(self):
        rss = SourceConfig(id="a-rss", name="A", source_type=SourceType.RSS, url="https://a.example.com/feed")
        vendor = SourceConfig(id="b-vendor", name="B", source_type=SourceType.VENDOR, url="https://b.example.com/news", category="reflow")
        r = SourceRegistry(sources=[rss, vendor])
        assert [s.id for s in r.enabled_sources(SourceType.VENDOR)] == ["b-vendor"]

    def test_by_id_lookup(self):
        s = SourceConfig(id="findme-rss", name="Find Me", source_type=SourceType.RSS, url="https://example.com/feed")
        r = SourceRegistry(sources=[s])
        assert r.by_id("findme-rss") is s
        assert r.by_id("does-not-exist") is None
