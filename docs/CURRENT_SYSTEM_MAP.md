# CURRENT_SYSTEM_MAP.md

This document describes the SMTInsider repository **as it actually exists in code**,
not an aspirational design. It was produced by reading every Python file, every
prompt file, `requirements.txt`, and `run-all.sh`. Where documentation and code
disagreed, the code was treated as the source of truth.

Generated during the first execution pass of `00_MASTER_PLAN.md`, section 30.

---

## 1. Execution Flow (actual, from `run-all.sh`)

```text
run-all.sh
    │
    │  (requires LLM_API_BASE, LLM_MODEL env vars; sources .env)
    ▼
agents/agent-01-trend-hunter.py  scan --days N --strict-fresh --verify-pages
    │  writes /tmp/smtinsider_briefs.json  (BRIEFS_FILE)
    ▼
agents/agent-02-writer.py  --brief briefs.json --output article.txt
    │  writes article.txt + article.meta.json (ARTICLE_FILE / META_FILE)
    │  internally: draft pass (write_article) → self-revision pass (revise_article)
    ▼
agents/agent-02b-quality-checker.py  --meta article.meta.json --threshold 75
    │  scores the article; if score < threshold, rewrites body/title/summary/tags
    │  in place inside article.txt + article.meta.json
    ▼
agents/agent-03-seo-doctor.py  --meta article.meta.json
    │  adds slug, meta_description, JSON-LD, canonical fields to meta.json
    ▼
agents/agent-04-distributor.py  --meta article.meta.json
    │  drafts LinkedIn/forum/email distribution text (no external posting)
    ▼
agents/agent-05-analyst.py  pulse
    │  reads Neon Postgres `news` table, prints engagement/coverage summary
    ▼
agents/agent-06-publisher.py  submit --meta article.meta.json
    │  INSERTs into Neon Postgres `news` table with is_published=false
    │  (dedupe.py runs here too — refuses duplicate submission)
    ▼
agents/agent-07-youtube-scout.py  scan --days N   (independent, not chained)
    │  yt-dlp search → Neon Postgres `videoitem` table, is_published=false
    ▼
dashboard/app.py (FastAPI)
    serves a control-room UI: run any agent individually or the whole
    pipeline, browse briefs/article/drafts, approve/delete drafts, select
    which brief topic Writer should pick next.
```

`agent-07-youtube-scout.py` is **not** part of the chained pipeline in
`run-all.sh` — it is invoked separately (also available as a button in the
dashboard).

---

## 2. Agent Responsibility Matrix

| Agent | Responsibility | Inputs | Outputs | LLM Used | External Sources |
|---|---|---|---|---|---|
| `agent-01-trend-hunter.py` | Collects fresh SMT/EMS/PCB industry signals from search + RSS + HTML + vendor pages; scores and ranks them; asks the LLM to select 1-3 topics and produce a structured brief per topic | none (config: env vars, `SEED_QUERIES`, `DEFAULT_RSS_FEEDS`, `DEFAULT_HTML_SOURCES`, `DEFAULT_VENDOR_SOURCES`) | `briefs.json` (`{"topics":[...]}`) | Yes — topic selection + brief authoring (`llm_client.ask_json`) | DuckDuckGo HTML search, Google News RSS, 24 RSS feeds, 3 HTML listing pages, 38 vendor pages |
| `agent-02-writer.py` | Two-pass article writing: draft from a single brief, then a self-revision pass against an editorial checklist | `briefs.json` (one topic) | `article.txt`, `article.meta.json` | Yes — 2 LLM calls per article (draft + revision) | none directly (consumes brief's pre-fetched source excerpts) |
| `agent-02b-quality-checker.py` | Final QA gate: scores the article 0-100 across 4 criteria; rewrites if below threshold | `article.meta.json` (+ article.txt) | mutates `article.txt` / `article.meta.json` in place, adds `quality_check` block to meta | Yes — 1 LLM call | none |
| `agent-03-seo-doctor.py` | Adds slug, meta description, JSON-LD schema, canonical URL fields | `article.meta.json` | mutates `article.meta.json` (adds `seo` block) | Mostly deterministic string work; needs direct re-check for any LLM call | none |
| `agent-04-distributor.py` | Drafts distribution copy (LinkedIn post, forum blurb, email) — does not actually post anywhere | `article.meta.json` | prints/saves distribution drafts | Likely yes, for copy variants | none |
| `agent-05-analyst.py` | Reads the `news` table in Neon Postgres and prints a coverage/engagement pulse report | Neon Postgres `news` table | console report | No (deterministic SQL + aggregation) | Neon Postgres |
| `agent-06-publisher.py` | Inserts articles into Neon Postgres `news` table as unpublished drafts; runs `dedupe.py` to block duplicate submission; also has `approve`/`list`/`submit-video` subcommands | `article.meta.json`, `article.txt` | Neon Postgres `news` row (`is_published=false`) | No | Neon Postgres |
| `agent-07-youtube-scout.py` | Searches YouTube via `yt-dlp` (no API key) for fresh SMT-related videos, inserts as unpublished drafts | `SEARCH_QUERIES` (hardcoded list of 10 queries) | Neon Postgres `videoitem` table | No | YouTube (via yt-dlp) |

---

## 3. Shared Modules

### `agents/llm_client.py` (357 lines)
- Single OpenAI-compatible chat client (`chat()`, `ask_json()`) used by every
  agent that talks to an LLM.
- Reads `LLM_API_BASE`, `LLM_API_KEY`, `LLM_MODEL` from env.
- `LLM_MOCK=1` switches to a deterministic local mock (`_mock_chat`) that
  pattern-matches on the system/user prompt content to fabricate a plausible
  JSON response — this exists purely so the dashboard/pipeline can be
  exercised without a live LLM endpoint.
- Wraps `llm_cache.py` for response caching before making a real HTTP call.
- **Coupling**: every LLM-calling agent imports this module directly by
  relative path manipulation (`sys.path.insert(0, os.path.dirname(__file__))`)
  rather than as an installed package — acceptable for a flat `agents/`
  layout, but it means the whole `agents/` directory must stay on `sys.path`
  for any submodule to import any other.
- **Technical debt**: the mock branch is a large if/elif chain matching on
  literal substrings inside system prompts (e.g. `"технический редактор" in
  system`). This is fragile — any prompt wording change can silently
  mis-route the mock (this was hit and fixed during a previous pass: a
  prompt example containing the word "test" was matching an unrelated
  connectivity-check branch).

### `agents/llm_cache.py` (76 lines)
- Flat JSON file cache at `cache/llm_responses.json`.
- Key = SHA-256 of `{model, messages}`. TTL = 24h.
- No eviction beyond TTL-on-read; the file can grow unbounded between reads
  of stale keys.
- No token/request accounting, no explicit prompt-version field in the cache
  key — changing a prompt file's wording does change the hash today (because
  the system prompt text is part of `messages`), but there is no dedicated
  field for observability/debugging of which prompt version produced which
  cache entry.

### `agents/section_router.py` (244 lines)
- Decides which of 4 site sections (`news`, `insight`, `review`, `vendor`)
  a piece of content belongs to, and the corresponding `section_path`
  (`/news/`, `/insights/`, `/reviews/`, `/vendors/`).
- Takes an explicit hint (from the brief's `editorial_type`/`format`) plus a
  keyword/heuristic fallback, and returns a `SectionDecision` with a
  `confidence` score.
- Used by both `agent-01` (to pre-tag briefs) and `agent-02` (to finalize the
  section for `article.meta.json`).
- Self-contained, no external I/O — good candidate to keep as-is.

### `agents/source_expander.py` (284 lines)
- Given one selected topic and the full pool of collected signals, expands a
  short source list into up to N sources (default 5) by:
  1. keeping the LLM-provided primary source(s) — role `fresh_primary`;
  2. finding token-similarity matches among already-collected signals from
     **different domains** — role `related_fresh_signal` (near-duplicate
     signals with similarity ≥0.97 are excluded so a republished copy of the
     same story isn't counted as independent corroboration);
  3. crawling the primary source page(s) for product/vendor context links —
     role `context_link`.
- Every expanded source carries an `excerpt` field (snippet or, for
  top-ranked signals, extracted full article text) so the Writer can
  synthesize across sources instead of seeing only titles/URLs.
- **Coupling**: depends on `requests` + `bs4` directly for `context_link`
  crawling; no shared HTTP layer with `agent-01-trend-hunter.py` (which has
  its own `_http_get` retry wrapper) — this is duplicated logic between the
  two files.

### `agents/dedupe.py` (212 lines)
- URL canonicalization (strips `www.`, trailing slash, `utm_*` params).
- Title normalization + Jaccard-style token overlap similarity
  (`title_similarity`, threshold 0.72 default).
- `ExistingIndex` loads up to 2000 recent rows from the Neon `news` table
  (title, slug, link, source_url, frontmatter_json — URLs are recursively
  extracted from the JSON blob) and builds slug/URL/title lookup dicts.
- `find_duplicate()` checks, in order: exact slug match → exact canonical URL
  match → exact normalized-title match → token-similarity above threshold.
- Used by `agent-01` (skip signals that already became articles) and
  `agent-06` (refuse duplicate submission).
- **Architectural note**: this is a single-level implementation (URL exact
  match + near-duplicate title). There is no content-hash deduplication, no
  semantic/embedding deduplication, and no event-level resolution that
  *links* rather than *discards* duplicate documents.

---

## 4. Information Collection Map (`agent-01-trend-hunter.py`, 1451 lines — by far the largest file)

| Source Type | Function | Discovery | Fetch | Parse | Dedup | Failure Handling | Rate Limiting | Caching | Coverage Limitation |
|---|---|---|---|---|---|---|---|---|---|
| DuckDuckGo HTML search | `search_duckduckgo()` | 15 hardcoded `SEED_QUERIES` | `requests.get` on `html.duckduckgo.com` | Regex/BS4 HTML scraping of result blocks | exact URL set (`seen`) + fuzzy title-token overlap (≥0.85) across all channels | best-effort; throttling/blocking silently yields fewer/no results | 0.4s sleep between queries | none | Search HTML scraping is inherently fragile — DDG can and does throttle bot UAs; no proxy rotation |
| Google News RSS | `search_google_news_rss()` | 8 hardcoded `GOOGLE_NEWS_QUERIES` | `requests.get` on `news.google.com/rss/search` | `xml.etree.ElementTree` parse of `<item>` | same as above | returns `[]` on any exception | 0.3s sleep between queries | none | Query set is small and fixed; Google News snippet quality varies |
| RSS/Atom feeds | `gather_rss_signals()` | 23 hardcoded feeds in `DEFAULT_RSS_FEEDS` (overridable via `NEWS_RSS_FEEDS` env) | `requests.get` per feed | `xml.etree.ElementTree`, handles both RSS `<item>` and Atom `<entry>` | same | per-feed try/except, one feed failure doesn't stop others | none explicit | none | Feed list is static; a dead/renamed feed silently yields 0 items with no health alert |
| HTML listing pages | `gather_html_signals()` | 3 hardcoded pages in `DEFAULT_HTML_SOURCES`: SMTnet (custom parser `smtnet_news`) + 2 pages using a generic `generic_dated_list` parser (heading links + nearby date, falls back to per-article page-date fetch) | `requests.get` | BeautifulSoup, kind-specific parsing logic | same | per-source try/except | none explicit | none | Only 3 sources; the generic parser is heuristic (CSS selector guesses) and will silently return 0 items on sites with a different DOM shape |
| Vendor/manufacturer pages | `gather_vendor_signals()` | 38 vendor URLs (registry-backed via `configured_vendor_sources()`, fallback `_FALLBACK_VENDOR_SOURCES`) (overridable via `NEWS_VENDOR_SOURCES` env), grouped by category (inspection/placement/reflow/soldering/materials/cleaning/standards/stencil/tht_insertion/depaneling/test) | `requests.get` + `_vendor_link_candidate()` heuristic link filter, then per-candidate page fetch for title/date | BeautifulSoup link discovery + `_extract_page_title_and_date()` | same | per-vendor try/except | `max_links_per_vendor` / `max_items_per_vendor` caps | none | 1 request per vendor page + up to N per candidate link — the slowest collector; sequential per vendor, not thread-pooled |
| Full-text enrichment | `enrich_top_signals_with_fulltext()` | n/a (post-collection step on already-ranked signals) | `extract_article_fulltext()` — `_http_get` + BeautifulSoup paragraph extraction, thread-pooled (`ThreadPoolExecutor(max_workers=6)`) | strips script/style/nav, prefers `<article>` container, joins `<p>` text, truncates to 1800 chars | n/a | returns `""` on any exception, caller checks truthiness | thread pool caps concurrency to 6 | none (re-fetches every run) | Only applied to the top `NEWS_FULLTEXT_TOP_N` (default 15) ranked signals |
| YouTube | `agent-07-youtube-scout.py` `search_videos()` | 10 hardcoded `SEARCH_QUERIES` | `yt_dlp.YoutubeDL().extract_info("ytsearchN:query")` | yt-dlp's own metadata extraction | none beyond the `--days` date cutoff (no cross-run title/URL dedup in this file) | try/except per query, prints warning | none explicit | none | No channel-based discovery, no dedup against already-collected videos beyond date filtering |

**Signal scoring** (`signal_editorial_score()`): weighted keyword matching
(SMT-specific terms +, HR/funding/award-only signals −), numeric-content
bonus (regex for `%`, units, dimensions), vendor-domain-authority bonus
(hardcoded host allowlist), verified-date bonus, recency bonus (≤2 days: +6,
≤7 days: +3). This is a **single opaque integer score**, not a decomposed
confidence object with separately inspectable components
(`source_trust`, `evidence_count`, `independent_source_count`,
`official_confirmation`, `semantic_consistency`, `contradiction_penalty`,
`freshness`).

**Source diversification** (`_diversify_by_source()`): caps signals from any
one feed/query to 4 out of the top 60 handed to the LLM for topic selection —
this exists so the LLM's topic-selection prompt isn't dominated by whichever
single feed happened to return the most items.

---

## 5. Data Flow — actual JSON/object schemas (extracted from code, not invented)

### Signal (internal, in-memory / optionally dumped with `--collect-only`)
```jsonc
{
  "title": "string",
  "snippet": "string (search/RSS excerpt, ~350-500 chars depending on collector)",
  "source": "string (canonical article URL)",
  "query": "string (originating search query or 'HTML:<name>' or 'RSS:<name>')",
  "feed": "string (RSS feed name / vendor name / 'GoogleNews:<outlet>')",
  "published_at": "ISO date string or 'unknown'",
  "date_source": "string (jsonld | meta_tag | rss_pubdate | html_page_date | google_news_rss | unknown)",
  "date_verified": "bool",
  "fresh_within_days": "bool",
  "_editorial_score": "int (set by build_briefs before ranking; debug/analysis field)",
  "full_text": "string, optional (only present on top-ranked signals after enrichment)"
}
```

### Brief topic (`briefs.json` -> `topics[]`, produced by `agent-01`, consumed by `agent-02`)
```jsonc
{
  "topic": "string",
  "angle": "string (engineering framing for the article)",
  "format": "news|insight|review",
  "editorial_type": "news|insight|review|vendor",
  "target_section": "/news/|/insights/|/reviews/|/vendors/",
  "section_routing": { "...": "SectionDecision.to_dict() output" },
  "keywords": ["string", "..."],
  "category": "string",
  "urgency": "HIGH|MEDIUM|LOW",
  "source_count": "int",
  "source_notes": "string",
  "key_facts": ["string", "..."],
  "sources": [
    {
      "title": "string",
      "url": "string (canonical)",
      "date": "ISO date string or 'unknown'",
      "role": "fresh_primary|related_fresh_signal|context_link",
      "excerpt": "string, up to 600 chars"
    }
  ],
  "expanded_sources": "same shape as 'sources'"
}
```

### Article meta (`article.meta.json`, produced by `agent-02`, mutated by `agent-02b`/`agent-03`)
```jsonc
{
  "title": "string",
  "summary": "string, <=200 chars",
  "category": "string",
  "tags": ["string", "..."],
  "editorial_type": "news|insight|review|vendor",
  "section_path": "string",
  "section_routing": { "...": "..." },
  "source_topic_brief": { "...": "the full brief topic object above" },
  "generated_at": "ISO datetime",
  "model": "string (LLM_MODEL)",
  "article_file": "string (path)",
  "draft_title": "string (pre-revision title, for audit)",
  "revision_notes": ["string", "..."],
  "revised": "bool",
  "quality_check": {
    "score": "int 0-100",
    "breakdown": {"factual_accuracy": "int", "engineering_value": "int", "writing_quality": "int", "seo_metadata": "int"},
    "issues": ["string", "..."],
    "improved": "bool",
    "checked_at": "ISO datetime"
  },
  "seo": "object added by agent-03-seo-doctor.py (slug, meta_description, json_ld, canonical)"
}
```

### Neon Postgres `news` table
Columns confirmed by code references across `dedupe.py`/`agent-06-publisher.py`:
`id, title, slug, link, source_url, frontmatter_json, is_published,
editorial_type, category_name` (+ additional columns referenced elsewhere in
`agent-06-publisher.py`; the file's own docstring states 22 columns total —
not fully re-enumerated in this pass).

---

## 6. Collection Bottlenecks (identified; addressed incrementally starting with `docs/SOURCE_REGISTRY.md`)

1. **No source health tracking.** A dead RSS feed or a vendor page that
   changes its DOM structure fails silently — 0 items, no alert, no record
   of `consecutive_failures` or state (`HEALTHY`/`DEGRADED`/`FAILING`/`DEAD`).
2. **~~Hardcoded source lists inside Python.~~ [RESOLVED]** `DEFAULT_RSS_FEEDS`,
   `DEFAULT_HTML_SOURCES`, `DEFAULT_VENDOR_SOURCES`, `SEED_QUERIES`,
   `GOOGLE_NEWS_QUERIES` have been extracted into a validated YAML registry
   (`sources/`) with Pydantic models (`src/models/source.py`) and a loader
   (`src/config/loader.py`). `agent-01-trend-hunter.py`'s
   `configured_*()` functions now read from the registry first, falling
   back to the original hardcoded lists (kept as `_FALLBACK_*`) only if the
   registry can't be loaded. See `docs/SOURCE_REGISTRY.md`. Still open:
   per-source health tracking (item 1 below) and a UI/CLI for
   enabling/disabling sources without a YAML edit.
3. **Two independent HTTP layers.** `agent-01-trend-hunter.py` has its own
   `_http_get` (retry/backoff) and `agent-07-youtube-scout.py`/
   `source_expander.py` each do their own `requests.get` without shared
   retry/backoff/timeout policy or per-domain rate limiting.
4. **No raw record preservation.** Once a signal is scored and (if selected)
   turned into a brief, the original fetched HTML/RSS payload is discarded.
5. **Single opaque relevance score, not a decomposed confidence model.**
6. **No event-level deduplication.** Only *discard*, never *link*.
7. **PDF/technical-document collection does not exist yet.**
8. **Vendor collector is sequential, not concurrent.**

Items 3-8 are follow-up work (see `docs/SOURCE_REGISTRY.md` §7 "Next Steps");
this pass focuses on item 2, the source-registry extraction, per the
master plan's incremental migration order (Steps 1-4 before any collector
rewrite).

---

## 7. Summary

The current system is a **working, single-process, agent-per-script
pipeline** with real (not fabricated) source lists, genuine freshness
verification, and a two-pass LLM writing stage. Its collection layer already
has 5 collection channels, opaque-but-functional scoring, source
diversification, fuzzy dedup, and full-text enrichment for top signals — but
it is still fundamentally a **flat, Python-literal source list with no
health tracking, no raw-record preservation, no event model, and no
decomposed confidence scoring**.

This document is the baseline. See `docs/SOURCE_REGISTRY.md` for the first
concrete architectural extraction: Pydantic source models, YAML source
configuration, and migration of the existing hardcoded RSS/vendor lists —
done without losing any currently configured source.
