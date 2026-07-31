"""
SMTInsider Collectors Module.

Contains specialized collectors for technical data, PDF datasheets,
brochures, application notes, and vendor specifications.
"""

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
