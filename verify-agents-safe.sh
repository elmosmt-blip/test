#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"
if [ -f .env ]; then set -a; source .env; set +a; fi

export LLM_MOCK=1
export ALLOW_DB_WRITES=0

BRIEFS="/tmp/smtinsider_safe_briefs.json"
ARTICLE="/tmp/smtinsider_safe_article.txt"
META="/tmp/smtinsider_safe_article.meta.json"
LOGDIR="/tmp/smtinsider-safe-verify"
mkdir -p "$LOGDIR"

printf 'SAFE VERIFY: no DB writes, no approve, no publish\n'

python3 -m compileall -q agents dashboard
python3 agents/llm_client.py >"$LOGDIR/00_llm.log"
python3 agents/agent-01-trend-hunter.py scan --no-search --output "$BRIEFS" --max-topics 1 >"$LOGDIR/01_trend.log"
python3 agents/agent-02-writer.py --brief "$BRIEFS" --output "$ARTICLE" >"$LOGDIR/02_writer.log"
python3 agents/agent-03-seo-doctor.py --meta "$META" >"$LOGDIR/03_seo.log"
python3 agents/agent-04-distributor.py --meta "$META" >"$LOGDIR/04_distributor.log"
python3 agents/agent-05-analyst.py pulse --days 1 >"$LOGDIR/05_analyst.log"
python3 agents/agent-06-publisher.py check >"$LOGDIR/06_publisher_check.log"
python3 agents/agent-06-publisher.py list >"$LOGDIR/06_publisher_list.log"
python3 agents/agent-07-youtube-scout.py list >"$LOGDIR/07_youtube_list.log"

python3 -m uvicorn dashboard.app:app --host 127.0.0.1 --port 8897 >"$LOGDIR/dashboard.log" 2>&1 &
pid=$!
trap 'kill $pid >/dev/null 2>&1 || true' EXIT
sleep 2
curl -fsS http://127.0.0.1:8897/status >"$LOGDIR/dashboard_status.json"
curl -fsS http://127.0.0.1:8897/drafts >"$LOGDIR/dashboard_drafts.json"
# Verify write-agent buttons are blocked in safe mode.
code=$(curl -s -o "$LOGDIR/dashboard_run6_block.json" -w '%{http_code}' -X POST http://127.0.0.1:8897/run/6)
if [ "$code" != "403" ]; then
  echo "ERROR: dashboard /run/6 was not blocked, code=$code" >&2
  exit 1
fi

python3 - <<'PY'
from pathlib import Path
import json
logdir=Path('/tmp/smtinsider-safe-verify')
status=json.loads((logdir/'dashboard_status.json').read_text())
drafts=json.loads((logdir/'dashboard_drafts.json').read_text())
print('✅ Safe verification passed')
print('  Agents #1-#5: executed with temp files only')
print('  Agent #6: check/list only, no submit')
print('  Agent #7: list only, no scan')
print('  Dashboard: status/drafts OK')
print('  Dashboard write action /run/6 blocked:', True)
print('  DB connected:', bool(status.get('db_connected')))
print('  DB writes allowed:', bool(status.get('allow_db_writes')))
print('  Drafts visible:', len(drafts.get('drafts', [])))
PY
