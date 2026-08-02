#!/usr/bin/env python3
"""SMTInsider Agent #7 — YouTube Scout.

Finds recent, engineering-relevant SMT videos with yt-dlp. Discovery and
editorial preview work without a database; DB writes require both
NEON_DATABASE_URL and ALLOW_DB_WRITES=1.

Usage:
  python agents/agent-07-youtube-scout.py preview --days 30
  python agents/agent-07-youtube-scout.py scan --days 30 --limit 8
  python agents/agent-07-youtube-scout.py list
  python agents/agent-07-youtube-scout.py approve --id 42
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

# Prevent Windows console encoding failures while preserving useful logs.
for _name in ("stdout", "stderr"):
    _stream = getattr(sys, _name, None)
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

try:
    import yt_dlp
except ImportError as exc:  # pragma: no cover - deployment dependency
    raise SystemExit("❌ yt-dlp не установлен. Выполните: pip install -r requirements.txt") from exc

DATABASE_URL = os.environ.get("NEON_DATABASE_URL", "")

DEFAULT_SEARCH_QUERIES = [
    "SMT equipment launch 2026", "SMT placement machine demonstration 2026",
    "AOI SPI AXI inspection demo 2026", "SMT smart factory MES traceability",
    "electronics manufacturing factory tour", "reflow soldering process webinar",
    "SMT trade show product demonstration", "PCB assembly automation video",
]

SMT_TERMS = {
    "smt", "pcba", "pcb", "aoi", "spi", "axi", "x-ray", "reflow", "solder",
    "placement", "pick and place", "component", "bga", "qfn", "stencil", "ems",
    "electronics manufacturing", "factory", "inspection", "assembly", "underfill",
}
OFFICIAL_CHANNEL_HINTS = {
    "fuji", "asmpt", "koh young", "yamaha", "saki", "tri", "vitrox", "viscom",
    "mirtec", "mycronic", "nordson", "heller", "rehm", "indium", "ipc", "smta",
    "dymax", "aegis", "europlacer", "juki", "essemtec", "cyberoptics",
}


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _brief_video_queries(brief_path: str) -> list[str]:
    """Turn fresh Agent #1 briefs into targeted video discovery queries."""
    if not brief_path:
        return []
    try:
        topics = json.loads(Path(brief_path).read_text(encoding="utf-8")).get("topics", [])
    except Exception:
        return []
    limit = max(0, int(os.environ.get("YOUTUBE_MAX_DYNAMIC_QUERIES", "8")))
    queries: list[str] = []
    for topic in topics:
        title = re.sub(r"\s+", " ", str(topic.get("topic", ""))).strip()
        # Full editorial headlines can be too long/noisy for YouTube. Keep
        # the named subject and first meaningful terms, then bias to video.
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9+&.-]*", title)
        if len(words) < 2:
            continue
        query = " ".join(words[:12]) + " video"
        if query.lower() not in {item.lower() for item in queries}:
            queries.append(query)
        if len(queries) >= limit:
            break
    return queries


def configured_queries(brief_path: str = "") -> list[str]:
    raw = os.environ.get("YOUTUBE_SEARCH_QUERIES", "").strip()
    base = [query.strip() for query in raw.split(";") if query.strip()] if raw else DEFAULT_SEARCH_QUERIES
    dynamic = _brief_video_queries(brief_path)
    deduped: list[str] = []
    for query in [*dynamic, *base]:
        if query.lower() not in {item.lower() for item in deduped}:
            deduped.append(query)
    return deduped


class _ScoutYDLLogger:
    """Capture yt-dlp extractor errors without flooding the Control Room log."""
    def __init__(self) -> None:
        self.errors: list[str] = []
    def debug(self, _message: str) -> None: pass
    def warning(self, _message: str) -> None: pass
    def error(self, message: str) -> None:
        self.errors.append(message)


_SEARCH_FAILURES: list[str] = []


def _youtube_ydl_options() -> tuple[dict[str, Any], str]:
    """Use an installed JS runtime when available, otherwise quiet metadata search."""
    runtime = os.environ.get("YTDLP_JS_RUNTIME", "").strip().lower()
    if not runtime:
        for candidate in ("node", "deno", "bun", "qjs"):
            if shutil.which(candidate):
                runtime = candidate
                break
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "extract_flat": True,
        "skip_download": True,
        "logger": _ScoutYDLLogger(),
    }
    if runtime:
        options["js_runtimes"] = {runtime: {}}
    return options, runtime


def _video_date(entry: dict[str, Any]) -> datetime | None:
    raw = entry.get("upload_date", "")
    if raw:
        try:
            return datetime.strptime(raw, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    timestamp = entry.get("timestamp") or entry.get("release_timestamp")
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _relevance(video: dict[str, Any]) -> tuple[int, list[str]]:
    haystack = " ".join(str(video.get(key, "")) for key in ("title", "description", "channel")).lower()
    matched = [term for term in SMT_TERMS if term in haystack]
    score = len(matched)
    channel = str(video.get("channel", "")).lower()
    if any(hint in channel for hint in OFFICIAL_CHANNEL_HINTS):
        score += 3
        matched.append("official-channel")
    return score, matched


def _channel_type(channel: str) -> str:
    lower = (channel or "").lower()
    return "official_vendor" if any(hint in lower for hint in OFFICIAL_CHANNEL_HINTS) else "unknown"


def search_videos(query: str, max_results: int = 5, max_days: int = 30) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_days)
    options, runtime = _youtube_ydl_options()
    # A JS runtime lets yt-dlp open result pages and obtain upload_date,
    # duration and description. Flat search has no reliable date metadata and
    # therefore made every result look ineligible for a freshness-only scout.
    if runtime:
        options["extract_flat"] = False
    videos: list[dict[str, Any]] = []
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            # ytsearchdate is not implemented by every yt-dlp build. Use the
            # universally supported ytsearch and enforce freshness ourselves.
            info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False) or {}
        logger = options["logger"]
        raw_entries = [entry for entry in (info.get("entries") or []) if isinstance(entry, dict)]
        if logger.errors and not raw_entries:
            message = logger.errors[-1].split("\n", 1)[0][:240]
            _SEARCH_FAILURES.append(f"{query}: {message}")
            print(f"  ⚠ {query}: поиск недоступен — {message}")
            return []
        seen: set[str] = set()
        for entry in info.get("entries", []) or []:
            if not isinstance(entry, dict):
                continue
            video_id = str(entry.get("id", "")).strip()
            if not video_id or video_id in seen:
                continue
            seen.add(video_id)
            published_at = _video_date(entry)
            if not published_at or published_at < cutoff:
                continue
            channel = str(entry.get("channel") or entry.get("uploader") or "").strip()
            candidate = {
                "video_id": video_id,
                "title": str(entry.get("title", "")).strip(),
                "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
                "thumbnail_url": str(entry.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"),
                "channel": channel,
                "channel_url": str(entry.get("channel_url") or entry.get("uploader_url") or ""),
                "description": str(entry.get("description") or "").strip(),
                "duration_seconds": entry.get("duration"),
                "published_at": published_at,
                "query": query,
            }
            score, matched = _relevance(candidate)
            # Two topical terms, or a trusted official channel plus one term.
            if score < 2 or not candidate["title"]:
                continue
            candidate["relevance_score"] = score
            candidate["matched_terms"] = matched
            candidate["channel_type"] = _channel_type(channel)
            candidate["editorial_reason"] = (
                f"Recent {candidate['channel_type']} video matching: {', '.join(matched[:5])}"
            )
            videos.append(candidate)
    except Exception as exc:
        message = str(exc).split("\n", 1)[0][:240]
        _SEARCH_FAILURES.append(f"{query}: {message}")
        print(f"  ⚠ {query}: поиск недоступен — {message}")
    return videos


def require_db_write() -> None:
    if not DATABASE_URL:
        raise RuntimeError("NEON_DATABASE_URL не задан")
    if not _env_truthy("ALLOW_DB_WRITES"):
        raise RuntimeError("ALLOW_DB_WRITES=0 — запись video drafts заблокирована")


@contextmanager
def get_conn() -> Iterator[Any]:
    if not DATABASE_URL:
        raise RuntimeError("NEON_DATABASE_URL не задан")
    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("psycopg2-binary не установлен. Выполните: pip install -r requirements.txt") from exc
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def video_exists(url: str) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM videoitem WHERE youtube_url = %s", (url,))
            return cur.fetchone() is not None


def add_video(video: dict[str, Any]) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO videoitem
                   (title, youtube_url, thumbnail_url, channel, description, published_at, is_published)
                   VALUES (%s,%s,%s,%s,%s,%s,false) RETURNING id;""",
                (
                    video["title"], video["youtube_url"], video["thumbnail_url"], video["channel"],
                    video["description"][:2000], video["published_at"],
                ),
            )
            return cur.fetchone()[0]


def list_drafts() -> list[dict[str, Any]]:
    try:
        import psycopg2.extras
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("psycopg2-binary не установлен") from exc
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, title, channel, published_at FROM videoitem WHERE is_published=false ORDER BY published_at DESC LIMIT 30")
            return cur.fetchall()


def print_preview(video: dict[str, Any]) -> None:
    date = video["published_at"].date().isoformat()
    duration = f" · {int(video['duration_seconds']) // 60}:{int(video['duration_seconds']) % 60:02d}" if isinstance(video.get("duration_seconds"), (int, float)) else ""
    print(f"  ▶ {date}{duration} | {video['channel'] or 'Unknown channel'} [{video['channel_type']}]")
    print(f"    {video['title']}")
    print(f"    {video['youtube_url']}")
    print(f"    Why: {video['editorial_reason']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["preview", "scan", "list", "approve", "delete", "cleanup"])
    parser.add_argument("--days", type=int, default=int(os.environ.get("YOUTUBE_LOOKBACK_DAYS", "60")))
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--brief", default=os.environ.get("YOUTUBE_BRIEF_PATH", ""), help="briefs.json from Agent #1 for dynamic video queries")
    parser.add_argument("--id", type=int)
    args = parser.parse_args()

    if args.action in {"preview", "scan"}:
        _SEARCH_FAILURES.clear()
        queries = configured_queries(args.brief)
        dynamic_count = len(_brief_video_queries(args.brief))
        _, runtime = _youtube_ydl_options()
        print(f"\n🎯 YouTube Scout — {len(queries)} запросов, свежесть {args.days} дней")
        if dynamic_count:
            print(f"   Динамических запросов из свежего editorial plan: {dynamic_count}")
        print(f"   JS runtime: {runtime}" if runtime else "   JS runtime не найден: тихий metadata-only поиск")
        candidates: dict[str, dict[str, Any]] = {}
        for query in queries:
            found = search_videos(query, args.limit, args.days)
            for video in found:
                current = candidates.get(video["video_id"])
                if current is None or video["relevance_score"] > current["relevance_score"]:
                    candidates[video["video_id"]] = video
            print(f"  → {query}: {len(found)} подходящих видео")
        if len(_SEARCH_FAILURES) == len(queries):
            print("\n❌ YouTube search недоступен для всех запросов. Drafts не создавались.")
            return 1
        ordered = sorted(candidates.values(), key=lambda item: (item["relevance_score"], item["published_at"]), reverse=True)
        print(f"\n📺 Найдено для review: {len(ordered)}")
        for video in ordered:
            print_preview(video)
        if args.action == "preview":
            return 0
        try:
            require_db_write()
        except RuntimeError as exc:
            print(f"\n❌ Drafts не сохранены: {exc}")
            return 2
        added = 0
        for video in ordered:
            if video_exists(video["youtube_url"]):
                continue
            video_id = add_video(video)
            added += 1
            print(f"  🆕 Draft [{video_id}] {video['title']}")
        print(f"\n✅ Добавлено video drafts: {added}")
        return 0

    if args.action == "list":
        for video in list_drafts():
            print(f"[{video['id']}] {video['published_at']:%Y-%m-%d} | {video['channel']} | {video['title']}")
        return 0

    if not args.id:
        print("❌ Укажите --id")
        return 2
    require_db_write()
    with get_conn() as conn:
        with conn.cursor() as cur:
            if args.action == "approve":
                cur.execute("UPDATE videoitem SET is_published=true WHERE id=%s", (args.id,))
                print(f"✅ Video {args.id} опубликовано")
            elif args.action == "delete":
                cur.execute("DELETE FROM videoitem WHERE id=%s AND is_published=false", (args.id,))
                print(f"🗑 Video {args.id} удалено")
            else:
                cutoff = datetime.now(timezone.utc) - timedelta(days=90)
                cur.execute("DELETE FROM videoitem WHERE is_published=false AND published_at < %s", (cutoff,))
                print(f"🗑 Удалено старых drafts: {cur.rowcount}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
