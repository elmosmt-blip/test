# SMTInsider.com — Команда 7 Агентов на открытых LLM

**Проект:** [SMTInsider.com](https://www.smtinsider.com) — Engineering Intelligence for SMT Production Teams
**Бэкенд:** FastAPI + Neon PostgreSQL
**LLM:** любая открытая модель через OpenAI-совместимый API (Ollama / vLLM / llama.cpp / LM Studio / OpenRouter)
**Таблицы:** `news` (22 колонки) — текстовые публикации, `videoitem` (8 колонок) — видео

---

## Что реально делает LLM, а что — детерминированный код

| # | Агент | Использует LLM? | Что делает |
|:-:|-------|:---:|-----------|
| 1 | Trend Hunter | ✅ | реальный поиск (DuckDuckGo) → LLM выбирает темы → JSON |
| 2 | Writer | ✅ | LLM пишет статью по теме/брифу → текст + meta.json |
| 3 | SEO Doctor | ❌ (не нужно) | slug, meta, JSON-LD, перелинковка — чистая логика |
| 4 | Distributor | ✅ | LLM пишет посты под LinkedIn/форум/email по смыслу статьи |
| 5 | Analyst | частично | реальные метрики из БД + LLM формирует рекомендации |
| 6 | Publisher | ❌ (не нужно) | запись в Neon Postgres, черновик → approve |
| 7 | YouTube Scout | ❌ (не нужно) | поиск видео через yt-dlp, без API-ключа |

Раньше агенты #1, #2, #5 были демо-заглушками с захардкоженным выводом — теперь это
исправлено, все реально обращаются к LLM-серверу, который вы укажете сами.

---

## Архитектура и передача данных между агентами

Агенты больше не полагаются на ручное копирование темы в CLI — данные передаются файлами:

```
#1 TREND HUNTER ──→ briefs.json ──→ #2 WRITER ──→ article.txt + meta.json
   (поиск + LLM)                       (LLM)              │
                                                            ├──→ #3 SEO DOCTOR  (читает meta.json)
                                                            ├──→ #4 DISTRIBUTOR (читает meta.json, LLM)
                                                            └──→ #6 PUBLISHER   (читает meta.json) → Neon DB
                                                                     is_published=false → ждёт approve

#5 ANALYST — реальные метрики из Neon + LLM-рекомендации (независимый шаг)
#7 YOUTUBE SCOUT — поиск видео → videoitem (is_published=false)
```

---

## Подключение открытой модели

Любой сервер с OpenAI-совместимым `/v1/chat/completions` подойдёт. Примеры:

```bash
# Ollama (проще всего локально)
ollama pull llama3.1:8b
ollama serve
export LLM_API_BASE="http://localhost:11434/v1"
export LLM_MODEL="llama3.1:8b"

# vLLM
python -m vllm.entrypoints.openai.api_server --model meta-llama/Llama-3.1-8B-Instruct
export LLM_API_BASE="http://localhost:8000/v1"
export LLM_MODEL="meta-llama/Llama-3.1-8B-Instruct"

# OpenRouter (облако, открытые модели, нужен ключ)
export LLM_API_BASE="https://openrouter.ai/api/v1"
export LLM_API_KEY="sk-or-..."
export LLM_MODEL="meta-llama/llama-3.1-70b-instruct"
```

Проверка подключения:
```bash
python3 agents/llm_client.py
```

Полный список переменных — в `.env.example`.

---

## Agent #1 — Trend Hunter
**Файл:** `agents/agent-01-trend-hunter.py`
- Реально ищет в DuckDuckGo (без API-ключа) по 7 SMT-запросам
- Передаёт сигналы в LLM, та выбирает 1-3 темы и отдаёт строгий JSON
- Сохраняет в `briefs.json` для Writer'а
```bash
python3 agents/agent-01-trend-hunter.py scan
python3 agents/agent-01-trend-hunter.py scan --no-search   # без реального поиска, только LLM
```

## Agent #2 — Writer
**Файл:** `agents/agent-02-writer.py`
- Берёт тему из `briefs.json` (или `--topic` вручную)
- LLM пишет статью 500-900 слов в стиле SMTInsider
- Сохраняет `article.txt` (plain text) + `article.meta.json` (title/category/tags/summary)
```bash
python3 agents/agent-02-writer.py --brief /tmp/smtinsider_briefs.json
python3 agents/agent-02-writer.py --topic "Своя тема" --output /tmp/article.txt
```

## Agent #3 — SEO Doctor
**Файл:** `agents/agent-03-seo-doctor.py`
- Slug, meta-описание (обрезка по границе слова), JSON-LD (Article + publisher + image)
- Перелинковка на инструменты SMTInsider
```bash
python3 agents/agent-03-seo-doctor.py --meta /tmp/smtinsider_article.meta.json
```

## Agent #4 — Distributor
**Файл:** `agents/agent-04-distributor.py`
- LLM пишет LinkedIn-пост, форум-ответ, email-блок — понимая статью целиком,
  а не нарезая её по индексам строк
- Публикация в каналы — вручную (или подключите LinkedIn API / SMTP сами)
```bash
python3 agents/agent-04-distributor.py --meta /tmp/smtinsider_article.meta.json
```

## Agent #5 — Analyst
**Файл:** `agents/agent-05-analyst.py`
- Контент-метрики — реальные, из Neon (статьи/видео за период)
- Трафик/подписки/CTR — внешние данные, опциональны (`--sessions` и т.д. или `--analytics-json`);
  если не переданы — агент честно говорит "нет данных", а не рисует цифры
- LLM формирует рекомендации на основе фактических цифр
```bash
python3 agents/agent-05-analyst.py pulse
python3 agents/agent-05-analyst.py pulse --sessions 1800 --subscribers 24 --tool-ctr 12.3
```

## Agent #6 — Publisher
**Файл:** `agents/agent-06-publisher.py`
- Пишет в Neon Postgres, `is_published=false` — черновик, не на сайте
- `approve --id X` — только вы публикуете
```bash
python3 agents/agent-06-publisher.py check
python3 agents/agent-06-publisher.py submit --meta /tmp/smtinsider_article.meta.json
python3 agents/agent-06-publisher.py list
python3 agents/agent-06-publisher.py approve --id 42
python3 agents/agent-06-publisher.py delete --id 42
```

## Agent #7 — YouTube Scout
**Файл:** `agents/agent-07-youtube-scout.py`
- Без API-ключа (yt-dlp), фильтр по дате, `is_published=false` — черновик
```bash
python3 agents/agent-07-youtube-scout.py scan --days 60
python3 agents/agent-07-youtube-scout.py list
python3 agents/agent-07-youtube-scout.py approve --id 48
python3 agents/agent-07-youtube-scout.py cleanup
```

---

## Запуск всего пайплайна

```bash
cp .env.example .env   # заполни и сделай export, или используй direnv/dotenv
bash run-all.sh
```

`run-all.sh` сам передаёт результат Trend Hunter в Writer, а meta.json — во все
последующие агенты. Шаги 6-7 (запись в БД) выполняются только если задан
`NEON_DATABASE_URL`.

## Установка

```bash
pip install -r requirements.txt
```

---

## Что исправлено по сравнению с первой версией

- Агенты #1, #2, #5 раньше выдавали захардкоженный демо-вывод вне зависимости
  от входных данных — теперь реально работают через LLM
- `agent-06-publisher.py`: `editorial_type` раньше всегда писался как `"NULL_TEMP"`
  независимо от `--type` — исправлено
- Утечка соединений с БД (`with psycopg2.connect()` не закрывает соединение) —
  исправлено явным `conn.close()` во всех функциях
- `except: pass` в YouTube Scout молча гасил все ошибки поиска — теперь логируются
- Meta-описание для SEO обрезалось посередине слова — теперь по границе слова
- JSON-LD дополнен `publisher`/`image`/`mainEntityOfPage` для rich results
- Связность пайплайна: данные между агентами передаются файлами (`briefs.json`,
  `meta.json`), а не вручную через CLI-аргументы
