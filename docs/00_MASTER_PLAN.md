# 00_MASTER_PLAN.md

# SMTInsider Industrial Intelligence Platform — Master Execution Plan

## ROLE

You are acting as:

* Principal Software Architect
* Staff Python Engineer
* Data Intelligence Architect
* AI/LLM Systems Engineer
* Web Crawling Architect
* Search and Information Retrieval Engineer
* Technical Lead

You are working directly with an existing production-oriented Python repository.

This is not a theoretical architecture exercise.

You must inspect, understand, modify, test, and improve the actual repository.

The current repository already contains working functionality.

Your responsibility is to evolve it without blindly destroying existing working behavior.

---

# 1. PRIMARY OBJECTIVE

Transform the existing SMTInsider agent project from a news collection and article generation pipeline into a comprehensive Industrial Intelligence Platform focused on:

* SMT
* EMS
* PCB
* PCBA
* THT
* semiconductor manufacturing
* electronics manufacturing
* electronics assembly
* AOI
* SPI
* AXI
* X-Ray inspection
* machine vision
* industrial AI
* solder paste printing
* pick-and-place
* reflow
* wave soldering
* selective soldering
* conformal coating
* cleaning
* dispensing
* traceability
* MES
* factory automation
* robotics
* component inspection
* electronics manufacturing software

The highest priority is:

> MAXIMIZE THE QUALITY, QUANTITY, COVERAGE, FRESHNESS, AND VERIFIABILITY OF COLLECTED INFORMATION.

Article generation is secondary.

SEO is secondary.

Publishing is secondary.

The information collection layer is the foundation of the entire platform.

If collection quality is poor, all downstream AI agents produce poor results.

---

# 2. EXISTING PROJECT

The repository currently contains the following major components:

```text
agents/
├── agent-01-trend-hunter.py
├── agent-02-writer.py
├── agent-02b-quality-checker.py
├── agent-03-seo-doctor.py
├── agent-04-distributor.py
├── agent-05-analyst.py
├── agent-06-publisher.py
├── agent-07-youtube-scout.py
├── dedupe.py
├── llm_cache.py
├── llm_client.py
├── section_router.py
├── source_expander.py
└── prompts/
```

The project also contains:

```text
dashboard/
README.md
NEWS_COLLECTION.md
VENDOR_SOURCES.md
DEDUPLICATION.md
SECTION_ROUTING.md
DEPLOY.md
HANDOVER.md
run-all.sh
smoke-test.sh
verify-agents-safe.sh
```

Do not assume the documentation is fully synchronized with the implementation.

The Python source code is the primary source of truth.

Documentation is supporting evidence only.

---

# 3. CRITICAL EXECUTION RULE

Before modifying any code:

1. Inspect the complete repository.
2. Read every Python file.
3. Read every prompt.
4. Read requirements.txt.
5. Read all architecture and deployment documentation.
6. Trace the actual execution path from run-all.sh.
7. Identify input and output files for every agent.
8. Identify all shared data formats.
9. Identify environment variables.
10. Identify external APIs.
11. Identify LLM dependencies.
12. Identify scraping mechanisms.
13. Identify duplicate detection logic.
14. Identify source discovery logic.
15. Identify error handling and retry logic.

Do not start refactoring based only on filenames.

Build a real understanding of the current implementation.

---

# 4. FIRST REQUIRED DELIVERABLE — CURRENT SYSTEM MAP

Before making architectural changes, create:

```text
docs/CURRENT_SYSTEM_MAP.md
```

The document must describe the real current system.

Include:

## 4.1 Execution Flow

Example format:

```text
run-all.sh
    ↓
agent-01-trend-hunter.py
    ↓
output artifact
    ↓
agent-02-writer.py
    ↓
agent-02b-quality-checker.py
    ↓
agent-03-seo-doctor.py
    ↓
...
```

Use the actual flow found in the repository.

Do not invent components.

## 4.2 Agent Responsibility Matrix

Create a table:

| Agent | Responsibility | Inputs | Outputs | LLM Used | External Sources |
| ----- | -------------- | ------ | ------- | -------- | ---------------- |

Populate it from actual code.

## 4.3 Shared Modules

Analyze:

* dedupe.py
* llm_cache.py
* llm_client.py
* section_router.py
* source_expander.py

Explain:

* responsibilities
* coupling
* reusable functionality
* architectural problems
* technical debt

## 4.4 Information Collection Map

Document every current source type.

Examples:

* RSS
* DuckDuckGo
* vendor websites
* YouTube
* direct HTML pages

For every source mechanism document:

```text
Source Type
Implementation File
Discovery Method
Fetch Method
Parsing Method
Deduplication Method
Failure Handling
Rate Limiting
Caching
Coverage Limitations
```

## 4.5 Data Flow

Document the actual object or JSON structures passed between agents.

Do not describe hypothetical schemas.

Extract schemas from real implementation.

---

# 5. ARCHITECTURAL PRINCIPLE

The future system must separate:

```text
SOURCE REGISTRY

        ↓

DISCOVERY

        ↓

FETCHING

        ↓

CONTENT EXTRACTION

        ↓

NORMALIZATION

        ↓

DEDUPLICATION

        ↓

ENTITY EXTRACTION

        ↓

EVENT RESOLUTION

        ↓

SOURCE CORRELATION

        ↓

FACT VERIFICATION

        ↓

INTELLIGENCE SCORING

        ↓

KNOWLEDGE STORAGE

        ↓

TREND DETECTION

        ↓

EDITORIAL PLANNING

        ↓

ARTICLE GENERATION

        ↓

QUALITY CONTROL

        ↓

SEO

        ↓

PUBLISHING
```

These responsibilities must not remain mixed inside one large agent.

---

# 6. COLLECTION-FIRST ARCHITECTURE

The current architecture appears centered around Trend Hunter.

This must evolve.

Do not immediately delete:

```text
agents/agent-01-trend-hunter.py
```

First understand all its behavior.

Then extract reusable functionality.

The target architecture should introduce:

```text
src/
├── collectors/
├── discovery/
├── fetchers/
├── extractors/
├── normalization/
├── deduplication/
├── entities/
├── events/
├── verification/
├── intelligence/
├── storage/
├── llm/
├── pipelines/
├── models/
├── config/
└── utils/
```

Exact names may be adjusted if repository constraints justify another structure.

Any deviation must be documented.

---

# 7. COLLECTOR ARCHITECTURE

Implement a common collector contract.

Conceptually:

```python
class BaseCollector:
    async def discover(self):
        ...

    async def collect(self):
        ...

    async def normalize(self):
        ...
```

Do not blindly use this exact interface if a better abstraction fits the existing code.

The important requirement is consistent collector behavior.

Target collectors:

```text
RSSCollector
VendorCollector
PressReleaseCollector
PDFCollector
TechnicalDocumentCollector
YouTubeCollector
PatentCollector
ResearchCollector
GitHubCollector
ForumCollector
ConferenceCollector
JobsCollector
FinancialCollector
```

Social sources requiring authentication, restricted APIs, or platform-specific legal constraints must be implemented only through sustainable and compliant access methods.

Do not introduce fragile scraping that immediately causes account bans or breaks the whole pipeline.

---

# 8. SOURCE REGISTRY

Hardcoded source lists must gradually be removed from Python implementation.

Create a configurable source registry.

Target structure:

```text
sources/
├── rss/
│   ├── smt.yaml
│   ├── pcb.yaml
│   ├── ems.yaml
│   ├── semiconductor.yaml
│   ├── automation.yaml
│   └── industrial_ai.yaml
│
├── vendors/
│   ├── placement.yaml
│   ├── aoi.yaml
│   ├── spi.yaml
│   ├── xray.yaml
│   ├── printing.yaml
│   ├── reflow.yaml
│   ├── soldering.yaml
│   ├── coating.yaml
│   ├── cleaning.yaml
│   ├── materials.yaml
│   └── software.yaml
│
├── youtube/
├── research/
├── patents/
├── conferences/
├── forums/
├── github/
├── jobs/
└── finance/
```

Source configuration should support fields such as:

```yaml
id:
name:
source_type:
homepage:
country:
language:
industry:
categories:
tags:
priority:
trust_level:
enabled:
crawl_frequency:
discovery_urls:
rss_urls:
news_urls:
press_release_urls:
product_urls:
download_urls:
support_urls:
manual_urls:
document_patterns:
```

Only use fields actually required by the architecture.

Do not create meaningless configuration fields that are never consumed.

All configuration must be validated.

Use Pydantic models where appropriate.

Invalid source configuration must fail with a useful diagnostic message.

---

# 9. SOURCE EXPANSION TARGET

The project must be designed to support:

* 200+ RSS feeds
* 300+ vendor websites
* 100+ relevant YouTube channels
* major patent databases
* major research indexes
* important SMT and electronics manufacturing conferences
* industry forums
* public financial disclosures
* public job postings
* technical document repositories

Important:

Do not add fake sources.

Do not invent RSS URLs.

Do not invent vendor pages.

Do not assume a URL exists because its path looks logical.

Every added source must be either:

1. already present in the project;
2. programmatically discovered and validated;
3. manually verified;
4. returned by a supported search/discovery mechanism.

A source registry containing 500 fake URLs is worse than 100 verified sources.

Quality and validation are mandatory.

---

# 10. SOURCE HEALTH SYSTEM

Every registered source must have health information.

Track:

```text
last_attempt_at
last_success_at
last_content_at
consecutive_failures
http_status
average_response_time
items_discovered
items_accepted
items_rejected
duplicate_rate
parser_error_rate
```

Calculate source state:

```text
HEALTHY
DEGRADED
FAILING
DEAD
DISABLED
```

Do not permanently remove failing sources automatically.

Mark and report them.

Implement a source health report.

Target output:

```text
reports/source_health.json
```

and, where practical:

```text
reports/source_health.md
```

---

# 11. INFORMATION DISCOVERY

Do not rely exclusively on predefined URLs.

Implement discovery mechanisms.

Examples:

```text
RSS autodiscovery
sitemap.xml discovery
robots.txt inspection
news page discovery
press release section discovery
download center discovery
document library discovery
PDF link discovery
product page discovery
YouTube channel discovery
conference exhibitor discovery
```

Discovery results must be validated before being promoted into the active registry.

Use states:

```text
DISCOVERED
VALIDATING
ACTIVE
REJECTED
DEAD
```

Store discovery evidence.

Example:

```text
discovered_from
discovered_at
validation_method
validation_status
```

---

# 12. PDF AND TECHNICAL DOCUMENT INTELLIGENCE

PDF collection is a critical priority.

The platform must identify:

* brochures
* datasheets
* application notes
* white papers
* case studies
* manuals
* catalogs
* technical presentations
* conference papers
* product specifications

PDF discovery must support:

* direct HTML links
* sitemap discovery
* download centers
* search results
* document pattern detection

For every document extract:

```text
title
document_type
company
products
technologies
publication_date
document_date
language
page_count
text
metadata
source_url
file_hash
text_hash
```

When practical extract:

* tables
* model names
* machine specifications
* throughput values
* accuracy values
* resolution values
* inspection speed
* supported component sizes
* dimensions
* process capabilities

Never fabricate a technical specification.

All extracted technical facts must preserve source provenance.

---

# 13. RAW DATA PRESERVATION

Never discard original collected data before normalization.

Use a multi-stage model:

```text
RawSourceRecord
        ↓
ExtractedContent
        ↓
NormalizedDocument
        ↓
ResolvedEvent
```

Raw records must preserve enough information for reprocessing.

Store:

```text
original_url
final_url
fetch_timestamp
status_code
headers where useful
content_type
raw content reference
collector
source_id
```

Do not store huge binary payloads directly inside JSON objects if filesystem or object storage is more appropriate.

---

# 14. NORMALIZED DATA MODEL

Create explicit models.

Possible conceptual structure:

```python
SourceRecord
Document
Entity
Event
Evidence
Relationship
```

A normalized document should support:

```text
id
title
summary
content
source_id
source_type
url
canonical_url
published_at
discovered_at
fetched_at
language
country
companies
products
technologies
people
document_type
tags
content_hash
semantic_hash
confidence
evidence
```

Use stable IDs where possible.

Avoid random IDs when deterministic IDs are more appropriate.

---

# 15. DEDUPLICATION

Existing `agents/dedupe.py` must be carefully analyzed.

Do not remove its behavior until replacement functionality is tested.

Implement multiple deduplication levels.

## Level 1 — Exact URL

Canonical URL comparison.

Remove:

* tracking parameters
* UTM parameters
* unnecessary fragments

## Level 2 — Content Hash

Detect exact content duplication.

## Level 3 — Near Duplicate

Use normalized title and text similarity.

## Level 4 — Semantic Duplicate

Use embeddings where available.

## Level 5 — Event Duplicate

Determine whether multiple documents describe the same real-world event.

Example:

```text
ASMPT launches inspection system
ASMPT unveils new inspection platform
New ASMPT system presented at Productronica
```

These may represent one event.

Documents must not be deleted.

They must be linked to the same event.

This distinction is critical.

---

# 16. EVENT RESOLUTION

Introduce an event model.

Examples of events:

```text
PRODUCT_LAUNCH
PRODUCT_UPDATE
COMPANY_ACQUISITION
PARTNERSHIP
PATENT_PUBLICATION
RESEARCH_PUBLICATION
CONFERENCE_PRESENTATION
FACTORY_EXPANSION
MANAGEMENT_CHANGE
FINANCIAL_RESULT
NEW_TECHNOLOGY
SOFTWARE_RELEASE
AI_IMPLEMENTATION
```

One event may contain many evidence documents.

Example:

```text
Event
├── official press release
├── product PDF
├── YouTube demonstration
├── conference page
├── distributor article
└── forum discussion
```

The event must maintain provenance to every evidence source.

---

# 17. CONFIDENCE AND EVIDENCE

Do not use one arbitrary confidence score produced by an LLM.

Confidence must be explainable.

Separate:

```text
source_trust
evidence_count
independent_source_count
official_confirmation
technical_document_confirmation
semantic_consistency
contradiction_penalty
freshness
```

Example source classes:

```text
OFFICIAL_VENDOR
REGULATORY
PATENT_DATABASE
ACADEMIC
FINANCIAL_DISCLOSURE
CONFERENCE
INDUSTRY_MEDIA
DISTRIBUTOR
SOCIAL
VIDEO
FORUM
UNKNOWN
```

The final score must expose its components.

Example:

```json
{
  "confidence": 0.91,
  "components": {
    "official_confirmation": 1.0,
    "independent_sources": 0.7,
    "technical_evidence": 1.0,
    "semantic_consistency": 0.95,
    "contradiction_penalty": 0.0
  }
}
```

Exact formula must be documented and tested.

---

# 18. ENTITY EXTRACTION

Extract and normalize:

```text
Company
Brand
Product
Product Family
Machine Model
Technology
Person
Conference
Organization
Standard
Material
Software
AI Model
Process
```

Entity aliases are required.

Example:

```text
Koh Young
Koh Young Technology
KYT
```

Do not automatically merge entities based only on similar names.

Entity resolution must preserve ambiguity.

---

# 19. KNOWLEDGE RELATIONSHIPS

Support relationships such as:

```text
COMPANY -> MANUFACTURES -> PRODUCT
PRODUCT -> USES -> TECHNOLOGY
COMPANY -> ANNOUNCED -> EVENT
EVENT -> SUPPORTED_BY -> DOCUMENT
PRODUCT -> PRESENTED_AT -> CONFERENCE
PERSON -> WORKS_FOR -> COMPANY
PATENT -> ASSIGNED_TO -> COMPANY
RESEARCH -> MENTIONS -> TECHNOLOGY
PRODUCT -> REPLACES -> PRODUCT
COMPANY -> ACQUIRED -> COMPANY
```

Every relationship must preserve evidence provenance.

The system must answer:

> Why does the platform believe this relationship exists?

If no evidence can be shown, the relationship must not be treated as verified fact.

---

# 20. LLM USAGE POLICY

LLMs must not be used for tasks that deterministic code performs better.

Use deterministic code for:

* URL normalization
* hashing
* HTTP status handling
* MIME detection
* exact deduplication
* date parsing where deterministic parsing works
* configuration validation

Use LLMs for:

* ambiguous entity extraction
* event classification
* semantic comparison
* relationship extraction
* technical summarization
* contradiction analysis
* editorial planning

LLM output must use structured schemas.

Prefer Pydantic validated structured output.

Invalid LLM output must be retried or rejected.

Never silently accept malformed data.

---

# 21. LLM COST AND CACHE

Analyze existing:

```text
agents/llm_cache.py
agents/llm_client.py
```

Preserve useful functionality.

Implement or improve:

```text
prompt versioning
model identification
input hash
structured output cache
TTL where appropriate
token accounting
request accounting
failure tracking
retry tracking
```

Do not call an LLM twice for identical immutable content unless prompt version or model strategy changed.

---

# 22. OBSERVABILITY

Every pipeline stage must use structured logging.

Required fields where applicable:

```text
timestamp
run_id
collector
source_id
document_id
event_id
stage
duration_ms
status
error_type
retry_count
```

Avoid uncontrolled `print()` debugging in production pipeline code.

Human-readable console logs may remain, but structured logs must be available.

---

# 23. FAILURE ISOLATION

One source failure must never stop the entire collection pipeline.

One collector failure must not corrupt other collectors.

One malformed PDF must not terminate document processing.

One invalid LLM response must not destroy the batch.

Implement:

```text
timeouts
bounded retries
exponential backoff
failure isolation
dead-letter handling where justified
```

Avoid infinite retries.

---

# 24. ASYNC AND CONCURRENCY

Use asynchronous I/O for network-heavy workloads where appropriate.

Concurrency must be bounded.

Do not create unlimited tasks.

Support:

```text
global concurrency limit
per-domain concurrency limit
per-source rate limit
request timeout
retry policy
```

Respect source stability.

The goal is maximum sustainable collection, not maximum abusive request volume.

---

# 25. BACKWARD COMPATIBILITY

Existing commands and scripts must remain functional during migration where practical.

Analyze:

```text
run-all.sh
smoke-test.sh
verify-agents-safe.sh
start-dashboard.sh
```

If a command must change:

1. document why;
2. provide migration instructions;
3. update related scripts;
4. update documentation;
5. add a compatibility wrapper where reasonable.

Do not leave broken scripts in the repository.

---

# 26. TESTING REQUIREMENTS

Every major architectural extraction must include tests.

Required test categories:

```text
source config validation
URL canonicalization
RSS parsing
HTML extraction
PDF classification
content hashing
duplicate detection
event merging
confidence scoring
collector failure isolation
LLM structured output validation
```

Use real sanitized fixtures where practical.

Do not make the entire test suite dependent on live websites.

Live integration tests must be separated from deterministic unit tests.

---

# 27. IMPLEMENTATION STRATEGY

Do not rewrite the whole repository in one operation.

Use incremental migration.

Required order:

## Step 1

Create the current system map.

## Step 2

Create core data models.

## Step 3

Create source registry and validation.

## Step 4

Extract RSS collection into the collector framework.

## Step 5

Extract vendor collection.

## Step 6

Implement source health tracking.

## Step 7

Implement raw record preservation.

## Step 8

Implement normalization.

## Step 9

Upgrade deduplication.

## Step 10

Implement PDF and technical document collection.

## Step 11

Implement event resolution.

## Step 12

Implement evidence and confidence model.

## Step 13

Implement entity extraction.

## Step 14

Implement relationships and knowledge storage.

## Step 15

Add additional collectors.

## Step 16

Integrate intelligence pipeline with existing Writer, Quality Checker, SEO Doctor, Distributor, Analyst, and Publisher agents.

At every step the project should remain testable.

---

# 28. DO NOT DO

Do not:

* invent source URLs
* fabricate RSS feeds
* add fake vendors
* add placeholder collectors and claim they are complete
* silently remove existing features
* rewrite code only for aesthetics
* overengineer simple tasks
* use LLMs for deterministic operations
* store secrets in source code
* commit API keys
* create unlimited async concurrency
* bypass errors with broad `except: pass`
* claim tests passed without executing them
* claim a source works without validating it
* fabricate technical specifications
* treat forum claims as verified facts
* treat LLM output as evidence
* generate articles before preserving source provenance

---

# 29. EXECUTION REPORT

After every implementation batch, output:

## COMPLETED

Exact tasks completed.

## FILES CREATED

List every created file.

## FILES MODIFIED

List every modified file.

## ARCHITECTURE CHANGES

Explain actual architecture changes.

## DATA FLOW CHANGES

Explain changes to data flow.

## BACKWARD COMPATIBILITY

State what was preserved and what changed.

## TESTS EXECUTED

Provide exact commands executed.

Provide actual result.

Do not say "tests should pass".

Run them.

## KNOWN LIMITATIONS

List real limitations.

## NEXT STEP

State the exact next implementation step.

---

# 30. FIRST EXECUTION TASK

Start now.

Do not provide another high-level proposal.

Do not ask whether to continue.

Do not only create documentation.

Perform the following work:

1. Inspect the entire repository.
2. Create `docs/CURRENT_SYSTEM_MAP.md`.
3. Identify all information collection logic in:

   * `agents/agent-01-trend-hunter.py`
   * `agents/agent-07-youtube-scout.py`
   * `agents/source_expander.py`
   * `agents/dedupe.py`
4. Identify every hardcoded source.
5. Identify every external search mechanism.
6. Identify current data schemas.
7. Identify collection bottlenecks.
8. Create the initial target architecture.
9. Create core source registry models.
10. Create source configuration validation.
11. Migrate existing hardcoded RSS and vendor sources into configuration files without losing current sources.
12. Add tests for source configuration loading and validation.
13. Run the tests.
14. Report actual results.

Do not stop after analysis if repository modification is possible.

Begin execution immediately.
