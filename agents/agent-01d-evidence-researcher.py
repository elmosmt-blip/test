#!/usr/bin/env python3
"""Agent #1d — automated evidence research and editorial routing.

Consumes candidate briefs, retrieves readable source pages, gathers relevant
same-source/official links, and routes each candidate to news, review, insight,
or discard. It never writes prose and never asks an operator to find sources.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import source_expander
from src.collectors import pdf_collector

ROOT = Path(__file__).resolve().parent.parent
LINKEDIN_SIGNALS_FILE = ROOT / "cache" / "linkedin_signals.json"


def _linkedin_official_urls(topic_text: str) -> list[str]:
    """Use only corroborated LinkedIn discoveries, never post URLs themselves."""
    try:
        signals = json.loads(LINKEDIN_SIGNALS_FILE.read_text(encoding="utf-8")).get("signals", [])
    except Exception:
        return []
    tokens = set(re.findall(r"[a-z0-9]{3,}", topic_text.lower()))
    urls: list[str] = []
    for signal in signals:
        if not signal.get("writer_allowed"):
            continue
        if not (tokens & set(re.findall(r"[a-z0-9]{3,}", str(signal.get("matched_topic", "")).lower()))):
            continue
        official = signal.get("official_source") or {}
        url = str(official.get("url", ""))
        if url and url not in urls:
            urls.append(url)
    return urls


OFFICIAL_DOMAINS = {
    "fuji": "fuji.co.jp", "koh young": "kohyoung.com", "asmpt": "asmpt.com",
    "yamaha": "yamaha-motor.com", "saki": "sakicorp.com", "tri": "tri.com.tw",
    "vitrox": "vitrox.com", "mirtec": "mirtec.com", "mycronic": "mycronic.com",
    "nordson": "nordson.com", "dymax": "dymax.com", "kurtz ersa": "ersa.com",
    "heller": "hellerindustries.com", "rehm": "rehm-group.com", "ipc": "ipc.org",
}


def _official_domains(topic_text: str) -> list[str]:
    low = topic_text.lower()
    return [domain for name, domain in OFFICIAL_DOMAINS.items() if name in low]


def _search_official_pages(topic_text: str, domains: list[str], limit: int = 3, suffix: str = "") -> list[str]:
    """Find official pages through public search, without LLM-generated URLs."""
    if not domains:
        return []
    title_terms = " ".join(re.findall(r"[A-Za-z0-9][A-Za-z0-9+&.-]*", topic_text)[:12])
    if suffix:
        title_terms = f"{title_terms} {suffix}"
    urls: list[str] = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SMTInsiderBot/1.0)"}
    for domain in domains:
        try:
            response = requests.get("https://html.duckduckgo.com/html/", params={"q": f"site:{domain} {title_terms}"}, headers=headers, timeout=12)
            response.raise_for_status()
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.text, "html.parser")
            for anchor in soup.select("a.result__a"):
                href = anchor.get("href", "")
                parsed = urllib.parse.urlparse(href)
                redirect = urllib.parse.parse_qs(parsed.query).get("uddg", [])
                url = redirect[0] if redirect else href
                host = urllib.parse.urlparse(url).netloc.lower().replace("www.", "")
                if host.endswith(domain) and url not in urls:
                    urls.append(url)
                if len(urls) >= limit:
                    return urls
        except Exception:
            continue
    return urls


def _is_post_event_coverage(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in ("event recap", "event results", "concluded", "highlights from", "showcased at", "demonstrated at"))


def _evidence_text(url: str) -> tuple[str, str, list[dict[str, Any]]]:
    """Retrieve page prose or parse an official PDF/TDS into source evidence."""
    path = urllib.parse.urlparse(url).path.lower()
    if path.endswith(".pdf"):
        document = pdf_collector.fetch_and_parse_pdf(url, timeout=20)
        if document and document.text:
            return document.text[:12000], "official_pdf", document.key_facts
        return "", "official_pdf", []
    return source_expander.fetch_readable_text(url), "retrieved_page", []


def _sentences(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", sentence).strip() for sentence in re.split(r"(?<=[.!?])\s+", text or "") if len(sentence.split()) >= 8]


def _claim_candidates(text: str, limit: int = 8) -> list[str]:
    # Evidence ledger candidates are literal source sentences, never LLM facts.
    sentences = _sentences(text)
    scored = [
        sentence for sentence in sentences
        if re.search(r"\d|\b(released|launch|introduced|will|supports|equipped|provides|announces)\b", sentence, re.I)
    ]
    return (scored or sentences)[:limit]


def _is_authoritative(source: dict[str, Any]) -> bool:
    role = str(source.get("role", ""))
    url = str(source.get("url", "")).lower()
    if role in {"fresh_primary", "official_primary", "primary_pdf_source"}:
        return True
    return not any(host in url for host in ("news.google.com", "linkedin.com", "facebook.com", "twitter.com", "youtube.com"))


def research_topic(topic: dict[str, Any]) -> dict[str, Any]:
    topic = dict(topic)
    sources = [dict(source) for source in (topic.get("expanded_sources") or topic.get("sources") or [])]
    researched: list[dict[str, Any]] = []
    seen: set[str] = set()
    topic_text = f"{topic.get('topic', '')} {topic.get('angle', '')}"

    for source in sources[:4]:
        url = source.get("url", "")
        canonical = source_expander.canonical_url(url)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        full_text, evidence_type, technical_specs = _evidence_text(canonical)
        if full_text:
            source["excerpt"] = full_text
            source["evidence_type"] = evidence_type
        if technical_specs:
            source["technical_specs"] = technical_specs
        source["authoritative"] = _is_authoritative(source)
        source["claim_candidates"] = _claim_candidates(source.get("excerpt", ""))
        researched.append(source)

        # Automatically inspect relevant official/context links on the primary
        # page. The link extractor filters social/navigation URLs.
        if len(researched) < 4:
            for link in source_expander.extract_candidate_links(canonical, topic_text, limit=5):
                linked_url = source_expander.canonical_url(link.get("url", ""))
                if not linked_url or linked_url in seen:
                    continue
                seen.add(linked_url)
                linked_text, evidence_type, technical_specs = _evidence_text(linked_url)
                if len(linked_text.split()) < 120:
                    continue
                researched.append({
                    "title": link.get("title", linked_url),
                    "url": linked_url,
                    "date": link.get("date", "unknown"),
                    "role": "research_context",
                    "excerpt": linked_text,
                    "evidence_type": evidence_type,
                    "technical_specs": technical_specs,
                    "authoritative": _is_authoritative(link),
                    "claim_candidates": _claim_candidates(linked_text),
                })
                if len(researched) >= 4:
                    break

    # If primary coverage is a trade-media release, automatically search the
    # matching vendor's official domain for a product page, TDS or newsroom
    # entry. This is a research retry, not a manual task for the operator.
    domains = _official_domains(topic_text)
    research_urls = [
        *_search_official_pages(topic_text, domains),
        *_linkedin_official_urls(topic_text),
    ]
    if topic.get("evidence_status") == "event_expired":
        # A stale pre-event announcement can only be revived by an explicit
        # post-event recap/result, never by rediscovering the same announcement.
        research_urls.extend(_search_official_pages(topic_text, domains, suffix="recap results highlights"))
    post_event_found = False
    for official_url in research_urls:
        canonical = source_expander.canonical_url(official_url)
        if not canonical or canonical in seen or len(researched) >= 4:
            continue
        seen.add(canonical)
        official_text, evidence_type, technical_specs = _evidence_text(canonical)
        if len(official_text.split()) < 120:
            continue
        if _is_post_event_coverage(official_text):
            post_event_found = True
        researched.append({
            "title": canonical,
            "url": canonical,
            "date": "unknown",
            "role": "official_research",
            "excerpt": official_text,
            "evidence_type": f"official_search_{evidence_type}",
            "technical_specs": technical_specs,
            "authoritative": True,
            "claim_candidates": _claim_candidates(official_text),
        })

    evidence_words = sum(len(str(source.get("excerpt", "")).split()) for source in researched)
    authoritative = [source for source in researched if source.get("authoritative") and len(str(source.get("excerpt", "")).split()) >= 120]
    claim_count = sum(len(source.get("claim_candidates", [])) for source in authoritative)

    topic["expanded_sources"] = researched
    topic["sources"] = researched
    topic["source_count"] = len(researched)
    topic["evidence_word_count"] = evidence_words
    topic["evidence_ledger"] = [
        {"source_url": source.get("url", ""), "claims": source.get("claim_candidates", [])}
        for source in authoritative
    ]
    # Replace LLM-generated angles/key facts from Agent #1 with literal source
    # evidence. Otherwise a speculative selection angle can leak unsupported
    # torque, timeline or comparison claims into Writer even after research.
    literal_claims = [claim for item in topic["evidence_ledger"] for claim in item["claims"]]
    topic["key_facts"] = literal_claims[:12]
    topic["angle"] = "Write a source-bounded article using only the evidence ledger; do not add undocumented technical details."
    topic["source_notes"] = f"Evidence Research retrieved {len(authoritative)} authoritative source(s) and {evidence_words} source words."

    # A pre-event announcement remains stale even if it has plenty of prose.
    # It may only return through a new post-event signal/coverage item.
    if topic.get("evidence_status") == "event_expired" and not post_event_found:
        route = ""
        status = "awaiting_post_event_evidence"
        allowed = False
    elif len(authoritative) >= 2 and evidence_words >= 1000 and claim_count >= 6:
        route = "review"
        status = "ready_review"
        allowed = True
    elif authoritative and evidence_words >= 700 and claim_count >= 4:
        # A single long, source-authored engineering article can support an
        # insight. It is not a comparative review, but it deserves more than
        # a 250-word press-release summary.
        route = "insight"
        status = "ready_insight"
        allowed = True
    elif authoritative and evidence_words >= 250 and claim_count >= 3:
        route = "news"
        status = "ready_news"
        allowed = True
    else:
        route = ""
        status = "discarded_insufficient_evidence"
        allowed = False

    topic["writer_allowed"] = allowed
    topic["evidence_status"] = status
    topic["research_decision"] = {
        "route": route or "discard",
        "authoritative_sources": len(authoritative),
        "evidence_words": evidence_words,
        "claim_candidates": claim_count,
        "researched_at": datetime.now(timezone.utc).isoformat(),
    }
    if allowed:
        topic["format"] = route
        topic["editorial_type"] = route
        topic["target_section"] = {"news": "/news/", "review": "/reviews/", "insight": "/insights/"}[route]
    return topic


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--brief", required=True)
    parser.add_argument("--output", default="")
    parser.add_argument("--keep-discarded", action="store_true")
    args = parser.parse_args()
    brief_path = Path(args.brief)
    data = json.loads(brief_path.read_text(encoding="utf-8"))
    researched = [research_topic(topic) for topic in data.get("topics", [])]
    if not args.keep_discarded:
        researched = [topic for topic in researched if topic.get("writer_allowed")]
    data["topics"] = researched
    data["evidence_researched_at"] = datetime.now(timezone.utc).isoformat()
    output = Path(args.output or args.brief)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    ready = sum(1 for topic in researched if topic.get("writer_allowed"))
    print(f"🔬 Evidence Research: ready={ready}, retained={len(researched)} → {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
