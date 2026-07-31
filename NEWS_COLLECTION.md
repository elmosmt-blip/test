# Fresh News Collection Policy

Agent #1 is now configured as a **fresh news collector** with multi-channel
sourcing, concurrent fetching, and content-quality scoring.

Default freshness window:

```env
NEWS_LOOKBACK_DAYS=30
NEWS_STRICT_FRESH=1
NEWS_VERIFY_DATES=1
NEWS_TIMEZONE=Asia/Jerusalem
```

## Collection channels (in order of execution)

1. **DuckDuckGo HTML search** — 15 seed queries, broad coverage, page dates
   verified concurrently (thread pool, 8 workers) since search engines don't
   reliably supply publication dates.
2. **Google News RSS** — resilient date-stamped fallback/complement to DDG.
   Every item already carries a real `pubDate`, so no extra page fetch is
   needed for these. Configured via 8 topical queries in `GOOGLE_NEWS_QUERIES`.
3. **RSS/Atom feeds** — 23 configured feeds spanning trade press (SMT007,
   PCB007, Global SMT & Packaging, EPP Europe, Electronics Weekly, IPC...)
   and vendor newsrooms (Saki, Juki, Fuji Europe, Europlacer, Mycronic,
   Nordson...).
4. **HTML listing pages** — SMTnet plus a generic `generic_dated_list` parser
   that works on most trade-press listing pages without a custom scraper per
   site (heading links + nearby date, falling back to a page-level date
   check).
5. **Vendor/manufacturer pages** — 26 vendor sources across inspection,
   placement, reflow/soldering, and standards/materials categories.

## Signal quality scoring (`signal_editorial_score`)

Every signal is scored before being handed to the LLM:

- +weights for SMT-specific technical terms (aoi, spi, axi, cpk, false call,
  voiding, ipc, mes, cfx...)
- +6/+2 for containing concrete numbers/specs (percentages, units, dimensions)
- +5 for coming directly from a vendor/manufacturer domain (more timely and
  authoritative than aggregator republication)
- +3 for a verified publication date, +3/+6 recency bonus for items from the
  last 2-7 days
- −8 penalties for HR/funding/award-only press releases with no engineering
  content

The score is persisted on each signal (`_editorial_score`) and shown to the
LLM in the topic-selection prompt.

## Source diversification

Before the ranked signal list is handed to the LLM, `_diversify_by_source`
caps how many signals from any single feed can appear (default: 4 per
source out of the top 60), so 3 topics aren't all pulled from whichever feed
happened to return the most items that run.

## Full-text enrichment

For the top-ranked signals (default 15, see `NEWS_FULLTEXT_TOP_N`), the
collector fetches the actual article page and extracts paragraph text
(`enrich_top_signals_with_fulltext`, parallelized with a thread pool). This
means the topic-selection prompt sees real article content instead of a
1-2 sentence search/RSS snippet — which is what lets `angle` and `key_facts`
in the brief cite actual numbers instead of paraphrasing a headline.

```env
NEWS_FULLTEXT_ENABLED=1
NEWS_FULLTEXT_TOP_N=15
```

## Near-duplicate detection across channels

The same story often appears via search + RSS + a vendor feed with a
slightly different URL (AMP page, redirect, syndicated copy). In addition to
exact-URL dedup, a fuzzy title-token-overlap check (≥85% overlap) prevents
these from being counted as separate signals.

## Retry/backoff

All page fetches (`_http_get`) retry up to twice with backoff — many trade
press and vendor sites are flaky under a bot user agent.

Meaning:

- only items from the last 30 days are accepted;
- undated items are rejected in strict mode;
- page/RSS metadata is checked for publication dates;
- RSS feeds and Google News RSS are used as reliable fallbacks because
  DuckDuckGo can throttle bot traffic.

Override feeds with:

```env
NEWS_RSS_FEEDS=Name|https://example.com/feed/;Name2|https://example.org/rss.xml
NEWS_GOOGLE_RSS_ENABLED=1
```

## Safe test without LLM

```bash
cd /home/user/smtinsider-agent-team
set -a; source .env; set +a
python3 agents/agent-01-trend-hunter.py scan \
  --collect-only \
  --output /tmp/smtinsider_fresh_signals_30d.json \
  --days 30 \
  --strict-fresh \
  --verify-pages
```

This produces a JSON file with fresh signals only (including `_editorial_score`
and, for top signals, `full_text`) and does not generate/write articles.

## Production run

For a real LLM-backed topic brief, set real LLM values and disable mock:

```env
LLM_MOCK=0
LLM_API_BASE=...
LLM_API_KEY=...
LLM_MODEL=...
```

Then run:

```bash
python3 agents/agent-01-trend-hunter.py scan --days 30 --strict-fresh --verify-pages
```

If no fresh signals are found, the agent exits instead of inventing topics.

Additional HTML source limit:

```env
NEWS_HTML_MAX_ITEMS=50
```


## Vendor/manufacturer sources

Vendor sites are enabled because product launches often appear there before
media republication.

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

Current vendor coverage includes Saki, Juki, Fuji Europe, Europlacer,
Pillarhouse, KYZEN, Koh Young, TRI, Viscom, ViTrox, Creative Electron,
Yamaha SMT, ASMPT, Essemtec, Heller, Rehm, AIM Solder, Mirtec, CyberOptics,
Mycronic, Panasonic Factory Solutions, Nordson SELECT, Indium Corporation,
Photo Stencil, and IPC.


## Feed discovery tool (2026-07-14) — reducing reliance on search engines

DuckDuckGo HTML scraping and Google News RSS are both unofficial,
undocumented endpoints — reliable enough to keep, but they can change or
throttle without notice, and neither has a stability guarantee.

`scripts/discover_feeds.py` adds two first-party discovery techniques that
don't depend on any search engine or paid API:

1. **RSS/Atom autodiscovery** — reads `<link rel="alternate"
   type="application/rss+xml">` tags from a vendor's own page (the same
   mechanism a browser uses to show a feed icon), falling back to probing a
   short list of common feed paths (`/feed/`, `/rss.xml`, ...) if no
   `<link>` tag is present. A site's own feed has a stable, versioned
   schema; parsing its HTML news-listing page (what `source_type: vendor`
   collection does today) is much more likely to break silently when the
   site redesigns.
2. **Sitemap.xml scanning** — parses `/sitemap.xml` (including sitemap
   index files, following up to 5 child sitemaps), keeps only
   news/press/blog-shaped URLs, and surfaces the most recently modified
   ones. Useful for vendors with no RSS feed at all.

Usage:

```bash
# Scan every currently-configured vendor source for a hidden RSS feed
python3 scripts/discover_feeds.py --scan-vendors

# Also check sitemap.xml (slower — extra fetches per source)
python3 scripts/discover_feeds.py --scan-vendors --sitemap

# Scan one arbitrary URL
python3 scripts/discover_feeds.py --url https://example.com/news/
```

**This script does not write to `sources/`.** It only prints a report.
Every candidate must be opened in a browser and manually confirmed to be a
real, live, dated feed before being added to the registry — same standard
used for every vendor added so far (see `docs/SOURCE_REGISTRY.md`).

Practical use: if `--scan-vendors` finds an RSS feed for a vendor currently
configured as `source_type: vendor` (HTML scraping), that vendor is a good
candidate to migrate to `source_type: rss` — same content, much lower
maintenance burden, since RSS won't break when the vendor redesigns their
news page.

Tested with fixture HTML/XML in `tests/test_feed_discovery.py` (26 tests,
no network access required). The live `--scan-vendors` / `--url` fetch path
itself was not run against real vendor sites during development (sandboxed
network access here is restricted to an allowlist that doesn't include
arbitrary vendor domains) — run it in an environment with normal internet
access and manually review the output before trusting any result.
