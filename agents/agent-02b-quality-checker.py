#!/usr/bin/env python3
"""
Agent #2b — Quality Checker (NEW)

Проверяет статью после Writer'а и до публикации:
 - Оценивает по 4 критериям (фактичность, инженерная ценность, качество текста, SEO)
 - Если score < 70 — возвращает улучшенную версию
 - Если score >= 70 — одобряет и пропускает дальше

Usage:
  python3 agents/agent-02b-quality-checker.py --meta /tmp/article.meta.json
  python3 agents/agent-02b-quality-checker.py --meta /tmp/article.meta.json --threshold 75
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

import os
import json
import argparse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import llm_client

_PROMPT_FILE = os.path.join(os.path.dirname(__file__), "prompts", "quality_checker.txt")
if os.path.exists(_PROMPT_FILE):
    with open(_PROMPT_FILE, encoding="utf-8") as _f:
        SYSTEM_PROMPT = _f.read()
else:
    SYSTEM_PROMPT = "Ты — редактор. Проверь статью. Ответь в JSON: {score, approved, issues, title, body, summary, tags}"


def check_article(title: str, body: str, brief: dict, summary: str = "") -> dict:
    user_prompt = f"""Проверь эту статью для SMTInsider:

ЗАГОЛОВОК: {title}

SUMMARY: {summary}

ТЕКСТ:
{body}

ИСХОДНЫЙ БРИФ (что было в источниках):
{json.dumps(brief, ensure_ascii=False, indent=2)}

Оцени строго. Если нашёл воду, клише или несоответствие брифу — исправь."""
    return llm_client.ask_json(SYSTEM_PROMPT, user_prompt, max_tokens=4000, temperature=0.4)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--meta", required=True, help="путь к *.meta.json от agent-02-writer.py")
    p.add_argument("--threshold", type=int, default=75, help="минимальный score для одобрения (default: 75)")
    p.add_argument("--dry-run", action="store_true", help="только показать оценку, не изменять файлы")
    args = p.parse_args()

    with open(args.meta, encoding="utf-8") as f:
        meta = json.load(f)

    article_file = meta.get("article_file", "")
    if not article_file or not os.path.exists(article_file):
        print(f"❌ Файл статьи не найден: {article_file}")
        sys.exit(1)

    with open(article_file, encoding="utf-8") as f:
        body = f.read()

    title = meta.get("title", "")
    summary = meta.get("summary", "")
    brief = meta.get("source_topic_brief", {})

    print(f"\n🔍 Agent #2b — Quality Checker")
    print(f"   Статья: {title}")
    print(f"   Проверяю...\n")

    try:
        result = check_article(title, body, brief, summary)
    except llm_client.LLMError as e:
        print(f"❌ {e}")
        sys.exit(1)

    score = result.get("score", 0)
    approved = result.get("approved", score >= args.threshold)
    issues = result.get("issues", [])
    breakdown = result.get("breakdown", {})

    print(f"📊 Score: {score}/100")
    if breakdown:
        for k, v in breakdown.items():
            print(f"   {k}: {v}/25")
    if issues:
        print(f"\n⚠️  Проблемы:")
        for issue in issues:
            print(f"   • {issue}")

    if score < args.threshold:
        print(f"\n✏️  Score < {args.threshold} — применяю улучшения...")
        if not args.dry_run:
            new_title = result.get("title", title)
            new_body = result.get("body", body)
            new_summary = result.get("summary", summary)
            new_tags = result.get("tags", meta.get("tags", []))

            # Save improved article
            with open(article_file, "w", encoding="utf-8") as f:
                f.write(f"{new_title}\n\n{new_body.strip()}\n")

            # Update meta
            meta["title"] = new_title
            meta["summary"] = new_summary
            meta["tags"] = new_tags
            meta["quality_check"] = {
                "score": score,
                "breakdown": breakdown,
                "issues": issues,
                "improved": True,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(args.meta, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            print(f"✅ Статья улучшена и сохранена")
            print(f"   Новый заголовок: {new_title}")
    else:
        print(f"\n✅ APPROVED (score {score} >= {args.threshold})")
        if not args.dry_run:
            meta["quality_check"] = {
                "score": score,
                "breakdown": breakdown,
                "issues": issues,
                "improved": False,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(args.meta, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"\n   → python3 agents/agent-03-seo-doctor.py --meta {args.meta}")
    print(f"   → python3 agents/agent-06-publisher.py submit --meta {args.meta}")


if __name__ == "__main__":
    main()
