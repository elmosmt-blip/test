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
    "1": [PYTHON_CMD, str(AGENTS_DIR / "agent-01-trend-hunter.py"), "scan", "--days", os.environ.get("NEWS_LOOKBACK_DAYS", "30"), "--strict-fresh", "--verify-pages", "--max-topics", os.environ.get("NEWS_MAX_TOPICS", "20"), "--output", str(BRIEFS_FILE)],
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
          "scan", "--days", os.environ.get("YOUTUBE_LOOKBACK_DAYS", "60"), "--brief", str(BRIEFS_FILE)],
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

        timeout_secs = int(os.environ.get("PDF_SCOUT_TIMEOUT_SECONDS", "900"))
        try:
            await asyncio.wait_for(read_stdout(), timeout=timeout_secs)
            await proc.wait()
        except asyncio.TimeoutError:
            # Large or malformed PDFs can make a parser stall.  Do not leave the
            # dashboard in the misleading "RUNNING" state indefinitely.
            proc.terminate()
            await proc.wait()
            _agent_status["1b"] = "error"
            _send(q, "log", {"agent": "1b", "line": (
                f"❌ PDF Scout остановлен по тайм-ауту ({timeout_secs} с). "
                "Проверьте PDF или попробуйте файл меньшего размера."
            )})
            _send(q, "status", {"agent": "1b", "state": "error", "code": "timeout"})
            _send(q, "done", {})
            return

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


def _review_meta_files() -> list[Path]:
    """Local editorial artifacts, including factual-blocked selected topics."""
    selected_dir = _TMP / "smtinsider_selected_articles"
    files = list(selected_dir.glob("*.meta.json")) if selected_dir.exists() else []
    if META_FILE.exists():
        files.append(META_FILE)
    return sorted(set(files), key=lambda path: path.stat().st_mtime, reverse=True)


@app.get("/review-queue")
async def get_review_queue():
    items = []
    for path in _review_meta_files():
        try:
            meta = json.loads(path.read_text("utf-8"))
            quality = meta.get("quality_check") or {}
            items.append({
                "id": path.name,
                "title": meta.get("title", path.stem),
                "status": quality.get("status", "pending"),
                "approved": bool(quality.get("approved")),
                "human_override": quality.get("human_override", {}),
                "score": quality.get("score"),
                "issues": quality.get("issues", []),
            })
        except Exception:
            continue
    return {"items": items}


@app.get("/review-queue/{item_id}")
async def get_review_item(item_id: str):
    path = next((candidate for candidate in _review_meta_files() if candidate.name == Path(item_id).name), None)
    if path is None:
        return JSONResponse({"error": "Материал review queue не найден"}, status_code=404)
    try:
        meta = json.loads(path.read_text("utf-8"))
        article_path = Path(meta.get("article_file", ""))
        text = article_path.read_text("utf-8") if article_path.exists() else ""
        return {"id": path.name, "meta": meta, "text": text}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/review-queue/{item_id}/override")
async def override_review_item(item_id: str, req: Request):
    """Record an explicit human editorial exception; never silently bypass QC."""
    payload = await req.json()
    reason = str(payload.get("reason", "")).strip()
    if len(reason) < 10:
        return JSONResponse({"error": "Укажите причину ручного редакционного решения (минимум 10 символов)"}, status_code=400)
    path = next((candidate for candidate in _review_meta_files() if candidate.name == Path(item_id).name), None)
    if path is None:
        return JSONResponse({"error": "Материал review queue не найден"}, status_code=404)
    try:
        meta = json.loads(path.read_text("utf-8"))
        quality = meta.setdefault("quality_check", {})
        quality["human_override"] = {
            "approved": True,
            "reason": reason,
            "approved_at": datetime.now(timezone.utc).isoformat(),
        }
        quality["status"] = "editorial_override"
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "id": path.name}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/review-queue/{item_id}/continue")
async def continue_review_item(item_id: str):
    """Continue an explicitly approved exception through SEO and distribution."""
    path = next((candidate for candidate in _review_meta_files() if candidate.name == Path(item_id).name), None)
    if path is None:
        return JSONResponse({"error": "Материал review queue не найден"}, status_code=404)
    meta = json.loads(path.read_text("utf-8"))
    override = (meta.get("quality_check") or {}).get("human_override") or {}
    if not override.get("approved"):
        return JSONResponse({"error": "Сначала сохраните редакторское исключение с причиной"}, status_code=400)
    run_id = str(uuid.uuid4())
    _runs[run_id] = asyncio.Queue(maxsize=2000)
    commands = [
        ("3", [PYTHON_CMD, str(AGENTS_DIR / "agent-03-seo-doctor.py"), "--meta", str(path)]),
        ("4", [PYTHON_CMD, str(AGENTS_DIR / "agent-04-distributor.py"), "--meta", str(path)]),
    ]
    if os.environ.get("NEON_DATABASE_URL") and _env_truthy("ALLOW_DB_WRITES"):
        # submit creates an unpublished DB draft; it never makes the article public.
        commands.append(("6", [PYTHON_CMD, str(AGENTS_DIR / "agent-06-publisher.py"), "submit", "--meta", str(path)]))

    async def task():
        q = _runs[run_id]
        for agent_id, command in commands:
            _agent_status[agent_id] = "running"
            _send(q, "status", {"agent": agent_id, "state": "running"})
            proc = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, cwd=str(ROOT), env={**os.environ, "PYTHONUNBUFFERED": "1"})
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                _send(q, "log", {"agent": agent_id, "line": line.decode("utf-8", errors="replace").rstrip()})
            await proc.wait()
            if proc.returncode:
                _agent_status[agent_id] = "error"
                _send(q, "status", {"agent": agent_id, "state": "error", "code": proc.returncode})
                _send(q, "done", {})
                return
            _agent_status[agent_id] = "done"
            _send(q, "status", {"agent": agent_id, "state": "done", "code": 0})
        _send(q, "done", {})

    asyncio.create_task(task())
    return {"run_id": run_id}


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


@app.get("/drafts/{article_id}")
async def get_draft(article_id: int):
    """Return one unpublished article for the Control Room reader."""
    db_url = os.environ.get("NEON_DATABASE_URL")
    if not db_url:
        return JSONResponse({"error": "NEON_DATABASE_URL не задан"}, status_code=400)
    try:
        import psycopg2, psycopg2.extras
        conn = psycopg2.connect(db_url)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, title, content, summary, editorial_type, category_name,
                       date, slug, source, source_url, author_name, frontmatter_json
                FROM news WHERE id=%s AND is_published=false
            """, (article_id,))
            row = cur.fetchone()
        conn.close()
        if not row:
            return JSONResponse({"error": "Черновик не найден"}, status_code=404)
        draft = dict(row)
        if draft.get("date"):
            draft["date"] = draft["date"].isoformat()
        raw_frontmatter = draft.get("frontmatter_json")
        if isinstance(raw_frontmatter, str):
            try:
                draft["frontmatter_json"] = json.loads(raw_frontmatter)
            except json.JSONDecodeError:
                draft["frontmatter_json"] = {}
        return {"draft": draft}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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


@app.post("/briefs/select-many")
async def select_briefs(req: Request):
    """Persist selected topic indices without reordering the editorial plan."""
    data = await req.json()
    indices = sorted(set(data.get("indices", [])))
    if not isinstance(indices, list) or not all(isinstance(index, int) for index in indices):
        return JSONResponse({"error": "Некорректный список тем"}, status_code=400)
    if not BRIEFS_FILE.exists():
        return JSONResponse({"error": "briefs.json не найден"}, status_code=404)
    payload = json.loads(BRIEFS_FILE.read_text("utf-8"))
    topics = payload.get("topics", [])
    if any(index < 0 or index >= len(topics) for index in indices):
        return JSONResponse({"error": "Выбранная тема больше не существует"}, status_code=400)
    payload["selected_topic_indices"] = indices
    BRIEFS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # The single Writer play button must honour a single selected card, not
    # silently write topics[0]. Multi-selection uses the explicit full cycle.
    if len(indices) == 1:
        AGENT_CMDS["2"] = [
            PYTHON_CMD, str(AGENTS_DIR / "agent-02-writer.py"),
            "--brief", str(BRIEFS_FILE), "--pick", str(indices[0]),
            "--output", str(ARTICLE_FILE),
        ]
    return {"ok": True, "indices": indices}


@app.post("/briefs/run-selected")
async def run_selected_briefs(req: Request):
    """Continue all selected topics through Writer, QC, SEO and distribution."""
    data = await req.json()
    indices = data.get("indices", [])
    if not isinstance(indices, list) or not indices or not all(isinstance(i, int) for i in indices):
        return JSONResponse({"error": "Выберите хотя бы одну тему"}, status_code=400)
    if not BRIEFS_FILE.exists():
        return JSONResponse({"error": "briefs.json не найден"}, status_code=404)
    topics = json.loads(BRIEFS_FILE.read_text("utf-8")).get("topics", [])
    if any(i < 0 or i >= len(topics) for i in indices):
        return JSONResponse({"error": "Выбранная тема больше не существует"}, status_code=400)
    run_id = str(uuid.uuid4())
    _runs[run_id] = asyncio.Queue(maxsize=5000)
    command = [
        PYTHON_CMD, str(AGENTS_DIR / "run-selected-topics.py"),
        "--brief", str(BRIEFS_FILE),
        "--indices", ",".join(str(i) for i in sorted(set(indices))),
        "--output-dir", str(_TMP / "smtinsider_selected_articles"),
    ]

    async def task():
        q = _runs[run_id]
        _agent_status["2"] = "running"
        _send(q, "status", {"agent": "2", "state": "running"})
        _send(q, "log", {"agent": "2", "line": f"▶ {' '.join(command)}"})
        proc = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, cwd=str(ROOT), env={**os.environ, "PYTHONUNBUFFERED": "1"})
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            _send(q, "log", {"agent": "2", "line": line.decode("utf-8", errors="replace").rstrip()})
        await proc.wait()
        state = "done" if proc.returncode == 0 else "error"
        _agent_status["2"] = state
        _send(q, "status", {"agent": "2", "state": state, "code": proc.returncode})
        _send(q, "done", {})

    asyncio.create_task(task())
    return {"run_id": run_id, "count": len(set(indices))}


@app.delete("/briefs/{index}")
async def delete_brief(index: int):
    """Remove an unsuitable topic before it reaches Writer."""
    global _selected_topic_index
    if not BRIEFS_FILE.exists():
        return JSONResponse({"error": "briefs.json не найден"}, status_code=404)
    data = json.loads(BRIEFS_FILE.read_text("utf-8"))
    topics = data.get("topics", [])
    if index < 0 or index >= len(topics):
        return JSONResponse({"error": f"Индекс {index} вне диапазона (0-{len(topics)-1})"}, status_code=400)
    deleted = topics.pop(index)
    data["topics"] = topics
    selected_indices = data.get("selected_topic_indices", [])
    data["selected_topic_indices"] = [
        value - 1 if value > index else value
        for value in selected_indices if value != index
    ]
    BRIEFS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if index == _selected_topic_index:
        _selected_topic_index = -1
    elif index < _selected_topic_index:
        _selected_topic_index -= 1
    return {"ok": True, "deleted_topic": deleted.get("topic", ""), "remaining": len(topics)}


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
.delete-topic-btn{
  padding:6px 10px;border-radius:6px;font-size:11px;font-family:var(--mono);cursor:pointer;
  border:1px solid var(--border);background:transparent;color:var(--text-dim);transition:all .2s;
}
.delete-topic-btn:hover{border-color:var(--red);color:var(--red);background:var(--red-dim)}

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
.btn-read{padding:5px 9px;background:var(--blue-dim);border:1px solid rgba(74,158,255,.35);color:var(--blue);border-radius:5px;font-size:11px;font-family:var(--mono);cursor:pointer;transition:all .15s}.btn-read:hover{background:var(--blue);color:#041221}
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

/* ── CONTROL ROOM REDESIGN: workflow-first application shell ── */
:root{--bg:#09111e;--surface:#101b2d;--surface2:#15233a;--surface3:#1c2e49;--border:#263b5b;--border2:#345070;--text:#e6eefb;--text-mid:#a9bdd8;--text-dim:#7188a8;--radius:12px}
html,body{overflow:hidden;background:radial-gradient(circle at 56% -25%,#17345a 0,var(--bg) 42%)}
.shell{grid-template-rows:64px 1fr;grid-template-columns:264px minmax(0,1fr);max-width:1800px;margin:auto;border-left:1px solid rgba(52,80,112,.45);border-right:1px solid rgba(52,80,112,.45)}
header{padding:0 24px;background:rgba(12,23,40,.9);backdrop-filter:blur(14px)}
.logo{font-size:15px;letter-spacing:.04em}.logo::before{content:'◆';font-size:10px;margin-right:9px;color:var(--green)}
.header-meta{gap:8px}.pill{padding:5px 10px;background:rgba(21,35,58,.75);font-family:var(--sans);font-size:11px}
.panel-agents,.panel-drafts{background:rgba(12,23,40,.76);backdrop-filter:blur(12px)}.panel-drafts.attention{box-shadow:inset 3px 0 0 var(--green),0 0 0 2px rgba(0,229,160,.22);transition:box-shadow .2s ease}
.panel-title{padding:17px 16px 11px;color:var(--text-mid);font-weight:700;letter-spacing:.1em}
.agent-list{padding:10px 9px;gap:6px}.agent-card{padding:11px 10px;border-color:rgba(38,59,91,.45);background:rgba(21,35,58,.36)}
.agent-card:hover{transform:translateX(2px);background:var(--surface2);border-color:var(--border2)}
.agent-name{font-size:12px;font-weight:600}.agent-desc{font-size:10.5px;line-height:1.35}.agent-state{font-size:8px}
.pipeline-btn{margin:12px;border-radius:9px;padding:13px;box-shadow:0 8px 24px rgba(0,229,160,.08)}
.panel-main{background:rgba(9,17,30,.38)}.main-tabs{padding:0 24px;background:rgba(12,23,40,.72);gap:10px}.tab{padding:18px 10px 15px;font-weight:600}.tab.active{color:#fff}
.content-pane{padding:24px;scrollbar-gutter:stable}#pane-log{margin:0 24px 24px;padding:0;border:1px solid var(--border);border-radius:12px;background:rgba(7,14,25,.68);min-height:0}.log-stream{padding:18px;font-size:12px;line-height:1.75}
.briefs-toolbar{position:sticky;top:-24px;z-index:2;padding:14px 16px;background:rgba(16,27,45,.96);backdrop-filter:blur(12px);box-shadow:0 8px 18px rgba(0,0,0,.15)}
.brief-card{padding:20px;margin-bottom:14px;border-radius:12px;background:linear-gradient(135deg,rgba(21,35,58,.92),rgba(16,27,45,.85));box-shadow:0 10px 28px rgba(0,0,0,.12)}
.brief-topic{font-size:16px}.brief-angle{font-size:13px}.fact-item{font-size:12px}.select-topic-btn{padding:8px 13px}
.drafts-header{padding:17px 14px 11px}.drafts-list{padding:9px}.draft-card{padding:14px;border-radius:10px;background:rgba(21,35,58,.62)}
/* Overview is the default decision surface. */
.overview-grid{display:grid;gap:16px;grid-template-columns:repeat(4,minmax(0,1fr));margin-bottom:20px}.metric-card,.overview-card{border:1px solid var(--border);border-radius:12px;background:linear-gradient(145deg,rgba(22,38,63,.94),rgba(14,25,43,.9));box-shadow:0 12px 30px rgba(0,0,0,.12)}
.metric-card{padding:16px}.metric-label{font:600 10px var(--mono);letter-spacing:.09em;color:var(--text-dim);text-transform:uppercase}.metric-value{font-size:27px;font-weight:600;color:#fff;margin-top:9px}.metric-note{font-size:11px;color:var(--text-mid);margin-top:4px}
.overview-layout{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(280px,.9fr);gap:16px}.overview-card{padding:20px}.overview-eyebrow{font:600 10px var(--mono);color:var(--green);letter-spacing:.1em;text-transform:uppercase}.overview-title{font-size:21px;line-height:1.25;margin:8px 0;color:#fff}.overview-copy{line-height:1.55;font-size:13px;color:var(--text-mid)}.workflow-steps{display:grid;grid-template-columns:repeat(5,1fr);gap:7px;margin-top:20px}.workflow-step{min-height:68px;padding:10px;border-radius:8px;border:1px solid var(--border);background:rgba(12,23,40,.46);color:var(--text-dim);font:11px var(--sans);text-align:left;cursor:pointer;transition:transform .16s ease,border-color .16s ease,background .16s ease,box-shadow .16s ease}.workflow-step:hover{transform:translateY(-2px);border-color:var(--blue);background:var(--blue-dim);color:var(--text)}.workflow-step:focus-visible{outline:3px solid rgba(74,158,255,.45);outline-offset:2px}.workflow-step b{display:block;color:var(--text);font-size:12px;margin-bottom:4px}.workflow-step span{display:block}.workflow-step.active{border-color:var(--green);background:var(--green-dim);color:var(--green);box-shadow:inset 0 0 0 1px rgba(0,229,160,.14)}.workflow-step.active b{color:var(--green)}
.next-action{border-left:3px solid var(--green)}.overview-action{margin-top:16px;padding:9px 13px;border-radius:7px;border:1px solid var(--green);background:var(--green);font:700 11px var(--sans);cursor:pointer;color:#06251d}.overview-action:hover{filter:brightness(1.08)}
.source-summary{display:flex;align-items:center;justify-content:space-between;padding:11px 0;border-bottom:1px solid rgba(38,59,91,.7);font-size:12px}.source-summary:last-child{border:0}.health{font:600 10px var(--mono);padding:3px 7px;border-radius:99px}.health.ok{background:var(--green-dim);color:var(--green)}.health.watch{background:var(--orange-dim);color:var(--orange)}.health.blocked{background:var(--red-dim);color:var(--red)}.plan-header{display:flex;align-items:flex-end;justify-content:space-between;gap:20px;margin:2px 0 18px}.plan-header h1{margin:6px 0;color:#fff;font-size:25px;letter-spacing:-.03em}.plan-header p{max-width:650px;color:var(--text-mid);font-size:13px;line-height:1.55}.plan-summary{display:flex;align-items:center;gap:10px;color:var(--text-mid);font-size:12px;white-space:nowrap}
@media(max-width:1100px){.shell{grid-template-columns:230px minmax(0,1fr)}.panel-drafts{display:none}.overview-grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:760px){html,body{overflow:auto}.shell{height:auto;min-height:100vh;display:block}.panel-agents{border-right:0}.agent-list{max-height:260px}.panel-main{min-height:70vh}.overview-layout{grid-template-columns:1fr}.workflow-steps{grid-template-columns:repeat(2,1fr)}.header-meta .pill:not(:first-child),#hdr-time{display:none}.content-pane{padding:14px}#pane-log{margin:0 14px 14px}.main-tabs{padding:0 14px;overflow:auto}.tab{padding-left:9px;padding-right:9px}}
.preview-toolbar{align-items:center;justify-content:space-between}.preview-label{display:block;color:var(--green);font:700 10px var(--mono);letter-spacing:.12em}.preview-note{display:block;margin-top:4px;color:var(--text-dim);font-size:11px}.preview-actions{display:flex;align-items:center;gap:8px}.site-preview{overflow:hidden;border:1px solid var(--border);border-radius:16px;background:var(--bg);color:#d9e1ed}.site-preview-hero{display:grid;grid-template-columns:minmax(0,1fr) 278px;gap:58px;max-width:920px;margin:0 auto;padding:54px 36px 38px}.preview-back{color:#8998ad;text-decoration:none;font-size:13px}.preview-kicker{margin-top:40px;color:#7c899e;font:10px var(--mono);letter-spacing:.14em;text-transform:uppercase}.site-preview h1{max-width:590px;margin:22px 0 15px;color:#f5f2ed;font:400 clamp(37px,4.2vw,58px)/1.02 Georgia,'Times New Roman',serif;letter-spacing:-.035em}.preview-dek{max-width:590px;color:#aeb9ca;font-size:16px;line-height:1.55}.preview-context{align-self:start;margin-top:60px;padding:24px 20px;border:1px solid rgba(66,81,104,.6);border-radius:16px;background:rgba(17,23,34,.9)}.preview-context>div{color:var(--green);font:700 9px var(--mono);letter-spacing:.15em}.preview-context dl{margin:20px 0 0}.preview-context dt{margin-top:17px;color:#6f7d92;font:9px var(--mono);letter-spacing:.12em}.preview-context dd{margin:7px 0 0;color:#e1e6ee;font-size:12px;font-weight:600;line-height:1.4}.preview-context dd a{color:var(--blue);text-decoration:none}.preview-context dd a:hover{text-decoration:underline}.preview-decision-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;max-width:920px;margin:0 auto 32px;padding:0 36px}.preview-decision-grid>div{min-height:160px;padding:22px 18px;border:1px solid rgba(53,69,91,.6);border-radius:12px;background:rgba(15,21,31,.82)}.preview-decision-grid b{color:var(--green);font:700 9px var(--mono);letter-spacing:.16em}.preview-decision-grid p,.preview-decision-grid li{margin-top:15px;color:#c7d0de;font-size:12px;line-height:1.55}.preview-decision-grid ul{margin:12px 0 0;padding-left:15px}.preview-decision-grid li{margin:6px 0}.preview-body{max-width:760px;margin:0 auto;padding:38px 44px 58px;border:1px solid rgba(53,69,91,.6);border-radius:14px 14px 0 0;background:rgba(14,19,29,.92);font-size:15px;line-height:1.8;color:#d0d7e2}.preview-body p{margin:0 0 18px}.preview-body .article-section-heading{margin:34px 0 11px;color:#f2f4f8;font-size:22px}.preview-qa{margin:16px 0;padding:13px 16px;border:1px solid var(--border);border-radius:10px;background:var(--surface);color:var(--text-mid);font-size:12px}.preview-qa summary{cursor:pointer;color:var(--blue);font-weight:600}.preview-qa .article-evidence{margin:15px 0 0}.workspace-hero{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;padding:28px;margin-bottom:18px;border:1px solid var(--border);border-radius:14px;background:linear-gradient(115deg,rgba(22,47,78,.98),rgba(15,27,46,.95))}.workspace-hero h1{margin:7px 0 9px;color:#fff;font-size:26px;letter-spacing:-.03em}.workspace-hero p{max-width:690px;color:var(--text-mid);font-size:13px;line-height:1.6}.workspace-hero.compact{padding:22px}.workspace-hero.compact h1{font-size:22px}.workspace-actions{display:flex;gap:9px;flex-shrink:0}.collect-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.collect-grid h2{margin:8px 0;color:#fff;font-size:18px}.publish-drafts{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));align-content:start;gap:12px;padding:0}.publish-drafts .draft-card{margin:0;min-height:160px}.publish-drafts .empty-state{grid-column:1/-1}.review-queue{display:grid;gap:12px}.review-card{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;padding:18px;border:1px solid var(--border);border-radius:12px;background:var(--surface)}.review-card h2{margin:10px 0 6px;color:#fff;font-size:16px}.review-card p,.review-card small{display:block;color:var(--text-mid);font-size:12px;line-height:1.45}.review-card-actions{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}.article-evidence{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;margin:0 0 14px;padding:16px;border:1px solid var(--border);border-radius:10px;background:var(--surface)}.article-evidence.pass{border-left:3px solid var(--green)}.article-evidence.blocked{border-left:3px solid var(--red)}.article-evidence h2{margin:5px 0;color:#fff;font-size:15px}.article-evidence p{color:var(--text-mid);font-size:12px;line-height:1.5}.evidence-details{display:flex;align-items:flex-end;flex-direction:column;gap:7px;color:var(--text-dim);font:11px var(--mono)}.evidence-alert{grid-column:1/-1;padding:10px 12px;border-radius:7px;background:var(--red-dim);color:var(--text-mid);font-size:11px;line-height:1.55}.evidence-alert b{display:block;color:var(--red);margin-bottom:3px}.evidence-sources{grid-column:1/-1;border-top:1px solid var(--border);padding-top:10px;color:var(--text-mid);font-size:12px}.evidence-sources summary{cursor:pointer;color:var(--blue)}.evidence-sources>div{margin:11px 0;padding-left:10px;border-left:2px solid var(--border)}.evidence-sources a{color:var(--blue);text-decoration:none}.evidence-sources p{margin-top:4px;font-size:11px}.pdf-launch-btn{margin:0 12px 14px;padding:10px;border:1px solid var(--border2);border-radius:9px;background:var(--surface2);color:var(--text);font:700 10px var(--mono);letter-spacing:.05em;cursor:pointer}.pdf-launch-btn:hover{border-color:var(--blue);color:var(--blue)}
.modal-backdrop{position:fixed;inset:0;z-index:1000;display:none;align-items:center;justify-content:center;padding:24px;background:rgba(3,9,18,.72);backdrop-filter:blur(7px)}.modal-backdrop.open{display:flex}.pdf-scout-dialog{width:min(720px,100%);max-height:calc(100vh - 48px);overflow:auto;padding:28px;border:1px solid var(--border2);border-radius:16px;background:linear-gradient(145deg,#14243b,#0e192a);box-shadow:0 30px 90px rgba(0,0,0,.55)}.pdf-dialog-header{display:flex;justify-content:space-between;gap:24px;padding-bottom:22px;border-bottom:1px solid var(--border)}.pdf-dialog-header h2{margin:6px 0 7px;color:#fff;font-size:22px}.pdf-dialog-header p{max-width:540px;color:var(--text-mid);font-size:13px;line-height:1.5}.modal-close{width:34px;height:34px;border:1px solid var(--border);border-radius:8px;background:transparent;color:var(--text-mid);font-size:25px;line-height:1;cursor:pointer}.modal-close:hover{color:var(--red);border-color:var(--red)}.pdf-form-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:22px 0}.pdf-field{display:flex;flex-direction:column;gap:7px;color:var(--text);font-size:12px;font-weight:600}.pdf-field-wide{grid-column:1/-1}.pdf-field span em{font-style:normal;font-weight:400;color:var(--text-dim)}.pdf-field input,.pdf-field select{width:100%;padding:11px 12px;border:1px solid var(--border);border-radius:8px;background:#0a1424;color:var(--text);font:12px var(--sans)}.pdf-field input:focus,.pdf-field select:focus{outline:none;border-color:var(--blue);box-shadow:0 0 0 3px var(--blue-dim)}.pdf-field small{color:var(--text-dim);font-size:10px;font-weight:400;line-height:1.4}.pdf-evidence-note{padding:12px 14px;border:1px solid rgba(74,158,255,.32);border-radius:8px;background:var(--blue-dim);color:var(--text-mid);font-size:12px;line-height:1.5}.pdf-evidence-note b{color:var(--blue)}.pdf-dialog-actions{display:flex;justify-content:flex-end;gap:9px;margin-top:22px}.modal-secondary,.modal-primary{padding:10px 14px;border-radius:8px;font:600 12px var(--sans);cursor:pointer}.modal-secondary{border:1px solid var(--border);background:var(--surface2);color:var(--text)}.modal-primary{border:1px solid var(--green);background:var(--green);color:#06251d}.modal-secondary:hover{border-color:var(--text-mid)}.modal-primary:hover{filter:brightness(1.08)}@media(max-width:760px){.workspace-hero{align-items:flex-start;flex-direction:column;padding:20px}.workspace-actions{width:100%;flex-wrap:wrap}.collect-grid{grid-template-columns:1fr}.review-card{flex-direction:column}.review-card-actions{justify-content:flex-start}.site-preview-hero{grid-template-columns:1fr;gap:20px;padding:30px 22px}.preview-context{margin-top:0}.preview-decision-grid{grid-template-columns:1fr;padding:0 22px}.preview-body{margin:0 22px;padding:28px 22px}.preview-actions{margin-top:10px;flex-wrap:wrap}.article-evidence{grid-template-columns:1fr}.evidence-details{align-items:flex-start}}.draft-reader-dialog{width:min(960px,100%);max-height:calc(100vh - 42px);overflow:auto;padding:22px 28px 46px;border:1px solid var(--border2);border-radius:16px;background:#0d1522;box-shadow:0 30px 90px rgba(0,0,0,.55)}.draft-reader-topbar{display:flex;justify-content:space-between;align-items:center;padding-bottom:15px;border-bottom:1px solid var(--border)}.draft-reader-topbar>div{display:flex;align-items:center;gap:8px}.draft-reader-content{max-width:720px;margin:45px auto 0}.draft-reader-meta{color:var(--green);font:700 10px var(--mono);letter-spacing:.13em;text-transform:uppercase}.draft-reader-content h1{margin:18px 0;color:#f5f2ed;font:400 clamp(35px,5vw,58px)/1.04 Georgia,'Times New Roman',serif;letter-spacing:-.035em}.draft-reader-summary{margin:0 0 24px;color:var(--text-mid);font-size:17px;line-height:1.55}.draft-reader-context{display:flex;justify-content:space-between;gap:12px;padding:13px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border);color:var(--text-dim);font-size:11px}.draft-reader-context a{color:var(--blue);text-decoration:none}.draft-reader-body{padding-top:28px;color:#d6deea;font-size:15px;line-height:1.8}.draft-reader-body p{margin:0 0 18px}.draft-reader-body h2{margin:34px 0 12px;color:#fff;font-size:23px}@media(max-width:600px){.modal-backdrop{padding:10px}.pdf-scout-dialog,.draft-reader-dialog{padding:20px}.pdf-form-grid{grid-template-columns:1fr}.pdf-field-wide{grid-column:auto}.pdf-dialog-actions{flex-wrap:wrap}.pdf-dialog-actions button{flex:1}.draft-reader-context{flex-direction:column}.draft-reader-topbar{align-items:flex-start}.draft-reader-topbar>div{flex-wrap:wrap;justify-content:flex-end}}
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
    <button class="pdf-launch-btn" onclick="openPdfScout()">＋ НОВЫЙ PDF / ЖУРНАЛ</button>
  </div>

  <!-- CENTRE: MAIN CONTENT -->
  <div class="panel-main">
    <div class="main-tabs">
      <div class="tab active" onclick="showTab('overview')">Обзор</div>
      <div class="tab" onclick="showTab('collect')">Сбор</div>
      <div class="tab" onclick="showTab('briefs')">План <span id="briefs-count" style="font-size:10px;color:var(--text-dim)"></span></div>
      <div class="tab" onclick="showTab('article')">Создание</div>
      <div class="tab" onclick="showTab('review')">Review</div>
      <div class="tab" onclick="showTab('publish')">Публикация</div>
      <div class="tab" onclick="showTab('log')">Активность</div>
      <div class="tab-spacer"></div>
      <button class="clear-btn" id="clear-btn" onclick="clearLog()" style="display:none">✕ очистить лог</button>
    </div>

    <!-- Overview pane: decision-first default workspace -->
    <div id="pane-overview" class="content-pane active">
      <div id="overview-content"><div class="empty-state"><div class="empty-icon">◇</div>Загружаю рабочее состояние…</div></div>
    </div>

    <!-- Collect workspace -->
    <div id="pane-collect" class="content-pane">
      <div class="workspace-hero">
        <div><div class="overview-eyebrow">Collect</div><h1>Соберите доказательства, а не просто ссылки</h1><p>Запустите мониторинг источников или добавьте документ. Каждый материал проходит извлечение текста и evidence gate до появления в редакционном плане.</p></div>
        <div class="workspace-actions"><button class="modal-primary" onclick="openPdfScout()">＋ Добавить PDF / журнал</button><button class="modal-secondary" onclick="runAgent('1')">Запустить Trend Hunter</button></div>
      </div>
      <div class="collect-grid">
        <section class="overview-card"><div class="overview-eyebrow">Web sources</div><h2>Автоматический мониторинг</h2><p class="overview-copy">Trend Hunter собирает свежие сигналы, проверяет даты и передаёт только подходящие evidence в план.</p><button class="overview-action" onclick="runAgent('1')">Начать сбор →</button></section>
        <section class="overview-card"><div class="overview-eyebrow">Manual source</div><h2>PDF, datasheet или журнал</h2><p class="overview-copy">Загрузите исходный файл или укажите официальный URL. Повреждённый текст, metadata и неподтверждённые claims будут остановлены до Writer.</p><button class="overview-action" onclick="openPdfScout()">Открыть PDF Scout →</button></section>
      </div>
    </div>

    <!-- Log pane -->
    <div id="pane-log" class="content-pane">
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

    <!-- Human review workspace -->
    <div id="pane-review" class="content-pane">
      <div class="workspace-hero compact"><div><div class="overview-eyebrow">Human review</div><h1>Решения редактора</h1><p>Читайте остановленные материалы, изучайте factual findings и при необходимости фиксируйте осознанное редакционное исключение.</p></div><button class="modal-secondary" onclick="loadReviewQueue()">↻ Обновить</button></div>
      <div class="review-queue" id="review-queue"><div class="empty-state">Загружаю материалы review…</div></div>
    </div>

    <!-- Publish workspace -->
    <div id="pane-publish" class="content-pane">
      <div class="workspace-hero compact"><div><div class="overview-eyebrow">Publish</div><h1>Очередь редакционной публикации</h1><p>Публикуйте только материалы с factual pass. Черновики остаются доступными для ручного review.</p></div><button class="modal-secondary" onclick="loadDrafts()">↻ Обновить очередь</button></div>
      <div class="drafts-list publish-drafts" id="publish-drafts-list"><div class="empty-state"><div class="empty-icon">☰</div>Загрузка черновиков…</div></div>
    </div>
  </div>

</div>

<!-- PDF Scout is a focused workflow, not a compressed sidebar form. -->
<div class="modal-backdrop" id="pdf-scout-modal" role="dialog" aria-modal="true" aria-labelledby="pdf-scout-title" onclick="closePdfScout(event)">
  <section class="pdf-scout-dialog">
    <div class="pdf-dialog-header">
      <div>
        <div class="overview-eyebrow">Collect · Manual source</div>
        <h2 id="pdf-scout-title">Добавить PDF или журнал</h2>
        <p>Сначала извлечём и проверим evidence. Writer запускается только после успешной проверки.</p>
      </div>
      <button class="modal-close" onclick="closePdfScout()" aria-label="Закрыть">×</button>
    </div>
    <div class="pdf-form-grid">
      <label class="pdf-field pdf-field-wide"><span>Официальный URL источника <em>необязательно для локального PDF</em></span>
        <input id="pdf-url-input" type="url" placeholder="https://vendor.com/news/product-release или https://online.fliphtml5.com/...">
      </label>
      <label class="pdf-field pdf-field-wide"><span>Файл документа</span>
        <input id="pdf-file-input" type="file" accept=".pdf,.txt,.html">
        <small>PDF с полноценным текстовым слоем предпочтительнее. Повреждённые streams и OCR-мусор будут отклонены.</small>
      </label>
      <label class="pdf-field"><span>Тип материала</span>
        <select id="pdf-format-select">
          <option value="magazine">Журнал / выпуск</option>
          <option value="review">Продуктовый материал</option>
          <option value="datasheet">Datasheet / specification</option>
        </select>
      </label>
      <label class="pdf-field"><span>Максимум тем</span>
        <input id="pdf-topics-input" type="number" value="7" min="1" max="50">
        <small>Фактическое разделение доступно только при подтверждённых самостоятельных статьях.</small>
      </label>
    </div>
    <div class="pdf-evidence-note"><b>Evidence gate:</b> Nemotron проверит, есть ли в документе названный предмет, источники и достаточные факты для выбранного editorial format.</div>
    <div class="pdf-dialog-actions">
      <button class="modal-secondary" onclick="closePdfScout()">Отмена</button>
      <button class="modal-secondary" onclick="runPdfScoutUI(false)">Создать briefs</button>
      <button class="modal-primary" onclick="runPdfScoutUI(true)">Проверить и написать статью →</button>
    </div>
  </section>
</div>

<!-- Read-only draft preview. The article remains unpublished until approval. -->
<div class="modal-backdrop" id="draft-reader-modal" role="dialog" aria-modal="true" aria-labelledby="draft-reader-title" onclick="closeDraftReader(event)">
  <section class="draft-reader-dialog">
    <div class="draft-reader-topbar"><span class="preview-label">UNPUBLISHED DRAFT · READ ONLY</span><div><button class="modal-secondary" id="draft-reader-publish" type="button">✓ Опубликовать</button><button class="modal-close" onclick="closeDraftReader()" aria-label="Закрыть">×</button></div></div>
    <article id="draft-reader-content" class="draft-reader-content"></article>
  </section>
</div>

<div class="modal-backdrop" id="review-reader-modal" role="dialog" aria-modal="true" onclick="closeReviewReader(event)">
  <section class="draft-reader-dialog">
    <div class="draft-reader-topbar"><span class="preview-label">EDITORIAL REVIEW · NOT PUBLISHED</span><button class="modal-close" onclick="closeReviewReader()" aria-label="Закрыть">×</button></div>
    <article id="review-reader-content" class="draft-reader-content"></article>
  </section>
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
let selectedTopicIndices = new Set();
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
function subscribeRun(runId, completionTab = '') {
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
    setTimeout(() => {
      loadBriefs(); loadArticle(); loadDrafts();
      if (completionTab) showTab(completionTab);
    }, 700);
  });
  es.addEventListener('ping', () => {});
  es.onerror = () => { es.close(); pipelineRunning = false; renderAgents(); };
}

// ── Run agent / pipeline ────────────────────────────────────────────
async function runAgent(agentId) {
  if (agentId === '2' && selectedTopicIndices.size > 1) {
    // A multi-selection is deliberately a full per-topic workflow. Do not
    // silently pick index 0 through Writer's legacy single-topic command.
    await runSelectedTopics();
    return;
  }
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

function openPdfScout() {
  document.getElementById('pdf-scout-modal').classList.add('open');
  setTimeout(() => document.getElementById('pdf-url-input').focus(), 0);
}

function closePdfScout(event) {
  if (event && event.target !== document.getElementById('pdf-scout-modal')) return;
  document.getElementById('pdf-scout-modal').classList.remove('open');
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
      if (!res.ok || !data.file_path) {
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      filePath = data.file_path;
      appendLog('1b', '✓ Файл загружен: ' + data.filename);
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
      closePdfScout();
      subscribeRun(data.run_id, writeFlag ? 'article' : 'briefs');
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

async function deleteTopic(index) {
  const topic = briefsData?.topics?.[index]?.topic || 'эту тему';
  if (!confirm(`Удалить «${topic}»? Writer не сможет использовать её.`)) return;
  const r = await fetch(`/briefs/${index}`, {method:'DELETE'}).then(res => res.json()).catch(() => null);
  if (!r || r.error) { toast(r?.error || 'Не удалось удалить тему', 'err'); return; }
  selectedTopicIndices = new Set([...selectedTopicIndices]
    .filter(value => value !== index)
    .map(value => value > index ? value - 1 : value));
  if (index === selectedTopicIndex) {
    selectedTopicIndex = null;
    document.getElementById('selected-topic-pill').style.display = 'none';
  }
  toast(`Удалено. Осталось тем: ${r.remaining}`, 'info');
  await loadBriefs();
  loadOverview();
}

// ── Tabs ───────────────────────────────────────────────────────────
function showTab(name) {
  const tabs = ['overview', 'collect', 'briefs', 'article', 'review', 'publish', 'log'];
  document.querySelectorAll('.tab').forEach((t, i) => t.classList.toggle('active', tabs[i] === name));
  tabs.forEach(n => document.getElementById(`pane-${n}`).classList.toggle('active', n === name));
  document.getElementById('clear-btn').style.display = name === 'log' ? '' : 'none';
  if (name === 'overview') loadOverview();
  if (name === 'briefs') loadBriefs();
  if (name === 'article') loadArticle();
  if (name === 'review') loadReviewQueue();
  if (name === 'publish') loadDrafts();
}

// ── Overview: concise operating state, not a raw diagnostic log ─────
async function loadOverview() {
  const [status, briefs, drafts] = await Promise.all([
    fetch('/status').then(r => r.json()).catch(() => null),
    fetch('/briefs').then(r => r.json()).catch(() => null),
    fetch('/drafts').then(r => r.json()).catch(() => null),
  ]);
  const el = document.getElementById('overview-content');
  if (!status) {
    el.innerHTML = '<div class="empty-state"><div class="empty-icon">!</div>Не удалось получить состояние Control Room</div>';
    return;
  }
  briefsData = briefs;
  const agents = status.agents || {};
  const running = Object.values(agents).filter(s => s === 'running').length;
  const failed = Object.values(agents).filter(s => s === 'error').length;
  const topics = briefs?.topics?.length || 0;
  const draftCount = drafts?.drafts?.length || 0;
  const nextTitle = running ? 'Пайплайн выполняется' : topics ? 'Выберите тему для подготовки статьи' : 'Соберите свежие сигналы';
  const nextCopy = running
    ? 'Следите за прогрессом в разделе «Запуски и логи». Результаты появятся после завершения этапа.'
    : topics ? `${topics} тем готовы к редакционному решению. Выбранная тема станет входом для Writer.`
    : 'Запустите сбор новостей или загрузите технический PDF, чтобы получить проверяемые темы.';
  const nextAction = running ? "showTab('log')" : topics ? "showTab('briefs')" : "runAgent('1')";
  const nextLabel = running ? 'Открыть ход запуска' : topics ? 'Открыть темы' : 'Запустить сбор';
  const dbHealth = status.db_connected ? (status.allow_db_writes ? 'DB · запись разрешена' : 'DB · только чтение') : 'База данных не подключена';
  const llmHealth = status.llm_mock ? 'LLM · тестовый режим' : (status.llm_api_base !== 'не задан' ? 'LLM · подключён' : 'LLM не настроен');

  el.innerHTML = `
    <div class="overview-grid">
      <div class="metric-card"><div class="metric-label">Активные запуски</div><div class="metric-value">${running}</div><div class="metric-note">${failed ? `${failed} требуют внимания` : 'Без блокирующих ошибок'}</div></div>
      <div class="metric-card"><div class="metric-label">Темы готовы</div><div class="metric-value">${topics}</div><div class="metric-note">Переходят в редакционный план</div></div>
      <div class="metric-card"><div class="metric-label">Черновики</div><div class="metric-value">${draftCount}</div><div class="metric-note">Ожидают review или публикации</div></div>
      <div class="metric-card"><div class="metric-label">Пайплайн</div><div class="metric-value">${status.pipeline === 'running' ? 'RUN' : 'READY'}</div><div class="metric-note">${status.pipeline === 'running' ? 'Этапы выполняются' : 'Готов к следующему запуску'}</div></div>
    </div>
    <div class="overview-layout">
      <section class="overview-card next-action">
        <div class="overview-eyebrow">Рекомендуемое действие</div>
        <h1 class="overview-title">${escHtml(nextTitle)}</h1>
        <p class="overview-copy">${escHtml(nextCopy)}</p>
        <div class="workflow-steps" aria-label="Этапы редакционного процесса">
          <button type="button" class="workflow-step ${!topics && !running ? 'active' : ''}" onclick="workflowAction('collect')" title="Открыть загрузку PDF или начать сбор источников"><b>01 · Collect</b><span>Источники и PDF</span></button>
          <button type="button" class="workflow-step ${topics ? 'active' : ''}" onclick="workflowAction('plan')" title="Открыть темы и проверить evidence"><b>02 · Plan</b><span>Темы и evidence</span></button>
          <button type="button" class="workflow-step" onclick="workflowAction('create')" title="Открыть подготовленную статью или выбрать тему"><b>03 · Create</b><span>Текст и проверка</span></button>
          <button type="button" class="workflow-step" onclick="workflowAction('review')" title="Открыть качество, SEO и журнал выполнения"><b>04 · Review</b><span>SEO и качество</span></button>
          <button type="button" class="workflow-step" onclick="workflowAction('publish')" title="Перейти к очереди черновиков справа"><b>05 · Publish</b><span>Approval и каналы</span></button>
        </div>
        <button class="overview-action" onclick="${nextAction}">${nextLabel} →</button>
      </section>
      <aside class="overview-card">
        <div class="overview-eyebrow">Состояние системы</div>
        <div class="source-summary"><span>${escHtml(llmHealth)}</span><span class="health ${status.llm_api_base !== 'не задан' ? 'ok' : 'watch'}">${status.llm_api_base !== 'не задан' ? 'READY' : 'SETUP'}</span></div>
        <div class="source-summary"><span>${escHtml(dbHealth)}</span><span class="health ${status.db_connected ? 'ok' : 'watch'}">${status.db_connected ? 'ONLINE' : 'OFFLINE'}</span></div>
        <div class="source-summary"><span>Состояние источников</span><span class="health ok">MONITORED</span></div>
        <div class="source-summary"><span>Детальная диагностика</span><button class="clear-btn" onclick="showTab('log')">Открыть логи →</button></div>
      </aside>
    </div>`;
}

// ── Workflow navigation: cards are real, safe actions — never dead UI. ──
function workflowAction(step) {
  if (step === 'collect') {
    openPdfScout();
    return;
  }
  if (step === 'plan') {
    showTab('briefs');
    return;
  }
  if (step === 'create') {
    if (!briefsData?.topics?.length) {
      toast('Сначала соберите источники и создайте тему', 'info');
      openPdfScout();
      return;
    }
    if (selectedTopicIndex === null) {
      toast('Выберите тему в разделе «План» перед запуском Writer', 'info');
      showTab('briefs');
      return;
    }
    showTab('article');
    return;
  }
  if (step === 'review') {
    showTab('review');
    return;
  }
  if (step === 'publish') {
    showTab('publish');
    toast('Публикуйте только материалы со статусом factual pass.', 'info');
  }
}

// ── Briefs ─────────────────────────────────────────────────────────
async function loadBriefs() {
  const data = await fetch('/briefs').then(r=>r.json()).catch(()=>null);
  briefsData = data;
  if (Array.isArray(data?.selected_topic_indices)) {
    selectedTopicIndices = new Set(data.selected_topic_indices);
    selectedTopicIndex = selectedTopicIndices.size ? [...selectedTopicIndices][0] : null;
  }
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

  const gate = data.editorial_gate || {};
  const gateStatus = gate.decision === 'accept' ? 'evidence verified' : gate.decision === 'reject' ? 'evidence blocked' : 'evidence pending';
  const gateClass = gate.decision === 'accept' ? 'ok' : gate.decision === 'reject' ? 'blocked' : 'watch';
  const toolbar = `
    <div class="plan-header">
      <div><div class="overview-eyebrow">Plan</div><h1>Редакционный план</h1><p>Выберите только тему с достаточными evidence. Удалите слабые или нерелевантные темы до Writer.</p></div>
      <div class="plan-summary"><span class="health ${gateClass}">${escHtml(gateStatus)}</span><span>${data.topics.length} тем</span></div>
    </div>
    <div class="briefs-toolbar">
      <span>Сгенерировано: ${ts || '—'}</span>
      <span>·</span>
      <span>модель: ${escHtml(model)}</span>
      ${gate.recommended_format ? `<span>·</span><span>формат: ${escHtml(gate.recommended_format)}</span>` : ''}
      <span style="flex:1"></span>
      <span id="selected-topics-count">Выберите статьи для Writer</span>
      <button class="select-topic-btn" id="run-selected-topics" onclick="runSelectedTopics()" disabled>Продолжить цикл →</button>
    </div>`;

  const cards = data.topics.map((t, i) => {
    const urgency = (t.urgency || 'low').toLowerCase();
    const isSelected = selectedTopicIndices.has(i);
    const isPriority = isSelected;

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

    const isCurrentPriority = isSelected;
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
        <button class="delete-topic-btn" onclick="deleteTopic(${i})" title="Удалить тему до запуска Writer">
          ✕ Удалить
        </button>
        <button class="select-topic-btn ${isCurrentPriority ? 'active' : ''}" onclick="toggleTopic(${i})">
          ${isCurrentPriority ? '✓ Выбрана' : '＋ Выбрать'}
        </button>
      </div>
    </div>`;
  }).join('');

  el.innerHTML = toolbar + cards;
  updateSelectedTopicsUI();
}

function updateSelectedTopicsUI() {
  const count = selectedTopicIndices.size;
  const label = document.getElementById('selected-topics-count');
  const button = document.getElementById('run-selected-topics');
  if (label) label.textContent = count ? `Выбрано статей: ${count}` : 'Выберите статьи для Writer';
  if (button) button.disabled = !count;
}

async function toggleTopic(index) {
  if (selectedTopicIndices.has(index)) selectedTopicIndices.delete(index);
  else selectedTopicIndices.add(index);
  selectedTopicIndex = selectedTopicIndices.size ? [...selectedTopicIndices][0] : null;
  const result = await fetch('/briefs/select-many', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({indices: [...selectedTopicIndices].sort((a, b) => a - b)})
  }).then(r => r.json()).catch(() => null);
  if (!result || result.error) { toast(result?.error || 'Не удалось сохранить выбор', 'err'); return; }
  await loadBriefs();
}

async function runSelectedTopics() {
  const indices = [...selectedTopicIndices].sort((a, b) => a - b);
  if (!indices.length) { toast('Выберите хотя бы одну тему', 'err'); return; }
  showTab('log');
  const result = await fetch('/briefs/run-selected', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({indices})
  }).then(r => r.json()).catch(() => null);
  if (!result || result.error) { toast(result?.error || 'Не удалось запустить цикл', 'err'); return; }
  toast(`Запущен полный редакционный цикл для ${result.count} статей`, 'info');
  subscribeRun(result.run_id, 'publish');
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
    const sc = qc.score ?? '—';
    const factualPass = qc.approved === true && qc.factual_verdict === 'pass';
    const cls = factualPass ? 'good' : qc.status === 'needs_revision' ? 'warn' : 'bad';
    const label = factualPass ? 'Factual pass' : qc.status === 'needs_revision' ? 'Needs revision' : 'Publication blocked';
    scoreHtml += `<div class="article-score ${cls}">Quality: ${sc}/100 · ${label}</div>`;
  }
  if (lint && lint.issues && lint.issues.length) {
    scoreHtml += `<div style="margin-top:8px;font-size:11px;color:var(--text-dim)">
      ${lint.issues.map(i=>`<span style="display:block;margin-bottom:3px">${i.severity==='error'?'✖':'⚠'} ${escHtml(i.message)}</span>`).join('')}
    </div>`;
  }

  const sourceBrief = m.source_topic_brief || {};
  const sources = sourceBrief.expanded_sources || sourceBrief.sources || [];
  const unsupported = Array.isArray(qc?.unsupported_claims) ? qc.unsupported_claims : [];
  const missingEvidence = Array.isArray(qc?.missing_evidence) ? qc.missing_evidence : [];
  const factualPass = qc?.approved === true && qc?.factual_verdict === 'pass';
  const evidenceHtml = `
    <section class="article-evidence ${factualPass ? 'pass' : 'blocked'}">
      <div><div class="overview-eyebrow">Editorial evidence</div><h2>${factualPass ? 'Factual pass — материал готов к следующему этапу' : 'Проверка фактов не пройдена или ещё не выполнена'}</h2>
      <p>${factualPass ? 'Все существенные claims прошли Quality Checker. Перед публикацией всё равно проверьте первоисточник.' : 'SEO и публикация должны быть остановлены, пока Quality Checker не вернёт factual pass.'}</p></div>
      <div class="evidence-details">
        <span class="health ${factualPass ? 'ok' : 'blocked'}">${factualPass ? 'FACTUAL PASS' : (qc?.status || 'PENDING').toUpperCase()}</span>
        <span>${sources.length} источник(ов) в brief</span>
      </div>
      ${unsupported.length ? `<div class="evidence-alert"><b>Unsupported claims</b>${unsupported.map(c => `<div>• ${escHtml(c.claim || c)}${c.reason ? ` — ${escHtml(c.reason)}` : ''}</div>`).join('')}</div>` : ''}
      ${missingEvidence.length ? `<div class="evidence-alert"><b>Недостающие доказательства</b><div>${missingEvidence.map(escHtml).join(' · ')}</div></div>` : ''}
      ${sources.length ? `<details class="evidence-sources"><summary>Показать sources и excerpts</summary>${sources.map(s => `<div><a href="${escHtml(s.url || '#')}" target="_blank" rel="noopener">${escHtml(s.title || s.url || 'Источник')} ↗</a><p>${escHtml((s.excerpt || 'Excerpt не сохранён. Используйте только ссылку, не как доказательство факта.').slice(0, 500))}</p></div>`).join('')}</details>` : ''}
    </section>`;

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

  let articleText = data.text || '';
  if (m.title && articleText.trim().startsWith(m.title)) articleText = articleText.trim().slice(m.title.length).trim();
  const bodyHtml = articleText ? formatBody(articleText) : '<em style="color:var(--text-dim)">Текст не загружен</em>';
  const formatLabel = {review: 'Buyer Guide', news: 'Industry News', insight: 'Engineering Insight', vendor: 'Vendor Profile'}[m.editorial_type] || 'SMTInsider Editorial';
  const primarySource = sources[0] || {};
  const publishedDate = m.generated_at ? new Date(m.generated_at).toLocaleDateString('en-US', {month:'short', day:'numeric', year:'numeric'}) : 'Draft preview';
  const bestFor = (m.tags || sourceBrief.keywords || []).slice(0, 4);
  const invalidSourceTitle = /<\/?html|xmlns=|<\/?body/i.test(primarySource.title || '');
  const sourceTitle = invalidSourceTitle
    ? (String(primarySource.url || '').includes('fliphtml5.com') ? 'SMT Today' : 'Official source')
    : (primarySource.title || primarySource.url || 'SMTInsider Editorial');
  const sourceLink = invalidSourceTitle && String(primarySource.url || '').includes('fliphtml5.com')
    ? 'https://smttoday.com/' : (primarySource.url || '');

  el.innerHTML = `
    <div class="article-toolbar preview-toolbar">
      <div><span class="preview-label">PUBLIC SITE PREVIEW</span><span class="preview-note">Отображение подготовленного материала в стиле SMTInsider</span></div>
      <div class="preview-actions"><button class="copy-btn" onclick="copyArticle()">⎘ Копировать текст</button>${scoreHtml}</div>
    </div>
    <article class="site-preview" aria-label="Предпросмотр статьи в публичном формате">
      <div class="site-preview-hero">
        <main>
          <a class="preview-back" href="#" onclick="showTab('briefs');return false;">← Back to editorial plan</a>
          <div class="preview-kicker">${escHtml(formatLabel)} &nbsp; ${escHtml(m.category || 'SMT Equipment')} &nbsp; ${escHtml(publishedDate)}</div>
          <h1>${escHtml(m.title || 'Untitled article')}</h1>
          <p class="preview-dek">${escHtml(m.summary || sourceBrief.angle || 'Summary will appear here after the article is prepared.')}</p>
        </main>
        <aside class="preview-context"><div>REVIEW CONTEXT</div><dl><dt>CATEGORY</dt><dd>${escHtml(m.category || 'SMT Equipment')}</dd><dt>SOURCE</dt><dd>${sourceLink ? `<a href="${escHtml(sourceLink)}" target="_blank" rel="noopener">${escHtml(sourceTitle)} ↗</a>` : escHtml(sourceTitle)}</dd><dt>AUTHOR</dt><dd>SMTInsider Editorial</dd><dt>STATUS</dt><dd>${factualPass ? 'Fact-checked draft' : 'Editorial draft — not publishable'}</dd><dt>LAST REVIEWED</dt><dd>${escHtml(publishedDate)}</dd></dl></aside>
      </div>
      <section class="preview-decision-grid">
        <div><b>BEST FOR</b>${bestFor.length ? `<ul>${bestFor.map(v => `<li>${escHtml(v)}</li>`).join('')}</ul>` : '<p>Confirm the production fit from the verified source before publication.</p>'}</div>
        <div><b>WATCH FOR</b><p>Validate every product-specific claim, capacity figure, interface and process outcome against the official source material.</p></div>
        <div><b>DECISION OUTPUT</b><p>${escHtml(sourceBrief.angle || 'Use this article to define the technical questions that must be answered before a shortlist or production decision.')}</p></div>
      </section>
      <section class="preview-body">${bodyHtml}</section>
    </article>
    <details class="preview-qa"><summary>Internal editorial evidence and Quality Checker report</summary>${evidenceHtml}</details>`;
}

async function copyArticle() {
  const data = await fetch('/article').then(r=>r.json()).catch(()=>null);
  if (!data?.text) { toast('Нет текста', 'err'); return; }
  try {
    await navigator.clipboard.writeText(data.text);
    toast('Скопировано!', 'ok');
  } catch { toast('Ошибка копирования', 'err'); }
}

// ── Human review queue ─────────────────────────────────────────────
async function loadReviewQueue() {
  const data = await fetch('/review-queue').then(r => r.json()).catch(() => null);
  const el = document.getElementById('review-queue');
  if (!data || data.error) { el.innerHTML = '<div class="empty-state">Не удалось загрузить review queue</div>'; return; }
  if (!data.items.length) { el.innerHTML = '<div class="empty-state">Нет материалов, ожидающих редакционного решения</div>'; return; }
  el.innerHTML = data.items.map(item => {
    const override = item.human_override?.approved;
    const state = override ? 'editorial override' : item.approved ? 'factual pass' : item.status || 'pending';
    const cls = override || item.approved ? 'ok' : 'blocked';
    return `<article class="review-card"><div><span class="health ${cls}">${escHtml(state)}</span><h2>${escHtml(item.title)}</h2><p>${item.score !== null && item.score !== undefined ? `Quality score: ${item.score}/100` : 'Quality score unavailable'}</p>${item.issues?.length ? `<small>${escHtml(item.issues[0])}</small>` : ''}</div><div class="review-card-actions"><button class="btn-read" onclick="openReviewReader('${escHtml(item.id)}')">Читать и проверить</button>${!override && !item.approved ? `<button class="modal-secondary" onclick="overrideReviewItem('${escHtml(item.id)}')">Редакторское исключение</button>` : ''}${override ? `<button class="overview-action" onclick="continueReviewItem('${escHtml(item.id)}')">Продолжить SEO →</button>` : ''}</div></article>`;
  }).join('');
}

async function openReviewReader(itemId) {
  const modal = document.getElementById('review-reader-modal');
  const content = document.getElementById('review-reader-content');
  modal.classList.add('open');
  content.innerHTML = '<div class="empty-state">Загружаю статью и factual report…</div>';
  const data = await fetch(`/review-queue/${encodeURIComponent(itemId)}`).then(r => r.json()).catch(() => null);
  if (!data || data.error) { content.innerHTML = `<div class="empty-state">${escHtml(data?.error || 'Не удалось открыть материал')}</div>`; return; }
  const m = data.meta || {}; const q = m.quality_check || {};
  content.innerHTML = `<div class="draft-reader-meta">${escHtml(q.status || 'pending')} · ${escHtml(m.editorial_type || 'editorial')}</div><h1>${escHtml(m.title || 'Untitled')}</h1><p class="draft-reader-summary">${escHtml(m.summary || '')}</p><div class="evidence-alert"><b>Factual findings</b>${(q.unsupported_claims || q.issues || []).map(x => `<div>• ${escHtml(x.claim || x)}</div>`).join('') || '<div>Нет сохранённых findings</div>'}</div><div class="draft-reader-body">${formatDraftReaderBody(data.text)}</div>`;
}

function closeReviewReader(event) { if (event && event.target !== document.getElementById('review-reader-modal')) return; document.getElementById('review-reader-modal').classList.remove('open'); }

async function overrideReviewItem(itemId) {
  const reason = prompt('Почему вы принимаете это редакторское исключение? Причина будет сохранена в metadata и нужна для публикации.');
  if (!reason) return;
  const result = await fetch(`/review-queue/${encodeURIComponent(itemId)}/override`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({reason})}).then(r=>r.json()).catch(()=>null);
  if (!result || result.error) { toast(result?.error || 'Не удалось сохранить решение', 'err'); return; }
  toast('Редакторское исключение сохранено и зафиксировано', 'info');
  loadReviewQueue();
}

async function continueReviewItem(itemId) {
  const result = await fetch(`/review-queue/${encodeURIComponent(itemId)}/continue`, {method:'POST'}).then(r=>r.json()).catch(()=>null);
  if (!result || result.error) { toast(result?.error || 'Не удалось продолжить цикл', 'err'); return; }
  showTab('log');
  toast('Запущены SEO, Distributor и создание непубличного draft. Публикация остаётся ручным действием.', 'info');
  subscribeRun(result.run_id, 'publish');
}

// ── Drafts ─────────────────────────────────────────────────────────
async function loadDrafts() {
  const data = await fetch('/drafts').then(r=>r.json()).catch(()=>null);
  const targets = [document.getElementById('sidebar-drafts-list'), document.getElementById('publish-drafts-list')].filter(Boolean);
  const setDrafts = html => targets.forEach(el => { el.innerHTML = html; });
  if (!data || data.error) {
    setDrafts(`<div class="empty-state"><div class="empty-icon">🗄</div><span style="font-size:11px">${data?.error||'Ошибка загрузки'}</span></div>`);
    return;
  }
  if (!data.drafts.length) {
    setDrafts('<div class="empty-state"><div class="empty-icon">☰</div>Черновиков нет</div>');
    return;
  }
  setDrafts(data.drafts.map(d => {
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
        <button class="btn-read" onclick="openDraftReader(${d.id})">Читать</button>
        <button class="btn-approve" onclick="approveDraft(${d.id})">✓ Опубликовать</button>
        <button class="btn-del" onclick="deleteDraft(${d.id})" title="Удалить черновик">✕</button>
      </div>
    </div>`;
  }).join(''));
}

function formatDraftReaderBody(text) {
  return String(text || '').split('\n').map(line => {
    const trimmed = line.trim();
    if (!trimmed) return '';
    if (/^##\s+/.test(trimmed)) return `<h2>${escHtml(trimmed.replace(/^##\s+/, ''))}</h2>`;
    if (/^#\s+/.test(trimmed)) return `<h1>${escHtml(trimmed.replace(/^#\s+/, ''))}</h1>`;
    return `<p>${escHtml(trimmed)}</p>`;
  }).join('');
}

async function openDraftReader(id) {
  const modal = document.getElementById('draft-reader-modal');
  const content = document.getElementById('draft-reader-content');
  content.innerHTML = '<div class="empty-state">Загружаю черновик…</div>';
  modal.classList.add('open');
  const r = await fetch(`/drafts/${id}`).then(res => res.json()).catch(() => null);
  if (!r || r.error || !r.draft) {
    content.innerHTML = `<div class="empty-state">${escHtml(r?.error || 'Не удалось открыть черновик')}</div>`;
    return;
  }
  const d = r.draft;
  const date = d.date ? new Date(d.date).toLocaleDateString('en-US', {month:'short', day:'numeric', year:'numeric'}) : 'Draft';
  content.innerHTML = `
    <div class="draft-reader-meta">${escHtml(d.editorial_type || 'Editorial')} · ${escHtml(d.category_name || 'SMT Equipment')} · ${escHtml(date)}</div>
    <h1 id="draft-reader-title">${escHtml(d.title || 'Untitled')}</h1>
    ${d.summary ? `<p class="draft-reader-summary">${escHtml(d.summary)}</p>` : ''}
    <div class="draft-reader-context"><span>Source: ${escHtml(d.source || 'SMTInsider Editorial')}</span>${d.source_url ? `<a href="${escHtml(d.source_url)}" target="_blank" rel="noopener">Открыть источник ↗</a>` : ''}</div>
    <div class="draft-reader-body">${formatDraftReaderBody(d.content)}</div>`;
  const publish = document.getElementById('draft-reader-publish');
  publish.onclick = async () => { await approveDraft(id); closeDraftReader(); };
}

function closeDraftReader(event) {
  if (event && event.target !== document.getElementById('draft-reader-modal')) return;
  document.getElementById('draft-reader-modal').classList.remove('open');
}

async function approveDraft(id) {
  const r = await fetch(`/drafts/${id}/approve`, {method:'POST'}).then(r=>r.json());
  if (r.ok) { toast(`Опубликовано #${id}`, 'ok'); loadDrafts(); }
  else toast(r.error||'Ошибка', 'err');
}

async function deleteDraft(id) {
  if (!confirm(`Удалить черновик #${id}?`)) return;
  const r = await fetch(`/drafts/${id}`, {method:'DELETE'}).then(r=>r.json());
  if (r.ok) { toast(`Удалено #${id}`, 'ok'); loadDrafts(); }
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
loadOverview();
setInterval(loadStatus, 30000);
setInterval(loadOverview, 30000);
</script>
</body>
</html>
"""



@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(HTML)
