#!/usr/bin/env python3
"""
Agent #4 — Distributor (реальная версия через LLM)

Старая версия резала текст статьи по индексам строк ("первые 3 строки = интро") —
получался корявый, оборванный текст. Теперь LLM пишет осмысленный пост под
каждый канал, понимая суть статьи целиком.

Важно: этот агент генерирует ТЕКСТ для публикации, но НЕ публикует сам —
постинг в LinkedIn / форум / отправку email нужно подключать отдельно
(LinkedIn API, SMTP/ESP). Здесь — только подготовка контента.

Usage:
  python3 agents/agent-04-distributor.py --title "..." --file article.txt
  python3 agents/agent-04-distributor.py --meta /tmp/smtinsider_article.meta.json
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
import re
import json
import argparse

sys.path.insert(0, os.path.dirname(__file__))
import llm_client

SYSTEM_PROMPT = """Ты — SMM-редактор SMTInsider.com (издание для инженеров SMT-производства).
По тексту статьи подготовь 3 материала для дистрибуции:

1. linkedin_post — 100-200 слов, профессиональный тон, без хайпа и эмодзи-перебора,
   заканчивается вопросом к аудитории (вовлечение в комментарии). До 3-4 релевантных хэштегов.
2. forum_answer — короткий разбор сути (для SMTnet/Reddit-форумов инженеров),
   3-5 пунктов списком с конкретикой из статьи, без маркетинговых фраз.
3. email_block — заголовок + 1-2 предложения сути + призыв "Читать полностью",
   для блока в рассылке.

Ответь СТРОГО в формате JSON (без пояснений, без markdown-обёртки):
{
  "linkedin_post": "...",
  "forum_answer": "...",
  "email_block": "..."
}
"""


def distribute(title: str, body: str) -> dict:
    user_prompt = f"Заголовок статьи: {title}\n\nТекст статьи:\n{body}"
    return llm_client.ask_json(SYSTEM_PROMPT, user_prompt, max_tokens=1500, temperature=0.6)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--title", help="заголовок статьи (не нужен при --meta)")
    p.add_argument("--file", help="файл статьи (не нужен при --meta)")
    p.add_argument("--meta", help="путь к *.meta.json от agent-02-writer.py")
    p.add_argument("--output", help="сохранить результат в JSON-файл (опционально)")
    args = p.parse_args()

    if args.meta:
        with open(args.meta, encoding="utf-8") as f:
            meta = json.load(f)
        title = meta["title"]
        with open(meta["article_file"], encoding="utf-8") as f:
            body = f.read()
    else:
        if not args.title or not args.file:
            print("❌ Укажи --title и --file, либо --meta"); sys.exit(1)
        title = args.title
        with open(args.file, encoding="utf-8") as f:
            body = f.read()
    print(f"\n📣 Agent #4 — Distributor")
    print(f"   {title}")
    print(f"   Модель: {llm_client.LLM_MODEL}\n")

    try:
        result = distribute(title, body)
    except llm_client.LLMError as e:
        print(f"❌ {e}")
        sys.exit(1)

    print("=" * 55)
    print("📱 LINKEDIN ПОСТ")
    print("=" * 55)
    print(result.get("linkedin_post", "").strip())

    print("\n" + "=" * 55)
    print("💬 ФОРУМ-ОТВЕТ")
    print("=" * 55)
    print(result.get("forum_answer", "").strip())

    print("\n" + "=" * 55)
    print("📧 EMAIL-БЛОК")
    print("=" * 55)
    print(result.get("email_block", "").strip())

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n💾 Сохранено: {args.output}")

    if args.meta:
        # Without this, running the pipeline as `--meta X` (no --output, the
        # normal case in run-all.sh / the dashboard) computed real
        # distribution copy and then threw it away — nothing downstream
        # ever saw it. Persist it into meta.json alongside `seo` (added by
        # agent-03) and `quality_check`/`lint_report` (added by agent-02b),
        # so it survives past this process and is visible in the dashboard.
        meta["distribution"] = result
        with open(args.meta, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        print(f"💾 Сохранено в {args.meta} (ключ \"distribution\")")

    print("\n✅ Distributor завершил. Тексты готовы — публикация в каналы вручную "
          "(или подключи LinkedIn API / SMTP отдельно).")


if __name__ == "__main__":
    main()
