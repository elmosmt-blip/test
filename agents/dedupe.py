#!/usr/bin/env python3
"""
dedupe.py — duplicate prevention for SMTInsider agents.

Problem this solves:
  Agent #1 can find a fresh news signal that already became an SMTInsider
  article/review. The system must not repeatedly rewrite/publish the same topic.

Checks:
  - existing slug;
  - existing title / normalized title similarity;
  - source URL in link/source_url/frontmatter_json;
  - source URLs stored in source_topic_brief / expanded_sources / sources.

Used by:
  - Agent #1: filter fresh signals before topic selection;
  - Agent #6: refuse duplicate draft creation unless explicitly allowed.
"""

from __future__ import annotations

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


import json
import os
import re
import urllib.parse
from dataclasses import dataclass, asdict
from typing import Any, Optional


def slugify(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text)
    return text.strip("-")[:200]


def normalize_title(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def canonical_url(url: str) -> str:
    if not url:
        return ""
    try:
        p = urllib.parse.urlparse(url.strip())
        host = p.netloc.lower().replace("www.", "")
        path = re.sub(r"/+$", "", p.path or "")
        qs = urllib.parse.parse_qsl(p.query, keep_blank_values=False)
        qs = [(k, v) for k, v in qs if not k.lower().startswith("utm_")]
        query = urllib.parse.urlencode(qs)
        return urllib.parse.urlunparse((p.scheme.lower() or "https", host, path, "", query, ""))
    except Exception:
        return url.strip().lower()


STOPWORDS = {
    "the", "and", "for", "with", "what", "how", "why", "new", "review",
    "launch", "launches", "series", "system", "systems", "smt", "pcb", "pcba",
    "inspection", "manufacturing", "electronics", "higher", "throughput",
}


def title_tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9\-]{2,}", normalize_title(text)) if w not in STOPWORDS}


def title_similarity(a: str, b: str) -> float:
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(1, min(len(ta), len(tb)))


@dataclass
class DuplicateResult:
    is_duplicate: bool
    reason: str = ""
    matched_id: Optional[int] = None
    matched_title: str = ""
    matched_slug: str = ""
    matched_url: str = ""
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExistingIndex:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.slugs: dict[str, dict[str, Any]] = {}
        self.urls: dict[str, dict[str, Any]] = {}

    def add_row(self, row: dict[str, Any]) -> None:
        self.rows.append(row)
        if row.get("slug"):
            self.slugs[row["slug"]] = row
        for url in row.get("urls", []):
            cu = canonical_url(url)
            if cu:
                self.urls[cu] = row


def _collect_urls_from_json(value: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            lk = str(k).lower()
            if lk in {"url", "source_url", "link"} and isinstance(v, str):
                urls.append(v)
            else:
                urls.extend(_collect_urls_from_json(v))
    elif isinstance(value, list):
        for item in value:
            urls.extend(_collect_urls_from_json(item))
    return urls


def load_existing_index(db_url: Optional[str] = None, limit: int = 2000) -> ExistingIndex:
    idx = ExistingIndex()
    db_url = db_url or os.environ.get("NEON_DATABASE_URL")
    if not db_url:
        return idx
    try:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(db_url)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, title, slug, link, source_url, frontmatter_json, is_published, editorial_type, category_name
                    FROM news
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                for r in cur.fetchall():
                    row = dict(r)
                    urls = []
                    for k in ("link", "source_url"):
                        if row.get(k):
                            urls.append(row[k])
                    fm = row.get("frontmatter_json")
                    if fm:
                        try:
                            urls.extend(_collect_urls_from_json(json.loads(fm)))
                        except Exception:
                            pass
                    row["urls"] = [u for u in urls if u]
                    row["title_norm"] = normalize_title(row.get("title", ""))
                    idx.add_row(row)
        finally:
            conn.close()
    except Exception:
        # Dedupe should never break collection if DB is temporarily unavailable.
        return idx
    return idx


def find_duplicate(index: ExistingIndex, title: str = "", slug: str = "", urls: Optional[list[str]] = None,
                   similarity_threshold: float = 0.72) -> DuplicateResult:
    urls = urls or []
    slug = slug or slugify(title)
    if slug and slug in index.slugs:
        r = index.slugs[slug]
        return DuplicateResult(True, "same_slug", r.get("id"), r.get("title", ""), r.get("slug", ""), "", 1.0)

    for url in urls:
        cu = canonical_url(url)
        if cu and cu in index.urls:
            r = index.urls[cu]
            return DuplicateResult(True, "same_source_url", r.get("id"), r.get("title", ""), r.get("slug", ""), cu, 1.0)

    nt = normalize_title(title)
    for r in index.rows:
        if nt and nt == r.get("title_norm"):
            return DuplicateResult(True, "same_title", r.get("id"), r.get("title", ""), r.get("slug", ""), "", 1.0)

    best: Optional[DuplicateResult] = None
    for r in index.rows:
        score = title_similarity(title, r.get("title", ""))
        if score >= similarity_threshold:
            cand = DuplicateResult(True, "similar_title", r.get("id"), r.get("title", ""), r.get("slug", ""), "", score)
            if best is None or cand.score > best.score:
                best = cand
    return best or DuplicateResult(False)


def duplicate_for_signal(index: ExistingIndex, signal: dict[str, Any]) -> DuplicateResult:
    return find_duplicate(index, title=signal.get("title", ""), urls=[signal.get("source", "")])


def duplicate_for_meta(index: ExistingIndex, meta: dict[str, Any], body: str = "") -> DuplicateResult:
    urls: list[str] = []
    for k in ("source_url", "link"):
        if meta.get(k):
            urls.append(meta[k])
    brief = meta.get("source_topic_brief") or {}
    urls.extend(_collect_urls_from_json(brief))
    urls.extend(_collect_urls_from_json(meta.get("sources", [])))
    urls.extend(_collect_urls_from_json(meta.get("expanded_sources", [])))
    return find_duplicate(index, title=meta.get("title", ""), urls=urls)


if __name__ == "__main__":
    idx = load_existing_index()
    print(f"loaded={len(idx.rows)} urls={len(idx.urls)} slugs={len(idx.slugs)}")
