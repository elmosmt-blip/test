#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

export PATH="$HOME/.local/bin:$PATH"
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

echo "== Python =="
python3 --version

echo "== Import/compile check =="
python3 -m compileall -q agents dashboard

echo "== LLM healthcheck =="
python3 agents/llm_client.py

echo "== Agent #1 mock scan =="
python3 agents/agent-01-trend-hunter.py scan --no-search --output /tmp/smtinsider_briefs.json --max-topics 2

echo "== Agent #2 writer =="
python3 agents/agent-02-writer.py --brief /tmp/smtinsider_briefs.json --output /tmp/smtinsider_article.txt

echo "== Agent #3 SEO =="
python3 agents/agent-03-seo-doctor.py --meta /tmp/smtinsider_article.meta.json >/tmp/smtinsider_seo.log

echo "== Agent #4 distributor =="
python3 agents/agent-04-distributor.py --meta /tmp/smtinsider_article.meta.json >/tmp/smtinsider_distributor.log

echo "== Agent #5 analyst =="
python3 agents/agent-05-analyst.py pulse --days 1 >/tmp/smtinsider_analyst.log

echo "== Dashboard HTTP smoke =="
python3 -m uvicorn dashboard.app:app --host 127.0.0.1 --port 8898 >/tmp/smtinsider_uvicorn.log 2>&1 &
pid=$!
trap 'kill $pid >/dev/null 2>&1 || true' EXIT
sleep 2
curl -fsS http://127.0.0.1:8898/status | python3 -m json.tool
curl -fsS http://127.0.0.1:8898/ >/dev/null

echo "✅ Smoke-test passed"
