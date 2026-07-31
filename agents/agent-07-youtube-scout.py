#!/usr/bin/env python3
"""
Agent YouTube Scout — SMTInsider (БЕЗ API-КЛЮЧА)

Ищет свежие видео по SMT-темам через yt-dlp.
Проверяет дату. Добавляет ТОЛЬКО моложе --days.
is_published=false → черновик. Ты смотришь → публикуешь.

Usage:
  export NEON_DATABASE_URL='postgresql://...'
  python3 agent-youtube-scout.py scan --days 30
  python3 agent-youtube-scout.py list
  python3 agent-youtube-scout.py approve --id 42
"""

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


import os, sys
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

DATABASE_URL = os.environ.get("NEON_DATABASE_URL")
if not DATABASE_URL:
    print("NEON_DATABASE_URL ne zadan"); sys.exit(1)

try:
    import psycopg2, psycopg2.extras
except ImportError:
    os.system("pip install psycopg2-binary -q")
    import psycopg2, psycopg2.extras

try:
    import yt_dlp
except ImportError:
    os.system("pip install yt-dlp -q")
    import yt_dlp


@contextmanager
def get_conn():
    """Явно закрывает соединение (psycopg2 `with conn:` коммитит, но не закрывает)."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

SEARCH_QUERIES = [
    "SMT pick and place machine",
    "AOI inspection system",
    "BGA soldering rework",
    "reflow oven SMT",
    "SMT assembly line tour",
    "Koh Young AOI",
    "ASMPT SIPLACE",
    "Yamaha YRM20",
    "Fuji NXTR placement",
    "SMT smart factory",
]

def search_videos(query, max_results=5, max_days=30):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_days)
    videos = []
    
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": False}) as ydl:
            info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
            for e in info.get("entries", []):
                vid = e.get("id", "")
                if not vid: continue
                pub_str = e.get("upload_date", "")
                if not pub_str: continue
                try:
                    pub_dt = datetime.strptime(pub_str, "%Y%m%d").replace(tzinfo=timezone.utc)
                except:
                    continue
                if pub_dt < cutoff: continue
                videos.append({
                    "title": e.get("title", ""),
                    "youtube_url": f"https://www.youtube.com/watch?v={vid}",
                    "thumbnail_url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                    "channel": e.get("channel", e.get("uploader", "")),
                    "description": (e.get("description") or "")[:200],
                    "published_at": pub_dt,
                })
    except Exception as e:
        print(f"  ⚠ {query}: ошибка поиска — {e}")
    return videos

def video_exists(url):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM videoitem WHERE youtube_url = %s", (url,))
            return cur.fetchone() is not None

def add_video(v):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO videoitem
                    (title, youtube_url, thumbnail_url, channel,
                     description, published_at, is_published)
                VALUES (%s,%s,%s,%s,%s,%s,false) RETURNING id;
            """, (v["title"], v["youtube_url"], v["thumbnail_url"],
                  v["channel"], v["description"], v["published_at"]))
            pid = cur.fetchone()[0]
        conn.commit()
    return pid

def list_drafts():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, title, channel, published_at
                FROM videoitem WHERE is_published = false
                ORDER BY published_at DESC LIMIT 30
            """)
            return cur.fetchall()

def approve(vid):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE videoitem SET is_published = true WHERE id = %s", (vid,))
        conn.commit()
    return {"id": vid, "status": "published"}

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["scan", "list", "approve", "delete", "cleanup"])
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--id", type=int)
    args = p.parse_args()

    if args.action == "scan":
        print(f"\n🎯 YouTube Scout — {len(SEARCH_QUERIES)} zaprosov, ishu svezhee {args.days} dney\n")
        total = 0
        for q in SEARCH_QUERIES:
            videos = search_videos(q, args.limit, args.days)
            fresh = 0
            for v in videos:
                if video_exists(v["youtube_url"]):
                    continue
                pid = add_video(v)
                fresh += 1
                d = v["published_at"].strftime("%d.%m.%Y")
                print(f"  🆕 [{pid}] {d} | {v['channel']:30s} | {v['title'][:60]}")
            total += fresh
            if fresh == 0:
                print(f"  — {q}: nichego")
        print(f"\n✅ Itogo: {total} novyh")

    elif args.action == "list":
        dd = list_drafts()
        if not dd:
            print("\n📋 Chernovikov net")
        else:
            print(f"\n📋 Chernoviki ({len(dd)}):\n")
            for d in dd:
                d_fmt = d['published_at'].strftime("%d.%m.%Y")
                print(f"  [{d['id']:3d}] {d_fmt} | {str(d['channel']):25s} | {str(d['title'])[:55]}")

    elif args.action == "approve":
        if not args.id: print("ukazhi --id"); sys.exit(1)
        r = approve(args.id)
        print(f"✅ Video {r['id']} opublikovano")

    elif args.action == "delete":
        if not args.id: print("ukazhi --id"); sys.exit(1)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM videoitem WHERE id = %s AND is_published = false", (args.id,))
            conn.commit()
        print(f"🗑 Video {args.id} udaleno")

    elif args.action == "cleanup":
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM videoitem WHERE is_published = false AND published_at < %s::date", (cutoff,))
                print(f"🗑 Udaleno {cur.rowcount} staryh chernovikov")
            conn.commit()
