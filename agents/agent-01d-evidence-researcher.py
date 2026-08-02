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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(__file__))
import source_expander


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
        full_text = source_expander.fetch_readable_text(canonical)
        if full_text:
            source["excerpt"] = full_text
            source["evidence_type"] = "retrieved_page"
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
                linked_text = source_expander.fetch_readable_text(linked_url)
                if len(linked_text.split()) < 120:
                    continue
                researched.append({
                    "title": link.get("title", linked_url),
                    "url": linked_url,
                    "date": link.get("date", "unknown"),
                    "role": "research_context",
                    "excerpt": linked_text,
                    "evidence_type": "retrieved_page",
                    "authoritative": _is_authoritative(link),
                    "claim_candidates": _claim_candidates(linked_text),
                })
                if len(researched) >= 4:
                    break

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

    if len(authoritative) >= 2 and evidence_words >= 1000 and claim_count >= 6:
        route = "review"
        status = "ready_review"
        allowed = True
    elif authoritative and evidence_words >= 250 and claim_count >= 3:
        route = "news"
        status = "ready_news"
        allowed = True
    elif authoritative and evidence_words >= 700 and claim_count >= 4:
        route = "insight"
        status = "ready_insight"
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
