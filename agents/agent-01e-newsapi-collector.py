"""
agent-01e-newsapi-collector.py — NewsAPI.org collector for SMTInsider.

Free tier: 100 requests/day, 100 results per request, articles from last 30 days.
Integrates with agent-00-orchestrator and feeds into the same briefs.json pipeline.

Requires: NEWSAPI_KEY env var (get one at https://newsapi.org/register)

Usage:
  python3 agents/agent-01e-newsapi-collector.py
  python3 agents/agent-01e-newsapi-collector.py --days 7 --max 50
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

TMP = Path(tempfile.gettempdir())
BRIEFS_FILE = TMP / "smtinsider_briefs.json"

# SMT/EMS industry queries — broad enough to catch relevant news,
# specific enough to avoid generic "electronics" noise.
NEWSAPI_QUERIES = [
    # Core SMT/manufacturing
    '"SMT" OR "surface mount" equipment placement inspection',
    '"PCB assembly" OR "PCBA" manufacturing production',
    '"AOI" OR "SPI" OR "AXI" inspection electronics',
    '"pick and place" OR "placement machine" SMT',
    '"reflow soldering" OR "wave soldering" OR "selective soldering"',
    # EMS / contract manufacturing
    '"electronics manufacturing services" OR "EMS provider"',
    # Standards / quality
    '"IPC" OR "J-STD" electronics manufacturing standard',
    # Advanced / semiconductor
    '"advanced packaging" OR "heterogeneous integration" semiconductor',
    '"SiP" OR "system-in-package" OR "chiplet" assembly',
    # Smart factory / Industry 4.0
    '"smart factory" OR "Industry 4.0" electronics manufacturing',
    '"MES" OR "traceability" OR "CFX" SMT electronics',
    # Materials / consumables
    '"solder paste" OR "solder alloy" OR "flux" electronics',
    '"conformal coating" OR "underfill" OR "encapsulation" PCB',
    # Test / inspection
    '"in-circuit test" OR "functional test" OR "flying probe" PCB',
    '"X-ray inspection" OR "CT inspection" OR "3D inspection" SMT',
    # Equipment vendors launching products
    '"Koh Young" OR "Fuji" OR "ASMPT" OR "Yamaha" OR "Juki" OR "Hanwha" SMT',
    '"Mycronic" OR "Europlacer" OR "Essemtec" OR "Panasonic" placement',
    '"Nordson" OR "Kurtz Ersa" OR "Rehm" OR "Heller" OR "BTU" soldering reflow',
    '"TRI" OR "Viscom" OR "ViTrox" OR "Saki" OR "Mirtec" OR "Pemtron" inspection',
    '"AIM Solder" OR "Indium" OR "KYZEN" OR "ZESTRON" OR "MacDermid" SMT materials',
]

# Sources to exclude — generic aggregators that add noise
EXCLUDE_DOMAINS = {
    "finance.yahoo.com", "seekingalpha.com", "marketwatch.com",
    "investopedia.com", "fool.com", "tradingview.com", "stockhouse.com",
    "simplywall.st", "tipranks.com", "zacks.com",
}

SMT_KEYWORD_CHECK = re.compile(
    r"(?i)\b("
    r"smt|pcb|pcba|aoi|spi|axi|pick.?and.?place|placement|solder|reflow|"
    r"wave.?solder|selective.?solder|rework|stencil|screen.?print|"
    r"ems|electronics.?manufactur|surface.?mount|inspection|conformal.?coat|"
    r"advanced.?packag|sip|system.?in.?package|chiplet|heterogeneous|"
    r"factory|smart.?factory|industry.?4\.?0|iiot|cfx|mes|traceability|"
    r"j.?std|ipc-?\d|ipc.?apex|productronica|smtconnect|ipc.?standard|"
    r"underfill|encapsulation|flux|solder.?paste|nozzle|feeder|"
    r"in.?circuit.?test|functional.?test|flying.?probe|boundary.?scan|"
    r"x.?ray|ct.?inspection|axi|3d.?inspection|voiding|head.?in.?pillow|"
    r"tombstoning|graping|false.?call|first.?pass.?yield|oee|cpk|dpmo|"
    r"depaneling|singulation|laser.?cut|through.?hole|tht|odd.?form|"
    r"bga|qfn|qfp|0201|01005|008004|016008"
    r")\b"
)


def _http_get(url: str, headers: dict | None = None, timeout: int = 15, retries: int = 2) -> requests.Response | None:
    hdrs = {
        "User-Agent": "SMTInsider/1.0 (news bot; contact@smtinsider.com)",
        "Accept": "application/json",
    }
    hdrs.update(headers or {})
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=hdrs, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.RequestException:
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    return None


def collect_from_newsapi(api_key: str, days: int = 7, max_per_query: int = 15, max_requests: int = 80) -> list[dict[str, Any]]:
    """Fetch articles from NewsAPI.org for all SMT queries.

    Respects free-tier limits: 100 requests/day, 100 results max.
    """
    from_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    base_url = "https://newsapi.org/v2/everything"
    all_articles: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    request_count = 0
    for query in NEWSAPI_QUERIES:
        if request_count >= max_requests:
            print(f"  ⏹️  Достигнут лимит {max_requests} запросов к NewsAPI")
            break

        params = {
            "q": query,
            "from": from_date,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": min(max_per_query, 100),
            "apiKey": api_key,
        }
        resp = _http_get(base_url, params=params, timeout=15)
        request_count += 1

        if resp is None:
            continue

        data = resp.json()
        if data.get("status") != "ok":
            if data.get("code") == "rateLimited":
                print(f"  ⚠️  NewsAPI rate limit reached after {request_count} requests")
                break
            print(f"  ⚠️  NewsAPI error: {data.get('message', data)}")
            continue

        articles = data.get("articles", [])
        for art in articles:
            url = (art.get("url") or "").strip()
            if not url or url in seen_urls:
                continue

            domain = url.split("/")[2].lower().replace("www.", "") if "//" in url else ""
            if any(excl in domain for excl in EXCLUDE_DOMAINS):
                continue

            title = (art.get("title") or "").strip()
            description = (art.get("description") or "").strip()
            if not title:
                continue

            # Relevance check: must mention SMT/EMS terms
            combined = f"{title} {description}"
            if not SMT_KEYWORD_CHECK.search(combined):
                continue

            seen_urls.add(url)
            published = art.get("publishedAt", "")
            try:
                pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pub_dt = None

            all_articles.append({
                "title": title,
                "snippet": description[:500],
                "source": url,
                "source_name": (art.get("source", {}).get("name") or "NewsAPI").strip(),
                "query": query,
                "feed": f"NewsAPI:{art.get('source', {}).get('name', '')}",
                "search_date": pub_dt.date().isoformat() if pub_dt else "unknown",
                "date_source": "newsapi",
                "_date_dt": pub_dt,
            })

        print(f"  ✓ NewsAPI: {len(articles)} found for «{query[:60]}...» (total unique: {len(all_articles)})")

    return all_articles


def merge_into_briefs(signals: list[dict[str, Any]], output_path: Path | None = None) -> int:
    """Merge NewsAPI signals into existing briefs.json (or create new)."""
    output_path = output_path or BRIEFS_FILE
    existing: dict[str, Any] = {"topics": []}
    if output_path.exists():
        try:
            existing = json.loads(output_path.read_text("utf-8"))
        except Exception:
            pass

    existing_topics = existing.get("topics", [])
    # Avoid duplicating signals already in briefs by comparing URLs
    existing_urls = {s.get("source", "") for s in existing_topics}
    new_count = 0

    from section_router import normalize_category
    
    for sig in signals:
        if sig["source"] in existing_urls:
            continue
        # Create a topic entry compatible with the briefs format
        topic = {
            "topic": sig["title"],
            "angle": sig["snippet"][:200] if sig["snippet"] else "",
            "format": "news",
            "editorial_type": "news",
            "target_section": "/news/",
            "keywords": [],
            "category": normalize_category(sig.get("source_name", "")),
            "urgency": "MEDIUM",
            "source_count": 1,
            "source_notes": f"NewsAPI via {sig.get('source_name', '')}",
            "key_facts": [],
            "sources": [{
                "source_name": sig.get("source_name", "NewsAPI"),
                "source_url": sig["source"],
                "excerpt": sig["snippet"],
                "date": sig.get("search_date", ""),
            }],
        }
        existing_topics.append(topic)
        existing_urls.add(sig["source"])
        new_count += 1

    existing["topics"] = existing_topics
    existing["newsapi_collected_at"] = datetime.now(timezone.utc).isoformat()
    existing["newsapi_signal_count"] = len(signals)
    existing["newsapi_merged_count"] = new_count

    output_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return new_count


def main() -> int:
    parser = argparse.ArgumentParser(description="NewsAPI.org collector for SMTInsider")
    parser.add_argument("--days", type=int, default=7, help="Days to look back (default: 7)")
    parser.add_argument("--max-per-query", type=int, default=15, help="Max results per query (default: 15)")
    parser.add_argument("--max-requests", type=int, default=80, help="Max API requests (default: 80, free tier: 100)")
    parser.add_argument("--output", default=str(BRIEFS_FILE), help="Output briefs.json path")
    args = parser.parse_args()

    api_key = os.environ.get("NEWSAPI_KEY", "").strip()
    if not api_key:
        print("❌ NEWSAPI_KEY не задан. Получите ключ на https://newsapi.org/register")
        return 1

    print(f"\n📡 Agent #1e — NewsAPI Collector")
    print(f"   Lookback: {args.days} days, max {args.max_per_query} per query, {args.max_requests} requests\n")

    signals = collect_from_newsapi(api_key, args.days, args.max_per_query, args.max_requests)
    print(f"\n📊 Собрано сигналов: {len(signals)}")

    merged = merge_into_briefs(signals, Path(args.output))
    print(f"📥 Добавлено в briefs: {merged}")
    print(f"📄 Файл: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())