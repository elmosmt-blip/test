# SMTInsider Agent Team — запуск в этой среде

Проект скачан из Google Drive, распакован и подготовлен в каталоге:

```bash
/home/user/smtinsider-agent-team
```

## Быстрый старт

```bash
cd /home/user/smtinsider-agent-team
./start-dashboard.sh
```

Dashboard поднимется на:

```text
http://127.0.0.1:8800
```

## Текущий режим

В `.env` включён безопасный sandbox-режим:

```env
LLM_MOCK=1
LLM_MODEL=local-mock-llm
NEON_DATABASE_URL=
```

Это позволяет проверить dashboard и агентов #1–#5 без внешнего LLM-сервера и без базы.
Шаги #6 Publisher и #7 YouTube Scout в общем пайплайне автоматически пропускаются,
пока `NEON_DATABASE_URL` пустой.

## Проверка установки

```bash
cd /home/user/smtinsider-agent-team
./smoke-test.sh
```

Скрипт проверяет:

- Python/import/compile;
- LLM healthcheck;
- агентов #1–#5 в mock-режиме;
- HTTP-ответ dashboard `/status` и `/`.

## Перевод в production

1. Откройте `.env`.
2. Выключите mock:

```env
LLM_MOCK=0
```

3. Укажите реальный OpenAI-compatible LLM endpoint:

```env
LLM_API_BASE=http://localhost:11434/v1
LLM_MODEL=llama3.1:8b
LLM_API_KEY=
```

или OpenRouter/vLLM/LM Studio/etc.

4. Для публикации в Neon PostgreSQL заполните:

```env
NEON_DATABASE_URL=postgresql://user:password@host/db?sslmode=require
```

5. Перезапустите dashboard:

```bash
./start-dashboard.sh
```

## CLI-команды

```bash
# Весь пайплайн
bash run-all.sh

# Отдельные агенты
python3 agents/llm_client.py
python3 agents/agent-01-trend-hunter.py scan
python3 agents/agent-02-writer.py --brief /tmp/smtinsider_briefs.json
python3 agents/agent-03-seo-doctor.py --meta /tmp/smtinsider_article.meta.json
python3 agents/agent-04-distributor.py --meta /tmp/smtinsider_article.meta.json
python3 agents/agent-05-analyst.py pulse
```
