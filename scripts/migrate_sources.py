#!/usr/bin/env python3
"""
scripts/migrate_sources.py — one-time (but re-runnable) migration of the
hardcoded source lists in agents/agent-01-trend-hunter.py into the YAML
source registry under sources/.

Per 00_MASTER_PLAN.md section 30, item 11:
  "Migrate existing hardcoded RSS and vendor sources into configuration
   files without losing current sources."

This script does NOT invent any URL. It parses the literal Python lists
(_FALLBACK_RSS_FEEDS, _FALLBACK_HTML_SOURCES, _FALLBACK_VENDOR_SOURCES,
_FALLBACK_GOOGLE_NEWS_QUERIES, _FALLBACK_SEED_QUERIES) directly out of
agents/agent-01-trend-hunter.py using ast.literal_eval, then writes them out
as validated SourceConfig / SearchQueryConfig YAML entries.

Usage:
  python3 scripts/migrate_sources.py                 # writes sources/**/*.yaml
  python3 scripts/migrate_sources.py --check          # dry run, prints counts + diff, writes nothing
  python3 scripts/migrate_sources.py --verify-parity  # after writing, loads the registry back and
                                                        # asserts counts match the Python source exactly
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
# Ensure UTF-8 console output on Windows (prevent UnicodeEncodeError for emojis/box chars)
for _s in ("stdout", "stderr"):
    _stream = getattr(sys, _s, None)
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            try:
                _stream.reconfigure(errors="replace")
            except Exception:
                pass

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_FILE = REPO_ROOT / "agents" / "agent-01-trend-hunter.py"
SOURCES_DIR = REPO_ROOT / "sources"

sys.path.insert(0, str(REPO_ROOT))


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug or "source"


def _extract_literal(text: str, name: str) -> list:
    m = re.search(rf"^{name} = (\[.*?^\])", text, re.S | re.M)
    if not m:
        raise SystemExit(f"❌ Could not find `{name}` in {AGENT_FILE}")
    return ast.literal_eval(m.group(1))


def extract_all() -> dict:
    text = AGENT_FILE.read_text(encoding="utf-8")
    return {
        "rss": _extract_literal(text, "_FALLBACK_RSS_FEEDS"),
        "html": _extract_literal(text, "_FALLBACK_HTML_SOURCES"),
        "vendor": _extract_literal(text, "_FALLBACK_VENDOR_SOURCES"),
        "gnews": _extract_literal(text, "_FALLBACK_GOOGLE_NEWS_QUERIES"),
        "seed": _extract_literal(text, "_FALLBACK_SEED_QUERIES"),
    }


def build_rss_entries(rss: list[tuple[str, str]]) -> list[dict]:
    seen_ids: set[str] = set()
    entries = []
    for name, url in rss:
        base_id = _slugify(name) + "-rss"
        source_id = base_id
        n = 2
        while source_id in seen_ids:
            source_id = f"{base_id}-{n}"
            n += 1
        seen_ids.add(source_id)
        is_vendor_feed = "vendor" in name.lower()
        entries.append({
            "id": source_id,
            "name": name,
            "source_type": "rss",
            "url": url,
            "trust_level": "official_vendor" if is_vendor_feed else "industry_media",
            "priority": 3 if is_vendor_feed else 4,
            "tags": ["vendor-feed"] if is_vendor_feed else ["trade-press"],
            "notes": "Migrated from agents/agent-01-trend-hunter.py _FALLBACK_RSS_FEEDS",
        })
    return entries


def build_html_entries(html: list[tuple[str, str, str]]) -> list[dict]:
    seen_ids: set[str] = set()
    entries = []
    for name, url, kind in html:
        base_id = _slugify(name) + "-html"
        source_id = base_id
        n = 2
        while source_id in seen_ids:
            source_id = f"{base_id}-{n}"
            n += 1
        seen_ids.add(source_id)
        entries.append({
            "id": source_id,
            "name": name,
            "source_type": "html",
            "url": url,
            "html_parser": kind,
            "trust_level": "industry_media",
            "priority": 5,
            "tags": ["html-listing"],
            "notes": "Migrated from agents/agent-01-trend-hunter.py _FALLBACK_HTML_SOURCES",
        })
    return entries


VENDOR_FILE_BY_CATEGORY = {
    "inspection": "aoi.yaml",
    "placement": "placement.yaml",
    "reflow": "reflow.yaml",
    "soldering": "soldering.yaml",
    "materials": "materials.yaml",
    "cleaning": "cleaning.yaml",
    "standards": "standards.yaml",
    "stencil": "stencil.yaml",
}


def build_vendor_entries(vendor: list[tuple[str, str, str]]) -> dict[str, list[dict]]:
    """Return {filename: [entries]} so vendors are split by category file,
    matching the sources/vendors/<category>.yaml layout the master plan
    describes in section 8.
    """
    by_file: dict[str, list[dict]] = {}
    seen_ids: set[str] = set()
    for name, url, category in vendor:
        base_id = _slugify(name) + "-" + _slugify(category)
        source_id = base_id
        n = 2
        while source_id in seen_ids:
            source_id = f"{base_id}-{n}"
            n += 1
        seen_ids.add(source_id)
        filename = VENDOR_FILE_BY_CATEGORY.get(category, f"{_slugify(category)}.yaml")
        by_file.setdefault(filename, []).append({
            "id": source_id,
            "name": name,
            "source_type": "vendor",
            "url": url,
            "category": category,
            "trust_level": "official_vendor",
            "priority": 3,
            "tags": [category],
            "notes": "Migrated from agents/agent-01-trend-hunter.py _FALLBACK_VENDOR_SOURCES",
        })
    return by_file


def build_search_query_entries(gnews: list[str], seed: list[str]) -> list[dict]:
    entries = []
    seen_ids: set[str] = set()
    for q in gnews:
        base_id = _slugify(q)[:60] + "-gnews"
        qid = base_id
        n = 2
        while qid in seen_ids:
            qid = f"{base_id}-{n}"
            n += 1
        seen_ids.add(qid)
        entries.append({"id": qid, "query": q, "engine": "google_news"})
    for q in seed:
        base_id = _slugify(q)[:60] + "-ddg"
        qid = base_id
        n = 2
        while qid in seen_ids:
            qid = f"{base_id}-{n}"
            n += 1
        seen_ids.add(qid)
        entries.append({"id": qid, "query": q, "engine": "duckduckgo"})
    return entries


def write_yaml(path: Path, entries: list[dict], header: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f"# {header}\n# Auto-generated by scripts/migrate_sources.py — do not hand-edit the\n"
    text += "# 'notes: Migrated from ...' entries; add new sources as new list items instead.\n\n"
    text += yaml.safe_dump(entries, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true", help="dry run: print counts, write nothing")
    p.add_argument("--verify-parity", action="store_true",
                    help="after writing, reload the registry and assert counts match the Python source")
    args = p.parse_args()

    data = extract_all()
    print(f"Extracted from {AGENT_FILE.relative_to(REPO_ROOT)}:")
    print(f"  RSS feeds:          {len(data['rss'])}")
    print(f"  HTML sources:       {len(data['html'])}")
    print(f"  Vendor sources:     {len(data['vendor'])}")
    print(f"  Google News queries:{len(data['gnews'])}")
    print(f"  DDG seed queries:   {len(data['seed'])}")

    rss_entries = build_rss_entries(data["rss"])
    html_entries = build_html_entries(data["html"])
    vendor_by_file = build_vendor_entries(data["vendor"])
    query_entries = build_search_query_entries(data["gnews"], data["seed"])

    if args.check:
        print("\n--check: dry run, nothing written.")
        return

    write_yaml(
        SOURCES_DIR / "rss" / "trade_press_and_vendor_feeds.yaml",
        rss_entries,
        "RSS/Atom feeds — trade press + vendor newsroom feeds",
    )
    write_yaml(
        SOURCES_DIR / "html" / "listing_pages.yaml",
        html_entries,
        "HTML listing pages without RSS (custom or generic_dated_list parser)",
    )
    for filename, entries in vendor_by_file.items():
        write_yaml(
            SOURCES_DIR / "vendors" / filename,
            entries,
            f"Vendor/manufacturer pages — category: {filename.replace('.yaml', '')}",
        )
    write_yaml(
        SOURCES_DIR / "search" / "seed_queries.yaml",
        query_entries,
        "Seed search queries for DuckDuckGo HTML search and Google News RSS search",
    )

    total_vendor_written = sum(len(v) for v in vendor_by_file.values())
    print("\nWrote:")
    print(f"  sources/rss/trade_press_and_vendor_feeds.yaml    ({len(rss_entries)} entries)")
    print(f"  sources/html/listing_pages.yaml                  ({len(html_entries)} entries)")
    for filename, entries in sorted(vendor_by_file.items()):
        print(f"  sources/vendors/{filename:<32} ({len(entries)} entries)")
    print(f"  sources/search/seed_queries.yaml                 ({len(query_entries)} entries)")
    print(f"\nVendor total: {total_vendor_written} (source: {len(data['vendor'])})")

    if args.verify_parity:
        from src.config.loader import load_source_registry
        registry = load_source_registry(SOURCES_DIR)
        n_rss = len(registry.enabled_sources(source_type=None))
        rss_count = len([s for s in registry.sources if s.source_type.value == "rss"])
        html_count = len([s for s in registry.sources if s.source_type.value == "html"])
        vendor_count = len([s for s in registry.sources if s.source_type.value == "vendor"])
        query_count = len(registry.search_queries)

        print("\n--verify-parity:")
        ok = True
        checks = [
            ("RSS", rss_count, len(data["rss"])),
            ("HTML", html_count, len(data["html"])),
            ("Vendor", vendor_count, len(data["vendor"])),
            ("Search queries", query_count, len(data["gnews"]) + len(data["seed"])),
        ]
        for label, got, expected in checks:
            status = "OK" if got == expected else "MISMATCH"
            if got != expected:
                ok = False
            print(f"  {label:<15} registry={got:<4} python_source={expected:<4} [{status}]")
        if not ok:
            print("\n❌ Parity check FAILED — migration lost or duplicated sources.")
            sys.exit(1)
        print("\n✅ Parity check passed — every source from the Python literals is in the registry.")


if __name__ == "__main__":
    main()
