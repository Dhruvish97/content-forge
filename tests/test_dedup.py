#!/usr/bin/env python3
"""Tests for the content-dedup logic and a font-loading smoke test."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import generate_posts as gp


class ContentLogTestCase(unittest.TestCase):
    """Base class that points CONTENT_LOG at a scratch file per test."""

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


class TestJaccard(unittest.TestCase):
    def test_full_overlap(self):
        self.assertEqual(gp._jaccard({"a", "b"}, {"a", "b"}), 1.0)

    def test_no_overlap(self):
        self.assertEqual(gp._jaccard({"a"}, {"b"}), 0.0)

    def test_empty_set_is_zero(self):
        self.assertEqual(gp._jaccard(set(), {"a"}), 0.0)
        self.assertEqual(gp._jaccard(set(), set()), 0.0)

    def test_partial_overlap(self):
        self.assertAlmostEqual(gp._jaccard({"a", "b"}, {"b", "c"}), 1 / 3)


class TestHighImportance(unittest.TestCase):
    def test_matches_keyword(self):
        self.assertTrue(gp._is_high_importance("Fed Cuts Rates Amid Market Crash"))

    def test_no_match(self):
        self.assertFalse(gp._is_high_importance("Nvidia Reports Record Revenue"))


class TestCheckDuplicates(ContentLogTestCase):
    def test_headline_within_window_flagged(self):
        self._write_log({
            self._date(2): {"headlines": ["Meta Slashes 8,000 Jobs"], "categories": ["TECH"]}
        })
        dupes = gp.check_duplicates(
            [{"headline": "Meta Slashes 8,000 Jobs", "category": "TECH"}], None
        )
        self.assertEqual(dupes, ["Meta Slashes 8,000 Jobs"])

    def test_headline_outside_window_not_flagged(self):
        self._write_log({
            self._date(gp.DEDUP_DAYS + 1): {"headlines": ["Old Headline"], "categories": ["TECH"]}
        })
        dupes = gp.check_duplicates(
            [{"headline": "Old Headline", "category": "TECH"}], None
        )
        self.assertEqual(dupes, [])

    def test_edu_title_within_window_flagged(self):
        self._write_log({
            self._date(1): {"headlines": [], "categories": [], "edu_title": "What Is an ETF?"}
        })
        dupes = gp.check_duplicates([], {"title": "What Is an ETF?"})
        self.assertEqual(dupes, ["What Is an ETF?"])


class TestTopicDiversity(ContentLogTestCase):
    def test_overused_topic_flagged(self):
        log = {
            self._date(i): {"headlines": [f"Story {i}"], "categories": ["TECH"]}
            for i in range(gp.TOPIC_MAX_DAYS)
        }
        self._write_log(log)
        overused = gp.check_topic_diversity([{"headline": "New Tech Story", "category": "TECH"}])
        self.assertEqual(len(overused), 1)
        self.assertEqual(overused[0][0], "TECH")

    def test_high_importance_bypasses_cap(self):
        log = {
            self._date(i): {"headlines": [f"Story {i}"], "categories": ["MARKETS"]}
            for i in range(gp.TOPIC_MAX_DAYS)
        }
        self._write_log(log)
        overused = gp.check_topic_diversity(
            [{"headline": "Market Crash Wipes Out Gains", "category": "MARKETS"}]
        )
        self.assertEqual(overused, [])

    def test_under_cap_not_flagged(self):
        self._write_log({
            self._date(0): {"headlines": ["Story"], "categories": ["TECH"]}
        })
        overused = gp.check_topic_diversity([{"headline": "New Story", "category": "TECH"}])
        self.assertEqual(overused, [])


class TestEduSimilarity(ContentLogTestCase):
    def test_similar_point_flagged(self):
        self._write_log({
            self._date(1): {
                "headlines": [], "categories": [],
                "edu_points": ["Invest a fixed amount on a schedule no matter the price"],
            }
        })
        conflicts = gp.check_edu_content_similarity({
            "points": ["Invest a fixed amount on a regular schedule regardless of price"]
        })
        self.assertEqual(len(conflicts), 1)

    def test_dissimilar_point_not_flagged(self):
        self._write_log({
            self._date(1): {
                "headlines": [], "categories": [],
                "edu_points": ["Credit ratings grade how likely a borrower is to repay debt"],
            }
        })
        conflicts = gp.check_edu_content_similarity({
            "points": ["An ETF holds a basket of stocks you can buy like a single share"]
        })
        self.assertEqual(conflicts, [])

    def test_no_edu_item_returns_empty(self):
        self.assertEqual(gp.check_edu_content_similarity(None), [])


class TestLogPruning(ContentLogTestCase):
    def test_old_entries_pruned_after_logging(self):
        cutoff_days = max(gp.DEDUP_DAYS, gp.EDU_DEDUP_DAYS)
        self._write_log({self._date(cutoff_days + 5): {"headlines": ["Ancient"], "categories": []}})
        gp.log_generated_content(
            [{"headline": "Fresh Story", "category": "TECH"}],
            {"title": "Fresh Edu", "category": "LEARN", "points": []},
        )
        log = json.loads(gp.CONTENT_LOG.read_text())
        self.assertNotIn(self._date(cutoff_days + 5), log)
        self.assertIn(datetime.now().strftime("%Y-%m-%d"), log)


class TestLogAccumulatesWithinADay(ContentLogTestCase):
    """Regression test: a real duplicate slipped past dedup because a second
    run on the same day overwrote the first run's log entry, erasing its
    headline/edu title from history even though it had already been
    published. log_generated_content() must append, not replace."""

    def test_second_run_same_day_does_not_erase_first(self):
        gp.log_generated_content(
            [{"headline": "Morning Story", "category": "TECH"}],
            {"title": "Morning Edu", "category": "LEARN", "points": ["morning point"]},
        )
        gp.log_generated_content(
            [{"headline": "Afternoon Story", "category": "MARKETS"}],
            {"title": "Afternoon Edu", "category": "LEARN", "points": ["afternoon point"]},
        )
        used = gp._recent_used_content(gp._load_content_log())
        self.assertIn("morning story", used)
        self.assertIn("afternoon story", used)
        self.assertIn("morning edu", used)
        self.assertIn("afternoon edu", used)

    def test_first_run_title_still_flagged_as_duplicate_after_second_run(self):
        gp.log_generated_content(
            [{"headline": "Morning Story", "category": "TECH"}],
            {"title": "Morning Edu", "category": "LEARN", "points": []},
        )
        gp.log_generated_content(
            [{"headline": "Afternoon Story", "category": "MARKETS"}],
            {"title": "Afternoon Edu", "category": "LEARN", "points": []},
        )
        dupes = gp.check_duplicates([], {"title": "Morning Edu"})
        self.assertEqual(dupes, ["Morning Edu"])

    def test_old_dict_per_date_log_upgraded_to_list_on_load(self):
        # Logs written before this fix stored a single dict per date — must
        # still be readable, not silently dropped or misread.
        self._write_log({
            self._date(1): {"headlines": ["Old Format Story"], "categories": ["TECH"]}
        })
        used = gp._recent_used_content(gp._load_content_log())
        self.assertIn("old format story", used)


class TestFontLoadingSmoke(unittest.TestCase):
    """Proves the bundled fonts actually resolve and render, not just in theory."""

    def test_generate_news_post_dark_renders(self):
        img = gp.generate_news_post_dark(
            headline="Test Headline",
            summary="Test summary text.",
            category="TECH",
            source="Test Source",
            stat_label="Stat",
            stat_value="42%",
        )
        self.assertEqual(img.size, gp.SIZE)


class TestNewsPostStyles(unittest.TestCase):
    """Smoke-render every style in the pool, with and without a stat, for every palette."""

    def test_all_styles_render_with_stat(self):
        for style in gp.NEWS_POST_STYLES:
            for _ in range(4):  # covers random.choice() across all palette entries
                img = style(
                    headline="A Reasonably Long Test Headline For Wrapping",
                    summary="A test summary sentence long enough to wrap across lines.",
                    category="TECH",
                    source="Test Source",
                    stat_label="Test Stat",
                    stat_value="42%",
                )
                self.assertEqual(img.size, gp.SIZE, f"{style.__name__} produced wrong size")

    def test_all_styles_render_without_stat(self):
        for style in gp.NEWS_POST_STYLES:
            img = style(
                headline="Headline With No Stat Provided",
                summary="Summary text.",
                category="MARKETS",
                source="Test Source",
            )
            self.assertEqual(img.size, gp.SIZE, f"{style.__name__} produced wrong size")


if __name__ == "__main__":
    unittest.main()
