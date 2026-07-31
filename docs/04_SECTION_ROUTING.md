# Section Routing

Core file:

```text
agents/section_router.py
```

The router chooses:

```text
news    -> /news/
insight -> /insights/
review  -> /reviews/
vendor  -> /vendors/
```

## Rules

### News

Use for appointments, acquisitions, partnerships, facility openings, market reports, and short event-style updates.

### Insights

Use for technical process analysis, troubleshooting, defect explanation, process-control plans, and evergreen engineering articles.

### Reviews

Use for specific equipment/platform/system/series content, buyer guides, comparisons, adoption checks, demo questions, and product launches rewritten as equipment evaluation.

Example:

```text
New High Throughput X-ray Inspection System -> review /reviews/
```

### Vendors

Use for vendor/supplier/company profiles.

## Important

`editorial_type` must remain in the DB. It controls routing. Approve must not set it to NULL.
