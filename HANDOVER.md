# SMTInsider Agent Team — Handover

This package contains the SMTInsider 7-agent project, dashboard, fresh-news collection logic, section routing, and documentation.

## Current production state

- Dashboard: FastAPI app in `dashboard/app.py`.
- Agents: `agents/agent-01...07`.
- Fresh-news collector: configured for the last 30 days.
- Section router: `agents/section_router.py` routes content to News / Insights / Reviews / Vendors.
- Publisher preserves `editorial_type` on approve so the site route remains correct.
- Safe mode: DB write actions require `ALLOW_DB_WRITES=1`.

## Important security note

The real `.env` file is **not included in the archive**. Use `.env.example` to recreate it.

## Latest notable published item

```text
ID: 2895
Section: Reviews
URL: https://www.smtinsider.com/reviews/tri-tr7600-sv-series-axi-review
Title: TRI TR7600 SV Series AXI Review: Higher-Throughput 3D X-Ray Inspection for SMT Lines
```

## Start dashboard

```bash
cd smtinsider-agent-team
cp .env.example .env
# fill .env
./start-dashboard.sh
```

Local URL:

```text
http://127.0.0.1:8800
```

## Read docs in order

```text
docs/00_QUICK_START.md
docs/01_ENVIRONMENT.md
docs/02_AGENT_PIPELINE.md
docs/03_FRESH_NEWS_COLLECTION.md
docs/04_SECTION_ROUTING.md
VENDOR_SOURCES.md
DEDUPLICATION.md
docs/05_DASHBOARD.md
docs/06_PUBLISHING_WORKFLOW.md
docs/07_VALIDATION_REPORT.md
docs/08_TROUBLESHOOTING.md
```
