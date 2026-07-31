"""
SMTInsider PDF & Technical Document Collector (`src/collectors/pdf_collector.py`).

Provides specialized discovery, ingestion, classification, and technical
specification extraction for vendor PDFs (datasheets, brochures, application
notes, white papers, manuals, specifications, case studies).

Architecture Compliance (see docs/00_MASTER_PLAN.md §12):
  - Classifies document types (datasheet, brochure, app note, white paper, etc.).
  - Extracts title, document_type, company, products, technologies,
    publication_date, document_date, language, page_count, text, metadata,
    source_url, file_hash, and text_hash.
  - Extracts concrete engineering specifications (throughput, accuracy,
    resolution, component sizes, inspection speed, PCB dimensions, process
    capabilities) without fabricating values.
  - Every extracted technical fact preserves source provenance.
"""

from __future__ import annotations

import sys
# Ensure UTF-8 console output on Windows (prevent UnicodeEncodeError for emojis/box chars)
for _s in ("stdout", "stderr"):
    _stream = getattr(sys, _s, None)
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            try:
                _stream.reconfigure(errors="replace")
            except Exception:
                pass


import hashlib
import io
import json
import os
import re
import urllib.parse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests
from bs4 import BeautifulSoup

try:
    import pypdf
    _PYPDF_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PYPDF_AVAILABLE = False


class PDFDocumentType:
    """Standard classification categories for SMT technical documents."""
    DATASHEET = "datasheet"
    BROCHURE = "brochure"
    APPLICATION_NOTE = "application_note"
    WHITE_PAPER = "white_paper"
    CASE_STUDY = "case_study"
    MANUAL = "manual"
    CATALOG = "catalog"
    SPECIFICATION = "specification"
    PRESENTATION = "presentation"
    MAGAZINE = "magazine"
    ARTICLE = "article"
    INTERVIEW = "interview"
    TECHNICAL_DOCUMENT = "technical_document"


@dataclass
class TechnicalFact:
    """A single verifiable technical specification extracted from a document."""
    parameter: str
    value: str
    raw_context: str
    source_url: str
    provenance: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_fact_string(self) -> str:
        return f"{self.parameter}: {self.value} [{self.provenance}]"


@dataclass
class PDFDocument:
    """Normalized technical document representation."""
    title: str
    document_type: str
    company: str
    products: list[str] = field(default_factory=list)
    technologies: list[str] = field(default_factory=list)
    publication_date: Optional[str] = None
    document_date: Optional[str] = None
    language: str = "en"
    page_count: int = 1
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    source_url: str = ""
    file_hash: str = ""
    text_hash: str = ""
    key_facts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_signal(self, vendor_name: str = "", vendor_group: str = "") -> dict[str, Any]:
        """Convert this PDF document into a Trend Hunter signal dict."""
        snippet_text = re.sub(r"\s+", " ", self.text[:350]).strip()
        facts_summary = "; ".join(
            f"{f['parameter']}={f['value']}" for f in self.key_facts[:4] if isinstance(f, dict) and "value" in f
        )
        if facts_summary:
            snippet_text = f"{snippet_text} [Key Specs: {facts_summary}]"

        full_text_body = self.text[:1800]
        if self.key_facts:
            specs_block = "\n".join(
                f"- {f['parameter'].upper()}: {f['value']} ({f['provenance']})"
                for f in self.key_facts if isinstance(f, dict)
            )
            full_text_body = f"{full_text_body}\n\nVERIFIED TECHNICAL SPECIFICATIONS:\n{specs_block}"

        # String representation of facts for brief building & fact grounding
        fact_strings = [
            f"{f['parameter']}: {f['value']}"
            for f in self.key_facts if isinstance(f, dict) and "value" in f
        ]

        return {
            "title": self.title,
            "snippet": snippet_text,
            "full_text": full_text_body,
            "source": self.source_url,
            "query": f"PDF:{vendor_name or self.company or 'Vendor'}",
            "feed": vendor_name or self.company or "Technical Document",
            "vendor_group": vendor_group or "technical_document",
            "published_at": self.publication_date or "unknown",
            "date_source": "pdf_metadata" if self.publication_date else "unknown",
            "date_verified": bool(self.publication_date),
            "fresh_within_days": bool(self.publication_date),
            "document_type": self.document_type,
            "page_count": self.page_count,
            "file_hash": self.file_hash,
            "text_hash": self.text_hash,
            "company": self.company,
            "products": self.products,
            "technologies": self.technologies,
            "key_facts": fact_strings,
            "technical_specs": self.key_facts,
        }


# Known SMT vendors for automatic entity detection
_KNOWN_VENDORS = [
    "Koh Young", "ASMPT", "Yamaha", "Mycronic", "Viscom", "TRI", "Saki",
    "ViTrox", "Creative Electron", "Mirtec", "CyberOptics", "Heller", "Rehm",
    "Pillarhouse", "Nordson", "Indium Corporation", "IPC", "Photo Stencil",
    "Kurtz Ersa", "BTU International", "MEK", "TAGARNO", "ASYS Group", "Seica",
    "Robotas", "Sciencgo", "Controlar", "Delvitech", "Sincotron", "Europlacer",
    "Fuji Europe", "Essemtec", "Panasonic", "AIM Solder", "KYZEN",
]

_KNOWN_TECHNOLOGIES = [
    "3D AOI", "3D SPI", "AXI", "X-Ray", "AOI", "SPI", "SMT", "PCB", "PCBA",
    "Reflow", "Wave Soldering", "Selective Soldering", "Conformal Coating",
    "Pick and Place", "Placement", "Inspection", "Industry 4.0", "MES", "CFX",
    "IPC-CFX", "Through-Hole", "THT", "Depaneling", "In-Circuit Test", "ICT",
    "Functional Test", "Semiconductor Packaging",
]

# Non-technical / generic PDFs to exclude during discovery
_EXCLUDED_PDF_PATTERNS = {
    "privacy", "terms", "cookie", "legal", "sitemap", "tax", "w9",
    "iso9001", "certificate", "audit", "compliance-form", "supplier-code",
    "modern-slavery", "rohs-certificate",
}


def hash_bytes(data: bytes) -> str:
    """Compute SHA-256 hex digest of raw binary data."""
    return hashlib.sha256(data).hexdigest()


def hash_text(text: str) -> str:
    """Compute SHA-256 hex digest of normalized text."""
    norm = " ".join((text or "").split()).strip()
    return hashlib.sha256(norm.encode("utf-8", errors="ignore")).hexdigest()


def classify_pdf_document_type(title: str, text: str, url: str = "") -> str:
    """Classify technical document into standard PDFDocumentType categories."""
    haystack = f"{title} {url} {text[:3000]}".lower()

    if any(k in haystack for k in ["magazine", "issue", "smt today", "emsnow", "circuits assembly", "pcb007"]):
        return PDFDocumentType.MAGAZINE
    if any(k in haystack for k in ["interview", "q&a", "conversation with"]):
        return PDFDocumentType.INTERVIEW
    if any(k in haystack for k in ["datasheet", "data sheet", "spec sheet", "specification sheet", "technical specifications"]):
        return PDFDocumentType.DATASHEET
    if any(k in haystack for k in ["application note", "app note", "technical note", "application brief"]):
        return PDFDocumentType.APPLICATION_NOTE
    if any(k in haystack for k in ["white paper", "whitepaper", "technical paper"]):
        return PDFDocumentType.WHITE_PAPER
    if any(k in haystack for k in ["case study", "success story", "customer story"]):
        return PDFDocumentType.CASE_STUDY
    if any(k in haystack for k in ["user manual", "operator manual", "service manual", "instruction manual", "operation manual"]):
        return PDFDocumentType.MANUAL
    if any(k in haystack for k in ["catalog", "product catalog", "lineup", "selection guide"]):
        return PDFDocumentType.CATALOG
    if any(k in haystack for k in ["brochure", "product brief", "solution brief", "product overview"]):
        return PDFDocumentType.BROCHURE
    if any(k in haystack for k in ["specification", "technical spec"]):
        return PDFDocumentType.SPECIFICATION
    if any(k in haystack for k in ["presentation", "slides", "conference paper", "proceedings"]):
        return PDFDocumentType.PRESENTATION
    return PDFDocumentType.TECHNICAL_DOCUMENT


def parse_pdf_date(date_str: str, text_fallback: Optional[str] = None) -> Optional[str]:
    """Parse PDF dictionary date format or ISO date strings into YYYY-MM-DD."""
    if not date_str and not text_fallback:
        return None
    s = (date_str or "").strip()

    # PDF spec metadata format: D:YYYYMMDDHHmmSS or D:YYYYMMDD
    m = re.search(r"D:?(\d{4})(\d{2})(\d{2})", s)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
            return dt.date().isoformat()
        except ValueError:
            pass

    # ISO format YYYY-MM-DD
    m = re.search(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b", s)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
            return dt.date().isoformat()
        except ValueError:
            pass

    # Human-readable month DD, YYYY
    months = {
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
        "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
        "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
    }
    target_str = (s or text_fallback or "")
    m = re.search(
        r"\b(" + "|".join(months.keys()) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+(20\d{2})\b",
        target_str.lower(),
        re.IGNORECASE,
    )
    if m:
        try:
            month_num = months[m.group(1).lower().rstrip(".")]
            dt = datetime(int(m.group(3)), month_num, int(m.group(2)), tzinfo=timezone.utc)
            return dt.date().isoformat()
        except ValueError:
            pass

    return None


def identify_company_and_products(text: str, title: str, url: str, metadata: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    """Identify SMT company name, product names, and technologies from content."""
    haystack = f"{title} {url} {' '.join(str(v) for v in metadata.values())} {text[:2500]}".lower()

    company = ""
    for vendor in _KNOWN_VENDORS:
        if vendor.lower() in haystack:
            company = vendor
            break

    technologies: list[str] = []
    for tech in _KNOWN_TECHNOLOGIES:
        if re.search(r"\b" + re.escape(tech.lower()) + r"\b", haystack, re.I):
            if tech not in technologies:
                technologies.append(tech)

    products: list[str] = []
    # Extract uppercase/alphanumeric equipment model identifiers (e.g. TR7600, Alpha 3D, KY-P3)
    model_patterns = [
        r"\b([A-Z]{2,4}[-_]?\d{4,5}[A-Z0-9-]*)\b",  # TR7600, S3088, V810i
        r"\b((?:Alpha|Zenith|Eagleyes|Neptune|Meister|Paragon|Sige|Challenger)\s*(?:3D|II|III|IV|Pro|Plus|SV|HS)?)\b",
        r"\b([A-Z]{2,3}[-_][PVMX]\d{1,4}[A-Z0-9-]*)\b",  # KY-P3, VP6000
    ]
    for pattern in model_patterns:
        matches = re.findall(pattern, f"{title} {text[:1500]}")
        for m in matches:
            clean = str(m).strip()
            if len(clean) >= 3 and clean not in products and clean.upper() not in {"SMT", "PCB", "AOI", "SPI", "AXI", "MES", "CFX", "IPC", "EMS"}:
                products.append(clean)

    return company, products[:5], technologies[:8]


def extract_technical_facts(text: str, source_url: str = "", title: str = "") -> list[dict[str, Any]]:
    """
    Extract concrete, verifiable engineering specifications from document text.
    Never fabricates a specification; all extracted facts preserve provenance.
    """
    facts: list[TechnicalFact] = []
    seen_pairs: set[tuple[str, str]] = set()

    doc_label = title or "Technical Document"
    if len(doc_label) > 45:
        doc_label = doc_label[:42] + "..."
    prov = f"Extracted from PDF: {doc_label} ({source_url or 'local'})"

    def _add_fact(parameter: str, value: str, context: str) -> None:
        val_clean = " ".join(value.split()).strip()
        ctx_clean = " ".join(context.split()).strip()
        pair = (parameter.lower(), val_clean.lower())
        if not val_clean or pair in seen_pairs:
            return
        seen_pairs.add(pair)
        facts.append(
            TechnicalFact(
                parameter=parameter,
                value=val_clean,
                raw_context=ctx_clean[:180],
                source_url=source_url,
                provenance=prov,
            )
        )

    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # 1. Regex-based specification extraction across sentences/lines
    for line in lines:
        # Throughput / Speed
        m = re.search(
            r"(?i)\b(\d+(?:[\.,]\d+)?\s*(?:cph|components/hour|cm[²2]/s(?:ec)?|sq\s*cm/s(?:ec)?|mm/s(?:ec)?|boards/hour|sec/board|ms/component))\b",
            line,
        )
        if m:
            _add_fact("throughput", m.group(1), line)

        # Resolution / Accuracy / Repeatability (metric & mils)
        m = re.search(
            r"(?i)\b(\d+(?:[\.,]\d+)?\s*(?:μm|um|micron|microns|nm|mil|mils|mm)\s*(?:resolution|accuracy|repeatability|height\s*accuracy|xy\s*accuracy)?)\b",
            line,
        )
        if m:
            # Check if it specifies resolution or accuracy
            param = "resolution"
            if "accuracy" in line.lower() or "repeatability" in line.lower():
                param = "accuracy"
            _add_fact(param, m.group(1), line)

        # Defect detection accuracy / FPY / False call rate (%)
        for m in re.finditer(
            r"(?i)\b(\d+(?:[\.,]\d+)?\s*%)\s*([^.\n\r]{1,40})",
            line,
        ):
            val_str = m.group(1).strip()
            after_str = m.group(2).lower()
            before_idx = max(0, m.start() - 30)
            before_str = line[before_idx:m.start()].lower()
            window = f"{before_str} {val_str} {after_str}"
            if any(k in window for k in ["accuracy", "defect detection", "fpy", "first pass yield", "yield", "false call", "escape rate", "uptime"]):
                param = "accuracy"
                if "yield" in window or "fpy" in window:
                    param = "first_pass_yield"
                elif "false call" in window:
                    param = "false_call_rate"
                _add_fact(param, val_str, line)

        # Supported component sizes (e.g. 01005, 0201, 008004, WLP, BGA)
        m = re.search(
            r"(?i)\b((?:01005|0201|0402|0603|0805|1206|008004|wlp|bga|qfp|csp|flip\s*chip)\s*[-to–]+\s*(?:01005|0201|0402|0603|0805|1206|wlp|bga|qfp|csp|flip\s*chip|45x45\s*mm|100x100\s*mm|[0-9\.\s]+mm))\b",
            line,
        )
        if m:
            _add_fact("supported_components", m.group(1), line)

        # Board / PCB dimensions / FOV
        m = re.search(
            r"(?i)\b((?:pcb|board|max\s*board|fov|field\s*of\s*view)\s*(?:size|dimensions|area)?)\s*[:=]\s*([^.\n\r]{3,50})",
            line,
        )
        if m:
            _add_fact(m.group(1).lower().replace(" ", "_"), m.group(2).strip(), line)

    # 2. Key-value table/specification format (e.g. "Throughput : 12,000 CPH", "Resolution - 0.5 micron")
    for line in lines:
        if ":" in line or " - " in line:
            parts = re.split(r":|\s-\s", line, maxsplit=1)
            if len(parts) == 2:
                param_raw, val_raw = parts[0].strip(), parts[1].strip()
                if 2 <= len(param_raw) <= 30 and 1 <= len(val_raw) <= 45:
                    low_p = param_raw.lower()
                    if any(kw in low_p for kw in [
                        "throughput", "speed", "accuracy", "resolution", "repeatability",
                        "dimensions", "size", "weight", "power", "fov", "magnification",
                        "inspection speed", "false calls", "defects", "min component",
                        "camera", "laser", "light source", "warpage",
                    ]):
                        _add_fact(low_p.replace(" ", "_"), val_raw, line)

    return [f.to_dict() for f in facts]


def parse_pdf_bytes(
    content: bytes,
    source_url: str = "",
    default_title: str = "",
) -> Optional[PDFDocument]:
    """
    Parse raw PDF binary data into a structured PDFDocument.
    Works deterministically in unit tests without requiring network access.
    """
    if not content:
        return None

    file_hash = hash_bytes(content)
    text_content = ""
    page_count = 1
    metadata: dict[str, Any] = {}
    pub_date: Optional[str] = None

    if _PYPDF_AVAILABLE:
        try:
            reader = pypdf.PdfReader(io.BytesIO(content))
            page_count = len(reader.pages)
            meta = reader.metadata or {}
            for k, v in meta.items():
                if v:
                    clean_k = str(k).lstrip("/")
                    metadata[clean_k] = str(v)

            pages_text: list[str] = []
            for page in reader.pages[:30]:
                try:
                    pt = page.extract_text()
                    if pt:
                        pages_text.append(pt)
                except Exception:
                    continue
            text_content = "\n".join(pages_text)
        except Exception as e:
            # Fallback for malformed or ASCII-stream mock PDFs in unit tests
            text_content = _fallback_ascii_extract(content)
    else:
        text_content = _fallback_ascii_extract(content)

    text_content = re.sub(r"\r\n|\r", "\n", text_content)
    text_content = re.sub(r"[ \t]+", " ", text_content).strip()
    if not text_content and metadata.get("Title"):
        text_content = f"{metadata['Title']}. Technical specification document."
    text_hash = hash_text(text_content)

    # Determine title
    title = ""
    if metadata.get("Title"):
        title_meta = str(metadata["Title"]).strip()
        if len(title_meta) >= 6 and not title_meta.lower().endswith((".pdf", ".doc", ".docx")):
            title = title_meta
    if not title:
        for line in text_content.splitlines():
            line_clean = line.strip()
            if len(line_clean) >= 8 and len(line_clean) <= 120 and not line_clean.startswith(("http", "www.")):
                title = line_clean
                break
    if not title:
        title = default_title or (
            urllib.parse.unquote(source_url.split("/")[-1]).replace(".pdf", "").replace("-", " ").replace("_", " ").title()
            if source_url else "SMT Technical Document"
        )

    # Determine date
    meta_date_raw = metadata.get("CreationDate") or metadata.get("ModDate") or ""
    pub_date = parse_pdf_date(meta_date_raw, text_fallback=text_content[:2000])

    # Classify document and extract engineering specifications
    doc_type = classify_pdf_document_type(title, text_content, source_url)
    company, products, technologies = identify_company_and_products(text_content, title, source_url, metadata)
    key_facts = extract_technical_facts(text_content, source_url, title)

    return PDFDocument(
        title=title,
        document_type=doc_type,
        company=company,
        products=products,
        technologies=technologies,
        publication_date=pub_date,
        document_date=pub_date,
        language="en",
        page_count=page_count,
        text=text_content,
        metadata=metadata,
        source_url=source_url,
        file_hash=file_hash,
        text_hash=text_hash,
        key_facts=key_facts,
    )


def _fallback_ascii_extract(content: bytes) -> str:
    """Best-effort printable ASCII/UTF-8 extraction for PDF test stubs or when pypdf fails."""
    try:
        raw_str = content.decode("utf-8", errors="ignore")
        # Strip out PDF syntax operators
        lines = []
        for line in raw_str.splitlines():
            clean = line.strip()
            if clean and not re.match(r"^(\d+\s+\d+\s+obj|endobj|stream|endstream|xref|trailer|%PDF-)", clean):
                lines.append(clean)
        return "\n".join(lines)
    except Exception:
        return ""


def fetch_and_parse_pdf(url: str, timeout: int = 15, max_pages: int = 30) -> Optional[PDFDocument]:
    """Fetch a PDF document over HTTP and parse it into a normalized PDFDocument."""
    if not url:
        return None
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; SMTInsiderBot/1.0; +https://smtinsider.com/bot)",
        "Accept": "application/pdf,*/*",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        content_type = (resp.headers.get("Content-Type") or "").lower()
        if "html" in content_type and not url.lower().endswith(".pdf"):
            return None
        return parse_pdf_bytes(resp.content, source_url=resp.url)
    except Exception as e:
        return None


def discover_pdf_links_on_page(
    page_url: str,
    html_content: Optional[str] = None,
    timeout: int = 15,
    max_links: int = 15,
) -> list[dict[str, str]]:
    """
    Discover links to PDF datasheets, brochures, and technical documents on a webpage.
    Filters out irrelevant/legal PDFs (privacy policies, terms of service, tax certificates).
    """
    html_text = html_content
    if html_text is None:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; SMTInsiderBot/1.0)"}
        try:
            resp = requests.get(page_url, headers=headers, timeout=timeout, allow_redirects=True)
            resp.raise_for_status()
            html_text = resp.text
            page_url = resp.url
        except Exception:
            return []

    results: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    try:
        soup = BeautifulSoup(html_text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = str(a["href"]).strip()
            title = " ".join(a.get_text(" ", strip=True).split())
            if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue

            abs_url = urllib.parse.urljoin(page_url, href)
            path_lower = urllib.parse.urlparse(abs_url).path.lower()
            text_lower = f"{title} {a.get('title', '')} {a.get('type', '')}".lower()

            is_pdf = path_lower.endswith(".pdf") or "pdf" in text_lower or ".pdf?" in abs_url.lower()
            if not is_pdf:
                continue

            # Reject legal/privacy/terms PDFs
            if any(p in abs_url.lower() or p in text_lower for p in _EXCLUDED_PDF_PATTERNS):
                continue

            if abs_url in seen_urls:
                continue
            seen_urls.add(abs_url)

            parent_block = a.parent.get_text(" ", strip=True) if a.parent else title
            results.append({
                "url": abs_url,
                "title": title or urllib.parse.unquote(abs_url.split("/")[-1]).replace(".pdf", ""),
                "context": parent_block[:300],
                "discovered_from": page_url,
            })
            if len(results) >= max_links:
                break
    except Exception:
        pass

    return results
