"""
SMTInsider Collectors Module.

Contains specialized collectors for technical data, PDF datasheets,
brochures, application notes, and vendor specifications.
"""

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


from src.collectors.pdf_collector import (
    PDFDocument,
    PDFDocumentType,
    TechnicalFact,
    classify_pdf_document_type,
    discover_pdf_links_on_page,
    extract_technical_facts,
    fetch_and_parse_pdf,
    parse_pdf_bytes,
    parse_pdf_date,
)

__all__ = [
    "PDFDocument",
    "PDFDocumentType",
    "TechnicalFact",
    "classify_pdf_document_type",
    "discover_pdf_links_on_page",
    "extract_technical_facts",
    "fetch_and_parse_pdf",
    "parse_pdf_bytes",
    "parse_pdf_date",
]
