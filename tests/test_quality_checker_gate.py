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


def test_missing_factual_verdict_fails_closed():
    checker = _load_quality_checker()
    verdict = checker.assess_quality_verdict({"score": 100, "approved": True}, threshold=75)

    assert verdict["approved"] is False
    assert verdict["factual_verdict"] == "reject"
