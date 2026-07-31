# Quick Start

## 1. Install dependencies

```bash
cd smtinsider-agent-team
python3 -m pip install -r requirements.txt
```

## 2. Create environment file

```bash
cp .env.example .env
```

Fill:

```env
LLM_MOCK=0
LLM_API_BASE=...
LLM_API_KEY=...
LLM_MODEL=...
NEON_DATABASE_URL=...
ALLOW_DB_WRITES=0
NEWS_LOOKBACK_DAYS=30
NEWS_STRICT_FRESH=1
NEWS_VERIFY_DATES=1
```

## 3. Run dashboard

```bash
./start-dashboard.sh
```

Open:

```text
http://127.0.0.1:8800
```

## 4. Safe verification

```bash
./verify-agents-safe.sh
```

This checks agents and dashboard without creating/publishing DB records.

## 5. Fresh-news collection only

```bash
python3 agents/agent-01-trend-hunter.py scan \
  --collect-only \
  --output /tmp/smtinsider_fresh_signals_30d.json \
  --days 30 \
  --strict-fresh \
  --verify-pages
```

## 6. Real write run

Only after review:

```env
ALLOW_DB_WRITES=1
```

Then run the pipeline or selected agents.
