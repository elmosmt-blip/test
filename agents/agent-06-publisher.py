#!/usr/bin/env python3
"""
Agent Publisher — SMTInsider (Neon PostgreSQL)

Пишет статьи в БД со статусом is_published=false.
Статья видна в админ-дашборде, НЕ на сайте.
Ты читаешь → approve → появляется на сайте.

Таблица: news (22 колонки)
Разделы сайта определяются полем editorial_type (НЕ стирается при approve):
  editorial_type='news'     — /news/
  editorial_type='insight'  — /insights/
  editorial_type='review'   — /reviews/
  editorial_type='vendor'   — /vendors/
  (Video Briefs — submit-video → videoitem)

Usage:
  export NEON_DATABASE_URL='postgresql://user:pass@ep-xxx.neon.tech/neondb?sslmode=require'
  python3 agent-publisher.py check
  python3 agent-publisher.py submit --title "..." --file article.txt --type news
  python3 agent-publisher.py list
  python3 agent-publisher.py approve --id 42
"""

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


import os, re, sys, json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional, List

import section_router
import dedupe

def _friendly_excepthook(exc_type, exc, tb):
    if exc_type is RuntimeError and str(exc).startswith("Duplicate publication blocked"):
        print(f"❌ {exc}")
        sys.exit(1)
    sys.__excepthook__(exc_type, exc, tb)

sys.excepthook = _friendly_excepthook

DATABASE_URL = os.environ.get("NEON_DATABASE_URL")
if not DATABASE_URL:
    print("❌ NEON_DATABASE_URL не задан")
    sys.exit(1)

try:
    import psycopg2, psycopg2.extras
except ImportError:
    os.system("pip install psycopg2-binary -q")
    import psycopg2, psycopg2.extras


@contextmanager
def get_conn():
    """psycopg2 `with conn:` коммитит/роллбэкает транзакцию, но НЕ закрывает
    соединение — это утечка при частых вызовах. Этот контекст-менеджер
    закрывает соединение явно в любом случае."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def check():
    """Проверить соединение и схему."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'news' ORDER BY ordinal_position
            """)
            cols = cur.fetchall()
            print(f"✅ Таблица 'news': {len(cols)} колонок\n")
            for c in cols:
                print(f"  {c[0]:25s} {c[1]:20s} nullable={c[2]:5s}")

            cur.execute("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'videoitem' ORDER BY ordinal_position
            """)
            vcols = cur.fetchall()
            print(f"\n✅ Таблица 'videoitem': {len(vcols)} колонок\n")
            for c in vcols:
                print(f"  {c[0]:25s} {c[1]:20s} nullable={c[2]:5s}")


def slugify(text: str) -> str:
    slug = re.sub(r'[^a-z0-9\s-]', '', text.lower())
    slug = re.sub(r'[\s-]+', '-', slug)
    return slug.strip('-')[:200]


def unique_slug(base: str) -> str:
    slug = base
    n = 1
    with get_conn() as conn:
        with conn.cursor() as cur:
            while True:
                cur.execute("SELECT id FROM news WHERE slug = %s", (slug,))
                if not cur.fetchone():
                    return slug
                n += 1
                slug = f"{base}-{n}"


def html_to_plain(html: str) -> str:
    """Убрать HTML-теги, сохранив markdown/plain-text структуру.

    Старый вариант схлопывал все whitespace в одну строку. Для сайта это ломает
    markdown: `# title` превращался в H1 для всего текста, а `##` показывался
    внутри абзаца. Поэтому сохраняем переносы строк и нормализуем пробелы
    только внутри каждой строки.
    """
    import html as html_lib

    text = html or ""
    # Сохраняем смысловые разрывы для типичных HTML-блоков.
    text = re.sub(r'(?i)<\s*br\s*/?\s*>', '\n', text)
    text = re.sub(r'(?i)</\s*(p|div|h[1-6]|li|ul|ol|blockquote|section|article)\s*>', '\n\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = html_lib.unescape(text).replace('\xa0', ' ')

    # Нормализуем пробелы построчно, но не уничтожаем markdown-параграфы.
    lines = [re.sub(r'[ \t]+', ' ', line).rstrip() for line in text.splitlines()]
    compact = []
    blank = False
    for line in lines:
        if line.strip():
            compact.append(line.strip())
            blank = False
        elif not blank:
            compact.append('')
            blank = True
    return '\n'.join(compact).strip() + '\n'

def build_frontmatter_data(
    tags: Optional[List[str]],
    section_dict: dict,
    source_url: str,
    seo: Optional[dict],
    final_slug: str,
) -> dict:
    """Pure function (no DB, no I/O) that assembles the frontmatter_json
    payload, including the slug-correction fix-up for the `seo` block's
    JSON-LD (see submit()'s docstring: SEO Doctor's slug is provisional,
    computed before Publisher's uniqueness check runs). Extracted from
    submit() specifically so this logic is unit-testable without a database
    connection.
    """
    data = {
        "tags": tags or [],
        "section_routing": section_dict,
        "source_url": source_url,
    }
    if seo:
        corrected_jsonld = seo.get("jsonld", "")
        provisional_slug = seo.get("slug", "")
        if corrected_jsonld and provisional_slug and provisional_slug != final_slug:
            corrected_jsonld = corrected_jsonld.replace(f"/{provisional_slug}", f"/{final_slug}")
        data["seo"] = {
            "meta_description": seo.get("meta_description", ""),
            "jsonld": corrected_jsonld,
            "internal_links": seo.get("internal_links", []),
        }
    return data


def submit(
    title: str,
    content_text: str,
    editorial_type: str = "news",
    category: str = "SMT Equipment",
    source: str = "SMTInsider Editorial",
    summary: str = "",
    author: str = "SMTInsider Editorial",
    tags: Optional[List[str]] = None,
    link: str = "",
    source_url: str = "",
    is_rss: bool = True,
    allow_duplicate: bool = False,
    seo: Optional[dict] = None,
) -> dict:
    """
    Создать публикацию в таблице news.
    Статус: is_published=false → черновик в дашборде.
    editorial_type определяет раздел сайта (news/insight/review/vendor) и не
    стирается при approve.
    content — plain text, без HTML.
    seo — опциональный пакет от agent-03-seo-doctor.py (meta_description,
    jsonld, internal_links). SEO Doctor вычисляет slug ДО того, как Publisher
    проверил его на уникальность в БД — так что slug внутри seo['jsonld']
    может не совпадать с финальным. Этот slug исправляется на актуальный
    перед сохранением, чтобы JSON-LD не ссылался на несуществующий URL.
    """
    if not summary:
        summary = content_text[:200].rstrip() + "…"

    # Normalize category before it hits the DB — the site only shows articles
    # whose category_name matches one of the predefined filter values.
    category = section_router.normalize_category(category)

    section = section_router.decide_section(
        title=title,
        body=content_text,
        category=category,
        tags=tags or [],
        explicit=editorial_type,
        source_url=link,
    )
    editorial_type = section.editorial_type
    source_url = source_url or link

    if not allow_duplicate and os.environ.get("ALLOW_DUPLICATE_PUBLICATIONS", "0").lower() not in {"1", "true", "yes", "on"}:
        idx = dedupe.load_existing_index(DATABASE_URL)
        dup = dedupe.find_duplicate(idx, title=title, urls=[u for u in [link, source_url] if u])
        if dup.is_duplicate:
            raise RuntimeError(
                "Duplicate publication blocked: "
                f"reason={dup.reason}, matched_id={dup.matched_id}, "
                f"matched_slug={dup.matched_slug}, matched_title={dup.matched_title!r}"
            )

    slug = unique_slug(slugify(title))
    now = datetime.now(timezone.utc)
    frontmatter = json.dumps(
        build_frontmatter_data(tags, section.to_dict(), source_url, seo, slug),
        ensure_ascii=False,
    )
    # content_text должен быть plain text, не HTML

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO news
                    (title, content, link, source, date, category_name,
                     is_rss, is_expert, is_published, slug, editorial_type,
                     summary, source_url, author_name, frontmatter_json)
                VALUES
                    (%s, %s, %s, %s, %s, %s,
                     %s, %s, %s, %s, %s,
                     %s, %s, %s, %s)
                RETURNING id;
            """, (
                title, content_text, link, source, now, category,
                is_rss, False, False, slug, editorial_type,  # is_published=false
                summary, source_url, author, frontmatter
            ))
            pid = cur.fetchone()[0]

    return {
        "id": pid,
        "slug": slug,
        "status": "draft",
        "editorial_type": editorial_type,
        "section_path": section.section_path,
        "url": f"{section.section_path}{slug}",
    }


def drafts():
    """Все черновики (is_published=false)."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, title, editorial_type, category_name, date
                FROM news
                WHERE is_published = false
                ORDER BY id DESC
                LIMIT 30
            """)
            return cur.fetchall()


def approve(article_id: int):
    """Только ты. Опубликовать.

    Важно: editorial_type НЕ стираем. Он определяет раздел сайта:
    news / insight / review / vendor.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE news
                SET is_published = true
                WHERE id = %s
            """, (article_id,))
        conn.commit()
    return {"id": article_id, "status": "published"}


def delete(article_id: int):
    """Удалить черновик."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM news WHERE id = %s AND is_published = false", (article_id,))
        conn.commit()
    return {"id": article_id, "deleted": True}


def submit_video(title: str, youtube_url: str, channel: str = "",
                 thumbnail_url: str = "", description: str = ""):
    """Добавить Video Brief (черновик)."""
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO videoitem
                    (title, youtube_url, thumbnail_url, channel,
                     description, published_at, is_published)
                VALUES (%s, %s, %s, %s, %s, %s, false)
                RETURNING id;
            """, (title, youtube_url, thumbnail_url, channel, description, now))
            pid = cur.fetchone()[0]
        conn.commit()
    return {"id": pid, "status": "draft"}


# ───────── CLI ─────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(prog="agent-publisher")
    p.add_argument("action", choices=["check", "submit", "submit-video", "list", "approve", "delete"])
    p.add_argument("--title")
    p.add_argument("--file", help="файл с текстом статьи (plain text)")
    p.add_argument("--meta", help="путь к *.meta.json от agent-02-writer.py — "
                                   "тогда --title/--file/--category/--tags не нужны")
    p.add_argument("--type", dest="etype", default="news",
                   help="news | insight | review | vendor | article")
    p.add_argument("--category", default="")
    p.add_argument("--source", default="SMTInsider Editorial")
    p.add_argument("--author", default="SMTInsider Editorial")
    p.add_argument("--tags")
    p.add_argument("--id", type=int)
    p.add_argument("--youtube")
    p.add_argument("--channel")
    p.add_argument("--source-url", help="primary source URL for duplicate detection")
    p.add_argument("--allow-duplicate", action="store_true", help="allow duplicate publication/draft")

    args = p.parse_args()

    if args.action == "check":
        check()

    elif args.action == "list":
        dd = drafts()
        if not dd:
            print("\n📋 Черновиков нет")
        else:
            print(f"\n📋 Черновики ({len(dd)}):\n")
            for d in dd:
                t = str(d['title'])[:55]
                cat = str(d['category_name'])[:18]
                print(f"  [{d['id']:4d}] {cat} | {t}")

    elif args.action == "submit":
        if args.meta:
            with open(args.meta, encoding="utf-8") as f:
                meta = json.load(f)
            quality = meta.get("quality_check") or {}
            human_override = quality.get("human_override") or {}
            factual_pass = quality.get("approved") and quality.get("factual_verdict") == "pass"
            editorial_override = human_override.get("approved") and human_override.get("reason")
            if not factual_pass and not editorial_override:
                print(
                    "⚠️  Предупреждение: статья не прошла Quality Checker "
                    f"(статус: {quality.get('status', 'quality_check отсутствует')}). "
                    "Публикуется как черновик — review рекомендован."
                )
            with open(meta["article_file"], encoding="utf-8") as f:
                raw = f.read()
            text = html_to_plain(raw)
            brief = meta.get("source_topic_brief", {}) or {}
            primary_source_url = args.source_url or meta.get("source_url") or ""
            if not primary_source_url:
                for src in (brief.get("expanded_sources") or brief.get("sources") or []):
                    if src.get("url"):
                        primary_source_url = src.get("url")
                        break
            r = submit(meta["title"], text,
                       editorial_type=meta.get("editorial_type", "news"),
                       category=meta.get("category", "SMT Equipment"),
                       source=args.source, summary=meta.get("summary", ""),
                       author=args.author, tags=meta.get("tags", []),
                       link=primary_source_url, source_url=primary_source_url,
                       allow_duplicate=args.allow_duplicate,
                       seo=meta.get("seo"))
        else:
            if not args.title or not args.file:
                print("❌ Укажи --title и --file, либо --meta"); sys.exit(1)
            with open(args.file, encoding="utf-8") as f:
                raw = f.read()
            # конвертим в plain text на всякий случай
            text = html_to_plain(raw)
            cat = args.category or "SMT Equipment"
            tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
            r = submit(args.title, text, args.etype, cat, args.source,
                       author=args.author, tags=tags,
                       link=args.source_url or "", source_url=args.source_url or "",
                       allow_duplicate=args.allow_duplicate)
        print(f"\n✅ Черновик создан!")
        print(f"   ID={r['id']}  slug={r['slug']}")
        print(f"   Статус: DRAFT — в дашборде, НЕ на сайте")
        print(f"   👉 Зайди в дашборд → прочитай → approve --id {r['id']}")

    elif args.action == "submit-video":
        if not args.title or not args.youtube:
            print("❌ Укажи --title и --youtube"); sys.exit(1)
        r = submit_video(args.title, args.youtube, args.channel or "")
        print(f"\n✅ Видео в дашборде ID={r['id']}")

    elif args.action == "approve":
        if not args.id: print("❌ Укажи --id"); sys.exit(1)
        r = approve(args.id)
        print(f"✅ Статья {r['id']} опубликована!")

    elif args.action == "delete":
        if not args.id: print("❌ Укажи --id"); sys.exit(1)
        r = delete(args.id)
        print(f"🗑 Черновик {r['id']} удалён")
