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

    def test_reads_fliphtml5_searchable_page_text_layer(self, pdf_scout, monkeypatch):
        class FakeResponse:
            def __init__(self, status_code, text):
                self.status_code = status_code
                self.text = text
            def raise_for_status(self):
                if self.status_code >= 400:
                    raise pdf_scout.requests.RequestException("HTTP error")

        def fake_get(url, **kwargs):
            if "text_position[1].js" in url:
                positions = ",".join('{"w":"Fuji Corporation NXTR placement platform verified production evidence"}' for _ in range(20))
                return FakeResponse(200, f'positionForPages[0]={{"page":1,"positions":[{positions}]}};')
            return FakeResponse(404, "")

        monkeypatch.setattr(pdf_scout.requests, "get", fake_get)
        doc = PDFDocument(title="Viewer shell", document_type=PDFDocumentType.MAGAZINE, company="", source_url="https://online.fliphtml5.com/kwnhb/fakj/")
        recovered, error = pdf_scout.recover_fliphtml5_text_layer(doc.source_url, doc)

        assert error == ""
        assert recovered is not None
        assert "--- PAGE 1 ---" in recovered.text
        assert "Fuji Corporation" in recovered.text

    def test_segments_page_bounded_magazine_article_before_topic_creation(self, pdf_scout, monkeypatch):
        page_text = "Fuji NXTR Placement Breakthrough verified production evidence " * 30
        doc = PDFDocument(
            title="SMT Magazine Issue",
            document_type=PDFDocumentType.MAGAZINE,
            company="",
            text=f"--- PAGE 6 ---\n{page_text}\n--- PAGE 7 ---\n{page_text}",
            source_url="https://online.fliphtml5.com/kwnhb/fakj/",
        )
        monkeypatch.setattr(pdf_scout.llm_client, "LLM_MOCK", False)
        responses = iter([
            {"articles": [{"title": "Fuji NXTR Placement Breakthrough", "company": "Fuji", "start_page": 6, "end_page": 7, "recommended_format": "news"}]},
            {"decision": "accept", "recommended_format": "news", "allow_segmentation": False},
        ])
        monkeypatch.setattr(pdf_scout.llm_client, "ask_json", lambda **kwargs: next(responses))

        topics = pdf_scout._segment_magazine_with_llm(
            doc, doc.source_url, doc.title, "SMT Equipment", "magazine", "review", 3, datetime.now(timezone.utc)
        )

        assert len(topics) == 1
        assert topics[0]["topic"] == "Fuji NXTR Placement Breakthrough"
        assert topics[0]["sources"][0]["page_range"] == [6, 7]
        assert "--- PAGE 6 ---" in topics[0]["sources"][0]["excerpt"]

    def test_rejects_pdf_syntax_as_editorial_evidence(self, pdf_scout):
        doc = PDFDocument(
            title="Unknown PDF",
            document_type=PDFDocumentType.MAGAZINE,
            company="",
            text="<< /Filter /FlateDecode /Length 2277 >>stream\nresolution: 8nM",
        )

        error = pdf_scout.validate_document_for_editorial_use(doc)
        assert error is not None
        assert "служебные PDF-данные" in error

    def test_editorial_gate_requires_a_supported_format(self, pdf_scout, monkeypatch):
        doc = PDFDocument(title="Aton", document_type=PDFDocumentType.BROCHURE, company="Delvitech", text="Aton " * 100)
        monkeypatch.setattr(pdf_scout.llm_client, "LLM_MOCK", False)
        monkeypatch.setattr(pdf_scout.llm_client, "ask_json", lambda **kwargs: {
            "decision": "accept", "recommended_format": "review", "allow_segmentation": False,
        })

        error, gate = pdf_scout.audit_document_evidence_with_llm(doc)
        assert error is None
        assert gate["recommended_format"] == "review"

    def test_cli_requires_file_or_url(self, pdf_scout, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["agent-01b-pdf-scout.py"])
        with pytest.raises(SystemExit):
            pdf_scout.main()
        err = capsys.readouterr().err
        assert "Укажи --file /path/to/file.pdf или --url https://" in err
