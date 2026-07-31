#!/usr/bin/env python3
"""
Agent #1b — PDF Scout / Manual PDF-to-Article Pipeline (`agents/agent-01b-pdf-scout.py`)

Позволяет оператору вручную подать PDF-файл (брошюру, даташит, каталог,
инструкцию, напр. https://online.fliphtml5.com/...) или указать URL:
  1) Извлекает текст и проверяет инженерные спецификации (скорость, точность,
     разрешение, габариты и т.д.) через `src/collectors/pdf_collector.py`.
  2) Формирует стандартизированный `briefs.json` с привязкой источника (`--url`).
  3) При указании флага `--write` автоматически запускает Agent #2 (Writer),
     Agent #2b (Quality Checker), Agent #3 (SEO Doctor) и Agent #4 (Distributor)
     для написания статьи "своими словами" с точным указанием ссылки на источник.

Usage:
  # 1. Только создать бриф по локальному PDF-файлу с указанием официального URL:
  python3 agents/agent-01b-pdf-scout.py --file /tmp/catalog.pdf --url "https://online.fliphtml5.com/kwnhb/fakj/"

  # 2. Создать бриф и сразу написать статью с SEO-обвязкой и промо-постами:
  python3 agents/agent-01b-pdf-scout.py --file /tmp/catalog.pdf --url "https://online.fliphtml5.com/kwnhb/fakj/" --write

  # 3. Полный цикл с отправкой черновика в БД Neon Postgres:
  python3 agents/agent-01b-pdf-scout.py --file /tmp/catalog.pdf --url "https://online.fliphtml5.com/kwnhb/fakj/" --write --submit
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
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
    from src.collectors.pdf_collector import PDFDocument, PDFDocumentType
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
        content = p.read_bytes()
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


def build_pdf_topic_brief(
    doc: PDFDocument,
    source_url: str,
    custom_title: str = "",
    custom_topic: str = "",
    custom_angle: str = "",
    category: str = "SMT Equipment",
    format_type: str = "review",
    editorial_type: str = "review",
    use_llm: bool = True,
) -> dict[str, Any]:
    """Generate a Trend Hunter compatible `briefs.json` payload from a PDFDocument."""
    now = datetime.now(timezone.utc)
    official_url = source_url or doc.source_url or "https://www.smtinsider.com"

    doc_title = custom_title or doc.title or "SMT Technical Document"
    vendor = doc.company or "SMT Equipment Vendor"

    # Derive topic title
    topic_title = custom_topic
    if not topic_title:
        if doc.products:
            topic_title = f"{vendor} {doc.products[0]}: Technical Specifications & Engineering Review"
        else:
            topic_title = f"Engineering Review: {doc_title}"

    # Extract verifiable technical facts for grounding
    key_facts_strings = [
        f"{f['parameter']}: {f['value']} [{f['provenance']}]"
        for f in doc.key_facts if isinstance(f, dict) and "value" in f
    ]

    # Derive editorial angle
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

    # Automatically map target section
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
    topic = brief["topics"][0]
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
───────────────────────────────────────────────────────────────
  📝 Тема статьи:  {topic['topic']}
  🎯 Раздел сайта: {topic['editorial_type']} → {topic['target_section']}
  🔍 Угол обзора:  {topic['angle'][:90]}...
───────────────────────────────────────────────────────────────""")


def run_pipeline(
    brief_path: str,
    article_path: str,
    meta_path: str,
    do_submit: bool = False,
) -> dict[str, Any]:
    """Execute Agent #2, #2b, #3, #4 (and optionally #6 submit) on the generated PDF brief."""
    print("\n━━━ ШАГ 1/5: Agent #2 (Writer) — Пишу статью по PDF документации ━━━")
    cmd_writer = [
        sys.executable,
        os.path.join(os.path.dirname(__file__), "agent-02-writer.py"),
        "--brief", brief_path,
        "--pick", "0",
        "--output", article_path,
    ]
    res_w = subprocess.run(cmd_writer, check=True)

    print("\n━━━ ШАГ 2/5: Agent #2b (Quality Checker) ━━━")
    cmd_qc = [
        sys.executable,
        os.path.join(os.path.dirname(__file__), "agent-02b-quality-checker.py"),
        "--meta", meta_path,
    ]
    subprocess.run(cmd_qc, check=True)

    print("\n━━━ ШАГ 3/5: Agent #3 (SEO Doctor) ━━━")
    cmd_seo = [
        sys.executable,
        os.path.join(os.path.dirname(__file__), "agent-03-seo-doctor.py"),
        "--meta", meta_path,
    ]
    subprocess.run(cmd_seo, check=True)

    print("\n━━━ ШАГ 4/5: Agent #4 (Distributor) — LinkedIn, Форум, Email ━━━")
    cmd_dist = [
        sys.executable,
        os.path.join(os.path.dirname(__file__), "agent-04-distributor.py"),
        "--meta", meta_path,
    ]
    subprocess.run(cmd_dist, check=True)

    submitted_id = None
    if do_submit:
        db_url = os.environ.get("NEON_DATABASE_URL")
        allow_write = os.environ.get("ALLOW_DB_WRITES", "0").lower() in {"1", "true", "yes", "on"}
        print("\n━━━ ШАГ 5/5: Agent #6 (Publisher) — Запись черновика в Neon DB ━━━")
        if not db_url or not allow_write:
            print("  ⚠ Запись в БД пропущена (NEON_DATABASE_URL не задан или ALLOW_DB_WRITES=0).")
        else:
            cmd_pub = [
                sys.executable,
                os.path.join(os.path.dirname(__file__), "agent-06-publisher.py"),
                "submit",
                "--meta", meta_path,
            ]
            subprocess.run(cmd_pub, check=True)

    with open(meta_path, encoding="utf-8") as f:
        meta_data = json.load(f)

    return meta_data


def main():
    p = argparse.ArgumentParser(
        prog="agent-01b-pdf-scout",
        description="SMTInsider Manual PDF-to-Article pipeline scout",
    )
    p.add_argument("--file", "-f", help="Путь к локальному PDF (или текстовому/HTML) файлу")
    p.add_argument("--url", "-u", default="", help="Официальный URL источника (напр. https://online.fliphtml5.com/kwnhb/fakj/)")
    p.add_argument("--title", default="", help="Название документа (опционально, иначе извлекается из PDF)")
    p.add_argument("--topic", default="", help="Заголовок статьи (опционально)")
    p.add_argument("--angle", default="", help="Угол обзора для инженера (опционально)")
    p.add_argument("--category", default="SMT Equipment", help="Категория (default: SMT Equipment)")
    p.add_argument("--format", dest="format_type", default="review", choices=["review", "insight", "news", "vendor"],
                   help="Формат статьи (default: review)")
    p.add_argument("--type", dest="editorial_type", default="review", choices=["review", "insight", "news", "vendor"],
                   help="Секция сайта: review -> /reviews/, insight -> /insights/ и т.д.")
    p.add_argument("--brief", default="/tmp/pdf_scout_briefs.json", help="Путь сохранения briefs.json")
    p.add_argument("--write", "-w", action="store_true", help="Автоматически запустить Writer, Quality Checker, SEO и Distributor")
    p.add_argument("--article", default="/tmp/pdf_scout_article.txt", help="Путь сохранения article.txt при --write")
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

    brief_payload = build_pdf_topic_brief(
        doc=doc,
        source_url=args.url or doc.source_url,
        custom_title=args.title,
        custom_topic=args.topic,
        custom_angle=args.angle,
        category=args.category,
        format_type=args.format_type,
        editorial_type=args.editorial_type,
    )

    brief_path = Path(args.brief)
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(json.dumps(brief_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print_scout_summary(doc, brief_payload, args.url or doc.source_url)
    print(f"✅ Topic Brief сохранён: {brief_path}")

    if not args.write:
        print(f"\n👉 Чтобы написать статью по этому PDF вручную, выполни:")
        print(f"   python3 agents/agent-02-writer.py --brief {brief_path} --output /tmp/article.txt")
        print(f"   python3 agents/agent-03-seo-doctor.py --meta /tmp/article.meta.json")
        print(f"   python3 agents/agent-04-distributor.py --meta /tmp/article.meta.json")
        return

    meta_data = run_pipeline(
        brief_path=str(brief_path),
        article_path=args.article,
        meta_path=args.meta,
        do_submit=args.submit,
    )

    print(f"\n╔═══════════════════════════════════════════════════════════════╗")
    print(f"║  ✅ СТАТЬЯ ПО PDF НАПИСАНА И ГОТОВА К ДИСТРИБУЦИИ            ║")
    print(f"╚═══════════════════════════════════════════════════════════════╝")
    print(f"  📝 Заголовок:  {meta_data.get('title')}")
    print(f"  📎 Slug:       {meta_data.get('seo', {}).get('slug', 'N/A')}")
    print(f"  🔗 Источник:   {meta_data.get('source_url', args.url)}")
    print(f"  📄 Текст:      {args.article}")
    print(f"  💾 Метаданные: {args.meta}")
    print(f"  📊 Качество:   {meta_data.get('lint_report', {}).get('score', 'N/A')}/100")
    print(f"  📣 LinkedIn:   {'Готов' if meta_data.get('distribution', {}).get('linkedin_post') else 'Нет'}")
    print(f"  💬 Форум:      {'Готов' if meta_data.get('distribution', {}).get('forum_answer') else 'Нет'}")
    print(f"  📧 Email:      {'Готов' if meta_data.get('distribution', {}).get('email_block') else 'Нет'}")
    print(f"───────────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
