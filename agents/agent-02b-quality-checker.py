#!/usr/bin/env python3
"""
Agent #2b — Quality Checker (NEW)

Проверяет статью после Writer'а и до публикации:
 - Оценивает по 4 критериям (фактичность, инженерная ценность, качество текста, SEO)
 - Если score < 70 — возвращает улучшенную версию
 - Если score >= 70 — одобряет и пропускает дальше

Usage:
  python3 agents/agent-02b-quality-checker.py --meta /tmp/article.meta.json
  python3 agents/agent-02b-quality-checker.py --meta /tmp/article.meta.json --threshold 75
"""

import sys
# Ensure UTF-8 console output on Windows (prevent UnicodeEncodeError for emojis/box chars)
for _s in ("stdout", "stderr"):
    _stream = getattr(sys, _s, None)
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            try:
                _stream.reconfigure(errors="replace")
            except Exception:
                pass

import os
import json
import argparse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import llm_client

_PROMPT_FILE = os.path.join(os.path.dirname(__file__), "prompts", "quality_checker.txt")
if os.path.exists(_PROMPT_FILE):
    with open(_PROMPT_FILE, encoding="utf-8") as _f:
        SYSTEM_PROMPT = _f.read()
else:
    SYSTEM_PROMPT = "Ты — редактор. Проверь статью. Ответь в JSON: {score, approved, issues, title, body, summary, tags}"


def check_article(title: str, body: str, brief: dict, summary: str = "") -> dict:
    # Research-routed briefs are never allowed to bypass their evidence ledger.
    # This is deterministic and prevents a provider/model formatting issue from
    # accidentally approving a source-less article.
    if brief.get("evidence_status", "").startswith("ready_") and not brief.get("evidence_ledger"):
        return {
            "score": 0,
            "approved": False,
            "factual_verdict": "reject",
            "issues": ["Research-routed brief has no evidence ledger"],
            "unsupported_claims": [{
                "claim": "Entire article",
                "reason": "No evidence ledger was supplied with a ready research brief",
                "severity": "blocking",
            }],
            "missing_evidence": ["evidence_ledger"],
        }
    user_prompt = f"""Проверь эту статью для SMTInsider:

ЗАГОЛОВОК: {title}

SUMMARY: {summary}

ТЕКСТ:
{body}

ИСХОДНЫЙ БРИФ (что было в источниках):
{json.dumps(brief, ensure_ascii=False, indent=2)}

Оцени строго. Если нашёл воду, клише или несоответствие брифу — исправь."""
    return llm_client.ask_json(SYSTEM_PROMPT, user_prompt, max_tokens=4000, temperature=0.4)


def assess_quality_verdict(result: dict, threshold: int) -> dict:
    """Turn the LLM's structured factual review into a publish decision.

    A high prose/SEO score cannot override a factual rejection. `reject` and
    any blocking unsupported claim are hard stops for the downstream pipeline.
    """
    score = result.get("score", 0)
    try:
        score = int(score)
    except (TypeError, ValueError):
        score = 0
    factual_verdict = str(result.get("factual_verdict", "reject")).lower()
    unsupported_claims = result.get("unsupported_claims", [])
    if not isinstance(unsupported_claims, list):
        unsupported_claims = []
    blocking_claims = [
        claim for claim in unsupported_claims
        if isinstance(claim, dict) and str(claim.get("severity", "blocking")).lower() == "blocking"
    ]
    explicit_approval = result.get("approved", factual_verdict == "pass")
    approved = bool(explicit_approval) and factual_verdict == "pass" and score >= threshold and not blocking_claims
    status = "approved" if approved else ("needs_revision" if factual_verdict == "revise" else "blocked")
    return {
        "score": score,
        "factual_verdict": factual_verdict,
        "unsupported_claims": unsupported_claims,
        "missing_evidence": result.get("missing_evidence", []) if isinstance(result.get("missing_evidence", []), list) else [],
        "blocking_claims": blocking_claims,
        "approved": approved,
        "status": status,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--meta", required=True, help="путь к *.meta.json от agent-02-writer.py")
    p.add_argument("--threshold", type=int, default=75, help="минимальный score для одобрения (default: 75)")
    p.add_argument("--dry-run", action="store_true", help="только показать оценку, не изменять файлы")
    args = p.parse_args()

    with open(args.meta, encoding="utf-8") as f:
        meta = json.load(f)

    article_file = meta.get("article_file", "")
    if not article_file or not os.path.exists(article_file):
        print(f"❌ Файл статьи не найден: {article_file}")
        sys.exit(1)

    with open(article_file, encoding="utf-8") as f:
        body = f.read()

    title = meta.get("title", "")
    summary = meta.get("summary", "")
    brief = meta.get("source_topic_brief", {})

    print(f"\n🔍 Agent #2b — Quality Checker")
    print(f"   Статья: {title}")
    print(f"   Проверяю...\n")

    try:
        result = check_article(title, body, brief, summary)
    except llm_client.LLMError as e:
        print(f"❌ {e}")
        sys.exit(1)

    verdict = assess_quality_verdict(result, args.threshold)
    score = verdict["score"]
    issues = result.get("issues", [])
    breakdown = result.get("breakdown", {})

    print(f"📊 Score: {score}/100 · factual verdict: {verdict['factual_verdict'].upper()}")
    if breakdown:
        for k, v in breakdown.items():
            print(f"   {k}: {v}/25")
    if issues:
        print("\n⚠️  Проблемы:")
        for issue in issues:
            print(f"   • {issue}")
    if verdict["unsupported_claims"]:
        print("\n⛔ Unsupported claims:")
        for claim in verdict["unsupported_claims"]:
            if isinstance(claim, dict):
                print(f"   • [{claim.get('severity', 'blocking')}] {claim.get('claim', '')}: {claim.get('reason', '')}")

    # A `revise` result may contain a corrected candidate. Re-check that exact
    # candidate once; never publish a rewrite that has not been fact-audited.
    improved = False
    if verdict["status"] == "needs_revision":
        candidate_title = result.get("title", title)
        candidate_body = result.get("body", body)
        candidate_summary = result.get("summary", summary)
        if candidate_body and candidate_body.strip() != body.strip():
            print("\n✏️  Повторно проверяю исправленную версию на фактическую точность...")
            try:
                result = check_article(candidate_title, candidate_body, brief, candidate_summary)
                verdict = assess_quality_verdict(result, args.threshold)
                improved = True
            except llm_client.LLMError as e:
                verdict = {**verdict, "approved": False, "status": "blocked", "factual_verdict": "reject"}
                issues = list(issues) + [f"Повторная factual-проверка не выполнилась: {e}"]
        else:
            verdict = {**verdict, "approved": False, "status": "blocked"}

    final_title = result.get("title", title)
    final_body = result.get("body", body)
    final_summary = result.get("summary", summary)
    final_tags = result.get("tags", meta.get("tags", []))
    quality_record = {
        **verdict,
        "breakdown": result.get("breakdown", breakdown),
        "issues": result.get("issues", issues),
        "improved": improved,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

    if not args.dry_run:
        # Keep rejected text for editorial inspection, but never overwrite the
        # article with an unverified rewrite.
        if verdict["approved"]:
            with open(article_file, "w", encoding="utf-8") as f:
                f.write(f"{final_title}\n\n{final_body.strip()}\n")
            meta["title"] = final_title
            meta["summary"] = final_summary
            meta["tags"] = final_tags
        meta["quality_check"] = quality_record
        with open(args.meta, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    if verdict["approved"]:
        print(f"\n✅ APPROVED: factual pass, score {verdict['score']}/{args.threshold}+")
        print(f"\n   → python3 agents/agent-03-seo-doctor.py --meta {args.meta}")
        print(f"   → python3 agents/agent-06-publisher.py submit --meta {args.meta}")
        return

    print("\n❌ BLOCKED: статья не переходит в SEO/Publisher до factual pass.")
    if verdict["missing_evidence"]:
        print("   Недостающие доказательства: " + "; ".join(map(str, verdict["missing_evidence"])))
    sys.exit(2)


if __name__ == "__main__":
    main()
