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


class TestHasHardNumbers(unittest.TestCase):
    def test_dollar_amount_with_scale_word_counts(self):
        self.assertTrue(fn._has_hard_numbers("Amazon commits $33 billion to Anthropic"))

    def test_dollar_amount_with_letter_suffix_counts(self):
        self.assertTrue(fn._has_hard_numbers("Anthropic now valued at $380B"))

    def test_percentage_counts(self):
        self.assertTrue(fn._has_hard_numbers("S&P 500 climbs +1.2% as Netflix drops 9%"))

    def test_large_comma_grouped_count_counts(self):
        self.assertTrue(fn._has_hard_numbers("Meta cuts 8,000 jobs in restructuring"))

    def test_bare_product_price_does_not_count(self):
        # Small dollar amounts with no scale word are product prices, not financial news —
        # this is the exact pattern that let a gadget review slip into the feed before.
        self.assertFalse(fn._has_hard_numbers("This $9 key physically locks your apps"))

    def test_no_numbers_does_not_count(self):
        self.assertFalse(fn._has_hard_numbers("Founders reflect on culture and burnout"))


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

    def test_story_with_concrete_number_beats_newer_soft_story(self):
        # A story with a headline-worthy dollar figure should win even against
        # a more recent, single-source story with no numbers — concrete
        # numbers outrank both recency and corroboration count.
        entries_by_url = {
            "https://a.example/feed": [_entry(
                "Startup Raises $50 Million In New Funding Round", summary="d", hours_ago=10,
            )],
            "https://b.example/feed": [_entry(
                "Founders Reflect On Culture And Burnout In Tech", summary="d", hours_ago=1,
            )],
        }
        with patch.object(fn, "FEED_SOURCES", [
            {"url": "https://a.example/feed", "category": "TECH", "source": "SourceA"},
            {"url": "https://b.example/feed", "category": "TECH", "source": "SourceB"},
        ]), self._mock_fetch(entries_by_url):
            picked = fn.fetch_candidate_news(n=1)
        self.assertEqual(picked[0]["headline"], "Startup Raises $50 Million In New Funding Round")

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
        self.assertEqual(result, {"summary": "Polished, richer summary.", "stat_label": None, "stat_value": None})

    def test_exception_returns_none(self):
        with patch.object(fn.anthropic, "Anthropic", side_effect=RuntimeError("boom")):
            result = fn._polish_summary("Headline", "TECH", "Src", "Thin real description.", "fake-key")
        self.assertIsNone(result)

    def test_stat_line_parsed_out_of_summary(self):
        text = "Amazon commits $33B to Anthropic in one of the biggest AI bets yet.\nSTAT: Anthropic valuation|$380B"
        with patch.object(fn.anthropic, "Anthropic", return_value=_mock_anthropic_client(text)):
            result = fn._polish_summary("Headline", "AI", "Src", "Some description.", "fake-key")
        self.assertEqual(result["summary"], "Amazon commits $33B to Anthropic in one of the biggest AI bets yet.")
        self.assertEqual(result["stat_label"], "Anthropic valuation")
        self.assertEqual(result["stat_value"], "$380B")

    def test_no_stat_line_leaves_stat_fields_none(self):
        with patch.object(fn.anthropic, "Anthropic", return_value=_mock_anthropic_client("Just a summary, no stat.")):
            result = fn._polish_summary("Headline", "TECH", "Src", "Thin real description.", "fake-key")
        self.assertIsNone(result["stat_label"])
        self.assertIsNone(result["stat_value"])


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
            fn, "_polish_summary",
            return_value={"summary": "Polished version.", "stat_label": None, "stat_value": None},
        ) as mock_polish:
            result = fn.enrich_news_items(items)
        mock_polish.assert_called_once()
        self.assertEqual(result[0]["summary"], "Polished version.")

    def test_stat_from_polish_is_attached_to_item(self):
        items = [self._item("A real description.")]
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake-key"}), patch.object(
            fn, "_polish_summary",
            return_value={"summary": "Polished.", "stat_label": "Deal size", "stat_value": "$33B"},
        ):
            result = fn.enrich_news_items(items)
        self.assertEqual(result[0]["stat_label"], "Deal size")
        self.assertEqual(result[0]["stat_value"], "$33B")

    def test_no_stat_from_polish_leaves_item_without_stat_fields(self):
        items = [self._item("A real description.")]
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake-key"}), patch.object(
            fn, "_polish_summary",
            return_value={"summary": "Polished.", "stat_label": None, "stat_value": None},
        ):
            result = fn.enrich_news_items(items)
        self.assertNotIn("stat_label", result[0])
        self.assertNotIn("stat_value", result[0])

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
