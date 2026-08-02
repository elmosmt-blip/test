from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILE = ROOT / "agents" / "agent-01c-linkedin-signals.py"


def load_module():
    spec = importlib.util.spec_from_file_location("linkedin_scout_test", FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_topic_queries_use_public_linkedin_post_paths():
    scout = load_module()
    queries = scout.topic_queries("Dymax 9310 Adhesive Launch")
    assert queries[0].startswith("site:linkedin.com/posts")
    assert queries[1].startswith("site:linkedin.com/feed/update")


def test_signal_is_not_writer_eligible_without_corroboration():
    scout = load_module()
    signal = scout.classify_signal(
        {"url": "https://www.linkedin.com/posts/example", "title": "Dymax 9310", "snippet": "Dymax release"},
        "Dymax 9310 Adhesive Launch",
    )
    assert signal["trust_level"] == "named_company"
    assert signal["writer_allowed"] is False
    assert signal["status"] == "needs_corroboration"


def test_discovery_attaches_matching_official_result(monkeypatch):
    scout = load_module()
    def fake_search(query, limit=8, linkedin_only=True):
        if linkedin_only:
            return [{"url": "https://www.linkedin.com/posts/dymax", "title": "Dymax 9310", "snippet": "Dymax release"}]
        return [{"url": "https://www.dymax.com/news/9310", "title": "Dymax 9310 release", "snippet": "Official"}]
    monkeypatch.setattr(scout, "ddg_public_search", fake_search)
    signals = scout.discover("Dymax 9310 Adhesive Launch")
    assert signals[0]["status"] == "corroborated"
    assert signals[0]["writer_allowed"] is True
