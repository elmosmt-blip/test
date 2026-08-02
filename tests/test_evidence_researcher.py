from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILE = ROOT / "agents" / "agent-01d-evidence-researcher.py"


def load_module():
    spec = importlib.util.spec_from_file_location("evidence_researcher_test", FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_routes_single_authoritative_source_to_news(monkeypatch):
    researcher = load_module()
    text = "IPC released a new standard with class coded acceptance criteria for electronic box assemblies. " * 20
    monkeypatch.setattr(researcher.source_expander, "fetch_readable_text", lambda url: text)
    monkeypatch.setattr(researcher.source_expander, "extract_candidate_links", lambda *args, **kwargs: [])
    monkeypatch.setattr(researcher, "_search_official_pages", lambda *args, **kwargs: [])
    topic = {"topic": "IPC-A-630A Released", "sources": [{"url": "https://www.ipc.org/news", "role": "fresh_primary"}]}

    result = researcher.research_topic(topic)

    assert result["writer_allowed"] is True
    assert result["format"] == "news"
    assert result["evidence_status"] == "ready_news"


def test_discards_insufficient_evidence(monkeypatch):
    researcher = load_module()
    monkeypatch.setattr(researcher.source_expander, "fetch_readable_text", lambda url: "Too short")
    monkeypatch.setattr(researcher.source_expander, "extract_candidate_links", lambda *args, **kwargs: [])
    monkeypatch.setattr(researcher, "_search_official_pages", lambda *args, **kwargs: [])
    result = researcher.research_topic({"topic": "Weak signal", "sources": [{"url": "https://example.com", "role": "fresh_primary"}]})
    assert result["writer_allowed"] is False
    assert result["evidence_status"] == "discarded_insufficient_evidence"
