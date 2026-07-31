"""
agents/article_linter.py — deterministic, non-LLM quality checks for a
generated article.

Why this exists alongside the LLM-based self-revision pass (writer_revise.txt)
and Quality Checker (agent-02b): an LLM instructed "don't use clichés" will
still miss some — instructions are a strong bias, not a guarantee. A regex
scan for a fixed banned-phrase list, sentence-length variance, or whether a
number in the body actually appears in the source brief are objective,
100%-reliable checks that don't depend on the model "remembering" the rule
this time. This module is fast (no network, no LLM call) and its findings
feed a targeted, cheap third repair pass in agent-02-writer.py — the LLM
only has to fix the specific issues found, not re-derive the whole article.

Nothing here replaces human editorial judgement or the LLM's self-revision —
it catches the mechanically-detectable subset of "reads like AI" problems.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field


# Mirrors the banned-phrase list in agents/prompts/writer.txt so drift
# between "what we tell the model not to do" and "what we actually check
# for" stays visible if one list is edited without the other.
BANNED_PHRASES = [
    "in today's fast-paced", "in the ever-evolving landscape", "it is worth noting",
    "it should be mentioned", "as we can see", "at the end of the day",
    "when it comes to", "in conclusion", "dive into", "delve into",
    "unlock the potential", "unlock the power", "seamlessly", "seamless integration",
    "robust solution", "cutting-edge", "state-of-the-art solution", "game-changer",
    "game-changing", "revolutionize", "revolutionary", "paradigm shift",
    "next-generation solution", "industry-leading", "unparalleled",
    "the future of", "as technology continues to advance", "moreover,",
    "furthermore,", "additionally,", "in summary,", "to sum up",
]

# "Rule of three" pattern: three short comma/and-separated adjectives or
# noun phrases — a very recognizable LLM tic ("faster, smarter, and more
# efficient"). Matches 3 short (<=3 word) segments joined by commas and a
# final "and".
_RULE_OF_THREE_RE = re.compile(
    r"\b(\w+(?:\s\w+){0,2}),\s(\w+(?:\s\w+){0,2}),?\sand\s(\w+(?:\s\w+){0,2})\b",
    re.IGNORECASE,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZА-Я0-9])")
_HEADING_RE = re.compile(r"^##\s+\S")
_NUMBER_RE = re.compile(r"\b\d[\d,.]*\s?%?\b")


@dataclass
class LintIssue:
    code: str
    message: str
    severity: str = "warning"  # "warning" | "error"


@dataclass
class LintReport:
    issues: list[LintIssue] = field(default_factory=list)
    word_count: int = 0
    sentence_count: int = 0
    heading_count: int = 0

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    @property
    def score(self) -> int:
        """0-100 heuristic score, deducting per issue. Not a substitute for
        the LLM-based Quality Checker score — a fast, cheap signal to decide
        whether a targeted repair pass is worth running."""
        score = 100
        for issue in self.issues:
            score -= 15 if issue.severity == "error" else 6
        return max(0, score)

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "word_count": self.word_count,
            "sentence_count": self.sentence_count,
            "heading_count": self.heading_count,
            "issues": [{"code": i.code, "message": i.message, "severity": i.severity} for i in self.issues],
        }


def _split_sentences(text: str) -> list[str]:
    # Strip heading lines before sentence-splitting so "## What Changed"
    # doesn't get counted as a sentence.
    body_lines = [ln for ln in text.split("\n") if not _HEADING_RE.match(ln.strip())]
    body = " ".join(body_lines)
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(body) if s.strip()]
    return sentences


def check_banned_phrases(text: str) -> list[LintIssue]:
    lower = text.lower()
    issues = []
    for phrase in BANNED_PHRASES:
        if phrase in lower:
            issues.append(LintIssue(
                code="banned_phrase",
                message=f'запрещённая фраза найдена: "{phrase}"',
                severity="error",
            ))
    return issues


def check_rule_of_three(text: str) -> list[LintIssue]:
    matches = _RULE_OF_THREE_RE.findall(text)
    if len(matches) >= 2:
        # One rule-of-three list can be legitimate; two or more in one
        # article is the recognizable LLM pattern the writer prompt warns
        # against.
        return [LintIssue(
            code="rule_of_three",
            message=f"найдено {len(matches)} перечислений по три элемента подряд (rule of three) — LLM-паттерн",
            severity="warning",
        )]
    return []


def check_sentence_rhythm(text: str) -> list[LintIssue]:
    sentences = _split_sentences(text)
    if len(sentences) < 6:
        return []
    lengths = [len(s.split()) for s in sentences]
    stdev = statistics.pstdev(lengths)
    mean = statistics.mean(lengths)
    issues = []
    # A very low standard deviation relative to the mean means most
    # sentences are nearly the same length — the flattened, uniform rhythm
    # that reads as machine-generated per the writer prompt's guidance.
    if mean > 0 and (stdev / mean) < 0.28:
        issues.append(LintIssue(
            code="flat_sentence_rhythm",
            message=f"низкая вариативность длины предложений (stdev={stdev:.1f}, mean={mean:.1f} слов) — текст читается монотонно",
            severity="warning",
        ))
    return issues


def check_paragraph_rhythm(text: str) -> list[LintIssue]:
    paragraphs = [p for p in text.split("\n\n") if p.strip() and not _HEADING_RE.match(p.strip())]
    if len(paragraphs) < 4:
        return []
    lengths = [len(p.split()) for p in paragraphs]
    stdev = statistics.pstdev(lengths)
    mean = statistics.mean(lengths)
    if mean > 0 and (stdev / mean) < 0.25:
        return [LintIssue(
            code="flat_paragraph_rhythm",
            message=f"абзацы почти одинаковой длины (stdev={stdev:.1f}, mean={mean:.1f} слов) — нет ритмического разнообразия",
            severity="warning",
        )]
    return []


def check_headings(text: str, expect_headings: bool) -> list[LintIssue]:
    headings = [ln for ln in text.split("\n") if _HEADING_RE.match(ln.strip())]
    if expect_headings and len(headings) == 0:
        return [LintIssue(
            code="missing_headings",
            message="в статье нет подзаголовков (## ...), хотя формат этого требует",
            severity="warning",
        )]
    return []


def check_word_count(text: str, min_words: int, max_words: int) -> list[LintIssue]:
    count = len(text.split())
    issues = []
    if count < min_words:
        issues.append(LintIssue(
            code="too_short",
            message=f"статья короче ожидаемого: {count} слов (минимум {min_words})",
            severity="warning",
        ))
    elif count > max_words:
        issues.append(LintIssue(
            code="too_long",
            message=f"статья длиннее ожидаемого: {count} слов (максимум {max_words})",
            severity="warning",
        ))
    return issues


# Generic words that are routinely capitalized in Title Case headlines
# without indicating an actual named entity (product, company, standard).
# Used to avoid check_title_specificity() mistaking ordinary Title Case
# formatting ("New Improvements in Manufacturing Quality") for the presence
# of a genuine proper noun.
_GENERIC_TITLE_WORDS = {
    "new", "improvements", "improvement", "manufacturing", "quality", "system",
    "systems", "solution", "solutions", "technology", "technologies", "process",
    "processes", "production", "update", "updates", "guide", "overview", "review",
    "analysis", "report", "industry", "equipment", "market", "trends", "trend",
    "insights", "insight", "news", "future", "advanced", "modern", "latest",
    "best", "top", "key", "major", "global", "smart", "digital", "innovation",
    "innovations", "development", "developments", "growth", "strategy", "in",
    "for", "with", "and", "the", "of", "to", "on", "at",
}


def check_title_specificity(title: str) -> list[LintIssue]:
    """A weak heading test: does the title contain at least one digit,
    capitalized multi-word entity (a model/company name), or a specific
    technical acronym, as opposed to being a fully generic phrase? This is
    intentionally permissive (low false-positive risk) — it only flags
    titles that are ALL lowercase-common-word, no numbers, no acronyms, and
    whose capitalized words are all generic Title-Case filler rather than
    an actual named entity.
    """
    has_digit = bool(re.search(r"\d", title))
    has_acronym = bool(re.search(r"\b[A-Z]{2,}\b", title))
    words = title.split()
    has_proper_noun = any(
        w[:1].isupper() and w.strip(".,:").lower() not in _GENERIC_TITLE_WORDS
        for w in words[1:] if w[:1].isalpha()
    )
    if has_digit or has_acronym or has_proper_noun:
        return []
    return [LintIssue(
        code="generic_title",
        message="заголовок не содержит ни цифр, ни аббревиатур, ни явного собственного имени — возможно, слишком общий",
        severity="warning",
    )]


def check_fact_grounding(body: str, key_facts: list[str]) -> list[LintIssue]:
    """Spot-check: do the numbers that appear in key_facts (from the brief)
    also appear somewhere in the article body? This doesn't prove every
    number in the article is grounded (that needs the LLM's judgement,
    covered by writer_revise.txt / agent-02b), but a brief that supplied
    specific figures and got an article that mentions NONE of them is a
    strong signal the Writer ignored the source material.
    """
    if not key_facts:
        return []
    fact_numbers: set[str] = set()
    for fact in key_facts:
        fact_numbers.update(_NUMBER_RE.findall(str(fact)))
    if not fact_numbers:
        return []
    body_numbers = set(_NUMBER_RE.findall(body))
    overlap = fact_numbers & body_numbers
    if not overlap:
        return [LintIssue(
            code="no_fact_grounding",
            message=f"ни одна цифра из key_facts брифа ({', '.join(sorted(fact_numbers))}) не встречается в тексте статьи",
            severity="error",
        )]
    return []


# Word-count targets mirror the format-specific ranges in
# agents/prompts/writer.txt's "СТРУКТУРА ПО ТИПУ СТАТЬИ" section.
FORMAT_WORD_RANGES = {
    "news": (350, 700),
    "insight": (600, 1150),
    "review": (700, 1250),
    "vendor": (500, 1000),
}


def lint_article(
    title: str,
    body: str,
    editorial_type: str = "news",
    key_facts: list[str] | None = None,
) -> LintReport:
    """Run every deterministic check and return a combined report."""
    issues: list[LintIssue] = []
    issues += check_banned_phrases(title + " " + body)
    issues += check_rule_of_three(body)
    issues += check_sentence_rhythm(body)
    issues += check_paragraph_rhythm(body)
    issues += check_headings(body, expect_headings=editorial_type in ("insight", "review", "vendor"))
    min_w, max_w = FORMAT_WORD_RANGES.get(editorial_type, (350, 1250))
    issues += check_word_count(body, min_w, max_w)
    issues += check_title_specificity(title)
    issues += check_fact_grounding(body, key_facts or [])

    sentences = _split_sentences(body)
    headings = [ln for ln in body.split("\n") if _HEADING_RE.match(ln.strip())]
    return LintReport(
        issues=issues,
        word_count=len(body.split()),
        sentence_count=len(sentences),
        heading_count=len(headings),
    )
