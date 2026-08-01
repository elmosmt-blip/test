#!/usr/bin/env python3
"""Replace stored Google News redirects with publisher canonical URLs.

Usage:
  python scripts/repair_google_news_sources.py --dry-run
  python scripts/repair_google_news_sources.py --apply

Requires NEON_DATABASE_URL. Google News is used for discovery only; public
articles must point their Source / Official Link to the actual publisher.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SMTInsiderBot/1.0)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def resolve(url: str) -> str:
    if not url or "news.google.com" not in urlparse(url).netloc.lower():
        return ""
    try:
        response = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        response.raise_for_status()
        final = response.url
        return final if final and "news.google.com" not in urlparse(final).netloc.lower() else ""
    except requests.RequestException:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write resolved URLs to Neon (default is dry-run)")
    args = parser.parse_args()
    db_url = os.environ.get("NEON_DATABASE_URL", "")
    if not db_url:
        print("❌ NEON_DATABASE_URL не задан")
        return 1
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError as e:
        print(f"❌ psycopg2 unavailable: {e}")
        return 1

    with psycopg2.connect(db_url) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, title, link, source_url, frontmatter_json FROM news "
                "WHERE link ILIKE '%news.google.com%' OR source_url ILIKE '%news.google.com%'"
            )
            rows = cur.fetchall()
            changed = 0
            for row in rows:
                original = row["source_url"] or row["link"] or ""
                canonical = resolve(original)
                if not canonical:
                    print(f"⚠ #{row['id']}: unable to resolve {original}")
                    continue
                print(f"{'APPLY' if args.apply else 'DRY'} #{row['id']}: {original} -> {canonical}")
                if not args.apply:
                    continue
                frontmatter = row["frontmatter_json"] or {}
                if isinstance(frontmatter, str):
                    try:
                        frontmatter = json.loads(frontmatter)
                    except json.JSONDecodeError:
                        frontmatter = {}
                if isinstance(frontmatter, dict):
                    frontmatter["source_url"] = canonical
                cur.execute(
                    "UPDATE news SET link=%s, source_url=%s, frontmatter_json=%s WHERE id=%s",
                    (canonical, canonical, json.dumps(frontmatter, ensure_ascii=False), row["id"]),
                )
                changed += 1
            if args.apply:
                conn.commit()
            print(f"✓ {'Updated' if args.apply else 'Would update'} {changed} article(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
