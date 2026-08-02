#!/usr/bin/env python3
"""Tests for run_today.py's content validation."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import run_today as rt

VALID_CONTENT = {
    "news": [
        {"headline": "H1", "summary": "S1", "category": "TECH", "source": "Reuters"},
        {"headline": "H2", "summary": "S2", "category": "MARKETS", "source": "Bloomberg"},
    ],
    "educational": {
        "title": "T",
        "category": "LEARN",
        "points": ["p1", "p2", "p3", "p4", "p5"],
    },
}


class ContentDirTestCase(unittest.TestCase):
    """Base class that points CONTENT_DIR at a scratch dir per test."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_dir = rt.CONTENT_DIR
        rt.CONTENT_DIR = Path(self._tmpdir.name)

    def tearDown(self):
        rt.CONTENT_DIR = self._orig_dir
        self._tmpdir.cleanup()

    def _write_content(self, date_str, data):
        (rt.CONTENT_DIR / f"{date_str}.json").write_text(json.dumps(data))


class TestLoadContentValid(ContentDirTestCase):
    def test_valid_content_loads(self):
        self._write_content("2026-01-01", VALID_CONTENT)
        data = rt.load_content("2026-01-01")
        self.assertEqual(data, VALID_CONTENT)

    def test_real_sample_file_is_valid(self):
        sample_path = Path(__file__).parent.parent / "content" / "2026-05-23.json"
        data = json.loads(sample_path.read_text())
        self._write_content("2026-05-23", data)
        self.assertIsNotNone(rt.load_content("2026-05-23"))


class TestLoadContentErrors(ContentDirTestCase):
    def test_missing_news_field_rejected(self):
        bad = json.loads(json.dumps(VALID_CONTENT))
        del bad["news"][0]["source"]
        self._write_content("2026-01-01", bad)
        self.assertIsNone(rt.load_content("2026-01-01"))

    def test_empty_string_field_rejected(self):
        bad = json.loads(json.dumps(VALID_CONTENT))
        bad["news"][0]["headline"] = "   "
        self._write_content("2026-01-01", bad)
        self.assertIsNone(rt.load_content("2026-01-01"))

    def test_non_list_news_rejected(self):
        bad = json.loads(json.dumps(VALID_CONTENT))
        bad["news"] = "not a list"
        self._write_content("2026-01-01", bad)
        self.assertIsNone(rt.load_content("2026-01-01"))

    def test_empty_news_list_rejected(self):
        bad = json.loads(json.dumps(VALID_CONTENT))
        bad["news"] = []
        self._write_content("2026-01-01", bad)
        self.assertIsNone(rt.load_content("2026-01-01"))

    def test_empty_points_rejected(self):
        bad = json.loads(json.dumps(VALID_CONTENT))
        bad["educational"]["points"] = []
        self._write_content("2026-01-01", bad)
        self.assertIsNone(rt.load_content("2026-01-01"))

    def test_missing_title_rejected(self):
        bad = json.loads(json.dumps(VALID_CONTENT))
        del bad["educational"]["title"]
        self._write_content("2026-01-01", bad)
        self.assertIsNone(rt.load_content("2026-01-01"))


class TestLoadContentWarnings(ContentDirTestCase):
    """Cases that should still load successfully, just print a warning."""

    def test_single_news_item_still_loads(self):
        one_item = json.loads(json.dumps(VALID_CONTENT))
        one_item["news"] = [one_item["news"][0]]
        self._write_content("2026-01-01", one_item)
        self.assertIsNotNone(rt.load_content("2026-01-01"))

    def test_four_points_still_loads(self):
        four_points = json.loads(json.dumps(VALID_CONTENT))
        four_points["educational"]["points"] = ["p1", "p2", "p3", "p4"]
        self._write_content("2026-01-01", four_points)
        self.assertIsNotNone(rt.load_content("2026-01-01"))


if __name__ == "__main__":
    unittest.main()
