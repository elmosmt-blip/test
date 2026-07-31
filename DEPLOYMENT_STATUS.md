# Deployment Status

## Current state

DB is connected. Dashboard is operational. Write actions in dashboard remain protected by default:

```env
ALLOW_DB_WRITES=0
```

This keeps dashboard approve/delete/run-write actions blocked unless writes are explicitly enabled.

## Real sequential run completed

A real sequential run was completed after user confirmation:

- LLM mock article generation was not used.
- Agent #1/#2/#4 LLM functions were performed by the Arena assistant using real web/fetched sources because no real external LLM API key/endpoint was provided.
- Agent #3/#5/#6/#7 were executed with project scripts where applicable.
- DB writes were allowed for draft creation only.
- No approve/public publish was executed.

Run directory:

```text
real-run-20260622T215626Z
```

Created draft records:

```text
news article draft: 2894
videoitem draft: 51
```

Verification:

```text
NEWS_DRAFT=(2894, title, False, 'insight', 'Quality Control')
VIDEO_DRAFTS=[(51, title, False)]
DASHBOARD_DB_CONNECTED=True
DASHBOARD_ALLOW_DB_WRITES=False
DASHBOARD_SEES_ARTICLE_DRAFT=True
```

Artifacts copied to:

```text
sample-output/real-latest/briefs.json
sample-output/real-latest/article.txt
sample-output/real-latest/article.meta.json
sample-output/real-latest/distribution.json
sample-output/real-latest/sequential_real_run.log
sample-output/real-latest/REAL_RUN_REPORT.md
```

## Run dashboard

```bash
cd /home/user/smtinsider-agent-team
./start-dashboard.sh
```

Dashboard URL:

```text
http://127.0.0.1:8800
```

## Publishing note

Drafts remain unpublished. To publish an article, use an explicit approve command only after editorial review.
