# Environment Variables

The real `.env` contains secrets and is not included in the archive.

Use `.env.example` as the template.

## LLM

```env
LLM_MOCK=0
LLM_API_BASE=http://localhost:11434/v1
LLM_API_KEY=
LLM_MODEL=llama3.1:8b
LLM_TIMEOUT=180
LLM_MAX_RETRIES=3
```

For OpenRouter or another cloud provider:

```env
LLM_API_BASE=https://openrouter.ai/api/v1
LLM_API_KEY=...
LLM_MODEL=...
```

## Database

```env
NEON_DATABASE_URL=postgresql://user:password@host/db?sslmode=require
```

## DB write guard

```env
ALLOW_DB_WRITES=0
```

- `0`: read-only/safe mode for dashboard write actions and pipeline write agents.
- `1`: allow Publisher submit, YouTube inserts, approve/delete actions.

## Fresh news collector

```env
NEWS_LOOKBACK_DAYS=30
NEWS_STRICT_FRESH=1
NEWS_VERIFY_DATES=1
NEWS_TIMEZONE=Asia/Jerusalem
NEWS_MAX_RESULTS=5
NEWS_RSS_MAX_ITEMS=20
```

Optional RSS override:

```env
NEWS_RSS_FEEDS=SMT Today|https://smttoday.com/feed/;EMSNow|https://www.emsnow.com/feed/
```
