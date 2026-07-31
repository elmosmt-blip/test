"""
src/models/source.py — core source registry models.

These models are the first concrete architectural extraction requested by
00_MASTER_PLAN.md (section 8: SOURCE REGISTRY, section 27 Step 2-3).

Design constraints followed from the master plan:
  - "Only use fields actually required by the architecture. Do not create
    meaningless configuration fields that are never consumed." -> every
    field here maps to something an existing collector in
    agents/agent-01-trend-hunter.py actually reads today (name, url, group/
    category, feed kind) plus a small set of fields needed to support the
    next increment (enabled, priority, trust_level) without inventing a
    speculative schema no code will read for months.
  - "A source registry containing 500 fake URLs is worse than 100 verified
    sources." -> the migration step (src/config/loader.py +
    scripts/migrate_sources.py) copies *exactly* the URLs already present in
    DEFAULT_RSS_FEEDS / DEFAULT_HTML_SOURCES / DEFAULT_VENDOR_SOURCES in
    agents/agent-01-trend-hunter.py. No new source is invented in this pass.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl, PrivateAttr, field_validator, model_validator


class SourceType(str, Enum):
    """Collector family this source belongs to. Maps 1:1 to an existing
    collection function in agents/agent-01-trend-hunter.py today, and to a
    future BaseCollector subclass under src/collectors/."""

    RSS = "rss"
    VENDOR = "vendor"
    HTML = "html"
    YOUTUBE = "youtube"


class TrustLevel(str, Enum):
    """Coarse source-trust classification, per master plan section 17.
    Used as one *component* of a future decomposed confidence score -- not
    a replacement for the full evidence/confidence model, which is out of
    scope for this pass (see docs/SOURCE_REGISTRY.md, Next Steps).
    """

    OFFICIAL_VENDOR = "official_vendor"
    INDUSTRY_MEDIA = "industry_media"
    AGGREGATOR = "aggregator"
    UNKNOWN = "unknown"


class HtmlParserKind(str, Enum):
    """Which HTML parsing strategy gather_html_signals() should use.
    Mirrors the 'kind' string already used in DEFAULT_HTML_SOURCES."""

    SMTNET_NEWS = "smtnet_news"
    GENERIC_DATED_LIST = "generic_dated_list"


class SourceConfig(BaseModel):
    """A single collectible source: an RSS feed, a vendor newsroom page, or
    an HTML listing page. This is the unit stored in sources/*/*.yaml.
    """

    model_config = {"extra": "forbid"}

    id: str = Field(..., description="Stable slug identifier, e.g. 'smt-today-rss'")
    name: str = Field(..., description="Human-readable source name, e.g. 'SMT Today'")
    source_type: SourceType
    url: str = Field(..., description="Feed URL (rss) or page URL (vendor/html)")

    # Only meaningful for source_type == html
    html_parser: Optional[HtmlParserKind] = Field(
        default=None, description="Required when source_type == html"
    )

    # Only meaningful for source_type == vendor
    category: Optional[str] = Field(
        default=None,
        description="Vendor equipment category, e.g. 'inspection', 'placement', 'reflow'",
    )

    tags: list[str] = Field(default_factory=list)
    priority: int = Field(default=5, ge=1, le=10, description="1=highest, 10=lowest crawl priority")
    trust_level: TrustLevel = TrustLevel.UNKNOWN
    enabled: bool = True
    language: str = Field(default="en", description="ISO 639-1 language code")
    notes: str = Field(default="", description="Free-text provenance/maintenance note")

    @field_validator("id")
    @classmethod
    def id_must_be_slug(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9][a-z0-9\-]{1,79}$", v):
            raise ValueError(
                f"id {v!r} must be a lowercase slug matching ^[a-z0-9][a-z0-9-]{{1,79}}$ "
                f"(letters, digits, hyphens; 2-80 chars)"
            )
        return v

    @field_validator("url")
    @classmethod
    def url_must_be_valid_http(cls, v: str) -> str:
        # Delegate real validation to pydantic's HttpUrl, but store as plain
        # str so callers don't have to unwrap a Url object everywhere.
        HttpUrl(v)
        return v

    @model_validator(mode="after")
    def html_parser_required_for_html_sources(self) -> "SourceConfig":
        if self.source_type == SourceType.HTML and self.html_parser is None:
            raise ValueError(
                f"source {self.id!r}: source_type=html requires html_parser to be set "
                f"(one of {[k.value for k in HtmlParserKind]})"
            )
        if self.source_type == SourceType.VENDOR and not self.category:
            raise ValueError(
                f"source {self.id!r}: source_type=vendor requires a non-empty 'category' "
                f"(e.g. inspection, placement, reflow)"
            )
        return self

    def as_legacy_rss_tuple(self) -> tuple[str, str]:
        """Adapter back to the (name, url) tuple shape
        agents/agent-01-trend-hunter.py's DEFAULT_RSS_FEEDS currently uses,
        so the existing collector code can consume registry-loaded sources
        without a rewrite in this pass."""
        return (self.name, self.url)

    def as_legacy_vendor_tuple(self) -> tuple[str, str, str]:
        """Adapter back to the (name, url, category) tuple shape
        DEFAULT_VENDOR_SOURCES currently uses."""
        return (self.name, self.url, self.category or "vendor")

    def as_legacy_html_tuple(self) -> tuple[str, str, str]:
        """Adapter back to the (name, url, kind) tuple shape
        DEFAULT_HTML_SOURCES currently uses."""
        return (self.name, self.url, self.html_parser.value if self.html_parser else "generic_dated_list")


class SearchQueryConfig(BaseModel):
    """A seed query for a query-based collector (DuckDuckGo HTML search,
    Google News RSS search). These are not URL sources, so they are kept as
    a separate, much smaller model rather than forced into SourceConfig.
    """

    model_config = {"extra": "forbid"}

    id: str
    query: str
    engine: str = Field(default="duckduckgo", description="'duckduckgo' or 'google_news'")
    enabled: bool = True

    @field_validator("id")
    @classmethod
    def id_must_be_slug(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9][a-z0-9\-]{1,79}$", v):
            raise ValueError(f"id {v!r} must be a lowercase slug")
        return v

    @field_validator("query")
    @classmethod
    def query_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("query must not be empty")
        return v


class SourceRegistry(BaseModel):
    """A loaded, validated collection of sources -- the in-memory result of
    reading every YAML file under sources/. This is what src/config/loader.py
    returns; collectors and tests consume this, not raw YAML.
    """

    model_config = {"extra": "forbid"}

    sources: list[SourceConfig] = Field(default_factory=list)
    search_queries: list[SearchQueryConfig] = Field(default_factory=list)

    # Non-model-field scratch space used by loader.load_source_registry()
    # when called with strict=False, so callers can inspect what failed
    # without the loader raising. Not part of the validated schema itself.
    _load_errors: list = PrivateAttr(default_factory=list)

    @model_validator(mode="after")
    def ids_must_be_unique(self) -> "SourceRegistry":
        seen: set[str] = set()
        for s in self.sources:
            if s.id in seen:
                raise ValueError(f"duplicate source id: {s.id!r}")
            seen.add(s.id)
        seen_q: set[str] = set()
        for q in self.search_queries:
            if q.id in seen_q:
                raise ValueError(f"duplicate search_query id: {q.id!r}")
            seen_q.add(q.id)
        return self

    def enabled_sources(self, source_type: Optional[SourceType] = None) -> list[SourceConfig]:
        return [
            s for s in self.sources
            if s.enabled and (source_type is None or s.source_type == source_type)
        ]

    def enabled_queries(self, engine: Optional[str] = None) -> list[SearchQueryConfig]:
        return [
            q for q in self.search_queries
            if q.enabled and (engine is None or q.engine == engine)
        ]

    def by_id(self, source_id: str) -> Optional[SourceConfig]:
        for s in self.sources:
            if s.id == source_id:
                return s
        return None
