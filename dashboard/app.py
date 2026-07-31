#!/usr/bin/env python3
"""
SMTInsider Dashboard — FastAPI backend
Запуск: cd smtinsider-agent-team && uvicorn dashboard.app:app --reload --port 8800
"""

import asyncio
import json
import os
import sys
# Ensure UTF-8 console output on Windows (prevent UnicodeEncodeError for emojis/box chars)
for _s in ("stdout", "stderr"):
    _stream = getattr(sys, _s, None)
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            try:
                _stream.reconfigure(errors="replace")
            except Exception:
                pass

import uuid
import time
import re
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, AsyncGenerator

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

ROOT = Path(__file__).parent.parent

# Dashboard сам подхватывает .env из корня проекта. Так запуск `./start-dashboard.sh`
# и прямой `python -m uvicorn dashboard.app:app` ведут себя одинаково.
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

import tempfile
AGENTS_DIR = ROOT / "agents"
_TMP = Path(tempfile.gettempdir())
BRIEFS_FILE = _TMP / "smtinsider_briefs.json"
ARTICLE_FILE = _TMP / "smtinsider_article.txt"
META_FILE = _TMP / "smtinsider_article.meta.json"

app = FastAPI(title="SMTInsider Dashboard")

# ── Хранилище активных запусков (run_id → asyncio.Queue) ──────────────────────
_runs: dict[str, asyncio.Queue] = {}
_agent_status: dict[str, str] = {str(i): "idle" for i in range(1, 8)}
_pipeline_status: str = "idle"


# ══════════════════════════════════════════════════════════════════════════════
# Запуск агента как subprocess с потоковой передачей stdout/stderr
# ══════════════════════════════════════════════════════════════════════════════

import sys
PYTHON_CMD = "python" if sys.platform == "win32" else "python3"

AGENT_CMDS = {
    "1": [PYTHON_CMD, str(AGENTS_DIR / "agent-01-trend-hunter.py"), "scan", "--days", os.environ.get("NEWS_LOOKBACK_DAYS", "30"), "--strict-fresh", "--verify-pages", "--output", str(BRIEFS_FILE)],
    "1b": [PYTHON_CMD, str(AGENTS_DIR / "agent-01b-pdf-scout.py"), "--url", "https://online.fliphtml5.com/kwnhb/fakj/", "--format", "magazine", "--max-topics", "3", "--brief", str(BRIEFS_FILE)],
    "2": [PYTHON_CMD, str(AGENTS_DIR / "agent-02-writer.py"),
          "--brief", str(BRIEFS_FILE), "--output", str(ARTICLE_FILE)],
    "2b": [PYTHON_CMD, str(AGENTS_DIR / "agent-02b-quality-checker.py"),
           "--meta", str(META_FILE), "--threshold", os.environ.get("QUALITY_THRESHOLD", "75")],
    "3": [PYTHON_CMD, str(AGENTS_DIR / "agent-03-seo-doctor.py"),
          "--meta", str(META_FILE)],
    "4": [PYTHON_CMD, str(AGENTS_DIR / "agent-04-distributor.py"),
          "--meta", str(META_FILE)],
    "5": [PYTHON_CMD, str(AGENTS_DIR / "agent-05-analyst.py"), "pulse"],
    "6": [PYTHON_CMD, str(AGENTS_DIR / "agent-06-publisher.py"),
          "submit", "--meta", str(META_FILE)],
    "7": [PYTHON_CMD, str(AGENTS_DIR / "agent-07-youtube-scout.py"),
          "scan", "--days", os.environ.get("NEWS_LOOKBACK_DAYS", "30")],
}


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


def _send(q: asyncio.Queue, event: str, data: dict):
    """Кладёт SSE-событие в очередь."""
    try:
        q.put_nowait({"event": event, "data": data})
    except asyncio.QueueFull:
        pass


async def _run_agent(agent_id: str, run_id: str, extra_args: list = None) -> int:
    """Запускает агента как subprocess, шлёт вывод в SSE-очередь."""
    q = _runs[run_id]
    cmd = AGENT_CMDS[agent_id] + (extra_args or [])
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}

    _send(q, "log", {"agent": agent_id, "line": f"▶ {' '.join(cmd)}"})
    _agent_status[agent_id] = "running"
    _send(q, "status", {"agent": agent_id, "state": "running"})

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
        cwd=str(ROOT),
    )

    async def read_stdout():
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            _send(q, "log", {"agent": agent_id, "line": text})

    await read_stdout()
    await proc.wait()

    state = "done" if proc.returncode == 0 else "error"
    _agent_status[agent_id] = state
    _send(q, "status", {"agent": agent_id, "state": state, "code": proc.returncode})
    return proc.returncode


async def _run_pipeline(run_id: str):
    """Запускает весь пайплайн последовательно."""
    global _pipeline_status
    _pipeline_status = "running"
    q = _runs[run_id]
    _send(q, "pipeline", {"state": "running"})

    steps = ["1", "2", "2b", "3", "4", "5"]
    if os.environ.get("NEON_DATABASE_URL") and _env_truthy("ALLOW_DB_WRITES"):
        steps += ["6", "7"]
    else:
        reason = "NEON_DATABASE_URL не задан" if not os.environ.get("NEON_DATABASE_URL") else "ALLOW_DB_WRITES=0 — запись в БД заблокирована"
        _send(q, "log", {"agent": "0",
              "line": f"⚠ Шаги 6, 7 пропущены: {reason}"})

    for agent_id in steps:
        code = await _run_agent(agent_id, run_id)
        if code != 0:
            _send(q, "log", {"agent": agent_id,
                  "line": f"✖ Агент #{agent_id} завершился с ошибкой (код {code}). Пайплайн остановлен."})
            _pipeline_status = "error"
            _send(q, "pipeline", {"state": "error"})
            _send(q, "done", {})
            return

    _pipeline_status = "done"
    _send(q, "pipeline", {"state": "done"})
    _send(q, "log", {"agent": "0", "line": "✅ Весь пайплайн завершён"})
    _send(q, "done", {})


# ══════════════════════════════════════════════════════════════════════════════
# SSE endpoint
# ══════════════════════════════════════════════════════════════════════════════

async def _sse_generator(run_id: str) -> AsyncGenerator[str, None]:
    if run_id not in _runs:
        yield "event: error\ndata: {\"msg\": \"run not found\"}\n\n"
        return

    q = _runs[run_id]
    timeout_secs = 600  # 10 минут максимум
    deadline = time.monotonic() + timeout_secs

    while True:
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                yield "event: timeout\ndata: {}\n\n"
                break
            item = await asyncio.wait_for(q.get(), timeout=min(30, remaining))
        except asyncio.TimeoutError:
            yield "event: ping\ndata: {}\n\n"
            continue

        event = item["event"]
        data = json.dumps(item["data"], ensure_ascii=False)
        yield f"event: {event}\ndata: {data}\n\n"

        if event == "done":
            break

    # Чистим очередь через некоторое время
    await asyncio.sleep(5)
    _runs.pop(run_id, None)


# ══════════════════════════════════════════════════════════════════════════════
# API Routes
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/events")
async def events(run_id: str):
    return StreamingResponse(
        _sse_generator(run_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/run/{agent_id}")
async def run_agent(agent_id: str):
    if agent_id not in AGENT_CMDS:
        return JSONResponse({"error": "unknown agent"}, status_code=400)
    if agent_id in {"6", "7"} and not _env_truthy("ALLOW_DB_WRITES"):
        return JSONResponse(
            {"error": "DB write agents are blocked. Set ALLOW_DB_WRITES=1 to enable."},
            status_code=403,
        )
    run_id = str(uuid.uuid4())
    _runs[run_id] = asyncio.Queue(maxsize=2000)
    asyncio.create_task(_run_single(agent_id, run_id))
    return {"run_id": run_id}


async def _run_single(agent_id: str, run_id: str):
    q = _runs[run_id]
    await _run_agent(agent_id, run_id)
    _send(q, "done", {})


@app.post("/run/all/pipeline")
async def run_all():
    run_id = str(uuid.uuid4())
    _runs[run_id] = asyncio.Queue(maxsize=5000)
    asyncio.create_task(_run_pipeline(run_id))
    return {"run_id": run_id}


@app.post("/api/upload/pdf")
async def upload_pdf_file(req: Request):
    cache_dir = ROOT / "cache"
    cache_dir.mkdir(exist_ok=True)
    filename = req.headers.get("x-filename", "uploaded.pdf")
    clean_name = re.sub(r"[^a-zA-Z0-9_.-]", "", filename or "uploaded.pdf")
    file_path = cache_dir / f"uploaded_{clean_name}"
    content = await req.body()
    file_path.write_bytes(content)
    return {"file_path": str(file_path), "filename": filename}


@app.post("/api/run/pdf")
async def run_pdf_scout_custom(req: Request):
    data = await req.json()
    url = data.get("url", "").strip() or "https://online.fliphtml5.com/kwnhb/fakj/"
    file_path = data.get("file_path", "").strip()
    format_type = data.get("format_type", "magazine")
    max_topics = str(data.get("max_topics", 3))
    write_flag = data.get("write", False)

    cmd = [
        PYTHON_CMD, str(AGENTS_DIR / "agent-01b-pdf-scout.py"),
        "--url", url,
        "--format", format_type,
        "--max-topics", max_topics,
        "--brief", str(BRIEFS_FILE),
        "--article", str(ARTICLE_FILE),
        "--meta", str(META_FILE),
    ]
    if file_path and os.path.exists(file_path):
        cmd.extend(["--file", file_path])
    if write_flag:
        cmd.append("--write")

    run_id = str(uuid.uuid4())
    _runs[run_id] = asyncio.Queue(maxsize=5000)

    async def _run_pdf_task():
        q = _runs[run_id]
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        _send(q, "log", {"agent": "1b", "line": f"▶ {' '.join(cmd)}"})
        _agent_status["1b"] = "running"
        _send(q, "status", {"agent": "1b", "state": "running"})

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            cwd=str(ROOT),
        )

        async def read_stdout():
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                _send(q, "log", {"agent": "1b", "line": text})

        await read_stdout()
        await proc.wait()

        state = "done" if proc.returncode == 0 else "error"
        _agent_status["1b"] = state
        _send(q, "status", {"agent": "1b", "state": state, "code": proc.returncode})
        _send(q, "done", {})

    asyncio.create_task(_run_pdf_task())
    return {"run_id": run_id}


@app.get("/status")
async def get_status():
    return {
        "agents": _agent_status,
        "pipeline": _pipeline_status,
        "llm_api_base": os.environ.get("LLM_API_BASE", "не задан"),
        "llm_model": os.environ.get("LLM_MODEL", "не задан"),
        "llm_mock": _env_truthy("LLM_MOCK") or _env_truthy("MOCK_LLM"),
        "db_connected": bool(os.environ.get("NEON_DATABASE_URL")),
        "allow_db_writes": _env_truthy("ALLOW_DB_WRITES"),
    }


@app.get("/briefs")
async def get_briefs():
    if not BRIEFS_FILE.exists():
        return {"topics": [], "generated_at": None}
    return json.loads(BRIEFS_FILE.read_text("utf-8"))


@app.get("/article")
async def get_article():
    result = {}
    if ARTICLE_FILE.exists():
        result["text"] = ARTICLE_FILE.read_text("utf-8")[:3000]
    if META_FILE.exists():
        result["meta"] = json.loads(META_FILE.read_text("utf-8"))
    return result


@app.get("/drafts")
async def get_drafts():
    db_url = os.environ.get("NEON_DATABASE_URL")
    if not db_url:
        return {"drafts": [], "error": "NEON_DATABASE_URL не задан"}
    try:
        import psycopg2, psycopg2.extras
        conn = psycopg2.connect(db_url)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, title, editorial_type, category_name, date, slug
                FROM news WHERE is_published = false
                ORDER BY id DESC LIMIT 50
            """)
            drafts = [dict(r) for r in cur.fetchall()]
        conn.close()
        for d in drafts:
            if d.get("date"):
                d["date"] = d["date"].isoformat()
        return {"drafts": drafts}
    except Exception as e:
        return {"drafts": [], "error": str(e)}


@app.post("/drafts/{article_id}/approve")
async def approve_draft(article_id: int):
    db_url = os.environ.get("NEON_DATABASE_URL")
    if not db_url:
        return JSONResponse({"error": "NEON_DATABASE_URL не задан"}, status_code=400)
    if not _env_truthy("ALLOW_DB_WRITES"):
        return JSONResponse({"error": "Запись в БД заблокирована: ALLOW_DB_WRITES=0"}, status_code=403)
    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        with conn.cursor() as cur:
            # editorial_type определяет раздел сайта (news/insight/review/vendor),
            # поэтому при approve его нельзя стирать.
            cur.execute(
                "UPDATE news SET is_published=true WHERE id=%s",
                (article_id,)
            )
        conn.commit()
        conn.close()
        return {"ok": True, "id": article_id}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/drafts/{article_id}")
async def delete_draft(article_id: int):
    db_url = os.environ.get("NEON_DATABASE_URL")
    if not db_url:
        return JSONResponse({"error": "NEON_DATABASE_URL не задан"}, status_code=400)
    if not _env_truthy("ALLOW_DB_WRITES"):
        return JSONResponse({"error": "Запись в БД заблокирована: ALLOW_DB_WRITES=0"}, status_code=403)
    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM news WHERE id=%s AND is_published=false", (article_id,))
        conn.commit()
        conn.close()
        return {"ok": True, "id": article_id}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


_selected_topic_index: int = 0  # индекс выбранной темы из briefs.json


@app.post("/briefs/select/{index}")
async def select_topic(index: int):
    """Устанавливает приоритетную тему для Writer'а."""
    global _selected_topic_index
    if not BRIEFS_FILE.exists():
        return JSONResponse({"error": "briefs.json не найден"}, status_code=404)
    data = json.loads(BRIEFS_FILE.read_text("utf-8"))
    topics = data.get("topics", [])
    if index < 0 or index >= len(topics):
        return JSONResponse({"error": f"Индекс {index} вне диапазона (0-{len(topics)-1})"}, status_code=400)
    _selected_topic_index = index
    # Патчим AGENT_CMDS[2] чтобы Writer взял нужную тему
    AGENT_CMDS["2"] = [
        PYTHON_CMD, str(AGENTS_DIR / "agent-02-writer.py"),
        "--brief", str(BRIEFS_FILE),
        "--pick", "first",
        "--output", str(ARTICLE_FILE),
    ]
    # Перемещаем выбранную тему на первое место в briefs.json
    selected = topics.pop(index)
    topics.insert(0, selected)
    data["topics"] = topics
    BRIEFS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _selected_topic_index = 0
    return {"ok": True, "selected_topic": selected.get("topic", ""), "index": 0}


@app.get("/briefs/selected")
async def get_selected_topic():
    return {"selected_index": _selected_topic_index}


# ══════════════════════════════════════════════════════════════════════════════
# HTML UI (всё в одном файле)
# ══════════════════════════════════════════════════════════════════════════════

HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SMTInsider — Control Room</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600&display=swap">
<style>
:root {
  --bg: #080C14;
  --surface: #0f1623;
  --surface2: #161f30;
  --surface3: #1d2840;
  --border: #1e2d44;
  --border2: #253550;
  --green: #00E5A0;
  --green-dim: rgba(0,229,160,.1);
  --green-dim2: rgba(0,229,160,.18);
  --orange: #FF8C42;
  --orange-dim: rgba(255,140,66,.12);
  --blue: #4A9EFF;
  --blue-dim: rgba(74,158,255,.1);
  --blue-dim2: rgba(74,158,255,.18);
  --red: #FF4757;
  --red-dim: rgba(255,71,87,.1);
  --yellow: #FFD166;
  --yellow-dim: rgba(255,209,102,.1);
  --text: #C8D6EF;
  --text-dim: #4e6a8f;
  --text-mid: #7a9bc4;
  --mono: 'JetBrains Mono', monospace;
  --sans: 'Inter', sans-serif;
  --radius: 8px;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:var(--bg);color:var(--text);font-family:var(--sans);overflow:hidden}

/* ── LAYOUT ── */
.shell{display:grid;grid-template-rows:52px 1fr;grid-template-columns:280px 1fr 340px;height:100vh}

/* ── HEADER ── */
header{
  grid-column:1/-1;display:flex;align-items:center;justify-content:space-between;
  padding:0 20px;background:var(--surface);border-bottom:1px solid var(--border);
  font-family:var(--mono);gap:12px;
}
.logo{font-size:13px;font-weight:700;letter-spacing:.08em;color:var(--green);white-space:nowrap}
.logo span{color:var(--text-dim)}
.header-meta{display:flex;align-items:center;gap:12px;font-size:11px;color:var(--text-dim)}
.pill{
  display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:99px;
  font-size:11px;border:1px solid var(--border);background:var(--surface2);font-family:var(--mono);white-space:nowrap;
}
.dot{width:6px;height:6px;border-radius:50%;background:var(--text-dim);flex-shrink:0}
.dot.green{background:var(--green);box-shadow:0 0 8px var(--green);animation:blink 2s infinite}
.dot.red{background:var(--red)}
.dot.orange{background:var(--orange)}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.35}}

/* ── LEFT PANEL: AGENTS ── */
.panel-agents{background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}
.panel-title{
  padding:14px 16px 10px;font-size:10px;font-family:var(--mono);letter-spacing:.12em;
  color:var(--text-dim);border-bottom:1px solid var(--border);text-transform:uppercase;
}
.agent-list{flex:1;overflow-y:auto;padding:8px;display:flex;flex-direction:column;gap:3px}
.agent-card{
  display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:var(--radius);
  border:1px solid transparent;cursor:pointer;transition:all .15s;
}
.agent-card:hover{background:var(--surface2);border-color:var(--border)}
.agent-card.running{background:var(--green-dim2);border-color:var(--green)}
.agent-card.done{border-color:rgba(0,229,160,.25)}
.agent-card.error{background:var(--red-dim);border-color:var(--red)}
.agent-num{font-family:var(--mono);font-size:11px;font-weight:700;color:var(--text-dim);width:18px;flex-shrink:0}
.agent-info{flex:1;min-width:0}
.agent-name{font-size:13px;font-weight:500;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.agent-desc{font-size:10px;color:var(--text-dim);margin-top:2px}
.agent-state{
  font-family:var(--mono);font-size:9px;font-weight:600;letter-spacing:.06em;
  padding:2px 6px;border-radius:4px;background:var(--surface2);color:var(--text-dim);
  text-transform:uppercase;flex-shrink:0;
}
.agent-card.running .agent-state{background:var(--green);color:#000}
.agent-card.error .agent-state{background:var(--red);color:#fff}
.agent-card.done .agent-state{color:var(--green)}
.agent-run-btn{
  background:none;border:1px solid var(--border);color:var(--text-mid);padding:3px 8px;
  border-radius:5px;font-size:10px;font-family:var(--mono);cursor:pointer;transition:all .15s;flex-shrink:0;
}
.agent-run-btn:hover{border-color:var(--green);color:var(--green)}

.pipeline-btn{
  margin:10px;padding:12px;background:var(--green-dim2);border:1px solid var(--green);
  color:var(--green);font-family:var(--mono);font-size:12px;font-weight:700;letter-spacing:.08em;
  border-radius:var(--radius);cursor:pointer;transition:all .2s;
}
.pipeline-btn:hover{background:var(--green);color:#000}
.pipeline-btn:disabled{opacity:.4;cursor:not-allowed}

/* ── CENTRE PANEL ── */
.panel-main{display:flex;flex-direction:column;overflow:hidden;border-right:1px solid var(--border)}
.main-tabs{
  display:flex;align-items:center;background:var(--surface);border-bottom:1px solid var(--border);
  padding:0 16px;gap:2px;flex-shrink:0;
}
.tab{
  padding:14px 16px 12px;font-size:12px;font-weight:500;color:var(--text-dim);cursor:pointer;
  border-bottom:2px solid transparent;transition:all .15s;white-space:nowrap;
}
.tab:hover{color:var(--text)}
.tab.active{color:var(--green);border-bottom-color:var(--green)}
.tab-spacer{flex:1}
.clear-btn{
  background:none;border:none;color:var(--text-dim);font-size:11px;font-family:var(--mono);
  cursor:pointer;padding:4px 8px;border-radius:4px;
}
.clear-btn:hover{color:var(--text);background:var(--surface2)}

.content-pane{flex:1;overflow-y:auto;display:none;padding:16px}
.content-pane.active{display:block}
#pane-log{padding:0;background:var(--bg)}

/* ── LOG ── */
.log-stream{
  font-family:var(--mono);font-size:11.5px;line-height:1.65;padding:14px 16px;
  min-height:100%;color:var(--text-mid);
}
.log-line{padding:1px 0;white-space:pre-wrap;word-break:break-all}
.log-line.ok{color:var(--green)}
.log-line.err{color:var(--red)}
.log-line.warn{color:var(--orange)}
.log-line.info{color:var(--blue)}
.log-line.dim{color:var(--text-dim)}
.log-agent-tag{
  display:inline-block;font-size:9px;padding:1px 5px;border-radius:3px;
  background:var(--surface2);color:var(--text-dim);margin-right:6px;vertical-align:middle;
}

/* ── BRIEFS PANEL ── */
.briefs-toolbar{
  display:flex;align-items:center;gap:8px;margin-bottom:14px;
  padding:10px 12px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
  font-size:11px;color:var(--text-dim);font-family:var(--mono);
}
.brief-card{
  background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
  padding:14px 16px;margin-bottom:10px;transition:all .2s;cursor:default;
}
.brief-card.selected{border-color:var(--blue);background:var(--blue-dim2)}
.brief-card.priority-selected{border-color:var(--green);background:var(--green-dim2)}
.brief-header{display:flex;align-items:flex-start;gap:10px;margin-bottom:8px}
.brief-index{
  font-family:var(--mono);font-size:11px;font-weight:700;color:var(--text-dim);
  background:var(--surface2);border:1px solid var(--border);border-radius:5px;
  padding:3px 8px;flex-shrink:0;margin-top:1px;
}
.brief-index.priority{background:var(--green-dim2);border-color:var(--green);color:var(--green)}
.brief-topic{font-size:14px;font-weight:600;color:var(--text);line-height:1.4;flex:1}
.brief-meta{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.badge{
  display:inline-flex;align-items:center;padding:2px 8px;border-radius:4px;
  font-size:10px;font-family:var(--mono);font-weight:600;letter-spacing:.04em;
  border:1px solid;
}
.badge-high{background:rgba(255,71,87,.12);color:var(--red);border-color:rgba(255,71,87,.3)}
.badge-medium{background:rgba(255,140,66,.12);color:var(--orange);border-color:rgba(255,140,66,.3)}
.badge-low{background:rgba(74,158,255,.1);color:var(--blue);border-color:rgba(74,158,255,.25)}
.badge-gray{background:var(--surface2);color:var(--text-dim);border-color:var(--border)}
.brief-angle{
  font-size:12px;color:var(--text-mid);line-height:1.55;margin-bottom:10px;
  border-left:2px solid var(--border2);padding-left:10px;
}
.brief-facts{margin-bottom:10px}
.brief-facts-title{font-size:10px;font-family:var(--mono);color:var(--text-dim);letter-spacing:.08em;margin-bottom:5px}
.fact-item{font-size:11px;color:var(--text-mid);padding:2px 0;display:flex;gap:6px}
.fact-item::before{content:"•";color:var(--green);flex-shrink:0}
.brief-sources{margin-bottom:10px}
.source-link{
  display:inline-flex;align-items:center;gap:4px;font-size:11px;color:var(--blue);
  text-decoration:none;font-family:var(--mono);
}
.source-link:hover{color:var(--text)}
.brief-footer{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.key-tag{
  display:inline-block;padding:2px 7px;border-radius:4px;
  font-size:10px;font-family:var(--mono);background:var(--surface2);
  color:var(--text-dim);border:1px solid var(--border);
}
.select-topic-btn{
  margin-left:auto;padding:6px 14px;border-radius:6px;font-size:11px;font-family:var(--mono);
  font-weight:600;cursor:pointer;transition:all .2s;border:1px solid var(--border);
  background:var(--surface2);color:var(--text-mid);
}
.select-topic-btn:hover{border-color:var(--green);color:var(--green);background:var(--green-dim)}
.select-topic-btn.active{border-color:var(--green);color:#000;background:var(--green)}

/* ── ARTICLE PANEL ── */
.article-toolbar{
  display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap;align-items:center;
}
.article-meta-chips{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
.article-summary{
  font-size:13px;color:var(--text-mid);line-height:1.6;margin-bottom:16px;
  padding:12px 14px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
  border-left:3px solid var(--blue);
}
.article-score{
  display:inline-flex;align-items:center;gap:8px;padding:6px 12px;border-radius:6px;
  font-size:11px;font-family:var(--mono);
}
.article-score.good{background:var(--green-dim);border:1px solid rgba(0,229,160,.3);color:var(--green)}
.article-score.warn{background:var(--orange-dim);border:1px solid rgba(255,140,66,.3);color:var(--orange)}
.article-score.bad{background:var(--red-dim);border:1px solid rgba(255,71,87,.3);color:var(--red)}
.article-body{
  font-size:13.5px;line-height:1.75;color:var(--text);
  white-space:pre-wrap;word-break:break-word;
  background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
  padding:20px 22px;
}
.article-section-heading{color:var(--green);font-weight:600;font-size:15px;margin:16px 0 6px}
.copy-btn{
  padding:6px 14px;background:var(--surface2);border:1px solid var(--border);color:var(--text-mid);
  border-radius:6px;font-size:11px;font-family:var(--mono);cursor:pointer;transition:all .15s;
}
.copy-btn:hover{border-color:var(--blue);color:var(--blue)}

/* ── RIGHT PANEL: DRAFTS ── */
.panel-drafts{background:var(--surface);display:flex;flex-direction:column;overflow:hidden}
.drafts-header{
  display:flex;align-items:center;justify-content:space-between;
  padding:14px 16px 10px;border-bottom:1px solid var(--border);
}
.drafts-list{flex:1;overflow-y:auto;padding:8px;display:flex;flex-direction:column;gap:6px}
.draft-card{
  background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius);
  padding:12px 14px;transition:border-color .15s;
}
.draft-card:hover{border-color:var(--border2)}
.draft-title{font-size:12.5px;font-weight:500;color:var(--text);line-height:1.4;margin-bottom:7px}
.draft-meta{display:flex;gap:6px;font-size:10px;font-family:var(--mono);color:var(--text-dim);margin-bottom:9px;flex-wrap:wrap}
.draft-actions{display:flex;gap:6px}
.btn-approve{
  flex:1;padding:5px 0;background:var(--green-dim);border:1px solid rgba(0,229,160,.3);
  color:var(--green);border-radius:5px;font-size:11px;font-family:var(--mono);cursor:pointer;
  transition:all .15s;
}
.btn-approve:hover{background:var(--green);color:#000}
.btn-del{
  padding:5px 10px;background:none;border:1px solid var(--border);color:var(--text-dim);
  border-radius:5px;font-size:11px;cursor:pointer;transition:all .15s;
}
.btn-del:hover{border-color:var(--red);color:var(--red)}

/* ── EMPTY STATE ── */
.empty-state{
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  gap:10px;min-height:200px;color:var(--text-dim);font-size:12px;font-family:var(--mono);text-align:center;
}
.empty-icon{font-size:28px;opacity:.5}

/* ── TOAST ── */
#toast{
  position:fixed;bottom:20px;left:50%;transform:translateX(-50%);
  padding:10px 20px;border-radius:8px;font-size:12px;font-family:var(--mono);
  opacity:0;transition:opacity .2s;pointer-events:none;z-index:999;
}
#toast.show{opacity:1}
#toast.ok{background:var(--green);color:#000}
#toast.err{background:var(--red);color:#fff}
#toast.info{background:var(--blue);color:#fff}

/* ── SCROLLBAR ── */
::-webkit-scrollbar{width:4px;height:4px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--border2);border-radius:2px}
</style>
</head>
<body>
<div class="shell">

  <!-- HEADER -->
  <header>
    <div class="logo">SMTInsider <span>/ Control Room</span></div>
    <div class="header-meta">
      <span class="pill"><span class="dot" id="llm-dot"></span><span id="llm-label">…</span></span>
      <span class="pill"><span class="dot" id="db-dot"></span><span id="db-label">…</span></span>
      <span class="pill" id="selected-topic-pill" style="display:none">
        <span style="color:var(--green)">▶</span>
        <span id="selected-topic-name" style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></span>
      </span>
      <span style="font-size:11px;color:var(--text-dim);font-family:var(--mono)" id="hdr-time"></span>
    </div>
  </header>

  <!-- LEFT: AGENTS -->
  <div class="panel-agents">
    <div class="panel-title">Агенты</div>
    <div class="agent-list" id="agent-list"></div>
    <button class="pipeline-btn" id="btn-run-all" onclick="runAll()">▶ ЗАПУСТИТЬ ПАЙПЛАЙН</button>
    <div style="margin-top:16px; border-top:1px solid var(--border); padding-top:12px;">
      <div class="panel-title" style="font-size:11px; margin-bottom:8px;">📄 РУЧНАЯ ПОДАЧА PDF (#1b)</div>
      <input id="pdf-url-input" type="text" placeholder="URL: https://online.fliphtml5.com/..." 
             style="width:100%; background:var(--bg-card); border:1px solid var(--border); color:var(--text); padding:6px 8px; border-radius:4px; font-size:11px; margin-bottom:6px;">
      <input id="pdf-file-input" type="file" accept=".pdf,.txt,.html" style="font-size:10px; color:var(--text-dim); margin-bottom:6px; width:100%;">
      <div style="display:flex; gap:6px; margin-bottom:6px;">
        <select id="pdf-format-select" style="flex:1; background:var(--bg-card); border:1px solid var(--border); color:var(--text); padding:4px; border-radius:4px; font-size:11px;">
          <option value="magazine">Журнал (magazine)</option>
          <option value="review">Обзор (review)</option>
          <option value="datasheet">Даташит (datasheet)</option>
        </select>
        <input id="pdf-topics-input" type="number" value="3" min="1" max="10" title="Сколько тем выделить" 
               style="width:48px; background:var(--bg-card); border:1px solid var(--border); color:var(--text); padding:4px; border-radius:4px; font-size:11px; text-align:center;">
      </div>
      <div style="display:flex; gap:6px;">
        <button onclick="runPdfScoutUI(false)" style="flex:1; background:var(--bg-card); border:1px solid var(--border); color:var(--text); padding:6px; border-radius:4px; font-size:10px; cursor:pointer;" title="Создать бриф тем">📋 СОЗДАТЬ БРИФ</button>
        <button onclick="runPdfScoutUI(true)" style="flex:1; background:var(--accent); border:none; color:#000; font-weight:bold; padding:6px; border-radius:4px; font-size:10px; cursor:pointer;" title="Создать бриф и написать статьи">✍️ НАПИСАТЬ СТАТЬИ</button>
      </div>
    </div>
  </div>

  <!-- CENTRE: MAIN CONTENT -->
  <div class="panel-main">
    <div class="main-tabs">
      <div class="tab active" onclick="showTab('log')">Лог</div>
      <div class="tab" onclick="showTab('briefs')">Темы <span id="briefs-count" style="font-size:10px;color:var(--text-dim)"></span></div>
      <div class="tab" onclick="showTab('article')">Статья</div>
      <div class="tab-spacer"></div>
      <button class="clear-btn" id="clear-btn" onclick="clearLog()" style="display:none">✕ очистить лог</button>
    </div>

    <!-- Log pane -->
    <div id="pane-log" class="content-pane active">
      <div class="log-stream" id="log-stream">
        <div class="empty-state"><div class="empty-icon">⬡</div>Лог пуст — запусти агента или пайплайн</div>
      </div>
    </div>

    <!-- Briefs pane -->
    <div id="pane-briefs" class="content-pane">
      <div id="briefs-content"></div>
    </div>

    <!-- Article pane -->
    <div id="pane-article" class="content-pane">
      <div id="article-content"></div>
    </div>
  </div>

  <!-- RIGHT: DRAFTS -->
  <div class="panel-drafts">
    <div class="drafts-header">
      <div class="panel-title" style="padding:0;border:none;font-size:10px">Черновики в БД</div>
      <button class="clear-btn" onclick="loadDrafts()">↻ обновить</button>
    </div>
    <div class="drafts-list" id="drafts-list">
      <div class="empty-state"><div class="empty-icon">☰</div>Загрузка…</div>
    </div>
  </div>
</div>

<div id="toast"></div>

<script>
// ── State ──────────────────────────────────────────────────────────
const AGENTS = [
  {id:"1", name:"Trend Hunter",     desc:"Собирает новости, выбирает темы", needs:""},
  {id:"1b", name:"PDF Scout",       desc:"Ручная подача PDF / журнала",     needs:"URL или файл"},
  {id:"2", name:"Writer",           desc:"Пишет статью по выбранной теме",  needs:"briefs.json"},
  {id:"2b",name:"Quality Checker",  desc:"Проверяет и улучшает текст",      needs:"meta.json"},
  {id:"3", name:"SEO Doctor",       desc:"Slug, meta-description, JSON-LD", needs:"meta.json"},
  {id:"4", name:"Distributor",      desc:"LinkedIn / форум / email",        needs:"meta.json"},
  {id:"5", name:"Analyst",          desc:"Метрики + рекомендации из БД",    needs:""},
  {id:"6", name:"Publisher",        desc:"Черновик в Neon Postgres",        needs:"meta.json + DB"},
  {id:"7", name:"YouTube Scout",    desc:"Поиск видео через yt-dlp",        needs:"DB"},
];

let agentStatus = {};
let pipelineRunning = false;
let logEmpty = true;
let selectedTopicIndex = null;
let briefsData = null;

// ── Clock ──────────────────────────────────────────────────────────
function tick() {
  document.getElementById('hdr-time').textContent =
    new Date().toLocaleTimeString('ru', {hour12:false});
}
setInterval(tick, 1000); tick();

// ── Status ─────────────────────────────────────────────────────────
async function loadStatus() {
  const r = await fetch('/status').then(r=>r.json()).catch(()=>null);
  if (!r) return;
  agentStatus = r.agents || {};

  const llmDot = document.getElementById('llm-dot');
  const llmLabel = document.getElementById('llm-label');
  llmDot.className = 'dot ' + (r.llm_mock ? 'orange' : r.llm_api_base !== 'не задан' ? 'green' : 'red');
  llmLabel.textContent = r.llm_mock ? 'MOCK' : (r.llm_model || 'no model');

  const dbDot = document.getElementById('db-dot');
  const dbLabel = document.getElementById('db-label');
  dbDot.className = 'dot ' + (r.db_connected ? 'green' : 'red');
  dbLabel.textContent = r.db_connected
    ? (r.allow_db_writes ? 'DB:rw' : 'DB:ro')
    : 'no DB';

  renderAgents();
}

// ── Agents ─────────────────────────────────────────────────────────
function renderAgents() {
  const list = document.getElementById('agent-list');
  list.innerHTML = AGENTS.map(a => {
    const state = agentStatus[a.id] || 'idle';
    const cls = state === 'running' ? 'running' : state === 'error' ? 'error' : state === 'done' ? 'done' : '';
    const stateLabel = state === 'idle' ? 'idle' : state === 'running' ? 'running…' : state;
    return `
    <div class="agent-card ${cls}" title="${escHtml(a.needs ? 'Нужно: '+a.needs : '')}">
      <div class="agent-num">#${a.id}</div>
      <div class="agent-info">
        <div class="agent-name">${escHtml(a.name)}</div>
        <div class="agent-desc">${escHtml(a.desc)}</div>
      </div>
      <div class="agent-state">${stateLabel}</div>
      <button class="agent-run-btn" onclick="event.stopPropagation();runAgent('${a.id}')" title="Запустить ${a.name}">▶</button>
    </div>`;
  }).join('');

  const btn = document.getElementById('btn-run-all');
  btn.disabled = pipelineRunning;
  btn.textContent = pipelineRunning ? '⏳ ПАЙПЛАЙН РАБОТАЕТ…' : '▶ ЗАПУСТИТЬ ПАЙПЛАЙН';
}

// ── Log ────────────────────────────────────────────────────────────
function colorClass(line) {
  if (/✅|✓|done|OK|success/i.test(line)) return 'ok';
  if (/❌|✖|error|fail|traceback/i.test(line)) return 'err';
  if (/⚠|warn|skip/i.test(line)) return 'warn';
  if (/▶|agent|запуск|running/i.test(line)) return 'info';
  if (/^\s*(#|\[|\d{4})/.test(line)) return 'dim';
  return '';
}

function appendLog(agentId, line) {
  const stream = document.getElementById('log-stream');
  if (logEmpty) { stream.innerHTML = ''; logEmpty = false; }
  const div = document.createElement('div');
  div.className = 'log-line ' + colorClass(line);
  const tag = agentId && agentId !== '0' ? `<span class="log-agent-tag">#${escHtml(agentId)}</span>` : '';
  div.innerHTML = tag + escHtml(line);
  stream.appendChild(div);
  stream.scrollTop = stream.scrollHeight;
}

function clearLog() {
  document.getElementById('log-stream').innerHTML =
    '<div class="empty-state"><div class="empty-icon">⬡</div>Лог очищен</div>';
  logEmpty = true;
}

// ── SSE ────────────────────────────────────────────────────────────
function subscribeRun(runId) {
  const es = new EventSource(`/events?run_id=${runId}`);
  es.addEventListener('log', e => {
    const d = JSON.parse(e.data);
    appendLog(d.agent, d.line);
  });
  es.addEventListener('status', e => {
    const d = JSON.parse(e.data);
    agentStatus[d.agent] = d.state;
    renderAgents();
  });
  es.addEventListener('pipeline', e => {
    const d = JSON.parse(e.data);
    pipelineRunning = d.state === 'running';
    renderAgents();
  });
  es.addEventListener('done', () => {
    es.close();
    pipelineRunning = false;
    renderAgents();
    setTimeout(() => { loadBriefs(); loadArticle(); loadDrafts(); }, 700);
  });
  es.addEventListener('ping', () => {});
  es.onerror = () => { es.close(); pipelineRunning = false; renderAgents(); };
}

// ── Run agent / pipeline ────────────────────────────────────────────
async function runAgent(agentId) {
  showTab('log');
  const r = await fetch(`/run/${agentId}`, {method:'POST'}).then(r=>r.json());
  if (r.run_id) { subscribeRun(r.run_id); }
  else toast(r.error || 'Ошибка запуска', 'err');
}

async function runAll() {
  if (pipelineRunning) return;
  showTab('log');
  const r = await fetch('/run/all/pipeline', {method:'POST'}).then(r=>r.json());
  if (r.run_id) { pipelineRunning = true; renderAgents(); subscribeRun(r.run_id); }
  else toast(r.error || 'Ошибка', 'err');
}

async function runPdfScoutUI(writeFlag) {
  showTab('log');
  let filePath = '';
  const fileInput = document.getElementById('pdf-file-input');
  if (fileInput.files && fileInput.files[0]) {
    appendLog('1b', '▶ Загружаю файл ' + fileInput.files[0].name + ' на сервер...');
    try {
      const res = await fetch('/api/upload/pdf', {
        method: 'POST',
        headers: {
          'x-filename': encodeURIComponent(fileInput.files[0].name),
          'Content-Type': fileInput.files[0].type || 'application/octet-stream'
        },
        body: fileInput.files[0]
      });
      const data = await res.json();
      filePath = data.file_path || '';
    } catch (e) {
      appendLog('1b', '❌ Ошибка загрузки файла: ' + e);
      return;
    }
  }
  const urlVal = document.getElementById('pdf-url-input').value.trim() || "https://online.fliphtml5.com/kwnhb/fakj/";
  const fmtVal = document.getElementById('pdf-format-select').value;
  const topVal = parseInt(document.getElementById('pdf-topics-input').value) || 3;

  appendLog('1b', `▶ Запуск PDF Scout (#1b): url=${urlVal}, format=${fmtVal}, max-topics=${topVal}, write=${writeFlag}`);
  try {
    const res = await fetch('/api/run/pdf', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: urlVal,
        file_path: filePath,
        format_type: fmtVal,
        max_topics: topVal,
        write: writeFlag
      })
    });
    const data = await res.json();
    if (data.run_id) {
      subscribeRun(data.run_id);
    } else {
      toast(data.error || 'Ошибка запуска PDF Scout', 'err');
    }
  } catch (e) {
    appendLog('1b', '❌ Ошибка запуска PDF Scout: ' + e);
  }
}

// ── Topic selection ─────────────────────────────────────────────────
async function selectTopic(index) {
  const r = await fetch(`/briefs/select/${index}`, {method:'POST'}).then(r=>r.json()).catch(()=>null);
  if (!r || r.error) { toast(r?.error || 'Ошибка', 'err'); return; }
  selectedTopicIndex = 0; // after selection it's moved to index 0
  toast(`✓ Тема выбрана: Writer напишет эту статью`, 'info');
  // Update pill in header
  const pill = document.getElementById('selected-topic-pill');
  const name = document.getElementById('selected-topic-name');
  pill.style.display = 'inline-flex';
  name.textContent = r.selected_topic;
  // Reload briefs to show new order
  await loadBriefs();
}

// ── Tabs ───────────────────────────────────────────────────────────
function showTab(name) {
  document.querySelectorAll('.tab').forEach((t,i) => {
    t.classList.toggle('active', ['log','briefs','article'][i] === name);
  });
  ['log','briefs','article'].forEach(n => {
    document.getElementById(`pane-${n}`).classList.toggle('active', n === name);
  });
  document.getElementById('clear-btn').style.display = name === 'log' ? '' : 'none';
  if (name === 'briefs') loadBriefs();
  if (name === 'article') loadArticle();
}

// ── Briefs ─────────────────────────────────────────────────────────
async function loadBriefs() {
  const data = await fetch('/briefs').then(r=>r.json()).catch(()=>null);
  briefsData = data;
  const el = document.getElementById('briefs-content');
  const countEl = document.getElementById('briefs-count');

  if (!data || !data.topics || !data.topics.length) {
    el.innerHTML = '<div class="empty-state"><div class="empty-icon">⬡</div>Нет тем — запусти Trend Hunter (#1)</div>';
    countEl.textContent = '';
    return;
  }

  countEl.textContent = `(${data.topics.length})`;
  const ts = data.generated_at ? new Date(data.generated_at).toLocaleString('ru') : '';
  const model = data.model || '?';

  const toolbar = `
    <div class="briefs-toolbar">
      <span>${data.topics.length} тем найдено</span>
      <span>·</span>
      <span>модель: ${escHtml(model)}</span>
      ${ts ? `<span>·</span><span>${ts}</span>` : ''}
      <span style="flex:1"></span>
      <span style="color:var(--green)">★</span>
      <span>Нажми «Выбрать» чтобы Writer написал эту тему</span>
    </div>`;

  const cards = data.topics.map((t, i) => {
    const urgency = (t.urgency || 'low').toLowerCase();
    const isPriority = i === 0 && selectedTopicIndex !== null;
    const isSelected = i === selectedTopicIndex;

    const factsHtml = t.key_facts && t.key_facts.length
      ? `<div class="brief-facts">
           <div class="brief-facts-title">ФАКТЫ ИЗ ИСТОЧНИКОВ</div>
           ${t.key_facts.map(f => `<div class="fact-item">${escHtml(f)}</div>`).join('')}
         </div>` : '';

    const sourcesHtml = t.sources && t.sources.length
      ? `<div class="brief-sources">
           ${t.sources.map(s => `
             <a class="source-link" href="${escHtml(s.url||'#')}" target="_blank" rel="noopener">
               ↗ ${escHtml(s.title||s.url||'источник')}
               ${s.date ? `<span style="color:var(--text-dim)">(${escHtml(s.date)})</span>` : ''}
             </a>`).join('<br>')}
         </div>` : '';

    const isCurrentPriority = isPriority && i === 0;
    return `
    <div class="brief-card ${isCurrentPriority ? 'priority-selected' : ''}">
      <div class="brief-header">
        <div class="brief-index ${isCurrentPriority ? 'priority' : ''}">${isCurrentPriority ? '★ приоритет' : '#' + (i+1)}</div>
        <div class="brief-topic">${escHtml(t.topic)}</div>
      </div>
      <div class="brief-meta">
        <span class="badge badge-${urgency}">${t.urgency || '?'}</span>
        <span class="badge badge-gray">${escHtml(t.editorial_type || t.format || '')}</span>
        <span class="badge badge-gray">${escHtml(t.category || '')}</span>
        <span class="badge badge-gray">${t.source_count || t.sources?.length || 0} источн.</span>
        ${t.target_section ? `<span class="badge badge-gray">${escHtml(t.target_section)}</span>` : ''}
      </div>
      ${t.angle ? `<div class="brief-angle">${escHtml(t.angle)}</div>` : ''}
      ${factsHtml}
      ${sourcesHtml}
      <div class="brief-footer">
        <div style="display:flex;gap:4px;flex-wrap:wrap;flex:1">
          ${(t.keywords||[]).map(k=>`<span class="key-tag">${escHtml(k)}</span>`).join('')}
        </div>
        ${!isCurrentPriority ? `
          <button class="select-topic-btn" onclick="selectTopic(${i})">
            ★ Выбрать для Writer
          </button>` : `
          <button class="select-topic-btn active" disabled>
            ★ Выбрана (активна)
          </button>`}
      </div>
    </div>`;
  }).join('');

  el.innerHTML = toolbar + cards;
}

// ── Article ────────────────────────────────────────────────────────
async function loadArticle() {
  const data = await fetch('/article').then(r=>r.json()).catch(()=>null);
  const el = document.getElementById('article-content');
  if (!data || (!data.text && !data.meta)) {
    el.innerHTML = '<div class="empty-state"><div class="empty-icon">✍</div>Нет статьи — запусти Writer (#2)</div>';
    return;
  }
  const m = data.meta || {};
  const qc = m.quality_check || null;
  const lint = m.lint_report || null;

  let scoreHtml = '';
  if (lint && (lint.score !== undefined)) {
    const lsc = lint.score;
    const lcls = lsc >= 80 ? 'good' : lsc >= 65 ? 'warn' : 'bad';
    scoreHtml += `<div class="article-score ${lcls}" title="Детерминированные проверки без LLM: штампы, ритм, подзаголовки, факты">Lint: ${lsc}/100</div>`;
  }
  if (qc) {
    const sc = qc.score;
    const cls = sc >= 80 ? 'good' : sc >= 65 ? 'warn' : 'bad';
    const imp = qc.improved ? ' (улучшена)' : ' (одобрена)';
    scoreHtml += `<div class="article-score ${cls}">Quality: ${sc}/100${imp}</div>`;
    if (qc.issues && qc.issues.length) {
      scoreHtml += `<div style="margin-top:8px;font-size:11px;color:var(--text-dim)">
        ${qc.issues.map(i=>`<span style="display:block;margin-bottom:3px">⚠ ${escHtml(i)}</span>`).join('')}
      </div>`;
    }
  }
  if (lint && lint.issues && lint.issues.length) {
    scoreHtml += `<div style="margin-top:8px;font-size:11px;color:var(--text-dim)">
      ${lint.issues.map(i=>`<span style="display:block;margin-bottom:3px">${i.severity==='error'?'✖':'⚠'} ${escHtml(i.message)}</span>`).join('')}
    </div>`;
  }

  // Format article body: treat lines starting with ## or all-caps as headings
  function formatBody(text) {
    return text.split('\n').map(line => {
      const trimmed = line.trim();
      if (!trimmed) return '<br>';
      // Detect section headings: lines with ## or short all-caps or "The X:" pattern
      if (/^##\s/.test(trimmed) || /^The [A-Z]/.test(trimmed) || (trimmed.length < 60 && /^[A-Z][A-Za-z\s:]+$/.test(trimmed))) {
        return `<div class="article-section-heading">${escHtml(trimmed.replace(/^##\s*/,''))}</div>`;
      }
      return `<p style="margin-bottom:10px">${escHtml(line)}</p>`;
    }).join('');
  }

  const bodyHtml = data.text ? formatBody(data.text) : '<em style="color:var(--text-dim)">Текст не загружен</em>';

  el.innerHTML = `
    <div class="article-toolbar">
      <button class="copy-btn" onclick="copyArticle()">⎘ Копировать текст</button>
      ${scoreHtml}
    </div>
    <div class="article-meta-chips">
      ${m.editorial_type ? `<span class="badge badge-medium">${escHtml(m.editorial_type)}</span>` : ''}
      ${m.category ? `<span class="badge badge-low">${escHtml(m.category)}</span>` : ''}
      ${m.section_path ? `<span class="badge badge-gray">${escHtml(m.section_path)}</span>` : ''}
      ${(m.tags||[]).map(t=>`<span class="key-tag">${escHtml(t)}</span>`).join('')}
    </div>
    ${m.summary ? `<div class="article-summary">${escHtml(m.summary)}</div>` : ''}
    <div class="article-body">${bodyHtml}</div>
    ${m.model ? `<p style="font-size:10px;color:var(--text-dim);font-family:var(--mono);margin-top:10px">модель: ${escHtml(m.model)} · ${m.generated_at ? new Date(m.generated_at).toLocaleString('ru') : ''}</p>` : ''}`;
}

async function copyArticle() {
  const data = await fetch('/article').then(r=>r.json()).catch(()=>null);
  if (!data?.text) { toast('Нет текста', 'err'); return; }
  try {
    await navigator.clipboard.writeText(data.text);
    toast('Скопировано!', 'ok');
  } catch { toast('Ошибка копирования', 'err'); }
}

// ── Drafts ─────────────────────────────────────────────────────────
async function loadDrafts() {
  const data = await fetch('/drafts').then(r=>r.json()).catch(()=>null);
  const el = document.getElementById('drafts-list');
  if (!data || data.error) {
    el.innerHTML = `<div class="empty-state"><div class="empty-icon">🗄</div><span style="font-size:11px">${data?.error||'Ошибка загрузки'}</span></div>`;
    return;
  }
  if (!data.drafts.length) {
    el.innerHTML = '<div class="empty-state"><div class="empty-icon">☰</div>Черновиков нет</div>';
    return;
  }
  el.innerHTML = data.drafts.map(d => {
    const dt = d.date ? new Date(d.date).toLocaleDateString('ru') : '';
    const typeBadge = d.editorial_type
      ? `<span class="badge badge-gray">${escHtml(d.editorial_type)}</span>` : '';
    return `
    <div class="draft-card" id="draft-${d.id}">
      <div class="draft-title">${escHtml(d.title||'Без заголовка')}</div>
      <div class="draft-meta">
        <span>#${d.id}</span>
        ${typeBadge}
        ${d.category_name ? `<span>${escHtml(d.category_name)}</span>` : ''}
        ${dt ? `<span>${dt}</span>` : ''}
      </div>
      <div class="draft-actions">
        <button class="btn-approve" onclick="approveDraft(${d.id})">✓ Опубликовать</button>
        <button class="btn-del" onclick="deleteDraft(${d.id})">✕</button>
      </div>
    </div>`;
  }).join('');
}

async function approveDraft(id) {
  const r = await fetch(`/drafts/${id}/approve`, {method:'POST'}).then(r=>r.json());
  if (r.ok) { toast(`Опубликовано #${id}`, 'ok'); document.getElementById(`draft-${id}`)?.remove(); }
  else toast(r.error||'Ошибка', 'err');
}

async function deleteDraft(id) {
  if (!confirm(`Удалить черновик #${id}?`)) return;
  const r = await fetch(`/drafts/${id}`, {method:'DELETE'}).then(r=>r.json());
  if (r.ok) { toast(`Удалено #${id}`, 'ok'); document.getElementById(`draft-${id}`)?.remove(); }
  else toast(r.error||'Ошибка', 'err');
}

// ── Toast ──────────────────────────────────────────────────────────
function toast(msg, type='ok') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `show ${type}`;
  clearTimeout(el._t);
  el._t = setTimeout(() => el.className='', 3500);
}

// ── Utils ──────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Boot ───────────────────────────────────────────────────────────
loadStatus();
loadDrafts();
setInterval(loadStatus, 30000);
</script>
</body>
</html>
"""



@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(HTML)
