#!/usr/bin/env python3
"""
Agent #3 — SEO Doctor
Готовит SEO для статьи: JSON-LD, slug, мета-описание, перелинковка.

Usage:
  python3 agents/agent-03-seo-doctor.py --title "..." --file article.txt
"""

import sys, re, json
from datetime import datetime

def make_slug(text: str) -> str:
    slug = re.sub(r'[^a-z0-9\s-]', '', text.lower())
    return re.sub(r'[\s-]+', '-', slug).strip('-')[:100]

def make_meta(body: str, summary: str = "") -> str:
    """Если есть готовый summary (от Writer'а) — используем его, иначе
    режем тело по границе слова, а не посередине."""
    src = summary.strip() if summary else re.sub(r'<[^>]+>', '', body).strip()
    if len(src) <= 160:
        return src
    cut = src[:157]
    last_space = cut.rfind(" ")
    if last_space > 100:  # не резать слишком коротко, если пробел далеко не найден
        cut = cut[:last_space]
    return cut.rstrip(",.;: ") + "…"

def make_jsonld(title: str, desc: str, slug: str, category: str) -> str:
    now = datetime.now().strftime("%Y-%m-%d")
    ld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": title,
        "description": desc,
        "author": {"@type": "Organization", "name": "SMTInsider Editorial"},
        "publisher": {
            "@type": "Organization",
            "name": "SMTInsider",
            "logo": {"@type": "ImageObject", "url": "https://www.smtinsider.com/logo.png"},
        },
        "mainEntityOfPage": {"@type": "WebPage", "@id": f"https://www.smtinsider.com/news/{slug}"},
        "datePublished": now,
        "dateModified": now,
        "about": {"@type": "Thing", "name": category},
    }
    return json.dumps(ld, indent=2, ensure_ascii=False)

def find_internal_links(body: str) -> list:
    """Находит упоминания инструментов SMTInsider для перелинковки."""
    tools = {
        "defect diagnostics": "/smt-defect-diagnostics",
        "defect diagnostic": "/smt-defect-diagnostics",
        "smd finder": "/finder",
        "component finder": "/finder",
        "oee calculator": "/tools/smt-line-oee-calculator",
        "reflow profile": "/tools/reflow-profile-builder",
        "solder paste calculator": "/solder-paste-calculator",
        "msl tracker": "/tools/msl-floor-life-tracker",
        "ipc class selector": "/tools/ipc-class-selector",
    }
    found = []
    for keyword, url in tools.items():
        if keyword.lower() in body.lower():
            found.append((keyword, url))
    return found

def optimize(title: str, body: str, category: str = "SMT Equipment", summary: str = ""):
    print(f"\n🔧 Agent #3 — SEO Doctor")
    print(f"   {title}\n")

    slug = make_slug(title)
    meta_desc = make_meta(body, summary)
    ld = make_jsonld(title, meta_desc, slug, category)
    links = find_internal_links(body)

    print(f"📎 Slug (предварительный, финальный назначит Publisher): {slug}")
    print(f"📝 Meta:           {meta_desc} ({len(meta_desc)} симв.)")
    print(f"🔗 Внутренние ссылки:")
    if links:
        for kw, url in links:
            print(f"     → {kw}: {url}")
    else:
        print(f"     (не найдено — добавь упоминание инструмента в текст)")

    print(f"\n📋 JSON-LD:\n{ld}")
    print(f"\n✅ SEO-пакет готов")
    return {
        "slug": slug,
        "meta_description": meta_desc,
        "jsonld": ld,
        "internal_links": [{"keyword": kw, "url": url} for kw, url in links],
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--title", help="заголовок (не нужен, если указан --meta)")
    p.add_argument("--file", help="файл статьи (plain text)")
    p.add_argument("--meta", help="путь к *.meta.json от agent-02-writer.py — "
                                   "тогда --title/--file/--category не нужны")
    p.add_argument("--category", default="SMT Equipment")
    args = p.parse_args()

    if args.meta:
        with open(args.meta, encoding="utf-8") as f:
            meta = json.load(f)
        with open(meta["article_file"], encoding="utf-8") as f:
            body = f.read()
        seo = optimize(meta["title"], body, meta.get("category", "SMT Equipment"),
                        summary=meta.get("summary", ""))
        # Persist the SEO package into meta.json instead of only printing it
        # to the console — otherwise agent-06-publisher.py has no way to see
        # it, and this whole step's output was previously thrown away.
        meta["seo"] = seo
        with open(args.meta, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        print(f"\n💾 SEO-пакет сохранён в {args.meta} (ключ \"seo\")")
        print(f"   → python3 agents/agent-04-distributor.py --meta {args.meta}")
        print(f"   → python3 agents/agent-06-publisher.py submit --meta {args.meta}")
    else:
        if not args.title:
            print("❌ Укажи --title (или --meta с файлом от Writer'а)"); sys.exit(1)
        body = ""
        if args.file:
            with open(args.file) as f:
                body = f.read()
        optimize(args.title, body, args.category)
