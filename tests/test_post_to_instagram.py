#!/usr/bin/env python3
"""Tests for post_to_instagram.py — no real network calls (requests is mocked)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from PIL import Image

import post_to_instagram as pti


def _mock_response(status_code=200, json_data=None, text=""):
    resp = Mock()
    resp.status_code = status_code
    resp.text = text
    resp.json = Mock(return_value=json_data if json_data is not None else {})
    resp.raise_for_status = Mock()
    if status_code >= 400:
        def _raise():
            raise Exception(f"HTTP {status_code}")
        resp.raise_for_status = Mock(side_effect=_raise)
    return resp


class OutputDirTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_dir = pti.OUTPUT_DIR
        self._orig_log = pti.PUBLISH_LOG
        pti.OUTPUT_DIR = Path(self._tmpdir.name)
        pti.PUBLISH_LOG = pti.OUTPUT_DIR / "publish_log.json"

    def tearDown(self):
        pti.OUTPUT_DIR = self._orig_dir
        pti.PUBLISH_LOG = self._orig_log
        self._tmpdir.cleanup()

    def _write_png(self, name, size=(20, 20)):
        path = pti.OUTPUT_DIR / name
        Image.new("RGB", size, (10, 20, 30)).save(path, "PNG")
        return path

    def _write_caption(self, name, text="Test caption"):
        path = pti.OUTPUT_DIR / name
        path.write_text(text)
        return path


class TestConvertToJpeg(OutputDirTestCase):
    def test_converts_png_to_jpeg(self):
        png_path = self._write_png("2026-01-01_news_1.png", size=(50, 40))
        jpg_path = pti.convert_to_jpeg(png_path)
        self.assertEqual(jpg_path.suffix, ".jpg")
        with Image.open(jpg_path) as img:
            self.assertEqual(img.format, "JPEG")
            self.assertEqual(img.size, (50, 40))


class TestTruncateCaption(unittest.TestCase):
    def test_short_caption_untouched(self):
        self.assertEqual(pti._truncate_caption("short"), "short")

    def test_long_caption_truncated(self):
        long_caption = "x" * 3000
        result = pti._truncate_caption(long_caption)
        self.assertEqual(len(result), pti.CAPTION_MAX_CHARS)
        self.assertTrue(result.endswith("…"))


class TestFindTodaysPosts(OutputDirTestCase):
    def test_finds_expected_pairs(self):
        self._write_png("2026-01-01_news_1.png")
        self._write_caption("2026-01-01_news_1_caption.txt")
        self._write_png("2026-01-01_news_2.png")
        self._write_caption("2026-01-01_news_2_caption.txt")
        self._write_png("2026-01-01_educational.png")
        self._write_caption("2026-01-01_educational_caption.txt")

        posts = pti.find_todays_posts("2026-01-01")
        self.assertEqual(len(posts), 3)
        labels = {pti._label(p) for p in posts}
        self.assertEqual(labels, {"news_1", "news_2", "educational"})

    def test_ignores_unrelated_files(self):
        self._write_png("2026-01-01_news_1.png")
        self._write_caption("2026-01-01_news_1_caption.txt")
        (pti.OUTPUT_DIR / "content_log.json").write_text("{}")

        posts = pti.find_todays_posts("2026-01-01")
        self.assertEqual(len(posts), 1)

    def test_returns_empty_when_nothing_exists(self):
        self.assertEqual(pti.find_todays_posts("2099-12-31"), [])


class TestGitHubReleasesFlow(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(pti, "_repo", return_value="owner/repo")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_creates_release_when_tag_missing(self):
        get_resp = _mock_response(404)
        create_resp = _mock_response(201, {
            "id": 1, "upload_url": "https://uploads.github.com/repos/owner/repo/releases/1/assets{?name,label}",
            "assets": [],
        })
        with patch("post_to_instagram.requests.request", side_effect=[get_resp, create_resp]) as mock_req:
            release = pti.get_or_create_release("daily-posts-2026-01-01", "tok")
        self.assertEqual(release["id"], 1)
        self.assertEqual(mock_req.call_count, 2)
        self.assertEqual(mock_req.call_args_list[1][0][0], "POST")

    def test_reuses_release_when_tag_found(self):
        get_resp = _mock_response(200, {"id": 1, "upload_url": "https://uploads.github.com/x{?name}", "assets": []})
        with patch("post_to_instagram.requests.request", return_value=get_resp) as mock_req:
            release = pti.get_or_create_release("daily-posts-2026-01-01", "tok")
        self.assertEqual(release["id"], 1)
        self.assertEqual(mock_req.call_count, 1)

    def test_upload_asset_happy_path(self):
        release = {"upload_url": "https://uploads.github.com/x{?name,label}", "assets": []}
        upload_resp = _mock_response(201, {"browser_download_url": "https://github.com/x/y/releases/download/tag/img.jpg"})
        with tempfile.TemporaryDirectory() as tmp:
            fpath = Path(tmp) / "img.jpg"
            fpath.write_bytes(b"fake-jpeg-bytes")
            with patch("post_to_instagram.requests.request", return_value=upload_resp) as mock_req:
                url = pti.upload_asset(release, fpath, "img.jpg", "tok")

        self.assertEqual(url, "https://github.com/x/y/releases/download/tag/img.jpg")
        call = mock_req.call_args_list[0]
        self.assertEqual(call[0][0], "POST")
        self.assertEqual(call[1]["headers"]["Content-Type"], "image/jpeg")
        self.assertEqual(call[1]["params"]["name"], "img.jpg")

    def test_upload_asset_retries_after_conflict(self):
        release = {
            "id": 1, "upload_url": "https://uploads.github.com/x{?name}",
            "assets": [{"name": "img.jpg", "id": 99}],
        }
        conflict_resp = _mock_response(422)
        delete_resp = _mock_response(204)
        success_resp = _mock_response(201, {"browser_download_url": "https://example.com/img.jpg"})
        with tempfile.TemporaryDirectory() as tmp:
            fpath = Path(tmp) / "img.jpg"
            fpath.write_bytes(b"fake")
            with patch(
                "post_to_instagram.requests.request",
                side_effect=[conflict_resp, delete_resp, success_resp],
            ) as mock_req:
                url = pti.upload_asset(release, fpath, "img.jpg", "tok")

        self.assertEqual(url, "https://example.com/img.jpg")
        methods = [c[0][0] for c in mock_req.call_args_list]
        self.assertEqual(methods, ["POST", "DELETE", "POST"])


class TestTokenNeverInUrl(unittest.TestCase):
    """Regression test: access_token must go in the Authorization header, never
    in a URL/query/body param, so it can't leak via a logged exception message."""

    def test_create_container_sends_token_as_header_only(self):
        resp = _mock_response(200, {"id": "container-1"})
        with patch("post_to_instagram.requests.request", return_value=resp) as mock_req:
            pti.create_container("igid", "http://img", "caption", "secret-token")
        call = mock_req.call_args_list[0]
        self.assertEqual(call[1]["headers"]["Authorization"], "Bearer secret-token")
        self.assertNotIn("secret-token", str(call[1].get("data", {})))
        self.assertNotIn("secret-token", str(call[1].get("params", {})))
        self.assertNotIn("secret-token", call[0][1])  # not in the URL itself

    def test_poll_until_finished_sends_token_as_header_only(self):
        resp = _mock_response(200, {"status_code": "FINISHED"})
        with patch("post_to_instagram.requests.request", return_value=resp) as mock_req:
            pti.poll_until_finished("container-1", "secret-token")
        call = mock_req.call_args_list[0]
        self.assertEqual(call[1]["headers"]["Authorization"], "Bearer secret-token")
        self.assertNotIn("secret-token", str(call[1].get("params", {})))
        self.assertNotIn("secret-token", call[0][1])

    def test_publish_container_sends_token_as_header_only(self):
        resp = _mock_response(200, {"id": "media-1"})
        with patch("post_to_instagram.requests.request", return_value=resp) as mock_req:
            pti.publish_container("igid", "container-1", "secret-token")
        call = mock_req.call_args_list[0]
        self.assertEqual(call[1]["headers"]["Authorization"], "Bearer secret-token")
        self.assertNotIn("secret-token", str(call[1].get("data", {})))


class TestRedact(unittest.TestCase):
    def test_redacts_known_secrets(self):
        text = "connection failed for url ...access_token=abc123&other=1"
        self.assertEqual(pti._redact(text, ["abc123"]), "connection failed for url ...access_token=***&other=1")

    def test_leaves_text_unchanged_when_no_secrets_present(self):
        self.assertEqual(pti._redact("no secrets here", ["abc123"]), "no secrets here")

    def test_ignores_empty_secret_list(self):
        self.assertEqual(pti._redact("some text", []), "some text")


class TestGraphApiSequencing(unittest.TestCase):
    def test_publish_only_after_finished(self):
        create_resp = _mock_response(200, {"id": "container-1"})
        poll_in_progress = _mock_response(200, {"status_code": "IN_PROGRESS"})
        poll_finished = _mock_response(200, {"status_code": "FINISHED"})
        publish_resp = _mock_response(200, {"id": "media-1"})

        with patch("post_to_instagram.time.sleep"), patch(
            "post_to_instagram.requests.request",
            side_effect=[create_resp, poll_in_progress, poll_in_progress, poll_finished, publish_resp],
        ) as mock_req:
            creation_id = pti.create_container("igid", "http://img", "caption", "tok")
            pti.poll_until_finished(creation_id, "tok")
            media_id = pti.publish_container("igid", creation_id, "tok")

        self.assertEqual(creation_id, "container-1")
        self.assertEqual(media_id, "media-1")
        self.assertEqual(mock_req.call_count, 5)

    def test_error_status_stops_before_publish(self):
        create_resp = _mock_response(200, {"id": "container-1"})
        poll_error = _mock_response(200, {"status_code": "ERROR"})

        with patch("post_to_instagram.time.sleep"), patch(
            "post_to_instagram.requests.request", side_effect=[create_resp, poll_error]
        ) as mock_req:
            creation_id = pti.create_container("igid", "http://img", "caption", "tok")
            with self.assertRaises(pti.PublishError):
                pti.poll_until_finished(creation_id, "tok")

        self.assertEqual(mock_req.call_count, 2)  # create + one poll, never publish

    def test_expired_token_raises_specific_error(self):
        error_resp = _mock_response(400, {"error": {"code": 190, "message": "Token expired"}})
        with patch("post_to_instagram.requests.request", return_value=error_resp):
            with self.assertRaises(pti.TokenExpiredError):
                pti.create_container("igid", "http://img", "caption", "bad-tok")


class TestMainDryRun(OutputDirTestCase):
    def test_dry_run_skips_graph_calls_but_uploads_release(self):
        self._write_png("2026-01-01_news_1.png")
        self._write_caption("2026-01-01_news_1_caption.txt")

        get_release_resp = _mock_response(200, {"id": 1, "upload_url": "https://uploads.github.com/x{?name}", "assets": []})
        upload_resp = _mock_response(201, {"browser_download_url": "https://example.com/img.jpg"})

        with patch.dict("os.environ", {"GITHUB_TOKEN": "gh-tok"}, clear=False), patch(
            "post_to_instagram.requests.request", side_effect=[get_release_resp, upload_resp]
        ) as mock_req:
            exit_code = pti.main("2026-01-01", dry_run=True)

        self.assertEqual(exit_code, 0)
        urls_called = [c[0][1] for c in mock_req.call_args_list]
        self.assertTrue(all("graph.facebook.com" not in u for u in urls_called))
        log = json.loads(pti.PUBLISH_LOG.read_text())
        self.assertEqual(log["2026-01-01"][0]["status"], "dry-run")

    def test_no_posts_returns_zero_without_requiring_tokens(self):
        with patch.dict("os.environ", {}, clear=True):
            exit_code = pti.main("2099-12-31", dry_run=False)
        self.assertEqual(exit_code, 0)

    def test_invalid_date_format_rejected(self):
        exit_code = pti.main("not-a-date", dry_run=False)
        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
