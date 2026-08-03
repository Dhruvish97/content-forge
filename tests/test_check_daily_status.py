#!/usr/bin/env python3
"""Tests for check_daily_status.py — the workflow's skip-if-already-done gate."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import check_daily_status as cds

REPO_ROOT = Path(__file__).parent.parent

DATE = "2026-08-03"


class CheckDailyStatusTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        self._orig_content_dir = cds.CONTENT_DIR
        self._orig_publish_log = cds.PUBLISH_LOG
        cds.CONTENT_DIR = tmp / "content"
        cds.CONTENT_DIR.mkdir()
        cds.PUBLISH_LOG = tmp / "publish_log.json"

    def tearDown(self):
        cds.CONTENT_DIR = self._orig_content_dir
        cds.PUBLISH_LOG = self._orig_publish_log
        self._tmpdir.cleanup()

    def _write_content(self, news=None, educational=None):
        path = cds.CONTENT_DIR / f"{DATE}.json"
        path.write_text(json.dumps({"news": news, "educational": educational}))

    def _write_publish_log(self, entries):
        cds.PUBLISH_LOG.write_text(json.dumps({DATE: entries}))


class TestContentReady(CheckDailyStatusTestCase):
    def test_no_file_is_not_ready(self):
        self.assertFalse(cds.content_ready(DATE))

    def test_missing_news_is_not_ready(self):
        self._write_content(news=[], educational={"title": "T", "points": ["p"]})
        self.assertFalse(cds.content_ready(DATE))

    def test_missing_educational_is_not_ready(self):
        self._write_content(news=[{"headline": "H"}], educational=None)
        self.assertFalse(cds.content_ready(DATE))

    def test_full_content_is_ready(self):
        self._write_content(news=[{"headline": "H"}], educational={"title": "T", "points": ["p"]})
        self.assertTrue(cds.content_ready(DATE))

    def test_malformed_json_is_not_ready(self):
        (cds.CONTENT_DIR / f"{DATE}.json").write_text("{not valid json")
        self.assertFalse(cds.content_ready(DATE))


class TestAlreadyPublished(CheckDailyStatusTestCase):
    def test_no_log_file_is_not_published(self):
        self.assertFalse(cds.already_published(DATE))

    def test_no_entries_for_today_is_not_published(self):
        cds.PUBLISH_LOG.write_text(json.dumps({"2026-01-01": [{"status": "published"}] * 3}))
        self.assertFalse(cds.already_published(DATE))

    def test_fewer_than_three_entries_is_not_published(self):
        self._write_publish_log([{"status": "published"}, {"status": "published"}])
        self.assertFalse(cds.already_published(DATE))

    def test_a_failed_entry_is_not_published(self):
        self._write_publish_log([{"status": "published"}, {"status": "published"}, {"status": "error"}])
        self.assertFalse(cds.already_published(DATE))

    def test_three_published_entries_is_published(self):
        self._write_publish_log([{"status": "published"}] * 3)
        self.assertTrue(cds.already_published(DATE))

    def test_malformed_json_is_not_published(self):
        cds.PUBLISH_LOG.write_text("{not valid json")
        self.assertFalse(cds.already_published(DATE))


class TestMainSubprocess(unittest.TestCase):
    """End-to-end: run the actual script the way the workflow does, in a
    scratch copy so CONTENT_DIR/PUBLISH_LOG (relative to the script's own
    location) point at throwaway fixtures instead of the real repo."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        (self.root / "content").mkdir()
        (self.root / "posts").mkdir()
        (self.root / "check_daily_status.py").write_text(
            (REPO_ROOT / "check_daily_status.py").read_text()
        )

    def tearDown(self):
        self._tmpdir.cleanup()

    def _run(self):
        return subprocess.run(
            [sys.executable, "check_daily_status.py", DATE],
            cwd=self.root, capture_output=True, text=True, check=True,
        )

    def test_nothing_present_prints_both_false(self):
        result = self._run()
        self.assertIn("content_ready=false", result.stdout)
        self.assertIn("already_published=false", result.stdout)

    def test_content_and_publish_present_prints_both_true(self):
        (self.root / "content" / f"{DATE}.json").write_text(
            json.dumps({"news": [{"headline": "H"}], "educational": {"title": "T"}})
        )
        (self.root / "posts" / "publish_log.json").write_text(
            json.dumps({DATE: [{"status": "published"}] * 3})
        )
        result = self._run()
        self.assertIn("content_ready=true", result.stdout)
        self.assertIn("already_published=true", result.stdout)


if __name__ == "__main__":
    unittest.main()
