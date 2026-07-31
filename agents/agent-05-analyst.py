#!/usr/bin/env python3
"""
Agent #5 — Analyst (реальная версия, без дефолтов-заглушек)

Старая версия использовала захардкоженные дефолты (--sessions 1250 и т.п.),
никак не связанные с реальностью. Теперь:

  - Контент-метрики (статей создано/опубликовано за период, видео) —
    берутся напрямую из Neon Postgres (news, videoitem).
  - Метрики трафика (sessions, subscribers, tool_ctr) — внешние данные,
    их у этого скрипта нет своего источника (Google Analytics / Plausible /
    что у вас стоит на сайте). Поэтому они ОПЦИОНАЛЬНЫ: либо передаёшь их
    флагами вручную (--sessions ...), либо передаёшь --analytics-json
    с файлом, который сам выгружаешь из своей аналитики, либо просто
    не передаёшь — агент честно скажет "нет данных", а не нарисует цифры.
  - LLM формирует текстовые рекомендации на основе РЕАЛЬНЫХ цифр, а не
    статичный if/else.

Usage:
  export NEON_DATABASE_URL='postgresql://...'
  python3 agents/agent-05-analyst.py pulse
  python3 agents/agent-05-analyst.py pulse --days 1
  python3 agents/agent-05-analyst.py pulse --analytics-json /tmp/ga4_export.json
  python3 agents/agent-05-analyst.py pulse --sessions 1800 --subscribers 24 --tool-ctr 12.3
"""

import sys
import os
import json
import argparse
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import llm_client

DATABASE_URL = os.environ.get("NEON_DATABASE_URL")

SYSTEM_PROMPT = """Ты — аналитик контент-маркетинга для SMTInsider.com (B2B-издание
для инженеров SMT-производства). На основе предоставленных метрик дай 2-4 конкретных,
действенных рекомендации на сегодня. Без воды, без общих фраз вроде "продолжайте
в том же духе" — только то, что реально стоит сделать, исходя из цифр.
Если каких-то данных нет (null) — НЕ выдумывай их и не делай вид, что они есть;
честно укажи, что метрика не отслеживается, и порекомендуй, как её подключить.

Ответь СТРОГО в формате JSON (без пояснений, без markdown-обёртки):
{
  "recommendations": ["рекомендация 1", "рекомендация 2", "..."]
}
"""


def get_content_stats(days: int) -> dict:
    """Реальные данные из БД: сколько статей/видео создано и опубликовано за период."""
    if not DATABASE_URL:
        return {"db_connected": False}

    import psycopg2
    since = datetime.now(timezone.utc) - timedelta(days=days)
    stats = {"db_connected": True}

    # Важно: не используем `with psycopg2.connect(...) as conn` вместе с
    # ручным `conn.close()` внутри блока. Иначе psycopg2 пытается сделать
    # commit/rollback уже после закрытия соединения и падает с
    # `InterfaceError: connection already closed`.
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM news WHERE date >= %s", (since,))
            stats["articles_created"] = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM news WHERE date >= %s AND is_published = true", (since,))
            stats["articles_published"] = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM news WHERE is_published = false")
            stats["drafts_pending"] = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM videoitem WHERE published_at >= %s", (since,))
            stats["videos_created"] = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM videoitem WHERE is_published = false")
            stats["video_drafts_pending"] = cur.fetchone()[0]
    finally:
        conn.close()
    return stats


def _deterministic_recommendations(metrics: dict) -> list:
    """Fallback-рекомендации без внешней LLM: только на основе фактических метрик."""
    recs = []
    if metrics.get("drafts_pending", 0) > 0:
        recs.append(f"Разобрать очередь статей: сейчас {metrics['drafts_pending']} черновиков, чтобы не копить устаревающий контент.")
    if metrics.get("video_drafts_pending", 0) > 0:
        recs.append(f"Проверить видео-черновики: сейчас {metrics['video_drafts_pending']} элементов ждут editorial review.")
    if metrics.get("articles_created", 0) > 0 and metrics.get("articles_published", 0) == 0:
        recs.append("За период есть созданные статьи, но нет опубликованных — нужен ручной editorial approval или причина блокировки публикации.")
    if metrics.get("sessions") is None or metrics.get("subscribers") is None or metrics.get("tool_ctr") is None:
        recs.append("Подключить внешнюю аналитику (sessions/subscribers/tool_ctr), иначе невозможно оценить трафик, конверсию и эффект публикаций.")
    if not recs:
        recs.append("Критичных сигналов по доступным метрикам нет; продолжить мониторинг публикаций, черновиков и видео-очереди.")
    return recs[:4]


def daily_pulse(days: int, sessions, subscribers, tool_ctr, analytics_path: str = None, no_llm: bool = False):
    today = datetime.now().strftime("%d.%m.%Y")

    print(f"\n📊 Agent #5 — Analyst")
    print(f"   Daily Pulse: {today}  (период: последние {days} дн.)\n")

    content = get_content_stats(days)

    analytics = {"sessions": sessions, "subscribers": subscribers, "tool_ctr": tool_ctr}
    if analytics_path:
        with open(analytics_path, encoding="utf-8") as f:
            analytics.update(json.load(f))

    print("╔══════════════════════════════════════════════════════════════╗")
    print("║  SMTInsider — DAILY PULSE")
    print(f"║  {today}")
    print("╚══════════════════════════════════════════════════════════════╝\n")

    print("📈 КОНТЕНТ (из БД):")
    if not content.get("db_connected"):
        print("   ⚠ NEON_DATABASE_URL не задан — данные по контенту недоступны")
    else:
        print(f"   Статей создано:        {content['articles_created']}")
        print(f"   Статей опубликовано:   {content['articles_published']}")
        print(f"   Черновиков в очереди:  {content['drafts_pending']}")
        print(f"   Видео добавлено:       {content['videos_created']}")
        print(f"   Видео-черновиков:      {content['video_drafts_pending']}")

    print("\n📊 ТРАФИК И ВОВЛЕЧЁННОСТЬ (внешняя аналитика):")
    for key, label in [("sessions", "Сессий"), ("subscribers", "Подписок"),
                        ("tool_ctr", "Tool CTR")]:
        val = analytics.get(key)
        if val is None:
            print(f"   {label}: нет данных (не подключена аналитика)")
        else:
            suffix = "%" if key == "tool_ctr" else ""
            print(f"   {label}: {val}{suffix}")

    metrics_for_llm = {**content, **analytics, "period_days": days}
    print("\n🎯 РЕКОМЕНДАЦИИ:")
    if no_llm:
        print("   (--no-llm: детерминированные рекомендации без обращения к LLM)")
        for r in _deterministic_recommendations(metrics_for_llm):
            print(f"   • {r}")
    else:
        try:
            result = llm_client.ask_json(
                SYSTEM_PROMPT,
                "Метрики за период:\n" + json.dumps(metrics_for_llm, ensure_ascii=False, indent=2),
                max_tokens=800,
            )
            for r in result.get("recommendations", []):
                print(f"   • {r}")
        except llm_client.LLMError as e:
            # Fall back to the deterministic recommendations rather than
            # just printing a warning and giving up — the LLM being down
            # shouldn't mean the daily pulse has zero actionable output.
            print(f"   ⚠ LLM недоступна ({e}), показываю детерминированные рекомендации:")
            for r in _deterministic_recommendations(metrics_for_llm):
                print(f"   • {r}")

    print(f"\n✅ Daily Pulse завершён")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["pulse"])
    p.add_argument("--days", type=int, default=1, help="за сколько последних дней считать контент-метрики")
    p.add_argument("--sessions", type=int, default=None)
    p.add_argument("--subscribers", type=int, default=None)
    p.add_argument("--tool-ctr", type=float, default=None)
    p.add_argument("--analytics-json", default=None,
                    help="JSON-файл с внешними метриками (sessions/subscribers/tool_ctr), "
                         "если выгружаешь их из GA4/Plausible отдельно")
    p.add_argument("--no-llm", action="store_true",
                    help="не вызывать LLM; вывести deterministic-рекомендации по фактическим метрикам")
    args = p.parse_args()

    if args.action == "pulse":
        daily_pulse(args.days, args.sessions, args.subscribers, args.tool_ctr, args.analytics_json, no_llm=args.no_llm)
