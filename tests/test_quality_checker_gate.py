"""Unit tests for the factual publication gate in Agent #2b."""

from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
QUALITY_CHECKER = REPO_ROOT / "agents" / "agent-02b-quality-checker.py"


def _load_quality_checker():
    spec = importlib.util.spec_from_file_location("quality_checker_gate", QUALITY_CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_factual_pass_with_score_is_publishable():
    checker = _load_quality_checker()
    verdict = checker.assess_quality_verdict(
        {
            "score": 86,
            "approved": True,
            "factual_verdict": "pass",
            "unsupported_claims": [],
        },
        threshold=75,
    )

    assert verdict["approved"] is True
    assert verdict["status"] == "approved"


def test_blocking_unsupported_claim_overrides_high_score():
    checker = _load_quality_checker()
    verdict = checker.assess_quality_verdict(
        {
            "score": 95,
            "approved": True,
            "factual_verdict": "pass",
            "unsupported_claims": [
                {
                    "claim": "Supports Hermes integration",
                    "reason": "Absent from source excerpts",
                    "severity": "blocking",
                }
            ],
        },
        threshold=75,
    )

    assert verdict["approved"] is False
    assert verdict["status"] == "blocked"


def test_ready_research_brief_without_ledger_is_rejected_without_llm(monkeypatch):
    checker = _load_quality_checker()
    monkeypatch.setattr(checker.llm_client, "ask_json", lambda **kwargs: (_ for _ in ()).throw(AssertionError("LLM must not run")))
    result = checker.check_article("Title", "Body", {"evidence_status": "ready_news"})
    assert result["factual_verdict"] == "reject"
    assert result["unsupported_claims"][0]["severity"] == "blocking"


def test_deterministic_ledger_audit_rejects_invented_date_and_number():
    checker = _load_quality_checker()
    violations = checker._ledger_numeric_violations(
        "The standard adds 47 photos and takes effect January 1, 2027.",
        [{"claims": ["The standard was released on July 29, 2026."]}],
    )
    assert violations
    assert all(violation["severity"] == "blocking" for violation in violations)


def test_missing_factual_verdict_fails_closed():
    checker = _load_quality_checker()
    verdict = checker.assess_quality_verdict({"score": 100, "approved": True}, threshold=75)

    assert verdict["approved"] is False
    assert verdict["factual_verdict"] == "reject"
