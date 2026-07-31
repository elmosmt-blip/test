# Section Routing Policy

Agents now choose the target site section automatically.

Site sections:

```text
news    -> /news/
insight -> /insights/
review  -> /reviews/
vendor  -> /vendors/
```

The selected section is stored in `news.editorial_type` and is **not cleared on approve**.

## Rules

### News

Use `news` for event-style updates:

- appointments;
- acquisitions / partnerships;
- facility openings;
- financial/market updates;
- general announcements without equipment evaluation.

### Insights

Use `insight` for engineering/process analysis:

- troubleshooting;
- process-control explainers;
- defect-cause analysis;
- checklists and control-plan articles;
- evergreen SMT production knowledge.

### Reviews

Use `review` for buyer-guide/equipment content:

- specific machine/platform/system/station/series;
- product launch rewritten as equipment evaluation;
- comparison / review / buyer guide;
- demo questions, adoption checks, configuration, service/training, MES integration.

Example: a fresh news item about a new AXI system should route to `review` if the article evaluates what SMT engineers should verify before adoption.

### Vendors

Use `vendor` for supplier/manufacturer/company profiles.

## Implementation

Core router:

```text
agents/section_router.py
```

Integrated into:

```text
agents/agent-01-trend-hunter.py  # adds editorial_type/target_section to briefs
agents/agent-02-writer.py        # writes section_routing into meta
agents/agent-06-publisher.py     # validates/repairs section before DB insert
 dashboard/app.py                # approve preserves editorial_type
```

## Safety

`approve` no longer sets `editorial_type=NULL`. The section must remain intact for correct routing.
