#!/usr/bin/env python3
"""Run Writer → Quality → SEO → Distributor for selected brief indices.

Publication remains a manual Control Room action after factual review.
"""
from __future__ import annotations

import sys
for _name in ("stdout", "stderr"):
    _stream = getattr(sys, _name, None)
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--brief", required=True)
    parser.add_argument("--indices", required=True, help="comma-separated current topic indices")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    brief_path = Path(args.brief)
    topics = json.loads(brief_path.read_text(encoding="utf-8")).get("topics", [])
    try:
        indices = sorted({int(value) for value in args.indices.split(",") if value.strip()})
    except ValueError:
        print("❌ Некорректные индексы выбранных тем")
        return 2
    invalid = [index for index in indices if index < 0 or index >= len(topics)]
    if invalid or not indices:
        print(f"❌ Индексы тем вне диапазона: {invalid or 'пустой выбор'}")
        return 2

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    agents = Path(__file__).parent
    failures = 0
    for number, index in enumerate(indices, 1):
        topic = str(topics[index].get("topic", "Без названия"))
        article_path = output_dir / f"selected_topic_{index}.txt"
        meta_path = article_path.with_suffix(".meta.json")
        print(f"\n━━━ {number}/{len(indices)}: {topic} ━━━", flush=True)
        commands = [
            [sys.executable, str(agents / "agent-02-writer.py"), "--brief", str(brief_path), "--pick", str(index), "--output", str(article_path)],
            [sys.executable, str(agents / "agent-02b-quality-checker.py"), "--meta", str(meta_path)],
            [sys.executable, str(agents / "agent-03-seo-doctor.py"), "--meta", str(meta_path)],
            [sys.executable, str(agents / "agent-04-distributor.py"), "--meta", str(meta_path)],
        ]
        if os.environ.get("NEON_DATABASE_URL") and os.environ.get("ALLOW_DB_WRITES", "").lower() in {"1", "true", "yes", "on"}:
            # Publisher creates an unpublished draft. Human approval remains
            # the only action that makes it public.
            commands.append([sys.executable, str(agents / "agent-06-publisher.py"), "submit", "--meta", str(meta_path)])
        for command in commands:
            result = subprocess.run(command)
            if result.returncode:
                failures += 1
                print(f"❌ Тема #{index} остановлена на {Path(command[1]).name} (код {result.returncode})", flush=True)
                break
        else:
            print(f"✅ Тема #{index} завершена и готова к ручному review/publish", flush=True)
    if failures:
        print(f"⚠ Завершено с {failures} остановленными темами; остальные drafts доступны для review.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
