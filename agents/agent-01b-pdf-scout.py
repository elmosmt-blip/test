#!/usr/bin/env python3
"""
Agent #1b — PDF Scout / Manual PDF-to-Article Pipeline (`agents/agent-01b-pdf-scout.py`)

Позволяет оператору вручную подать PDF-файл (отраслевой журнал, брошюру, даташит,
каталог, инструкцию, напр. https://online.fliphtml5.com/kwnhb/fakj/ - SMT Today Issue 80)
или указать URL:
  1) Извлекает текст и проверяет инженерные спецификации (скорость, точность,
     разрешение, габариты и т.д.) через `src/collectors/pdf_collector.py`.
  2) При обработке многостраничных журналов/выпусков (или по флагу `--split-articles` /
     `--max-topics N`) автоматически выделяет независимые статьи/темы по вендорам
     и технологиям (Fuji, Koh Young, Mirtec и др.) в массив `topics`.
  3) При указании флага `--write` автоматически запускает Agent #2 (Writer),
     Agent #2b (Quality Checker), Agent #3 (SEO Doctor) и Agent #4 (Distributor)
     для написания статей "своими словами" по каждой теме с точным указанием
     ссылки на источник (`source_url` в meta.json и микроразметке JSON-LD).

Usage:
  # 1. Обработать выпуск журнала (напр. SMT Today Issue 80) и выделить до 5 независимых статей:
  python3 agents/agent-01b-pdf-scout.py --file SMTMag-Issue-80.pdf --url "https://online.fliphtml5.com/kwnhb/fakj/" --max-topics 5

  # 2. Обработать выпуск и сразу написать статьи по всем выделенным темам выпуска:
  python3 agents/agent-01b-pdf-scout.py --file SMTMag-Issue-80.pdf --url "https://online.fliphtml5.com/kwnhb/fakj/" --max-topics 3 --write

  # 3. Написать статью только по конкретному индексу темы из журнала:
  python3 agents/agent-01b-pdf-scout.py --file SMTMag-Issue-80.pdf --url "https://online.fliphtml5.com/kwnhb/fakj/" --write --pick 0

  # 4. Полный цикл с отправкой черновиков всех статей выпуска в Neon Postgres:
  python3 agents/agent-01b-pdf-scout.py --file SMTMag-Issue-80.pdf --url "https://online.fliphtml5.com/kwnhb/fakj/" --write --submit
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
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

import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

sys.path.insert(0, os.path.dirname(__file__))
import llm_client
import section_router

# Ensure repo root is in sys.path to import src.collectors
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    from src.collectors import pdf_collector
    from src.collectors.pdf_collector import PDFDocument, PDFDocumentType, _KNOWN_VENDORS
    _PDF_COLLECTOR_AVAILABLE = True
except Exception as _pdf_import_err:  # pragma: no cover
    _PDF_COLLECTOR_AVAILABLE = False
    _pdf_import_err_msg = str(_pdf_import_err)


def load_pdf_input(
    file_path: Optional[str],
    source_url: str,
    default_title: str = "",
    timeout: int = 20,
) -> PDFDocument:
    """Load and parse a PDF document from a local file path or remote HTTP URL."""
    if not _PDF_COLLECTOR_AVAILABLE:
        raise RuntimeError(
            f"PDF collector module unavailable ({_pdf_import_err_msg}). "
            "Ensure src/collectors/pdf_collector.py and requirements are present."
        )

    if file_path:
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"PDF файл не найден: {file_path}")
        print(f"📥 Читаю файл: {p.name} ({p.stat().st_size:,} байт)", flush=True)
        content = p.read_bytes()
        print("🔎 Извлекаю текст и метаданные PDF…", flush=True)
        url_to_use = source_url or f"file://{p.absolute()}"
        doc = pdf_collector.parse_pdf_bytes(
            content,
            source_url=url_to_use,
            default_title=default_title or p.stem.replace("-", " ").replace("_", " ").title(),
        )
        if doc is None:
            raise ValueError(f"Не удалось распознать PDF файл: {file_path}")
        return doc

    if not source_url:
        raise ValueError("Укажи либо --file /path/to/doc.pdf, либо --url https://...")

    # Fetch remote PDF over HTTP
    print(f"🌐 Скачиваю документ по ссылке: {source_url} ...")
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; SMTInsiderBot/1.0; +https://smtinsider.com/bot)",
        "Accept": "application/pdf,text/html,*/*",
    }
    try:
        resp = requests.get(source_url, headers=headers, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        doc = pdf_collector.parse_pdf_bytes(
            resp.content,
            source_url=source_url,
            default_title=default_title,
        )
        if doc is None:
            raise ValueError(f"Не удалось распознать содержимое по ссылке {source_url}")
        return doc
    except requests.RequestException as e:
        raise RuntimeError(
            f"Не удалось скачать PDF по адресу --url ({source_url}): {e}. "
            "В изолированной среде скачай файл заранее и передай через аргумент --file /path/to/doc.pdf."
        )


def _segment_magazine_with_llm(
    doc: PDFDocument,
    source_url: str,
    doc_title: str,
    category: str,
    format_type: str,
    editorial_type: str,
    max_topics: int,
    now: datetime,
) -> list[dict[str, Any]]:
    """Identify real article ranges from page-marked magazine text."""
    if getattr(llm_client, "LLM_MOCK", False) or "--- PAGE " not in doc.text:
        return []
    page_matches = list(re.finditer(r"--- PAGE (\d+) ---\s*", doc.text))
    if not page_matches:
        return []
    pages: dict[int, str] = {}
    for i, match in enumerate(page_matches):
        end = page_matches[i + 1].start() if i + 1 < len(page_matches) else len(doc.text)
        pages[int(match.group(1))] = doc.text[match.end():end].strip()
    # Preserve sufficient page context while keeping the segmentation request
    # bounded for a large magazine.
    outline = "\n\n".join(
        f"[PAGE {number}]\n{text[:1800]}" for number, text in pages.items() if text
    )[:70000]
    try:
        result = llm_client.ask_json(
            system=(
                "Ты — редакционный координатор SMTInsider. Раздели журнал только на "
                "самостоятельные статьи, используя номера страниц и ТОЛЬКО текст ниже. "
                "Не создавай тему по случайному упоминанию вендора. Для каждой статьи "
                "нужны связный текст, named subject и факты на её страницах. Верни JSON "
                '{"articles":[{"title":"точный заголовок из текста","company":"...",'
                '"start_page":1,"end_page":2,"recommended_format":"news|review|insight",'
                '"reason":"коротко"}]}. Не добавляй несуществующие статьи.'
            ),
            user=f"MAGAZINE: {doc_title}\n\n{outline}",
            temperature=0,
            max_tokens=1800,
        )
    except Exception as e:
        print(f"⚠ Nemotron не смог сегментировать журнал: {e}")
        return []

    topics: list[dict[str, Any]] = []
    allowed_formats = {"news", "review", "insight"}
    for item in result.get("articles", [])[:max_topics]:
        try:
            start, end = int(item.get("start_page")), int(item.get("end_page"))
        except (TypeError, ValueError):
            continue
        if start not in pages or end not in pages or end < start:
            continue
        segment_text = "\n\n".join(
            f"--- PAGE {page} ---\n{pages[page]}" for page in range(start, end + 1) if page in pages
        ).strip()
        if len(re.findall(r"[A-Za-z][A-Za-z'-]{1,}", segment_text)) < 120:
            continue
        title = str(item.get("title", "")).strip()
        company = str(item.get("company", "")).strip()
        # A title/company emitted by the model must be evidenced literally by
        # the source segment; otherwise it is not a valid article boundary.
        if not title or title.lower() not in segment_text.lower():
            continue
        if company and company.lower() not in segment_text.lower():
            company = ""
        detected_company, products, technologies = pdf_collector.identify_company_and_products(
            segment_text, title, source_url, {}
        )
        company = company or detected_company
        facts = pdf_collector.extract_technical_facts(segment_text, source_url, title)
        # The article-level gate runs on the bounded source text, not the whole
        # magazine. Page markers are removed so the gate cannot confuse this
        # segment with a multi-article container.
        segment_doc = PDFDocument(
            title=title,
            document_type=PDFDocumentType.ARTICLE,
            company=company,
            products=products,
            technologies=technologies,
            page_count=end - start + 1,
            text=re.sub(r"--- PAGE \d+ ---", "", segment_text).strip(),
            source_url=source_url,
            key_facts=facts,
        )
        segment_error, segment_gate = audit_document_evidence_with_llm(segment_doc)
        if segment_error:
            continue
        recommended = str(segment_gate.get("recommended_format") or item.get("recommended_format", format_type)).lower()
        if recommended not in allowed_formats:
            continue
        section_type = "review" if recommended == "review" else recommended
        section = section_router.decide_section(
            title=title,
            body=segment_text,
            category=category,
            tags=[company, *technologies] if company else technologies,
            explicit=section_type,
            source_url=source_url,
        )
        source_entry = {
            "title": f"{doc_title}, pp. {start}-{end}: {title}",
            "url": source_url,
            "date": doc.publication_date or now.strftime("%Y-%m-%d"),
            "role": "magazine_article",
            "excerpt": segment_text[:5000],
            "page_range": [start, end],
            "technical_specs": facts,
            "key_facts": [f"{f['parameter']}: {f['value']} [{f['provenance']}]" for f in facts],
        }
        topics.append({
            "topic": title,
            "angle": f"Report only the documented engineering and production implications in pages {start}-{end} of {doc_title}.",
            "format": recommended,
            "editorial_type": section.editorial_type,
            "target_section": section.section_path,
            "section_routing": section.to_dict(),
            "category": category,
            "keywords": [value for value in [company, *technologies] if value][:8],
            "source_count": 1,
            "urgency": "HIGH",
            "source_notes": f"Page-bounded magazine article, pp. {start}-{end}. {item.get('reason', '')}",
            "key_facts": source_entry["key_facts"],
            "sources": [source_entry],
            "expanded_sources": [source_entry],
            "editorial_gate": {**segment_gate, "page_range": [start, end]},
        })
    return topics


def _build_magazine_topics(
    doc: PDFDocument,
    source_url: str,
    custom_title: str,
    category: str,
    format_type: str,
    editorial_type: str,
    max_topics: int,
) -> list[dict[str, Any]]:
    """Segment a magazine by page boundaries before constructing any topics.

    The preferred path uses Nemotron only to identify article page ranges. Each
    resulting topic then receives the *actual page text* as evidence. It never
    turns a vendor name spotted elsewhere in the issue into a fictional article.
    """
    now = datetime.now(timezone.utc)
    llm_topics = _segment_magazine_with_llm(
        doc, source_url, custom_title, category, format_type, editorial_type, max_topics, now
    )
    if llm_topics:
        return llm_topics

    # Compatibility fallback exists only for local/mock tests. A real
    # page-marked issue with failed/empty segmentation must fail closed rather
    # than reverting to "vendor mentioned somewhere" pseudo-articles.
    if "--- PAGE " in doc.text and not getattr(llm_client, "LLM_MOCK", False):
        return []

    official_url = source_url or doc.source_url or "https://www.smtinsider.com"
    doc_title = custom_title or doc.title or "SMT Industry Magazine"

    # 1. Detect major SMT equipment vendors or featured companies in the text
    found_vendors: list[str] = []
    for vendor in _KNOWN_VENDORS:
        first_word = vendor.split()[0]
        if re.search(r"\b" + re.escape(first_word) + r"\b", doc.text, re.I):
            if vendor not in found_vendors:
                found_vendors.append(vendor)

    topics: list[dict[str, Any]] = []
    lines = doc.text.splitlines()

    for vendor in found_vendors[:max_topics]:
        # Extract paragraph context around this vendor
        matching_lines = [l.strip() for l in lines if vendor.split()[0].lower() in l.lower()]
        vendor_excerpt = " ".join(matching_lines[:6]) if matching_lines else doc.text[:1000]
        if len(vendor_excerpt) < 200:
            vendor_excerpt = doc.text[:1200]

        # Filter key_facts relevant to this vendor
        vendor_facts = []
        for f in doc.key_facts:
            if isinstance(f, dict) and "value" in f:
                ctx = str(f.get("raw_context", ""))
                idx = doc.text.lower().find(ctx.lower()) if ctx else -1
                window = doc.text[max(0, idx - 150):min(len(doc.text), idx + len(ctx) + 150)].lower() if idx >= 0 else ""
                if vendor.split()[0].lower() in window or vendor.split()[0].lower() in str(f.get("provenance", "")).lower():
                    vendor_facts.append(f"{f['parameter']}: {f['value']} [{f['provenance']}]")
        if not vendor_facts and doc.key_facts:
            vendor_facts = [
                f"{f['parameter']}: {f['value']} [{f['provenance']}]"
                for f in doc.key_facts[:3] if isinstance(f, dict) and "value" in f
            ]

        topic_title = f"{vendor}: Engineering Innovations & Production Line Impact in {doc_title}"
        angle = (
            f"Analyze the technical features, automation benefits, and process control impact of "
            f"{vendor} equipment as featured in {doc_title}. Explain practical trade-offs "
            "for SMT assembly engineers."
        )

        section = section_router.decide_section(
            title=topic_title,
            body=angle + "\n" + vendor_excerpt,
            category=category,
            tags=[vendor, "SMT", "Assembly", "Innovation"],
            explicit=editorial_type or format_type,
            source_url=official_url,
        )

        source_entry = {
            "title": f"{doc_title} — {vendor} Feature Article",
            "url": official_url,
            "date": doc.publication_date or now.strftime("%Y-%m-%d"),
            "role": "magazine_article",
            "excerpt": vendor_excerpt[:1800],
            "key_facts": vendor_facts,
            "technical_specs": [
                f for f in doc.key_facts
                if isinstance(f, dict) and "value" in f and (
                    vendor.split()[0].lower() in str(f.get("raw_context", "")).lower()
                    or vendor.split()[0].lower() in str(f.get("provenance", "")).lower()
                )
            ] or doc.key_facts[:3],
        }

        topics.append({
            "topic": topic_title,
            "angle": angle,
            "format": format_type,
            "editorial_type": section.editorial_type,
            "target_section": section.section_path,
            "section_routing": section.to_dict(),
            "category": category,
            "keywords": [vendor, "SMT", "Electronics Manufacturing", "Assembly"][:8],
            "source_count": 1,
            "urgency": "HIGH",
            "source_notes": f"Magazine issue feature article: {vendor} in {doc_title} ({official_url}).",
            "key_facts": vendor_facts,
            "sources": [source_entry],
            "expanded_sources": [source_entry],
        })

    # If fewer vendors matched than max_topics, add thematic articles from the magazine issue
    themes = [
        ("SMT Inspection & 3D Metrology Trends", ["AOI", "SPI", "AXI", "inspection", "defect", "metrology"]),
        ("Advanced Placement & Line Automation", ["placement", "pick and place", "cph", "automation", "robotics"]),
        ("Reflow & Zero-Defect Thermal Profiles", ["reflow", "soldering", "wave", "thermal", "profile"]),
        ("Industry 4.0 & MES Traceability in Electronics", ["traceability", "mes", "cfx", "industry 4.0", "smart factory"]),
    ]
    for theme_name, theme_kws in themes:
        if len(topics) >= max_topics:
            break
        if any(re.search(r"\b" + kw + r"\b", doc.text, re.I) for kw in theme_kws):
            matching_lines = [l.strip() for l in lines if any(kw in l.lower() for kw in theme_kws)]
            excerpt = " ".join(matching_lines[:8]) if matching_lines else doc.text[:1200]
            if len(excerpt) < 200:
                excerpt = doc.text[:1200]

            topic_title = f"{theme_name}: Key Takeaways from {doc_title}"
            angle = (
                f"Synthesize the engineering insights and practical production line guidance on {theme_name} "
                f"from {doc_title}. Focus on verifiable process improvements for SMT teams."
            )
            section = section_router.decide_section(
                title=topic_title,
                body=angle + "\n" + excerpt,
                category=category,
                tags=theme_kws[:4],
                explicit=editorial_type or format_type,
                source_url=official_url,
            )
            source_entry = {
                "title": f"{doc_title} — {theme_name}",
                "url": official_url,
                "date": doc.publication_date or now.strftime("%Y-%m-%d"),
                "role": "magazine_article",
                "excerpt": excerpt[:1800],
                "key_facts": [
                    f"{f['parameter']}: {f['value']} [{f['provenance']}]"
                    for f in doc.key_facts[:4] if isinstance(f, dict) and "value" in f
                ],
                "technical_specs": doc.key_facts[:4],
            }
            topics.append({
                "topic": topic_title,
                "angle": angle,
                "format": format_type,
                "editorial_type": section.editorial_type,
                "target_section": section.section_path,
                "section_routing": section.to_dict(),
                "category": category,
                "keywords": ["SMT", "Electronics Manufacturing", theme_kws[0].upper()][:8],
                "source_count": 1,
                "urgency": "HIGH",
                "source_notes": f"Magazine thematic synthesis: {theme_name} in {doc_title} ({official_url}).",
                "key_facts": [
                    f"{f['parameter']}: {f['value']} [{f['provenance']}]"
                    for f in doc.key_facts[:4] if isinstance(f, dict) and "value" in f
                ],
                "sources": [source_entry],
                "expanded_sources": [source_entry],
            })

    return topics


def build_pdf_topic_brief(
    doc: PDFDocument,
    source_url: str,
    custom_title: str = "",
    custom_topic: str = "",
    custom_angle: str = "",
    category: str = "SMT Equipment",
    format_type: str = "review",
    editorial_type: str = "review",
    max_topics: int = 1,
    split_articles: bool = False,
    use_llm: bool = True,
) -> dict[str, Any]:
    """Generate a Trend Hunter compatible `briefs.json` payload from a PDFDocument."""
    now = datetime.now(timezone.utc)
    official_url = source_url or doc.source_url or "https://www.smtinsider.com"
    doc_title = custom_title or doc.title or "SMT Technical Document"
    vendor = doc.company or "SMT Equipment Vendor"

    # A magazine container is not itself evidence that every vendor mention is
    # an article. Segmentation is allowed only when explicitly requested by the
    # caller after the editorial evidence gate has approved it.
    if split_articles or max_topics > 1:
        topics = _build_magazine_topics(
            doc, official_url, doc_title, category, format_type, editorial_type, max_topics=max_topics
        )
        if topics or "--- PAGE " in doc.text:
            return {
                "generated_at": now.isoformat(),
                "source_type": "manual_pdf",
                "segmentation_error": "No page-bounded article passed evidence gate" if not topics else "",
                "pdf_metadata": {
                    "title": doc_title,
                    "document_type": doc.document_type,
                    "company": doc.company,
                    "products": doc.products,
                    "technologies": doc.technologies,
                    "page_count": doc.page_count,
                    "file_hash": doc.file_hash,
                    "text_hash": doc.text_hash,
                    "official_url": official_url,
                },
                "topics": topics,
            }

    # Single-document / brochure / datasheet topic brief
    topic_title = custom_topic
    if not topic_title:
        if doc.products:
            topic_title = f"{vendor} {doc.products[0]}: Technical Specifications & Engineering Review"
        else:
            topic_title = f"Engineering Review: {doc_title}"

    key_facts_strings = [
        f"{f['parameter']}: {f['value']} [{f['provenance']}]"
        for f in doc.key_facts if isinstance(f, dict) and "value" in f
    ]

    angle = custom_angle
    if not angle:
        specs_summary = "; ".join(
            f"{f['parameter']} = {f['value']}"
            for f in doc.key_facts[:4] if isinstance(f, dict) and "value" in f
        )
        if specs_summary:
            angle = (
                f"Analyze the technical capabilities and line impact of {doc_title} "
                f"from {vendor}. Focus on verified parameters ({specs_summary}) "
                f"and explain practical trade-offs for SMT production engineers."
            )
        else:
            angle = (
                f"Provide a comprehensive technical breakdown of {doc_title} from {vendor}. "
                "Evaluate operational benefits, process control features, and SMT line integration."
            )

    section = section_router.decide_section(
        title=topic_title,
        body=angle + "\n" + doc.text[:1500],
        category=category,
        tags=doc.technologies or ["SMT", "PCB", "Inspection"],
        explicit=editorial_type or format_type,
        source_url=official_url,
    )

    keywords = list(doc.technologies) if doc.technologies else ["SMT", "Electronics Manufacturing", "Quality Control"]
    if vendor and vendor not in keywords:
        keywords.insert(0, vendor)
    for p in doc.products[:3]:
        if p and p not in keywords:
            keywords.append(p)

    sig = doc.to_signal(vendor_name=vendor, vendor_group="manual_pdf")
    sig["source"] = official_url

    source_entry = {
        "title": doc_title,
        "url": official_url,
        "date": doc.publication_date or now.strftime("%Y-%m-%d"),
        "role": "primary_pdf_source",
        "excerpt": doc.text[:1800],
        "key_facts": key_facts_strings,
        "technical_specs": doc.key_facts,
    }

    topic_dict = {
        "topic": topic_title,
        "angle": angle,
        "format": format_type,
        "editorial_type": section.editorial_type,
        "target_section": section.section_path,
        "section_routing": section.to_dict(),
        "category": category,
        "keywords": keywords[:8],
        "source_count": 1,
        "urgency": "HIGH",
        "source_notes": f"Manual PDF Scout ingestion: {doc_title} ({official_url}). Pages: {doc.page_count}.",
        "key_facts": key_facts_strings,
        "sources": [source_entry],
        "expanded_sources": [source_entry],
    }

    return {
        "generated_at": now.isoformat(),
        "source_type": "manual_pdf",
        "pdf_metadata": {
            "title": doc_title,
            "document_type": doc.document_type,
            "company": doc.company,
            "products": doc.products,
            "technologies": doc.technologies,
            "page_count": doc.page_count,
            "file_hash": doc.file_hash,
            "text_hash": doc.text_hash,
            "official_url": official_url,
        },
        "topics": [topic_dict],
    }


def print_scout_summary(doc: PDFDocument, brief: dict[str, Any], source_url: str):
    topics = brief.get("topics", [])
    print(f"""
╔═══════════════════════════════════════════════════════════════╗
║  SMTInsider PDF Scout — Документ обработан                  ║
╚═══════════════════════════════════════════════════════════════╝
  📄 Документ:     {doc.title}
  🏢 Вендор:       {doc.company or 'Не определен'}
  📁 Тип файла:    {doc.document_type} ({doc.page_count} стр.)
  🔗 Источник URL: {source_url or doc.source_url}
  🛠  Технологии:  {', '.join(doc.technologies) if doc.technologies else 'SMT, Assembly'}
  📦 Продукты:     {', '.join(doc.products) if doc.products else 'Не определены'}
  📊 Спецификации: найдено {len(doc.key_facts)} конкретных параметров
───────────────────────────────────────────────────────────────""")
    print(f"  📚 В выпуске/документе выделено {len(topics)} тем(ы)/статей:")
    for i, t in enumerate(topics):
        print(f"  [{i}] {t['topic']}")
        print(f"      → Раздел сайта: {t['editorial_type']} → {t['target_section']}")
        print(f"      → Угол обзора:  {t['angle'][:75]}...")
    print("───────────────────────────────────────────────────────────────")


def run_pipeline(
    brief_path: str,
    article_path: str,
    meta_path: str,
    do_submit: bool = False,
    pick: str = "all",
) -> list[dict[str, Any]]:
    """Execute Agent #2, #2b, #3, #4 (and optionally #6 submit) on the generated PDF brief."""
    with open(brief_path, encoding="utf-8") as f:
        brief_data = json.load(f)
    topics = brief_data.get("topics", [])
    if not topics:
        raise ValueError(f"❌ В {brief_path} нет тем для написания статьи")

    indices_to_write = []
    if pick == "all":
        indices_to_write = list(range(len(topics)))
    elif pick.isdigit():
        idx = int(pick)
        if idx < 0 or idx >= len(topics):
            raise ValueError(f"❌ --pick {idx} вне диапазона (0-{len(topics)-1})")
        indices_to_write = [idx]
    else:
        indices_to_write = [0]

    results: list[dict[str, Any]] = []

    for i in indices_to_write:
        curr_article_path = article_path if len(indices_to_write) == 1 else article_path.rsplit(".", 1)[0] + f"_{i}.txt"
        curr_meta_path = meta_path if len(indices_to_write) == 1 else meta_path.rsplit(".", 1)[0] + f"_{i}.meta.json"

        print(f"\n━━━ ШАГ 1/5: Agent #2 (Writer) — Тема [{i}] ({topics[i]['topic'][:55]}) ━━━")
        cmd_writer = [
            sys.executable,
            os.path.join(os.path.dirname(__file__), "agent-02-writer.py"),
            "--brief", brief_path,
            "--pick", str(i),
            "--output", curr_article_path,
        ]
        subprocess.run(cmd_writer, check=True)

        print(f"\n━━━ ШАГ 2/5: Agent #2b (Quality Checker) — Тема [{i}] ━━━")
        cmd_qc = [
            sys.executable,
            os.path.join(os.path.dirname(__file__), "agent-02b-quality-checker.py"),
            "--meta", curr_meta_path,
        ]
        subprocess.run(cmd_qc, check=True)

        print(f"\n━━━ ШАГ 3/5: Agent #3 (SEO Doctor) — Тема [{i}] ━━━")
        cmd_seo = [
            sys.executable,
            os.path.join(os.path.dirname(__file__), "agent-03-seo-doctor.py"),
            "--meta", curr_meta_path,
        ]
        subprocess.run(cmd_seo, check=True)

        print(f"\n━━━ ШАГ 4/5: Agent #4 (Distributor) — LinkedIn, Форум, Email [{i}] ━━━")
        cmd_dist = [
            sys.executable,
            os.path.join(os.path.dirname(__file__), "agent-04-distributor.py"),
            "--meta", curr_meta_path,
        ]
        subprocess.run(cmd_dist, check=True)

        if do_submit:
            db_url = os.environ.get("NEON_DATABASE_URL")
            allow_write = os.environ.get("ALLOW_DB_WRITES", "0").lower() in {"1", "true", "yes", "on"}
            print(f"\n━━━ ШАГ 5/5: Agent #6 (Publisher) — Запись в Neon DB [{i}] ━━━")
            if not db_url or not allow_write:
                print("  ⚠ Запись в БД пропущена (NEON_DATABASE_URL не задан или ALLOW_DB_WRITES=0).")
            else:
                cmd_pub = [
                    sys.executable,
                    os.path.join(os.path.dirname(__file__), "agent-06-publisher.py"),
                    "submit",
                    "--meta", curr_meta_path,
                ]
                subprocess.run(cmd_pub, check=True)

        with open(curr_meta_path, encoding="utf-8") as f:
            meta_data = json.load(f)
            meta_data["_article_file"] = curr_article_path
            meta_data["_meta_file"] = curr_meta_path
            results.append(meta_data)

    return results


def recover_fliphtml5_text_layer(source_url: str, doc: PDFDocument) -> tuple[Optional[PDFDocument], str]:
    """Read FlipHTML5's own per-page searchable text layer.

    FlipHTML5 publishes text positions for its reader search feature at
    ``files/search/text_position[N].js``. This is the publisher/viewer's own
    text layer, not an OCR guess and not raw PDF stream parsing. Keeping page
    markers allows the next segmentation stage to build separate articles from
    a magazine issue.
    """
    parsed = urllib.parse.urlparse(source_url)
    if not parsed.netloc.endswith("fliphtml5.com"):
        return None, "источник не является FlipHTML5"
    base = source_url.split("#", 1)[0].rstrip("/") + "/"
    max_pages = max(1, int(os.environ.get("FLIPHTML5_MAX_PAGES", "120")))
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SMTInsiderBot/1.0)"}
    pages: list[str] = []
    try:
        for page_number in range(1, max_pages + 1):
            response = requests.get(
                f"{base}files/search/text_position[{page_number}].js",
                headers=headers,
                timeout=15,
            )
            if response.status_code == 404:
                break
            response.raise_for_status()
            match = re.search(r"=\s*(\{.*\})\s*;?\s*$", response.text, re.S)
            if not match:
                continue
            page_data = json.loads(match.group(1))
            words = [str(item.get("w", "")).replace("|", " ").strip() for item in page_data.get("positions", [])]
            page_text = " ".join(word for word in words if word)
            if page_text:
                pages.append(f"\n\n--- PAGE {page_number} ---\n{page_text}")
            if page_number % 10 == 0:
                print(f"   FlipHTML5 text layer: {page_number} страниц", flush=True)
    except (requests.RequestException, json.JSONDecodeError) as e:
        return None, f"не удалось получить text layer FlipHTML5: {e}"

    recovered_text = "".join(pages).strip()
    if len(re.findall(r"[A-Za-z][A-Za-z'-]{1,}", recovered_text)) < 80:
        return None, "FlipHTML5 text layer не содержит достаточно читаемого текста"

    doc.text = recovered_text
    doc.text_hash = pdf_collector.hash_text(recovered_text)
    doc.page_count = max(doc.page_count, len(pages))
    # Never retain a PDF syntax object as a document title.
    if not doc.title or any(marker in doc.title for marker in ("FlateDecode", "<<", "/Filter")):
        doc.title = "SMT Magazine Issue"
    doc.document_type = PDFDocumentType.MAGAZINE
    doc.company, doc.products, doc.technologies = pdf_collector.identify_company_and_products(
        recovered_text, doc.title, doc.source_url, doc.metadata
    )
    doc.key_facts = pdf_collector.extract_technical_facts(recovered_text, doc.source_url, doc.title)
    return doc, ""


def recover_pdf_text_with_ocr(file_path: str, doc: PDFDocument) -> tuple[Optional[PDFDocument], str]:
    """OCR a scanned/malformed local PDF when normal text extraction is unusable.

    This is intentionally a local, opt-in-by-availability recovery path: OCR
    reads page images from the operator's uploaded file and does not ask the
    LLM to guess missing text. Tesseract must be installed on the host; PyMuPDF
    and pytesseract are Python dependencies.
    """
    try:
        import fitz  # PyMuPDF
        import pytesseract
        from PIL import Image
    except ImportError:
        return None, "OCR-модули не установлены (нужны PyMuPDF и pytesseract)"

    tesseract_cmd = os.environ.get("TESSERACT_CMD", "").strip()
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    try:
        pytesseract.get_tesseract_version()
    except Exception:
        return None, "Tesseract OCR не найден. Установите Tesseract и при необходимости задайте TESSERACT_CMD"

    max_pages = max(1, int(os.environ.get("PDF_OCR_MAX_PAGES", "80")))
    try:
        pdf = fitz.open(file_path)
        page_count = min(len(pdf), max_pages)
        print(f"🧾 Запускаю OCR: {page_count} из {len(pdf)} страниц (это может занять несколько минут)…", flush=True)
        pages: list[str] = []
        for page_number in range(page_count):
            # 2x rasterization is a practical balance for small magazine text
            # without turning a 80-page document into an unbounded job.
            pix = pdf.load_page(page_number).get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image = Image.open(io.BytesIO(pix.tobytes("png")))
            page_text = pytesseract.image_to_string(image, lang=os.environ.get("PDF_OCR_LANGUAGE", "eng"))
            if len(re.findall(r"[A-Za-z][A-Za-z'-]{1,}", page_text)) >= 20:
                pages.append(page_text)
            if (page_number + 1) % 5 == 0 or page_number + 1 == page_count:
                print(f"   OCR обработано страниц: {page_number + 1}/{page_count}", flush=True)
        pdf.close()
    except Exception as e:
        return None, f"OCR не выполнился: {e}"

    recovered_text = "\n".join(pages).strip()
    if len(re.findall(r"[A-Za-z][A-Za-z'-]{1,}", recovered_text)) < 80:
        return None, "OCR не получил достаточно читаемого текста"

    doc.text = recovered_text
    doc.text_hash = pdf_collector.hash_text(recovered_text)
    doc.document_type = pdf_collector.classify_pdf_document_type(doc.title, recovered_text, doc.source_url)
    doc.company, doc.products, doc.technologies = pdf_collector.identify_company_and_products(
        recovered_text, doc.title, doc.source_url, doc.metadata
    )
    doc.key_facts = pdf_collector.extract_technical_facts(recovered_text, doc.source_url, doc.title)
    doc.page_count = max(doc.page_count, page_count)
    return doc, ""


def validate_document_for_editorial_use(doc: PDFDocument) -> Optional[str]:
    """Return an error when extraction cannot support a factual article.

    A title or a few numbers from a malformed PDF are not enough evidence for
    a publishable SMTInsider article. Stop before Writer can turn PDF syntax
    or isolated OCR fragments into invented engineering analysis.
    """
    text = (doc.text or "").strip()
    syntax_markers = ("FlateDecode", "startxref", "<<", "/Filter", "/Length")
    if any(marker in text for marker in syntax_markers):
        return "извлечённый текст содержит служебные PDF-данные, а не текст статьи"
    words = re.findall(r"[A-Za-z][A-Za-z'-]{1,}", text)
    if len(words) < 80:
        return (
            "в документе недостаточно читаемого текста для проверяемой статьи "
            f"({len(words)} слов; требуется не менее 80)"
        )
    alpha_chars = sum(ch.isalpha() for ch in text)
    if alpha_chars / max(len(text), 1) < 0.45:
        return "извлечение похоже на повреждённый текстовый слой или OCR-мусор"
    return None


def audit_document_evidence_with_llm(doc: PDFDocument) -> tuple[Optional[str], dict[str, Any]]:
    """Classify editorial evidence with Nemotron before Writer can use it.

    The model is a constrained gatekeeper, never an extractor or fact source.
    It chooses a format only when the extracted text itself supports that
    format; otherwise it rejects the document. This prevents a magazine
    fragment from becoming a fake product review or news report.
    """
    fallback = {"decision": "deterministic_only", "recommended_format": "", "reason": "LLM audit disabled"}
    if os.environ.get("PDF_SCOUT_LLM_AUDIT", "1").lower() in {"0", "false", "no", "off"}:
        return None, fallback
    if getattr(llm_client, "LLM_MOCK", False):
        fallback["reason"] = "LLM mock mode"
        return None, fallback

    # A page-marked magazine is not one editorial claim. Its individual
    # segments are audited below; do not reject a whole issue simply because
    # its first pages are a cover or table of contents.
    if "--- PAGE " in (doc.text or ""):
        return None, {
            "decision": "accept",
            "recommended_format": "news",
            "reason": "Magazine requires page-bounded article segmentation",
            "allow_segmentation": True,
        }

    excerpt = (doc.text or "")[:12000]
    try:
        result = llm_client.ask_json(
            system=(
                "Ты — строгий редактор SMTInsider. Оцениваешь ТОЛЬКО доказательства "
                "в переданном извлечённом тексте. Никогда не добавляй знания, не угадывай "
                "vendor/model/specifications и не пиши статью. Верни только JSON: "
                '{"decision":"accept|reject","recommended_format":"news|review|buyer_guide|insight|reject",'
                '"reason":"...","missing_evidence":["..."],"named_subject":true|false,'
                '"attributable_facts":0,"allow_segmentation":true|false}.\n'
                "Форматы: news требует named company/product/event и >=2 связанных факта; "
                "review требует vendor+named product/model и >=3 подтверждённых specs/features; "
                "buyer_guide требует >=3 названных кандидата ИЛИ >=5 практических критериев выбора; "
                "insight требует >=300 слов связного process text и не должен делать claims о конкретном продукте. "
                "Reject во всех остальных случаях, особенно для metadata, OCR-мусора, изолированных чисел "
                "и журнальных фрагментов без атрибуции. allow_segmentation=true только когда текст явно "
                "разделён на самостоятельные статьи с отдельными vendor/product evidence."
            ),
            user=(
                f"TITLE: {doc.title}\nCOMPANY: {doc.company or 'unknown'}\n"
                f"EXTRACTED TEXT:\n{excerpt}"
            ),
            temperature=0,
            max_tokens=450,
        )
    except Exception as e:
        print(f"⚠ LLM-проверка доказательств недоступна, использую детерминированную проверку: {e}")
        fallback["reason"] = "LLM audit unavailable"
        return None, fallback

    allowed_formats = {"news", "review", "buyer_guide", "insight"}
    decision = str(result.get("decision", "")).lower()
    recommended = str(result.get("recommended_format", "")).lower()
    if decision != "accept" or recommended not in allowed_formats:
        reason = str(result.get("reason", "недостаточно связанного с темой проверяемого содержания"))
        return f"LLM-проверка отклонила материал: {reason}", result
    return None, result


def main():
    p = argparse.ArgumentParser(
        prog="agent-01b-pdf-scout",
        description="SMTInsider Manual PDF-to-Article pipeline scout",
    )
    p.add_argument("--file", "-f", help="Путь к локальному PDF (или текстовому/HTML) файлу")
    p.add_argument("--url", "-u", default="", help="Официальный URL источника (напр. https://online.fliphtml5.com/kwnhb/fakj/)")
    p.add_argument("--title", default="", help="Название документа (опционально, иначе извлекается из PDF)")
    p.add_argument("--topic", default="", help="Заголовок статьи (опционально, для одного топика)")
    p.add_argument("--angle", default="", help="Угол обзора для инженера (опционально)")
    p.add_argument("--category", default="SMT Equipment", help="Категория (default: SMT Equipment)")
    p.add_argument("--format", dest="format_type", default="review", choices=["review", "insight", "news", "vendor", "magazine", "article"],
                   help="Формат статьи (default: review)")
    p.add_argument("--type", dest="editorial_type", default="review", choices=["review", "insight", "news", "vendor", "magazine", "article"],
                   help="Секция сайта: review -> /reviews/, insight -> /insights/ и т.д.")
    p.add_argument("--max-topics", type=int, default=5, help="Максимум статей/тем для выделения из многостраничного выпуска/журнала (default: 5)")
    p.add_argument("--split-articles", action="store_true", help="Принудительно разбить документ на несколько независимых статей/тем")
    p.add_argument("--brief", default="/tmp/pdf_scout_briefs.json", help="Путь сохранения briefs.json")
    p.add_argument("--write", "-w", action="store_true", help="Автоматически запустить Writer, Quality Checker, SEO и Distributor")
    p.add_argument("--pick", default="all", help="Индекс темы для написания ('all', '0', '1'... default: 'all')")
    p.add_argument("--article", default="/tmp/pdf_scout_article.txt", help="Путь сохранения article.txt при --write (или префикс для мульти-тем)")
    p.add_argument("--meta", default="/tmp/pdf_scout_article.meta.json", help="Путь сохранения meta.json при --write")
    p.add_argument("--submit", action="store_true", help="Сохранить черновик в Neon Postgres (если задан ALLOW_DB_WRITES=1)")

    args = p.parse_args()

    if not args.file and not args.url:
        p.error("Укажи --file /path/to/file.pdf или --url https://...")

    try:
        doc = load_pdf_input(args.file, args.url, default_title=args.title)
    except Exception as e:
        print(f"❌ Ошибка обработки документа: {e}")
        sys.exit(1)

    editorial_error = validate_document_for_editorial_use(doc)
    # A FlipHTML5 URL returns a viewer HTML shell, which can accidentally pass
    # a simple word-count check. Always replace that shell with the platform's
    # own searchable page text before any evidence/LLM decision.
    is_fliphtml5 = "fliphtml5.com" in urllib.parse.urlparse(args.url).netloc.lower()
    if args.url and is_fliphtml5:
        print("📖 Получаю постраничный searchable text layer FlipHTML5…", flush=True)
        recovered_doc, layer_error = recover_fliphtml5_text_layer(args.url, doc)
        if recovered_doc is not None:
            doc = recovered_doc
            editorial_error = validate_document_for_editorial_use(doc)
            if not editorial_error:
                layer_word_count = len(re.findall(r"[A-Za-z][A-Za-z'-]{1,}", doc.text))
                print(f"✅ Получено {layer_word_count} слов из text layer FlipHTML5; запускаю page-aware segmentation.", flush=True)
        else:
            print(f"⚠ Text layer недоступен: {layer_error}", flush=True)

    # OCR is an explicit last resort for a local scanned document, never the
    # default workflow. The FlipHTML5 path above does not require Tesseract.
    if editorial_error and args.file and os.environ.get("PDF_ENABLE_LOCAL_OCR", "0").lower() in {"1", "true", "yes", "on"}:
        print("⚠ Включён локальный OCR fallback…", flush=True)
        recovered_doc, ocr_error = recover_pdf_text_with_ocr(args.file, doc)
        if recovered_doc is not None:
            doc = recovered_doc
            editorial_error = validate_document_for_editorial_use(doc)
        else:
            print(f"⚠ OCR fallback недоступен: {ocr_error}", flush=True)

    editorial_gate: dict[str, Any] = {"decision": "not_run", "recommended_format": ""}
    if not editorial_error:
        print("🧠 Nemotron выбирает допустимый editorial format по доказательствам…", flush=True)
        editorial_error, editorial_gate = audit_document_evidence_with_llm(doc)
    if editorial_error:
        print(
            "❌ Статья не создана: " + editorial_error + ". "
            "Загрузите исходный PDF с текстовым слоем, выполните OCR или укажите "
            "официальную HTML-страницу/пресс-релиз с полным текстом."
        )
        sys.exit(2)

    recommended_format = editorial_gate.get("recommended_format") or args.format_type
    # Buyer guides live in the Reviews section, while the other approved
    # formats map 1:1 to the site's editorial sections.
    effective_editorial_type = "review" if recommended_format == "buyer_guide" else recommended_format
    allow_segmentation = bool(editorial_gate.get("allow_segmentation", False))
    effective_max_topics = args.max_topics if allow_segmentation else 1
    if recommended_format != args.format_type:
        print(f"ℹ Формат изменён evidence gate: {args.format_type} → {recommended_format}")
    if args.max_topics > 1 and not allow_segmentation:
        print("ℹ Разделение журнала отключено: Nemotron не подтвердил самостоятельные статьи с отдельными доказательствами.")

    brief_payload = build_pdf_topic_brief(
        doc=doc,
        source_url=args.url or doc.source_url,
        custom_title=args.title,
        custom_topic=args.topic,
        custom_angle=args.angle,
        category=args.category,
        format_type=recommended_format,
        editorial_type=effective_editorial_type,
        max_topics=effective_max_topics,
        split_articles=args.split_articles and allow_segmentation,
    )
    brief_payload["editorial_gate"] = editorial_gate
    if not brief_payload.get("topics"):
        print(
            "❌ Статьи не созданы: ни один page-bounded сегмент журнала не прошёл "
            "evidence gate. Нужны самостоятельный заголовок, предмет статьи и "
            "достаточные подтверждённые факты на его страницах."
        )
        sys.exit(2)

    brief_path = Path(args.brief)
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(json.dumps(brief_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print_scout_summary(doc, brief_payload, args.url or doc.source_url)
    print(f"✅ Topic Brief сохранён: {brief_path}")

    if not args.write:
        print(f"\n👉 Чтобы написать статью по этому выпуску/PDF вручную, выполни:")
        print(f"   python3 agents/agent-02-writer.py --brief {brief_path} --pick 0 --output /tmp/article_0.txt")
        print(f"   python3 agents/agent-03-seo-doctor.py --meta /tmp/article_0.meta.json")
        print(f"   python3 agents/agent-04-distributor.py --meta /tmp/article_0.meta.json")
        return

    meta_results = run_pipeline(
        brief_path=str(brief_path),
        article_path=args.article,
        meta_path=args.meta,
        do_submit=args.submit,
        pick=args.pick,
    )

    print(f"\n╔═══════════════════════════════════════════════════════════════╗")
    print(f"║  ✅ СТАТЬИ ПО ВЫПУСКУ НАПИСАНЫ И ГОТОВЫ К ДИСТРИБУЦИИ        ║")
    print(f"╚═══════════════════════════════════════════════════════════════╝")
    for idx, m in enumerate(meta_results):
        print(f"  --- СТАТЬЯ [{idx}] ---")
        print(f"  📝 Заголовок:  {m.get('title')}")
        print(f"  📎 Slug:       {m.get('seo', {}).get('slug', 'N/A')}")
        print(f"  🔗 Источник:   {m.get('source_url', args.url)}")
        print(f"  📄 Текст:      {m.get('_article_file')}")
        print(f"  💾 Метаданные: {m.get('_meta_file')}")
        print(f"  📊 Качество:   {m.get('lint_report', {}).get('score', 'N/A')}/100")
        print(f"  📣 LinkedIn:   {'Готов' if m.get('distribution', {}).get('linkedin_post') else 'Нет'}")
        print(f"  💬 Форум:      {'Готов' if m.get('distribution', {}).get('forum_answer') else 'Нет'}")
        print(f"  📧 Email:      {'Готов' if m.get('distribution', {}).get('email_block') else 'Нет'}")
    print(f"───────────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
