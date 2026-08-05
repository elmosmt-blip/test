#!/usr/bin/env python3
"""
agent-00-orchestrator.py — единый оркестратор сбора новостей SMTInsider.

Запускает всех агентов сбора (Trend Hunter, PDF Scout, LinkedIn, YouTube),
мержит результаты, дедуплицирует кросс-агентно и относительно уже
опубликованных статей в БД, и формирует единый файл briefs.json.

Usage:
  python3 agents/agent-00-orchestrator.py
  python3 agents/agent-00-orchestrator.py --skip linkedin --skip youtube
  python3 agents/agent-00-orchestrator.py --db-dedupe  # проверять БД на дубликаты
"""

from __future__ import annotations

import sys
for _name in ("stdout", "stderr"):
    _s = getattr(sys, _name, None)
    if _s and hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AGENTS_DIR = Path(__file__).parent
TMP = Path(tempfile.gettempdir())
BRIEFS_FILE = TMP / "smtinsider_briefs.json"


def _run_agent(name: str, command: list[str], timeout_seconds: int = 600) -> tuple[bool, Path | None]:
    """Запустить агента и вернуть (успех, путь к выходному файлу)."""
    print(f"\n{'='*60}")
    print(f"  🚀 Запуск {name}")
    print(f"  {'='*60}")
    try:
        result = subprocess.run(
            command,
            timeout=timeout_seconds,
            cwd=str(AGENTS_DIR.parent),
        )
        ok = result.returncode == 0
        print(f"  {'✅' if ok else '❌'} {name} завершён (exit {result.returncode})")
        return ok, None
    except subprocess.TimeoutExpired:
        print(f"  ⏰ {name} превысил таймаут {timeout_seconds}с")
        return False, None
    except Exception as e:
        print(f"  ❌ {name} ошибка: {e}")
        return False, None


def _dedupe_cross_agent(briefs: dict[str, Any]) -> dict[str, Any]:
    """Удалить дубликаты между разными агентами внутри одного запуска."""
    topics = briefs.get("topics", [])
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    unique: list[dict[str, Any]] = []

    for topic in topics:
        url = (topic.get("source") or topic.get("source_url") or "").strip().lower()
        title = (topic.get("topic") or topic.get("title") or "").strip().lower()

        if url and url in seen_urls:
            print(f"  🔄 Дубликат по URL пропущен: {topic.get('topic', '')[:80]}")
            continue
        if title:
            # Normalize: remove punctuation, collapse whitespace
            import re
            norm_title = re.sub(r"[^a-z0-9\s]", "", title)
            norm_title = re.sub(r"\s+", " ", norm_title).strip()
            if norm_title and norm_title in seen_titles:
                print(f"  🔄 Дубликат по заголовку пропущен: {topic.get('topic', '')[:80]}")
                continue
            seen_titles.add(norm_title)

        seen_urls.add(url)
        unique.append(topic)

    removed = len(topics) - len(unique)
    if removed:
        print(f"\n  🧹 Кросс-агентная дедупликация: удалено {removed} дубликатов")
    return {**briefs, "topics": unique, "deduped_at": datetime.now(timezone.utc).isoformat(),
            "deduped_removed": removed}


def _db_dedupe(briefs: dict[str, Any]) -> dict[str, Any]:
    """Удалить темы, которые уже опубликованы в БД."""
    try:
        import dedupe
        idx = dedupe.load_existing_index(limit=3000)
        if not idx.rows:
            print("  ⚠️  БД недоступна, дедупликация по БД пропущена")
            return briefs

        topics = briefs.get("topics", [])
        unique: list[dict[str, Any]] = []
        db_dupes = 0
        for topic in topics:
            result = dedupe.duplicate_for_signal(idx, topic)
            if result.is_duplicate:
                print(f"  🗄️  Уже в БД (#{result.matched_id}): {topic.get('topic', '')[:80]}")
                db_dupes += 1
            else:
                unique.append(topic)

        if db_dupes:
            print(f"\n  🗄️  Дедупликация по БД: удалено {db_dupes} уже опубликованных тем")
        return {**briefs, "topics": unique, "db_deduped_at": datetime.now(timezone.utc).isoformat(),
                "db_deduped_removed": db_dupes}
    except Exception as e:
        print(f"  ⚠️  Ошибка дедупликации по БД: {e}")
        return briefs


def main() -> int:
    parser = argparse.ArgumentParser(description="Оркестратор сбора новостей SMTInsider")
    parser.add_argument("--skip", nargs="*", default=[],
                        choices=["trend", "pdf", "linkedin", "youtube", "newsapi"],
                        help="Пропустить указанных агентов")
    parser.add_argument("--db-dedupe", action="store_true",
                        help="Проверить на дубликаты с уже опубликованными статьями в БД")
    parser.add_argument("--output", default=str(BRIEFS_FILE),
                        help="Путь к выходному файлу briefs.json")
    args = parser.parse_args()

    skip = set(args.skip)
    collected_any = False

    # ── Agent #1: Trend Hunter (RSS + News API) ──
    if "trend" not in skip:
        ok, _ = _run_agent(
            "Agent #1 — Trend Hunter",
            [sys.executable, str(AGENTS_DIR / "agent-01-trend-hunter.py"), "scan"]
        )
        collected_any = collected_any or ok

    # ── Agent #1e: NewsAPI Collector ──
    if "newsapi" not in skip and os.environ.get("NEWSAPI_KEY"):
        ok, _ = _run_agent(
            "Agent #1e — NewsAPI Collector",
            [sys.executable, str(AGENTS_DIR / "agent-01e-newsapi-collector.py"),
             "--days", "7", "--max-requests", "80"]
        )
        collected_any = collected_any or ok
    elif "newsapi" not in skip:
        print("  ⏭️  NewsAPI пропущен: нет NEWSAPI_KEY (https://newsapi.org/register)")

    # ── Agent #1b: PDF Scout (requires --file or --url — run manually) ──
    if "pdf" not in skip:
        print("  ⏭️  PDF Scout: укажите --file/--url вручную через agent-01b-pdf-scout.py")

    # ── Agent #1c: LinkedIn Signals ──
    if "linkedin" not in skip and os.environ.get("LINKEDIN_ACCESS_TOKEN"):
        ok, _ = _run_agent(
            "Agent #1c — LinkedIn Signals",
            [sys.executable, str(AGENTS_DIR / "agent-01c-linkedin-signals.py")]
        )
        collected_any = collected_any or ok
    elif "linkedin" not in skip:
        print("  ⏭️  LinkedIn пропущен: нет LINKEDIN_ACCESS_TOKEN")

    # ── Agent #7: YouTube Scout ──
    if "youtube" not in skip and os.environ.get("YOUTUBE_API_KEY"):
        ok, _ = _run_agent(
            "Agent #7 — YouTube Scout",
            [sys.executable, str(AGENTS_DIR / "agent-07-youtube-scout.py")]
        )
        collected_any = collected_any or ok
    elif "youtube" not in skip:
        print("  ⏭️  YouTube пропущен: нет YOUTUBE_API_KEY")

    if not collected_any:
        print("\n❌ Ни один агент не собрал темы")
        return 1

    # ── Мерж и дедупликация ──
    if BRIEFS_FILE.exists():
        briefs = json.loads(BRIEFS_FILE.read_text("utf-8"))
        topics = briefs.get("topics", [])

        # Cross-agent dedupe
        briefs = _dedupe_cross_agent(briefs)

        # DB dedupe (опционально)
        if args.db_dedupe:
            briefs = _db_dedupe(briefs)

        # Сохраняем результат
        output_path = Path(args.output)
        output_path.write_text(json.dumps(briefs, ensure_ascii=False, indent=2), encoding="utf-8")

        total = len(briefs.get("topics", []))
        print(f"\n{'='*60}")
        print(f"  📊 Итого: {total} уникальных тем в {output_path}")
        print(f"  {'='*60}")
        print(f"\n  → python3 agents/run-selected-topics.py --brief {output_path} --indices 0,1,2,... --output-dir /tmp/smtinsider_selected_articles")
    else:
        print(f"\n❌ {BRIEFS_FILE} не найден — агенты не создали выходной файл")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())