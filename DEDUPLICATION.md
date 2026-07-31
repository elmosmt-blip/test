# Duplicate Prevention

Problem:

A fresh news item can stay inside the 30-day collection window even after SMTInsider has already published a review/news/insight about it. The agents must not keep rewriting the same article.

## Implemented

Core module:

```text
agents/dedupe.py
```

Integrated into:

```text
agents/agent-01-trend-hunter.py
agents/agent-06-publisher.py
```

## Agent #1 behavior

After fresh signals are collected, Agent #1 checks existing `news` rows in Neon and removes signals already covered by SMTInsider.

Checks include:

- same source URL;
- same slug;
- same title;
- similar title;
- source URLs stored in `frontmatter_json`.

Env:

```env
NEWS_DEDUPE_EXISTING=1
```

Disable only for debugging:

```env
NEWS_DEDUPE_EXISTING=0
```

## Publisher behavior

Agent #6 blocks duplicate draft creation unless explicitly allowed.

Default:

```env
ALLOW_DUPLICATE_PUBLICATIONS=0
```

Manual override:

```bash
python3 agents/agent-06-publisher.py submit --meta article.meta.json --allow-duplicate
```

## Verified case

Existing published page:

```text
https://www.smtinsider.com/reviews/tri-tr7600-sv-series-axi-review
```

Fresh source:

```text
https://smttoday.com/2026/06/22/new-high-throughput-x-ray-inspection-system/
```

Result:

```text
Agent #1 excludes it as already covered.
Agent #6 blocks duplicate submit with reason=same_source_url, matched_id=2895.
```

## Latest test

Agent #1 fresh scan found:

```text
83 raw fresh signals
12 duplicate/covered signals excluded
71 new signals after dedupe
```

The already published TRI TR7600 SV article was excluded:

```text
New High Throughput X-ray Inspection System -> matched_id=2895 reason=same_source_url
```
