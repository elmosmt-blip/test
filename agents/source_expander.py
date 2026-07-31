#!/usr/bin/env python3
"""
source_expander.py — расширение источников для выбранной свежей темы.

Правильный порядок для новостного агента:
  1) найти свежую тему;
  2) собрать несколько источников по этой теме;
  3) только потом писать статью.

Этот модуль работает без внешних API:
  - использует уже собранный корпус fresh signals;
  - ищет похожие сигналы по токенам;
  - открывает исходную страницу и вытаскивает релевантные product/vendor links;
  - добавляет vendor/product pages как contextual sources.

Важно: не все дополнительные источники обязаны быть свежими новостями. Fresh topic
должна быть свежей; product/vendor pages могут быть context/reference sources.
"""

from __future__ import annotations

import sys
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

import json
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Optional

import requests

STOPWORDS = {
    "the", "and", "with", "for", "from", "into", "that", "this", "what", "when",
    "where", "which", "while", "series", "system", "systems", "launch", "launches",
    "new", "high", "throughput", "inspection", "electronics", "manufacturing",
    "supports", "upgrade", "technology", "solutions", "group", "inc", "ltd", "gmbh",
}

IMPORTANT_HOSTS = [
    "tri.com.tw", "kohyoung", "altusgroup", "wnie", "iconnect007",
    "directindustry", "electronicspecifier", "smtnet", "circuitsassembly",
    "europlacer", "fuji", "saki", "vitrox", "viscom", "yamaha", "juki",
]


def normalize_title(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9\-]{2,}", (text or "").lower())
    return {w.strip("-") for w in words if w not in STOPWORDS}


def token_score(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    return len(inter) / max(1, min(len(ta), len(tb)))


def canonical_url(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlparse(url.strip())
        # Remove fragments and common tracking params.
        qs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
        qs = [(k, v) for k, v in qs if not k.lower().startswith("utm_")]
        query = urllib.parse.urlencode(qs)
        return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", query, ""))
    except Exception:
        return url.strip()


def page_title_and_date(url: str) -> tuple[str, str]:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SMTInsiderBot/1.0)"}
    try:
        resp = requests.get(url, headers=headers, timeout=12, allow_redirects=True)
        resp.raise_for_status()
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        title = ""
        h1 = soup.find("h1")
        if h1:
            title = normalize_title(h1.get_text(" ", strip=True))
        if not title:
            og = soup.find("meta", attrs={"property": "og:title"}) or soup.find("meta", attrs={"name": "title"})
            if og and og.get("content"):
                title = normalize_title(og["content"])
        if not title and soup.title:
            title = normalize_title(soup.title.get_text(" ", strip=True))

        date = "unknown"
        for key in ["article:published_time", "datePublished", "dateModified", "pubdate", "DC.date"]:
            tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
            if tag and tag.get("content"):
                date = tag["content"][:10]
                break
        if date == "unknown":
            time_tag = soup.find("time", attrs={"datetime": True})
            if time_tag:
                date = time_tag.get("datetime", "unknown")[:10]
        return title, date
    except Exception:
        return "", "unknown"


def extract_candidate_links(source_url: str, topic_text: str, limit: int = 20) -> list[dict[str, Any]]:
    """Extract likely product/vendor/context links from a source page."""
    if not source_url:
        return []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SMTInsiderBot/1.0)"}
    try:
        resp = requests.get(source_url, headers=headers, timeout=12, allow_redirects=True)
        resp.raise_for_status()
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    page_host = urllib.parse.urlparse(resp.url).netloc.replace("www.", "")
    topic_tokens = tokens(topic_text)

    for a in soup.find_all("a", href=True):
        href = urllib.parse.urljoin(resp.url, a.get("href", ""))
        href = canonical_url(href)
        if not href or href in seen:
            continue
        parsed = urllib.parse.urlparse(href)
        if parsed.scheme not in {"http", "https"}:
            continue
        host = parsed.netloc.replace("www.", "")
        if host in {"twitter.com", "x.com", "linkedin.com", "facebook.com", "mailto"}:
            continue
        if not host or host == page_host:
            # Same-site related pages are useful only if title/path is strongly relevant.
            pass
        link_text = normalize_title(a.get_text(" ", strip=True))
        combined = f"{link_text} {href}"
        path = parsed.path.lower()
        same_host = host == page_host
        host_hit = any(h in host.lower() or h in href.lower() for h in IMPORTANT_HOSTS)
        term_hit = len(tokens(combined) & topic_tokens) >= 1
        product_hit = any(k in path for k in ["product", "solution", "aoi", "spi", "axi", "x-ray", "xray", "zenith", "tr7600"])
        nav_hit = any(k in path for k in ["/category/", "/tag/", "/subscribe", "/contact", "/privacy", "/terms", "/go/", "/share", "/intent/"])
        if nav_hit:
            continue
        if same_host:
            # Same-site related links are usually navigation/related posts. Keep
            # only very strong exact-topic matches.
            if token_score(topic_text, combined) < 0.55:
                continue
        else:
            if not (host_hit or product_hit or term_hit):
                continue
            if not (term_hit or product_hit):
                continue
        seen.add(href)
        title, date = page_title_and_date(href)
        out.append({
            "title": title or link_text or href,
            "url": href,
            "date": date,
            "role": "context_link",
        })
        if len(out) >= limit:
            break
    return out


def add_source(acc: list[dict[str, Any]], seen: set[str], title: str, url: str,
               date: str = "unknown", role: str = "source", excerpt: str = "",
               key_facts: list[Any] | None = None, technical_specs: list[Any] | None = None) -> None:
    cu = canonical_url(url)
    if not cu or cu in seen:
        return
    seen.add(cu)
    item = {
        "title": normalize_title(title) or cu,
        "url": cu,
        "date": date or "unknown",
        "role": role,
        "excerpt": (excerpt or "").strip()[:600],
    }
    if key_facts:
        item["key_facts"] = key_facts
    if technical_specs:
        item["technical_specs"] = technical_specs
    acc.append(item)


def expand_sources_for_topic(topic: dict[str, Any], signals: list[dict[str, Any]], max_sources: int = 5) -> list[dict[str, Any]]:
    topic_text = " ".join([
        str(topic.get("topic", "")),
        str(topic.get("angle", "")),
        str(topic.get("source_notes", "")),
    ])
    expanded: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Index collected signals by canonical URL so we can attach real excerpt
    # content (snippet or, for top-ranked signals, full article text) to any
    # source the LLM or the similarity search picked — instead of shipping
    # the Writer a bare title+URL with nothing to actually synthesize from.
    signal_by_url: dict[str, dict[str, Any]] = {}
    for s in signals or []:
        u = canonical_url(s.get("source", ""))
        if u:
            signal_by_url[u] = s

    def _excerpt_for(url: str, fallback: str = "") -> str:
        s = signal_by_url.get(canonical_url(url))
        if s:
            return s.get("full_text") or s.get("snippet") or fallback
        return fallback

    # 1) Original LLM/source list.
    for src in topic.get("sources", []) or []:
        add_source(
            expanded, seen, src.get("title", ""), src.get("url", ""), src.get("date", "unknown"),
            "fresh_primary", excerpt=_excerpt_for(src.get("url", ""), src.get("excerpt", "")),
        )

    # 2) Similar fresh signals already collected — these are what make a topic
    # genuinely multi-source instead of a single-press-release rewrite. Prefer
    # signals that corroborate the topic from a *different* domain than the
    # primary source(s) already added, so the Writer gets independent
    # confirmation/detail rather than three copies of the same wire story.
    scored = []
    for s in signals or []:
        title = s.get("title", "")
        url = s.get("source", "")
        if not title or not url:
            continue
        score = token_score(topic_text, title + " " + s.get("snippet", ""))
        if (not expanded and score >= 0.28) or (0.28 <= score < 0.97):  # allow exact match if no primary source yet
            scored.append((score, s))

    primary_domains = {urllib.parse.urlparse(x.get("url", "")).netloc for x in expanded}
    scored.sort(key=lambda x: x[0], reverse=True)
    # First pass: prioritize different domains for genuine cross-source corroboration.
    for score, s in scored:
        domain = urllib.parse.urlparse(s.get("source", "")).netloc
        if domain in primary_domains:
            continue
        add_source(
            expanded, seen, s.get("title", ""), s.get("source", ""), s.get("published_at", "unknown"),
            "related_fresh_signal", excerpt=s.get("full_text") or s.get("snippet", ""),
            key_facts=s.get("key_facts"), technical_specs=s.get("technical_specs"),
        )
        primary_domains.add(domain)
        if len(expanded) >= max_sources:
            return expanded
    # Second pass: fill remaining slots even from an already-seen domain.
    for score, s in scored:
        add_source(
            expanded, seen, s.get("title", ""), s.get("source", ""), s.get("published_at", "unknown"),
            "related_fresh_signal", excerpt=s.get("full_text") or s.get("snippet", ""),
            key_facts=s.get("key_facts"), technical_specs=s.get("technical_specs"),
        )
        if len(expanded) >= max_sources:
            return expanded

    # 3) Product/vendor links from primary source pages.
    primary_urls = [x.get("url", "") for x in expanded[:2]]
    for url in primary_urls:
        for link in extract_candidate_links(url, topic_text, limit=10):
            add_source(
                expanded, seen, link.get("title", ""), link.get("url", ""), link.get("date", "unknown"),
                link.get("role", "context_link"), excerpt=link.get("excerpt", ""),
            )
            if len(expanded) >= max_sources:
                return expanded

    return expanded


if __name__ == "__main__":
    # Simple CLI for debugging: python agents/source_expander.py briefs.json
    import sys
    from pathlib import Path
    if len(sys.argv) < 2:
        print("usage: source_expander.py briefs.json")
        raise SystemExit(2)
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    signals = data.get("signals", [])
    for topic in data.get("topics", []):
        print("\n#", topic.get("topic"))
        for src in expand_sources_for_topic(topic, signals):
            print("-", src)
