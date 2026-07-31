"""Unit tests for agents/article_linter.py — deterministic, non-LLM article
quality checks. Pure functions over fixture text; no network, no LLM calls.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))

from article_linter import (
    check_banned_phrases,
    check_fact_grounding,
    check_headings,
    check_paragraph_rhythm,
    check_rule_of_three,
    check_sentence_rhythm,
    check_title_specificity,
    check_word_count,
    lint_article,
)


class TestBannedPhrases:
    def test_catches_known_cliche(self):
        issues = check_banned_phrases("This is a cutting-edge system for inspection.")
        assert len(issues) == 1
        assert issues[0].code == "banned_phrase"
        assert issues[0].severity == "error"

    def test_catches_multiple_cliches(self):
        text = "It is worth noting this revolutionary, game-changing system."
        issues = check_banned_phrases(text)
        assert len(issues) >= 2

    def test_clean_text_has_no_issues(self):
        text = "TRI's TR7600 SV pushes AXI throughput to 3,200 CPH on a 2-camera setup."
        assert check_banned_phrases(text) == []

    def test_case_insensitive(self):
        issues = check_banned_phrases("CUTTING-EDGE technology is here.")
        assert len(issues) == 1


class TestRuleOfThree:
    def test_single_rule_of_three_is_tolerated(self):
        text = "The system is faster, smarter, and more efficient than before."
        assert check_rule_of_three(text) == []

    def test_two_or_more_flagged(self):
        text = (
            "The system is faster, smarter, and more efficient. "
            "It also improves quality, reduces cost, and increases yield."
        )
        issues = check_rule_of_three(text)
        assert len(issues) == 1
        assert issues[0].code == "rule_of_three"


class TestSentenceRhythm:
    def test_uniform_sentence_lengths_flagged(self):
        # 8 sentences, all almost exactly 10 words -- flat rhythm.
        sentence = "The system processes boards at a steady constant rate today."
        text = " ".join([sentence] * 8)
        issues = check_sentence_rhythm(text)
        assert len(issues) == 1
        assert issues[0].code == "flat_sentence_rhythm"

    def test_varied_sentence_lengths_not_flagged(self):
        text = (
            "It works. The system processes boards at a steady rate, and engineers "
            "monitoring the line noticed a marked drop in false calls after the update. "
            "Numbers matter here. "
            "When the throughput increased by twenty percent compared to the previous "
            "generation, the plant manager decided to expand the pilot to two more lines "
            "before committing to a full rollout across the facility. "
            "That's the real test."
        )
        issues = check_sentence_rhythm(text)
        assert issues == []

    def test_too_few_sentences_skips_check(self):
        assert check_sentence_rhythm("One sentence. Two sentences.") == []


class TestParagraphRhythm:
    def test_uniform_paragraph_lengths_flagged(self):
        para = "This is a paragraph with exactly ten words in total right here."
        text = "\n\n".join([para] * 5)
        issues = check_paragraph_rhythm(text)
        assert len(issues) == 1
        assert issues[0].code == "flat_paragraph_rhythm"

    def test_varied_paragraph_lengths_not_flagged(self):
        paragraphs = [
            "Short one.",
            "A medium length paragraph that goes into a bit more detail about the topic at hand, explaining one specific mechanism.",
            "Tiny.",
            "This paragraph is considerably longer than the others and walks through several distinct engineering considerations in sequence, covering cause, effect, and the resulting practical recommendation for process engineers evaluating this equipment on their own line.",
        ]
        text = "\n\n".join(paragraphs)
        assert check_paragraph_rhythm(text) == []

    def test_too_few_paragraphs_skips_check(self):
        assert check_paragraph_rhythm("Para one.\n\nPara two.") == []


class TestHeadings:
    def test_missing_headings_flagged_when_expected(self):
        issues = check_headings("Just plain text with no structure at all.", expect_headings=True)
        assert len(issues) == 1
        assert issues[0].code == "missing_headings"

    def test_present_headings_not_flagged(self):
        text = "## What Changed\n\nSome text here."
        assert check_headings(text, expect_headings=True) == []

    def test_news_format_does_not_require_headings(self):
        assert check_headings("Just plain text.", expect_headings=False) == []


class TestWordCount:
    def test_too_short_flagged(self):
        issues = check_word_count("word " * 50, min_words=300, max_words=800)
        assert len(issues) == 1
        assert issues[0].code == "too_short"

    def test_too_long_flagged(self):
        issues = check_word_count("word " * 1000, min_words=300, max_words=800)
        assert len(issues) == 1
        assert issues[0].code == "too_long"

    def test_within_range_not_flagged(self):
        issues = check_word_count("word " * 500, min_words=300, max_words=800)
        assert issues == []


class TestTitleSpecificity:
    def test_generic_title_flagged(self):
        issues = check_title_specificity("New Improvements in Manufacturing Quality")
        assert len(issues) == 1
        assert issues[0].code == "generic_title"

    def test_title_with_digit_not_flagged(self):
        assert check_title_specificity("TRI TR7600 SV Ships With 20% Higher Throughput") == []

    def test_title_with_acronym_not_flagged(self):
        assert check_title_specificity("New AOI System Cuts False Calls") == []

    def test_title_with_proper_noun_not_flagged(self):
        assert check_title_specificity("Koh Young Launches New Inspection Platform") == []


class TestFactGrounding:
    def test_missing_facts_flagged(self):
        body = "The company announced a new system with improved performance."
        issues = check_fact_grounding(body, key_facts=["3200 CPH throughput", "20% increase"])
        assert len(issues) == 1
        assert issues[0].code == "no_fact_grounding"
        assert issues[0].severity == "error"

    def test_present_facts_not_flagged(self):
        body = "The new system reaches 3200 CPH, a notable jump for the category."
        issues = check_fact_grounding(body, key_facts=["3200 CPH throughput"])
        assert issues == []

    def test_no_key_facts_skips_check(self):
        assert check_fact_grounding("Any text at all.", key_facts=[]) == []

    def test_key_facts_without_numbers_skips_check(self):
        body = "Some unrelated text."
        assert check_fact_grounding(body, key_facts=["qualitative improvement noted"]) == []


class TestLintArticleIntegration:
    def test_clean_article_scores_well(self):
        title = "TRI TR7600 SV Ships With 20% Higher AXI Throughput"
        body = (
            "## What Changed\n\n"
            "TRI's TR7600 SV pushes AXI throughput to 3200 CPH on a 2-camera configuration. "
            "That's not a small gain. "
            "For a line running mixed BGA and QFN packages at high density, "
            "this changes how AXI fits into the cycle time budget.\n\n"
            "## Engineering Considerations\n\n"
            "Process engineers evaluating this platform should verify the throughput "
            "figure against their own board mix, since vendor-quoted numbers rarely "
            "hold under real production conditions without adjustment. "
            "An independent test found a lower number on mixed boards, closer to 2900 CPH."
        )
        report = lint_article(title, body, editorial_type="review", key_facts=["3200 CPH", "20%"])
        assert report.score >= 70
        assert not any(i.code == "banned_phrase" for i in report.issues)

    def test_cliche_heavy_article_scores_poorly(self):
        title = "New Improvements in Manufacturing"
        body = (
            "In today's fast-paced landscape, this cutting-edge, revolutionary, "
            "game-changing solution is faster, smarter, and more efficient. "
            "It is worth noting that this seamless, state-of-the-art solution "
            "will unlock the potential of your production line. "
            "Moreover, it improves quality, reduces cost, and increases yield. "
            "Furthermore, it boasts industry-leading performance."
        )
        report = lint_article(title, body, editorial_type="news", key_facts=[])
        assert report.score < 50
        assert report.has_errors
        codes = {i.code for i in report.issues}
        assert "banned_phrase" in codes

    def test_word_count_and_heading_count_are_reported(self):
        title = "TRI TR7600 SV Update"
        body = "## Section One\n\nSome text here with a few words in it for counting purposes."
        report = lint_article(title, body, editorial_type="review")
        assert report.word_count == len(body.split())
        assert report.heading_count == 1

    def test_to_dict_is_json_serializable(self):
        import json
        report = lint_article("Generic Title", "Some plain unstructured text.", editorial_type="news")
        # Must not raise -- to_dict() output should be plain JSON-safe types.
        json.dumps(report.to_dict())
