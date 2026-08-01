# Control Room — UX/UI Audit and Redesign Plan

**Scope:** internal SMTInsider Control Room dashboard (`dashboard/app.py`).
**Direction:** hybrid product — a dark, operational control room; a separate light editorial design system for the future public site.
**Goal:** make the full content-production workflow understandable, reliable and fast: discover → verify → select → write → review → publish.

## Executive summary

The current dashboard is visually coherent but behaves like a developer console rather than an operator product. It places raw logs, all agents, PDF inputs, draft records and output in a dense three-column layout. The most important questions are not answered at a glance:

1. What should the operator do next?
2. Which run is active, how far has it progressed, and what is blocking it?
3. Did the collector find usable sources and topics?
4. Which topic is selected and what will be published?
5. Which warnings are actionable versus expected remote-source failures?

The redesign should not begin with decorative changes. It should first establish workflow states, source health, clear output hierarchy and reusable components.

## Current-state findings

### 1. Information architecture is tool-first, not workflow-first

The left rail lists all agents, while the center is a log/topics/article tab set and the right rail is drafts. This maps the implementation, not the operator’s task.

**Impact:** users must infer dependency order (for example, Trend Hunter → topic selection → Writer) and cannot immediately see whether an agent is eligible to run.

### 2. The raw log dominates critical feedback

The log is the default destination and currently includes long command lines, repeated remote-source failures and technical output. Important success events and next actions are visually equivalent to low-value diagnostics.

**Impact:** users perceive the system as broken even when it has collected usable material. The screenshots show this directly: source warnings obscure successful signals and generated topics.

### 3. Status states are underspecified

`idle`, `running`, `done` and `error` appear on agent cards, but there is no duration, start time, current stage, source count, output count, retry action or error classification.

**Impact:** “RUNNING” can look stalled; “DONE” does not explain what was produced; an error does not identify whether it is user input, local configuration, network availability or a remote-source failure.

### 4. PDF Scout is compressed into a secondary sidebar form

The PDF workflow has multiple meaningful choices — file/URL, document type, title quality, topic count, extraction state, source quality and write mode — but is represented by a narrow form with inline styles.

**Impact:** a high-value workflow feels risky and users cannot inspect the parsed document before starting expensive downstream work.

### 5. Topics lack decision support

Topic cards show detailed source/fact data but do not present a strong editorial decision: source freshness, confidence, source-health status, estimated writing readiness, duplicate risk, or a single primary call-to-action.

### 6. Accessibility and responsiveness are limited

The fixed 280px / flexible / 340px three-column, 100vh layout will be cramped on common laptop resolutions. Dense 10–11px text, low-contrast muted labels and reliance on color reduce scanability and accessibility.

### 7. Presentation and implementation are tightly coupled

The full HTML, CSS and JavaScript live inside `dashboard/app.py`; there are many inline style declarations. This slows redesign, makes visual regression difficult and prevents a consistent component system.

## Product model for the redesign

### Primary navigation

Replace agent-centric navigation with five workspaces:

1. **Overview** — current run, queue, health summary, outputs and next recommended action.
2. **Collect** — Trend Hunter, PDF Scout, source-health dashboard and run history.
3. **Plan** — topics, source evidence, selection, duplicate checks and editorial priorities.
4. **Create** — Writer, quality, SEO and article preview in one production flow.
5. **Publish** — drafts, approvals, distribution status and database/publishing events.

A compact **Runs** drawer provides full technical logs and diagnostics without making logs the main screen.

### Primary operator flow

```
Collect source → Inspect evidence → Select topic → Generate draft
→ Review quality/SEO → Approve → Publish/distribute
```

Every screen should show the current item, its state, the next permitted action and the reason an action is unavailable.

## Proposed key screens

### A. Overview

- Header: environment status, current run, notifications and account/settings.
- “Today” KPI row: fresh signals, healthy/degraded sources, topics ready, drafts awaiting approval.
- Current run card: stage progress, elapsed time, latest meaningful event, Stop/Retry/View details.
- Next action card: e.g. “Choose one of 3 verified topics to start writing.”
- Recent activity timeline: concise human summaries, not raw process output.

### B. Collect

Two tabs: **Web sources** and **Manual PDF**.

**Web sources:**
- run configuration with freshness window and source groups;
- source health table: source, active channel, last successful collection, result count, status, fallback in use, retry;
- collection results grouped by source and deduplicated.

**Manual PDF:**
- large drag-and-drop upload zone plus URL input;
- document setup panel: format, title override, topic limit;
- extraction stepper: upload → parse → verify metadata → topics;
- parsed-document preview: title, page count, detected company, date, first text excerpt and source URL;
- explicit choices: **Create topic briefs** / **Create briefs and write drafts**.

### C. Plan

- Filterable topic list with source count, freshness, confidence, category and readiness.
- Detail pane with one selected topic, source evidence, extracted technical facts and duplicate warnings.
- Clear CTA: **Select for writing**.
- Avoid treating raw extracted text as a headline; show fallback/metadata quality visibly.

### D. Create

- A stage stepper: Brief → Draft → Quality → SEO → Distribution.
- Article reader with title, metadata, source citations and quality score in a sticky side panel.
- Explicit error/action states such as “Writer blocked: no selected topic.”
- Before/after revision comparison for Quality Checker changes.

### E. Publish

- Draft queue with states: Needs review, Approved, Published, Failed.
- Strong approval confirmation with destination and publication metadata.
- Distribution outputs (LinkedIn/forum/email) grouped under the associated article.

## Visual system

### Internal Control Room (dark)

- Keep dark mode but move from “terminal” aesthetics to a high-contrast operations product.
- Use one accent color for primary action (emerald), blue for information, amber for attention and red only for blocking failures.
- Increase base text size to 14px and metadata to at least 12px.
- Use semantic badges with icon + text, never color alone.
- Restrict monospace to IDs, timestamps, command snippets and technical fields.
- Replace inline style declarations with tokenized CSS variables and components.

### Public editorial site (light; future phase)

- Warm white/near-white backgrounds, ink-black typography and restrained SMT-green accent.
- Editorial serif/display typography only for headlines; robust sans-serif for body and navigation.
- Reading-first article layout, clear source/citation blocks, technical-spec cards and related coverage.
- The public site should share semantic tokens and data models with Control Room but not share its dark visual language.

## Component inventory

Build reusable components before rebuilding screens:

- `AppShell`, `TopBar`, `SideNav`, `PageHeader`
- `StatusBadge`, `HealthBadge`, `ProgressStepper`, `EmptyState`
- `RunCard`, `RunTimeline`, `LogDrawer`, `ErrorCallout`
- `SourceHealthTable`, `SourceEvidenceCard`, `FactList`
- `UploadDropzone`, `DocumentPreview`, `TopicCard`, `TopicDetail`
- `ArticlePreview`, `QualityScore`, `ApprovalDialog`, `Toast`

## Source-error design

Remote failures must be classified and summarized instead of flooding the log:

| Class | User-facing wording | Default action |
|---|---|---|
| `source_degraded` | “RSS temporarily unavailable; backup channel is active.” | Continue run |
| `source_stale_url` | “Source address needs updating.” | Queue source-health task |
| `source_blocked` | “Site does not permit automated access; using permitted fallback when available.” | Continue run |
| `local_configuration` | “Database/API configuration is incomplete.” | Block relevant action |
| `input_error` | “The uploaded document could not be parsed.” | Request correction |

The detailed HTTP error and stack trace belong in the Run Details drawer, not in the primary workflow view.

## Technical implementation approach

1. Keep FastAPI and existing API routes during the first redesign to avoid disrupting agents.
2. Extract the inline dashboard into:
   - `dashboard/templates/` for page templates;
   - `dashboard/static/css/` for tokens/components/pages;
   - `dashboard/static/js/` for API client, state and views.
3. Add a `/api/runs/{run_id}` status model with stage, elapsed time, progress, output counts and structured warnings.
4. Persist run history and source-health state instead of retaining only a transient SSE queue.
5. Keep SSE for live updates, but publish structured events (`run_started`, `stage_changed`, `source_degraded`, `output_ready`, `run_completed`) rather than only log lines.
6. Add screenshot-level UI tests and API tests for each workflow state.

## Phased delivery

### Phase 0 — foundation

- Design tokens, typography, spacing, icons and responsive breakpoints.
- Extract CSS/JS from `dashboard/app.py`.
- Define structured run/source-health event schema.

### Phase 1 — operational clarity

- New app shell and Overview.
- Run progress/status cards and log drawer.
- Source-health classification and summaries.
- Responsive layout.

### Phase 2 — collection and PDF Scout

- Dedicated Collect workspace.
- PDF upload/parse/preview workflow.
- Clear completion state that links directly to generated topics.

### Phase 3 — planning and creation

- Topic evidence and selection workspace.
- Writer/quality/SEO stage stepper and article workspace.

### Phase 4 — publication and polish

- Publishing queue, approvals and distribution.
- Keyboard navigation, focus states, loading/skeleton states, empty/error states, accessibility review.

## Definition of done for the first redesign release

- An operator can understand the next required action within five seconds.
- A completed collection visibly shows output counts and a direct route to topics.
- A remote-source warning never looks like a whole-pipeline failure.
- A PDF is previewed and its parsed title can be corrected before topic generation.
- The dashboard is usable at 1280px desktop width and tablet width without clipped controls.
- Every agent state has a human-readable explanation and a next action.
- Logs remain available for diagnostics but are not the default decision surface.
