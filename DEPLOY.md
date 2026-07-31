# Инструкция по развёртыванию — 7 агентов на открытых LLM

## 1. Подготовка сервера

```bash
python3 --version  # нужен Python 3.10+
pip install -r requirements.txt
```

## 2. Подними сервер открытой модели (пример — Ollama)

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1:8b
ollama serve &
```

Любой другой OpenAI-совместимый сервер (vLLM, llama.cpp, LM Studio, OpenRouter)
тоже подойдёт — см. `.env.example`.

## 3. Настройка переменных окружения

```bash
cp .env.example .env
# отредактируй .env: LLM_API_BASE, LLM_MODEL, NEON_DATABASE_URL
set -a; source .env; set +a
```

Проверка LLM:
```bash
python3 agents/llm_client.py
# ✅ LLM доступна: http://localhost:11434/v1  модель=llama3.1:8b
```

Проверка БД:
```bash
python3 agents/agent-06-publisher.py check
```

Ожидаемый вывод:
```
✅ Таблица 'news': 22 колонок
✅ Таблица 'videoitem': 8 колонок
```

## 4. Структура файлов

```
smtinsider-agent-team/
├── README.md
├── DEPLOY.md
├── requirements.txt
├── .env.example
├── run-all.sh
├── article-template.txt
├── agents/
│   ├── llm_client.py                # подключение к открытой модели (общий модуль)
│   ├── agent-01-trend-hunter.py     # поиск + LLM выбирает темы
│   ├── agent-02-writer.py           # LLM пишет статью
│   ├── agent-03-seo-doctor.py       # SEO-подготовка (без LLM)
│   ├── agent-04-distributor.py      # LLM готовит посты для дистрибуции
│   ├── agent-05-analyst.py          # метрики из БД + LLM-рекомендации
│   ├── agent-06-publisher.py        # публикация в БД (без LLM)
│   └── agent-07-youtube-scout.py    # поиск видео (без LLM)
```

## 5. Запуск

```bash
# Весь пайплайн (агенты сами передают данные друг другу через файлы)
bash run-all.sh

# Или по шагам:
python3 agents/agent-01-trend-hunter.py scan
python3 agents/agent-02-writer.py --brief /tmp/smtinsider_briefs.json
python3 agents/agent-03-seo-doctor.py --meta /tmp/smtinsider_article.meta.json
python3 agents/agent-04-distributor.py --meta /tmp/smtinsider_article.meta.json
python3 agents/agent-05-analyst.py pulse
python3 agents/agent-06-publisher.py submit --meta /tmp/smtinsider_article.meta.json
python3 agents/agent-07-youtube-scout.py scan --days 60
```

## 6. Шпаргалка

```bash
# Publisher (нужна БД)
python3 agents/agent-06-publisher.py check
python3 agents/agent-06-publisher.py submit --meta article.meta.json
python3 agents/agent-06-publisher.py list
python3 agents/agent-06-publisher.py approve --id 42

# YouTube Scout (нужна БД)
python3 agents/agent-07-youtube-scout.py scan --days 60
python3 agents/agent-07-youtube-scout.py list
python3 agents/agent-07-youtube-scout.py approve --id 48
```

## 7. Ежедневная рутина (cron)

```bash
0 7 * * * cd /home/you/smtinsider-agent-team && set -a && source .env && set +a && \
  bash run-all.sh >> /var/log/smtinsider-pipeline.log 2>&1

# YouTube отдельно, раз в день
30 7 * * * cd /home/you/smtinsider-agent-team && set -a && source .env && set +a && \
  python3 agents/agent-07-youtube-scout.py scan --days 7 >> /var/log/yt-scout.log 2>&1
```

## 8. На что обратить внимание при первом запуске

- Локальные модели на 7-8B параметров могут не очень надёжно следовать
  инструкции "верни строго JSON" — если `llm_client.LLMError: модель не вернула
  валидный JSON` всплывает часто, попробуйте модель покрупнее (13B+) или
  модель, заточенную под function calling/JSON (например, варианты Qwen2.5
  или Llama 3.1 70B+ через OpenRouter).
- `LLM_TIMEOUT` по умолчанию 180 секунд — для слабого железа может не хватить
  на генерацию статьи 800 слов, увеличьте при необходимости.
- DuckDuckGo HTML-поиск в Trend Hunter — best-effort: при недоступности
  просто продолжит работу на знаниях модели (см. `--no-search` флаг).
