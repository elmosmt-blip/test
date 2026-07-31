"""
Unit tests for SMTInsider PDF & Technical Document Collector (`src/collectors/pdf_collector.py`).

Tests document classification, technical fact/spec extraction, PDF byte
parsing (using pypdf), date parsing, SHA-256 hashing, PDF link discovery
from HTML, and signal generation.
"""

import io
from datetime import datetime, timezone
from typing import Any

import pytest

from src.collectors.pdf_collector import (
    PDFDocument,
    PDFDocumentType,
    TechnicalFact,
    classify_pdf_document_type,
    discover_pdf_links_on_page,
    extract_technical_facts,
    hash_bytes,
    hash_text,
    identify_company_and_products,
    parse_pdf_bytes,
    parse_pdf_date,
)

try:
    import pypdf
    _PYPDF_AVAILABLE = True
except ImportError:
    _PYPDF_AVAILABLE = False


class TestPDFDocumentClassification:
    def test_classify_datasheet(self):
        doc_type = classify_pdf_document_type(
            "Koh Young Alpha 3D SPI Datasheet",
            "This technical specification sheet covers the Alpha 3D SPI series.",
            "https://example.com/alpha-3d-datasheet.pdf",
        )
        assert doc_type == PDFDocumentType.DATASHEET

    def test_classify_application_note(self):
        doc_type = classify_pdf_document_type(
            "Application Note: AOI False-Call Reduction",
            "In this application brief we describe false call mitigation techniques.",
            "https://example.com/app-note-aoi.pdf",
        )
        assert doc_type == PDFDocumentType.APPLICATION_NOTE

    def test_classify_brochure(self):
        doc_type = classify_pdf_document_type(
            "ASMPT SE Ultra Product Brief",
            "An executive product overview of our inline SMT printer.",
            "https://example.com/se-ultra-brochure.pdf",
        )
        assert doc_type == PDFDocumentType.BROCHURE

    def test_classify_white_paper(self):
        doc_type = classify_pdf_document_type(
            "White Paper: CFX Standard Implementation in SMT",
            "Technical paper describing IPC-CFX telemetry in Industry 4.0 lines.",
            "https://example.com/cfx-whitepaper.pdf",
        )
        assert doc_type == PDFDocumentType.WHITE_PAPER

    def test_classify_manual(self):
        doc_type = classify_pdf_document_type(
            "TR7600 Operator Manual",
            "Service and operation manual for TRI 3D AXI system.",
            "https://example.com/tr7600-manual.pdf",
        )
        assert doc_type == PDFDocumentType.MANUAL

    def test_classify_case_study(self):
        doc_type = classify_pdf_document_type(
            "Customer Story: 30% Higher FPY with 3D SPI",
            "A case study at an EMS provider deploying Koh Young SPI.",
            "https://example.com/ems-case-study.pdf",
        )
        assert doc_type == PDFDocumentType.CASE_STUDY

    def test_classify_magazine(self):
        doc_type = classify_pdf_document_type(
            "SMTMag-Issue 80-DIGI",
            "The 80th Issue of SMT Today, featuring Fuji Corporation, Koh Young Technology, Mirtec.",
            "https://online.fliphtml5.com/kwnhb/fakj/",
        )
        assert doc_type == PDFDocumentType.MAGAZINE

    def test_classify_default(self):
        doc_type = classify_pdf_document_type(
            "SMT Assembly Process Report",
            "General technical discussion of solder paste printing.",
            "https://example.com/report.pdf",
        )
        assert doc_type == PDFDocumentType.TECHNICAL_DOCUMENT


class TestTechnicalFactExtraction:
    def test_extract_throughput_and_resolution(self):
        text = (
            "The new ASMPT inline AOI system achieves throughput of 12,000 CPH with "
            "height accuracy of 0.5 micron. The inspection speed reaches 120 sq cm/s."
        )
        facts = extract_technical_facts(
            text,
            source_url="https://example.com/aoi-specs.pdf",
            title="AOI Specs 2026",
        )
        params = {f["parameter"]: f["value"] for f in facts}
        assert "throughput" in params
        assert "12,000 cph" in params["throughput"].lower()
        assert "accuracy" in params
        assert "0.5 micron" in params["accuracy"].lower()
        for f in facts:
            assert "AOI Specs 2026" in f["provenance"]
            assert "https://example.com/aoi-specs.pdf" in f["provenance"]

    def test_extract_component_size_and_board_size(self):
        text = (
            "Supported component sizes range from 01005 to 45x45 mm BGA packages. "
            "Max Board Size : 510 x 510 mm."
        )
        facts = extract_technical_facts(
            text,
            source_url="https://example.com/spi-specs.pdf",
            title="SPI Datasheet",
        )
        params = {f["parameter"]: f["value"] for f in facts}
        assert "supported_components" in params
        assert "01005 to 45x45 mm" in params["supported_components"].lower()
        assert any("510 x 510" in str(v) for v in params.values())

    def test_extract_defect_accuracy_and_fpy(self):
        text = (
            "System provides 99.8% defect detection accuracy while maintaining "
            "first pass yield of 99.5% across automotive PCBA lines."
        )
        facts = extract_technical_facts(
            text,
            source_url="https://example.com/axi-specs.pdf",
            title="AXI Datasheet",
        )
        params = {f["parameter"]: f["value"] for f in facts}
        assert "accuracy" in params
        assert "99.8%" in params["accuracy"]
        assert "first_pass_yield" in params
        assert "99.5%" in params["first_pass_yield"]

    def test_no_fabrication_on_clean_marketing_text(self):
        text = "Our company develops world-class electronics manufacturing solutions."
        facts = extract_technical_facts(text, "https://example.com/about.pdf", "About Us")
        assert len(facts) == 0


class TestCompanyAndProductIdentification:
    def test_identify_known_vendor_and_products(self):
        text = "Koh Young today announced the Alpha 3D SPI system with IPC-CFX support."
        company, products, technologies = identify_company_and_products(
            text, "Alpha 3D Release", "https://kohyoung.com/alpha.pdf", {}
        )
        assert company == "Koh Young"
        assert any("3D SPI" in t or "IPC-CFX" in t for t in technologies)
        assert any("Alpha" in p for p in products)


class TestPDFDateParsing:
    def test_parse_pdf_metadata_date(self):
        parsed = parse_pdf_date("D:20260728103000Z")
        assert parsed == "2026-07-28"

    def test_parse_iso_string_date(self):
        parsed = parse_pdf_date("2026-07-28")
        assert parsed == "2026-07-28"

    def test_parse_human_month_date(self):
        parsed = parse_pdf_date("", text_fallback="Published July 28, 2026 by SMTInsider")
        assert parsed == "2026-07-28"

    def test_parse_none_when_no_date_present(self):
        assert parse_pdf_date("invalid-date-string") is None


class TestPDFHashing:
    def test_hash_bytes(self):
        data = b"SMTInsider test PDF content"
        h = hash_bytes(data)
        assert len(h) == 64
        assert hash_bytes(data) == h

    def test_hash_text_normalizes_whitespace(self):
        t1 = "Koh Young  Alpha   3D SPI "
        t2 = "Koh Young Alpha 3D SPI"
        assert hash_text(t1) == hash_text(t2)


@pytest.mark.skipif(not _PYPDF_AVAILABLE, reason="pypdf not installed")
class TestPDFByteParsingWithPyPDF:
    def test_parse_generated_pypdf_bytes(self):
        writer = pypdf.PdfWriter()
        writer.add_metadata({
            "/Title": "Koh Young Alpha 3D SPI Datasheet",
            "/Author": "Koh Young Technology",
            "/CreationDate": "D:20260728120000Z",
        })
        page = writer.add_blank_page(width=612, height=792)
        # Note: blank page text is empty, but title & date come from metadata
        out = io.BytesIO()
        writer.write(out)
        pdf_bytes = out.getvalue()

        doc = parse_pdf_bytes(
            pdf_bytes,
            source_url="https://kohyoung.com/alpha-3d.pdf",
        )
        assert doc is not None
        assert doc.title == "Koh Young Alpha 3D SPI Datasheet"
        assert doc.company == "Koh Young"
        assert doc.document_type == PDFDocumentType.DATASHEET
        assert doc.publication_date == "2026-07-28"
        assert doc.file_hash == hash_bytes(pdf_bytes)


class TestPDFLinkDiscovery:
    def test_discover_pdf_links_on_page(self):
        html = """
        <html>
        <body>
          <a href="/datasheets/tr7600-axi-2026.pdf">Download TR7600 AXI Datasheet</a>
          <a href="/privacy-policy.pdf">Privacy Policy PDF</a>
          <a href="/contact">Contact Us</a>
        </body>
        </html>
        """
        links = discover_pdf_links_on_page(
            page_url="https://example.com/products/tr7600",
            html_content=html,
        )
        assert len(links) == 1
        assert "tr7600-axi-2026.pdf" in links[0]["url"]
        assert "AXI Datasheet" in links[0]["title"]
        # Ensure privacy-policy.pdf was filtered out
        assert not any("privacy" in l["url"] for l in links)


class TestPDFDocumentToSignal:
    def test_to_signal_produces_trend_hunter_compatible_dict(self):
        facts = [
            TechnicalFact(
                parameter="resolution",
                value="0.5 micron",
                raw_context="Resolution of 0.5 micron.",
                source_url="https://example.com/spi.pdf",
                provenance="Extracted from datasheet: Alpha SPI",
            ).to_dict()
        ]
        doc = PDFDocument(
            title="Alpha 3D SPI Datasheet",
            document_type=PDFDocumentType.DATASHEET,
            company="Koh Young",
            publication_date="2026-07-28",
            page_count=4,
            text="Koh Young Alpha 3D SPI achieves resolution of 0.5 micron across PCB lines.",
            source_url="https://example.com/spi.pdf",
            file_hash="abcdef0123456789",
            key_facts=facts,
        )
        sig = doc.to_signal(vendor_name="Koh Young", vendor_group="inspection")

        assert sig["title"] == "Alpha 3D SPI Datasheet"
        assert "0.5 micron" in sig["snippet"]
        assert "0.5 micron" in sig["full_text"]
        assert sig["source"] == "https://example.com/spi.pdf"
        assert sig["feed"] == "Koh Young"
        assert sig["vendor_group"] == "inspection"
        assert sig["published_at"] == "2026-07-28"
        assert sig["date_verified"] is True
        assert sig["document_type"] == PDFDocumentType.DATASHEET
        assert sig["file_hash"] == "abcdef0123456789"
        assert "resolution: 0.5 micron" in sig["key_facts"][0]
        assert sig["technical_specs"][0]["parameter"] == "resolution"
