#!/usr/bin/env python3
"""
scripts/discover_feeds.py — find candidate RSS feeds and fresh news URLs for
vendor sources already in the registry, or for an arbitrary URL, WITHOUT a
paid search API and without relying on DuckDuckGo/Google News scraping.

Two techniques, both first-party (reading what a site publishes about
itself, not scraping a search engine):

  1. RSS/Atom autodiscovery — fetch the vendor's page, look for
     <link rel="alternate" type="application/rss+xml"> tags, and if none are
     found, probe a short list of common feed paths (/feed/, /rss.xml, ...).
     A vendor's own feed is far more reliable long-term than parsing their
     HTML news-listing page (which is what today's `vendor` source_type
     collector does): feeds have a stable, versioned schema, whereas HTML
     structure changes without notice.

  2. Sitemap.xml scanning — fetch /sitemap.xml (or a discovered sitemap
     index's child sitemaps), keep only news/press/blog-shaped URLs, and
     surface the most recently modified ones. Useful for vendors with no
     RSS feed at all.

THIS SCRIPT DOES NOT WRITE TO sources/. It only prints a report. Per
00_MASTER_PLAN.md's explicit warning ("a source registry containing 500
fake URLs is worse than 100 verified sources"), every candidate must be
manually reviewed — opened in a browser, confirmed to be a real, live,
dated feed/page — before being added to sources/vendors/*.yaml or
sources/rss/*.yaml by hand (or via scripts/migrate_sources.py after adding
it to the Python fallback list, per the existing workflow).

Usage:
  # Scan every currently-configured vendor source for a hidden RSS feed
  python3 scripts/discover_feeds.py --scan-vendors

  # Scan one specific URL
  python3 scripts/discover_feeds.py --url https://example.com/news/

  # Also scan sitemap.xml for fresh news-like URLs (slower: one more fetch
  # per source, plus child-sitemap fetches if a sitemap index is found)
  python3 scripts/discover_feeds.py --scan-vendors --sitemap
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import urllib.parse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_FILE = REPO_ROOT / "agents" / "agent-01-trend-hunter.py"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "agents"))

from src.discovery.feed_discovery import (
    candidate_common_feed_urls,
    discover_rss_feeds_from_html,
    filter_news_like_urls,
    looks_like_valid_feed,
    most_recent_first,
    parse_sitemap_xml,
)

# Reuse agent-01's retry/backoff HTTP wrapper rather than a third
# independent requests.get() implementation.
import importlib.util
_spec = importlib.util.spec_from_file_location("agent01_discover", AGENT_FILE)
_agent01 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_agent01)
_http_get = _agent01._http_get


def extract_current_vendor_sources() -> list[tuple[str, str, str]]:
    text = AGENT_FILE.read_text(encoding="utf-8")
    m = re.search(r"^_FALLBACK_VENDOR_SOURCES = (\[.*?^\])", text, re.S | re.M)
    if not m:
        raise SystemExit("❌ Could not find _FALLBACK_VENDOR_SOURCES in agent-01-trend-hunter.py")
    return ast.literal_eval(m.group(1))


def discover_for_url(base_url: str, check_sitemap: bool) -> dict:
    result = {"url": base_url, "feeds": [], "sitemap_news_urls": [], "errors": []}

    resp = _http_get(base_url, timeout=15)
    if resp is None:
        result["errors"].append("could not fetch page (see NEWS_COLLECTION.md retry policy)")
        return result

    feeds = discover_rss_feeds_from_html(resp.text, base_url)
    if not feeds:
        # No <link> autodiscovery hit — probe common paths as a fallback,
        # verifying each candidate actually parses as RSS/Atom before
        # reporting it (a 200-OK soft-404 HTML page is not a feed).
        for candidate_url in candidate_common_feed_urls(base_url):
            probe = _http_get(candidate_url, timeout=10, retries=0)
            if probe is not None and looks_like_valid_feed(probe.text):
                feeds.append(type("Probed", (), {
                    "url": candidate_url, "title": "", "kind": "rss",
                    "discovery_method": "common_path_probe",
                })())
    result["feeds"] = feeds

    if check_sitemap:
        parsed = urllib.parse.urlparse(base_url)
        sitemap_url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
        sm_resp = _http_get(sitemap_url, timeout=15, retries=0)
        if sm_resp is not None:
            entries = parse_sitemap_xml(sm_resp.text)
            # If this was a sitemap index, fetch a bounded number of child
            # sitemaps and merge their URLs in, rather than recursing
            # unboundedly (some sites have dozens of child sitemaps).
            index_entries = [e for e in entries if e.is_sitemap_index_entry]
            if index_entries:
                merged = []
                for child in index_entries[:5]:
                    child_resp = _http_get(child.loc, timeout=15, retries=0)
                    if child_resp is not None:
                        merged.extend(parse_sitemap_xml(child_resp.text))
                entries = merged
            news_urls = filter_news_like_urls(entries)
            result["sitemap_news_urls"] = most_recent_first(news_urls)[:10]
        else:
            result["errors"].append("no sitemap.xml found (or fetch failed)")

    return result


def print_report(name: str, category: str, discovery: dict) -> None:
    print(f"\n{'─' * 70}")
    print(f"  {name}  ({category})")
    print(f"  {discovery['url']}")
    if discovery["feeds"]:
        for f in discovery["feeds"]:
            method = "link-tag autodiscovery" if f.discovery_method == "link_tag" else "common-path probe"
            title_part = f" — \"{f.title}\"" if getattr(f, "title", "") else ""
            print(f"  ✓ FEED FOUND ({method}): {f.url}{title_part}")
    else:
        print(f"  · no RSS/Atom feed found")
    if discovery["sitemap_news_urls"]:
        print(f"  · {len(discovery['sitemap_news_urls'])} recent news-like sitemap URLs:")
        for u in discovery["sitemap_news_urls"][:5]:
            date_str = u.lastmod.date().isoformat() if u.lastmod else "no lastmod"
            print(f"      [{date_str}] {u.loc}")
    for err in discovery["errors"]:
        print(f"  ⚠ {err}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", help="scan a single URL")
    p.add_argument("--scan-vendors", action="store_true",
                    help="scan every source in _FALLBACK_VENDOR_SOURCES for a hidden RSS feed")
    p.add_argument("--sitemap", action="store_true",
                    help="also scan sitemap.xml for recent news-like URLs (slower)")
    args = p.parse_args()

    if not args.url and not args.scan_vendors:
        print("Specify --url <URL> or --scan-vendors. See --help.")
        sys.exit(1)

    print("═" * 70)
    print("  Feed Discovery — RSS autodiscovery + sitemap scan")
    print("  (no search engine, no paid API — first-party site signals only)")
    print("═" * 70)

    targets: list[tuple[str, str, str]] = []
    if args.url:
        targets.append(("(manual)", args.url, "-"))
    if args.scan_vendors:
        targets.extend(extract_current_vendor_sources())

    feed_found_count = 0
    for name, url, category in targets:
        discovery = discover_for_url(url, check_sitemap=args.sitemap)
        if discovery["feeds"]:
            feed_found_count += 1
        print_report(name, category, discovery)

    print(f"\n{'═' * 70}")
    print(f"  Scanned {len(targets)} source(s). Feeds found on {feed_found_count}.")
    print("  NOTHING WAS WRITTEN TO sources/. Review each result manually:")
    print("  open the feed URL in a browser, confirm it's real and dated,")
    print("  then add it by hand (or via the _FALLBACK_* + migrate_sources.py")
    print("  workflow described in docs/SOURCE_REGISTRY.md) before trusting it.")


if __name__ == "__main__":
    main()
