#!/usr/bin/env python3
"""
Agent #1 — Trend Hunter / News Scout

Production goal:
  - Collect only fresh SMT/news signals from the last N days (default 30).
  - Prefer verifiable publication dates from search snippets or page metadata.
  - Never invent news when real fresh signals are missing, unless explicitly asked
    via --allow-llm-fallback or --no-search.

Usage:
  python3 agents/agent-01-trend-hunter.py scan
  python3 agents/agent-01-trend-hunter.py scan --days 30 --strict-fresh --verify-pages
  python3 agents/agent-01-trend-hunter.py scan --collect-only --output /tmp/news_signals.json
  python3 agents/agent-01-trend-hunter.py scan --no-search   # only for local/mock tests
"""

from __future__ import annotations

import argparse
import json
import os
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

import time
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import requests

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

sys.path.insert(0, os.path.dirname(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import llm_client
import section_router
import source_expander
import dedupe

try:
    from src.collectors import pdf_collector
    _PDF_COLLECTOR_AVAILABLE = True
except Exception as _pdf_err:  # pragma: no cover
    _PDF_COLLECTOR_AVAILABLE = False
    print(f"  ⚠ PDF collector unavailable: {_pdf_err}")

try:
    from src.config.loader import SourceConfigError, load_source_registry
    from src.models.source import SourceType
    _REGISTRY_AVAILABLE = True
except Exception as _registry_import_error:  # pragma: no cover
    # The registry is an additive layer (see docs/SOURCE_REGISTRY.md). If
    # pydantic/pyyaml aren't installed, or src/ isn't deployed alongside
    # agents/ in some environment, collection must still work using the
    # hardcoded fallback lists below rather than crashing at import time.
    _REGISTRY_AVAILABLE = False
    _registry_import_error_msg = str(_registry_import_error)

NEWS_TIMEZONE = os.environ.get("NEWS_TIMEZONE", "Asia/Jerusalem")
DEFAULT_LOOKBACK_DAYS = int(os.environ.get("NEWS_LOOKBACK_DAYS", "30"))

# ─────────────────────────────────────────────────────────────────────────
# Hardcoded fallback source lists.
#
# As of this pass, these are NO LONGER the primary source of truth — the
# YAML registry under sources/ (loaded via src/config/loader.py) is. These
# lists exist purely as a safety net: if the registry directory is missing,
# a YAML file is corrupted, or pydantic/pyyaml aren't installed, collection
# still works using exactly what shipped before the registry existed.
# See docs/SOURCE_REGISTRY.md for the migration record and parity guarantee
# (scripts/migrate_sources.py --verify-parity confirms these fallback lists
# and the registry currently contain the identical set of sources).
# ─────────────────────────────────────────────────────────────────────────
DEFAULT_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_FALLBACK_RSS_FEEDS = [
    # Industry media — core SMT/EMS trade press
    ("SMT Today", "https://smttoday.com/feed/"),
    ("EMSNow", "https://www.emsnow.com/feed/"),
    ("Circuits Assembly", "https://www.circuitsassembly.com/ca/editorial/menu-news.feed"),
    ("Electronics Sourcing", "https://electronics-sourcing.com/feed/"),
    ("I-Connect007 SMT", "https://www.iconnect007.com/feed/smt007/"),
    ("I-Connect007 PCB", "https://www.iconnect007.com/feed/pcb007/"),
    ("I-Connect007 PCBA", "https://www.iconnect007.com/feed/pcbaa007/"),
    ("Global SMT & Packaging", "https://www.globalsmt.net/wp-json/wp/v2/posts?per_page=20"),
    ("Production Engineering (PES)", "https://www.pes.eu.com/news/feed/"),
    ("EPP Europe", "https://www.epp-europe-news.com/feed/"),
    ("New Electronics", "https://www.newelectronics.co.uk/feed/"),
    ("Electronics Weekly", "https://www.electronicsweekly.com/feed/"),
    ("SMTA Chapter News", "https://www.smta.org/feed/"),
    ("IPC Community News", "https://www.ipc.org/rss.xml"),
    ("Electropages", "https://www.electropages.com/rss"),
    ("Assembly Magazine", "https://www.assemblymag.com/rss"),
    # Vendor/manufacturer feeds
    ("Saki Vendor", "https://www.sakicorp.com/en/feed/"),
    ("Juki SMT Vendor", "https://www.juki.co.jp/smt/en/feed/"),
    ("Fuji Europe Vendor", "https://www.fuji-euro.de/en/feed/"),
    ("Europlacer Vendor", "https://europlacer.com/feed/"),
    ("Pillarhouse Vendor", "https://www.pillarhouse.co.uk/feed/"),
    ("KYZEN Vendor", "https://kyzen.com/news/feed/"),
    ("Mycronic Vendor", "https://www.mycronic.com/en/rss/press-releases/"),
    ("Nordson Vendor", "https://www.nordson.com/en/rss/press-releases"),
]

# Google News RSS acts as a resilient, date-stamped search fallback. It is far
# less likely to throttle bot traffic than scraping DuckDuckGo HTML results,
# and every item already carries a real publication date.
_FALLBACK_GOOGLE_NEWS_QUERIES = [
    "SMT AOI SPI AXI inspection",
    "surface mount technology equipment launch",
    "pick and place SMT placement machine",
    "PCB assembly quality control defect",
    "reflow soldering process SMT",
    "IPC standard electronics manufacturing",
    "SMT smart factory Industry 4.0",
    "electronics manufacturing services EMS news",
]

# Non-RSS fresh pages with stable date markup. Used as an additional collector.
_FALLBACK_HTML_SOURCES = [
    ("SMTnet", "https://smtnet.com/news/index.cfm?maxrows=100", "smtnet_news"),
    ("SMT007 Latest News", "https://smt007.iconnect007.com/index.php/newsletters/", "generic_dated_list"),
    ("PCB Directory News", "https://pcbdirectory.com/news", "generic_dated_list"),
]

# Vendor/manufacturer pages. These are important because equipment launches often
# appear on vendor sites before industry media republishes them.
_FALLBACK_VENDOR_SOURCES = [
    # AOI / SPI / AXI / inspection
    ("Koh Young", "https://kohyoungamerica.com/category/press-releases/", "inspection"),
    ("Koh Young", "https://kohyoungamerica.com/news/", "inspection"),
    ("TRI", "https://www.tri.com.tw/en/index.aspx", "inspection"),
    ("Viscom", "https://www.viscom.com/en/company/news/events/", "inspection"),
    ("Saki", "https://www.sakicorp.com/en/news/", "inspection"),
    ("ViTrox", "https://www.vitrox.com/news-and-events/news.php", "inspection"),
    ("Creative Electron", "https://creativeelectron.com/newsroom/", "inspection"),
    ("Mirtec", "https://www.mirtec.com/news.php", "inspection"),
    ("CyberOptics", "https://www.cyberoptics.com/news/", "inspection"),
    # Placement / SMT equipment
    ("Yamaha SMT", "https://global.yamaha-motor.com/business/smt/news/", "placement"),
    ("Juki SMT", "https://www.juki.co.jp/smt/en/news/", "placement"),
    ("ASMPT", "https://www.asmpt.com/en/news-center/press-releases/", "placement"),
    ("Fuji Europe", "https://www.fuji-euro.de/en/", "placement"),
    ("Essemtec", "https://essemtec.com/en/news/", "placement"),
    ("Europlacer", "https://europlacer.com/news-hub/", "placement"),
    ("Mycronic", "https://www.mycronic.com/news-events/news/", "placement"),
    ("Panasonic Factory Solutions", "https://na.panasonic.com/us/factory-solutions/news", "placement"),
    # Reflow / soldering / cleaning / materials
    ("Heller", "https://hellerindustries.com/news/", "reflow"),
    ("Rehm", "https://www.rehm-group.com/en/news/dates.html", "reflow"),
    ("Pillarhouse", "https://www.pillarhouse.co.uk/news/", "soldering"),
    ("AIM Solder", "https://www.aimsolder.com/news/", "materials"),
    ("KYZEN", "https://kyzen.com/news/", "cleaning"),
    ("Nordson SELECT", "https://www.nordsonselect.com/en/news", "soldering"),
    ("Indium Corporation", "https://www.indium.com/blog/", "materials"),
    # Standards / test / stencils
    ("IPC", "https://www.ipc.org/news", "standards"),
    ("Photo Stencil", "https://www.photostencil.com/news/", "stencil"),
    # THT insertion (verified live news pages, added after Apodex-sourced
    # gap analysis — see docs/SOURCE_REGISTRY.md "THT scope" note)
    ("Sciencgo", "https://www.xzg-sciencgo.com/news.html", "tht_insertion"),
    ("Robotas", "https://www.robotas.com/news/", "tht_insertion"),
    # Depaneling
    ("ASYS Group", "https://www.asys-group.com/en/news", "depaneling"),
    # In-circuit / functional test
    ("Forwessun", "https://forwessun.net/news/", "test"),
    # Inspection (digital microscopy / manual inspection)
    ("TAGARNO", "https://tagarno.com/news/", "inspection"),
    # Second batch (2026-07-11) — additional verified vendors across
    # inspection, reflow, soldering, materials, cleaning, test, stencil
    ("MEK (Marantz Electronics)", "https://marantz-electronics.com/press-releases/", "inspection"),
    ("BTU International", "https://www.btu.com/press-news/", "reflow"),
    ("Kurtz Ersa", "https://kurtzersa.com/news/all", "soldering"),
    ("MacDermid Alpha", "https://www.macdermidalpha.com/news", "materials"),
    ("ZESTRON", "https://www.zestron.com/en/news/press-releases.html", "cleaning"),
    ("Seica", "https://www.seica-na.com/news/", "test"),
    ("Christian Koenen", "https://www.christian-koenen.de/en/news", "stencil"),
]

SMT_KEYWORDS = [
    "smt", "pcb", "pcba", "assembly", "electronics manufacturing", "aoi", "spi",
    "axi", "inspection", "solder", "reflow", "pick and place", "placement",
    "stencil", "ems", "surface mount", "semiconductor", "advanced packaging",
    "test fixture", "manufacturing", "factory", "automation",
    # THT / depaneling / test terms — added alongside the THT vendor sources
    # above so their signals aren't filtered out by the SMT-only relevance
    # gate. THT remains a lightweight addition (new vendor sources + these
    # keywords), not a full new editorial vertical — see
    # docs/SOURCE_REGISTRY.md for the scoping decision.
    "through-hole", "tht", "insertion machine", "depaneling", "in-circuit test",
    "ict", "functional test", "wave solder", "odd form",
]

_FALLBACK_SEED_QUERIES = [
    "SMT electronics manufacturing news AI AOI SPI 2026",
    "IPC APEX EXPO 2026 SMT inspection AOI SPI AI",
    "SMT equipment launch 2026 pick and place AOI SPI reflow",
    "electronics manufacturing SMT smart factory inspection news",
    "surface mount technology production quality inspection 2026",
    "AOI SPI AXI inspection SMT false calls AI 2026",
    "SMT assembly line automation equipment latest news",
    "PCB assembly defect analysis process control news",
    "reflow oven soldering profile SMT news 2026",
    "stencil printing solder paste inspection news",
    "electronics manufacturing services EMS expansion news",
    "IPC standard update electronics assembly 2026",
    "SMT traceability MES Industry 4.0 news",
    "conformal coating cleaning SMT process news",
    "advanced packaging SiP module assembly news",
]

# ─────────────────────────────────────────────────────────────────────────
# Registry-backed source loading.
#
# All configured_*() functions below follow the same precedence, in order:
#   1. explicit env var override (NEWS_RSS_FEEDS / NEWS_VENDOR_SOURCES) —
#      unchanged from before the registry existed, still takes priority so
#      operators can override without touching YAML;
#   2. the YAML source registry under sources/ (src/config/loader.py);
#   3. the hardcoded _FALLBACK_* list above, if the registry can't be
#      loaded for any reason (missing dependency, missing directory,
#      invalid YAML).
# Every fallback path logs why it fell back, so a broken registry is
# visible in the scan output rather than silently masked.
# ─────────────────────────────────────────────────────────────────────────
_registry_cache: Optional[Any] = None
_registry_load_attempted = False


def _get_registry():
    """Load the YAML source registry once per process and cache it.
    Returns None (and prints a diagnostic) if the registry can't be loaded,
    so callers fall back to the hardcoded lists.
    """
    global _registry_cache, _registry_load_attempted
    if _registry_load_attempted:
        return _registry_cache
    _registry_load_attempted = True

    if not _REGISTRY_AVAILABLE:
        print(f"  ⚠ Source registry unavailable ({_registry_import_error_msg}); using built-in fallback source lists.")
        return None
    if os.environ.get("NEWS_DISABLE_REGISTRY", "").lower() in {"1", "true", "yes"}:
        print("  ℹ NEWS_DISABLE_REGISTRY=1 — using built-in fallback source lists instead of sources/.")
        return None
    try:
        registry = load_source_registry()
        print(f"  ✓ Source registry loaded: {len(registry.sources)} sources, {len(registry.search_queries)} search queries (sources/)")
        _registry_cache = registry
        return registry
    except SourceConfigError as e:
        print(f"  ⚠ Source registry failed to load ({e}); using built-in fallback source lists.")
        return None
    except Exception as e:  # pragma: no cover
        print(f"  ⚠ Unexpected error loading source registry ({e}); using built-in fallback source lists.")
        return None


def configured_html_sources() -> list[tuple[str, str, str]]:
    registry = _get_registry()
    if registry is not None:
        entries = [s.as_legacy_html_tuple() for s in registry.enabled_sources(SourceType.HTML)]
        if entries:
            return entries
    return _FALLBACK_HTML_SOURCES


def configured_google_news_queries() -> list[str]:
    registry = _get_registry()
    if registry is not None:
        queries = [q.query for q in registry.enabled_queries(engine="google_news")]
        if queries:
            return queries
    return _FALLBACK_GOOGLE_NEWS_QUERIES


def configured_seed_queries() -> list[str]:
    registry = _get_registry()
    if registry is not None:
        queries = [q.query for q in registry.enabled_queries(engine="duckduckgo")]
        if queries:
            return queries
    return _FALLBACK_SEED_QUERIES

_TH_PROMPT_FILE = os.path.join(os.path.dirname(__file__), "prompts", "trend_hunter.txt")
if os.path.exists(_TH_PROMPT_FILE):
    with open(_TH_PROMPT_FILE, encoding="utf-8") as _f:
        SYSTEM_PROMPT = _f.read()
else:
    SYSTEM_PROMPT = """Ты — редакционный аналитик SMTInsider. Выбери столько source-backed тем из сигналов, сколько разрешено в запросе; не ограничивайся тремя.
Ответь СТРОГО в формате JSON: {"topics": [{"topic":"...","angle":"...","format":"news","editorial_type":"news","target_section":"/news/","keywords":[],"category":"SMT Equipment","urgency":"MEDIUM","source_count":1,"source_notes":"","key_facts":[],"sources":[]}]}
"""

MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def _env_bool(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).lower() in {"1", "true", "yes", "on"}


def now_local() -> datetime:
    if ZoneInfo:
        try:
            return datetime.now(ZoneInfo(NEWS_TIMEZONE))
        except Exception:
            pass
    return datetime.now(timezone.utc)


def to_aware(dt: datetime, now: Optional[datetime] = None) -> datetime:
    if dt.tzinfo is None:
        tz = (now or now_local()).tzinfo or timezone.utc
        return dt.replace(tzinfo=tz)
    return dt


def parse_any_date(text: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """Best-effort date parser for search snippets and page metadata."""
    if not text:
        return None
    now = now or now_local()
    s = " ".join(str(text).split())

    lower = s.lower()
    if re.search(r"\btoday\b", lower):
        return now
    if re.search(r"\byesterday\b", lower):
        return now - timedelta(days=1)

    m = re.search(r"\b(\d{1,2})\s+(minute|minutes|hour|hours|day|days|week|weeks|month|months)\s+ago\b", lower)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("minute"):
            return now - timedelta(minutes=n)
        if unit.startswith("hour"):
            return now - timedelta(hours=n)
        if unit.startswith("day"):
            return now - timedelta(days=n)
        if unit.startswith("week"):
            return now - timedelta(days=7 * n)
        if unit.startswith("month"):
            return now - timedelta(days=30 * n)

    # ISO / numeric date: 2026-06-22, 2026/06/22, optionally with time.
    m = re.search(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})(?:[T\s][0-9:.+-Z]+)?\b", s)
    if m:
        try:
            return to_aware(datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))), now)
        except ValueError:
            pass

    # Month DD, YYYY
    m = re.search(
        r"\b(" + "|".join(MONTHS.keys()) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+(20\d{2})\b",
        lower,
        re.IGNORECASE,
    )
    if m:
        try:
            return to_aware(datetime(int(m.group(3)), MONTHS[m.group(1).lower().rstrip(".")], int(m.group(2))), now)
        except ValueError:
            pass

    # DD Month YYYY
    m = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(MONTHS.keys()) + r")\.?[,]?\s+(20\d{2})\b",
        lower,
        re.IGNORECASE,
    )
    if m:
        try:
            return to_aware(datetime(int(m.group(3)), MONTHS[m.group(2).lower().rstrip(".")], int(m.group(1))), now)
        except ValueError:
            pass

    # Try Python ISO parser for metadata values such as 2026-06-22T10:30:00Z.
    cleaned = s.strip().replace("Z", "+00:00")
    try:
        return to_aware(datetime.fromisoformat(cleaned), now)
    except Exception:
        return None


def iso_date(dt: Optional[datetime]) -> str:
    return dt.date().isoformat() if dt else "unknown"


def within_lookback(dt: Optional[datetime], days: int, now: Optional[datetime] = None) -> bool:
    if not dt:
        return False
    now = now or now_local()
    dt = to_aware(dt, now)
    return (now - timedelta(days=days)) <= dt <= (now + timedelta(days=1))


def ddg_df(days: int) -> str:
    if days <= 1:
        return "d"
    if days <= 7:
        return "w"
    if days <= 31:
        return "m"
    if days <= 366:
        return "y"
    return ""


def resolve_ddg_url(href: str, fallback_text: str = "") -> str:
    href = (href or "").strip()
    fallback_text = (fallback_text or "").strip()
    if href.startswith("//"):
        href = "https:" + href
    if href.startswith("/"):
        href = "https://duckduckgo.com" + href
    if href:
        try:
            parsed = urllib.parse.urlparse(href)
            qs = urllib.parse.parse_qs(parsed.query)
            if "uddg" in qs and qs["uddg"]:
                return qs["uddg"][0]
        except Exception:
            pass
        if href.startswith(("http://", "https://")):
            return href
    if fallback_text:
        if fallback_text.startswith(("http://", "https://")):
            return fallback_text
        return "https://" + fallback_text.lstrip("/")
    return ""


def _http_get(url: str, *, headers: dict | None = None, timeout: int = 20,
               retries: int = 2, backoff: float = 1.5, **kwargs) -> Optional[requests.Response]:
    """GET with small retry/backoff — many trade-press sites are flaky under bot UA."""
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    hdrs.update(headers or {})
    last_exc = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=hdrs, timeout=timeout, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_exc = e
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
    if last_exc:
        pass
    return None


def resolve_google_news_url(url: str) -> str:
    """Resolve a Google News redirect to the publisher's canonical URL.

    Google News is an index/discovery channel, never a publishable source URL.
    If its redirect cannot be resolved, the result is deliberately discarded by
    the caller rather than storing news.google.com as an "Official Source".
    """
    if not url:
        return ""
    host = urllib.parse.urlparse(url).netloc.lower()
    if "news.google.com" not in host:
        return url
    try:
        response = requests.get(url, headers=DEFAULT_HTTP_HEADERS, timeout=12, allow_redirects=True)
        response.raise_for_status()
        final_url = response.url
        final_host = urllib.parse.urlparse(final_url).netloc.lower()
        return final_url if final_url and "news.google.com" not in final_host else ""
    except requests.RequestException:
        return ""


def search_google_news_rss(query: str, max_results: int = 8, lookback_days: int = 30) -> list[dict[str, Any]]:
    """Search Google News RSS — a resilient, date-stamped alternative to scraping
    DuckDuckGo's HTML results page. Every item ships with a real pubDate, so it
    plugs directly into the same freshness pipeline as the RSS feeds.
    """
    now = now_local()
    encoded = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}+when:{lookback_days}d&hl=en-US&gl=US&ceid=US:en"
    resp = _http_get(url, timeout=15)
    if resp is None:
        return []

    results: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(resp.content)
        items = root.findall(".//item")
        for item in items[:max_results]:
            title = child_text(item, ["title"])
            link = child_text(item, ["link"])
            desc = child_text(item, ["description"])
            date_raw = child_text(item, ["pubDate"])
            source_tag = item.find("source")
            src_name = source_tag.text.strip() if source_tag is not None and source_tag.text else "Google News"
            dt = parse_rss_date(date_raw)
            canonical_link = resolve_google_news_url(link)
            if not title or not canonical_link:
                continue
            results.append({
                "title": title,
                "snippet": re.sub(r"<[^>]+>", "", desc or "")[:350],
                "source": canonical_link,
                "query": query,
                "feed": f"GoogleNews:{src_name}",
                "search_date": iso_date(dt),
                "date_source": "google_news_rss" if dt else "unknown",
                "_date_dt": dt,
            })
    except Exception as e:
        print(f"  ⚠ Не удалось разобрать Google News RSS для «{query}»: {e}")
    return results


# DuckDuckGo's HTML endpoint is optional: RSS and direct industry sources are
# collected independently.  Keep the failure state so gather_signals() can
# stop retrying an unavailable endpoint for every query in a scan.
_last_ddg_request_failed = False


def search_duckduckgo(query: str, max_results: int = 5, lookback_days: int = 30) -> list[dict[str, Any]]:
    """Search DuckDuckGo HTML with a date filter (df=m for 30 days).

    DDG may be unavailable or throttle automated requests. A failed request is
    deliberately reported to the caller, which opens a circuit breaker for the
    rest of the scan rather than spending minutes on identical timeouts.
    """
    global _last_ddg_request_failed
    _last_ddg_request_failed = False
    # GET works in more corporate/proxy environments than the legacy POST
    # endpoint. The lite endpoint is a second independent DDG frontend.
    params = {"q": query}
    df = ddg_df(lookback_days)
    if df:
        params["df"] = df
    headers = {
        **DEFAULT_HTTP_HEADERS,
        "Referer": "https://duckduckgo.com/",
        "DNT": "1",
    }
    timeout = max(1, int(os.environ.get("NEWS_DDG_TIMEOUT_SECONDS", "12")))
    resp = None
    errors: list[str] = []
    for url in ("https://html.duckduckgo.com/html/", "https://lite.duckduckgo.com/lite/"):
        try:
            candidate = requests.get(url, params=params, headers=headers, timeout=timeout)
            candidate.raise_for_status()
            resp = candidate
            break
        except requests.RequestException as e:
            errors.append(f"{urllib.parse.urlparse(url).netloc}: {e}")

    if resp is None:
        _last_ddg_request_failed = True
        print(f"  ⚠ DDG недоступен для «{query}»: {'; '.join(errors)}")
        return []

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        # html.duckduckgo.com uses result__* classes; lite.duckduckgo.com
        # uses result-link anchors. Supporting both avoids a single fragile
        # endpoint and keeps DDG search available when one frontend is blocked.
        blocks = soup.select(".result__body") or soup.select(".result")
        if blocks:
            candidates = [
                (
                    r.select_one("a.result__a") or r.select_one(".result__title a"),
                    r.select_one(".result__snippet"),
                    r.select_one(".result__url"),
                    r.get_text(" ", strip=True),
                )
                for r in blocks
            ]
        else:
            candidates = [
                (a, a.find_parent("td") or a.parent, None, (a.find_parent("tr") or a.parent).get_text(" ", strip=True))
                for a in soup.select("a.result-link")
            ]

        for title_el, snip_el, url_el, result_text in candidates:
            title = title_el.get_text(" ", strip=True) if title_el else ""
            snippet = snip_el.get_text(" ", strip=True) if snip_el else ""
            fallback_url = url_el.get_text(" ", strip=True) if url_el else ""
            source = resolve_ddg_url(title_el.get("href", "") if title_el else "", fallback_url)
            if not title or not source or source in seen:
                continue
            seen.add(source)
            date_dt = parse_any_date(result_text)
            results.append({
                "title": title,
                "snippet": snippet,
                "source": source,
                "query": query,
                "search_date": iso_date(date_dt),
                "date_source": "duckduckgo_snippet" if date_dt else "unknown",
                "_date_dt": date_dt,
            })
            if len(results) >= max_results:
                break
    except Exception as e:
        print(f"  ⚠ Не удалось разобрать выдачу DDG для «{query}»: {e}")
    return results


def _find_date_in_jsonld(obj: Any) -> Optional[str]:
    if isinstance(obj, dict):
        for k in ("datePublished", "dateModified", "uploadDate", "dateCreated"):
            if obj.get(k):
                return str(obj[k])
        for v in obj.values():
            found = _find_date_in_jsonld(v)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_date_in_jsonld(item)
            if found:
                return found
    return None


def extract_page_date(url: str) -> tuple[Optional[datetime], str]:
    """Fetch a page and try to find publication date in metadata/JSON-LD/time tags."""
    if not url:
        return None, "no_url"
    headers = DEFAULT_HTTP_HEADERS.copy()
    try:
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException:
        return None, "fetch_failed"

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")

        meta_keys = [
            "article:published_time", "article:modified_time", "og:published_time",
            "datePublished", "dateModified", "publishdate", "pubdate", "timestamp",
            "DC.date", "dc.date", "sailthru.date",
        ]
        for key in meta_keys:
            tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
            if tag and tag.get("content"):
                dt = parse_any_date(tag["content"])
                if dt:
                    return dt, f"meta:{key}"

        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = script.get_text(strip=True)
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            value = _find_date_in_jsonld(data)
            if value:
                dt = parse_any_date(value)
                if dt:
                    return dt, "jsonld"

        time_tag = soup.find("time", attrs={"datetime": True})
        if time_tag:
            dt = parse_any_date(time_tag.get("datetime", ""))
            if dt:
                return dt, "time:datetime"

        # Fallback: search only early visible text to avoid picking unrelated dates.
        text = soup.get_text(" ", strip=True)[:5000]
        dt = parse_any_date(text)
        if dt:
            return dt, "page_text"
    except Exception:
        return None, "parse_failed"
    return None, "unknown"



def configured_rss_feeds() -> list[tuple[str, str]]:
    raw = os.environ.get("NEWS_RSS_FEEDS", "").strip()
    if raw:
        feeds: list[tuple[str, str]] = []
        for part in raw.split(";"):
            part = part.strip()
            if not part:
                continue
            if "|" in part:
                name, url = part.split("|", 1)
            else:
                name, url = urllib.parse.urlparse(part).netloc or "RSS", part
            feeds.append((name.strip(), url.strip()))
        if feeds:
            return feeds

    registry = _get_registry()
    if registry is not None:
        entries = [s.as_legacy_rss_tuple() for s in registry.enabled_sources(SourceType.RSS)]
        if entries:
            return entries

    return _FALLBACK_RSS_FEEDS


def text_matches_smt(text: str) -> bool:
    lower = (text or "").lower()
    return any(k in lower for k in SMT_KEYWORDS)


def parse_rss_date(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return to_aware(parsedate_to_datetime(value), now_local())
    except Exception:
        return parse_any_date(value)


def child_text(elem: ET.Element, names: list[str]) -> str:
    for name in names:
        child = elem.find(name)
        if child is not None and child.text:
            return child.text.strip()
    # Namespace-agnostic fallback.
    for child in list(elem):
        local = child.tag.split("}")[-1].lower()
        if local in {n.split(":")[-1].lower() for n in names} and child.text:
            return child.text.strip()
    return ""



def gather_html_signals(lookback_days: int = 30, max_items: int = 50, strict_fresh: bool = True) -> list[dict[str, Any]]:
    """Collect dated items from selected non-RSS news pages.

    Currently supports SMTnet's industry-news list. This broadens coverage with
    a source that is very SMT-specific and has explicit publication dates.
    """
    now = now_local()
    signals: list[dict[str, Any]] = []
    seen: set[str] = set()
    headers = DEFAULT_HTTP_HEADERS.copy()
    html_sources = configured_html_sources()
    print(f"\n  🌐 HTML/news pages: {len(html_sources)} источников")
    try:
        from bs4 import BeautifulSoup
    except Exception as e:
        print(f"     ⚠ HTML sources skipped: bs4 unavailable — {e}")
        return []

    for source_name, url, kind in html_sources:
        kept = 0
        try:
            resp = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            print(f"     ⚠ {source_name}: page недоступна — {e}")
            continue

        if kind == "smtnet_news":
            items = []
            for h in soup.find_all("h3"):
                a = h.find("a")
                if not a or not a.get("href"):
                    continue
                title = a.get_text(" ", strip=True)
                href = urllib.parse.urljoin(url, a.get("href"))
                parent_text = h.parent.get_text(" ", strip=True) if h.parent else h.get_text(" ", strip=True)
                dt = parse_any_date(parent_text, now)
                snippet = parent_text.replace(title, "", 1).strip()
                items.append((title, href, snippet, dt))
            for title, href, snippet, dt in items[:max_items]:
                if not title or href in seen:
                    continue
                combined = f"{title} {snippet} {source_name}"
                if not text_matches_smt(combined):
                    continue
                if dt and not within_lookback(dt, lookback_days, now):
                    continue
                if strict_fresh and not dt:
                    continue
                seen.add(href)
                signals.append({
                    "title": title,
                    "snippet": snippet[:350],
                    "source": href,
                    "query": f"HTML:{source_name}",
                    "feed": source_name,
                    "published_at": iso_date(dt),
                    "date_source": "html_page_date" if dt else "unknown",
                    "date_verified": bool(dt),
                    "fresh_within_days": bool(dt and within_lookback(dt, lookback_days, now)),
                })
                kept += 1
        elif kind == "generic_dated_list":
            # Generic fallback for trade-press listing pages without RSS:
            # scan headline-like links (h2/h3/h4 or common "title" classes),
            # then try to find a nearby date in the surrounding block or, if
            # that fails, on the article page itself (bounded by max_items).
            items = []
            heading_tags = soup.find_all(["h2", "h3", "h4"]) or []
            card_links = soup.select("a.title, a.entry-title, .post-title a, .headline a") or []
            candidates = heading_tags + card_links
            for el in candidates:
                a = el if el.name == "a" else el.find("a")
                if not a or not a.get("href"):
                    continue
                title = a.get_text(" ", strip=True)
                if not title or len(title) < 8:
                    continue
                href = urllib.parse.urljoin(url, a.get("href"))
                block = el.parent.get_text(" ", strip=True) if el.parent else el.get_text(" ", strip=True)
                dt = parse_any_date(block, now)
                snippet = block.replace(title, "", 1).strip()
                items.append((title, href, snippet, dt))
            for title, href, snippet, dt in items[:max_items]:
                if not title or href in seen:
                    continue
                combined = f"{title} {snippet} {source_name}"
                if not text_matches_smt(combined):
                    continue
                if not dt:
                    # Listing pages rarely show dates inline; verify on the
                    # article page itself before accepting.
                    dt, _ = extract_page_date(href)
                if dt and not within_lookback(dt, lookback_days, now):
                    continue
                if strict_fresh and not dt:
                    continue
                seen.add(href)
                signals.append({
                    "title": title,
                    "snippet": snippet[:350],
                    "source": href,
                    "query": f"HTML:{source_name}",
                    "feed": source_name,
                    "published_at": iso_date(dt),
                    "date_source": "html_page_date" if dt else "unknown",
                    "date_verified": bool(dt),
                    "fresh_within_days": bool(dt and within_lookback(dt, lookback_days, now)),
                })
                kept += 1
        print(f"     → {source_name}: свежих сигналов принято {kept}")
    return signals



def configured_vendor_sources() -> list[tuple[str, str, str]]:
    """Return vendor pages, in precedence order: env override > YAML registry > hardcoded fallback.

    Env format:
      NEWS_VENDOR_SOURCES=Name|https://url|group;Name2|https://url2|group
    """
    raw = os.environ.get("NEWS_VENDOR_SOURCES", "").strip()
    if raw:
        sources: list[tuple[str, str, str]] = []
        for part in raw.split(";"):
            part = part.strip()
            if not part:
                continue
            pieces = [x.strip() for x in part.split("|")]
            if len(pieces) == 1:
                name = urllib.parse.urlparse(pieces[0]).netloc or "Vendor"
                url = pieces[0]
                group = "vendor"
            elif len(pieces) == 2:
                name, url = pieces
                group = "vendor"
            else:
                name, url, group = pieces[0], pieces[1], pieces[2]
            sources.append((name, url, group))
        if sources:
            return sources

    registry = _get_registry()
    if registry is not None:
        entries = [s.as_legacy_vendor_tuple() for s in registry.enabled_sources(SourceType.VENDOR)]
        if entries:
            return entries

    return _FALLBACK_VENDOR_SOURCES


GENERIC_LINK_TITLES = {
    "contact", "contact us", "news", "news release", "announcement",
    "announcements", "read more", "read more »", "more", "learn more",
    "site map", "sitemap", "privacy policy", "terms", "skip to content",
    "personal information protection statement", "home", "events", "products",
    "japanese", "customer support", "support", "history", "full line solutions",
}

VENDOR_LINK_PATH_HINTS = [
    "news", "press", "release", "article", "blog", "media", "event",
    "solution", "smt", "aoi", "spi", "axi", "x-ray", "xray",
    "datasheet", "brochure", "spec", "specification", "catalog", "manual",
    "pdf", "download",
]


def _vendor_link_candidate(page_url: str, href: str, title: str) -> bool:
    title_clean = " ".join((title or "").split()).strip()
    if len(title_clean) < 8:
        return False
    if title_clean.lower() in GENERIC_LINK_TITLES:
        return False
    if title_clean.lower().startswith(("read more", "skip", "contact")):
        return False
    if href.startswith(("mailto:", "tel:", "javascript:")):
        return False
    absolute = urllib.parse.urljoin(page_url, href)
    parsed = urllib.parse.urlparse(absolute)
    page_host = urllib.parse.urlparse(page_url).netloc.replace("www.", "")
    host = parsed.netloc.replace("www.", "")
    if page_host and host and page_host not in host and host not in page_host:
        return False
    path = (parsed.path or "").lower()
    if parsed.fragment and not parsed.path:
        return False
    if absolute.rstrip("/") == page_url.rstrip("/"):
        return False
    if any(skip in path for skip in ["/support", "/privacy", "/policy", "/sitemap", "/contact", "/concept/history", "/category-"]):
        return False
    # Product/category pages are not news unless the title itself says it is a launch/update.
    if "/product/" in path and not re.search(r"\b(new|launch|introduc|release|update|series|platform)\b", title_clean, re.I):
        return False
    return any(h in path for h in VENDOR_LINK_PATH_HINTS) or re.search(r"\b(new|launch|introduc|release|unveil|showcase|exhibit|award|appoint|expand)\b", title_clean, re.I) is not None


def _extract_page_title_and_date(url: str) -> tuple[str, Optional[datetime]]:
    headers = DEFAULT_HTTP_HEADERS.copy()
    try:
        resp = requests.get(url, headers=headers, timeout=12, allow_redirects=True)
        resp.raise_for_status()
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        title = ""
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(" ", strip=True)
        if not title:
            og = soup.find("meta", attrs={"property": "og:title"}) or soup.find("meta", attrs={"name": "title"})
            if og and og.get("content"):
                title = og["content"].strip()
        dt, _ = extract_page_date(resp.url)
        return title, dt
    except Exception:
        return "", None


def gather_vendor_signals(lookback_days: int = 30, max_links_per_vendor: int = 25,
                          max_items_per_vendor: int = 5, strict_fresh: bool = True) -> list[dict[str, Any]]:
    """Collect fresh signals directly from vendor/manufacturer pages."""
    now = now_local()
    sources = configured_vendor_sources()
    signals: list[dict[str, Any]] = []
    seen: set[str] = set()
    headers = DEFAULT_HTTP_HEADERS.copy()
    print(f"\n  🏭 Vendor/manufacturer pages: {len(sources)} источников")
    try:
        from bs4 import BeautifulSoup
    except Exception as e:
        print(f"     ⚠ Vendor sources skipped: bs4 unavailable — {e}")
        return []

    verify_child_pages = _env_bool("NEWS_VENDOR_VERIFY_PAGES", "0")

    for vendor_name, page_url, vendor_group in sources:
        kept = 0
        checked = 0
        try:
            resp = requests.get(page_url, headers=headers, timeout=18, allow_redirects=True)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            print(f"     ⚠ {vendor_name}: vendor page недоступна — {e}")
            continue

        candidates: list[tuple[str, str, str, Optional[datetime]]] = []
        for a in soup.find_all("a", href=True):
            title = a.get_text(" ", strip=True)
            href = urllib.parse.urljoin(resp.url, a.get("href", ""))
            if not _vendor_link_candidate(resp.url, href, title):
                continue
            parent = a
            parent_text = ""
            dt = None
            for _ in range(4):
                if parent.parent:
                    parent = parent.parent
                block_text = parent.get_text(" ", strip=True)
                # Avoid taking dates from huge page/nav containers; they often
                # contain unrelated current/event dates.
                if title in block_text and len(block_text) <= 1200:
                    possible_dt = parse_any_date(block_text, now)
                    if possible_dt:
                        parent_text = block_text
                        dt = possible_dt
                        break
                    parent_text = block_text
            if not parent_text:
                parent_text = a.parent.get_text(" ", strip=True) if a.parent else title
            snippet = re.sub(r"\s+", " ", parent_text.replace(title, "", 1)).strip()[:350]
            candidates.append((title, href, snippet, dt))

        # Deduplicate while preserving order.
        unique: list[tuple[str, str, str, Optional[datetime]]] = []
        local_seen: set[str] = set()
        for item in candidates:
            if item[1] in local_seen:
                continue
            local_seen.add(item[1])
            unique.append(item)

        for title, href, snippet, dt in unique[:max_links_per_vendor]:
            if href in seen:
                continue
            checked += 1
            if href.lower().endswith(".pdf") and _PDF_COLLECTOR_AVAILABLE:
                doc = pdf_collector.fetch_and_parse_pdf(href, timeout=15)
                if doc and doc.text:
                    sig = doc.to_signal(vendor_name=vendor_name, vendor_group=vendor_group)
                    pub_dt = None
                    if sig.get("published_at") and sig["published_at"] != "unknown":
                        try:
                            pub_dt = datetime.fromisoformat(sig["published_at"])
                        except Exception:
                            pass
                    if pub_dt and not within_lookback(pub_dt, lookback_days, now):
                        continue
                    if strict_fresh and not pub_dt:
                        continue
                    seen.add(href)
                    signals.append(sig)
                    kept += 1
                    if kept >= max_items_per_vendor:
                        break
                    continue
            page_title = ""
            if (not dt or len(title) < 18) and verify_child_pages:
                page_title, page_dt = _extract_page_title_and_date(href)
                if page_dt:
                    dt = page_dt
                if page_title and len(page_title) > len(title):
                    title = page_title
            if dt and not within_lookback(dt, lookback_days, now):
                continue
            if strict_fresh and not dt:
                continue
            combined = f"{title} {snippet} {vendor_name} {vendor_group}"
            if not text_matches_smt(combined):
                continue
            seen.add(href)
            signals.append({
                "title": title,
                "snippet": snippet,
                "source": href,
                "query": f"VENDOR:{vendor_name}",
                "feed": vendor_name,
                "vendor_group": vendor_group,
                "published_at": iso_date(dt),
                "date_source": "vendor_page" if dt else "unknown",
                "date_verified": bool(dt),
                "fresh_within_days": bool(dt and within_lookback(dt, lookback_days, now)),
            })
            kept += 1
            if kept >= max_items_per_vendor:
                break
        print(f"     → {vendor_name}: свежих сигналов принято {kept}/{checked}")
        time.sleep(0.2)
    return signals


def gather_pdf_signals(
    lookback_days: int = 30,
    max_items_per_vendor: int = 2,
    strict_fresh: bool = True,
) -> list[dict[str, Any]]:
    """Collect technical facts and specifications directly from vendor PDF documents."""
    if not _PDF_COLLECTOR_AVAILABLE or not _env_bool("NEWS_PDF_COLLECTOR_ENABLED", "1"):
        return []
    now = now_local()
    sources = configured_vendor_sources()
    signals: list[dict[str, Any]] = []
    seen: set[str] = set()
    print(f"\n  📑 PDF technical documents: scan across {len(sources)} vendor pages")

    for vendor_name, page_url, vendor_group in sources:
        kept = 0
        try:
            links = pdf_collector.discover_pdf_links_on_page(page_url, timeout=12, max_links=8)
        except Exception:
            continue
        for l in links[:max_items_per_vendor]:
            pdf_url = l.get("url", "")
            if not pdf_url or pdf_url in seen:
                continue
            doc = pdf_collector.fetch_and_parse_pdf(pdf_url, timeout=15)
            if not doc or not doc.text:
                continue
            sig = doc.to_signal(vendor_name=vendor_name, vendor_group=vendor_group)
            pub_dt = None
            if sig.get("published_at") and sig["published_at"] != "unknown":
                try:
                    pub_dt = datetime.fromisoformat(sig["published_at"])
                except Exception:
                    pass
            if pub_dt and not within_lookback(pub_dt, lookback_days, now):
                continue
            if strict_fresh and not pub_dt:
                continue
            seen.add(pdf_url)
            signals.append(sig)
            kept += 1
            if kept >= max_items_per_vendor:
                break
        if kept:
            print(f"     → {vendor_name}: свежих PDF-документов принято {kept}")
    return signals


def _wordpress_api_signals(
    response: requests.Response,
    feed_name: str,
    feed_url: str,
    lookback_days: int,
    strict_fresh: bool,
    now: datetime,
) -> list[dict[str, Any]]:
    """Normalize a public WordPress REST posts endpoint into RSS-like signals.

    Some publishers protect ``/feed/`` with a WAF while intentionally leaving
    their documented public ``wp-json/wp/v2/posts`` API available.  This is a
    first-party API fallback, not scraped or fabricated content.
    """
    try:
        posts = response.json()
    except (ValueError, json.JSONDecodeError):
        return []
    if not isinstance(posts, list):
        return []

    signals: list[dict[str, Any]] = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        title = re.sub(r"<[^>]+>", "", str(post.get("title", {}).get("rendered", ""))).strip()
        link = str(post.get("link", "")).strip()
        excerpt = re.sub(r"<[^>]+>", "", str(post.get("excerpt", {}).get("rendered", ""))).strip()
        dt = parse_any_date(str(post.get("date", "")), now)
        if not title or not link or not text_matches_smt(f"{title} {excerpt} {feed_name}"):
            continue
        if dt and not within_lookback(dt, lookback_days, now):
            continue
        if strict_fresh and not dt:
            continue
        signals.append({
            "title": title,
            "snippet": excerpt[:350],
            "source": link,
            "query": f"WordPress API:{feed_name}",
            "feed": feed_name,
            "published_at": iso_date(dt),
            "date_source": "wordpress_api_date" if dt else "unknown",
            "date_verified": bool(dt),
            "fresh_within_days": bool(dt and within_lookback(dt, lookback_days, now)),
        })
    return signals


def gather_rss_signals(lookback_days: int = 30, max_items_per_feed: int = 20, strict_fresh: bool = True) -> list[dict[str, Any]]:
    """Collect fresh items from configured RSS/Atom feeds.

    RSS is intentionally used as a first-class source because search engines can
    throttle HTML scraping. Feeds provide explicit publication dates and are a
    better fit for the "latest within 30 days" requirement.
    """
    now = now_local()
    feeds = configured_rss_feeds()
    signals: list[dict[str, Any]] = []
    seen: set[str] = set()
    headers = DEFAULT_HTTP_HEADERS.copy()

    print(f"\n  📰 RSS/news feeds: {len(feeds)} источников")
    for feed_name, feed_url in feeds:
        try:
            resp = requests.get(feed_url, headers=headers, timeout=20, allow_redirects=True)
            resp.raise_for_status()
            if "/wp-json/wp/v2/posts" in feed_url:
                api_signals = _wordpress_api_signals(
                    resp, feed_name, feed_url, lookback_days, strict_fresh, now
                )
                signals.extend(api_signals)
                print(f"     → {feed_name}: свежих сигналов принято {len(api_signals)} (WordPress API)")
                continue
            try:
                root = ET.fromstring(resp.content)
            except ET.ParseError:
                cleaned = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;)", "&amp;", resp.text)
                root = ET.fromstring(cleaned.encode("utf-8", errors="ignore"))
        except Exception as e:
            print(f"     ⚠ {feed_name}: RSS недоступен — {e}")
            continue

        items = root.findall(".//item")
        if not items:
            # Atom fallback
            items = [e for e in root.iter() if e.tag.split("}")[-1].lower() == "entry"]

        kept = 0
        for item in items[:max_items_per_feed]:
            title = child_text(item, ["title"])
            link = child_text(item, ["link"])
            if not link:
                for child in list(item):
                    if child.tag.split("}")[-1].lower() == "link" and child.attrib.get("href"):
                        link = child.attrib["href"].strip()
                        break
            desc = child_text(item, ["description", "summary", "content:encoded"])
            date_raw = child_text(item, ["pubDate", "published", "updated", "dc:date"])
            dt = parse_rss_date(date_raw)
            combined = f"{title} {desc} {feed_name}"

            if not title or not link or link in seen:
                continue
            if not text_matches_smt(combined):
                continue
            if dt and not within_lookback(dt, lookback_days, now):
                continue
            if strict_fresh and not dt:
                continue

            seen.add(link)
            signals.append({
                "title": title,
                "snippet": re.sub(r"<[^>]+>", "", desc)[:350],
                "source": link,
                "query": f"RSS:{feed_name}",
                "feed": feed_name,
                "published_at": iso_date(dt),
                "date_source": "rss_pubDate" if dt else "unknown",
                "date_verified": bool(dt),
                "fresh_within_days": bool(dt and within_lookback(dt, lookback_days, now)),
            })
            kept += 1
        print(f"     → {feed_name}: свежих сигналов принято {kept}/{len(items[:max_items_per_feed])}")
    return signals


def gather_signals(
    queries: list[str],
    max_results_per_query: int = 5,
    do_search: bool = True,
    lookback_days: int = 30,
    strict_fresh: bool = True,
    verify_pages: bool = True,
) -> list[dict[str, Any]]:
    now = now_local()
    cutoff = now - timedelta(days=lookback_days)
    signals: list[dict[str, Any]] = []
    seen: set[str] = set()
    seen_titles: list[str] = []  # for fuzzy near-duplicate detection across sources

    def _is_near_duplicate_title(title: str) -> bool:
        """Catch the same story picked up via search + RSS + vendor with a
        slightly different URL (redirects, AMP pages, syndication)."""
        norm = re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()
        norm_tokens = set(norm.split())
        if not norm_tokens:
            return False
        for prev in seen_titles:
            prev_tokens = set(prev.split())
            if not prev_tokens:
                continue
            overlap = len(norm_tokens & prev_tokens) / max(1, min(len(norm_tokens), len(prev_tokens)))
            if overlap >= 0.85:
                return True
        seen_titles.append(norm)
        return False

    print(f"   Окно свежести: {cutoff.date().isoformat()} → {now.date().isoformat()} ({lookback_days} дн.)")
    print(f"   Режим свежести: {'strict (без даты отбрасываем)' if strict_fresh else 'keep undated'}")
    print(f"   Проверка страниц: {'on' if verify_pages else 'off'}")

    def _accept(f: dict[str, Any], url: str) -> bool:
        if not url or url in seen:
            return False
        dt = f.pop("_date_dt", None)
        date_source = f.get("date_source", "unknown")
        if verify_pages and not dt:
            page_dt, page_source = extract_page_date(url)
            if page_dt:
                dt = page_dt
                date_source = page_source
        f["published_at"] = iso_date(dt)
        f["date_source"] = date_source
        f["fresh_within_days"] = bool(dt and within_lookback(dt, lookback_days, now))
        f["date_verified"] = bool(dt)
        if dt and not within_lookback(dt, lookback_days, now):
            return False
        if strict_fresh and not dt:
            return False
        if _is_near_duplicate_title(f.get("title", "")):
            return False
        seen.add(url)
        return True

    # 1) DuckDuckGo HTML search (best-effort; often throttled). It is not a
    # required source: Google News RSS, configured RSS feeds, vendor pages and
    # dated HTML sources continue below when DDG is blocked.
    ddg_found: list[dict[str, Any]] = []
    ddg_enabled = _env_bool("NEWS_DDG_ENABLED", "1")
    if do_search and ddg_enabled:
        for q in queries:
            print(f"  🔍 DDG: {q}")
            found = search_duckduckgo(q, max_results_per_query, lookback_days)
            ddg_found.extend(found)
            if _last_ddg_request_failed:
                print("     → DDG отключён до конца этого запуска; продолжаю с Google News RSS, RSS-лентами и сайтами вендоров.")
                break
            time.sleep(0.4)
    elif do_search:
        print("  ℹ DuckDuckGo отключён (NEWS_DDG_ENABLED=0); использую Google News RSS, RSS-ленты и сайты вендоров.")

    if ddg_found and verify_pages:
        # Verifying each page's publication date is the slowest step because it
        # means one HTTP fetch per candidate URL. Running these concurrently
        # keeps a 30+ query scan from taking many minutes.
        import concurrent.futures
        urls = [f.get("source", "") for f in ddg_found]
        date_map: dict[str, tuple[Optional[datetime], str]] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            future_to_url = {pool.submit(extract_page_date, u): u for u in urls if u}
            for fut in concurrent.futures.as_completed(future_to_url):
                u = future_to_url[fut]
                try:
                    date_map[u] = fut.result()
                except Exception:
                    date_map[u] = (None, "fetch_failed")
        for f in ddg_found:
            u = f.get("source", "")
            if u in date_map:
                page_dt, page_source = date_map[u]
                if page_dt:
                    f["_date_dt"] = page_dt
                    f["date_source"] = page_source

    kept_ddg = 0
    for f in ddg_found:
        if _accept(f, f.get("source", "")):
            signals.append(f)
            kept_ddg += 1
    if ddg_found:
        print(f"     → DDG всего принято: {kept_ddg}/{len(ddg_found)}")

    # 2) Google News RSS — resilient date-stamped search fallback/complement.
    if do_search and _env_bool("NEWS_GOOGLE_RSS_ENABLED", "1"):
        gn_queries = configured_google_news_queries()
        print(f"\n  📡 Google News RSS: {len(gn_queries)} запросов")
        for q in gn_queries:
            found = search_google_news_rss(q, max_results_per_query, lookback_days)
            kept = 0
            for f in found:
                if _accept(f, f.get("source", "")):
                    signals.append(f)
                    kept += 1
            print(f"     → {q}: принято {kept}/{len(found)}")
            time.sleep(0.3)

    # 3) RSS fallback/augmentation: search engines may challenge bots; RSS feeds
    # keep the news collector reliable and date-bounded.
    rss_signals = gather_rss_signals(
        lookback_days=lookback_days,
        max_items_per_feed=int(os.environ.get("NEWS_RSS_MAX_ITEMS", "20")),
        strict_fresh=strict_fresh,
    ) if do_search else []
    for item in rss_signals:
        url = item.get("source", "")
        if url and url not in seen and not _is_near_duplicate_title(item.get("title", "")):
            seen.add(url)
            signals.append(item)

    html_signals = gather_html_signals(
        lookback_days=lookback_days,
        max_items=int(os.environ.get("NEWS_HTML_MAX_ITEMS", "50")),
        strict_fresh=strict_fresh,
    ) if do_search else []
    for item in html_signals:
        url = item.get("source", "")
        if url and url not in seen and not _is_near_duplicate_title(item.get("title", "")):
            seen.add(url)
            signals.append(item)

    vendor_signals = gather_vendor_signals(
        lookback_days=lookback_days,
        max_links_per_vendor=int(os.environ.get("NEWS_VENDOR_MAX_LINKS", "25")),
        max_items_per_vendor=int(os.environ.get("NEWS_VENDOR_MAX_ITEMS", "5")),
        strict_fresh=strict_fresh,
    ) if do_search and _env_bool("NEWS_VENDOR_SOURCES_ENABLED", "1") else []
    for item in vendor_signals:
        url = item.get("source", "")
        if url and url not in seen and not _is_near_duplicate_title(item.get("title", "")):
            seen.add(url)
            signals.append(item)

    pdf_signals = gather_pdf_signals(
        lookback_days=lookback_days,
        max_items_per_vendor=int(os.environ.get("NEWS_PDF_MAX_ITEMS", "2")),
        strict_fresh=strict_fresh,
    ) if do_search and _env_bool("NEWS_PDF_COLLECTOR_ENABLED", "1") else []
    for item in pdf_signals:
        url = item.get("source", "")
        if url and url not in seen and not _is_near_duplicate_title(item.get("title", "")):
            seen.add(url)
            signals.append(item)

    return signals


def filter_existing_duplicates(signals: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Remove signals already covered on SMTInsider."""
    if not _env_bool("NEWS_DEDUPE_EXISTING", "1"):
        return signals, []
    idx = dedupe.load_existing_index()
    if not idx.rows:
        return signals, []
    kept: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for sig in signals:
        d = dedupe.duplicate_for_signal(idx, sig)
        if d.is_duplicate:
            copy = dict(sig)
            copy["duplicate_match"] = d.to_dict()
            skipped.append(copy)
        else:
            kept.append(sig)
    return kept, skipped


def signal_editorial_score(s: dict[str, Any]) -> int:
    """Rank fresh signals by engineering usefulness for SMTInsider."""
    text = f"{s.get('title','')} {s.get('snippet','')} {s.get('feed','')}".lower()
    score = 0
    weights = {
        "aoi": 12, "spi": 12, "axi": 12, "x-ray": 12, "inspection": 10,
        "smt": 10, "pcb": 8, "pcba": 8, "assembly": 7, "solder": 8,
        "reflow": 8, "pick and place": 8, "placement": 7, "stencil": 7,
        "tooling": 7, "fixture": 7, "test": 6, "automation": 6,
        "equipment": 6, "process": 6, "manufacturing": 5, "factory": 5,
        "defect": 9, "quality": 7, "throughput": 5, "yield": 6,
        "cpk": 9, "false call": 9, "voiding": 9, "head-in-pillow": 9,
        "ipc": 8, "mes": 6, "traceability": 6, "cfx": 6,
        # THT / depaneling / test — added with the Sciencgo/Robotas/ASYS/
        # Forwessun/TAGARNO vendor sources so their signals score fairly
        # instead of being pushed to the bottom for lacking SMT-specific terms.
        "through-hole": 10, "tht": 10, "insertion": 8, "depaneling": 9,
        "in-circuit test": 9, "ict": 7, "functional test": 8, "clinch": 6,
    }
    for k, w in weights.items():
        if k in text:
            score += w
    penalties = [
        "appoints", "appointed", "director", "ceo", "cfo", "market development",
        "acquisition", "raises", "funding", "partnership" if "technology" not in text else "",
        "supply chain" if not any(k in text for k in ["pcb", "smt", "assembly"]) else "",
        "award" if "outstanding" in text or "contributor" in text else "",
        "webinar", "hiring", "job opening",
    ]
    for p in penalties:
        if p and p in text:
            score -= 8

    # Concrete numbers/specs are what makes a signal useful for engineering
    # writing — reward snippets that actually contain figures.
    if re.search(r"\d+\s?%|\bx\d+\b|\d+\s?(nm|mm|hz|khz|w|kg|s\b|sec|ppm|micron|μm)", text):
        score += 6
    if re.search(r"\d{2,}", text):
        score += 2

    # Direct vendor/manufacturer sources are typically more authoritative and
    # timely than aggregator republication.
    source_url = str(s.get("source", "")).lower()
    feed_name = str(s.get("feed", "")).lower()
    if "vendor" in feed_name or any(
        host in source_url for host in (
            "kohyoung", "tri.com.tw", "viscom.com", "sakicorp", "vitrox",
            "creativeelectron", "yamaha-motor", "juki.co.jp", "asmpt.com",
            "fuji-euro", "essemtec", "europlacer", "hellerindustries",
            "rehm-group", "pillarhouse", "aimsolder", "kyzen", "mycronic",
            "indium.com", "photostencil", "cyberoptics", "mirtec",
            "xzg-sciencgo", "robotas.com", "asys-group", "forwessun", "tagarno.com",
        )
    ):
        score += 5

    if s.get("date_verified"):
        score += 3

    # Recency bonus: signals from the last few days matter more than those at
    # the edge of the lookback window.
    published = s.get("published_at")
    if published and published != "unknown":
        try:
            pub_dt = datetime.fromisoformat(published)
            age_days = (now_local().date() - pub_dt.date()).days
            if age_days <= 2:
                score += 6
            elif age_days <= 7:
                score += 3
        except Exception:
            pass

    return score


def _diversify_by_source(ranked: list[dict[str, Any]], limit: int, max_per_source: int = 4) -> list[dict[str, Any]]:
    """Cap how many signals from a single feed/source can dominate the LLM
    prompt, so 3 topics aren't all pulled from the one feed that happened to
    return the most items this run."""
    picked: list[dict[str, Any]] = []
    per_source: dict[str, int] = {}
    for s in ranked:
        key = str(s.get("feed") or s.get("query") or "unknown")
        if per_source.get(key, 0) >= max_per_source:
            continue
        picked.append(s)
        per_source[key] = per_source.get(key, 0) + 1
        if len(picked) >= limit:
            break
    return picked


def extract_article_fulltext(url: str, max_chars: int = 1800) -> str:
    """Fetch a signal's article page and pull out the main body text.

    Search/RSS snippets are often 1-2 sentences — not enough for the Writer to
    quote real specs. For the highest-ranked signals we fetch the full page and
    extract paragraph text so the trend brief (and later the article) can cite
    actual numbers instead of paraphrasing a headline.
    """
    resp = _http_get(url, timeout=15)
    if resp is None:
        return ""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        container = soup.find("article") or soup.find(attrs={"class": re.compile(r"(article|post|entry)[-_]?(body|content)", re.I)})
        paragraphs = (container or soup).find_all("p")
        text = " ".join(p.get_text(" ", strip=True) for p in paragraphs)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception:
        return ""


def enrich_top_signals_with_fulltext(ranked_signals: list[dict[str, Any]], top_n: int = 15) -> None:
    """Fetch full article text for the top N ranked signals, in parallel,
    and attach it as `full_text` on each signal dict (mutates in place).
    """
    if not _env_bool("NEWS_FULLTEXT_ENABLED", "1"):
        return
    import concurrent.futures
    top = ranked_signals[:top_n]
    urls = [s.get("source", "") for s in top if s.get("source")]
    if not urls:
        return
    results: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        future_to_url = {pool.submit(extract_article_fulltext, u): u for u in urls}
        for fut in concurrent.futures.as_completed(future_to_url):
            u = future_to_url[fut]
            try:
                results[u] = fut.result()
            except Exception:
                results[u] = ""
    for s in top:
        u = s.get("source", "")
        if u in results and results[u]:
            s["full_text"] = results[u]
        if _PDF_COLLECTOR_AVAILABLE and _env_bool("NEWS_PDF_ENRICH_ENABLED", "1") and not s.get("key_facts"):
            try:
                pdf_links = pdf_collector.discover_pdf_links_on_page(u, timeout=10, max_links=2)
                for pl in pdf_links:
                    pdoc = pdf_collector.fetch_and_parse_pdf(pl["url"], timeout=10)
                    if pdoc and pdoc.key_facts:
                        s["key_facts"] = [
                            f"{f['parameter']}: {f['value']}"
                            for f in pdoc.key_facts if isinstance(f, dict) and "value" in f
                        ]
                        s["technical_specs"] = pdoc.key_facts
                        specs_block = "\n".join(
                            f"- {f['parameter'].upper()}: {f['value']} ({f['provenance']})"
                            for f in pdoc.key_facts if isinstance(f, dict)
                        )
                        s["full_text"] = (s.get("full_text", "") + f"\n\nVERIFIED TECHNICAL SPECIFICATIONS:\n{specs_block}").strip()
                        break
            except Exception:
                pass


def find_corroborating_sources(
    topic: dict[str, Any],
    already_have_urls: set[str],
    lookback_days: int,
    max_new: int = 3,
) -> list[dict[str, Any]]:
    """After a topic is selected, actively search for additional coverage of
    that SPECIFIC topic instead of only hoping the generic pre-collected
    signal pool happens to contain a second source.

    This is what makes multi-source synthesis the normal case rather than a
    lucky accident: a topic about "TRI TR7600 SV" only gets a real second
    perspective if something searches for "TRI TR7600 SV" specifically —
    the 15-80 generic seed queries used for initial collection are far too
    broad to reliably surface a second article about one specific product
    announcement.

    Bounded to `max_new` accepted results and a couple of targeted queries,
    so this doesn't blow up scan time across every topic.
    """
    title = str(topic.get("topic", "")).strip()
    keywords = topic.get("keywords") or []
    if not title:
        return []

    queries = [title]
    if keywords:
        # A keyword-anchored query often surfaces coverage that phrases the
        # headline differently than the original source.
        queries.append(" ".join([title.split(" — ")[0].split(":")[0]] + keywords[:3]))

    now = now_local()
    found: list[dict[str, Any]] = []
    seen_in_this_call: set[str] = set(already_have_urls)

    finders = [lambda qq: search_google_news_rss(qq, max_results=5, lookback_days=lookback_days)]
    # DDG is optional and frequently unreachable in operator networks. Source
    # research later uses official domains, so do not spend 24+ seconds per
    # candidate on a second search engine unless explicitly enabled.
    if _env_bool("NEWS_DDG_TARGETED_ENABLED", "0") and not _last_ddg_request_failed:
        finders.append(lambda qq: search_duckduckgo(qq, max_results=5, lookback_days=lookback_days))

    for q in queries[:2]:
        for finder in finders:
            try:
                results = finder(q)
            except Exception:
                results = []
            for r in results:
                url = canonical_url_local(r.get("source", ""))
                if not url or url in seen_in_this_call:
                    continue
                title_sim = source_expander.token_score(title, r.get("title", ""))
                if title_sim < 0.22:
                    # Not actually about this topic — a targeted query can
                    # still return loosely-related noise.
                    continue
                dt = r.pop("_date_dt", None)
                if not dt and r.get("date_source") != "google_news_rss":
                    dt, date_source = extract_page_date(url)
                    if dt:
                        r["date_source"] = date_source
                if dt and not within_lookback(dt, lookback_days, now):
                    continue
                r["published_at"] = iso_date(dt) if dt else r.get("published_at", "unknown")
                r["source"] = url
                seen_in_this_call.add(url)
                found.append(r)
                if len(found) >= max_new:
                    return found
            if len(found) >= max_new:
                return found
    return found


def canonical_url_local(url: str) -> str:
    """Thin wrapper so this module doesn't need a hard import cycle with
    dedupe.py just for URL canonicalization inside find_corroborating_sources."""
    try:
        return dedupe.canonical_url(url)
    except Exception:
        return (url or "").strip()


def _expired_future_event(text: str, now: datetime) -> Optional[str]:
    """Reject pre-event announcements once their scheduled event is past."""
    low = (text or "").lower()
    if not any(phrase in low for phrase in ("will exhibit", "will demonstrate", "will show", "taking place", "expo", "tech forum")):
        return None
    month_map = {name: index for index, name in enumerate(
        ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"), 1
    )}
    for match in re.finditer(r"\b(" + "|".join(month_map) + r")\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+(20\d{2})\b", low):
        try:
            event_date = datetime(int(match.group(3)), month_map[match.group(1)], int(match.group(2)), tzinfo=now.tzinfo)
            if event_date.date() < now.date():
                return event_date.date().isoformat()
        except ValueError:
            continue
    return None


def build_briefs(signals: list[dict[str, Any]], max_topics: int, lookback_days: int) -> dict:
    if not signals:
        return {"topics": []}

    ranked_signals = sorted(signals, key=signal_editorial_score, reverse=True)
    # Persist the score on each signal so it's visible in the prompt and in
    # any saved briefs/signals JSON for debugging/analysis.
    for s in ranked_signals:
        s["_editorial_score"] = signal_editorial_score(s)

    # Enrich the strongest candidates with real article text before asking the
    # LLM to pick topics — this is what makes `key_facts`/`angle` concrete
    # instead of generic.
    print(f"\n  📄 Извлекаю полный текст топ-сигналов для контекста...")
    enrich_top_signals_with_fulltext(ranked_signals, top_n=int(os.environ.get("NEWS_FULLTEXT_TOP_N", "20")))

    # Cap how many signals from one feed dominate the prompt so 60 slots
    # aren't eaten by a single prolific source.
    prompt_signals = _diversify_by_source(ranked_signals, limit=80, max_per_source=4)

    def _signal_line(s: dict[str, Any]) -> str:
        title = str(s.get("title", "")).replace("|", " ").strip()
        body = str(s.get("full_text") or s.get("snippet", "")).replace("|", " ").strip()[:500]
        url = str(s.get("source", "")).replace("|", "").strip()
        published = s.get("published_at", "unknown")
        score = s.get("_editorial_score", "")
        score_str = f" [score:{score}]" if score != "" else ""
        return f"- {published}{score_str} | {title} | {body} | {url}"

    signals_text = "\n".join(_signal_line(s) for s in prompt_signals)
    user_prompt = (
        f"Свежие сигналы за последние {lookback_days} дней ({len(signals)} шт., "
        f"показаны {len(prompt_signals)} лучших по релевантности и разнообразию источников):\n\n"
        f"{signals_text}\n\n"
        f"Выбери максимум {max_topics} лучших тем для SMTInsider. Используй только эти сигналы.\n"
        f"ВАЖНО: поле 'angle' должно объяснять конкретно — что инженер узнает из статьи, "
        f"какие цифры/факты будут использованы. Поле 'key_facts' — список конкретных фактов/цифр из источника."
    )
    data = llm_client.ask_json(SYSTEM_PROMPT, user_prompt, max_tokens=2800)
    if isinstance(data, list):
        data = {"topics": data}

    # The model may return only one conservative topic even when the requested
    # cap is higher. Fill the candidate queue deterministically from ranked
    # fresh signals; Evidence Research will later retain only source-backed
    # candidates, so this never forces weak articles into Writer.
    topics = list(data.get("topics", []) or [])
    existing_urls = {
        str(source.get("url", ""))
        for topic in topics for source in (topic.get("sources", []) or [])
        if isinstance(source, dict)
    }
    existing_titles = {str(topic.get("topic", "")).lower() for topic in topics}
    for signal in ranked_signals:
        if len(topics) >= max_topics:
            break
        url = str(signal.get("source", ""))
        title = str(signal.get("title", "")).strip()
        if not title or not url or url in existing_urls or title.lower() in existing_titles:
            continue
        topics.append({
            "topic": title,
            "angle": "Report only the documented announcement and production relevance in the verified source.",
            "format": "news",
            "editorial_type": "news",
            "category": "SMT Equipment",
            "keywords": [],
            "urgency": "MEDIUM",
            "source_count": 1,
            "source_notes": "Deterministic candidate from a fresh verified signal; Evidence Research required before writing.",
            "key_facts": [],
            "sources": [{
                "title": title,
                "url": url,
                "date": signal.get("published_at", "unknown"),
                "role": "fresh_primary",
                "excerpt": signal.get("full_text") or signal.get("snippet", ""),
            }],
        })
        existing_urls.add(url)
        existing_titles.add(title.lower())
    data["topics"] = topics

    for topic_index, topic in enumerate(data.get("topics", []) or []):
        # Normalize/repair section choice from LLM or mock output.
        section = section_router.decide_section(
            title=topic.get("topic", ""),
            body=(topic.get("angle", "") + "\n" + topic.get("source_notes", "")),
            category=topic.get("category", ""),
            tags=topic.get("keywords", []),
            source_topic_brief=topic,
            explicit=topic.get("editorial_type") or topic.get("target_section") or topic.get("format"),
        )
        topic["editorial_type"] = section.editorial_type
        topic["target_section"] = section.section_path
        topic["section_routing"] = section.to_dict()

        # First pass: see how many sources the already-collected signal pool
        # can supply for this specific topic.
        expanded = source_expander.expand_sources_for_topic(
            topic,
            ranked_signals,
            max_sources=int(os.environ.get("NEWS_TOPIC_MAX_SOURCES", "5")),
        )

        # If that leaves the topic with fewer than 2 sources, actively search
        # for this specific topic instead of accepting a single-source brief.
        # This is what makes multi-source synthesis the default outcome
        # rather than something that only happens when the generic
        # pre-collection pool happened to have overlap.
        min_sources = int(os.environ.get("NEWS_MIN_SOURCES_PER_TOPIC", "2"))
        supplementary_limit = int(os.environ.get("NEWS_SUPPLEMENTARY_MAX_TOPICS", "5"))
        if len(expanded) < min_sources and topic_index < supplementary_limit and _env_bool("NEWS_TOPIC_SUPPLEMENTARY_SEARCH", "0"):
            already_urls = {s.get("url", "") for s in expanded}
            print(f"  🔎 «{topic.get('topic','')[:60]}» имеет {len(expanded)} источник(ов) — ищу подтверждающие...")
            corroborating = find_corroborating_sources(
                topic, already_urls, lookback_days,
                max_new=min_sources - len(expanded) + 1,
            )
            if corroborating:
                print(f"     → найдено доп. источников: {len(corroborating)}")
                # Attach directly: find_corroborating_sources() already
                # validated topical relevance (title-similarity against this
                # specific topic) before returning a result, so re-running
                # expand_sources_for_topic's generic similarity gate here
                # would just risk re-filtering out a source that's already
                # confirmed relevant, using a stricter, differently-scoped
                # comparison (full topic_text vs title+snippet rather than
                # title vs title).
                seen_urls = {s.get("url", "") for s in expanded}
                max_sources = int(os.environ.get("NEWS_TOPIC_MAX_SOURCES", "5"))
                for c in corroborating:
                    if len(expanded) >= max_sources:
                        break
                    url = c.get("source", "")
                    if not url or url in seen_urls:
                        continue
                    source_expander.add_source(
                        expanded, seen_urls, c.get("title", ""), url,
                        c.get("published_at", "unknown"), "related_fresh_signal",
                        excerpt=c.get("full_text") or c.get("snippet", ""),
                    )
                ranked_signals.extend(corroborating)
            else:
                print(f"     → доп. источников не найдено, статья будет по {len(expanded)} источнику/ам")

        topic["expanded_sources"] = expanded
        topic["source_count"] = len(expanded)
        evidence_words = sum(len(str(src.get("excerpt", "")).split()) for src in expanded)
        # Do not send an LLM a title plus a short snippet and call the result
        # an article. Topics with insufficient source prose remain visible for
        # research, but Writer is explicitly blocked until evidence is added.
        topic["evidence_word_count"] = evidence_words
        event_context = " ".join([str(topic.get("topic", "")), str(topic.get("angle", ""))] + [str(src.get("excerpt", "")) for src in expanded])
        expired_event = _expired_future_event(event_context, now_local())
        if expired_event:
            topic["writer_allowed"] = False
            topic["evidence_status"] = "event_expired"
            topic["source_notes"] = (
                f"{topic.get('source_notes', '')} Scheduled event date {expired_event} has passed; "
                "requires post-event coverage before writing."
            ).strip()
        elif not expanded or evidence_words < 800:
            topic["writer_allowed"] = False
            topic["evidence_status"] = "needs_research"
            topic["source_notes"] = (
                f"{topic.get('source_notes', '')} Evidence insufficient for auto-writing "
                f"({len(expanded)} source(s), {evidence_words} source words)."
            ).strip()
        else:
            topic["writer_allowed"] = True
            topic["evidence_status"] = "ready"
        # A fresh announcement with one short source is news, not a buyer
        # guide/review. Make the routing truthful before it reaches the UI and
        # Writer instead of asking a model to stretch sparse evidence.
        if len(expanded) < 2 or evidence_words < 900:
            topic["format"] = "news"
            topic["editorial_type"] = "news"
            topic["target_section"] = "/news/"
            topic["section_routing"] = section_router.decide_section(
                title=topic.get("topic", ""), body=topic.get("angle", ""),
                category=topic.get("category", ""), tags=topic.get("keywords", []), explicit="news",
            ).to_dict()
            topic["evidence_limited"] = True
        if expanded:
            # Keep `sources` backward-compatible but richer for Writer.
            topic["sources"] = expanded
        if not topic.get("key_facts"):
            aggregated_facts = []
            for src in expanded:
                for kf in (src.get("key_facts") or []):
                    kf_str = str(kf)
                    if kf_str not in aggregated_facts:
                        aggregated_facts.append(kf_str)
            if aggregated_facts:
                topic["key_facts"] = aggregated_facts
    return data


def print_brief(b: dict, now_str: str):
    print(f"""
╔═══════════════════════════════════════════════════════════════╗
║  FRESH TOPIC BRIEF                       {now_str}  ║
╚═══════════════════════════════════════════════════════════════╝
  Тема:     {b.get('topic', '?')}
  Угол:     {b.get('angle', '?')}
  Формат:   {b.get('format', '?')}
  Раздел:   {b.get('editorial_type', '?')} → {b.get('target_section', '?')}
  Категория:{b.get('category', '?')}
  Ключи:    {', '.join(b.get('keywords', []))}
  Ист.:     {b.get('source_count', 0)}
  Срочн.:   {b.get('urgency', '?')}
  Заметка:  {b.get('source_notes', '')}
───────────────────────────────────────────────────────────────""")


def scan_topics(
    output: str,
    max_topics: int,
    do_search: bool,
    max_results_per_query: int,
    lookback_days: int,
    strict_fresh: bool,
    verify_pages: bool,
    collect_only: bool,
    allow_llm_fallback: bool,
):
    now = now_local()
    seed_queries = configured_seed_queries()
    print(f"\n🎯 Agent #1 — Fresh News Trend Hunter")
    print(f"   Сканирую {len(seed_queries)} запросов "
          f"({'реальный поиск DuckDuckGo' if do_search else 'без поиска, только LLM/mock'})...\n")

    signals = gather_signals(
        seed_queries,
        max_results_per_query=max_results_per_query,
        do_search=do_search,
        lookback_days=lookback_days,
        strict_fresh=strict_fresh,
        verify_pages=verify_pages,
    )
    print(f"\n📡 Свежих сигналов собрано: {len(signals)}")
    signals, duplicate_signals = filter_existing_duplicates(signals)
    if duplicate_signals:
        print(f"♻️  Уже покрыто на сайте, исключено сигналов: {len(duplicate_signals)}")
    print(f"📡 Свежих новых сигналов после dedupe: {len(signals)}")

    if collect_only:
        payload = {
            "generated_at": now.isoformat(),
            "lookback_days": lookback_days,
            "strict_fresh": strict_fresh,
            "verify_pages": verify_pages,
            "signal_count": len(signals),
            "duplicate_signal_count": len(duplicate_signals),
            "signals": signals,
            "duplicate_signals": duplicate_signals,
        }
        Path(output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"💾 Fresh signals сохранены: {output}")
        return

    if not signals and do_search and not allow_llm_fallback:
        print("\n❌ Свежих сигналов за заданный период не найдено. Тему не генерирую, чтобы не выдумывать новости.")
        print("   Попробуй: увеличить --days, выключить --strict-fresh через --keep-undated, или проверить сеть/DDG.")
        sys.exit(1)

    if not signals and (not do_search or allow_llm_fallback):
        print("\n⚠ Свежих сигналов нет; включён fallback. Это допустимо только для тестов/mock.")
        signals = [{
            "title": "Fallback test signal (no real news)",
            "snippet": "Used only for local/mock testing.",
            "source": "",
            "published_at": "unknown",
        }]

    print(f"🧠 Передаю свежие сигналы в LLM ({llm_client.LLM_MODEL}) для отбора тем...")
    try:
        data = build_briefs(signals, max_topics, lookback_days)
    except llm_client.LLMError as e:
        print(f"\n❌ {e}")
        sys.exit(1)

    topics = data.get("topics", [])[:max_topics]
    if not topics:
        print("\n⚠ LLM не выделила тем из свежих сигналов.")
        sys.exit(1)

    now_str = now.strftime("%d.%m.%Y")
    for b in topics:
        print_brief(b, now_str)

    payload = {
        "generated_at": now.isoformat(),
        "model": llm_client.LLM_MODEL,
        "lookback_days": lookback_days,
        "strict_fresh": strict_fresh,
        "verify_pages": verify_pages,
        "signal_count": len(signals),
        "duplicate_signal_count": len(duplicate_signals),
        "signals": signals,
        "duplicate_signals": duplicate_signals,
        "topics": topics,
    }
    Path(output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n✅ Найдено свежих тем: {len(topics)}")
    print(f"💾 Сохранено: {output}")
    print(f"   → python3 agents/agent-02-writer.py --brief {output}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "pdf":
        import importlib.util
        _scout_path = os.path.join(os.path.dirname(__file__), "agent-01b-pdf-scout.py")
        _spec = importlib.util.spec_from_file_location("agent01b_pdf_scout", _scout_path)
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        _mod.main()
        sys.exit(0)

    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["scan", "pdf"])
    p.add_argument("--output", default="/tmp/smtinsider_briefs.json")
    p.add_argument("--max-topics", type=int, default=5,
                    help="макс. тем за один scan (default: 5, было 3 — поднято вместе с расширением реестра источников)")
    p.add_argument("--max-results", type=int, default=int(os.environ.get("NEWS_MAX_RESULTS", "5")),
                   help="результатов поиска на запрос")
    p.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS,
                   help="искать новости только за последние N дней (default/env NEWS_LOOKBACK_DAYS=30)")
    p.add_argument("--strict-fresh", dest="strict_fresh", action="store_true",
                   default=_env_bool("NEWS_STRICT_FRESH", "1"),
                   help="отбрасывать результаты без подтверждённой даты")
    p.add_argument("--keep-undated", dest="strict_fresh", action="store_false",
                   help="оставлять результаты без даты, если DDG уже был ограничен по времени")
    p.add_argument("--verify-pages", dest="verify_pages", action="store_true",
                   default=_env_bool("NEWS_VERIFY_DATES", "1"),
                   help="заходить на страницы и проверять meta/json-ld дату публикации")
    p.add_argument("--no-verify-pages", dest="verify_pages", action="store_false")
    p.add_argument("--collect-only", action="store_true",
                   help="только собрать свежие сигналы и сохранить JSON, без LLM")
    p.add_argument("--allow-llm-fallback", action="store_true",
                   help="разрешить fallback без свежих сигналов (только для тестов)")
    p.add_argument("--no-search", action="store_true", help="не ходить в поиск, только LLM/mock")
    args = p.parse_args()

    if args.action == "scan":
        scan_topics(
            output=args.output,
            max_topics=args.max_topics,
            do_search=not args.no_search,
            max_results_per_query=args.max_results,
            lookback_days=args.days,
            strict_fresh=args.strict_fresh,
            verify_pages=args.verify_pages,
            collect_only=args.collect_only,
            allow_llm_fallback=args.allow_llm_fallback,
        )
