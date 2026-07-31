# Fresh News Collection

The news collector is configured to avoid stale or invented stories.

Default policy:

```text
last 30 days only
strict date required
verify dates from RSS/page metadata
RSS fallback enabled
```

Command:

```bash
python3 agents/agent-01-trend-hunter.py scan \
  --output /tmp/smtinsider_briefs.json \
  --days 30 \
  --strict-fresh \
  --verify-pages \
  --max-topics 3
```

Collect-only command:

```bash
python3 agents/agent-01-trend-hunter.py scan \
  --collect-only \
  --output /tmp/smtinsider_fresh_signals_30d.json \
  --days 30 \
  --strict-fresh \
  --verify-pages
```

Default RSS feeds:

```text
SMT Today
EMSNow
Circuits Assembly
Electronics Sourcing
SMTnet (HTML dated news page)
```

If DuckDuckGo returns zero results due to bot challenge, RSS still provides dated fresh signals.

The collector writes each signal with:

```json
{
  "title": "...",
  "source": "...",
  "published_at": "YYYY-MM-DD",
  "date_source": "rss_pubDate | meta | jsonld | ...",
  "fresh_within_days": true
}
```


## Vendor/manufacturer sources

Vendor sites are enabled because product launches often appear there before media republication.

See:

```text
VENDOR_SOURCES.md
```

Key env:

```env
NEWS_VENDOR_SOURCES_ENABLED=1
NEWS_VENDOR_VERIFY_PAGES=0
NEWS_VENDOR_MAX_LINKS=8
NEWS_VENDOR_MAX_ITEMS=2
```

Current vendor coverage includes Saki, Juki, Fuji Europe, Europlacer, Pillarhouse, KYZEN, Koh Young, TRI, Viscom, ViTrox, Creative Electron, Yamaha SMT, ASMPT, Essemtec, Heller, Rehm, AIM Solder.
