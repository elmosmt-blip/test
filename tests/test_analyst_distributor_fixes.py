"""Tests for two audit-found bugs fixed together:

1. agent-05-analyst.py: the `--no-llm` CLI flag was accepted and threaded
   through to daily_pulse(no_llm=...), but the parameter was never actually
   checked inside the function -- it always called the real LLM regardless.
   The deterministic fallback function (_deterministic_recommendations)
   existed, fully implemented, but was dead code (never called from
   anywhere). Also: an LLM failure just printed a warning and gave up,
   instead of falling back to the deterministic recommendations that exist
   for exactly that situation.

2. agent-04-distributor.py: distribution copy (LinkedIn/forum/email) was
   only ever written to disk if --output was explicitly passed. When run
   as part of the normal pipeline (--meta only, no --output -- the case
   run-all.sh and the dashboard actually use), the generated copy was
   printed to the console and then discarded -- nothing downstream could
   ever see it. Now persisted into meta.json under "distribution", mirroring
   how agent-03's "seo" block and agent-02b's "quality_check" block work.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "agents"))


def _load_module(filename: str, alias: str):
    spec = importlib.util.spec_from_file_location(alias, REPO_ROOT / "agents" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def analyst(monkeypatch):
    # These tests exercise the no_llm/deterministic-fallback logic, not the
    # database path -- they should behave identically whether or not the
    # ambient environment happens to have NEON_DATABASE_URL set (e.g. CI
    # setting a dummy DSN so agent-06-publisher.py's import-time check
    # passes). Force "no DB configured" explicitly rather than relying on
    # the shell environment being empty, which is not something a test
    # should assume.
    module = _load_module("agent-05-analyst.py", f"analyst_{id(object())}")
    monkeypatch.setattr(module, "DATABASE_URL", None)
    return module


@pytest.fixture
def distributor():
    return _load_module("agent-04-distributor.py", f"distributor_{id(object())}")


class TestAnalystNoLlmFlag:
    def test_no_llm_never_calls_ask_json(self, analyst, monkeypatch):
        def raising_ask_json(*a, **k):
            raise AssertionError("ask_json should not be called when no_llm=True")

        monkeypatch.setattr(analyst.llm_client, "ask_json", raising_ask_json)
        # No DB configured -> content stats come back empty/db_connected False,
        # which is fine; we're only testing the no_llm branch is honored.
        buf = io.StringIO()
        with redirect_stdout(buf):
            analyst.daily_pulse(days=1, sessions=None, subscribers=None, tool_ctr=None, no_llm=True)
        output = buf.getvalue()
        assert "детерминированные рекомендации" in output

    def test_no_llm_uses_deterministic_recommendations_content(self, analyst, monkeypatch):
        monkeypatch.setattr(analyst.llm_client, "ask_json", lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("should not be called")
        ))
        buf = io.StringIO()
        with redirect_stdout(buf):
            analyst.daily_pulse(days=1, sessions=None, subscribers=None, tool_ctr=None, no_llm=True)
        output = buf.getvalue()
        # With no analytics and no DB, _deterministic_recommendations()
        # should at least flag the missing analytics connection.
        assert "аналитику" in output or "Критичных сигналов" in output

    def test_llm_failure_falls_back_to_deterministic(self, analyst, monkeypatch):
        def failing_ask_json(*a, **k):
            raise analyst.llm_client.LLMError("simulated outage")

        monkeypatch.setattr(analyst.llm_client, "ask_json", failing_ask_json)
        buf = io.StringIO()
        with redirect_stdout(buf):
            analyst.daily_pulse(days=1, sessions=None, subscribers=None, tool_ctr=None, no_llm=False)
        output = buf.getvalue()
        assert "LLM недоступна" in output
        assert "simulated outage" in output
        # Must still produce SOME recommendation output, not just the warning.
        assert "•" in output

    def test_llm_success_path_still_works(self, analyst, monkeypatch):
        monkeypatch.setattr(
            analyst.llm_client, "ask_json",
            lambda *a, **k: {"recommendations": ["Test recommendation from LLM."]},
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            analyst.daily_pulse(days=1, sessions=100, subscribers=5, tool_ctr=1.5, no_llm=False)
        output = buf.getvalue()
        assert "Test recommendation from LLM." in output

    def test_deterministic_recommendations_flags_pending_drafts(self, analyst):
        recs = analyst._deterministic_recommendations({"drafts_pending": 3, "video_drafts_pending": 0})
        assert any("3 черновиков" in r for r in recs)

    def test_deterministic_recommendations_caps_at_four(self, analyst):
        metrics = {
            "drafts_pending": 5, "video_drafts_pending": 2,
            "articles_created": 3, "articles_published": 0,
            "sessions": None, "subscribers": None, "tool_ctr": None,
        }
        recs = analyst._deterministic_recommendations(metrics)
        assert len(recs) <= 4


class TestDistributorMetaPersistence:
    def test_meta_path_persists_distribution_key(self, distributor, tmp_path, monkeypatch):
        article_file = tmp_path / "article.txt"
        article_file.write_text("Article body text.", encoding="utf-8")
        meta_file = tmp_path / "article.meta.json"
        meta_file.write_text(json.dumps({
            "title": "Test Title", "article_file": str(article_file),
        }), encoding="utf-8")

        monkeypatch.setattr(
            distributor.llm_client, "ask_json",
            lambda *a, **k: {
                "linkedin_post": "LI post text",
                "forum_answer": "Forum answer text",
                "email_block": "Email block text",
            },
        )
        monkeypatch.setattr(sys, "argv", ["prog", "--meta", str(meta_file)])
        distributor.main()

        reloaded = json.loads(meta_file.read_text(encoding="utf-8"))
        assert "distribution" in reloaded
        assert reloaded["distribution"]["linkedin_post"] == "LI post text"
        # Original fields must survive untouched.
        assert reloaded["title"] == "Test Title"

    def test_explicit_output_still_works_alongside_meta_persistence(self, distributor, tmp_path, monkeypatch):
        article_file = tmp_path / "article.txt"
        article_file.write_text("Article body text.", encoding="utf-8")
        meta_file = tmp_path / "article.meta.json"
        meta_file.write_text(json.dumps({
            "title": "Test Title", "article_file": str(article_file),
        }), encoding="utf-8")
        output_file = tmp_path / "distribution.json"

        monkeypatch.setattr(
            distributor.llm_client, "ask_json",
            lambda *a, **k: {"linkedin_post": "x", "forum_answer": "y", "email_block": "z"},
        )
        monkeypatch.setattr(sys, "argv", ["prog", "--meta", str(meta_file), "--output", str(output_file)])
        distributor.main()

        assert output_file.exists()
        assert json.loads(meta_file.read_text(encoding="utf-8"))["distribution"]["linkedin_post"] == "x"
