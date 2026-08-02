#!/usr/bin/env python3
"""Tests for fetch_news.py — no real network calls (feedparser.parse is mocked)."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import generate_posts as gp
import fetch_news as fn


def _entry(title, summary="", source_title=None):
    e = {"title": title, "summary": summary}
    if source_title:
        e["source"] = {"title": source_title}
    return e


def _fake_feed(entries):
    class FakeFeed:
        pass
    f = FakeFeed()
    f.entries = entries
    return f


class ContentLogTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_log = gp.CONTENT_LOG
        gp.CONTENT_LOG = Path(self._tmpdir.name) / "content_log.json"

    def tearDown(self):
        gp.CONTENT_LOG = self._orig_log
        self._tmpdir.cleanup()

    def _write_log(self, log):
        gp.CONTENT_LOG.write_text(json.dumps(log))

    def _date(self, days_ago):
        return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


class TestEntryToNewsItem(unittest.TestCase):
    def test_strips_source_suffix_from_headline(self):
        item = fn._entry_to_news_item(
            _entry("Big Story Here - The Fool", source_title="The Fool"), "TECH"
        )
        self.assertEqual(item["headline"], "Big Story Here")
        self.assertEqual(item["source"], "The Fool")

    def test_redundant_summary_falls_back_to_generic(self):
        item = fn._entry_to_news_item(
            _entry(
                "Big Story Here - The Fool",
                summary='<a href="https://x">Big Story Here (truncated</a>',
                source_title="The Fool",
            ),
            "TECH",
        )
        self.assertEqual(item["summary"], "Reported by The Fool.")

    def test_distinct_summary_is_kept(self):
        item = fn._entry_to_news_item(
            _entry(
                "Big Story Here - The Fool",
                summary="<a>Completely different body text about the story details</a>",
                source_title="The Fool",
            ),
            "TECH",
        )
        self.assertIn("different body text", item["summary"])

    def test_missing_source_field_falls_back(self):
        item = fn._entry_to_news_item(_entry("Standalone Headline"), "TECH")
        self.assertEqual(item["headline"], "Standalone Headline")
        self.assertEqual(item["source"], "Unknown")


class TestFetchCandidateNews(ContentLogTestCase):
    def test_dedup_excludes_recent_headline(self):
        self._write_log({
            self._date(1): {"headlines": ["Repeated Headline"], "categories": ["TECH"]}
        })
        fake_entries = {
            "technology industry news": [_entry("Repeated Headline - Src", source_title="Src")],
            "stock market": [_entry("Fresh Market Story - Src2", source_title="Src2")],
        }

        def fake_parse(url):
            if "technology" in url:
                return _fake_feed(fake_entries["technology industry news"])
            if "stock" in url:
                return _fake_feed(fake_entries["stock market"])
            return _fake_feed([])

        with patch.object(fn, "NEWS_QUERIES", [
            {"query": "technology industry news", "category": "TECH"},
            {"query": "stock market", "category": "MARKETS"},
        ]), patch("feedparser.parse", side_effect=fake_parse):
            picked = fn.fetch_candidate_news(n=2)

        headlines = [p["headline"] for p in picked]
        self.assertNotIn("Repeated Headline", headlines)
        self.assertIn("Fresh Market Story", headlines)

    def test_picks_diverse_categories_when_possible(self):
        def fake_parse(url):
            if "technology" in url:
                return _fake_feed([
                    _entry("Tech Story One - Src", source_title="Src"),
                    _entry("Tech Story Two - Src", source_title="Src"),
                ])
            if "stock" in url:
                return _fake_feed([_entry("Market Story One - Src", source_title="Src")])
            return _fake_feed([])

        with patch.object(fn, "NEWS_QUERIES", [
            {"query": "technology industry news", "category": "TECH"},
            {"query": "stock market", "category": "MARKETS"},
        ]), patch("feedparser.parse", side_effect=fake_parse):
            picked = fn.fetch_candidate_news(n=2)

        categories = {p["category"] for p in picked}
        self.assertEqual(len(picked), 2)
        self.assertEqual(categories, {"TECH", "MARKETS"})


class TestWriteDraft(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_dir = fn.CONTENT_DIR
        fn.CONTENT_DIR = Path(self._tmpdir.name)

    def tearDown(self):
        fn.CONTENT_DIR = self._orig_dir
        self._tmpdir.cleanup()

    def test_writes_new_file(self):
        items = [{"headline": "H", "summary": "S", "category": "TECH", "source": "Src"}]
        path = fn.write_draft("2026-01-01", items)
        self.assertIsNotNone(path)
        data = json.loads(path.read_text())
        self.assertEqual(data["news"], items)
        self.assertIsNone(data["educational"])

    def test_refuses_overwrite_without_force(self):
        items = [{"headline": "H", "summary": "S", "category": "TECH", "source": "Src"}]
        fn.write_draft("2026-01-01", items)
        result = fn.write_draft("2026-01-01", items)
        self.assertIsNone(result)

    def test_force_overwrites(self):
        items = [{"headline": "H", "summary": "S", "category": "TECH", "source": "Src"}]
        fn.write_draft("2026-01-01", items)
        new_items = [{"headline": "H2", "summary": "S2", "category": "MARKETS", "source": "Src2"}]
        path = fn.write_draft("2026-01-01", new_items, force=True)
        data = json.loads(path.read_text())
        self.assertEqual(data["news"], new_items)

    def test_preserves_existing_educational_on_overwrite(self):
        path = fn.CONTENT_DIR / "2026-01-01.json"
        path.write_text(json.dumps({
            "news": [],
            "educational": {"title": "Kept", "category": "LEARN", "points": ["p1"]},
        }))
        items = [{"headline": "H", "summary": "S", "category": "TECH", "source": "Src"}]
        fn.write_draft("2026-01-01", items, force=True)
        data = json.loads(path.read_text())
        self.assertEqual(data["educational"]["title"], "Kept")

    def test_force_with_auto_educational_regenerates_stale_pick(self):
        # Regression: a same-day rerun must not get stuck reusing an educational
        # pick that's already a same-day duplicate (this blocked a real run).
        path = fn.CONTENT_DIR / "2026-01-01.json"
        path.write_text(json.dumps({
            "news": [],
            "educational": {"title": "Stale Pick", "category": "LEARN", "points": ["p1"]},
        }))
        items = [{"headline": "H", "summary": "S", "category": "TECH", "source": "Src"}]
        with patch.object(fn, "pick_educational", return_value=fn.EDUCATIONAL_BANK[1]) as mock_pick:
            fn.write_draft("2026-01-01", items, auto_educational=True, force=True)
        mock_pick.assert_called_once()
        data = json.loads(path.read_text())
        self.assertEqual(data["educational"]["title"], fn.EDUCATIONAL_BANK[1]["title"])


class TestPickEducational(ContentLogTestCase):
    def test_skips_recently_used_title(self):
        first_title = fn.EDUCATIONAL_BANK[0]["title"]
        self._write_log({
            self._date(1): {"headlines": [], "categories": [], "edu_title": first_title}
        })
        picked = fn.pick_educational()
        self.assertNotEqual(picked["title"], first_title)


if __name__ == "__main__":
    unittest.main()
