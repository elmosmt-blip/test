#!/usr/bin/env python3
"""
Agent #2 — Writer (реальная версия, без хардкода)

Пишет статью через открытую LLM (llm_client.py), а не выводит один и тот же
демо-текст про HIP. Может работать в двух режимах:

  1) --brief /tmp/smtinsider_briefs.json   — берёт темы из Agent #1 (Trend Hunter),
     по умолчанию первую (или с наивысшей срочностью), пишет статью под неё.
  2) --topic "Своя тема"                    — пишет статью на произвольную тему
     без файла-брифа (angle/keywords LLM придумает сама).

Результат:
  - <output>.txt   — статья, plain text (без HTML), готова для agent-06-publisher.py
  - <output>.meta.json — title/category/tags/summary для agent-03/04/06,
    чтобы не передавать их вручную каждый раз через CLI.

Usage:
  python3 agents/agent-02-writer.py --brief /tmp/smtinsider_briefs.json
  python3 agents/agent-02-writer.py --topic "BGA Head-in-Pillow Detection 2026" \
      --output /tmp/article.txt
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
import section_router
import article_linter

_PROMPT_FILE = os.path.join(os.path.dirname(__file__), "prompts", "writer.txt")
if os.path.exists(_PROMPT_FILE):
    with open(_PROMPT_FILE, encoding="utf-8") as _f:
        SYSTEM_PROMPT = _f.read()
else:
    SYSTEM_PROMPT = """Ты — старший технический редактор SMTInsider.com.
Пиши статьи для практикующих SMT-инженеров: конкретика, цифры, причинно-следственные связи.
Ответь СТРОГО в формате JSON: {"title":"...","body":"...","summary":"...","category":"...","tags":[]}
"""

_REVISE_PROMPT_FILE = os.path.join(os.path.dirname(__file__), "prompts", "writer_revise.txt")
if os.path.exists(_REVISE_PROMPT_FILE):
    with open(_REVISE_PROMPT_FILE, encoding="utf-8") as _f:
        REVISE_SYSTEM_PROMPT = _f.read()
else:
    REVISE_SYSTEM_PROMPT = """Проверь и улучши черновик статьи по чек-листу: факты, штампы, структура.
Ответь СТРОГО в формате JSON: {"title":"...","body":"...","summary":"...","category":"...","tags":[],"revision_notes":[]}
"""

_REPAIR_SYSTEM_PROMPT = """Ты — редактор SMTInsider, который исправляет ТОЛЬКО конкретно указанные технические проблемы в готовой статье, не переписывая её целиком.

Тебе дан текст статьи и список конкретных найденных проблем (от автоматического линтера — не LLM, поэтому находки точные, не додумывай другие правки). Исправь именно эти проблемы:
- banned_phrase → замени конкретную найденную фразу-штамп на нейтральную формулировку с фактом
- rule_of_three → перепиши перечисление из 3 параллельных элементов, сделай его неровным по длине или замени на 1-2 сильных факта
- flat_sentence_rhythm / flat_paragraph_rhythm → раздели/объедини несколько предложений или абзацев, чтобы ритм стал неровным (одно короткое, одно длинное)
- missing_headings → добавь 2-4 подзаголовка вида "## Заголовок раздела", разбив текст по смыслу
- too_short / too_long → сократи или расширь текст до нужного объёма, не теряя фактов
- generic_title → перепиши заголовок так, чтобы он называл конкретную сущность (модель/компанию/метрику)
- no_fact_grounding → добавь в текст конкретные цифры из key_facts брифа, которые сейчас отсутствуют

НЕ трогай остальной текст, если он не связан с перечисленными проблемами. Не вводи новых фактов, которых нет в брифе.

Ответь СТРОГО в формате JSON:
{"title": "...", "body": "...", "summary": "...", "category": "...", "tags": [...], "repairs_made": ["что исправлено, кратко"]}
"""


def _env_bool(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def load_brief(path: str, pick: str = "first") -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    topics = data.get("topics", [])
    if not topics:
        raise SystemExit(f"❌ В {path} нет тем (topics пуст)")
    if pick == "first":
        return topics[0]
    if pick == "urgent":
        order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        return sorted(topics, key=lambda t: order.get(t.get("urgency", "LOW"), 3))[0]
    if pick.isdigit():
        idx = int(pick)
        if idx < 0 or idx >= len(topics):
            raise SystemExit(f"❌ --pick {idx}: индекс вне диапазона (0-{len(topics)-1}, доступно {len(topics)} тем)")
        return topics[idx]
    raise ValueError(f"Неизвестный режим выбора темы: {pick}")


def _source_limited_brief(brief: dict) -> bool:
    """Whether evidence supports only a short attributed update, not a review."""
    sources = brief.get("sources", []) or []
    excerpts = [str(source.get("excerpt", "")).strip() for source in sources if str(source.get("excerpt", "")).strip()]
    return len(excerpts) <= 1 and sum(len(text.split()) for text in excerpts) < 700


def prepare_brief_for_evidence(brief: dict) -> dict:
    """Downgrade sparse single-source reviews to evidence-bounded news.

    Forcing an 800-word review from one short press-release excerpt is an
    instruction to hallucinate. A concise attributed news item is useful and
    publishable; unsupported review detail is not.
    """
    prepared = dict(brief)
    if _source_limited_brief(prepared):
        prepared["format"] = "news"
        prepared["editorial_type"] = "news"
        prepared["evidence_limited"] = True
    return prepared


def build_evidence_dossier(brief: dict) -> dict:
    """Create a deterministic story plan from literal researched claims.

    The LLM receives a plan and claim IDs, not a vague invitation to turn a
    topic into an article. This keeps depth proportional to source depth.
    """
    ledger = brief.get("evidence_ledger", []) or []
    claims = []
    for source_index, entry in enumerate(ledger, 1):
        for claim in entry.get("claims", []) or []:
            claim = str(claim).strip()
            if claim:
                claims.append({"id": f"C{len(claims) + 1}", "text": claim, "source_url": entry.get("source_url", ""), "source": source_index})
    article_type = brief.get("editorial_type") or brief.get("format") or "news"
    if article_type == "review":
        target = "900-1300 words"
        section_size = 3
    elif article_type == "insight":
        target = "600-900 words"
        section_size = 3
    else:
        target = "180-320 words" if len(claims) <= 5 else "300-500 words"
        section_size = 2
    groups = [claims[i:i + section_size] for i in range(0, len(claims), section_size)]
    return {"article_type": article_type, "target_length": target, "claims": claims, "sections": groups}


def build_writer_user_prompt(brief: dict) -> str:
    # Build a structured user prompt that highlights the most important signals
    parts = []
    parts.append(f"ТЕМА: {brief.get('topic', '')}")
    if brief.get("angle"):
        parts.append(f"\nИНЖЕНЕРНЫЙ РАКУРС (обязательно раскрой в статье):\n{brief['angle']}")
    if brief.get("key_facts"):
        facts = "\n".join(f"  • {f}" for f in brief["key_facts"])
        parts.append(f"\nКОНКРЕТНЫЕ ФАКТЫ (используй точно, не перефразируй цифры):\n{facts}")
    if brief.get("source_notes"):
        parts.append(f"\nЧТО ПОДТВЕРЖДАЮТ ИСТОЧНИКИ: {brief['source_notes']}")
    ledger = brief.get("evidence_ledger", []) or []
    if ledger:
        ledger_blocks = []
        for entry in ledger:
            claims = [str(claim) for claim in entry.get("claims", []) if str(claim).strip()]
            if claims:
                ledger_blocks.append(
                    f"SOURCE: {entry.get('source_url', '')}\n" + "\n".join(f"  ✓ {claim}" for claim in claims)
                )
        if ledger_blocks:
            parts.append(
                "\nРАЗРЕШЁННЫЙ CLAIM LEDGER — КРИТИЧЕСКОЕ ПРАВИЛО:\n"
                "Каждое фактическое утверждение, цифра, дата, спецификация, comparison или historical claim "
                "в статье должно быть перефразировкой одного из пунктов ниже. Если claim отсутствует в ledger, "
                "не пиши его. Не выводи отрицательные claims вида «source does not disclose X», если это не "
                "явно сказано в source.\n\n" + "\n\n".join(ledger_blocks)
            )
    dossier = build_evidence_dossier(brief)
    if dossier["claims"]:
        plan_sections = []
        for number, group in enumerate(dossier["sections"], 1):
            plan_sections.append(f"Section {number}: " + ", ".join(item["id"] for item in group))
        claims_text = "\n".join(f"{item['id']} [{item['source_url']}]: {item['text']}" for item in dossier["claims"])
        parts.append(
            "\nSTORY PLAN — ОБЯЗАТЕЛЬНО:\n"
            f"Article type: {dossier['article_type']}\nTarget length: {dossier['target_length']}\n"
            f"Plan: {' | '.join(plan_sections)}\n\n"
            "Используй claims только из списка ниже. Каждый содержательный абзац должен опираться "
            "на указанный claim ID. Не добавляй новый факт для связности, длины или практического вывода.\n"
            f"{claims_text}"
        )
    editorial = brief.get("editorial_type") or brief.get("format", "")
    if editorial:
        parts.append(f"\nФОРМАТ СТАТЬИ: {editorial}")
    if brief.get("keywords"):
        parts.append(f"КЛЮЧЕВЫЕ СЛОВА: {', '.join(brief['keywords'])}")

    sources = brief.get("sources", []) or []
    sources_with_content = [s for s in sources if (s.get("excerpt") or "").strip()]
    sources_bare = [s for s in sources if not (s.get("excerpt") or "").strip()]

    if sources_with_content:
        blocks = []
        for i, s in enumerate(sources_with_content, 1):
            role = s.get("role", "source")
            role_label = {
                "fresh_primary": "первоисточник новости",
                "related_fresh_signal": "независимое подтверждение/доп. деталь",
                "context_link": "контекст с сайта производителя",
            }.get(role, role)
            excerpt = s.get("excerpt", "").strip()[:550]
            blocks.append(
                f"── ИСТОЧНИК {i}: {s.get('title','')} ({role_label}, {s.get('date','unknown')}) ──\n"
                f"URL: {s.get('url','')}\n"
                f"Содержание: {excerpt}"
            )
        parts.append(
            f"\n\nМАТЕРИАЛЫ ПО ТЕМЕ ({len(sources_with_content)} источник(ов) с содержанием):\n\n"
            + "\n\n".join(blocks)
        )
        if len(sources_with_content) >= 2:
            parts.append(
                "\nВАЖНО — ЭТО МНОГОИСТОЧНИКОВАЯ СТАТЬЯ:\n"
                "Синтезируй все источники вместе, а не перескажи только первый. Конкретно:\n"
                "  1) Если источники сообщают разные детали одного события — объедини их в цельную картину "
                "(например: первоисточник даёт цифры производителя, независимый источник — контекст рынка или реакцию).\n"
                "  2) Если источники расходятся в деталях или цифрах — прямо укажи это расхождение читателю, "
                "не сглаживай его молчанием.\n"
                "  3) Когда используешь факт из конкретного источника, где это уместно указывай откуда он "
                "(\"согласно пресс-релизу TRI...\", \"по данным независимого обзора...\") — это не одна и та же формулировка "
                "каждый раз, а естественная атрибуция по ходу текста.\n"
                "  4) Не пиши статью так, будто она пересказывает один пресс-релиз — читатель должен почувствовать, "
                "что редактор сверил несколько материалов, а не скопировал один."
            )
    if sources_bare:
        src_lines = "\n".join(
            f"  - {s.get('title','')} ({s.get('date','')}) {s.get('url','')}"
            for s in sources_bare
        )
        parts.append(f"\nДОПОЛНИТЕЛЬНЫЕ ССЫЛКИ БЕЗ ИЗВЛЕЧЁННОГО ТЕКСТА (используй только для блока Sources, не выдумывай их содержание):\n{src_lines}")

    if sources:
        parts.append(
            "\nВ КОНЦЕ СТАТЬИ добавь блок Sources: со всеми источниками выше в формате "
            "\"- Название — URL\" (по одному на строку)."
        )

    if brief.get("evidence_limited"):
        primary_headline = (sources_with_content[0].get("title", "") if sources_with_content else brief.get("topic", ""))
        parts.append(
            "\nРЕЖИМ ОГРАНИЧЕННЫХ ДОКАЗАТЕЛЬСТВ — ОБЯЗАТЕЛЬНО:\n"
            "Это короткая новость на 350–550 слов, а не review. Каждый факт должен быть "
            "пересказом конкретного предложения из excerpt/key_facts. НЕЛЬЗЯ добавлять "
            "типовые требования отрасли, примеры классов, цифры, standards, qualification, "
            "результаты внедрения, параметры или описание предыдущих версий, если их нет в "
            "источнике. Не компенсируй нехватку фактов общими инженерными знаниями.\n"
            f"Используй исходный headline как основу заголовка: «{primary_headline}». Не выноси "
            "в заголовок маркетинговый target market (например AI hardware), если это не главное "
            "событие новости. Не повторяй lead в следующих абзацах. Структура: короткий фактологический "
            "lead → что именно сообщил источник → один практический контекст без новых claims → источник. "
            "Если деталь не раскрыта, просто не обсуждай её. Допустим только нейтральный вопрос "
            "для читателя: «перед внедрением запросите у поставщика подробную документацию»."
        )

    parts.append("\nНапиши статью строго по этому брифу и источникам выше. Используй только факты, которые реально в них есть — не выдумывай спецификации, цифры или события.")
    return "\n".join(parts)


def write_article(brief: dict) -> dict:
    user_prompt = build_writer_user_prompt(brief)
    temperature = 0.2 if brief.get("evidence_ledger") else 0.7
    return llm_client.ask_json(SYSTEM_PROMPT, user_prompt, max_tokens=3800, temperature=temperature)




def revise_article(draft: dict, brief: dict) -> dict:
    """Second pass: the model re-reads its own draft against a strict editorial
    checklist (fact-grounding, AI clichés, repetition, structure, headline
    quality) and returns a polished version. This is what turns a decent
    first draft into something that reads like it was edited by a senior
    technical editor rather than generated in one shot.
    """
    evidence_rule = (
        "\nEVIDENCE-LIMITED: delete every unsupported claim; do not expand the article, "
        "do not add typical industry requirements, numerical examples, standards, or comparisons. "
        "A concise source-bounded news item is correct.\n"
        if brief.get("evidence_limited") else ""
    )
    user_prompt = (
        evidence_rule +
        f"ИСХОДНЫЙ БРИФ:\n{json.dumps(brief, ensure_ascii=False, indent=2)}\n\n"
        f"ЧЕРНОВИК ДЛЯ ПРАВКИ:\n\n"
        f"ЗАГОЛОВОК: {draft.get('title', '')}\n\n"
        f"ТЕКСТ:\n{draft.get('body', '')}\n\n"
        f"SUMMARY: {draft.get('summary', '')}\n\n"
        f"Пройдись по чек-листу и верни финальную версию."
    )
    return llm_client.ask_json(REVISE_SYSTEM_PROMPT, user_prompt, max_tokens=3800, temperature=0.5)


def repair_article(article: dict, issues: list, brief: dict) -> dict:
    """Third pass: fix ONLY the specific issues the deterministic linter
    found (agents/article_linter.py), rather than a full re-revision. This
    is cheap and precise — the model doesn't have to re-derive the whole
    article, just patch the flagged spots, which also means it's much less
    likely to introduce a new problem while fixing an old one.
    """
    issues_text = "\n".join(f"- [{i.code}] {i.message}" for i in issues)
    key_facts = brief.get("key_facts", [])
    facts_text = "\n".join(f"  • {f}" for f in key_facts) if key_facts else "(нет в брифе)"
    user_prompt = (
        f"ЗАГОЛОВОК: {article.get('title', '')}\n\n"
        f"ТЕКСТ:\n{article.get('body', '')}\n\n"
        f"НАЙДЕННЫЕ ПРОБЛЕМЫ (исправь только их):\n{issues_text}\n\n"
        f"KEY_FACTS ИЗ БРИФА (для no_fact_grounding, если применимо):\n{facts_text}"
    )
    return llm_client.ask_json(_REPAIR_SYSTEM_PROMPT, user_prompt, max_tokens=3800, temperature=0.4)


def write_article_with_revision(brief: dict, skip_revision: bool = False) -> dict:
    """Three-pass writing pipeline:
      1. Draft (write_article)
      2. Self-revision against the editorial checklist (revise_article) —
         an LLM re-reading its own work for facts/clichés/structure/rhythm.
      3. Targeted repair (repair_article) — ONLY runs if the deterministic
         linter (agents/article_linter.py) still finds objective problems
         after step 2. This catches what step 2 missed: an LLM instructed
         "avoid clichés" is a strong bias, not a guarantee, so a fast regex/
         statistics-based check backstops it before publishing.

    Each pass degrades gracefully to the previous result if it fails or
    returns something unusable, so a flaky LLM call never blocks
    publishing outright.
    """
    draft = write_article(brief)
    if skip_revision:
        if draft.get("body"):
            editorial_type = brief.get("editorial_type") or brief.get("format", "news")
            report = article_linter.lint_article(
                draft.get("title", ""), draft.get("body", ""),
                editorial_type=editorial_type, key_facts=brief.get("key_facts", []),
            )
            draft["_lint_report"] = report.to_dict()
            print(f"  🔍 Линтер: score={report.score}/100"
                  + (f", найдено проблем: {len(report.issues)} (repair пропущен: --no-revision)" if report.issues else ""))
        return draft
    if not draft.get("body"):
        return draft

    try:
        revised = revise_article(draft, brief)
    except llm_client.LLMError as e:
        print(f"  ⚠ Revision pass failed ({e}), keeping first draft.")
        revised = draft
    else:
        if not revised.get("body"):
            print("  ⚠ Revision pass returned empty body, keeping first draft.")
            revised = draft
        else:
            notes = revised.get("revision_notes") or []
            if notes:
                print("  ✏️  Правки на втором проходе:")
                for n in notes[:8]:
                    print(f"     • {n}")
            else:
                print("  ✓ Черновик прошёл self-review без существенных правок.")
            revised["_draft_title"] = draft.get("title", "")

    # Pass 3: deterministic lint + targeted repair, capped at one repair
    # attempt so a stubborn issue can't loop the pipeline indefinitely.
    editorial_type = brief.get("editorial_type") or brief.get("format", "news")
    report = article_linter.lint_article(
        revised.get("title", ""), revised.get("body", ""),
        editorial_type=editorial_type, key_facts=brief.get("key_facts", []),
    )
    revised["_lint_report"] = report.to_dict()
    if report.issues:
        print(f"  🔍 Линтер: score={report.score}/100, найдено проблем: {len(report.issues)}")
        for issue in report.issues:
            print(f"     • [{issue.severity}] {issue.message}")
    else:
        print(f"  🔍 Линтер: score={report.score}/100, проблем не найдено")

    # A sparse primary source must never be expanded just to satisfy a word
    # count or stylistic lint rule. That was the path that reintroduced
    # invented implementation details after a careful evidence-limited draft.
    repair_issues = [
        issue for issue in report.issues
        if issue.code not in {"too_short", "rule_of_three", "missing_headings", "no_fact_grounding"}
    ] if brief.get("evidence_limited") else report.issues
    if repair_issues and _env_bool("WRITER_LINT_REPAIR", "1"):
        try:
            repaired = repair_article(revised, repair_issues, brief)
        except llm_client.LLMError as e:
            print(f"  ⚠ Repair pass failed ({e}), keeping pre-repair version.")
            return revised
        if not repaired.get("body"):
            print("  ⚠ Repair pass returned empty body, keeping pre-repair version.")
            return revised
        repairs = repaired.get("repairs_made") or []
        if repairs:
            print("  🔧 Точечные исправления (проход 3):")
            for r in repairs[:8]:
                print(f"     • {r}")
        # Re-lint after repair so the final report reflects what actually
        # shipped, not what was true before the repair pass.
        final_report = article_linter.lint_article(
            repaired.get("title", ""), repaired.get("body", ""),
            editorial_type=editorial_type, key_facts=brief.get("key_facts", []),
        )
        repaired["_lint_report"] = final_report.to_dict()
        repaired["_draft_title"] = revised.get("_draft_title", draft.get("title", ""))
        print(f"  🔍 Линтер после ремонта: score={final_report.score}/100 (было {report.score}/100)")
        return repaired

    return revised


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--brief", help="путь к JSON от agent-01-trend-hunter.py")
    p.add_argument("--pick", default="urgent",
                    help="какую тему из брифа выбрать: 'first', 'urgent', или числовой индекс (0,1,2...)")
    p.add_argument("--topic", help="тема вручную (если не используешь --brief)")
    p.add_argument("--output", default="/tmp/smtinsider_article.txt")
    p.add_argument("--no-revision", action="store_true",
                    help="отключить второй проход самопроверки/полировки (быстрее, но ниже качество)")
    args = p.parse_args()

    if not args.brief and not args.topic:
        print("❌ Укажи --brief <файл от Trend Hunter> или --topic \"тема\"")
        sys.exit(1)

    if args.brief:
        brief = load_brief(args.brief, args.pick)
    else:
        brief = {"topic": args.topic, "angle": "", "format": "news",
                  "keywords": [], "category": "SMT Equipment"}

    brief = prepare_brief_for_evidence(brief)
    if brief.get("writer_allowed") is False:
        print("❌ Writer заблокирован: недостаточно source evidence. Автоматический Evidence Research должен найти дополнительные источники.")
        sys.exit(2)
    if brief.get("evidence_status", "").startswith("ready_") and not brief.get("evidence_ledger"):
        print("❌ Writer заблокирован: ready topic не содержит evidence ledger.")
        sys.exit(2)
    skip_revision = args.no_revision or os.environ.get("WRITER_SKIP_REVISION", "").lower() in {"1", "true", "yes"}

    print(f"\n✍️ Agent #2 — Writer")
    print(f"   Тема: {brief.get('topic')}")
    print(f"   Модель: {llm_client.LLM_MODEL}")
    if brief.get("evidence_limited"):
        print("   Evidence: один ограниченный источник → короткая news-статья без неподтверждённых деталей")
    print(f"   Режим: {'один проход (без self-review)' if skip_revision else 'три прохода (черновик → self-review → lint+repair)'}")
    print(f"   Пишу черновик...\n")

    try:
        article = write_article_with_revision(brief, skip_revision=skip_revision)
    except llm_client.LLMError as e:
        print(f"❌ {e}")
        sys.exit(1)

    body = article.get("body", "").strip()
    title = article.get("title", brief.get("topic", "Untitled"))
    if not body:
        print("❌ LLM вернула пустое тело статьи")
        sys.exit(1)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(f"{title}\n\n{body}\n")

    category = article.get("category", brief.get("category", "SMT Equipment"))
    tags = article.get("tags", brief.get("keywords", []))
    section = section_router.decide_section(
        title=title,
        body=body,
        category=category,
        tags=tags,
        source_topic_brief=brief,
        explicit=brief.get("editorial_type") or brief.get("target_section") or brief.get("format", "news"),
    )

    meta = {
        "title": title,
        "source_url": brief.get("sources", [{}])[0].get("url", "") if brief.get("sources") else "",
        "summary": article.get("summary", body[:200].rstrip() + "…"),
        "category": category,
        "tags": tags,
        "editorial_type": section.editorial_type,
        "section_path": section.section_path,
        "section_routing": section.to_dict(),
        "source_topic_brief": brief,
        "evidence_dossier": build_evidence_dossier(brief),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": llm_client.LLM_MODEL,
        "article_file": args.output,
        "draft_title": article.get("_draft_title", ""),
        "revision_notes": article.get("revision_notes", []),
        "revised": not skip_revision,
        "lint_report": article.get("_lint_report", {}),
    }
    meta_path = args.output.rsplit(".", 1)[0] + ".meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    word_count = len(body.split())
    print(f"\n✅ Статья сохранена: {args.output} ({len(body)} символов, ~{word_count} слов)")
    print(f"✅ Метаданные сохранены: {meta_path}")
    print(f"✅ Раздел публикации: {section.editorial_type} → {section.section_path} ({section.confidence:.0%})")
    print(f"   → python3 agents/agent-02b-quality-checker.py --meta {meta_path}")
    print(f"   → python3 agents/agent-03-seo-doctor.py --meta {meta_path}")
    print(f"   → python3 agents/agent-06-publisher.py submit --meta {meta_path}")


if __name__ == "__main__":
    main()
