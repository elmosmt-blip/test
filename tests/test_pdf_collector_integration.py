"""
Integration tests for SMTInsider PDF Technical Document Collector
(`src/collectors/pdf_collector.py`) inside `agents/agent-01-trend-hunter.py`.
"""

import importlib.util
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from src.collectors import pdf_collector
from src.collectors.pdf_collector import PDFDocumentType

try:
    import pypdf
    _PYPDF_AVAILABLE = True
except ImportError:
    _PYPDF_AVAILABLE = False

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_FILE = REPO_ROOT / "agents" / "agent-01-trend-hunter.py"


def _load_agent_module():
    spec = importlib.util.spec_from_file_location("agent01_pdf_test", AGENT_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def agent01(monkeypatch):
    monkeypatch.setenv("NEWS_PDF_COLLECTOR_ENABLED", "1")
    monkeypatch.setenv("NEWS_PDF_ENRICH_ENABLED", "1")
    monkeypatch.setenv("NEWS_TIMEZONE", "UTC")
    return _load_agent_module()


class TestPDFCollectorIntegration:
    def test_gather_pdf_signals_disabled_by_env(self, monkeypatch):
        monkeypatch.setenv("NEWS_PDF_COLLECTOR_ENABLED", "0")
        mod = _load_agent_module()
        res = mod.gather_pdf_signals()
        assert res == []

    @pytest.mark.skipif(not _PYPDF_AVAILABLE, reason="pypdf not installed")
    def test_gather_pdf_signals_finds_and_parses_pdf(self, agent01, monkeypatch):
        monkeypatch.setattr(
            agent01,
            "configured_vendor_sources",
            lambda: [("Koh Young", "https://kohyoung.com/news/", "inspection")]
        )

        def fake_discover(url, timeout=12, max_links=8):
            return [{
                "url": "https://kohyoung.com/datasheets/alpha-3d-spi.pdf",
                "title": "Alpha 3D SPI Datasheet",
                "context": "Download Alpha 3D SPI datasheet with 0.5 micron resolution",
                "discovered_from": url,
            }]
        monkeypatch.setattr(pdf_collector, "discover_pdf_links_on_page", fake_discover)

        writer = pypdf.PdfWriter()
        writer.add_metadata({
            "/Title": "Alpha 3D SPI Datasheet",
            "/CreationDate": f"D:{datetime.now(timezone.utc).strftime('%Y%m%d120000')}Z",
        })
        out = io.BytesIO()
        writer.write(out)
        pdf_bytes = out.getvalue()

        def fake_fetch_and_parse(url, timeout=15, max_pages=30):
            return pdf_collector.parse_pdf_bytes(
                pdf_bytes,
                source_url=url,
                default_title="Alpha 3D SPI Datasheet",
            )
        monkeypatch.setattr(pdf_collector, "fetch_and_parse_pdf", fake_fetch_and_parse)

        signals = agent01.gather_pdf_signals(lookback_days=30, max_items_per_vendor=2)
        assert len(signals) == 1
        sig = signals[0]
        assert sig["title"] == "Alpha 3D SPI Datasheet"
        assert sig["feed"] == "Koh Young"
        assert sig["vendor_group"] == "inspection"
        assert sig["document_type"] == PDFDocumentType.DATASHEET
        assert sig["date_verified"] is True

    @pytest.mark.skipif(not _PYPDF_AVAILABLE, reason="pypdf not installed")
    def test_gather_vendor_signals_handles_direct_pdf_link(self, agent01, monkeypatch):
        monkeypatch.setattr(
            agent01,
            "configured_vendor_sources",
            lambda: [("Koh Young", "https://kohyoung.com/news/", "inspection")]
        )

        html = """
        <html>
        <body>
          <a href="https://kohyoung.com/alpha-3d.pdf">Koh Young Alpha 3D SPI Datasheet 2026</a>
        </body>
        </html>
        """
        class FakeResp:
            def __init__(self, text, url):
                self.text = text
                self.url = url
            def raise_for_status(self):
                pass

        monkeypatch.setattr(agent01.requests, "get", lambda url, **kwargs: FakeResp(html, url))

        writer = pypdf.PdfWriter()
        writer.add_metadata({
            "/Title": "Koh Young Alpha 3D SPI Datasheet 2026",
            "/CreationDate": f"D:{datetime.now(timezone.utc).strftime('%Y%m%d120000')}Z",
        })
        out = io.BytesIO()
        writer.write(out)
        pdf_bytes = out.getvalue()

        def fake_fetch_and_parse(url, timeout=15, max_pages=30):
            return pdf_collector.parse_pdf_bytes(
                pdf_bytes,
                source_url=url,
                default_title="Koh Young Alpha 3D SPI Datasheet 2026",
            )
        monkeypatch.setattr(pdf_collector, "fetch_and_parse_pdf", fake_fetch_and_parse)

        signals = agent01.gather_vendor_signals(lookback_days=30, strict_fresh=True)
        assert len(signals) == 1
        sig = signals[0]
        assert sig["title"] == "Koh Young Alpha 3D SPI Datasheet 2026"
        assert sig["document_type"] == PDFDocumentType.DATASHEET
        assert sig["date_verified"] is True

    def test_build_briefs_aggregates_key_facts_from_pdf_sources(self, agent01, monkeypatch):
        signals = [{
            "title": "Alpha 3D SPI Datasheet",
            "snippet": "Alpha 3D SPI specifications and performance",
            "source": "https://kohyoung.com/alpha.pdf",
            "feed": "Koh Young",
            "published_at": "2026-07-28",
            "key_facts": ["resolution: 0.5 micron", "throughput: 12,000 cph"],
            "technical_specs": [
                {"parameter": "resolution", "value": "0.5 micron"},
                {"parameter": "throughput", "value": "12,000 cph"},
            ],
            "_editorial_score": 30,
        }]

        def fake_ask_json(sys_p, usr_p, max_tokens=2800):
            return {
                "topics": [{
                    "topic": "Alpha 3D SPI Datasheet",
                    "angle": "Engineering review of Koh Young SPI specs.",
                    "format": "review",
                    "category": "Inspection",
                    "keywords": ["SPI", "Koh Young"],
                    "key_facts": [],
                    "sources": [],
                }]
            }
        monkeypatch.setattr(agent01.llm_client, "ask_json", fake_ask_json)
        monkeypatch.setattr(agent01, "find_corroborating_sources", lambda *args, **kwargs: [])

        briefs_data = agent01.build_briefs(signals, max_topics=1, lookback_days=30)
        topic = briefs_data["topics"][0]
        assert "resolution: 0.5 micron" in topic["key_facts"]
        assert "throughput: 12,000 cph" in topic["key_facts"]
