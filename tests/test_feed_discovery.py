"""Unit tests for src/discovery/feed_discovery.py — RSS autodiscovery and
sitemap.xml parsing. All tests use fixture HTML/XML strings; no network
access, per 00_MASTER_PLAN.md section 26.
"""

from __future__ import annotations

from datetime import datetime

from src.discovery.feed_discovery import (
    DiscoveredFeed,
    SitemapUrl,
    candidate_common_feed_urls,
    discover_rss_feeds_from_html,
    filter_news_like_urls,
    looks_like_valid_feed,
    most_recent_first,
    parse_sitemap_xml,
)


class TestDiscoverRssFeedsFromHtml:
    def test_finds_single_rss_link_tag(self):
        html = """
        <html><head>
        <link rel="alternate" type="application/rss+xml" title="Vendor News" href="/feed/">
        </head><body></body></html>
        """
        result = discover_rss_feeds_from_html(html, "https://example.com/news/")
        assert len(result) == 1
        assert result[0].url == "https://example.com/feed/"
        assert result[0].title == "Vendor News"
        assert result[0].kind == "rss"

    def test_finds_atom_feed(self):
        html = '<link rel="alternate" type="application/atom+xml" href="/atom.xml">'
        result = discover_rss_feeds_from_html(html, "https://example.com/")
        assert len(result) == 1
        assert result[0].kind == "atom"
        assert result[0].url == "https://example.com/atom.xml"

    def test_resolves_relative_urls_against_base(self):
        html = '<link rel="alternate" type="application/rss+xml" href="feed.xml">'
        result = discover_rss_feeds_from_html(html, "https://example.com/news/index.html")
        assert result[0].url == "https://example.com/news/feed.xml"

    def test_ignores_non_feed_link_tags(self):
        html = """
        <link rel="stylesheet" href="/style.css">
        <link rel="icon" href="/favicon.ico">
        <link rel="canonical" href="/page">
        """
        result = discover_rss_feeds_from_html(html, "https://example.com/")
        assert result == []

    def test_deduplicates_identical_feed_urls(self):
        html = """
        <link rel="alternate" type="application/rss+xml" href="/feed/">
        <link rel="alternate" type="application/rss+xml" href="/feed/">
        """
        result = discover_rss_feeds_from_html(html, "https://example.com/")
        assert len(result) == 1

    def test_empty_html_returns_empty(self):
        assert discover_rss_feeds_from_html("", "https://example.com/") == []

    def test_multiple_distinct_feeds(self):
        html = """
        <link rel="alternate" type="application/rss+xml" title="News" href="/news/feed/">
        <link rel="alternate" type="application/atom+xml" title="Blog" href="/blog/atom.xml">
        """
        result = discover_rss_feeds_from_html(html, "https://example.com/")
        assert len(result) == 2
        kinds = {r.kind for r in result}
        assert kinds == {"rss", "atom"}


class TestCandidateCommonFeedUrls:
    def test_builds_candidates_from_domain_root(self):
        candidates = candidate_common_feed_urls("https://example.com/some/deep/page.html")
        assert "https://example.com/feed/" in candidates
        assert "https://example.com/rss.xml" in candidates
        # All candidates should be rooted at the domain, not the deep page path.
        assert all(c.startswith("https://example.com/") for c in candidates)

    def test_preserves_scheme(self):
        candidates = candidate_common_feed_urls("http://example.com/")
        assert all(c.startswith("http://") for c in candidates)


class TestLooksLikeValidFeed:
    def test_valid_rss_is_accepted(self):
        xml = '<?xml version="1.0"?><rss version="2.0"><channel><title>X</title></channel></rss>'
        assert looks_like_valid_feed(xml) is True

    def test_valid_atom_is_accepted(self):
        xml = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>X</title></feed>'
        assert looks_like_valid_feed(xml) is True

    def test_html_soft_404_is_rejected(self):
        html = "<html><head><title>Page Not Found</title></head><body>404</body></html>"
        assert looks_like_valid_feed(html) is False

    def test_empty_string_is_rejected(self):
        assert looks_like_valid_feed("") is False

    def test_malformed_xml_is_rejected(self):
        assert looks_like_valid_feed("<rss><unclosed>") is False


class TestParseSitemapXml:
    def test_parses_plain_urlset(self):
        xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://example.com/news/item-1</loc><lastmod>2026-07-01</lastmod></url>
          <url><loc>https://example.com/news/item-2</loc><lastmod>2026-07-10T12:00:00Z</lastmod></url>
        </urlset>"""
        result = parse_sitemap_xml(xml)
        assert len(result) == 2
        assert result[0].loc == "https://example.com/news/item-1"
        assert result[0].lastmod == datetime(2026, 7, 1)
        assert result[1].lastmod == datetime(2026, 7, 10, 12, 0, 0)
        assert all(not u.is_sitemap_index_entry for u in result)

    def test_parses_sitemap_index(self):
        xml = """<?xml version="1.0"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <sitemap><loc>https://example.com/sitemap-news.xml</loc><lastmod>2026-07-01</lastmod></sitemap>
          <sitemap><loc>https://example.com/sitemap-products.xml</loc></sitemap>
        </sitemapindex>"""
        result = parse_sitemap_xml(xml)
        assert len(result) == 2
        assert all(u.is_sitemap_index_entry for u in result)
        assert result[1].lastmod is None

    def test_handles_missing_namespace(self):
        xml = """<?xml version="1.0"?>
        <urlset>
          <url><loc>https://example.com/news/item-1</loc></url>
        </urlset>"""
        result = parse_sitemap_xml(xml)
        assert len(result) == 1
        assert result[0].loc == "https://example.com/news/item-1"

    def test_malformed_xml_returns_empty(self):
        assert parse_sitemap_xml("<not valid xml") == []

    def test_empty_string_returns_empty(self):
        assert parse_sitemap_xml("") == []

    def test_entry_without_loc_is_skipped(self):
        xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><lastmod>2026-07-01</lastmod></url>
          <url><loc>https://example.com/valid</loc></url>
        </urlset>"""
        result = parse_sitemap_xml(xml)
        assert len(result) == 1
        assert result[0].loc == "https://example.com/valid"


class TestFilterNewsLikeUrls:
    def test_keeps_news_press_blog_paths(self):
        urls = [
            SitemapUrl(loc="https://example.com/news/item-1"),
            SitemapUrl(loc="https://example.com/press-release/item-2"),
            SitemapUrl(loc="https://example.com/blog/item-3"),
            SitemapUrl(loc="https://example.com/products/widget"),
            SitemapUrl(loc="https://example.com/terms-of-service"),
        ]
        result = filter_news_like_urls(urls)
        locs = {u.loc for u in result}
        assert "https://example.com/news/item-1" in locs
        assert "https://example.com/press-release/item-2" in locs
        assert "https://example.com/blog/item-3" in locs
        assert "https://example.com/products/widget" not in locs
        assert "https://example.com/terms-of-service" not in locs

    def test_extra_path_hints_are_additive(self):
        urls = [SitemapUrl(loc="https://example.com/newsroom-updates/item-1")]
        result = filter_news_like_urls(urls, extra_path_hints=["newsroom-updates"])
        assert len(result) == 1

    def test_no_matches_returns_empty(self):
        urls = [SitemapUrl(loc="https://example.com/products/widget")]
        assert filter_news_like_urls(urls) == []


class TestMostRecentFirst:
    def test_sorts_descending_by_lastmod(self):
        urls = [
            SitemapUrl(loc="a", lastmod=datetime(2026, 1, 1)),
            SitemapUrl(loc="b", lastmod=datetime(2026, 7, 1)),
            SitemapUrl(loc="c", lastmod=datetime(2026, 4, 1)),
        ]
        result = most_recent_first(urls)
        assert [u.loc for u in result] == ["b", "c", "a"]

    def test_missing_lastmod_sorts_last(self):
        urls = [
            SitemapUrl(loc="dated", lastmod=datetime(2026, 1, 1)),
            SitemapUrl(loc="undated", lastmod=None),
        ]
        result = most_recent_first(urls)
        assert result[0].loc == "dated"
        assert result[1].loc == "undated"

    def test_no_crash_on_mixed_naive_dates(self):
        # Regression test: dates parsed from different sitemap timestamp
        # formats (date-only vs full ISO 8601 with offset) must all be
        # normalized to naive datetimes by parse_sitemap_xml, or this sort
        # raises "can't compare offset-naive and offset-aware datetimes".
        xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://example.com/a</loc><lastmod>2026-07-01</lastmod></url>
          <url><loc>https://example.com/b</loc><lastmod>2026-07-02T10:00:00+02:00</lastmod></url>
          <url><loc>https://example.com/c</loc><lastmod>2026-07-03T10:00:00Z</lastmod></url>
        </urlset>"""
        parsed = parse_sitemap_xml(xml)
        result = most_recent_first(parsed)  # must not raise
        assert len(result) == 3
