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


def test_uses_only_corroborated_linkedin_official_url(tmp_path, monkeypatch):
    researcher = load_module()
    signals = tmp_path / "linkedin_signals.json"
    signals.write_text('{"signals":[{"matched_topic":"Dymax 9310 launch","writer_allowed":true,"official_source":{"url":"https://www.dymax.com/news/9310"}}]}', encoding="utf-8")
    monkeypatch.setattr(researcher, "LINKEDIN_SIGNALS_FILE", signals)
    urls = researcher._linkedin_official_urls("Dymax 9310 Adhesive Launch")
    assert urls == ["https://www.dymax.com/news/9310"]


def test_expired_event_stays_blocked_even_with_source_text(monkeypatch):
    researcher = load_module()
    text = "Kurtz Ersa will exhibit at an expo on July 16, 2026 with HOTFLOW THREE. " * 30
    monkeypatch.setattr(researcher.source_expander, "fetch_readable_text", lambda url: text)
    monkeypatch.setattr(researcher.source_expander, "extract_candidate_links", lambda *args, **kwargs: [])
    monkeypatch.setattr(researcher, "_search_official_pages", lambda *args, **kwargs: [])
    result = researcher.research_topic({"topic": "Kurtz Ersa at expo", "evidence_status": "event_expired", "sources": [{"url": "https://ersa.com", "role": "fresh_primary"}]})
    assert result["writer_allowed"] is False
    assert result["evidence_status"] == "awaiting_post_event_evidence"


def test_routes_long_single_source_to_insight(monkeypatch):
    researcher = load_module()
    text = "Europlacer describes process integrity, production integrity and first board economics in high-mix manufacturing. " * 90
    monkeypatch.setattr(researcher.source_expander, "fetch_readable_text", lambda url: text)
    monkeypatch.setattr(researcher.source_expander, "extract_candidate_links", lambda *args, **kwargs: [])
    monkeypatch.setattr(researcher, "_search_official_pages", lambda *args, **kwargs: [])
    result = researcher.research_topic({"topic": "Europlacer First Board Right", "sources": [{"url": "https://europlacer.com/first-board-right", "role": "fresh_primary"}]})
    assert result["writer_allowed"] is True
    assert result["format"] == "insight"


def test_discards_insufficient_evidence(monkeypatch):
    researcher = load_module()
    monkeypatch.setattr(researcher.source_expander, "fetch_readable_text", lambda url: "Too short")
    monkeypatch.setattr(researcher.source_expander, "extract_candidate_links", lambda *args, **kwargs: [])
    monkeypatch.setattr(researcher, "_search_official_pages", lambda *args, **kwargs: [])
    result = researcher.research_topic({"topic": "Weak signal", "sources": [{"url": "https://example.com", "role": "fresh_primary"}]})
    assert result["writer_allowed"] is False
    assert result["evidence_status"] == "discarded_insufficient_evidence"
