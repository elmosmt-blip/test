# Fresh Run Status

Latest fresh-news run:

```text
fresh-run-20260622T223245Z
```

Policy:

```text
NEWS_LOOKBACK_DAYS=30
NEWS_STRICT_FRESH=1
NEWS_VERIFY_DATES=1
NEWS_TIMEZONE=Asia/Jerusalem
```

Agent results:

- Agent #1 Trend Hunter: collected 37 fresh signals within 30 days.
- Agent #2 Writer: source-based article prepared from the selected fresh signal.
- Agent #3 SEO Doctor: executed.
- Agent #4 Distributor: executed.
- Agent #5 Analyst: executed with real DB metrics and `--no-llm`.
- Agent #6 Publisher: created draft article ID `2895`.
- Agent #7 YouTube Scout: executed for 30 days; no new video drafts created.

Created DB record:

```text
news ID: 2895
status: draft / is_published=false
title: TRI’s TR7600 SV AXI Launch: What Higher-Throughput X-ray Inspection Changes for SMT Lines
category: AOI Systems
editorial_type: insight
```

Artifacts:

```text
sample-output/fresh-latest/briefs.json
sample-output/fresh-latest/article.txt
sample-output/fresh-latest/article.meta.json
sample-output/fresh-latest/distribution.json
sample-output/fresh-latest/distribution.from_agent.json
sample-output/fresh-latest/sequential_run.log
sample-output/fresh-latest/dashboard_snapshot.html
```
