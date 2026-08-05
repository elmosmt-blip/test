#!/usr/bin/env python3
"""
agents/check-rss-feeds.py — live-проверка RSS-фидов и vendor-страниц.

Загружает все RSS/HTML из конфигурации Trend Hunter, проверяет доступность
и сообщает о статусе: OK, 404, timeout, redirect, invalid XML.

Usage:
  python3 agents/check-rss-feeds.py
  python3 agents/check-rss-feeds.py --only-failing  # только проблемные
  python3 agents/check-rss-feeds.py --timeout 10     # свой таймаут
"""

from __future__ import annotations

import argparse
import concurrent.futures
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

# Force UTF-8 on Windows
for _name in ("stdout", "stderr"):
    _s = getattr(sys, _name, None)
    if _s and hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

try:
    import requests
except ImportError:
    print("⚠ pip install requests")
    sys.exit(1)


def _env_bool(name: str, default: str = "0") -> bool:
    import os
    return os.environ.get(name, default).lower() in {"1", "true", "yes", "on"}


# ——— RSS feeds (mirror _FALLBACK_RSS_FEEDS from agent-01-trend-hunter.py) ———
_RSS_FEEDS = [
    # Industry media
    ("SMT Today", "https://smttoday.com/feed/"),
    ("EMSNow", "https://www.emsnow.com/feed/"),
    ("Circuits Assembly", "https://www.circuitsassembly.com/ca/editorial/menu-news.feed"),
    ("Electronics Sourcing", "https://electronics-sourcing.com/feed/"),
    ("I-Connect007 SMT", "https://www.iconnect007.com/feed/smt007/"),
    ("I-Connect007 PCB", "https://www.iconnect007.com/feed/pcb007/"),
    ("I-Connect007 PCBA", "https://www.iconnect007.com/feed/pcbaa007/"),
    ("Global SMT & Packaging", "https://www.globalsmt.net/wp-json/wp/v2/posts?per_page=20"),
    ("EPP Europe", "https://www.epp-europe-news.com/feed/"),
    ("New Electronics", "https://www.newelectronics.co.uk/feed/"),
    ("Electronics Weekly", "https://www.electronicsweekly.com/feed/"),
    ("SMTA Chapter News", "https://www.smta.org/feed/"),
    ("IPC Community News", "https://www.ipc.org/rss.xml"),
    ("Electropages", "https://www.electropages.com/rss"),
    ("Assembly Magazine", "https://www.assemblymag.com/rss"),
    # Vendor feeds
    ("Saki Vendor", "https://www.sakicorp.com/en/feed/"),
    ("Juki SMT Vendor", "https://www.juki.co.jp/smt/en/feed/"),
    ("Fuji Europe Vendor", "https://www.fuji-euro.de/en/feed/"),
    ("Europlacer Vendor", "https://europlacer.com/feed/"),
    ("Pillarhouse Vendor", "https://www.pillarhouse.co.uk/feed/"),
    ("KYZEN Vendor", "https://kyzen.com/news/feed/"),
    ("Mycronic Vendor", "https://www.mycronic.com/en/rss/press-releases/"),
    ("Nordson Vendor", "https://www.nordson.com/en/rss/press-releases"),
    # Press releases
    ("PR Newswire: Electronics", "https://www.prnewswire.com/rss/electronics-news/electronics-news-list.rss"),
    ("PR Newswire: Manufacturing", "https://www.prnewswire.com/rss/manufacturing-news/manufacturing-news-list.rss"),
    ("PR Newswire: Technology", "https://www.prnewswire.com/rss/technology-news/technology-news-list.rss"),
    ("PR Newswire: Semiconductors", "https://www.prnewswire.com/rss/semiconductor-news/semiconductor-news-list.rss"),
    ("GlobeNewswire: Technology", "https://www.globenewswire.com/RssFeed/subjectcode/11-Technology/feedTitle/GlobeNewswire%20-%20Technology"),
    ("BusinessWire: Manufacturing", "https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeGVtYWA=="),
]

# ——— Vendor pages to check (mirror _FALLBACK_VENDOR_SOURCES) ———
_VENDOR_PAGES = [
    ("Koh Young Press", "https://kohyoungamerica.com/category/press-releases/"),
    ("Koh Young News", "https://kohyoungamerica.com/news/"),
    ("TRI", "https://www.tri.com.tw/en/index.aspx"),
    ("Viscom", "https://www.viscom.com/en/company/news/events/"),
    ("Saki News", "https://www.sakicorp.com/en/news/"),
    ("ViTrox", "https://www.vitrox.com/news-and-events/news.php"),
    ("Omron Inspection", "https://automation.omron.com/en/us/technologies/inspection/"),
    ("ASMPT News", "https://smt.asmpt.com/en/news/"),
    ("Fuji Corp", "https://smt.fuji.co.jp/en/news/"),
    ("Yamaha SMT", "https://smt.yamaha-motor.com/"),
    ("Juki Americas", "https://jukiamericas.com/news/"),
    ("Hanwha Precision", "https://www.hanwhaprecisionmachinery.com/en/news/"),
    ("Europlacer News", "https://europlacer.com/about-us/news/"),
    ("Essemtec", "https://essemtec.com/en/news/"),
    ("Mycronic Press", "https://www.mycronic.com/en/media/news/"),
    ("Kurtz Ersa News", "https://kurtzersa.com/products/electronics-production.html"),
    ("Rehm", "https://www.rehm-group.com/en/"),
    ("Heller", "https://www.hellerindustries.com/news/"),
    ("BTU", "https://www.btu.com/news/"),
]

# ——— HTML pages ———
_HTML_PAGES = [
    ("SMTnet", "https://smtnet.com/news/index.cfm?maxrows=100"),
    ("SMT007", "https://smt007.iconnect007.com/index.php/newsletters/"),
    ("PCB Directory", "https://pcbdirectory.com/news"),
]


def _get_status(url: str, timeout: int = 15) -> tuple[int, str, int | None]:
    """Return (status_code, content_type, item_count_or_None)."""
    headers = {
        "User-Agent": "SMTInsider/1.0 (feed checker; +https://smtinsider.com)",
        "Accept": "application/rss+xml, application/xml, application/json, text/html, */*",
    }
    try:
        start = time.time()
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        elapsed = time.time() - start
        ct = resp.headers.get("Content-Type", "").lower()
        body = resp.text

        count = None
        if "xml" in ct or "rss" in ct:
            try:
                root = ET.fromstring(body)
                # RSS 2.0: channel/item
                items = root.findall(".//item") or root.findall(".//{*}item")
                count = len(items)
            except ET.ParseError:
                # JSON (WordPress REST API)
                try:
                    count = len(resp.json()) if isinstance(resp.json(), list) else None
                except Exception:
                    count = 0

        return (resp.status_code, ct, count)
    except requests.Timeout:
        return (0, "timeout", None)
    except requests.ConnectionError:
        return (0, "connection_error", None)
    except requests.TooManyRedirects:
        return (0, "too_many_redirects", None)
    except Exception as e:
        return (0, f"error: {e!s:.80}", None)


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверить RSS-фиды и vendor-страницы SMTInsider")
    parser.add_argument("--only-failing", action="store_true", help="Показать только проблемные")
    parser.add_argument("--timeout", type=int, default=10, help="Таймаут на запрос (default: 10s)")
    args = parser.parse_args()

    all_checks = (
        [("RSS", name, url) for name, url in _RSS_FEEDS]
        + [("VENDOR", name, url) for name, url in _VENDOR_PAGES]
        + [("HTML", name, url) for name, url in _HTML_PAGES]
    )

    print(f"🔍 Проверка {len(all_checks)} источников (таймаут {args.timeout}с)...\n")

    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_get_status, url, args.timeout): (kind, name, url)
            for kind, name, url in all_checks
        }
        for future in concurrent.futures.as_completed(futures):
            kind, name, url = futures[future]
            try:
                status, ct, count = future.result()
            except Exception as e:
                status, ct, count = 0, f"error: {e!s:.80}", None
            results.append({
                "kind": kind, "name": name, "url": url,
                "status": status, "content_type": ct, "item_count": count,
            })

    results.sort(key=lambda r: (0 if r["status"] == 200 else 1, r["kind"], r["name"]))

    ok_count, warn_count, fail_count = 0, 0, 0
    for r in results:
        status = r["status"]
        if status == 200:
            ok_count += 1
        elif status == 0:
            fail_count += 1
            if not args.only_failing:
                print(f"  ❌ {r['kind']:7s} {r['name']:<30s} → {r['content_type']}")
        elif status in (403, 429):
            warn_count += 1
            if not args.only_failing:
                print(f"  ⚠️  {r['kind']:7s} {r['name']:<30s} → HTTP {status}")
        else:
            fail_count += 1
            if not args.only_failing:
                print(f"  ❌ {r['kind']:7s} {r['name']:<30s} → HTTP {status}")

    # Show OK sources
    if not args.only_failing:
        print()
        for r in results:
            if r["status"] == 200:
                count_str = f" [{r['item_count']} items]" if r["item_count"] is not None else ""
                print(f"  ✅ {r['kind']:7s} {r['name']:<30s} → OK{count_str}")

    print(f"\n{'='*60}")
    print(f"  Всего: {len(results)} источников")
    print(f"  ✅ OK: {ok_count}  ⚠️  Rate-limited: {warn_count}  ❌ Fail: {fail_count}")
    print(f"{'='*60}")

    # Summary: list failing URLs for manual cleanup
    failing = [r for r in results if r["status"] != 200]
    if failing:
        print(f"\n⚠️  Проблемные источники ({len(failing)}): удалите или замените URL в _FALLBACK_RSS_FEEDS / _FALLBACK_VENDOR_SOURCES / _FALLBACK_HTML_SOURCES\n")
        for r in failing:
            print(f"    # {r['kind']} {r['name']}")
            print(f"    url = \"{r['url']}\"")
            print(f"    # → {r['content_type']}\n")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())