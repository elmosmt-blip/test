# SOURCE_REGISTRY.md

First concrete architectural extraction from `00_MASTER_PLAN.md` (sections 8-9,
implementation strategy Step 2-4). This document describes what was actually
built in this pass — not the full 30-section target architecture.

## What this pass does and does not do

**Does:**
- Introduces `src/models/source.py` — Pydantic v2 models (`SourceConfig`,
  `SearchQueryConfig`, `SourceRegistry`) that are the single schema for every
  collectible source.
- Introduces `src/config/loader.py` — loads and validates every YAML file
  under `sources/`, with useful diagnostics on bad config (file path +
  pydantic error, not a bare traceback).
- Migrates **every** hardcoded source already in
  `agents/agent-01-trend-hunter.py` (`DEFAULT_RSS_FEEDS`,
  `DEFAULT_HTML_SOURCES`, `DEFAULT_VENDOR_SOURCES`, `GOOGLE_NEWS_QUERIES`,
  `SEED_QUERIES`) into YAML files under `sources/`, via
  `scripts/migrate_sources.py`, which parses the actual Python literals with
  `ast.literal_eval` — it does not re-type or re-derive URLs by hand, so no
  source can be silently altered or dropped in transcription.
- Verifies migration parity programmatically: `scripts/migrate_sources.py
  --verify-parity` reloads the written YAML through the same loader
  `agents/agent-01-trend-hunter.py` would eventually use, and asserts the
  counts match the original Python lists exactly (23 RSS / 3 HTML / 26
  vendor / 23 search queries at the time of this migration).
- **`agents/agent-01-trend-hunter.py` now reads from the registry as its
  primary source of truth.** `configured_rss_feeds()`,
  `configured_vendor_sources()`, `configured_html_sources()`,
  `configured_google_news_queries()`, and `configured_seed_queries()` all
  call `_get_registry()` (a process-cached `load_source_registry()`) and
  convert entries via the `.as_legacy_*_tuple()` adapters. The original
  Python literals still exist, renamed to `_FALLBACK_RSS_FEEDS` /
  `_FALLBACK_HTML_SOURCES` / `_FALLBACK_VENDOR_SOURCES` /
  `_FALLBACK_GOOGLE_NEWS_QUERIES` / `_FALLBACK_SEED_QUERIES`, and are used
  automatically if the registry can't be loaded (missing `sources/`
  directory, invalid YAML, missing `pydantic`/`pyyaml`, or explicit
  `NEWS_DISABLE_REGISTRY=1`). Env var overrides (`NEWS_RSS_FEEDS`,
  `NEWS_VENDOR_SOURCES`) still take precedence over both, unchanged from
  before the registry existed. Every fallback path prints why it fell back,
  so a broken registry is visible in scan output, not silently masked.
  Verified in `tests/test_agent01_registry_wiring.py` (11 tests): registry
  and fallback return byte-identical URL/query sets, `NEWS_DISABLE_REGISTRY`
  correctly forces fallback, env overrides still win, and the registry is
  cached (not re-read from disk) across repeated calls within one process.
- Adds a real, executed pytest suite (45 tests total:
  `tests/test_source_models.py` (21), `tests/test_source_loader.py` (13),
  `tests/test_agent01_registry_wiring.py` (11)) covering field validation,
  cross-field validation, duplicate-ID rejection, YAML syntax errors,
  missing directories, parity against the real `sources/` directory, and
  the live wiring inside `agent-01-trend-hunter.py`.

**Does not (yet):**
- No source health tracking (section 10 of the master plan) — no
  `last_attempt_at` / `consecutive_failures` / HEALTHY-DEGRADED-FAILING-DEAD
  state machine yet.
- No `BaseCollector` class hierarchy or `src/collectors/` — the existing
  `gather_rss_signals()` / `gather_html_signals()` / `gather_vendor_signals()`
  functions in `agent-01-trend-hunter.py` are structurally unchanged; only
  their *source list input* now comes from the registry.
- No discovery mechanisms (RSS autodiscovery, sitemap.xml, robots.txt) — the
  registry currently only holds sources that were already hardcoded and
  already proven to work.
- No PDF/technical-document collector, no event resolution, no entity
  extraction, no decomposed confidence model. These are all later steps in
  the master plan's 16-step implementation order and are out of scope for
  this pass.

This is deliberate. Section 27 of the master plan is explicit: *"Do not
rewrite the whole repository in one operation. Use incremental migration."*
Step 3 is "Create source registry and validation" — this document and its
accompanying code is exactly that step, and only that step.

## Directory layout

```text
src/
├── __init__.py
├── models/
│   ├── __init__.py
│   └── source.py          # SourceConfig, SearchQueryConfig, SourceRegistry, enums
└── config/
    ├── __init__.py
    └── loader.py           # load_source_registry(), validate_all(), SourceConfigError

sources/
├── rss/
│   └── trade_press_and_vendor_feeds.yaml   (23 entries)
├── html/
│   └── listing_pages.yaml                   (3 entries)
├── vendors/
│   ├── aoi.yaml            (10 entries — inspection category, incl. TAGARNO)
│   ├── placement.yaml      (8 entries)
│   ├── reflow.yaml         (3 entries)
│   ├── soldering.yaml      (3 entries)
│   ├── materials.yaml      (3 entries)
│   ├── cleaning.yaml       (2 entries)
│   ├── standards.yaml      (1 entry)
│   ├── stencil.yaml        (2 entries)
│   ├── tht-insertion.yaml  (2 entries — Sciencgo, Robotas)
│   ├── depaneling.yaml     (1 entry — ASYS Group)
│   └── test.yaml           (2 entries — Forwessun, Seica)
│                            = 38 entries total
└── search/
    └── seed_queries.yaml    (23 entries: 15 DuckDuckGo + 8 Google News)

scripts/
└── migrate_sources.py       # Python-literal -> YAML migration + parity check

tests/
├── conftest.py
├── test_source_models.py    # 21 tests — Pydantic model validation rules
└── test_source_loader.py    # 13 tests — YAML loading, error handling, real-registry parity
```

## Source YAML schema

Every entry under `sources/rss/`, `sources/html/`, `sources/vendors/` is a
`SourceConfig`:

```yaml
- id: smt-today-rss              # required, lowercase-slug, unique across the whole registry
  name: SMT Today                # required, human-readable
  source_type: rss                # required: rss | vendor | html | youtube
  url: https://smttoday.com/feed/ # required, must be a valid http(s) URL
  html_parser: null                # required (non-null) when source_type == html
  category: null                   # required (non-empty) when source_type == vendor
  tags: [trade-press]
  priority: 4                      # 1 (highest) .. 10 (lowest), default 5
  trust_level: industry_media      # official_vendor | industry_media | aggregator | unknown
  enabled: true
  language: en
  notes: "Migrated from agents/agent-01-trend-hunter.py DEFAULT_RSS_FEEDS"
```

Every entry under `sources/search/` is a `SearchQueryConfig`:

```yaml
- id: smt-electronics-manufacturing-news-ai-aoi-spi-2026-ddg
  query: "SMT electronics manufacturing news AI AOI SPI 2026"
  engine: duckduckgo    # duckduckgo | google_news
  enabled: true
```

Adding a new source is now a YAML edit, not a Python code change — append a
new list entry to the relevant category file (or create a new file; the
loader recursively scans every `*.yaml`/`*.yml` under `sources/`). Invalid
entries fail loudly with the file path and the exact validation error, per
the master plan's requirement that "invalid source configuration must fail
with a useful diagnostic message."

**Per the master plan's explicit warning ("a source registry containing 500
fake URLs is worse than 100 verified sources"): do not add a source to this
registry unless it is already live in the Python source, has been manually
verified, or comes from a supported discovery mechanism.** No discovery
mechanism exists yet in this pass, so any new entry today must be manually
verified.

## Running the migration / validation tooling

```bash
# Dry run: show what would be extracted, write nothing
python3 scripts/migrate_sources.py --check

# Real migration + parity verification against the Python source lists
python3 scripts/migrate_sources.py --verify-parity

# Run the test suite
python3 -m pytest tests/ -v
```

## Second source expansion + multi-article pipeline (2026-07-11, later same day)

**Sources: +7 vendors, +1 RSS feed (all independently URL-verified before
adding — live, dated news/press pages fetched directly, same standard as
the first expansion).**

New vendors:
- **MEK (Marantz Electronics)** — inspection (AOI)
- **BTU International** — reflow
- **Kurtz Ersa** — soldering (reflow/selective/rework/optical inspection)
- **MacDermid Alpha** — materials (solder paste, flux)
- **ZESTRON** — cleaning
- **Seica** — test (ICT/flying probe)
- **Christian Koenen** — stencil

New RSS feed:
- **Assembly Magazine** (assemblymag.com) — general manufacturing trade
  press with a dedicated electronics-assembly topic; broader than the other
  feeds, filtered like every other source by `text_matches_smt()` /
  `signal_editorial_score()`.

Totals after this pass: **24 RSS feeds, 3 HTML sources, 38 vendor sources,
23 search queries = 88 registry entries** (up from 57 after the first THT
expansion). Migrated via the same `scripts/migrate_sources.py
--verify-parity` workflow; parity confirmed (registry counts match the
Python fallback lists exactly).

**Article quantity: the pipeline now writes one article per brief topic,
not just one article per run.**

Previously `run-all.sh` always called `agent-02-writer.py --pick urgent`,
writing exactly one article regardless of how many topics Trend Hunter
found. This meant expanding the source pool increased topic *variety*
available for manual selection in the dashboard, but did nothing for batch
throughput. Two changes fixed this:

1. `agent-02-writer.py --pick` now accepts a numeric index (`--pick 0`,
   `--pick 1`, ...) in addition to `first`/`urgent`, via
   `load_brief()`. Out-of-range indices fail with a clear error
   (`--pick 5: индекс вне диапазона (0-2, доступно 3 тем)`) rather than a
   bare `IndexError`.
2. `run-all.sh` now reads how many topics `agent-01` actually returned
   (`TOPIC_COUNT`, read from `briefs.json`, capped by `--max-topics`,
   default raised from 3 to 5 — configurable via `NEWS_MAX_TOPICS`) and
   loops Writer → Quality Checker → SEO Doctor → Distributor once per topic,
   writing `/tmp/smtinsider_article_0.txt` .. `_N.txt` with matching
   `.meta.json` files. Publisher (when DB writes are enabled) submits all N
   articles, not just one.

The dashboard's interactive "run one agent" / "select a topic" flow is
**unchanged on purpose** — a human picking one topic and watching it get
written is a different (and still useful) interaction than unattended batch
generation, and changing it would undermine the topic-priority-selection UX
already built. `run-all.sh` is the batch/scheduled path; the dashboard is
the interactive path. They now diverge intentionally.

Also fixed in this pass: the dashboard's automatic full-pipeline run
(`_run_pipeline()` in `dashboard/app.py`) was missing the `2b` (Quality
Checker) step from its step list — `agent-02b-quality-checker.py` was
wired into `AGENT_CMDS` and runnable individually, but silently skipped
when the person clicked "run full pipeline". Fixed by adding `"2b"` to the
`steps` list.

Related knob increases (larger source pool → more raw signals per scan, so
the downstream limits that were tuned for ~57 sources were raised
proportionally):
- `NEWS_FULLTEXT_TOP_N`: 15 → 20 (more top-ranked signals get real
  extracted article text before topic selection)
- Signal diversification cap for the topic-selection prompt: 60 → 80 (more
  room for the larger, more diverse source pool without any single feed
  dominating)

## Active multi-source corroboration search (2026-07-13)

**Problem this fixes:** multi-source synthesis (Writer prompt instructions,
`source_expander.py` excerpt attachment — see the writing-quality passes
above) only ever worked when the generic pre-collection signal pool
*happened* to already contain two overlapping articles about the same
story. In practice a topic about one specific vendor announcement usually
only has one source in that pool — the 15-80 generic seed queries used for
initial collection are too broad to reliably surface a second, differently
phrased article about one specific product. So "multi-source" synthesis was
correct when it happened, but it didn't happen often enough.

**Fix:** `agents/agent-01-trend-hunter.py` now runs a **targeted
supplementary search per selected topic**, inside `build_briefs()`, after
`source_expander.expand_sources_for_topic()`'s first pass:

1. If a topic has fewer than `NEWS_MIN_SOURCES_PER_TOPIC` (default 2)
   sources after the first pass, `find_corroborating_sources()` runs 1-2
   targeted queries (the topic's own title, plus a keyword-anchored variant)
   through Google News RSS and DuckDuckGo — searching specifically for that
   topic, not the generic seed query set.
2. Results are filtered by title-similarity against the topic (threshold
   0.22, `source_expander.token_score()`) so an irrelevant result returned
   by a broad query doesn't get attached as a fake "second source".
3. Accepted results are attached directly to the topic's source list via
   `source_expander.add_source()`, bypassing a second, stricter
   full-text-similarity re-scan that (in testing) could re-filter out a
   result already confirmed relevant by the more targeted title check —
   two different similarity gates comparing different text spans, at
   different thresholds, should not both have to pass for a
   pre-validated source.
4. If nothing corroborating is found, the topic proceeds with its original
   source count — the Writer prompt (see "РАБОТА С НЕСКОЛЬКИМИ
   ИСТОЧНИКАМИ" / "РАБОТА С ФАКТАМИ" in `agents/prompts/writer.txt`)
   already handles the honest single-source case without fabricating a
   second perspective that doesn't exist.

Config:

```env
NEWS_MIN_SOURCES_PER_TOPIC=2       # target source count per topic
NEWS_TOPIC_SUPPLEMENTARY_SEARCH=1  # set 0 to disable (saves a scan a few extra requests per topic)
```

Bounded cost: at most 2 targeted queries × 2 search engines per
under-sourced topic, capped by `max_topics` (default 5) — worst case ~20
extra requests per scan, only for topics that actually need it.

Tested in `tests/test_multi_source_corroboration.py` (9 tests): relevant
corroboration is found and attached with the correct role/excerpt,
irrelevant results are filtered out, already-known URLs are skipped, the
`max_new` cap is respected, network/search failures degrade to an empty
result rather than crashing, and the feature can be disabled via env var.

## THT scope decision (2026-07-11)

An Apodex deep-research report surfaced 5 vendors covering THT insertion
(Sciencgo, Robotas), depaneling (ASYS Group / DIVISIO), in-circuit test
(Forwessun), and inspection (TAGARNO) that were not in the registry. Each
URL was independently verified (a live, dated news page fetched directly)
before being added — none were added on the report's word alone, per the
"a registry with 500 fake URLs is worse than 100 verified ones" principle.

**Decision: add the 5 verified vendor sources; do not build a full THT
editorial vertical.**

What was added:
- 5 vendor sources across 3 new categories (`tht_insertion`, `depaneling`,
  `test`) plus one more `inspection` entry (TAGARNO).
- THT/depaneling/test keywords added to `SMT_KEYWORDS` (the relevance gate
  used by `text_matches_smt()`) so signals from these sources aren't
  filtered out for lacking SMT-specific terms.
- Matching keyword weights added to `signal_editorial_score()` (through-hole,
  tht, insertion, depaneling, in-circuit test, ict, functional test, clinch)
  and the 5 new vendor domains added to the domain-authority bonus list.

What was deliberately **not** done:
- No new `editorial_type` (`news`/`insight`/`review`/`vendor` stay as-is) or
  `target_section` — THT content routes through the existing section logic.
- No new Writer prompt template or category taxonomy specific to THT.
- No dedicated THT keyword set in `SEED_QUERIES` / `GOOGLE_NEWS_QUERIES` —
  THT topics will surface opportunistically from the vendor/RSS channels,
  not from a dedicated THT search sweep.

Rationale: the 5 sources are low-risk, immediately useful, and use the
registry mechanism exactly as designed. A full THT vertical (dedicated
search queries, a THT-specific brief/writer prompt, possibly a new site
section) is a larger editorial and product decision — new keyword coverage
alone does not imply SMTInsider should reposition itself as covering THT
as a first-class beat. If THT coverage volume and quality turns out to
justify it, revisit as a separate, explicit scoping decision rather than
inferring it from a vendor list expansion.

## Next Steps (not done in this pass)

In the master plan's Step order (section 27), the next steps are:

1. **Source health tracking** (master plan section 10): add
   `last_attempt_at`, `last_success_at`, `consecutive_failures` etc. to a
   new `SourceHealth` model, updated after every collection run, with a
   `HEALTHY/DEGRADED/FAILING/DEAD` state derived from consecutive failures —
   surfaced as `reports/source_health.json` / `.md`.
2. **`BaseCollector` abstraction** (master plan section 7): extract
   `gather_rss_signals` / `gather_html_signals` / `gather_vendor_signals`
   into `RSSCollector` / `VendorCollector` / `HTMLCollector` classes under
   `src/collectors/`, sharing one HTTP retry/backoff/timeout policy instead
   of the two independent implementations that exist today
   (`agent-01-trend-hunter.py`'s `_http_get` vs `source_expander.py`'s bare
   `requests.get`).
3. **PDF/technical-document collector** (master plan section 12) — currently
   0% coverage, called out in the master plan as a critical priority.
