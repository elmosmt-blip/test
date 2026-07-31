# Agent Pipeline

## Agent #1 — Fresh News Trend Hunter

File:

```text
agents/agent-01-trend-hunter.py
```

Responsibilities:

- collect fresh SMT/electronics manufacturing signals from the last 30 days;
- reject undated stale results in strict mode;
- use RSS fallback when search engines throttle bot traffic;
- create `briefs.json` with `editorial_type`, `target_section`, and `section_routing`;
- exclude already-covered signals via `agents/dedupe.py`.

## Agent #2 — Writer

File:

```text
agents/agent-02-writer.py
```

Responsibilities:

- write article from brief;
- generate `article.txt` and `article.meta.json`;
- call `section_router.py` to decide section.

## Agent #3 — SEO Doctor

File:

```text
agents/agent-03-seo-doctor.py
```

Responsibilities:

- slug;
- meta description;
- JSON-LD;
- internal link suggestions.

## Agent #4 — Distributor

File:

```text
agents/agent-04-distributor.py
```

Responsibilities:

- LinkedIn post;
- forum answer;
- email block.

## Agent #5 — Analyst

File:

```text
agents/agent-05-analyst.py
```

Responsibilities:

- read real DB metrics;
- optional external analytics;
- deterministic recommendations with `--no-llm`.

## Agent #6 — Publisher

File:

```text
agents/agent-06-publisher.py
```

Responsibilities:

- create draft records;
- block duplicate drafts unless explicitly allowed;
- preserve markdown line breaks;
- choose/validate section via `section_router.py`;
- preserve `editorial_type` on approve.

## Agent #7 — YouTube Scout

File:

```text
agents/agent-07-youtube-scout.py
```

Responsibilities:

- search videos from the last `NEWS_LOOKBACK_DAYS` days;
- create video drafts only when DB writes are enabled.
