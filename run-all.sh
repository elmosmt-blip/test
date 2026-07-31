#!/bin/bash
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"
if [ -f .env ]; then set -a; source .env; set +a; fi
# Полный пайплайн: 7 агентов, реально связанных между собой через файлы
# (Trend Hunter -> briefs.json -> Writer -> article.txt + meta.json -> остальные)
set -e

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  SMTInsider — Команда 7 агентов                            ║"
echo "║  $(date)                               ║"
echo "╚══════════════════════════════════════════════════════════════╝"

: "${LLM_API_BASE:?Задай LLM_API_BASE, например http://localhost:11434/v1 (Ollama)}"
: "${LLM_MODEL:?Задай LLM_MODEL, например llama3.1:8b}"

BRIEFS_FILE="/tmp/smtinsider_briefs.json"

echo ""
echo "━━━ ШАГ 1/7: Trend Hunter ━━━"
TREND_ARGS=(scan --output "$BRIEFS_FILE" --max-topics "${NEWS_MAX_TOPICS:-5}" --days "${NEWS_LOOKBACK_DAYS:-30}" --strict-fresh --verify-pages)
python3 agents/agent-01-trend-hunter.py "${TREND_ARGS[@]}"

# Сколько тем реально пришло от Trend Hunter (может быть меньше max-topics,
# если свежих сигналов не хватило) — пишем статью на каждую тему, а не только
# на одну. Так количество статей за прогон растёт вместе с реестром источников,
# а не остаётся жёстко зафиксированным на 1.
TOPIC_COUNT=$(python3 -c "import json; print(len(json.load(open('$BRIEFS_FILE')).get('topics', [])))" 2>/dev/null || echo 0)
if [ "$TOPIC_COUNT" -eq 0 ]; then
  echo "❌ Trend Hunter не нашёл ни одной темы — пайплайн остановлен."
  exit 1
fi
echo "  → Тем в брифе: $TOPIC_COUNT. Пишу по одной статье на каждую."

ARTICLES_WRITTEN=()
for i in $(seq 0 $((TOPIC_COUNT - 1))); do
  ARTICLE_FILE_I="/tmp/smtinsider_article_${i}.txt"
  META_FILE_I="/tmp/smtinsider_article_${i}.meta.json"

  echo ""
  echo "━━━ ШАГ 2/7: Writer (тема $((i + 1))/$TOPIC_COUNT) ━━━"
  python3 agents/agent-02-writer.py --brief "$BRIEFS_FILE" --pick "$i" --output "$ARTICLE_FILE_I"

  echo ""
  echo "━━━ ШАГ 2b/7: Quality Checker (тема $((i + 1))/$TOPIC_COUNT) ━━━"
  python3 agents/agent-02b-quality-checker.py --meta "$META_FILE_I" --threshold "${QUALITY_THRESHOLD:-75}"

  echo ""
  echo "━━━ ШАГ 3/7: SEO Doctor (тема $((i + 1))/$TOPIC_COUNT) ━━━"
  python3 agents/agent-03-seo-doctor.py --meta "$META_FILE_I"

  echo ""
  echo "━━━ ШАГ 4/7: Distributor (тема $((i + 1))/$TOPIC_COUNT) ━━━"
  python3 agents/agent-04-distributor.py --meta "$META_FILE_I"

  ARTICLES_WRITTEN+=("$ARTICLE_FILE_I")
done

echo ""
echo "━━━ ШАГ 5/7: Analyst ━━━"
python3 agents/agent-05-analyst.py pulse --days 1

if [ -n "${NEON_DATABASE_URL:-}" ] && [ "${ALLOW_DB_WRITES:-0}" = "1" ]; then
  echo ""
  echo "━━━ ШАГ 6/7: Publisher (${TOPIC_COUNT} статей) ━━━"
  for i in $(seq 0 $((TOPIC_COUNT - 1))); do
    META_FILE_I="/tmp/smtinsider_article_${i}.meta.json"
    python3 agents/agent-06-publisher.py submit --meta "$META_FILE_I"
  done
  echo "  👉 python3 agents/agent-06-publisher.py list"
  echo "  👉 python3 agents/agent-06-publisher.py approve --id [ID]"

  echo ""
  echo "━━━ ШАГ 7/7: YouTube Scout ━━━"
  python3 agents/agent-07-youtube-scout.py scan --days "${NEWS_LOOKBACK_DAYS:-30}"
else
  echo ""
  echo "━━━ ШАГ 6-7/7: Publisher + YouTube Scout — SAFE-SKIP ━━━"
  if [ -z "${NEON_DATABASE_URL:-}" ]; then
    echo "  ⚠ NEON_DATABASE_URL не задан."
  else
    echo "  ⚠ БД подключена, но запись заблокирована: ALLOW_DB_WRITES=${ALLOW_DB_WRITES:-0}."
  fi
  echo "  Это защита от случайного создания/публикации тестового контента."
  echo "  Для реального запуска явно поставь ALLOW_DB_WRITES=1."
fi

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ПАЙПЛАЙН ЗАВЕРШЁН                                          ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Статей написано: $TOPIC_COUNT"
for f in "${ARTICLES_WRITTEN[@]}"; do
  echo "  - $f"
done
