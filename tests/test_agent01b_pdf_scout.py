"""
Tests for Manual PDF-to-Article Pipeline Scout (`agents/agent-01b-pdf-scout.py`).
"""

import importlib.util
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.collectors.pdf_collector import PDFDocument, PDFDocumentType

REPO_ROOT = Path(__file__).resolve().parent.parent
SCOUT_FILE = REPO_ROOT / "agents" / "agent-01b-pdf-scout.py"


def _load_scout_module():
    spec = importlib.util.spec_from_file_location("agent01b_pdf_scout", SCOUT_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def pdf_scout():
    return _load_scout_module()


class TestPDFScout:
    def test_build_pdf_topic_brief_minimal(self, pdf_scout):
        doc = PDFDocument(
            title="Koh Young Alpha 3D SPI Catalog 2026",
            document_type=PDFDocumentType.CATALOG,
            company="Koh Young",
            products=["Alpha 3D"],
            technologies=["3D SPI", "SPI"],
            publication_date="2026-07-28",
            page_count=2,
            text="Koh Young Alpha 3D SPI Catalog 2026 with 0.5 micron resolution and 12,000 cph.",
            source_url="https://online.fliphtml5.com/kwnhb/fakj/",
            key_facts=[
                {
                    "parameter": "resolution",
                    "value": "0.5 micron",
                    "raw_context": "0.5 micron resolution",
                    "source_url": "https://online.fliphtml5.com/kwnhb/fakj/",
                    "provenance": "Extracted from catalog",
                }
            ],
        )

        brief_payload = pdf_scout.build_pdf_topic_brief(
            doc=doc,
            source_url="https://online.fliphtml5.com/kwnhb/fakj/",
            category="SMT Equipment",
            format_type="review",
            editorial_type="review",
        )

        assert brief_payload["source_type"] == "manual_pdf"
        assert brief_payload["pdf_metadata"]["title"] == "Koh Young Alpha 3D SPI Catalog 2026"
        assert brief_payload["pdf_metadata"]["official_url"] == "https://online.fliphtml5.com/kwnhb/fakj/"

        topic = brief_payload["topics"][0]
        assert "Koh Young" in topic["topic"]
        assert topic["editorial_type"] == "review"
        assert len(topic["sources"]) == 1
        assert topic["sources"][0]["url"] == "https://online.fliphtml5.com/kwnhb/fakj/"
        assert "resolution: 0.5 micron" in topic["key_facts"][0]

    def test_build_pdf_topic_brief_magazine_issue(self, pdf_scout):
        text = (
            "SMT Today Magazine Issue 80.\n"
            "Section 1: Fuji Corporation introduces new placement architecture. Throughput: 45,000 CPH.\n"
            "Section 2: Koh Young Technology discusses 3D SPI and AOI inspection. Resolution: 0.5 micron.\n"
            "Section 3: Mirtec showcases 3D AOI systems for automotive lines. Inspection Speed: 120 sq cm/s.\n"
        )
        doc = PDFDocument(
            title="SMT Today Magazine Issue 80",
            document_type=PDFDocumentType.MAGAZINE,
            company="SMT Today",
            products=["Fuji Placement", "Alpha 3D", "Mirtec AOI"],
            technologies=["Placement", "3D SPI", "3D AOI"],
            publication_date="2026-07-31",
            page_count=68,
            text=text,
            source_url="https://online.fliphtml5.com/kwnhb/fakj/",
            key_facts=[
                {
                    "parameter": "throughput",
                    "value": "45,000 CPH",
                    "raw_context": "Throughput: 45,000 CPH",
                    "source_url": "https://online.fliphtml5.com/kwnhb/fakj/",
                    "provenance": "Extracted from magazine",
                }
            ],
        )

        brief_payload = pdf_scout.build_pdf_topic_brief(
            doc=doc,
            source_url="https://online.fliphtml5.com/kwnhb/fakj/",
            max_topics=3,
        )

        assert brief_payload["source_type"] == "manual_pdf"
        assert len(brief_payload["topics"]) == 3
        topic_titles = [t["topic"] for t in brief_payload["topics"]]
        assert any("Koh Young" in t for t in topic_titles)
        assert any("Mirtec" in t for t in topic_titles)
        assert any("Fuji" in t for t in topic_titles)

    def test_load_pdf_input_from_file_path(self, pdf_scout, tmp_path):
        # Create a mock PDF file
        pdf_file = tmp_path / "catalog.pdf"
        try:
            import pypdf
            writer = pypdf.PdfWriter()
            writer.add_metadata({
                "/Title": "Koh Young Alpha 3D SPI Catalog 2026",
                "/CreationDate": "D:20260728120000Z",
            })
            writer.add_blank_page(612, 792)
            out = io.BytesIO()
            writer.write(out)
            pdf_file.write_bytes(out.getvalue())
        except ImportError:
            pdf_file.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Title (Koh Young Alpha 3D SPI Catalog 2026) >>\nendobj\n%%EOF\n")

        doc = pdf_scout.load_pdf_input(
            file_path=str(pdf_file),
            source_url="https://online.fliphtml5.com/kwnhb/fakj/",
        )
        assert doc is not None
        assert doc.title == "Koh Young Alpha 3D SPI Catalog 2026"
        assert doc.company == "Koh Young"

    def test_cli_requires_file_or_url(self, pdf_scout, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["agent-01b-pdf-scout.py"])
        with pytest.raises(SystemExit):
            pdf_scout.main()
        err = capsys.readouterr().err
        assert "Укажи --file /path/to/file.pdf или --url https://" in err
