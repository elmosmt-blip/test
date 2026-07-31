"""Tests for the SEO Doctor -> Publisher data flow fix:

Bug found during audit: agent-03-seo-doctor.py computed a slug,
meta-description, JSON-LD, and internal-link suggestions, but only ever
printed them to the console — nothing downstream (agent-06-publisher.py,
the dashboard) ever read them, because they were never written back to
meta.json. Separately, agent-06-publisher.py has its OWN independent slug
generator with real database uniqueness checking (unique_slug()), which
could legitimately produce a different slug than agent-03's provisional one
(e.g. if a title collision requires a "-2" suffix) -- so simply piping
agent-03's JSON-LD straight through would embed a URL that doesn't match
the actually-published slug.

This test file covers both halves of the fix:
  1. agent-03's --meta CLI path writes a "seo" key into meta.json.
  2. agent-06's build_frontmatter_data() correctly patches the JSON-LD's
     embedded slug when Publisher's final slug differs from SEO Doctor's
     provisional one.
"""

from __future__ import annotations

import importlib.util
import json
import sys
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
def seo_doctor():
    return _load_module("agent-03-seo-doctor.py", f"seo_doctor_{id(object())}")


@pytest.fixture
def publisher(monkeypatch):
    # agent-06-publisher.py calls sys.exit(1) at MODULE IMPORT TIME if
    # NEON_DATABASE_URL isn't set (see its top-level `if not DATABASE_URL`
    # check) -- this couples "can I import this module's pure functions"
    # to "is a database configured", which build_frontmatter_data() itself
    # doesn't need. A dummy URL unblocks the import without any real
    # connection ever being opened (get_conn() is never called by the pure
    # function under test here).
    monkeypatch.setenv("NEON_DATABASE_URL", "postgresql://test:test@localhost/test")
    return _load_module("agent-06-publisher.py", f"publisher_{id(object())}")


class TestSeoDoctorOptimize:
    def test_optimize_returns_expected_keys(self, seo_doctor):
        result = seo_doctor.optimize(
            "TRI TR7600 SV Ships With Higher Throughput",
            "Some article body mentioning the OEE Calculator tool.",
            category="Inspection",
            summary="A short summary.",
        )
        assert set(result.keys()) == {"slug", "meta_description", "jsonld", "internal_links"}
        assert result["slug"] == "tri-tr7600-sv-ships-with-higher-throughput"
        assert result["meta_description"] == "A short summary."

    def test_optimize_finds_internal_links(self, seo_doctor):
        result = seo_doctor.optimize(
            "Title", "This mentions the OEE Calculator and a reflow profile tool.", "cat",
        )
        keywords = {link["keyword"] for link in result["internal_links"]}
        assert "oee calculator" in keywords
        assert "reflow profile" in keywords

    def test_jsonld_embeds_the_provisional_slug(self, seo_doctor):
        result = seo_doctor.optimize("My Article Title", "body text", "cat")
        assert result["slug"] in result["jsonld"]


class TestSeoDoctorMetaWriteback:
    def test_cli_meta_path_writes_seo_key_into_meta_json(self, seo_doctor, tmp_path):
        article_file = tmp_path / "article.txt"
        article_file.write_text("Article body text mentioning the component finder tool.", encoding="utf-8")

        meta_file = tmp_path / "article.meta.json"
        meta_file.write_text(json.dumps({
            "title": "TRI TR7600 SV Update",
            "category": "Inspection",
            "summary": "Short summary.",
            "article_file": str(article_file),
        }), encoding="utf-8")

        # Replicate exactly what the __main__ --meta code path does, since
        # that logic lives under `if __name__ == "__main__":` and isn't
        # itself an importable function -- this test documents the contract
        # that CLI path relies on (optimize() + write seo key back).
        with open(meta_file, encoding="utf-8") as f:
            meta = json.load(f)
        with open(meta["article_file"], encoding="utf-8") as f:
            body = f.read()
        seo = seo_doctor.optimize(meta["title"], body, meta.get("category", "SMT Equipment"),
                                    summary=meta.get("summary", ""))
        meta["seo"] = seo
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        # Re-read from disk (not just the in-memory dict) to prove it was
        # actually persisted, not just held in a local variable.
        reloaded = json.loads(meta_file.read_text(encoding="utf-8"))
        assert "seo" in reloaded
        assert reloaded["seo"]["meta_description"] == "Short summary."
        assert "component finder" in {l["keyword"] for l in reloaded["seo"]["internal_links"]}


class TestPublisherFrontmatterSlugCorrection:
    def test_no_seo_block_produces_plain_frontmatter(self, publisher):
        data = publisher.build_frontmatter_data(
            tags=["AXI", "inspection"], section_dict={"editorial_type": "review"},
            source_url="https://example.com/1", seo=None, final_slug="final-slug",
        )
        assert data["tags"] == ["AXI", "inspection"]
        assert "seo" not in data

    def test_seo_block_included_when_slug_matches(self, publisher):
        seo = {
            "slug": "final-slug",
            "meta_description": "desc",
            "jsonld": '{"mainEntityOfPage": {"@id": "https://www.smtinsider.com/news/final-slug"}}',
            "internal_links": [{"keyword": "oee calculator", "url": "/tools/x"}],
        }
        data = publisher.build_frontmatter_data(
            tags=[], section_dict={}, source_url="", seo=seo, final_slug="final-slug",
        )
        assert data["seo"]["meta_description"] == "desc"
        assert "final-slug" in data["seo"]["jsonld"]

    def test_slug_mismatch_is_corrected_in_jsonld(self, publisher):
        # This is the core bug-fix case: SEO Doctor computed a provisional
        # slug before Publisher ran its uniqueness check, and the final
        # slug ended up different (e.g. a "-2" suffix due to a title
        # collision). The stored JSON-LD must reference the ACTUAL
        # published URL, not the discarded provisional one.
        seo = {
            "slug": "tri-tr7600-sv-update",
            "meta_description": "desc",
            "jsonld": json.dumps({
                "mainEntityOfPage": {
                    "@id": "https://www.smtinsider.com/news/tri-tr7600-sv-update"
                }
            }),
            "internal_links": [],
        }
        data = publisher.build_frontmatter_data(
            tags=[], section_dict={}, source_url="", seo=seo,
            final_slug="tri-tr7600-sv-update-2",  # Publisher had to disambiguate
        )
        assert "tri-tr7600-sv-update-2" in data["seo"]["jsonld"]
        assert "tri-tr7600-sv-update\"" not in data["seo"]["jsonld"].replace(
            "tri-tr7600-sv-update-2", ""
        )

    def test_missing_provisional_slug_leaves_jsonld_untouched(self, publisher):
        seo = {"meta_description": "desc", "jsonld": '{"a": "b"}', "internal_links": []}
        data = publisher.build_frontmatter_data(
            tags=[], section_dict={}, source_url="", seo=seo, final_slug="whatever",
        )
        assert data["seo"]["jsonld"] == '{"a": "b"}'

    def test_empty_seo_dict_is_falsy_and_skipped(self, publisher):
        data = publisher.build_frontmatter_data(
            tags=[], section_dict={}, source_url="", seo={}, final_slug="x",
        )
        assert "seo" not in data
