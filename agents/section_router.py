#!/usr/bin/env python3
"""
section_router.py — единая логика выбора раздела публикации SMTInsider.

Секции сайта:
  news    -> /news/
  insight -> /insights/
  review  -> /reviews/
  vendor  -> /vendors/

Задача: агент не должен слепо публиковать всё в Insights или News. Он должен
выбирать раздел по типу материала, заголовку, содержанию, категории и source brief.
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


import re
from dataclasses import dataclass, asdict
from typing import Any, Optional

VALID_SECTIONS = {"news", "insight", "review", "vendor"}
SECTION_PATH = {
    "news": "/news/",
    "insight": "/insights/",
    "review": "/reviews/",
    "vendor": "/vendors/",
}


@dataclass
class SectionDecision:
    editorial_type: str
    section_path: str
    confidence: float
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def normalize_section(value: Optional[str]) -> Optional[str]:
    v = _norm(value)
    aliases = {
        "article": "insight",
        "insights": "insight",
        "review": "review",
        "reviews": "review",
        "buyer guide": "review",
        "buyer-guide": "review",
        "guide": "insight",
        "news": "news",
        "vendor": "vendor",
        "vendors": "vendor",
        "supplier": "vendor",
    }
    if v in VALID_SECTIONS:
        return v
    return aliases.get(v)


def _has_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, flags=re.I) for p in patterns)


def _score(text: str, patterns: list[tuple[str, int]]) -> tuple[int, list[str]]:
    total = 0
    hits: list[str] = []
    for pattern, weight in patterns:
        if re.search(pattern, text, flags=re.I):
            total += weight
            hits.append(pattern)
    return total, hits


REVIEW_PATTERNS: list[tuple[str, int]] = [
    (r"\breview\b", 30),
    (r"\bbuyer\s+guide\b", 28),
    (r"\bcomparison\b|\bcompare\b|\bvs\.?\b", 22),
    (r"\bbest\b|\btop\s+\d+\b|\bshortlist\b", 18),
    (r"\bwhat\s+to\s+verify\b|\bbefore\s+adoption\b|\bbefore\s+shortlisting\b", 18),
    (r"\bdemo\s+questions?\b|\bacceptance\s+criteria\b", 16),
    (r"\bconfiguration\b|\bservice\s+model\b|\btraining\b|\bspare\s+parts?\b", 10),
    (r"\bseries\b|\bplatform\b|\bsystem\b|\bstation\b|\bmachine\b", 9),
    (r"\blaunch(?:es|ed)?\b|\bnew\b|\bannounces?\b|\bunveils?\b|\breleases?\b", 6),
]

INSIGHT_PATTERNS: list[tuple[str, int]] = [
    (r"\bhow\s+to\b|\bwhy\b|\bwhat\s+causes\b|\bexplained\b", 20),
    (r"\btroubleshooting\b|\broot\s+cause\b|\bdiagnostic\b", 20),
    (r"\bprocess\s+control\b|\bcontrol\s+plan\b|\bprocess\s+window\b", 18),
    (r"\bchecklist\b|\bcriteria\b|\bacceptance\b", 14),
    (r"\bwhere\s+it\s+matters\b|\bcommon\s+mistake\b|\bengineering\s+output\b", 16),
    (r"\bdefect\b|\byield\b|\bquality\b|\breflow\b|\bstencil\b|\bplacement\b", 8),
]

NEWS_PATTERNS: list[tuple[str, int]] = [
    (r"\bappoints?\b|\bnames?\b|\bhired?\b|\bceo\b|\bdirector\b", 24),
    (r"\bacquires?\b|\bacquisition\b|\bmerger\b|\bpartnership\b|\bagreement\b", 18),
    (r"\bopens?\b|\bexpands?\b|\bfacility\b|\bhub\b", 16),
    (r"\breports?\b|\braises?\b|\bbook-to-bill\b|\bsales\b|\brevenue\b", 16),
    (r"\baward\b|\bappointed\b|\bjoins?\b", 12),
]

VENDOR_PATTERNS: list[tuple[str, int]] = [
    (r"\bvendor\s+profile\b|\bsupplier\s+profile\b|\bcompany\s+profile\b", 35),
    (r"\babout\s+the\s+(company|vendor|supplier)\b", 20),
    (r"\bdistributor\b|\brepresentative\b|\breseller\b", 10),
]

EQUIPMENT_TERMS = [
    r"\baoi\b", r"\bspi\b", r"\baxi\b", r"\bx-?ray\b", r"\binspection\b",
    r"\bpick\s+and\s+place\b", r"\bplacement\b", r"\breflow\b",
    r"\bwave\s+soldering\b", r"\bselective\s+soldering\b", r"\brework\b",
    r"\bprinter\b", r"\bstencil\b", r"\bfeeder\b", r"\bnozzle\b",
    r"\bconformal\s+coating\b", r"\btest\s+fixture\b", r"\btooling\b",
]


def decide_section(
    title: str,
    body: str = "",
    category: str = "",
    tags: Optional[list[str]] = None,
    source_topic_brief: Optional[dict[str, Any]] = None,
    source_url: str = "",
    explicit: Optional[str] = None,
) -> SectionDecision:
    """Choose publication section.

    `explicit` is respected when strong (review/insight/vendor). If explicit is
    `news`, a strong review/insight signal may override it — fresh product
    announcements often become Reviews or Insights after editorial treatment.
    """
    tags = tags or []
    brief = source_topic_brief or {}
    brief_section = normalize_section(
        brief.get("editorial_type") or brief.get("target_section") or brief.get("format")
    )
    explicit_section = normalize_section(explicit) or brief_section

    text = "\n".join([
        title or "",
        body or "",
        category or "",
        " ".join(tags),
        brief.get("topic", "") if isinstance(brief, dict) else "",
        brief.get("angle", "") if isinstance(brief, dict) else "",
        brief.get("source_notes", "") if isinstance(brief, dict) else "",
        source_url or "",
    ]).lower()

    review_score, review_hits = _score(text, REVIEW_PATTERNS)
    insight_score, insight_hits = _score(text, INSIGHT_PATTERNS)
    news_score, news_hits = _score(text, NEWS_PATTERNS)
    vendor_score, vendor_hits = _score(text, VENDOR_PATTERNS)

    has_equipment = _has_any(text, EQUIPMENT_TERMS)
    has_product_like = _has_any(text, [
        r"\bseries\b", r"\bplatform\b", r"\bsystem\b", r"\bstation\b", r"\bmachine\b",
        r"\bmodel\b", r"\btr\d+\b", r"\bmx-?\d+\b", r"\bcv-?\d+\b",
    ])
    has_buyer_structure = _has_any(text, [
        r"\bbest\s+for\b", r"\bwatch\s+for\b", r"\bdecision\s+output\b",
        r"\bquestions\s+to\s+ask\b", r"\bwhat\s+to\s+verify\b",
        r"\bbefore\s+adoption\b", r"\bconfiguration\b",
    ])

    reasons: list[str] = []

    # Vendor profile is a distinct section.
    if explicit_section == "vendor" or vendor_score >= 25:
        reasons.append("vendor profile/supplier signal")
        return SectionDecision("vendor", SECTION_PATH["vendor"], 0.9, reasons)

    # Strong review intent or buyer-guide structure.
    if explicit_section == "review":
        reasons.append("explicit review/editorial_type")
        return SectionDecision("review", SECTION_PATH["review"], 0.92, reasons)

    if has_buyer_structure and has_equipment:
        reasons.append("buyer-guide structure + equipment topic")
        return SectionDecision("review", SECTION_PATH["review"], 0.88, reasons)

    if has_equipment and has_product_like and review_score >= 18:
        reasons.append("specific equipment/product platform with review indicators")
        if review_hits:
            reasons.append("review hits: " + ", ".join(review_hits[:3]))
        return SectionDecision("review", SECTION_PATH["review"], 0.84, reasons)

    # Strong insight intent.
    if explicit_section == "insight":
        # But if the final article or fresh signal is clearly about a specific
        # equipment platform/system, route it to Reviews/Buyer Guides. This is
        # exactly the case for product launches like "New ... X-ray Inspection System".
        if has_equipment and has_product_like and (review_score >= 14 or review_score > insight_score + 6):
            reasons.append("review override: specific equipment/system signal")
            return SectionDecision("review", SECTION_PATH["review"], 0.84, reasons)
        reasons.append("explicit insight/editorial_type")
        return SectionDecision("insight", SECTION_PATH["insight"], 0.9, reasons)

    if insight_score >= max(news_score, review_score) + 5 and insight_score >= 18:
        reasons.append("technical/process explainer signal")
        if insight_hits:
            reasons.append("insight hits: " + ", ".join(insight_hits[:3]))
        return SectionDecision("insight", SECTION_PATH["insight"], 0.82, reasons)

    # Fresh event/company news stays News unless it was rewritten as a review.
    if explicit_section == "news" and not (has_equipment and has_product_like and review_score >= 24):
        reasons.append("explicit news/editorial_type")
        return SectionDecision("news", SECTION_PATH["news"], 0.86, reasons)

    if news_score > review_score and news_score >= 16:
        reasons.append("company/event/news signal")
        if news_hits:
            reasons.append("news hits: " + ", ".join(news_hits[:3]))
        return SectionDecision("news", SECTION_PATH["news"], 0.78, reasons)

    if review_score >= 22 and has_equipment:
        reasons.append("equipment review/buyer-guide signal")
        return SectionDecision("review", SECTION_PATH["review"], 0.78, reasons)

    # Fallback: sourced current announcements are News; evergreen technical bodies are Insights.
    if brief.get("sources") or source_url:
        reasons.append("fallback: sourced fresh signal")
        return SectionDecision("news", SECTION_PATH["news"], 0.62, reasons)

    reasons.append("fallback: technical editorial")
    return SectionDecision("insight", SECTION_PATH["insight"], 0.6, reasons)


if __name__ == "__main__":
    examples = [
        ("TRI TR7600 SV Series AXI Review: Higher-Throughput 3D X-Ray Inspection for SMT Lines", "What to verify before adoption. Configuration and MES integration.", "X-Ray Inspection", "insight"),
        ("MicroCare Appoints Doug Kay as Director of Market and New Business Development", "", "", "news"),
        ("How to Build an SMT Process Control Plan That Engineers Actually Use", "Where It Matters Common Mistake Engineering Output", "SMT Equipment", None),
        ("Koh Young Vendor Profile: 3D SPI and AOI Inspection", "", "", None),
    ]
    for title, body, category, explicit in examples:
        d = decide_section(title, body, category, explicit=explicit)
        print(f"{d.editorial_type:7s} {d.confidence:.2f} | {title} | {d.reasons}")
