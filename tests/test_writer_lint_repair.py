"""Tests for the 3-pass writer orchestration in agents/agent-02-writer.py:
draft -> self-revision -> deterministic lint + targeted repair.

Directly monkeypatches llm_client.ask_json rather than relying on the
LLM_MOCK env var, for the same reason as
tests/test_multi_source_corroboration.py: llm_client.py reads LLM_MOCK once
as a module-level constant at first import, and pytest caches imported
modules across test files in one process, so env-var timing is fragile.
Patching ask_json directly sidesteps that entirely.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WRITER_FILE = REPO_ROOT / "agents" / "agent-02-writer.py"
sys.path.insert(0, str(REPO_ROOT / "agents"))


def _load_writer_module():
    spec = importlib.util.spec_from_file_location(f"writer_{id(object())}", WRITER_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def writer():
    return _load_writer_module()


CLEAN_ARTICLE_BODY = (
    "## What Changed\n\n"
    "TRI's TR7600 SV pushes AXI throughput to 3200 CPH on a 2-camera configuration. "
    "That's a real gain. "
    "For a line running mixed BGA and QFN packages, this changes the cycle-time math "
    "for X-ray inspection stations that were previously the bottleneck standing between "
    "reflow output and final test, especially on boards where hidden solder joints under "
    "large BGA packages can't be confirmed by optical inspection alone.\n\n"
    "## Engineering Considerations\n\n"
    "Process engineers should verify the throughput figure against their own board mix "
    "before assuming the vendor number will hold on the shop floor. "
    "An independent test found a lower number closer to 2900 CPH under mixed-board conditions, "
    "which is still a meaningful improvement over the 2650 CPH baseline from the SIII generation, "
    "but the gap between the marketing figure and the field figure is exactly the kind of thing "
    "worth asking about before signing a purchase order. "
    "Cycle time is only part of the picture; review workload matters just as much. "
    "A system that scans faster but generates more false calls simply moves the bottleneck "
    "from the X-ray station to the review station down the line, which is not a net win "
    "for a plant that is already short on qualified inspection operators.\n\n"
    "## Questions Worth Asking at the Demo\n\n"
    "Bring your own board panel with real defect samples if the vendor allows it. "
    "Ask specifically how the reconstruction algorithm handles densely populated boards "
    "with overlapping component shadows, since that scenario is where throughput numbers "
    "measured on a clean demo board tend to diverge most from production reality. "
    "Confirm whether the MES integration supports your existing traceability format, "
    "because a fast inspection station that can't feed data back into the quality system "
    "creates a reporting gap that someone on the floor will end up filling manually.\n\n"
    "## Where It Fits on the Line\n\n"
    "For plants running high-mix production with a meaningful share of BGA and QFN "
    "packages, the throughput gain matters most when X-ray has historically been the "
    "station that operators route boards around during a rush, quietly building a "
    "backlog that gets caught up on the night shift. "
    "Closing that gap changes the staffing conversation as much as it changes the "
    "equipment budget, since a station that keeps pace with reflow output no longer "
    "needs a dedicated operator working overtime to clear the queue before the next "
    "shift change. "
    "Smaller shops running lower mix volumes may find the gain less decisive, since "
    "their existing AXI throughput was rarely the constraint in the first place, "
    "and the money might be better spent on SPI closed-loop control instead. "
    "The right comparison is not the vendor's headline number against the outgoing "
    "machine's spec sheet, but the vendor's number against what your own line actually "
    "needs to keep pace with, measured over a representative production week rather "
    "than a single best-case shift."
)

DIRTY_ARTICLE_BODY = (
    "This is a cutting-edge, revolutionary, game-changing system. "
    "It is faster, smarter, and more efficient than before. "
    "It also improves quality, reduces cost, and increases yield."
)


def test_sparse_single_source_is_downgraded_to_source_bounded_news(writer):
    brief = {
        "topic": "IPC-A-630A Released",
        "format": "review",
        "editorial_type": "review",
        "sources": [{"title": "IPC release", "excerpt": "IPC-A-630A provides class-coded acceptance criteria for box assemblies.", "url": "https://example.com/ipc"}],
    }

    prepared = writer.prepare_brief_for_evidence(brief)

    assert prepared["evidence_limited"] is True
    assert prepared["format"] == "news"
    assert prepared["editorial_type"] == "news"
    assert "РЕЖИМ ОГРАНИЧЕННЫХ ДОКАЗАТЕЛЬСТВ" in writer.build_writer_user_prompt(prepared)


def test_writer_prompt_includes_literal_evidence_ledger(writer):
    prompt = writer.build_writer_user_prompt({
        "topic": "IPC-A-630A", "evidence_ledger": [{
            "source_url": "https://example.com/ipc",
            "claims": ["IPC-A-630A introduces class-coded acceptance criteria."],
        }],
    })
    assert "РАЗРЕШЁННЫЙ CLAIM LEDGER" in prompt
    assert "IPC-A-630A introduces class-coded acceptance criteria." in prompt


class TestRepairArticle:
    def test_builds_prompt_with_issues_and_calls_ask_json(self, writer, monkeypatch):
        captured = {}

        def fake_ask_json(system, user, **kw):
            captured["system"] = system
            captured["user"] = user
            return {"title": "Fixed Title", "body": "Fixed body.", "summary": "s",
                    "category": "c", "tags": [], "repairs_made": ["removed cliché"]}

        monkeypatch.setattr(writer.llm_client, "ask_json", fake_ask_json)

        article = {"title": "Old Title", "body": DIRTY_ARTICLE_BODY}
        from article_linter import lint_article
        report = lint_article(article["title"], article["body"], editorial_type="news")
        result = writer.repair_article(article, report.issues, {"key_facts": []})

        assert result["title"] == "Fixed Title"
        assert "banned_phrase" in captured["user"] or "cutting-edge" in captured["user"].lower()
        assert "НАЙДЕННЫЕ ПРОБЛЕМЫ" in captured["user"]


class TestWriteArticleWithRevisionLintIntegration:
    def test_clean_article_skips_repair_pass(self, writer, monkeypatch):
        call_count = {"n": 0}

        def fake_ask_json(system, user, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:  # draft
                return {"title": "TRI TR7600 SV Ships With Higher Throughput",
                         "body": CLEAN_ARTICLE_BODY, "summary": "s", "category": "Inspection", "tags": ["AXI"]}
            elif call_count["n"] == 2:  # revision
                return {"title": "TRI TR7600 SV Ships With Higher Throughput",
                         "body": CLEAN_ARTICLE_BODY, "summary": "s", "category": "Inspection",
                         "tags": ["AXI"], "revision_notes": []}
            raise AssertionError("repair pass should not be called for a clean article")

        monkeypatch.setattr(writer.llm_client, "ask_json", fake_ask_json)
        brief = {"topic": "TRI TR7600 SV", "editorial_type": "news", "key_facts": ["3200 CPH", "2900 CPH", "2650 CPH"]}
        result = writer.write_article_with_revision(brief)

        assert call_count["n"] == 2  # draft + revision only, no repair call
        assert result["_lint_report"]["score"] >= 70

    def test_dirty_article_triggers_repair_pass(self, writer, monkeypatch):
        call_count = {"n": 0}

        def fake_ask_json(system, user, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:  # draft
                return {"title": "New Improvements", "body": DIRTY_ARTICLE_BODY,
                         "summary": "s", "category": "c", "tags": []}
            elif call_count["n"] == 2:  # revision (leaves it dirty, as if revision missed it)
                return {"title": "New Improvements", "body": DIRTY_ARTICLE_BODY,
                         "summary": "s", "category": "c", "tags": [], "revision_notes": []}
            elif call_count["n"] == 3:  # repair
                return {"title": "TRI TR7600 SV Update", "body": CLEAN_ARTICLE_BODY,
                         "summary": "s", "category": "c", "tags": [], "repairs_made": ["removed clichés"]}
            raise AssertionError("unexpected 4th ask_json call")

        monkeypatch.setattr(writer.llm_client, "ask_json", fake_ask_json)
        brief = {"topic": "Test Topic", "editorial_type": "news", "key_facts": []}
        result = writer.write_article_with_revision(brief)

        assert call_count["n"] == 3  # draft + revision + repair
        assert result["title"] == "TRI TR7600 SV Update"
        assert result["_lint_report"]["score"] > 50  # improved after repair

    def test_skip_revision_lints_but_never_repairs(self, writer, monkeypatch):
        call_count = {"n": 0}

        def fake_ask_json(system, user, **kw):
            call_count["n"] += 1
            return {"title": "New Improvements", "body": DIRTY_ARTICLE_BODY,
                     "summary": "s", "category": "c", "tags": []}

        monkeypatch.setattr(writer.llm_client, "ask_json", fake_ask_json)
        brief = {"topic": "Test Topic", "editorial_type": "news"}
        result = writer.write_article_with_revision(brief, skip_revision=True)

        assert call_count["n"] == 1  # draft only
        assert "_lint_report" in result
        assert len(result["_lint_report"]["issues"]) > 0  # still detected, just not repaired

    def test_repair_failure_degrades_to_pre_repair_version(self, writer, monkeypatch):
        call_count = {"n": 0}

        def fake_ask_json(system, user, **kw):
            call_count["n"] += 1
            if call_count["n"] <= 2:
                return {"title": "New Improvements", "body": DIRTY_ARTICLE_BODY,
                         "summary": "s", "category": "c", "tags": [], "revision_notes": []}
            raise writer.llm_client.LLMError("simulated repair failure")

        monkeypatch.setattr(writer.llm_client, "ask_json", fake_ask_json)
        brief = {"topic": "Test Topic", "editorial_type": "news"}
        result = writer.write_article_with_revision(brief)

        # Should not raise -- falls back to the pre-repair (post-revision) version.
        assert result["body"] == DIRTY_ARTICLE_BODY

    def test_repair_can_be_disabled_via_env(self, writer, monkeypatch):
        monkeypatch.setenv("WRITER_LINT_REPAIR", "0")
        call_count = {"n": 0}

        def fake_ask_json(system, user, **kw):
            call_count["n"] += 1
            return {"title": "New Improvements", "body": DIRTY_ARTICLE_BODY,
                     "summary": "s", "category": "c", "tags": [], "revision_notes": []}

        monkeypatch.setattr(writer.llm_client, "ask_json", fake_ask_json)
        brief = {"topic": "Test Topic", "editorial_type": "news"}
        result = writer.write_article_with_revision(brief)

        assert call_count["n"] == 2  # draft + revision, no repair call
        assert len(result["_lint_report"]["issues"]) > 0  # still flagged, just not fixed
