#!/usr/bin/env python3
"""Agent #1c — LinkedIn Signals Scout (public discovery mode).

This agent does not log in to, scrape, or automate LinkedIn. It discovers
public LinkedIn post URLs through search-engine result pages, classifies their
trust level, and requires an official corroborating URL before a signal may be
used by Writer.

Usage:
  python agents/agent-01c-linkedin-signals.py scan --brief /tmp/briefs.json
  python agents/agent-01c-linkedin-signals.py preview --topic "IPC-A-630A"
  python agents/agent-01c-linkedin-signals.py submit --url https://www.linkedin.com/posts/... --company "IPC"
"""
from __future__ import annotations

import sys
# Windows console can default to cp1251; logs contain UTF-8 arrows/symbols.
for _name in ("stdout", "stderr"):
    _stream = getattr(sys, _name, None)
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import argparse
import json
import os
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "cache" / "linkedin_signals.json"

OFFICIAL_DOMAIN_HINTS = {
    "fuji": "fuji.co.jp", "koh young": "kohyoung.com", "asmpt": "asmpt.com",
    "yamaha": "yamaha-motor.com", "saki": "sakicorp.com", "tri": "tri.com.tw",
    "vitrox": "vitrox.com", "mirtec": "mirtec.com", "mycronic": "mycronic.com",
    "nordson": "nordson.com", "ipc": "ipc.org", "smta": "smta.org",
    "dymax": "dymax.com", "aegis": "aiscorp.com", "europlacer": "europlacer.com",
}


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def topic_queries(topic: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9+&.-]*", topic)[:12]
    if not words:
        return []
    phrase = " ".join(words)
    return [
        f"site:linkedin.com/posts {phrase}",
        f"site:linkedin.com/feed/update {phrase}",
    ]


def ddg_public_search(query: str, limit: int = 8, linkedin_only: bool = True) -> list[dict[str, str]]:
    """Search public result pages; never request a LinkedIn page itself."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SMTInsiderBot/1.0)"}
    endpoints = ("https://html.duckduckgo.com/html/", "https://lite.duckduckgo.com/lite/")
    for endpoint in endpoints:
        try:
            response = requests.get(endpoint, params={"q": query}, headers=headers, timeout=12)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            anchors = soup.select("a.result__a, a.result-link")
            results: list[dict[str, str]] = []
            seen: set[str] = set()
            for anchor in anchors:
                href = anchor.get("href", "")
                parsed = urllib.parse.urlparse(href)
                params = urllib.parse.parse_qs(parsed.query)
                if params.get("uddg"):
                    href = params["uddg"][0]
                if href.startswith("//"):
                    href = "https:" + href
                if linkedin_only and "linkedin.com" not in urllib.parse.urlparse(href).netloc.lower():
                    continue
                if href in seen:
                    continue
                seen.add(href)
                parent = anchor.find_parent(class_=re.compile("result")) or anchor.parent
                snippet = normalize(parent.get_text(" ", strip=True) if parent else "")
                results.append({"url": href, "title": normalize(anchor.get_text(" ", strip=True)), "snippet": snippet[:700]})
                if len(results) >= limit:
                    break
            return results
        except requests.RequestException:
            continue
    return []


def classify_signal(result: dict[str, str], topic: str, company: str = "") -> dict[str, Any]:
    combined = f"{result.get('title', '')} {result.get('snippet', '')} {company}".lower()
    detected_company = company
    official_domain = ""
    for vendor, domain in OFFICIAL_DOMAIN_HINTS.items():
        if vendor in combined:
            detected_company = detected_company or vendor.title()
            official_domain = domain
            break
    return {
        "linkedin_url": result["url"],
        "linkedin_title": result.get("title", ""),
        "linkedin_excerpt": result.get("snippet", ""),
        "matched_topic": topic,
        "company": detected_company,
        "trust_level": "named_company" if detected_company else "unknown_author",
        "official_domain_hint": official_domain,
        "status": "needs_corroboration",
        "writer_allowed": False,
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


def official_corroboration_query(signal: dict[str, Any]) -> str:
    domain = signal.get("official_domain_hint", "")
    title_words = re.findall(r"[A-Za-z0-9][A-Za-z0-9+&.-]*", signal.get("linkedin_title", ""))[:10]
    if domain and title_words:
        return f"site:{domain} {' '.join(title_words)}"
    return ""


def find_official_corroboration(signal: dict[str, Any]) -> dict[str, str] | None:
    query = official_corroboration_query(signal)
    domain = signal.get("official_domain_hint", "")
    if not query or not domain:
        return None
    for result in ddg_public_search(query, limit=5, linkedin_only=False):
        host = urllib.parse.urlparse(result["url"]).netloc.lower().replace("www.", "")
        if host.endswith(domain):
            return result
    return None


def discover(topic: str, company: str = "") -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in topic_queries(topic):
        for result in ddg_public_search(query):
            if result["url"] in seen:
                continue
            seen.add(result["url"])
            signal = classify_signal(result, topic, company)
            official = find_official_corroboration(signal)
            if official:
                signal["official_source"] = official
                signal["status"] = "corroborated"
                signal["writer_allowed"] = True
            signals.append(signal)
    return signals


def save_signals(signals: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "signals": signals}, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["scan", "preview", "submit"])
    parser.add_argument("--brief", default="", help="briefs.json from Agent #1")
    parser.add_argument("--topic", default="")
    parser.add_argument("--company", default="")
    parser.add_argument("--url", default="", help="manual public LinkedIn post URL")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    output = Path(args.output)

    if args.action == "submit":
        if "linkedin.com" not in urllib.parse.urlparse(args.url).netloc.lower():
            print("❌ Укажите публичный linkedin.com URL")
            return 2
        signal = classify_signal({"url": args.url, "title": "Manual LinkedIn intake", "snippet": ""}, args.topic, args.company)
        signal["status"] = "manual_review"
        save_signals([signal], output)
        print(f"✅ LinkedIn signal сохранён для review: {output}")
        return 0

    topics: list[dict[str, Any]] = []
    if args.brief:
        try:
            topics = json.loads(Path(args.brief).read_text(encoding="utf-8")).get("topics", [])
        except Exception as exc:
            print(f"❌ Не удалось прочитать brief: {exc}")
            return 2
    elif args.topic:
        topics = [{"topic": args.topic, "company": args.company}]
    else:
        print("❌ Укажите --brief или --topic")
        return 2

    all_signals: list[dict[str, Any]] = []
    for item in topics[:20]:
        topic = str(item.get("topic", ""))
        if not topic:
            continue
        signals = discover(topic, str(item.get("company", "")))
        print(f"→ {topic[:70]}: LinkedIn signals {len(signals)}")
        all_signals.extend(signals)
    save_signals(all_signals, output)
    print(f"\n✅ Сохранено LinkedIn signals: {len(all_signals)} → {output}")
    print("ℹ Все signals требуют official corroboration или ручного review; Writer не запускается.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
