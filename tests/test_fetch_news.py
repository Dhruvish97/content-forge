#!/usr/bin/env python3
"""Tests for fetch_news.py — no real network calls (fn._fetch_feed_entries is mocked)."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import generate_posts as gp
import fetch_news as fn


def _entry(title, summary="", hours_ago=None):
    e = {"title": title, "summary": summary}
    if hours_ago is not None:
        published = datetime.utcnow() - timedelta(hours=hours_ago)
        e["published_parsed"] = published.timetuple()
    return e


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
    def test_uses_real_headline_and_summary(self):
        item = fn._entry_to_news_item(
            _entry("Big Story Here", summary="Real editorial description of the story."),
            "TECH", "The Fool",
        )
        self.assertEqual(item["headline"], "Big Story Here")
        self.assertEqual(item["summary"], "Real editorial description of the story.")
        self.assertEqual(item["source"], "The Fool")
        self.assertEqual(item["category"], "TECH")

    def test_strips_html_from_summary(self):
        item = fn._entry_to_news_item(
            _entry("Headline", summary="<p>Text with <b>markup</b> in it</p>"),
            "TECH", "Src",
        )
        self.assertEqual(item["summary"], "Text with markup in it")

    def test_empty_summary_falls_back_to_generic(self):
        item = fn._entry_to_news_item(_entry("Standalone Headline"), "TECH", "Src")
        self.assertEqual(item["summary"], "Reported by Src.")


class TestFetchFeedEntries(unittest.TestCase):
    """_fetch_feed_entries is the seam every other test in this file mocks out,
    so its own exception-catching behavior needs direct coverage."""

    def test_returns_empty_list_on_request_exception(self):
        with patch.object(fn.requests, "get", side_effect=fn.requests.exceptions.Timeout("boom")):
            self.assertEqual(fn._fetch_feed_entries("https://example.com/feed"), [])

    def test_returns_empty_list_on_http_error(self):
        resp = Mock()
        resp.raise_for_status.side_effect = fn.requests.exceptions.HTTPError("404")
        with patch.object(fn.requests, "get", return_value=resp):
            self.assertEqual(fn._fetch_feed_entries("https://example.com/feed"), [])

    def test_returns_entries_on_success(self):
        resp = Mock(content=b"<rss></rss>")
        resp.raise_for_status.return_value = None
        fake_parsed = Mock(entries=[{"title": "A Story"}])
        with patch.object(fn.requests, "get", return_value=resp), patch.object(
            fn.feedparser, "parse", return_value=fake_parsed
        ):
            self.assertEqual(fn._fetch_feed_entries("https://example.com/feed"), [{"title": "A Story"}])


class TestFetchCandidateNews(ContentLogTestCase):
    def _mock_fetch(self, entries_by_url):
        return patch.object(fn, "_fetch_feed_entries", side_effect=lambda url: entries_by_url.get(url, []))

    def test_dedup_excludes_recent_headline(self):
        self._write_log({
            self._date(1): {"headlines": ["Repeated Headline"], "categories": ["TECH"]}
        })
        entries_by_url = {
            "https://techcrunch.com/feed/": [_entry("Repeated Headline", summary="desc")],
            "https://feeds.marketwatch.com/marketwatch/topstories/": [_entry("Fresh Market Story", summary="desc")],
        }

        with patch.object(fn, "FEED_SOURCES", [
            {"url": "https://techcrunch.com/feed/", "category": "TECH", "source": "TechCrunch"},
            {"url": "https://feeds.marketwatch.com/marketwatch/topstories/", "category": "MARKETS", "source": "MarketWatch"},
        ]), self._mock_fetch(entries_by_url):
            picked = fn.fetch_candidate_news(n=2)

        headlines = [p["headline"] for p in picked]
        self.assertNotIn("Repeated Headline", headlines)
        self.assertIn("Fresh Market Story", headlines)

    def test_picks_diverse_categories_when_possible(self):
        entries_by_url = {
            "https://techcrunch.com/feed/": [
                _entry("Tech Story One", summary="d"),
                _entry("Tech Story Two", summary="d"),
            ],
            "https://feeds.marketwatch.com/marketwatch/topstories/": [_entry("Market Story One", summary="d")],
        }

        with patch.object(fn, "FEED_SOURCES", [
            {"url": "https://techcrunch.com/feed/", "category": "TECH", "source": "TechCrunch"},
            {"url": "https://feeds.marketwatch.com/marketwatch/topstories/", "category": "MARKETS", "source": "MarketWatch"},
        ]), self._mock_fetch(entries_by_url):
            picked = fn.fetch_candidate_news(n=2)

        categories = {p["category"] for p in picked}
        self.assertEqual(len(picked), 2)
        self.assertEqual(categories, {"TECH", "MARKETS"})

    def test_source_comes_from_configured_source(self):
        # Source must come from FEED_SOURCES config, never inferred from feed data —
        # some feeds' raw <title> is clunky or outright wrong for display (e.g.
        # CNBC's earnings feed titles itself just "Earnings").
        entries_by_url = {"https://search.cnbc.com/...": [_entry("A Story", summary="d")]}
        with patch.object(fn, "FEED_SOURCES", [
            {"url": "https://search.cnbc.com/...", "category": "EARNINGS", "source": "CNBC"},
        ]), self._mock_fetch(entries_by_url):
            picked = fn.fetch_candidate_news(n=1)
        self.assertEqual(picked[0]["source"], "CNBC")

    def test_corroborated_story_beats_lone_recent_story(self):
        # Two outlets covering the same real story (paraphrased headlines) should
        # outrank a single-source story even when the single-source one is newer —
        # cross-source corroboration is the "popularity" proxy.
        entries_by_url = {
            "https://a.example/feed": [_entry(
                "OpenAI Launches New Model For Developers", summary="d", hours_ago=5,
            )],
            "https://b.example/feed": [_entry(
                "OpenAI launches new model targeting developers",
                summary="A much longer, more detailed real editorial description of the launch.",
                hours_ago=3,
            )],
            "https://c.example/feed": [_entry(
                "Totally Different Story About Something Else", summary="d", hours_ago=1,
            )],
        }
        with patch.object(fn, "FEED_SOURCES", [
            {"url": "https://a.example/feed", "category": "TECH", "source": "SourceA"},
            {"url": "https://b.example/feed", "category": "TECH", "source": "SourceB"},
            {"url": "https://c.example/feed", "category": "TECH", "source": "SourceC"},
        ]), self._mock_fetch(entries_by_url):
            picked = fn.fetch_candidate_news(n=1)
        self.assertEqual(len(picked), 1)
        self.assertNotEqual(picked[0]["source"], "SourceC")

    def test_no_corroboration_falls_back_to_most_recent(self):
        entries_by_url = {
            "https://a.example/feed": [_entry(
                "Central Bank Raises Interest Rates Today", summary="d", hours_ago=10,
            )],
            "https://b.example/feed": [_entry(
                "Retailer Reports Record Holiday Sales Growth", summary="d", hours_ago=2,
            )],
        }
        with patch.object(fn, "FEED_SOURCES", [
            {"url": "https://a.example/feed", "category": "MARKETS", "source": "SourceA"},
            {"url": "https://b.example/feed", "category": "MARKETS", "source": "SourceB"},
        ]), self._mock_fetch(entries_by_url):
            picked = fn.fetch_candidate_news(n=1)
        self.assertEqual(picked[0]["headline"], "Retailer Reports Record Holiday Sales Growth")

    def test_stale_entries_older_than_24h_excluded(self):
        entries_by_url = {
            "https://tech.example/feed": [_entry("Fresh Tech Story", summary="d", hours_ago=2)],
            "https://crypto.example/feed": [_entry("Old Crypto News Nobody Cares About", summary="d", hours_ago=30)],
        }
        with patch.object(fn, "FEED_SOURCES", [
            {"url": "https://tech.example/feed", "category": "TECH", "source": "SourceA"},
            {"url": "https://crypto.example/feed", "category": "CRYPTO", "source": "SourceB"},
        ]), self._mock_fetch(entries_by_url):
            picked = fn.fetch_candidate_news(n=2)
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0]["category"], "TECH")

    def test_missing_timestamp_entries_are_kept(self):
        # No published_parsed/updated_parsed at all — should not be penalized.
        entries_by_url = {"https://a.example/feed": [_entry("Undated Story", summary="d")]}
        with patch.object(fn, "FEED_SOURCES", [
            {"url": "https://a.example/feed", "category": "TECH", "source": "SourceA"},
        ]), self._mock_fetch(entries_by_url):
            picked = fn.fetch_candidate_news(n=1)
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0]["headline"], "Undated Story")

    def test_one_broken_feed_does_not_block_others(self):
        # _fetch_feed_entries already swallows failures and returns [] — confirm
        # fetch_candidate_news still surfaces the other feed's entries.
        entries_by_url = {
            "https://broken.example/feed": [],
            "https://working.example/feed": [_entry("Working Feed Story", summary="d")],
        }
        with patch.object(fn, "FEED_SOURCES", [
            {"url": "https://broken.example/feed", "category": "TECH", "source": "BrokenSource"},
            {"url": "https://working.example/feed", "category": "MARKETS", "source": "WorkingSource"},
        ]), self._mock_fetch(entries_by_url):
            picked = fn.fetch_candidate_news(n=2)
        headlines = [p["headline"] for p in picked]
        self.assertIn("Working Feed Story", headlines)


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


def _mock_anthropic_client(response_text):
    client = Mock()
    content_block = Mock(type="text", text=response_text)
    client.messages.create.return_value = Mock(content=[content_block])
    return client


class TestPolishSummary(unittest.TestCase):
    def test_happy_path_returns_polished_text(self):
        with patch.object(fn.anthropic, "Anthropic", return_value=_mock_anthropic_client("Polished, richer summary.")):
            result = fn._polish_summary("Headline", "TECH", "Src", "Thin real description.", "fake-key")
        self.assertEqual(result, "Polished, richer summary.")

    def test_exception_returns_none(self):
        with patch.object(fn.anthropic, "Anthropic", side_effect=RuntimeError("boom")):
            result = fn._polish_summary("Headline", "TECH", "Src", "Thin real description.", "fake-key")
        self.assertIsNone(result)


class TestEnrichNewsItems(unittest.TestCase):
    def _item(self, summary):
        return {"headline": "H", "summary": summary, "category": "TECH", "source": "Src"}

    def test_no_api_key_leaves_items_unchanged(self):
        items = [self._item("A real description.")]
        with patch.dict("os.environ", {}, clear=True):
            result = fn.enrich_news_items(items)
        self.assertEqual(result[0]["summary"], "A real description.")

    def test_real_description_gets_polished(self):
        items = [self._item("A real description.")]
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake-key"}), patch.object(
            fn, "_polish_summary", return_value="Polished version."
        ) as mock_polish:
            result = fn.enrich_news_items(items)
        mock_polish.assert_called_once()
        self.assertEqual(result[0]["summary"], "Polished version.")

    def test_thin_fallback_summary_is_not_polished(self):
        items = [self._item("Reported by Src.")]
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake-key"}), patch.object(
            fn, "_polish_summary"
        ) as mock_polish:
            result = fn.enrich_news_items(items)
        mock_polish.assert_not_called()
        self.assertEqual(result[0]["summary"], "Reported by Src.")

    def test_polish_failure_preserves_original_summary(self):
        items = [self._item("A real description.")]
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake-key"}), patch.object(
            fn, "_polish_summary", return_value=None
        ):
            result = fn.enrich_news_items(items)
        self.assertEqual(result[0]["summary"], "A real description.")


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
