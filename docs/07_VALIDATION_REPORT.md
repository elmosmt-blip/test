# Validation Report

Validation directory:

```text
validation-20260623T160807Z
```

Validated on: 2026-06-23T16:09:57Z

## Checks performed

- Python compile check for `agents/` and `dashboard/`.
- Section router examples.
- LLM healthcheck.
- Fresh-news collection in collect-only mode.
- Analyst read-only DB metrics with `--no-llm`.
- Publisher DB schema check, read-only.
- Dashboard smoke test: `/status` and `/drafts`.

## Result

```text
VALIDATION_OK
DB_CONNECTED=True
ALLOW_DB_WRITES=False
FRESH_SIGNAL_COUNT=25
DRAFTS_LOADED=7
```

Fresh signals collected during validation:

```text
25
```

No DB write was performed during validation. `ALLOW_DB_WRITES=0` was forced for the validation run.

## Validation artifacts

```text
validation-20260623T160807Z/fresh_signals_30d.json
validation-20260623T160807Z/fresh_collect.log
validation-20260623T160807Z/section_router.log
validation-20260623T160807Z/analyst_no_llm.log
validation-20260623T160807Z/publisher_check.log
validation-20260623T160807Z/dashboard_status.json
validation-20260623T160807Z/dashboard_drafts.json
```
