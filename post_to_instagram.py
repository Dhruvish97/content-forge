#!/usr/bin/env python3
"""Publishes the day's generated posts to Instagram via the Graph API.

Uses the "Instagram API with Instagram Login" flow (no Facebook Page
required). Images are re-hosted as GitHub Release assets since Instagram's
API requires each image to be fetchable at a public URL at post-time.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image

OUTPUT_DIR = Path(__file__).parent / "posts"
PUBLISH_LOG = OUTPUT_DIR / "publish_log.json"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

GITHUB_API = "https://api.github.com"
GRAPH_API_VERSION = "v21.0"
# Instagram-Login-flow tokens (the "IGAA..." prefix this project uses) must be
# sent to graph.instagram.com, not graph.facebook.com — the latter is for the
# older Facebook-Login-for-Business flow's "EAA..." tokens and rejects IGAA
# tokens with a generic "Cannot parse access token" error.
GRAPH_BASE = f"https://graph.instagram.com/{GRAPH_API_VERSION}"

POLL_INTERVAL_SECONDS = 3
POLL_MAX_ATTEMPTS = 15
CAPTION_MAX_CHARS = 2200

# (post_type, index) — matches the exact filenames generate_posts.save_post() produces
EXPECTED_POST_FILES = [("news", 1), ("news", 2), ("educational", None)]


class PublishError(Exception):
    pass


class TokenExpiredError(PublishError):
    pass


def find_todays_posts(date_str):
    """Return the (image, caption) file pairs that exist for date_str."""
    posts = []
    for post_type, index in EXPECTED_POST_FILES:
        idx = f"_{index}" if index else ""
        img_path = OUTPUT_DIR / f"{date_str}_{post_type}{idx}.png"
        caption_path = OUTPUT_DIR / f"{date_str}_{post_type}{idx}_caption.txt"
        if img_path.exists() and caption_path.exists():
            posts.append({
                "post_type": post_type,
                "index": index,
                "date_str": date_str,
                "image_path": img_path,
                "caption_path": caption_path,
            })
    return posts


def convert_to_jpeg(png_path):
    """Instagram's API requires JPEG, not PNG."""
    jpg_path = png_path.with_suffix(".jpg")
    Image.open(png_path).convert("RGB").save(jpg_path, "JPEG", quality=95)
    return jpg_path


def _clean_token(token):
    """Strip whitespace/newlines and an accidentally-included 'Bearer ' prefix
    from a copy-pasted token — the most common cause of Meta's "Cannot parse
    access token" error."""
    if not token:
        return token
    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token[len("bearer "):].strip()
    return token.strip('"').strip("'")


def _redact(text, secrets):
    """Strip any live secret value out of text before it's printed or persisted."""
    for secret in secrets:
        text = text.replace(secret, "***")
    return text


def _truncate_caption(caption):
    if len(caption) <= CAPTION_MAX_CHARS:
        return caption
    return caption[:CAPTION_MAX_CHARS - 1].rstrip() + "…"


def _request(method, url, **kwargs):
    """requests.request with a couple of retries on transient failures."""
    last_exc = None
    resp = None
    for attempt in range(3):
        try:
            resp = requests.request(method, url, timeout=30, **kwargs)
        except requests.RequestException as e:
            last_exc = e
            time.sleep(1.5 * (attempt + 1))
            continue
        if resp.status_code >= 500:
            time.sleep(1.5 * (attempt + 1))
            continue
        return resp
    if resp is not None:
        return resp
    raise last_exc or RuntimeError(f"Request to {url} failed with no response and no exception captured")


def _repo():
    repo = os.environ.get("GITHUB_REPOSITORY")
    if repo:
        return repo
    result = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        capture_output=True, text=True, cwd=Path(__file__).parent,
    )
    match = re.search(r"github\.com[:/](.+?)(?:\.git)?$", result.stdout.strip())
    if not match:
        raise RuntimeError("Could not determine GitHub repository — set GITHUB_REPOSITORY.")
    return match.group(1)


def _gh_headers(github_token):
    return {"Authorization": f"Bearer {github_token}", "Accept": "application/vnd.github+json"}


def get_or_create_release(tag_name, github_token):
    repo = _repo()
    headers = _gh_headers(github_token)
    resp = _request("GET", f"{GITHUB_API}/repos/{repo}/releases/tags/{tag_name}", headers=headers)
    if resp.status_code == 200:
        return resp.json()
    resp = _request(
        "POST", f"{GITHUB_API}/repos/{repo}/releases", headers=headers,
        json={"tag_name": tag_name, "name": tag_name, "prerelease": True, "draft": False},
    )
    resp.raise_for_status()
    return resp.json()


def upload_asset(release, file_path, asset_name, github_token):
    headers = _gh_headers(github_token)
    upload_url = release["upload_url"].split("{")[0]
    data = file_path.read_bytes()

    resp = _request(
        "POST", upload_url, params={"name": asset_name},
        headers={**headers, "Content-Type": "image/jpeg"}, data=data,
    )
    if resp.status_code == 422:
        existing = next((a for a in release.get("assets", []) if a["name"] == asset_name), None)
        if existing is None:
            fresh = _request(
                "GET", f"{GITHUB_API}/repos/{_repo()}/releases/{release['id']}", headers=headers
            ).json()
            existing = next((a for a in fresh.get("assets", []) if a["name"] == asset_name), None)
        if existing is not None:
            _request(
                "DELETE", f"{GITHUB_API}/repos/{_repo()}/releases/assets/{existing['id']}",
                headers=headers,
            )
        resp = _request(
            "POST", upload_url, params={"name": asset_name},
            headers={**headers, "Content-Type": "image/jpeg"}, data=data,
        )
    resp.raise_for_status()
    return resp.json()["browser_download_url"]


def _raise_for_graph_error(resp):
    if resp.status_code < 400:
        return
    try:
        err = resp.json().get("error", {})
    except ValueError:
        err = {}
    code = err.get("code")
    message = err.get("message", resp.text[:200])
    if code == 190:
        raise TokenExpiredError(message)
    raise PublishError(f"Graph API error (code {code}): {message}")


def _graph_auth_header(access_token):
    # Sent as a header, never as a query/body param, so it can't end up in a
    # logged URL (e.g. inside a requests.ConnectionError's message).
    return {"Authorization": f"Bearer {access_token}"}


def create_container(ig_user_id, image_url, caption, access_token):
    resp = _request(
        "POST", f"{GRAPH_BASE}/{ig_user_id}/media",
        data={"image_url": image_url, "caption": caption},
        headers=_graph_auth_header(access_token),
    )
    _raise_for_graph_error(resp)
    return resp.json()["id"]


def poll_until_finished(creation_id, access_token):
    for _ in range(POLL_MAX_ATTEMPTS):
        resp = _request(
            "GET", f"{GRAPH_BASE}/{creation_id}", params={"fields": "status_code"},
            headers=_graph_auth_header(access_token),
        )
        _raise_for_graph_error(resp)
        status = resp.json().get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise PublishError(f"Container {creation_id} failed with status {status}")
        time.sleep(POLL_INTERVAL_SECONDS)
    raise PublishError(f"Container {creation_id} did not finish processing in time")


def publish_container(ig_user_id, creation_id, access_token):
    resp = _request(
        "POST", f"{GRAPH_BASE}/{ig_user_id}/media_publish",
        data={"creation_id": creation_id},
        headers=_graph_auth_header(access_token),
    )
    _raise_for_graph_error(resp)
    return resp.json()["id"]


def publish_one(post, ig_user_id, github_token, access_token, dry_run):
    jpg_path = convert_to_jpeg(post["image_path"])
    caption = _truncate_caption(post["caption_path"].read_text())

    tag = f"daily-posts-{post['date_str']}"
    release = get_or_create_release(tag, github_token)
    image_url = upload_asset(release, jpg_path, jpg_path.name, github_token)

    result = {"post_type": post["post_type"], "index": post["index"], "image_url": image_url}

    if dry_run:
        print(f"   (dry-run) would POST /media image_url={image_url} caption={caption[:60]!r}...")
        result["status"] = "dry-run"
        return result

    creation_id = create_container(ig_user_id, image_url, caption, access_token)
    poll_until_finished(creation_id, access_token)
    media_id = publish_container(ig_user_id, creation_id, access_token)
    result["container_id"] = creation_id
    result["media_id"] = media_id
    result["status"] = "published"
    return result


def _label(post_or_result):
    idx = post_or_result.get("index")
    return f"{post_or_result['post_type']}{'_' + str(idx) if idx else ''}"


def _write_publish_log(date_str, results):
    log = {}
    if PUBLISH_LOG.exists():
        try:
            log = json.loads(PUBLISH_LOG.read_text())
        except json.JSONDecodeError:
            log = {}
    log[date_str] = results
    PUBLISH_LOG.write_text(json.dumps(log, indent=2, default=str))


def _write_step_summary(date_str, results):
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = [f"### Instagram publish — {date_str}", "", "| Post | Status | Detail |", "|---|---|---|"]
    for r in results:
        detail = r.get("media_id") or r.get("error") or r.get("image_url", "")
        lines.append(f"| {_label(r)} | {r['status']} | {detail} |")
    with open(summary_path, "a") as f:
        f.write("\n".join(lines) + "\n")


def main(date_str, dry_run):
    if not DATE_RE.match(date_str):
        print(f"❌ Invalid date '{date_str}' — expected YYYY-MM-DD.")
        return 1

    posts = find_todays_posts(date_str)
    if not posts:
        print(f"ℹ️  No generated posts found for {date_str} — nothing to publish.")
        return 0

    github_token = _clean_token(os.environ.get("GITHUB_TOKEN"))
    if not github_token:
        print("❌ GITHUB_TOKEN is not set — needed to create/upload GitHub Release assets.")
        return 1

    access_token = _clean_token(os.environ.get("IG_ACCESS_TOKEN"))
    ig_user_id = os.environ.get("IG_USER_ID", "").strip()
    if not dry_run and not (access_token and ig_user_id):
        print("❌ IG_ACCESS_TOKEN / IG_USER_ID must be set (unless using --dry-run).")
        return 1

    if access_token:
        # Length only — never the value — so a malformed secret is diagnosable
        # from CI logs without exposing it.
        print(f"ℹ️  IG_ACCESS_TOKEN length: {len(access_token)} chars, IG_USER_ID: {ig_user_id!r}")

    secrets = [t for t in (access_token, github_token) if t]

    results = []
    failed = False
    for post in posts:
        label = _label(post)
        try:
            result = publish_one(post, ig_user_id, github_token, access_token, dry_run)
            results.append(result)
            print(f"✅ {label}: {result['status']} ({result.get('media_id', result['image_url'])})")
        except TokenExpiredError as e:
            error = _redact(str(e), secrets)
            print(f"❌ {label}: IG_ACCESS_TOKEN appears expired or invalid — regenerate it and update the GitHub secret. ({error})")
            results.append({"post_type": post["post_type"], "index": post["index"], "status": "failed", "error": error})
            failed = True
        except Exception as e:
            error = _redact(str(e), secrets)
            print(f"❌ {label}: {error}")
            results.append({"post_type": post["post_type"], "index": post["index"], "status": "failed", "error": error})
            failed = True

    _write_publish_log(date_str, results)
    _write_step_summary(date_str, results)
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Publish today's generated posts to Instagram")
    parser.add_argument("date", nargs="?", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Convert + upload images as real GitHub Release assets, but skip the actual Instagram publish calls",
    )
    args = parser.parse_args()
    sys.exit(main(args.date, args.dry_run))
