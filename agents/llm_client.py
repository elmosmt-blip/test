#!/usr/bin/env python3
"""
llm_client.py — общий клиент для подключения ЛЮБОЙ открытой модели
через OpenAI-совместимый эндпоинт /v1/chat/completions.

Работает "из коробки" с:
  - Ollama          (http://localhost:11434/v1)
  - vLLM             (http://localhost:8000/v1)
  - llama.cpp server (http://localhost:8080/v1)
  - LM Studio        (http://localhost:1234/v1)
  - text-generation-webui (http://localhost:5000/v1)
  - OpenRouter        (https://openrouter.ai/api/v1)
  - Together.ai, Groq, Fireworks и любой другой OpenAI-совместимый провайдер

Настройка через переменные окружения:
  export LLM_API_BASE="http://localhost:11434/v1"
  export LLM_API_KEY="ollama"                # для локальных серверов значение не важно,
                                              # но заголовок Authorization нужен не всем —
                                              # если сервер ругается, просто не задавайте ключ
  export LLM_MODEL="llama3.1:8b"
  export LLM_TIMEOUT="180"                   # секунд, локальные модели бывают медленными

Никакого SaaS/Anthropic/OpenAI API не используется — только то, что укажете сами.
"""

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

import json
import time
import re
from pathlib import Path
import requests

# Загружаем .env из корня проекта, чтобы CLI-агенты и dashboard работали
# без ручного `source .env` (если python-dotenv недоступен — просто пропускаем).
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass

LLM_API_BASE = os.environ.get("LLM_API_BASE", "http://localhost:11434/v1").rstrip("/")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "llama3.1:8b")
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "180"))
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "3"))
LLM_MOCK = os.environ.get("LLM_MOCK", os.environ.get("MOCK_LLM", "")).lower() in {"1", "true", "yes", "on"}


class LLMError(RuntimeError):
    pass


def _mock_chat(messages, model: str = None, json_mode: bool = False) -> str:
    """Локальный deterministic mock для развёртывания в sandbox без внешней LLM.

    В production выключите LLM_MOCK в .env и задайте реальный OpenAI-compatible
    endpoint. Mock нужен только чтобы dashboard/pipeline можно было проверить
    сразу после распаковки проекта.
    """
    system = (messages[0].get("content", "") if messages else "").lower()
    user = (messages[-1].get("content", "") if messages else "")

    if "ответь одним словом" in user.lower() or (len(system) < 120 and "connection test" in system):
        return "OK"

    if "редакционный аналитик" in system or "свежие сигналы" in user.lower() or "сырые сигналы" in user.lower():
        m = re.search(r"максимум\s+(\d+)", user, re.IGNORECASE)
        max_topics = int(m.group(1)) if m else 3

        # If real fresh signals were collected, the mock selector must not invent
        # a different topic. It builds briefs directly from the supplied signal
        # lines: "- YYYY-MM-DD | title | snippet | url".
        signal_rows = []
        for line in user.splitlines():
            line = line.strip()
            if not line.startswith("-") or "|" not in line:
                continue
            parts = [p.strip() for p in line.lstrip("- ").split("|")]
            if len(parts) >= 4:
                date = re.sub(r"\s*\[score:-?\d+\]\s*", "", parts[0]).strip()
                title = parts[1]
                url = parts[-1]
                snippet = " | ".join(parts[2:-1])
                if title and url:
                    signal_rows.append({"date": date, "title": title, "snippet": snippet, "url": url})
        if signal_rows:
            topics = []
            for row in signal_rows[:max_topics]:
                title = row["title"]
                lower = title.lower()
                if any(k in lower for k in ["aoi", "inspection", "x-ray", "test", "fixture"]):
                    category = "Quality Control"
                    fmt = "news" if "launch" in lower or "appoint" in lower else "insight"
                    keywords = ["inspection", "SMT", "process control"]
                elif any(k in lower for k in ["pcb", "pcba", "assembly", "ems", "manufacturing"]):
                    category = "SMT Equipment"
                    fmt = "news"
                    keywords = ["electronics manufacturing", "SMT", "production"]
                else:
                    category = "Process Engineering"
                    fmt = "news"
                    keywords = ["SMT", "manufacturing", "quality"]
                topics.append({
                    "topic": title,
                    "angle": "Fresh news signal from the last configured lookback window; frame it as practical engineering impact for SMT production teams.",
                    "format": fmt,
                    "keywords": keywords,
                    "category": category,
                    "urgency": "HIGH" if row.get("date") not in {"unknown", ""} else "MEDIUM",
                    "source_count": 1,
                    "source_notes": f"Fresh source dated {row.get('date','unknown')}: {title}",
                    "sources": [{"title": title, "url": row["url"], "date": row.get("date", "unknown")}],
                })
            return json.dumps({"topics": topics}, ensure_ascii=False)

        topics = [
            {
                "topic": "Fresh SMT news collection needs real RSS/search signals",
                "angle": "No real signals were supplied to the mock selector; this fallback is only for local tests.",
                "format": "news",
                "keywords": ["SMT", "news", "fresh signals"],
                "category": "SMT Equipment",
                "urgency": "LOW",
                "source_count": 0,
                "source_notes": "Mock fallback only; do not publish.",
                "sources": [],
            }
        ][:max_topics]
        return json.dumps({"topics": topics}, ensure_ascii=False)

    if ("технический редактор" in system or "напиши статью" in user.lower() or "self-editor" in system
            or "чек-листу" in system or "исправляет только" in system.lower()):
        is_revision_pass = "self-editor" in system or "чек-листу" in system
        is_repair_pass = "исправляет только" in system.lower()
        topic = "AOI False-Call Reduction in SMT Production"
        category = "Quality Control"
        keywords = ["AOI", "false calls", "SMT quality", "inspection"]
        try:
            start = user.find("{")
            end = user.rfind("}") + 1
            brief = json.loads(user[start:end]) if start != -1 and end > start else {}
            topic = brief.get("topic") or topic
            category = brief.get("category") or category
            keywords = brief.get("keywords") or keywords
        except Exception:
            pass
        # Revision/repair pass: try to recover the draft title from the
        # prompt so the mock echoes a plausible "revised"/"repaired" title
        # instead of the generic default.
        if is_revision_pass or is_repair_pass:
            m = re.search(r"ЗАГОЛОВОК:\s*(.+)", user)
            if m:
                topic = m.group(1).strip() or topic

        if any(k in topic.lower() for k in ["x-ray", "xray", "axi", "tr7600"]):
            body = f"""{topic}

TRI's new line-scan 3D AXI platform is relevant for SMT teams because hidden-joint inspection often becomes the constraint after reflow. Faster X-ray inspection matters only if it improves real production decisions: which packages can be inspected, how much review work remains, and whether the results connect to factory data.

Where the System Fits

AXI is needed when AOI cannot see the full solder geometry. BGAs, bottom-terminated components, SiP assemblies, and through-hole joints all create inspection cases where optical evidence is incomplete. A high-throughput AXI platform can support broader sampling or inline inspection if scan, reconstruction, and review times stay inside the production takt.

What Engineers Should Verify

The first check is package coverage. Validate BGA voiding, bridges, insufficient collapse, head-in-pillow indicators, THT barrel fill, and low-contrast SiP structures on the factory's own board mix. The second check is review burden. Higher image throughput does not help if false calls simply move to an operator queue.

Software and Data Integration

AI denoising, fine tuning, and buy-off workflows should be measured by programming time, calls per board, confirmed defects, and review minutes per lot. MES connectivity, IPC-CFX, SECS/GEM, SMEMA, and Hermes support matter because AXI data is most useful when it can be correlated with SPI, placement, reflow, and final test history.

Summary

The engineering value of a new AXI system is not only a throughput claim. The value is whether it improves hidden-defect coverage, reduces review friction, and gives process engineers traceable evidence for upstream corrective action."""
            payload = {
                "title": topic,
                "body": body,
                "summary": "A practical review of what higher-throughput 3D AXI changes for hidden-joint inspection, review load, and factory-data integration.",
                "category": "X-Ray Inspection",
                "tags": ["AXI", "3D X-ray", "BGA inspection", "SMT inspection", "Industry 4.0"],
            }
            if is_revision_pass:
                payload["revision_notes"] = ["Mock revision pass: draft already matched the checklist, kept as-is."]
            if is_repair_pass:
                payload["repairs_made"] = ["Mock repair pass: no changes needed beyond what was already flagged."]
            return json.dumps(payload, ensure_ascii=False)

        body = f"""{topic}

AOI false calls are rarely solved by a single sensitivity change. In most high-mix SMT environments, the review burden grows because the line is mixing several different problems under one label: illumination variation, solder paste volume scatter, component body reflection, fiducial instability, and true process drift. Treating all of them as one inspection problem usually moves the line from too many false calls to a higher escape risk.

Start With the Defect Taxonomy

The first step is to separate repeatable calls from random noise. A repeatable call that appears on the same reference designator, package family, or board area is process information. A random call that appears across unrelated packages is more likely to be recipe noise or image acquisition instability. Before changing thresholds, export the AOI review history and group calls by defect class, package, feeder lane, board side, and time window.

This classification gives the engineering team a safer decision tree. If bridges cluster around fine-pitch devices after stencil cleaning intervals, the answer is not an AOI recipe change. If missing-component calls cluster on black molded bodies under one camera angle, illumination and library training deserve attention. If polarity calls rise after a product changeover, the issue may be CAD/library mapping rather than placement quality.

Tune the Process Before the Inspection Window

AOI should confirm process control; it should not compensate for an unstable process. Engineers should compare false-call clusters with SPI volume data, placement offsets, reflow profile checks, and operator review notes. When a call class correlates with solder paste height or area, fix the print process first. When it correlates with placement offsets, check nozzle condition, feeder calibration, and vision centering. When it correlates with thermal mass, verify whether the profile still matches the current board mix.

Only after the process signal is separated from inspection noise should the AOI recipe be changed. The safest changes are usually local: package-specific thresholds, controlled lighting updates, improved golden images, and clearer defect classes for operators. Broad global sensitivity reductions are fast, but they can hide emerging process drift.

Measure Review Load and Escapes Together

A false-call reduction program should track at least three numbers: calls per board, confirmed defects per board, and post-AOI escapes. Calls per board alone is not enough. A recipe can look better simply because it stopped seeing marginal defects. The useful target is a lower review load with stable or improved confirmed-defect capture.

For production teams, a weekly Pareto of false calls by package and defect class is more valuable than a one-time recipe cleanup. It creates a feedback loop between process engineering and quality engineering. Over time, the AOI station becomes a process sensor rather than a bottleneck.

The Industry Takeaway

The fastest way to reduce AOI false calls is not to make AOI less sensitive. It is to make the defect language more precise, verify the process signals behind the calls, and tune only the inspection windows that are proven to be noise. That approach protects yield while reducing operator review fatigue."""
        payload = {
            "title": topic,
            "body": body,
            "summary": "A practical sequence for reducing AOI false calls without increasing escape risk.",
            "category": category,
            "tags": keywords,
        }
        if is_revision_pass:
            payload["revision_notes"] = ["Mock revision pass: draft already matched the checklist, kept as-is."]
        if is_repair_pass:
            payload["repairs_made"] = ["Mock repair pass: no changes needed beyond what was already flagged."]
        return json.dumps(payload, ensure_ascii=False)

    if "smm-редактор" in system or "linkedin_post" in system:
        m = re.search(r"Заголовок статьи:\s*(.+)", user)
        title = m.group(1).strip() if m else "SMT process update"
        lower = user.lower()
        if "x-ray" in lower or "axi" in lower or "tr7600" in lower:
            linkedin = f"{title}: the engineering question is not only scan speed. For SMT teams, higher-throughput AXI matters when it improves hidden-joint coverage, review consistency, and connection to MES/SPI/AOI process data. What is your biggest AXI constraint today: programming, review load, sampling policy, or line integration? #SMT #AXI #QualityEngineering"
            forum = "Key points:\n- Validate throughput on your own board mix, not only vendor demos.\n- Check BGA/THT/SiP defect coverage by resolution and package family.\n- Measure review time and false calls alongside scan speed.\n- Confirm CFX/Hermes/MES integration if AXI data is used for traceability.\n- Use AXI findings for upstream root-cause work, not just board disposition."
            email = f"{title}. A practical look at what higher-throughput AXI changes for hidden-joint inspection, review load, and factory-data integration. Читать полностью"
        else:
            linkedin = f"{title}: useful SMT updates should be evaluated by process impact, not only announcement language. Which production metric would you use first: throughput, defects, review load, or changeover time? #SMT #ElectronicsManufacturing"
            forum = "Key points:\n- Translate the announcement into process impact.\n- Identify which defect mode or bottleneck it affects.\n- Validate vendor claims on your own line data.\n- Link the update to control-plan or troubleshooting actions."
            email = f"{title}. What this update means for SMT process and quality teams. Читать полностью"
        return json.dumps({"linkedin_post": linkedin, "forum_answer": forum, "email_block": email}, ensure_ascii=False)

    if "аналитик контент-маркетинга" in system or "recommendations" in system:
        return json.dumps({
            "recommendations": [
                "Подключить реальные web-метрики (GA4/Plausible) и передавать sessions/subscribers/tool_ctr в Analyst, иначе рекомендации по трафику остаются ограниченными.",
                "Если БД не подключена, завершить настройку NEON_DATABASE_URL, чтобы видеть очередь черновиков и публикаций.",
                "После первой статьи проверить поисковые запросы по AOI/reflow и подготовить 2-3 связанных материала для внутренней перелинковки.",
            ]
        }, ensure_ascii=False)

    if json_mode:
        return json.dumps({"ok": True, "mock": True}, ensure_ascii=False)
    return "OK"


def chat(messages, temperature: float = 0.7, max_tokens: int = 2000,
         model: str = None, json_mode: bool = False) -> str:
    """
    Низкоуровневый вызов /v1/chat/completions.
    messages: [{"role": "system"|"user"|"assistant", "content": "..."}]
    Возвращает текст ответа модели (content первого choice).
    """
    if LLM_MOCK:
        return _mock_chat(messages, model=model, json_mode=json_mode)

    url = f"{LLM_API_BASE}/chat/completions"
    payload = {
        "model": model or LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        # Поддерживается не всеми серверами (vLLM/Ollama новых версий — да).
        # Если сервер не поддерживает — просто игнорирует поле, ничего не ломается.
        payload["response_format"] = {"type": "json_object"}

    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"

    last_err = None
    for attempt in range(1, LLM_MAX_RETRIES + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=LLM_TIMEOUT)
            if resp.status_code >= 400:
                raise LLMError(f"{resp.status_code} {resp.text[:300]}")
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (requests.RequestException, LLMError, KeyError, IndexError) as e:
            last_err = e
            if attempt < LLM_MAX_RETRIES:
                wait = 2 ** attempt
                print(f"  ⚠ LLM запрос неудачен (попытка {attempt}/{LLM_MAX_RETRIES}): {e}. "
                      f"Повтор через {wait}с…", file=sys.stderr)
                time.sleep(wait)
            else:
                raise LLMError(
                    f"Не удалось получить ответ от {url} после {LLM_MAX_RETRIES} попыток: {e}\n"
                    f"Проверь: запущен ли сервер модели, верен ли LLM_API_BASE='{LLM_API_BASE}' "
                    f"и LLM_MODEL='{LLM_MODEL}'."
                ) from last_err


def ask(system: str, user: str, **kw) -> str:
    """Короткий помощник: system-промпт + user-сообщение -> текст ответа."""
    return chat([{"role": "system", "content": system},
                 {"role": "user", "content": user}], **kw)


def ask_json(system: str, user: str, **kw) -> dict:
    """
    Просит модель вернуть строго JSON и парсит его.
    Открытые модели иногда оборачивают JSON в ```json ... ``` или добавляют текст —
    функция вычищает обёртку перед парсингом.
    """
    kw.setdefault("temperature", 0.4)
    raw = ask(system, user, json_mode=True, **kw)
    return _extract_json(raw)


def _extract_json(raw: str):
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    text = text.strip()
    # Если модель добавила пояснение до/после JSON — вырезаем по первой { и последней }
    start = text.find("{")
    start_arr = text.find("[")
    if start_arr != -1 and (start == -1 or start_arr < start):
        start = start_arr
        end = text.rfind("]") + 1
    else:
        end = text.rfind("}") + 1
    if start == -1 or end <= start:
        raise LLMError(f"Модель не вернула валидный JSON:\n{raw[:500]}")
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError as e:
        raise LLMError(f"Не удалось распарсить JSON от модели: {e}\nRAW:\n{raw[:500]}") from e


def healthcheck() -> bool:
    """Быстрая проверка, что сервер модели доступен и отвечает."""
    if LLM_MOCK:
        print(f"✅ LLM mock-режим активен: модель={LLM_MODEL}")
        print("   Для production выключи LLM_MOCK и задай реальный LLM_API_BASE.")
        return True
    try:
        out = ask("Ты — тестовый ассистент.", "Ответь одним словом: OK",
                   temperature=0.0, max_tokens=10)
        print(f"✅ LLM доступна: {LLM_API_BASE}  модель={LLM_MODEL}")
        print(f"   Ответ модели: {out.strip()[:50]}")
        return True
    except LLMError as e:
        print(f"❌ LLM недоступна: {e}")
        return False


if __name__ == "__main__":
    # python3 agents/llm_client.py  — быстрый health-check подключения
    ok = healthcheck()
    sys.exit(0 if ok else 1)
