"""
src/discovery/feed_discovery.py — RSS autodiscovery + sitemap.xml parsing.

Rationale (per conversation with the maintainer, 2026-07-14): DuckDuckGo HTML
scraping and Google News RSS are both unofficial, undocumented endpoints
that can change or throttle without notice. The most reliable way to widen
source coverage without a paid search API is to stop relying on search
entirely for sites we already know about, and instead:

  1. Autodiscover a site's own RSS/Atom feed via the standard
     `<link rel="alternate" type="application/rss+xml">` HTML tag (and a
     probe of common feed paths as a fallback) — a site's own feed is a
     first-party structured source, not a scrape, and is far less likely to
     break silently than parsing an HTML news-listing page.
  2. Parse a site's sitemap.xml (or sitemap index) to find recently-modified
     URLs under news/press/blog-shaped paths — useful for vendor sites that
     have no RSS feed at all.

Everything in this module is pure parsing logic — it takes HTML/XML text
already fetched by the caller and returns structured candidates. It makes no
network calls itself, which keeps it independently unit-testable with fixture
strings (per 00_MASTER_PLAN.md section 26: don't make tests depend on live
websites) and reusable from any HTTP layer (agent-01's `_http_get`,
source_expander's `requests.get`, or a future shared client).

This module is a DISCOVERY AID, not an auto-registration mechanism. Per the
master plan's explicit warning ("a source registry containing 500 fake URLs
is worse than 100 verified sources"), nothing found by this module is
written into sources/ automatically — see scripts/discover_feeds.py, which
prints candidates for manual review and verification, mirroring how the two
vendor-source expansion passes in this project were done (search/fetch to
find a candidate, independently verify it's real and dated, only then add
it to the YAML registry).
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from xml.etree import ElementTree as ET


# ── RSS/Atom autodiscovery ──────────────────────────────────────────────

_FEED_MIME_TYPES = (
    "application/rss+xml",
    "application/atom+xml",
    "application/x.atom+xml",
    "application/x-atom+xml",
)

# A small, well-known set of feed paths to probe when a page doesn't
# advertise a feed via <link rel="alternate">. Every one of these is tried
# as a real HTTP GET by the caller; this module only supplies the candidate
# path list.
COMMON_FEED_PATHS = (
    "/feed/", "/feed", "/rss/", "/rss", "/rss.xml", "/feed.xml",
    "/atom.xml", "/news/feed/", "/press/feed/", "/blog/feed/",
    "/index.xml",
)


@dataclass
class DiscoveredFeed:
    url: str
    title: str = ""
    kind: str = "rss"  # "rss" | "atom"
    discovery_method: str = "link_tag"  # "link_tag" | "common_path_probe"


def discover_rss_feeds_from_html(html: str, base_url: str) -> list[DiscoveredFeed]:
    """Parse `<link rel="alternate" type="application/rss+xml" ...>` (and
    Atom equivalent) tags out of an already-fetched HTML page. This is the
    same mechanism a browser uses to show a feed icon in the address bar —
    a first-party signal from the site itself, not a heuristic guess.
    """
    if not html:
        return []
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return _discover_rss_feeds_from_html_regex(html, base_url)

    soup = BeautifulSoup(html, "html.parser")
    found: list[DiscoveredFeed] = []
    seen_urls: set[str] = set()
    for link in soup.find_all("link", rel=lambda v: v and "alternate" in (v if isinstance(v, list) else [v])):
        mime = (link.get("type") or "").lower().strip()
        href = link.get("href")
        if not href or mime not in _FEED_MIME_TYPES:
            continue
        full_url = urllib.parse.urljoin(base_url, href)
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)
        kind = "atom" if "atom" in mime else "rss"
        found.append(DiscoveredFeed(
            url=full_url,
            title=(link.get("title") or "").strip(),
            kind=kind,
            discovery_method="link_tag",
        ))
    return found


def _discover_rss_feeds_from_html_regex(html: str, base_url: str) -> list[DiscoveredFeed]:
    """Fallback parser used only if bs4 isn't installed. Regex over raw HTML
    is not as robust as a real parser, but keeps discovery functional in a
    minimal environment rather than failing outright.
    """
    found: list[DiscoveredFeed] = []
    seen_urls: set[str] = set()
    for m in re.finditer(r"<link\b[^>]*>", html, re.IGNORECASE):
        tag = m.group(0)
        type_m = re.search(r'type=["\']([^"\']+)["\']', tag, re.IGNORECASE)
        href_m = re.search(r'href=["\']([^"\']+)["\']', tag, re.IGNORECASE)
        rel_m = re.search(r'rel=["\']([^"\']+)["\']', tag, re.IGNORECASE)
        if not (type_m and href_m and rel_m):
            continue
        if "alternate" not in rel_m.group(1).lower():
            continue
        mime = type_m.group(1).lower().strip()
        if mime not in _FEED_MIME_TYPES:
            continue
        full_url = urllib.parse.urljoin(base_url, href_m.group(1))
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)
        kind = "atom" if "atom" in mime else "rss"
        found.append(DiscoveredFeed(url=full_url, kind=kind, discovery_method="link_tag"))
    return found


def candidate_common_feed_urls(base_url: str) -> list[str]:
    """Return candidate feed URLs to probe when no <link> autodiscovery
    result was found. Caller is responsible for actually fetching each one
    and checking whether it parses as valid RSS/Atom — this function only
    builds the candidate list, so it stays a pure function with no I/O.
    """
    parsed = urllib.parse.urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    return [urllib.parse.urljoin(root, path) for path in COMMON_FEED_PATHS]


def looks_like_valid_feed(xml_text: str) -> bool:
    """Cheap validity check for a probed common-path candidate: does this
    actually parse as RSS or Atom XML, as opposed to e.g. a 200-OK HTML
    "page not found" response (common on sites with soft-404s)?
    """
    if not xml_text or "<" not in xml_text:
        return False
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError:
        return False
    tag = root.tag.lower()
    return tag.endswith("rss") or tag.endswith("feed") or "rdf" in tag


# ── Sitemap parsing ─────────────────────────────────────────────────────

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

# Path fragments that suggest a URL is a news/press/blog item worth treating
# as a collectible signal, as opposed to a product page, legal page, etc.
NEWS_LIKE_PATH_HINTS = (
    "/news", "/press", "/press-release", "/blog", "/media", "/newsroom",
    "/insights", "/announcements", "/articles",
)


@dataclass
class SitemapUrl:
    loc: str
    lastmod: Optional[datetime] = None
    is_sitemap_index_entry: bool = False


def parse_sitemap_xml(xml_text: str) -> list[SitemapUrl]:
    """Parse a sitemap.xml or sitemap-index.xml document into URLs (+
    optional lastmod). Handles both the plain <urlset> form and the
    <sitemapindex> form (which points to child sitemap files rather than
    pages directly — the caller is responsible for fetching and parsing
    those child sitemaps too, since this function does no I/O).
    """
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError:
        return []

    tag = root.tag.lower()
    is_index = tag.endswith("sitemapindex")
    entry_tag = "sitemap" if is_index else "url"

    results: list[SitemapUrl] = []
    # Namespace-tolerant iteration: try the standard namespace first, then
    # fall back to a namespace-agnostic search (some sitemaps omit xmlns).
    entries = root.findall(f"sm:{entry_tag}", _SITEMAP_NS) or [
        el for el in root if el.tag.lower().endswith(entry_tag)
    ]
    for entry in entries:
        loc_el = entry.find("sm:loc", _SITEMAP_NS)
        if loc_el is None:
            loc_el = next((c for c in entry if c.tag.lower().endswith("loc")), None)
        if loc_el is None or not (loc_el.text or "").strip():
            continue
        lastmod_el = entry.find("sm:lastmod", _SITEMAP_NS)
        if lastmod_el is None:
            lastmod_el = next((c for c in entry if c.tag.lower().endswith("lastmod")), None)
        lastmod = None
        if lastmod_el is not None and (lastmod_el.text or "").strip():
            lastmod = _parse_sitemap_date(lastmod_el.text.strip())
        results.append(SitemapUrl(loc=loc_el.text.strip(), lastmod=lastmod, is_sitemap_index_entry=is_index))
    return results


def _parse_sitemap_date(text: str) -> Optional[datetime]:
    # Sitemap lastmod is typically W3C datetime: YYYY-MM-DD or full ISO 8601.
    # Every result is normalized to a naive datetime (tzinfo stripped) so
    # callers can freely sort/compare dates from different sitemaps without
    # hitting "can't compare offset-naive and offset-aware datetimes".
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=None)
        except ValueError:
            continue
    # Trailing colon-less UTC offsets (e.g. +0000 vs +00:00) trip strptime
    # above in some sitemap generators; normalize and retry once.
    normalized = re.sub(r"([+-]\d{2}):(\d{2})$", r"\1\2", text)
    if normalized != text:
        try:
            dt = datetime.strptime(normalized, "%Y-%m-%dT%H:%M:%S%z")
            return dt.replace(tzinfo=None)
        except ValueError:
            pass
    return None


def filter_news_like_urls(urls: list[SitemapUrl], extra_path_hints: Optional[list[str]] = None) -> list[SitemapUrl]:
    """Keep only sitemap URLs whose path looks like a news/press/blog item,
    filtering out product pages, legal pages, category index pages, etc.
    """
    hints = list(NEWS_LIKE_PATH_HINTS) + (extra_path_hints or [])
    kept = []
    for u in urls:
        path = urllib.parse.urlparse(u.loc).path.lower()
        if any(h in path for h in hints):
            kept.append(u)
    return kept


def most_recent_first(urls: list[SitemapUrl]) -> list[SitemapUrl]:
    """Sort by lastmod descending; URLs with no lastmod sort last (not
    dropped — a missing lastmod doesn't mean the page is old, just
    unstamped)."""
    return sorted(urls, key=lambda u: u.lastmod or datetime.min, reverse=True)
