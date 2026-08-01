"""
Tests for PDF Scout UI and API endpoints in dashboard/app.py.
"""

import asyncio
import io
import json
import pytest
from pathlib import Path

from dashboard import app as dashboard_app


@pytest.mark.anyio
class TestDashboardPDFRoutes:
    async def test_upload_pdf_function(self, tmp_path, monkeypatch):
        class FakeRequest:
            def __init__(self, filename, content):
                self.headers = {"x-filename": filename}
                self._content = content
            async def body(self):
                return self._content

        monkeypatch.setattr(dashboard_app, "ROOT", tmp_path)
        fake_req = FakeRequest("test_catalog.pdf", b"%PDF-1.4 mock content")
        res = await dashboard_app.upload_pdf_file(fake_req)
        assert "file_path" in res
        assert res["filename"] == "test_catalog.pdf"
        assert Path(res["file_path"]).exists()

    async def test_run_pdf_function_creates_run_id(self, monkeypatch):
        class FakeProc:
            returncode = 0
            def __init__(self):
                self.stdout = self
            async def readline(self):
                return b""
            async def wait(self):
                return 0

        async def fake_exec(*args, **kwargs):
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        class FakeRequest:
            async def json(self):
                return {
                    "url": "https://online.fliphtml5.com/kwnhb/fakj/",
                    "file_path": "/tmp/test_catalog.pdf",
                    "format_type": "magazine",
                    "max_topics": 3,
                    "write": False,
                }

        res = await dashboard_app.run_pdf_scout_custom(FakeRequest())
        assert "run_id" in res
        assert len(res["run_id"]) > 5

    async def test_delete_brief_removes_unwanted_topic(self, tmp_path, monkeypatch):
        briefs_file = tmp_path / "briefs.json"
        briefs_file.write_text(json.dumps({"topics": [{"topic": "Bad topic"}, {"topic": "Good topic"}]}), encoding="utf-8")
        monkeypatch.setattr(dashboard_app, "BRIEFS_FILE", briefs_file)
        monkeypatch.setattr(dashboard_app, "_selected_topic_index", 0)

        res = await dashboard_app.delete_brief(0)

        assert res["ok"] is True
        assert res["deleted_topic"] == "Bad topic"
        assert res["remaining"] == 1
        assert json.loads(briefs_file.read_text(encoding="utf-8"))["topics"] == [{"topic": "Good topic"}]
        assert dashboard_app._selected_topic_index == -1
